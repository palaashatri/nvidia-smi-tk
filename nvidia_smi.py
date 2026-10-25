"""Pure parsing and CLI interactions for NVIDIA GPUs."""

from __future__ import annotations

import ctypes
import base64
import csv
import math
import ntpath
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

COLOR_UTIL_WARN = 70
COLOR_UTIL_DANGER = 90
COLOR_TEMP_WARN = 65
COLOR_TEMP_DANGER = 80
COLOR_POWER_WARN = 0.8
COLOR_POWER_DANGER = 0.95


def parse_numeric(val: Any, default: Any = None, is_int: bool = False) -> Any:
    """Defensively parse numeric fields from nvidia-smi CSV/text output."""
    if val is None:
        return default
    s = str(val).strip()
    if not s:
        return default
    lower = s.lower()
    unsupported = ("not supported", "unknown", "n/a")
    if lower.startswith("[") or any(kw in lower for kw in unsupported):
        return default
    try:
        num = float(s)
        if not math.isfinite(num):
            return default
        return int(num) if is_int else num
    except (ValueError, TypeError):
        return default


def parse_gpu_metrics(output: str) -> list[dict[str, Any]]:
    """Parse nvidia-smi CSV output into a list of GPU metric dictionaries.

    Supports:
    - 12 columns: index, uuid, name, 9 metrics
    - 11 columns: index, name, 9 metrics
    - 9 columns: legacy metrics only (default index 0)
    """
    gpus: list[dict[str, Any]] = []
    if (
        not output
        or output.lstrip().lower().startswith("error")
        or "no devices" in output.lower()
    ):
        return gpus

    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        try:
            parts = next(csv.reader([raw_line], skipinitialspace=True, strict=True))
        except (csv.Error, StopIteration):
            continue
        parts = [part.strip() for part in parts]
        if not parts or parts[0].startswith(("#", "=")):
            continue

        gpu_uuid: str | None = None

        if len(parts) >= 12:
            raw_index = parts[0]
            try:
                gpu_index = int(raw_index)
            except (TypeError, ValueError):
                continue
            gpu_uuid = parts[1]
            if gpu_index < 0 or not gpu_uuid.upper().startswith(("GPU-", "MIG-")):
                continue
            gpu_name = ", ".join(parts[2:-9]).strip() or f"GPU {gpu_index}"
            metric_parts = parts[-9:]
        elif len(parts) >= 11:
            raw_index = parts[0]
            try:
                gpu_index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if gpu_index < 0:
                continue
            gpu_name = ", ".join(parts[1:-9]).strip() or f"GPU {gpu_index}"
            metric_parts = parts[-9:]
        elif len(parts) >= 9:
            gpu_index = len(gpus)
            gpu_name = f"GPU {gpu_index}"
            metric_parts = parts[-9:]
        else:
            continue

        gpu_dict = {
            "index": gpu_index,
            "uuid": gpu_uuid,
            "name": gpu_name,
            "utilization": parse_numeric(metric_parts[0]),
            "mem_used": parse_numeric(metric_parts[1]),
            "mem_total": parse_numeric(metric_parts[2]),
            "temperature": parse_numeric(metric_parts[3], is_int=True),
            "power_draw": parse_numeric(metric_parts[4]),
            "power_limit": parse_numeric(metric_parts[5]),
            "fan_speed": parse_numeric(metric_parts[6]),
            "clock_gpu": parse_numeric(metric_parts[7]),
            "clock_mem": parse_numeric(metric_parts[8]),
        }
        gpus.append(gpu_dict)

    return gpus


def parse_processes(
    proc_output: str, uuid_to_index: dict[str, int] | None = None
) -> list[dict[str, str]]:
    """Parse nvidia-smi compute-apps CSV output into a list of process dicts.

    Maps GPU UUIDs to numerical GPU indices when uuid_to_index is provided.
    """
    processes: list[dict[str, str]] = []
    if not proc_output or proc_output.lstrip().lower().startswith("error"):
        return processes

    for raw_line in proc_output.splitlines():
        if not raw_line.strip():
            continue
        try:
            parts = next(csv.reader([raw_line], skipinitialspace=True, strict=True))
        except (csv.Error, StopIteration):
            continue
        parts = [part.strip() for part in parts]
        if not parts or parts[0].startswith("#"):
            continue
        if len(parts) >= 4:
            # gpu_id (uuid or index), pid, name, mem
            raw_gpu, pid, mem = parts[0], parts[1], parts[-1]
            name = ", ".join(parts[2:-1]).strip()
            gpu_str = "?"
            if uuid_to_index and raw_gpu in uuid_to_index:
                mapped_index = uuid_to_index[raw_gpu]
                if (
                    isinstance(mapped_index, int)
                    and not isinstance(mapped_index, bool)
                    and mapped_index >= 0
                ):
                    gpu_str = str(mapped_index)
            elif raw_gpu.isdigit():
                gpu_str = raw_gpu

            if not pid.isdigit() or not name:
                continue
            mem_val = mem if mem and "n/a" not in mem.lower() else "N/A"
            processes.append(
                {
                    "gpu": gpu_str,
                    "pid": pid,
                    "name": name,
                    "mem": mem_val,
                }
            )
        elif len(parts) >= 3:
            pid, mem = parts[0], parts[-1]
            name = ", ".join(parts[1:-1]).strip()
            if not pid.isdigit() or not name:
                continue
            mem_val = mem if mem and "n/a" not in mem.lower() else "N/A"
            processes.append(
                {
                    "gpu": "?",
                    "pid": pid,
                    "name": name,
                    "mem": mem_val,
                }
            )

    return processes


def parse_power_limits(
    smi_output: str, gpu_index: int | None = None
) -> tuple[float | None, float | None, float | None]:
    """Parse power limits (current, min, max) from nvidia-smi POWER output."""
    if not smi_output or "no devices" in smi_output.lower():
        return None, None, None
    if gpu_index is not None and (
        isinstance(gpu_index, bool) or not isinstance(gpu_index, int) or gpu_index < 0
    ):
        return None, None, None

    cur: float | None = None
    min_: float | None = None
    max_: float | None = None

    sections = re.split(r"(?m)(?=^GPU\s+[0-9A-Fa-f]{4,8}:)", smi_output)
    target_section = smi_output
    if gpu_index is not None and len(sections) > 1:
        gpu_sections = [
            s for s in sections if re.search(r"(?m)^GPU\s+[0-9A-Fa-f]{4,8}:", s)
        ]
        if 0 <= gpu_index < len(gpu_sections):
            target_section = gpu_sections[gpu_index]
        else:
            return None, None, None

    cur_pat = re.compile(
        r"^(?:Current\s+Power\s+Limit|Power\s+Limit|Enforced\s+Power\s+Limit)\s*:\s*([\d\.]+)\s*W",
        re.IGNORECASE,
    )
    min_pat = re.compile(r"^Min\s+Power\s+Limit\s*:\s*([\d\.]+)\s*W", re.IGNORECASE)
    max_pat = re.compile(r"^Max\s+Power\s+Limit\s*:\s*([\d\.]+)\s*W", re.IGNORECASE)

    for raw_line in target_section.splitlines():
        line = raw_line.strip()
        m_cur = cur_pat.match(line)
        m_min = min_pat.match(line)
        m_max = max_pat.match(line)

        if m_cur and cur is None:
            cur = parse_numeric(m_cur.group(1))
        elif m_min and min_ is None:
            min_ = parse_numeric(m_min.group(1))
        elif m_max and max_ is None:
            max_ = parse_numeric(m_max.group(1))

    return cur, min_, max_


def run_with_retry(
    func: Callable[[], Any], retries: int = 3, delay: float = 0.5
) -> Any:
    """Execute a function with retry logic."""
    for i in range(retries):
        try:
            return func()
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(delay)


def get_nvidia_smi_output(timeout: int = 5) -> tuple[str, str]:
    """Query nvidia-smi for GPU metrics and running processes.

    Returns:
        (gpu_output, proc_output) tuple of raw CSV strings.
    """
    creationflags = 0x08000000 if platform.system() == "Windows" else 0
    try:

        def fetch():
            fields = (
                "index,uuid,name,utilization.gpu,memory.used,memory.total,"
                "temperature.gpu,power.draw,power.limit,fan.speed,"
                "clocks.gr,clocks.mem"
            )
            gpu_res = subprocess.check_output(
                [
                    "nvidia-smi",
                    f"--query-gpu={fields}",
                    "--format=csv,noheader,nounits",
                ],
                encoding="utf-8",
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                timeout=timeout,
            )
            try:
                proc_res = subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
                        "--format=csv,noheader,nounits",
                    ],
                    encoding="utf-8",
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                    timeout=timeout,
                )
            except (subprocess.SubprocessError, OSError):
                proc_res = ""
            return gpu_res.strip(), proc_res.strip()

        return run_with_retry(fetch)
    except FileNotFoundError:
        return (
            "Error: nvidia-smi not found. Ensure NVIDIA drivers are installed.",
            "",
        )
    except subprocess.TimeoutExpired:
        return "Error: nvidia-smi command timed out.", ""
    except subprocess.CalledProcessError as exc:
        diagnostic = _subprocess_error_output(exc)
        if diagnostic:
            return f"Error: nvidia-smi failed.\n{diagnostic}", ""
        return f"Error: {exc}", ""
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        return f"Error: {exc}", ""


def _subprocess_error_output(error: subprocess.CalledProcessError) -> str:
    """Return captured subprocess output as readable diagnostic text."""
    output = error.output or error.stdout or error.stderr
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace").strip()
    return str(output).strip() if output else ""


def get_gpu_info(timeout: int = 5) -> str:
    """Fetch GPU model name(s) as a summary string."""
    creationflags = 0x08000000 if platform.system() == "Windows" else 0
    try:

        def fetch():
            info = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name",
                    "--format=csv,noheader,nounits",
                ],
                encoding="utf-8",
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                timeout=timeout,
            ).strip()
            lines = [
                line_text.strip()
                for line_text in info.splitlines()
                if line_text.strip()
            ]
            if not lines:
                return "Unknown GPU"
            if len(lines) == 1:
                return lines[0]
            return f"{lines[0]} (+{len(lines) - 1} more)"

        return run_with_retry(fetch)
    except (subprocess.SubprocessError, OSError, ValueError):
        return "Unknown GPU"


def get_full_output(timeout: int = 5) -> str:
    """Return bounded raw nvidia-smi output or a readable diagnostic."""
    creationflags = 0x08000000 if platform.system() == "Windows" else 0
    try:
        return subprocess.check_output(
            ["nvidia-smi"],
            encoding="utf-8",
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            timeout=timeout,
        )
    except FileNotFoundError:
        return "Error: nvidia-smi not found. Ensure NVIDIA drivers are installed."
    except subprocess.TimeoutExpired:
        return "Error: nvidia-smi command timed out."
    except subprocess.CalledProcessError as exc:
        diagnostic = _subprocess_error_output(exc)
        if diagnostic:
            return f"Error: nvidia-smi failed.\n{diagnostic}"
        return f"Error: {exc}"
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        return f"Error: {exc}"


class PowerLimitQueryError(RuntimeError):
    """A power-limit query failed before it could produce a parsed result."""


def get_power_limits_checked(
    gpu_index: int | None = None, timeout: int = 5
) -> tuple[float | None, float | None, float | None]:
    """Query power limits, raising with the driver diagnostic on failure."""
    if gpu_index is not None and (
        isinstance(gpu_index, bool) or not isinstance(gpu_index, int) or gpu_index < 0
    ):
        raise PowerLimitQueryError("GPU index must be a non-negative integer.")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise PowerLimitQueryError(
            "Timeout must be a positive integer number of seconds."
        )

    creationflags = 0x08000000 if platform.system() == "Windows" else 0
    try:

        def fetch():
            cmd = ["nvidia-smi"]
            if gpu_index is not None:
                cmd.extend(["-i", str(gpu_index)])
            cmd.extend(["-q", "-d", "POWER"])
            smi = subprocess.check_output(
                cmd,
                encoding="utf-8",
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                timeout=timeout,
            )
            # The command already filters output to gpu_index; parsing it as a
            # multi-device report would mistake a sparse index (for example 9)
            # for an ordinal position in the one returned section.
            return parse_power_limits(smi)

        return run_with_retry(fetch)
    except FileNotFoundError as exc:
        raise PowerLimitQueryError(
            "nvidia-smi not found. Ensure NVIDIA drivers are installed."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PowerLimitQueryError("nvidia-smi power-limit query timed out.") from exc
    except subprocess.CalledProcessError as exc:
        diagnostic = _subprocess_error_output(exc)
        message = (
            f"nvidia-smi power-limit query failed.\n{diagnostic}"
            if diagnostic
            else f"nvidia-smi power-limit query failed: {exc}"
        )
        raise PowerLimitQueryError(message) from exc
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise PowerLimitQueryError(
            f"nvidia-smi power-limit query failed: {exc}"
        ) from exc


def get_power_limits(
    gpu_index: int | None = None, timeout: int = 5
) -> tuple[float | None, float | None, float | None]:
    """Compatibility wrapper returning unavailable values when a query fails."""
    try:
        return get_power_limits_checked(gpu_index=gpu_index, timeout=timeout)
    except PowerLimitQueryError:
        return None, None, None


class _ShellExecuteInfo(ctypes.Structure):
    """ctypes representation of the Windows SHELLEXECUTEINFO structure."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


class _WindowsGuid(ctypes.Structure):
    """Win32 GUID layout with fixed-width fields on every host platform."""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _set_win_api_signature(function: Any, argtypes: list[Any], restype: Any) -> None:
    """Assign Win32 prototypes when using real ctypes function pointers."""
    try:
        function.argtypes = argtypes
        function.restype = restype
    except AttributeError:
        # Python fakes in tests do not expose ctypes function metadata.
        pass


def _run_windows_elevated(
    executable: str, arguments: list[str], timeout: int
) -> tuple[int | None, str | None]:
    """Run a Windows command through UAC and wait for its exit status."""
    if platform.system() != "Windows":
        return None, "Windows elevation is unavailable on this platform."

    process_handle: Any = None
    com_initialized = False
    try:
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32
        ole32 = ctypes.windll.ole32

        _set_win_api_signature(
            shell32.ShellExecuteExW,
            [ctypes.POINTER(_ShellExecuteInfo)],
            wintypes.BOOL,
        )
        _set_win_api_signature(
            kernel32.WaitForSingleObject,
            [wintypes.HANDLE, wintypes.DWORD],
            wintypes.DWORD,
        )
        _set_win_api_signature(
            kernel32.GetExitCodeProcess,
            [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        )
        _set_win_api_signature(kernel32.GetLastError, [], wintypes.DWORD)
        _set_win_api_signature(kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL)
        _set_win_api_signature(
            ole32.CoInitializeEx, [ctypes.c_void_p, wintypes.DWORD], ctypes.c_long
        )
        _set_win_api_signature(ole32.CoUninitialize, [], None)

        # ShellExecuteEx can use COM internally. RPC_E_CHANGED_MODE means the
        # current worker already initialized COM with a different apartment.
        com_result = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        if com_result < 0 and com_result != -2147417850:  # RPC_E_CHANGED_MODE
            return None, f"Could not initialize Windows COM (HRESULT {com_result})."
        com_initialized = com_result >= 0

        info = _ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        info.lpVerb = "runas"
        info.lpFile = executable
        info.lpParameters = subprocess.list2cmdline(arguments)
        info.nShow = 0

        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            error_code = kernel32.GetLastError()
            if error_code == 1223:  # ERROR_CANCELLED
                return None, "Administrator elevation was cancelled."
            return (
                None,
                f"Could not start elevated nvidia-smi (Windows error {error_code}).",
            )

        process_handle = info.hProcess
        if not process_handle:
            return (
                None,
                "Windows did not return a process handle; command completion is unconfirmed.",
            )

        wait_result = kernel32.WaitForSingleObject(process_handle, timeout * 1000)
        if wait_result == 258:  # WAIT_TIMEOUT
            return (
                None,
                "Elevated process timed out; command completion is unconfirmed.",
            )
        if wait_result != 0:  # WAIT_OBJECT_0
            error_code = kernel32.GetLastError()
            return (
                None,
                f"Could not wait for elevated process (Windows error {error_code}); "
                "command completion is unconfirmed.",
            )

        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
            error_code = kernel32.GetLastError()
            return (
                None,
                f"Could not read elevated process exit status (Windows error {error_code}); "
                "command completion is unconfirmed.",
            )
        return int(exit_code.value), None
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        return None, f"Windows elevation failed: {exc}"
    finally:
        if process_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(process_handle)
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        if com_initialized:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except (AttributeError, OSError, TypeError, ValueError):
                pass


def _get_windows_system_directory() -> str | None:
    """Resolve the Windows system directory through its authoritative API."""
    if platform.system() != "Windows":
        return None

    try:
        kernel32 = ctypes.windll.kernel32
        _set_win_api_signature(
            kernel32.GetSystemDirectoryW,
            [wintypes.LPWSTR, wintypes.UINT],
            wintypes.UINT,
        )
        system_directory = ctypes.create_unicode_buffer(32768)
        length = kernel32.GetSystemDirectoryW(system_directory, len(system_directory))
        if not length or length >= len(system_directory):
            return None
        path = system_directory.value
        return path if ntpath.isabs(path) else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _get_windows_program_files_directory() -> str | None:
    """Resolve Program Files using the Windows known-folder API."""
    if platform.system() != "Windows":
        return None

    folder_id = _WindowsGuid.from_buffer_copy(
        uuid.UUID("905e63b6-c1bf-494e-b29c-65b732d3d21a").bytes_le
    )
    path_pointer = wintypes.LPWSTR()
    try:
        shell32 = ctypes.windll.shell32
        _set_win_api_signature(
            shell32.SHGetKnownFolderPath,
            [
                ctypes.POINTER(_WindowsGuid),
                wintypes.DWORD,
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.LPWSTR),
            ],
            ctypes.c_long,
        )
        result = shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(path_pointer)
        )
        if result < 0 or not path_pointer.value:
            return None
        path = path_pointer.value
        return path if ntpath.isabs(path) else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    finally:
        if path_pointer.value:
            try:
                ole32 = ctypes.windll.ole32
                _set_win_api_signature(ole32.CoTaskMemFree, [wintypes.LPWSTR], None)
                ole32.CoTaskMemFree(path_pointer)
            except (AttributeError, OSError, TypeError, ValueError):
                pass


def _resolve_windows_nvidia_smi_path() -> str | None:
    """Find nvidia-smi only in OS-resolved Windows installation directories."""
    if platform.system() != "Windows":
        return None

    system_directory = _get_windows_system_directory()
    if system_directory and ntpath.isabs(system_directory):
        system_candidate = ntpath.join(system_directory, "nvidia-smi.exe")
        if os.path.isfile(system_candidate):
            return system_candidate

    program_files = _get_windows_program_files_directory()
    if program_files and ntpath.isabs(program_files):
        install_candidate = ntpath.join(
            program_files,
            "NVIDIA Corporation",
            "NVSMI",
            "nvidia-smi.exe",
        )
        if os.path.isfile(install_candidate):
            return install_candidate
    return None


def _get_windows_powershell_path() -> str | None:
    """Resolve Windows PowerShell beneath the Windows system directory."""
    system_directory = _get_windows_system_directory()
    if not system_directory:
        return None

    try:
        candidate = ntpath.join(
            system_directory,
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
        return (
            candidate if ntpath.isabs(candidate) and os.path.isfile(candidate) else None
        )
    except (OSError, TypeError, ValueError):
        return None


def _powershell_single_quoted(value: str) -> str:
    """Quote one literal for use as a PowerShell single-quoted string."""
    return "'" + value.replace("'", "''") + "'"


def _build_windows_capture_script(
    executable: str, arguments: list[str], timeout: int, output_path: str
) -> str:
    """Build a fixed PowerShell process wrapper with redirected output streams."""
    argument_string = subprocess.list2cmdline(arguments)
    executable_literal = _powershell_single_quoted(executable)
    argument_literal = _powershell_single_quoted(argument_string)
    output_literal = _powershell_single_quoted(output_path)
    timeout_ms = timeout * 1000
    return f"""$exePath = {executable_literal}
$nativeArguments = {argument_literal}
$logPath = {output_literal}
$timeoutMilliseconds = {timeout_ms}
$process = $null
$stdoutTask = $null
$stderrTask = $null
try {{
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $exePath
    $startInfo.Arguments = $nativeArguments
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {{ throw 'Could not start nvidia-smi.' }}
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()

    if (-not $process.WaitForExit($timeoutMilliseconds)) {{
        $killDiagnostic = ''
        try {{ $process.Kill() }} catch {{ $killDiagnostic = $_.Exception.Message }}
        [void]$process.WaitForExit(5000)
        [void]$stdoutTask.Wait(5000)
        [void]$stderrTask.Wait(5000)
        $stdout = if ($stdoutTask.IsCompleted) {{ $stdoutTask.Result }} else {{ '[stdout capture incomplete]' }}
        $stderr = if ($stderrTask.IsCompleted) {{ $stderrTask.Result }} else {{ '[stderr capture incomplete]' }}
        $diagnostic = "nvidia-smi timed out after $timeoutMilliseconds ms; command completion is unconfirmed.`r`nstdout:`r`n$stdout`r`nstderr:`r`n$stderr"
        if ($killDiagnostic) {{ $diagnostic += "`r`nCould not stop nvidia-smi: $killDiagnostic" }}
        [System.IO.File]::WriteAllText($logPath, $diagnostic, [System.Text.UTF8Encoding]::new($false))
        exit 124
    }}

    $process.WaitForExit()
    [void]$stdoutTask.Wait(5000)
    [void]$stderrTask.Wait(5000)
    $stdout = if ($stdoutTask.IsCompleted) {{ $stdoutTask.Result }} else {{ '[stdout capture incomplete]' }}
    $stderr = if ($stderrTask.IsCompleted) {{ $stderrTask.Result }} else {{ '[stderr capture incomplete]' }}
    $diagnostic = "stdout:`r`n$stdout`r`nstderr:`r`n$stderr"
    [System.IO.File]::WriteAllText($logPath, $diagnostic, [System.Text.UTF8Encoding]::new($false))
    exit $process.ExitCode
}} catch {{
    $diagnostic = "Could not run elevated nvidia-smi: " + ($_ | Out-String)
    try {{ [System.IO.File]::WriteAllText($logPath, $diagnostic, [System.Text.UTF8Encoding]::new($false)) }} catch {{}}
    exit 1
}} finally {{
    if ($process) {{ $process.Dispose() }}
}}
"""


def _run_windows_elevated_with_capture(
    executable: str, arguments: list[str], timeout: int
) -> tuple[int | None, str, str | None]:
    """Elevate nvidia-smi, return its native output, and clean temporary logs."""
    powershell_path = _get_windows_powershell_path()
    if not powershell_path:
        return (
            None,
            "",
            "Trusted Windows PowerShell was not found in the system directory.",
        )
    if not os.path.isabs(powershell_path):
        return None, "", "Windows PowerShell path is not absolute."
    if (
        len(arguments) != 4
        or arguments[0] != "-i"
        or not arguments[1].isdigit()
        or arguments[2] != "-pl"
        or not arguments[3].isdigit()
        or int(arguments[3]) <= 0
    ):
        return None, "Invalid GPU power-limit command arguments.", None

    try:
        temporary_directory = tempfile.mkdtemp(prefix="nvidia_smi_tk_power_")
    except OSError as exc:
        return None, "", f"Could not create temporary output capture: {exc}"

    output_path = os.path.join(temporary_directory, "nvidia-smi-output.txt")
    keep_temporary_directory = False
    try:
        script = _build_windows_capture_script(
            executable, arguments, timeout, output_path
        )
        encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        powershell_arguments = [
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded_script,
        ]
        exit_code, elevation_error = _run_windows_elevated(
            powershell_path, powershell_arguments, timeout + 10
        )
        try:
            with open(output_path, "r", encoding="utf-8", errors="replace") as log:
                diagnostic = log.read().strip()
        except FileNotFoundError:
            diagnostic = ""
        except OSError as exc:
            diagnostic = f"Could not read elevated nvidia-smi output: {exc}"

        if elevation_error and any(
            marker in elevation_error.lower()
            for marker in ("unconfirmed", "timed out", "could not wait")
        ):
            keep_temporary_directory = True
            elevation_error = (
                f"{elevation_error}\n"
                f"Temporary capture retained at {output_path} because the elevated "
                "process may still be writing."
            )
        return exit_code, diagnostic, elevation_error
    except (OSError, TypeError, ValueError) as exc:
        return None, "", f"Could not prepare elevated nvidia-smi capture: {exc}"
    finally:
        if not keep_temporary_directory:
            shutil.rmtree(temporary_directory, ignore_errors=True)


def set_power_limit(
    new_limit: float,
    gpu_index: int = 0,
    min_limit: float | None = None,
    max_limit: float | None = None,
    timeout: int = 15,
) -> tuple[bool, str]:
    """Set the GPU power limit with range validation and elevation support."""
    if isinstance(new_limit, bool) or not isinstance(new_limit, (int, float)):
        return False, "Power limit must be a finite positive number."
    if not math.isfinite(float(new_limit)) or new_limit <= 0:
        return False, "Power limit must be a finite positive number."
    if isinstance(gpu_index, bool) or not isinstance(gpu_index, int) or gpu_index < 0:
        return False, "GPU index must be a non-negative integer."
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        return False, "Timeout must be a positive integer number of seconds."

    new_limit = float(new_limit)
    if min_limit is None or max_limit is None:
        try:
            _current, queried_min, queried_max = get_power_limits_checked(
                gpu_index=gpu_index
            )
        except PowerLimitQueryError as exc:
            return False, f"Could not query GPU power-limit range: {exc}"
        if min_limit is None:
            min_limit = queried_min
        if max_limit is None:
            max_limit = queried_max

    if (
        isinstance(min_limit, bool)
        or isinstance(max_limit, bool)
        or not isinstance(min_limit, (int, float))
        or not isinstance(max_limit, (int, float))
        or not math.isfinite(float(min_limit))
        or not math.isfinite(float(max_limit))
        or min_limit <= 0
        or max_limit < min_limit
    ):
        return False, "Could not verify a valid GPU power-limit range."

    min_limit = float(min_limit)
    max_limit = float(max_limit)
    command_limit = round(new_limit)
    if (
        new_limit < min_limit
        or new_limit > max_limit
        or command_limit < min_limit
        or command_limit > max_limit
    ):
        return False, f"Value must be between {min_limit:.1f} and {max_limit:.1f} W"
    if command_limit <= 0:
        return False, "Power limit must be a finite positive number."

    system = platform.system()
    if system == "Windows":
        executable = _resolve_windows_nvidia_smi_path()
        if not executable:
            return (
                False,
                "Trusted nvidia-smi.exe was not found in the Windows system directory "
                "or NVIDIA NVSMI installation directory.",
            )
    else:
        executable = shutil.which("nvidia-smi")
        if not executable:
            return False, "nvidia-smi was not found on PATH."

    arguments = ["-i", str(gpu_index), "-pl", str(command_limit)]
    command = [executable, *arguments]
    creationflags = 0x08000000 if system == "Windows" else 0

    if system == "Windows":
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                creationflags=creationflags,
                timeout=timeout,
                check=False,
            )
            if result.returncode == 0:
                return True, result.stdout.strip() or "Power limit updated."

            error_output = "\n".join(
                value.strip()
                for value in (result.stderr or "", result.stdout or "")
                if value.strip()
            )
            if (
                "permission" in error_output.lower()
                or "administrator" in error_output.lower()
            ):
                exit_code, elevated_output, elevation_error = (
                    _run_windows_elevated_with_capture(executable, arguments, timeout)
                )
                diagnostics = "\n".join(
                    part.strip()
                    for part in (elevated_output, error_output)
                    if part and part.strip()
                )
                if elevation_error:
                    return False, f"{elevation_error}\n{diagnostics}".strip()
                if exit_code is None:
                    return (
                        False,
                        "Elevated command completion is unconfirmed.\n"
                        f"{diagnostics}".strip(),
                    )
                if exit_code != 0:
                    return (
                        False,
                        f"Elevated nvidia-smi exited with code {exit_code}.\n"
                        f"{diagnostics}",
                    )
                try:
                    current_limit, _min_limit, _max_limit = get_power_limits_checked(
                        gpu_index=gpu_index, timeout=timeout
                    )
                except PowerLimitQueryError as exc:
                    return (
                        False,
                        "Elevated command exited successfully, but power-limit "
                        f"readback failed: {exc}; command outcome is unconfirmed.\n"
                        f"{diagnostics}",
                    )
                if current_limit is None or not math.isclose(
                    current_limit, command_limit, rel_tol=0.0, abs_tol=0.01
                ):
                    actual = (
                        "unavailable"
                        if current_limit is None
                        else f"{current_limit:.1f} W"
                    )
                    return (
                        False,
                        "Elevated command exited successfully, but command outcome "
                        "is unconfirmed; the current limit "
                        f"could not be confirmed as {command_limit} W (reported {actual}).\n"
                        f"{diagnostics}",
                    )
                return (
                    True,
                    f"Power limit updated to {current_limit:.1f} W.\n{diagnostics}".strip(),
                )
            return False, error_output or "Failed to set power limit."
        except subprocess.TimeoutExpired:
            return False, "nvidia-smi timed out; command completion is unconfirmed."
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            return False, str(exc)

    pkexec_path = shutil.which("pkexec")
    if pkexec_path:
        command = [pkexec_path, executable, *arguments]
    else:
        command = ["sudo", "-n", executable, *arguments]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return True, result.stdout.strip() or "Power limit updated."
        error_output = "\n".join(
            value.strip()
            for value in (result.stderr or "", result.stdout or "")
            if value.strip()
        )
        if not pkexec_path and (
            "password" in error_output.lower() or "sudo" in error_output.lower()
        ):
            return (
                False,
                "Elevation required. Install pkexec (polkit) or run with sudo.\n"
                f"{error_output}",
            )
        return False, error_output or "Failed to set power limit."
    except subprocess.TimeoutExpired:
        return False, "Command timed out; completion is unconfirmed."
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        return False, str(exc)


def format_memory(mem_used: float | None, mem_total: float | None) -> tuple[str, float]:
    """Format memory used / total into human-readable string and percentage."""
    if mem_used is None or mem_total is None or mem_total <= 0:
        return "N/A", 0.0
    percent = (mem_used / mem_total) * 100
    if mem_total >= 1024:
        used_str = f"{mem_used / 1024:.1f} GB"
        total_str = f"{mem_total / 1024:.1f} GB"
    else:
        used_str = f"{mem_used:.0f} MB"
        total_str = f"{mem_total:.0f} MB"
    return f"{used_str} / {total_str} ({percent:.1f}%)", percent


def format_power(draw: float | None, limit: float | None) -> tuple[str, float]:
    """Format power draw / limit into string and percentage."""
    if draw is None and limit is None:
        return "N/A", 0.0
    if draw is None:
        return f"N/A / {limit:.1f} W", 0.0
    if limit is None or limit <= 0:
        return f"{draw:.1f} W / N/A", 0.0
    pct = (draw / limit) * 100
    return f"{draw:.1f} W / {limit:.1f} W", pct


def color_for_percent(
    val: float | None,
    warn: float = COLOR_UTIL_WARN,
    danger: float = COLOR_UTIL_DANGER,
) -> str:
    """Return color name based on percentage value."""
    if val is None:
        return "gray"
    if val < warn:
        return "green"
    elif val < danger:
        return "orange"
    else:
        return "red"


def color_for_temp(
    temp: float | None,
    warn: float = COLOR_TEMP_WARN,
    danger: float = COLOR_TEMP_DANGER,
) -> str:
    """Return color name based on temperature in Celsius."""
    if temp is None:
        return "gray"
    if temp < warn:
        return "green"
    elif temp < danger:
        return "orange"
    else:
        return "red"


def color_for_power(draw: float | None, limit: float | None) -> str:
    """Return color name based on power draw relative to limit."""
    if draw is None or limit is None or limit <= 0:
        return "gray"
    if draw < limit * COLOR_POWER_WARN:
        return "green"
    elif draw < limit * COLOR_POWER_DANGER:
        return "orange"
    else:
        return "red"
