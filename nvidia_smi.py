"""Pure parsing and CLI interactions for NVIDIA GPUs."""

from __future__ import annotations

import ctypes
import platform
import re
import shutil
import subprocess
import time
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
    if not output or output.startswith("Error") or "No devices" in output:
        return gpus

    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if line.startswith(("#", "=")):
            continue
        parts = [p.strip() for p in line.split(",")]
        gpu_uuid: str | None = None

        if len(parts) >= 12:
            raw_index = parts[0]
            try:
                gpu_index = int(raw_index)
            except ValueError:
                gpu_index = i
            gpu_uuid = parts[1]
            gpu_name = ", ".join(parts[2:-9]).strip() or f"GPU {gpu_index}"
            metric_parts = parts[-9:]
        elif len(parts) >= 11:
            raw_index = parts[0]
            try:
                gpu_index = int(raw_index)
            except ValueError:
                gpu_index = i
            gpu_name = ", ".join(parts[1:-9]).strip() or f"GPU {gpu_index}"
            metric_parts = parts[-9:]
        elif len(parts) >= 9:
            gpu_index = i
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
    processes = []
    if not proc_output or proc_output.startswith("Error"):
        return processes

    for raw_line in proc_output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 4:
            # gpu_id (uuid or index), pid, name, mem
            raw_gpu, pid, name, mem = parts[0], parts[1], parts[2], parts[3]
            gpu_str = "0"
            if uuid_to_index and raw_gpu in uuid_to_index:
                gpu_str = str(uuid_to_index[raw_gpu])
            elif raw_gpu.isdigit():
                gpu_str = raw_gpu

            mem_val = mem if mem and "n/a" not in mem.lower() else "N/A"
            processes.append(
                {
                    "gpu": gpu_str,
                    "pid": pid,
                    "name": name,
                    "mem": mem_val,
                }
            )
        elif len(parts) == 3:
            pid, name, mem = parts[0], parts[1], parts[2]
            mem_val = mem if mem and "n/a" not in mem.lower() else "N/A"
            processes.append(
                {
                    "gpu": "0",
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
    if not smi_output or "No devices" in smi_output:
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
            try:
                cur = float(m_cur.group(1))
            except ValueError:
                pass
        elif m_min and min_ is None:
            try:
                min_ = float(m_min.group(1))
            except ValueError:
                pass
        elif m_max and max_ is None:
            try:
                max_ = float(m_max.group(1))
            except ValueError:
                pass

    return cur, min_, max_


def run_with_retry(func, retries=3, delay=0.5):
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
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        return f"Error: {exc}", ""


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


def get_power_limits(
    gpu_index: int | None = None, timeout: int = 5
) -> tuple[float | None, float | None, float | None]:
    """Retrieve current, minimum, and maximum power limits in Watts."""
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
            return parse_power_limits(smi, gpu_index=gpu_index)

        return run_with_retry(fetch)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None, None, None


def set_power_limit(
    new_limit: float,
    gpu_index: int = 0,
    min_limit: float | None = None,
    max_limit: float | None = None,
    timeout: int = 15,
) -> tuple[bool, str]:
    """Set the GPU power limit with range validation and elevation support."""
    if (
        min_limit is not None
        and max_limit is not None
        and (new_limit < min_limit or new_limit > max_limit)
    ):
        msg = f"Value must be between {min_limit:.1f} and {max_limit:.1f} W"
        return False, msg

    val_str = str(round(new_limit))
    creationflags = 0x08000000 if platform.system() == "Windows" else 0
    system = platform.system()

    if system == "Windows":
        cmd = ["nvidia-smi", "-i", str(gpu_index), "-pl", val_str]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                creationflags=creationflags,
                timeout=timeout,
                check=False,
            )
            if res.returncode == 0:
                return True, res.stdout.strip()

            err_out = (res.stderr or res.stdout).strip()
            # If standard run fails due to permissions, trigger Windows UAC elevation
            if "permission" in err_out.lower() or "administrator" in err_out.lower():
                if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "shell32"):
                    params = f"-i {gpu_index} -pl {val_str}"
                    ret = ctypes.windll.shell32.ShellExecuteW(
                        None, "runas", "nvidia-smi", params, None, 0
                    )
                    if ret > 32:
                        return (
                            True,
                            "Elevation prompt requested. Power limit update submitted.",
                        )
                    return (
                        False,
                        "Administrator elevation was declined or failed.",
                    )
                return False, f"Administrator privileges required. {err_out}"
            return False, err_out or "Failed to set power limit."
        except subprocess.TimeoutExpired:
            return False, "nvidia-smi timed out."
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            return False, str(exc)

    # Linux / Unix: Check pkexec or fallback to sudo -n
    pkexec_path = shutil.which("pkexec")
    if pkexec_path:
        cmd = [pkexec_path, "nvidia-smi", "-i", str(gpu_index), "-pl", val_str]
    else:
        cmd = [
            "sudo",
            "-n",
            "nvidia-smi",
            "-i",
            str(gpu_index),
            "-pl",
            val_str,
        ]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode == 0:
            return True, res.stdout.strip()
        err_out = (res.stderr or res.stdout).strip()
        if not pkexec_path and (
            "password" in err_out.lower() or "sudo" in err_out.lower()
        ):
            return (
                False,
                "Elevation required. Install pkexec (polkit) or run with sudo.",
            )
        return False, err_out or "Failed to set power limit."
    except subprocess.TimeoutExpired:
        return False, "Command timed out."
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
