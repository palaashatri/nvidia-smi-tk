"""Focused regressions for parser, power-control, and config boundaries."""

from __future__ import annotations

import base64
import ctypes
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
import nvidia_smi


class ParsingRegressionTests(unittest.TestCase):
    def test_parse_numeric_rejects_non_finite_values(self) -> None:
        for raw in ("NaN", "Infinity", "-inf"):
            with self.subTest(raw=raw):
                self.assertIsNone(nvidia_smi.parse_numeric(raw))

    def test_gpu_names_with_csv_commas_are_unquoted(self) -> None:
        output = (
            '0, GPU-demo, "NVIDIA Demo, Series", 42, 2048, 8192, 55, '
            "110, 250, 40, 1600, 7000\n"
        )

        gpus = nvidia_smi.parse_gpu_metrics(output)

        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0]["name"], "NVIDIA Demo, Series")
        self.assertEqual(gpus[0]["uuid"], "GPU-demo")

    def test_gpu_rows_with_invalid_index_or_missing_uuid_are_skipped(self) -> None:
        output = (
            "bad-index, GPU-bad, Invalid index, 1, 2, 3, 4, 5, 6, 7, 8, 9\n"
            "1, , Missing UUID, 1, 2, 3, 4, 5, 6, 7, 8, 9\n"
            "4, malformed-uuid, Invalid UUID, 1, 2, 3, 4, 5, 6, 7, 8, 9\n"
            '3, GPU-good, "Good, Name", 42, 2048, 8192, 55, 110, 250, 40, 1600, 7000\n'
        )

        gpus = nvidia_smi.parse_gpu_metrics(output)

        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0]["index"], 3)
        self.assertEqual(gpus[0]["uuid"], "GPU-good")

    def test_process_names_with_commas_are_preserved(self) -> None:
        output = 'GPU-demo, 1234, "C:\\Apps\\render,helper.exe", 500\n'

        processes = nvidia_smi.parse_processes(output)

        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0]["name"], "C:\\Apps\\render,helper.exe")

    def test_process_rows_with_invalid_pid_or_name_are_skipped(self) -> None:
        output = (
            "GPU-demo, not-a-pid, renderer.exe, 500\n"
            "GPU-demo, 1234, , 500\n"
            "GPU-demo, 5678, renderer.exe, 500\n"
        )

        processes = nvidia_smi.parse_processes(output)

        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0]["pid"], "5678")

    def test_unmapped_process_uuid_is_not_reported_as_gpu_zero(self) -> None:
        output = "GPU-unmapped, 1234, renderer.exe, 500\n"

        processes = nvidia_smi.parse_processes(output, uuid_to_index={"GPU-known": 1})

        self.assertEqual(processes[0]["gpu"], "?")

    def test_legacy_process_without_gpu_identity_is_unknown(self) -> None:
        processes = nvidia_smi.parse_processes("1234, renderer.exe, 500\n")

        self.assertEqual(processes[0]["gpu"], "?")

    def test_invalid_selected_power_section_fails_closed(self) -> None:
        output = """GPU 00000000:01:00.0
    Current Power Limit : 100.00 W
    Min Power Limit : 50.00 W
    Max Power Limit : 200.00 W
GPU 00000000:02:00.0
    Current Power Limit : 150.00 W
    Min Power Limit : 100.00 W
    Max Power Limit : 300.00 W
"""

        self.assertEqual(
            nvidia_smi.parse_power_limits(output, gpu_index=1),
            (150.0, 100.0, 300.0),
        )
        self.assertEqual(
            nvidia_smi.parse_power_limits(output, gpu_index=2),
            (None, None, None),
        )

    def test_filtered_power_query_keeps_sparse_gpu_index(self) -> None:
        output = """GPU 00000000:65:00.0
    Current Power Limit : 150.00 W
    Min Power Limit : 100.00 W
    Max Power Limit : 300.00 W
"""

        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi.subprocess, "check_output", return_value=output
            ) as query,
        ):
            limits = nvidia_smi.get_power_limits(gpu_index=9)

        self.assertEqual(limits, (150.0, 100.0, 300.0))
        self.assertEqual(
            query.call_args.args[0], ["nvidia-smi", "-i", "9", "-q", "-d", "POWER"]
        )

    def test_checked_power_query_returns_parsed_limits_and_uses_timeout(self) -> None:
        output = """GPU 00000000:65:00.0
    Current Power Limit : 150.00 W
    Min Power Limit : 100.00 W
    Max Power Limit : 300.00 W
"""

        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi.subprocess, "check_output", return_value=output
            ) as query,
        ):
            limits = nvidia_smi.get_power_limits_checked(gpu_index=9, timeout=2)

        self.assertEqual(limits, (150.0, 100.0, 300.0))
        self.assertEqual(query.call_args.kwargs["timeout"], 2)

    def test_checked_power_query_preserves_captured_driver_error(self) -> None:
        error = subprocess.CalledProcessError(
            1,
            ["nvidia-smi"],
            output="Failed to initialize NVML: driver permission denied.",
        )

        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(nvidia_smi.subprocess, "check_output", side_effect=error),
            patch.object(nvidia_smi.time, "sleep"),
        ):
            with self.assertRaises(nvidia_smi.PowerLimitQueryError) as raised:
                nvidia_smi.get_power_limits_checked(gpu_index=2)

        self.assertIn("driver permission denied", str(raised.exception))

    def test_checked_power_query_exposes_timeout_and_missing_cli(self) -> None:
        failures = (
            (
                subprocess.TimeoutExpired(["nvidia-smi"], timeout=5),
                "timed out",
            ),
            (FileNotFoundError("nvidia-smi"), "not found"),
        )
        for failure, expected in failures:
            with self.subTest(expected=expected):
                with (
                    patch.object(nvidia_smi.platform, "system", return_value="Windows"),
                    patch.object(
                        nvidia_smi.subprocess, "check_output", side_effect=failure
                    ),
                    patch.object(nvidia_smi.time, "sleep"),
                ):
                    with self.assertRaises(nvidia_smi.PowerLimitQueryError) as raised:
                        nvidia_smi.get_power_limits_checked()

                self.assertIn(expected, str(raised.exception).lower())

    def test_legacy_power_query_keeps_none_triple_on_checked_error(self) -> None:
        with patch.object(
            nvidia_smi,
            "get_power_limits_checked",
            side_effect=nvidia_smi.PowerLimitQueryError("driver diagnostic"),
            create=True,
        ):
            result = nvidia_smi.get_power_limits(gpu_index=4)

        self.assertEqual(result, (None, None, None))


class PowerLimitRegressionTests(unittest.TestCase):
    def _trusted_windows_executable(self) -> str:
        return r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"

    def _successful_process(self) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["nvidia-smi"], returncode=0, stdout="Power limit updated", stderr=""
        )

    def _windows_path_apis(
        self, system_directory: str, program_files_directory: str
    ) -> SimpleNamespace:
        class Kernel32:
            def GetSystemDirectoryW(self, buffer: object, size: int) -> int:
                buffer.value = system_directory
                return len(system_directory)

        class Shell32:
            def __init__(self) -> None:
                self.known_folder_guid_bytes: bytes | None = None

            def SHGetKnownFolderPath(
                self,
                _folder_id: object,
                _flags: int,
                _token: object,
                path_pointer: object,
            ) -> int:
                self.known_folder_guid_bytes = ctypes.string_at(_folder_id, 16)
                path_pointer._obj.value = program_files_directory
                return 0

        class Ole32:
            def CoTaskMemFree(self, _path_pointer: object) -> None:
                return None

        return SimpleNamespace(kernel32=Kernel32(), shell32=Shell32(), ole32=Ole32())

    def test_windows_power_set_prefers_system_directory_over_path_executable(
        self,
    ) -> None:
        trusted = r"C:\Windows\System32\nvidia-smi.exe"
        path_executable = r"C:\Users\user\workspace\nvidia-smi.exe"
        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi.ctypes,
                "windll",
                self._windows_path_apis(r"C:\Windows\System32", r"C:\Program Files"),
                create=True,
            ),
            patch.object(
                nvidia_smi.os.path,
                "isfile",
                side_effect=lambda path: path.casefold() == trusted.casefold(),
            ),
            patch.object(
                nvidia_smi.shutil, "which", return_value=path_executable
            ) as which,
            patch.object(
                nvidia_smi.subprocess, "run", return_value=self._successful_process()
            ) as run,
        ):
            ok, _message = nvidia_smi.set_power_limit(
                200.0, min_limit=100.0, max_limit=300.0
            )

        self.assertTrue(ok, _message)
        self.assertEqual(run.call_args.args[0][0], trusted)
        which.assert_not_called()

    def test_windows_power_set_falls_back_to_known_program_files_directory(
        self,
    ) -> None:
        trusted = r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
        path_apis = self._windows_path_apis(r"C:\Windows\System32", r"C:\Program Files")
        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi.ctypes,
                "windll",
                path_apis,
                create=True,
            ),
            patch.object(
                nvidia_smi.os.path,
                "isfile",
                side_effect=lambda path: path.casefold() == trusted.casefold(),
            ),
            patch.dict(
                nvidia_smi.os.environ,
                {"ProgramFiles": r"C:\Users\user\spoofed-program-files"},
            ),
            patch.object(
                nvidia_smi.shutil,
                "which",
                return_value=r"C:\Users\user\workspace\nvidia-smi.exe",
            ) as which,
            patch.object(
                nvidia_smi.subprocess, "run", return_value=self._successful_process()
            ) as run,
        ):
            ok, _message = nvidia_smi.set_power_limit(
                200.0, min_limit=100.0, max_limit=300.0
            )

        self.assertTrue(ok, _message)
        self.assertEqual(run.call_args.args[0][0], trusted)
        which.assert_not_called()
        guid_type = getattr(nvidia_smi, "_WindowsGuid", None)
        self.assertIsNotNone(guid_type, "Windows GUID layout should be explicit")
        if guid_type is not None:
            self.assertEqual(ctypes.sizeof(guid_type), 16)
        self.assertEqual(
            path_apis.shell32.known_folder_guid_bytes,
            bytes.fromhex("b6635e90bfc14e49b29c65b732d3d21a"),
        )

    def test_windows_power_set_rejects_path_when_no_trusted_executable_exists(
        self,
    ) -> None:
        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi.ctypes,
                "windll",
                self._windows_path_apis(r"C:\Windows\System32", r"C:\Program Files"),
                create=True,
            ),
            patch.object(nvidia_smi.os.path, "isfile", return_value=False),
            patch.object(
                nvidia_smi.shutil,
                "which",
                return_value=r"C:\Users\user\workspace\nvidia-smi.exe",
            ) as which,
            patch.object(
                nvidia_smi.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=["nvidia-smi"],
                    returncode=1,
                    stdout="",
                    stderr="Insufficient Permissions",
                ),
            ) as run,
            patch.object(
                nvidia_smi,
                "_run_windows_elevated_with_capture",
                return_value=(5, "elevated access denied", None),
            ) as elevated,
        ):
            ok, message = nvidia_smi.set_power_limit(
                200.0, min_limit=100.0, max_limit=300.0
            )

        self.assertFalse(ok)
        self.assertIn("trusted", message.lower())
        which.assert_not_called()
        run.assert_not_called()
        elevated.assert_not_called()

    def test_windows_power_set_timeout_marks_completion_unconfirmed(self) -> None:
        trusted = r"C:\Windows\System32\nvidia-smi.exe"
        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi.ctypes,
                "windll",
                self._windows_path_apis(r"C:\Windows\System32", r"C:\Program Files"),
                create=True,
            ),
            patch.object(
                nvidia_smi.os.path,
                "isfile",
                side_effect=lambda path: path.casefold() == trusted.casefold(),
            ),
            patch.object(nvidia_smi.shutil, "which") as which,
            patch.object(
                nvidia_smi.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired([trusted], timeout=15),
            ),
        ):
            ok, message = nvidia_smi.set_power_limit(
                200.0, min_limit=100.0, max_limit=300.0
            )

        self.assertFalse(ok)
        self.assertIn("unconfirmed", message.lower())
        which.assert_not_called()

    def test_linux_power_set_timeout_marks_completion_unconfirmed(self) -> None:
        def executable_for(command: str) -> str | None:
            return "/usr/bin/nvidia-smi" if command == "nvidia-smi" else None

        with (
            patch.object(nvidia_smi.platform, "system", return_value="Linux"),
            patch.object(nvidia_smi.shutil, "which", side_effect=executable_for),
            patch.object(
                nvidia_smi.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["nvidia-smi"], timeout=15),
            ) as run,
        ):
            ok, message = nvidia_smi.set_power_limit(
                200.0, min_limit=100.0, max_limit=300.0
            )

        self.assertFalse(ok)
        self.assertIn("unconfirmed", message.lower())
        self.assertEqual(
            run.call_args.args[0],
            ["sudo", "-n", "/usr/bin/nvidia-smi", "-i", "0", "-pl", "200"],
        )

    def test_non_finite_and_non_positive_power_limits_are_rejected(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf"), 0.0, -1.0):
            with self.subTest(value=value):
                with patch.object(nvidia_smi.subprocess, "run") as run:
                    ok, message = nvidia_smi.set_power_limit(
                        value, min_limit=50.0, max_limit=300.0
                    )

                self.assertFalse(ok)
                self.assertTrue(message)
                run.assert_not_called()

    def test_windows_elevated_capture_reads_stdout_and_stderr_log(self) -> None:
        executable = "C:\\Program Files\\Vendor\\O'Hare\\nvidia-smi.exe"
        powershell = str(Path(tempfile.gettempdir()) / "powershell.exe")
        seen: dict[str, str] = {}

        def fake_elevated_run(
            elevated_executable: str, arguments: list[str], timeout: int
        ) -> tuple[int, None]:
            self.assertEqual(elevated_executable, powershell)
            self.assertGreater(timeout, 2)
            encoded_command = arguments[arguments.index("-EncodedCommand") + 1]
            script = base64.b64decode(encoded_command).decode("utf-16le")
            self.assertIn("ProcessStartInfo", script)
            self.assertIn("RedirectStandardOutput", script)
            self.assertIn("RedirectStandardError", script)
            self.assertIn("$timeoutMilliseconds = 2000", script)
            self.assertIn("WaitForExit($timeoutMilliseconds)", script)
            self.assertIn("O''Hare", script)
            match = re.search(r"(?m)^\$logPath = '((?:''|[^'])*)'$", script)
            self.assertIsNotNone(match)
            assert match is not None
            output_path = match.group(1).replace("''", "'")
            seen["output_path"] = output_path
            Path(output_path).write_text(
                "stdout: nvidia-smi result\nstderr: elevated permission denied\n",
                encoding="utf-8",
            )
            return 7, None

        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi, "_get_windows_powershell_path", return_value=powershell
            ),
            patch.object(
                nvidia_smi, "_run_windows_elevated", side_effect=fake_elevated_run
            ),
        ):
            exit_code, output, error = nvidia_smi._run_windows_elevated_with_capture(
                executable, ["-i", "1", "-pl", "200"], timeout=2
            )

        self.assertEqual(exit_code, 7)
        self.assertIn("stdout: nvidia-smi result", output)
        self.assertIn("stderr: elevated permission denied", output)
        self.assertIsNone(error)
        self.assertFalse(Path(seen["output_path"]).exists())

    def test_windows_elevated_capture_rejects_relative_powershell_path(self) -> None:
        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi,
                "_get_windows_powershell_path",
                return_value="powershell.exe",
            ),
            patch.object(nvidia_smi, "_run_windows_elevated") as elevate,
        ):
            exit_code, output, error = nvidia_smi._run_windows_elevated_with_capture(
                "C:\\NVIDIA\\nvidia-smi.exe", ["-i", "0", "-pl", "200"], timeout=2
            )

        self.assertIsNone(exit_code)
        self.assertEqual(output, "")
        self.assertIn("not absolute", error or "")
        elevate.assert_not_called()

    def test_set_power_limit_reports_captured_elevated_error(self) -> None:
        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi,
                "_resolve_windows_nvidia_smi_path",
                return_value=self._trusted_windows_executable(),
            ),
            patch.object(
                nvidia_smi.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=["nvidia-smi"],
                    returncode=1,
                    stdout="",
                    stderr="Insufficient Permissions",
                ),
            ),
            patch.object(
                nvidia_smi,
                "_run_windows_elevated_with_capture",
                return_value=(5, "elevated stderr: access denied", None),
            ) as elevated,
        ):
            ok, message = nvidia_smi.set_power_limit(
                200.0, min_limit=100.0, max_limit=300.0
            )

        self.assertFalse(ok)
        self.assertIn("elevated stderr: access denied", message)
        self.assertIn("Insufficient Permissions", message)
        elevated.assert_called_once()

    def test_windows_elevated_readback_failure_is_visible(self) -> None:
        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi,
                "_resolve_windows_nvidia_smi_path",
                return_value=self._trusted_windows_executable(),
            ),
            patch.object(
                nvidia_smi.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=["nvidia-smi"],
                    returncode=1,
                    stdout="",
                    stderr="Insufficient Permissions",
                ),
            ),
            patch.object(
                nvidia_smi,
                "_run_windows_elevated_with_capture",
                return_value=(0, "elevated command complete", None),
            ),
            patch.object(
                nvidia_smi,
                "get_power_limits_checked",
                side_effect=nvidia_smi.PowerLimitQueryError(
                    "NVML readback failed: driver not loaded"
                ),
            ) as query,
        ):
            ok, message = nvidia_smi.set_power_limit(
                200.0, min_limit=100.0, max_limit=300.0
            )

        self.assertFalse(ok)
        self.assertIn("NVML readback failed: driver not loaded", message)
        self.assertIn("command outcome is unconfirmed", message.lower())
        self.assertIn("Insufficient Permissions", message)
        query.assert_called_once_with(gpu_index=0, timeout=15)

    def test_invalid_gpu_index_and_range_are_rejected_before_command(self) -> None:
        for kwargs in (
            {"gpu_index": -1, "min_limit": 50.0, "max_limit": 300.0},
            {"gpu_index": 0, "min_limit": 300.0, "max_limit": 50.0},
        ):
            with (
                self.subTest(kwargs=kwargs),
                patch.object(nvidia_smi.subprocess, "run") as run,
            ):
                ok, message = nvidia_smi.set_power_limit(200.0, **kwargs)

            self.assertFalse(ok)
            self.assertTrue(message)
            run.assert_not_called()

    def test_missing_power_bounds_are_queried_and_unavailable_bounds_fail_closed(
        self,
    ) -> None:
        with (
            patch.object(
                nvidia_smi,
                "get_power_limits_checked",
                return_value=(None, None, None),
            ) as query,
            patch.object(nvidia_smi.subprocess, "run") as run,
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
        ):
            ok, message = nvidia_smi.set_power_limit(200.0)

        self.assertFalse(ok)
        self.assertIn("range", message.lower())
        query.assert_called_once_with(gpu_index=0)
        run.assert_not_called()

    def test_set_power_limit_preserves_failed_range_query_diagnostic(self) -> None:
        with (
            patch.object(
                nvidia_smi,
                "get_power_limits_checked",
                side_effect=nvidia_smi.PowerLimitQueryError(
                    "Insufficient Permissions querying GPU power limits."
                ),
            ) as query,
            patch.object(nvidia_smi.subprocess, "run") as run,
        ):
            ok, message = nvidia_smi.set_power_limit(200.0)

        self.assertFalse(ok)
        self.assertIn("Insufficient Permissions", message)
        query.assert_called_once_with(gpu_index=0)
        run.assert_not_called()

    def test_command_value_is_checked_after_rounding(self) -> None:
        with patch.object(nvidia_smi.subprocess, "run") as run:
            ok, message = nvidia_smi.set_power_limit(
                100.8, min_limit=100.0, max_limit=100.9
            )

        self.assertFalse(ok)
        self.assertIn("100.9", message)
        run.assert_not_called()

    def test_missing_bounds_are_used_from_device_query(self) -> None:
        with (
            patch.object(
                nvidia_smi,
                "get_power_limits_checked",
                return_value=(150.0, 100.0, 300.0),
            ) as query,
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi,
                "_resolve_windows_nvidia_smi_path",
                return_value=self._trusted_windows_executable(),
            ),
            patch.object(
                nvidia_smi.subprocess, "run", return_value=self._successful_process()
            ) as run,
        ):
            ok, _message = nvidia_smi.set_power_limit(250.0)

        self.assertTrue(ok)
        query.assert_called_once_with(gpu_index=0)
        self.assertEqual(
            run.call_args.args[0],
            [self._trusted_windows_executable(), "-i", "0", "-pl", "250"],
        )
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_gpu_query_diagnostic_includes_captured_driver_error(self) -> None:
        error = subprocess.CalledProcessError(
            1,
            ["nvidia-smi"],
            output="NVIDIA-SMI has failed because the driver is not loaded.",
        )

        with (
            patch.object(nvidia_smi.subprocess, "check_output", side_effect=error),
            patch.object(nvidia_smi.time, "sleep"),
        ):
            gpu_output, process_output = nvidia_smi.get_nvidia_smi_output()

        self.assertIn("driver is not loaded", gpu_output)
        self.assertEqual(process_output, "")

    def test_full_output_query_is_bounded_and_preserves_driver_diagnostic(self) -> None:
        error = subprocess.CalledProcessError(
            1,
            ["nvidia-smi"],
            output="NVIDIA-SMI has failed because the driver is not loaded.",
        )

        with patch.object(
            nvidia_smi.subprocess, "check_output", side_effect=error
        ) as query:
            output = nvidia_smi.get_full_output(timeout=2)

        self.assertIn("driver is not loaded", output)
        self.assertEqual(query.call_args.kwargs["timeout"], 2)

    def test_windows_elevation_is_verified_before_reporting_success(self) -> None:
        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi,
                "_resolve_windows_nvidia_smi_path",
                return_value=self._trusted_windows_executable(),
            ),
            patch.object(
                nvidia_smi.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=["nvidia-smi"],
                    returncode=1,
                    stdout="",
                    stderr="Insufficient Permissions",
                ),
            ),
            patch.object(
                nvidia_smi,
                "_run_windows_elevated_with_capture",
                return_value=(0, "stdout: complete\nstderr:", None),
            ) as elevated,
            patch.object(
                nvidia_smi,
                "get_power_limits_checked",
                return_value=(200.0, 100.0, 300.0),
            ) as query,
        ):
            ok, message = nvidia_smi.set_power_limit(
                200.0, gpu_index=1, min_limit=100.0, max_limit=300.0
            )

        self.assertTrue(ok)
        self.assertIn("200", message)
        self.assertIn("stdout: complete", message)
        elevated.assert_called_once_with(
            self._trusted_windows_executable(), ["-i", "1", "-pl", "200"], 15
        )
        query.assert_called_once_with(gpu_index=1, timeout=15)

    def test_windows_elevated_nonzero_exit_is_not_success(self) -> None:
        with (
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
            patch.object(
                nvidia_smi,
                "_resolve_windows_nvidia_smi_path",
                return_value=self._trusted_windows_executable(),
            ),
            patch.object(
                nvidia_smi.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=["nvidia-smi"],
                    returncode=1,
                    stdout="",
                    stderr="Insufficient Permissions",
                ),
            ),
            patch.object(
                nvidia_smi,
                "_run_windows_elevated_with_capture",
                return_value=(5, "stderr: elevated access denied", None),
            ),
            patch.object(nvidia_smi, "get_power_limits_checked") as query,
        ):
            ok, message = nvidia_smi.set_power_limit(
                200.0, min_limit=100.0, max_limit=300.0
            )

        self.assertFalse(ok)
        self.assertIn("5", message)
        self.assertIn("elevated access denied", message)
        self.assertIn("Insufficient Permissions", message)
        query.assert_not_called()

    def test_windows_elevation_without_process_handle_is_unconfirmed(self) -> None:
        class Shell32:
            def ShellExecuteExW(self, _info_pointer: object) -> int:
                return 1

        class Kernel32:
            def WaitForSingleObject(self, _handle: int, _timeout: int) -> int:
                return 0

            def GetExitCodeProcess(self, _handle: int, _code_pointer: object) -> int:
                return 1

            def GetLastError(self) -> int:
                return 0

            def CloseHandle(self, _handle: int) -> int:
                return 1

        class Ole32:
            def CoInitializeEx(self, _reserved: object, _mode: int) -> int:
                return 0

            def CoUninitialize(self) -> None:
                return None

        with (
            patch.object(
                nvidia_smi.ctypes,
                "windll",
                SimpleNamespace(shell32=Shell32(), kernel32=Kernel32(), ole32=Ole32()),
                create=True,
            ),
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
        ):
            code, message = nvidia_smi._run_windows_elevated(
                "C:\\NVIDIA\\trusted.exe", ["--probe"], 5
            )

        self.assertIsNone(code)
        self.assertIn("confirm", message.lower())

    def test_windows_uac_cancellation_is_reported(self) -> None:
        class Kernel32:
            def WaitForSingleObject(self, _handle: int, _timeout: int) -> int:
                return 0

            def GetExitCodeProcess(self, _handle: int, _code_pointer: object) -> int:
                return 0

            def GetLastError(self) -> int:
                return 1223

            def CloseHandle(self, _handle: int) -> int:
                return 1

        class Ole32:
            def CoInitializeEx(self, _reserved: object, _mode: int) -> int:
                return 0

            def CoUninitialize(self) -> None:
                return None

        with (
            patch.object(
                nvidia_smi.ctypes,
                "windll",
                SimpleNamespace(
                    shell32=SimpleNamespace(ShellExecuteExW=lambda _info_pointer: 0),
                    kernel32=Kernel32(),
                    ole32=Ole32(),
                ),
                create=True,
            ),
            patch.object(nvidia_smi.platform, "system", return_value="Windows"),
        ):
            code, message = nvidia_smi._run_windows_elevated(
                "C:\\NVIDIA\\trusted.exe", ["--probe"], 5
            )

        self.assertIsNone(code)
        self.assertIn("cancelled", message.lower())


class ConfigRegressionTests(unittest.TestCase):
    def test_load_config_validates_types_ranges_and_signed_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "refresh_rate": "fast",
                        "window_x": -1920,
                        "window_y": 80,
                        "window_width": 0,
                        "window_height": 700,
                        "dark_mode": "yes",
                        "alert_temp": 200,
                        "alert_util": 60,
                        "unknown_setting": "ignored",
                    }
                ),
                encoding="utf-8",
            )

            loaded = config.load_config(str(path))

        self.assertEqual(loaded["refresh_rate"], 3000)
        self.assertEqual(loaded["window_x"], -1920)
        self.assertEqual(loaded["window_y"], 80)
        self.assertEqual(loaded["window_width"], 850)
        self.assertEqual(loaded["window_height"], 700)
        self.assertFalse(loaded["dark_mode"])
        self.assertEqual(loaded["alert_temp"], 80)
        self.assertEqual(loaded["alert_util"], 60)
        self.assertNotIn("unknown_setting", loaded)

    def test_load_config_tolerates_invalid_utf8_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_bytes(b"{broken\xff")

            self.assertEqual(config.load_config(str(path)), config.DEFAULT_CONFIG)

    def test_failed_atomic_write_does_not_truncate_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            original = dict(config.DEFAULT_CONFIG)
            original["dark_mode"] = True
            path.write_text(json.dumps(original), encoding="utf-8")

            with patch.object(config.json, "dump", side_effect=OSError("disk full")):
                saved = config.save_config(
                    {**config.DEFAULT_CONFIG, "dark_mode": False}, str(path)
                )

            self.assertFalse(saved)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_save_config_rejects_invalid_values_without_replacing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            original = dict(config.DEFAULT_CONFIG)
            original["window_x"] = -1440
            path.write_text(json.dumps(original), encoding="utf-8")

            saved = config.save_config(
                {**config.DEFAULT_CONFIG, "refresh_rate": True}, str(path)
            )

            self.assertFalse(saved)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)


if __name__ == "__main__":
    unittest.main()
