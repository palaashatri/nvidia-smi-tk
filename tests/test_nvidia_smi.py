"""Unit tests for nvidia_smi pure parsing and utility functions."""

from __future__ import annotations

import os
import tempfile
import unittest

from config import DEFAULT_CONFIG, load_config, save_config
from nvidia_smi import (
    color_for_percent,
    color_for_power,
    color_for_temp,
    format_memory,
    format_power,
    parse_gpu_metrics,
    parse_numeric,
    parse_power_limits,
    parse_processes,
    set_power_limit,
)

SAMPLE_SINGLE_GPU = (
    "0, NVIDIA GeForce RTX 3080, 25, 2048, 10240, 42.0, 85.0, 100.0, "
    "40, 1710, 9501\n"
)

SAMPLE_MULTI_GPU = (
    "0, NVIDIA GeForce RTX 3080, 25, 2048, 10240, 42.0, 85.0, 100.0, "
    "40, 1710, 9501\n"
    "1, NVIDIA GeForce RTX 3070, 15, 1024, 8192, 38.0, 45.0, 220.0, "
    "30, 1500, 7000\n"
)

SAMPLE_LAPTOP_NA = (
    "0, NVIDIA GeForce RTX 3060 Laptop GPU, 10, 1500, 6144, 55.0, "
    "[N/A], [N/A], [N/A], 1425, 6000\n"
)

SAMPLE_NOT_SUPPORTED = (
    "0, NVIDIA A100-SXM4-40GB, 99, 35000, 40960, 60.0, 250.0, 400.0, "
    "[Not Supported], 1410, 1215\n"
)

SAMPLE_LEGACY_9COL = (
    "20, 1000, 8000, 45, 50, 200, 35, 1200, 5000\n"
)

SAMPLE_POWER_OUTPUT = """
==============NVSMI LOG==============

Timestamp                                              : Sat Oct 24 00:59:49 2026
Attached GPUs                                          : 1
GPU 00000000:01:00.0
    GPU Power Readings
        Average Power Draw                             : 40.37 W
        Instantaneous Power Draw                       : 64.79 W
        GPU Ceiling Power Limit
            Current Power Limit                        : 100.00 W
            Requested Power Limit                      : 100.00 W
            Default Power Limit                        : 320.00 W
        Min Power Limit                                : 100.00 W
        Max Power Limit                                : 370.00 W
"""

SAMPLE_MULTI_GPU_POWER_OUTPUT = """
==============NVSMI LOG==============
Attached GPUs                                          : 2
GPU 00000000:01:00.0
    GPU Ceiling Power Limit
        Current Power Limit                        : 100.00 W
    Min Power Limit                                : 100.00 W
    Max Power Limit                                : 370.00 W
GPU 00000000:02:00.0
    GPU Ceiling Power Limit
        Current Power Limit                        : 220.00 W
    Min Power Limit                                : 125.00 W
    Max Power Limit                                : 250.00 W
"""

SAMPLE_PROCESS_OUTPUT_WINDOWS = """
1720, ....0.4283.62\\msedgewebview2.exe, N/A
9520, ...2txyewy\\CrossDeviceResume.exe, [N/A]
10748, ...s\\antigravity\\Antigravity.exe, N/A
"""

SAMPLE_PROCESS_OUTPUT_LINUX = """
0, 14502, /usr/bin/python3, 1500 MiB
1, 16789, /usr/local/bin/blender, 3200 MiB
"""


def test_parse_numeric():
    assert parse_numeric("42.5") == 42.5
    assert parse_numeric("42.5", is_int=True) == 42
    assert parse_numeric("[N/A]") is None
    assert parse_numeric("[Not Supported]") is None
    assert parse_numeric("N/A") is None
    assert parse_numeric("not supported") is None
    assert parse_numeric("Unknown") is None
    assert parse_numeric("", default=0) == 0
    assert parse_numeric(None, default=10) == 10
    assert parse_numeric("invalid", default=-1) == -1


def test_parse_single_gpu():
    gpus = parse_gpu_metrics(SAMPLE_SINGLE_GPU)
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu["index"] == 0
    assert gpu["name"] == "NVIDIA GeForce RTX 3080"
    assert gpu["utilization"] == 25.0
    assert gpu["mem_used"] == 2048.0
    assert gpu["mem_total"] == 10240.0
    assert gpu["temperature"] == 42
    assert gpu["power_draw"] == 85.0
    assert gpu["power_limit"] == 100.0
    assert gpu["fan_speed"] == 40.0
    assert gpu["clock_gpu"] == 1710.0
    assert gpu["clock_mem"] == 9501.0

    # Test backward compatibility dict-like access
    assert gpus.get("utilization") == 25.0
    assert gpus["temperature"] == 42


def test_parse_multi_gpu():
    gpus = parse_gpu_metrics(SAMPLE_MULTI_GPU)
    assert len(gpus) == 2

    gpu0 = gpus[0]
    assert gpu0["index"] == 0
    assert gpu0["name"] == "NVIDIA GeForce RTX 3080"
    assert gpu0["utilization"] == 25.0
    assert gpu0["mem_total"] == 10240.0

    gpu1 = gpus[1]
    assert gpu1["index"] == 1
    assert gpu1["name"] == "NVIDIA GeForce RTX 3070"
    assert gpu1["utilization"] == 15.0
    assert gpu1["mem_total"] == 8192.0
    assert gpu1["power_limit"] == 220.0


def test_parse_laptop_na_fields():
    gpus = parse_gpu_metrics(SAMPLE_LAPTOP_NA)
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu["name"] == "NVIDIA GeForce RTX 3060 Laptop GPU"
    assert gpu["utilization"] == 10.0
    assert gpu["temperature"] == 55
    # Defensive parsing for fan and power
    assert gpu["fan_speed"] is None
    assert gpu["power_draw"] is None
    assert gpu["power_limit"] is None


def test_parse_not_supported():
    gpus = parse_gpu_metrics(SAMPLE_NOT_SUPPORTED)
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu["fan_speed"] is None
    assert gpu["power_draw"] == 250.0
    assert gpu["power_limit"] == 400.0


def test_parse_legacy_9col():
    gpus = parse_gpu_metrics(SAMPLE_LEGACY_9COL)
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu["index"] == 0
    assert gpu["utilization"] == 20.0
    assert gpu["mem_used"] == 1000.0
    assert gpu["mem_total"] == 8000.0


def test_parse_error_or_empty():
    assert len(parse_gpu_metrics("")) == 0
    assert len(parse_gpu_metrics("Error: nvidia-smi not found.")) == 0
    gpus = parse_gpu_metrics("")
    assert gpus.get("utilization") is None


def test_parse_processes_windows():
    procs = parse_processes(SAMPLE_PROCESS_OUTPUT_WINDOWS)
    assert len(procs) == 3
    assert procs[0]["pid"] == "1720"
    assert "msedgewebview2.exe" in procs[0]["name"]
    assert procs[0]["mem"] == "N/A"
    assert procs[1]["pid"] == "9520"


def test_parse_processes_linux():
    procs = parse_processes(SAMPLE_PROCESS_OUTPUT_LINUX)
    assert len(procs) == 2
    assert procs[0]["gpu"] == "0"
    assert procs[0]["pid"] == "14502"
    assert procs[0]["mem"] == "1500 MiB"
    assert procs[1]["gpu"] == "1"
    assert procs[1]["pid"] == "16789"
    assert procs[1]["mem"] == "3200 MiB"


def test_parse_power_limits_single():
    cur, min_lim, max_lim = parse_power_limits(SAMPLE_POWER_OUTPUT)
    assert cur == 100.0
    assert min_lim == 100.0
    assert max_lim == 370.0


def test_parse_power_limits_multi():
    cur0, min0, max0 = parse_power_limits(
        SAMPLE_MULTI_GPU_POWER_OUTPUT, gpu_index=0
    )
    assert cur0 == 100.0
    assert min0 == 100.0
    assert max0 == 370.0

    cur1, min1, max1 = parse_power_limits(
        SAMPLE_MULTI_GPU_POWER_OUTPUT, gpu_index=1
    )
    assert cur1 == 220.0
    assert min1 == 125.0
    assert max1 == 250.0


def test_power_limit_validation():
    ok, err = set_power_limit(
        50.0, gpu_index=0, min_limit=100.0, max_limit=370.0
    )
    assert not ok
    assert "Value must be between 100.0 and 370.0 W" in err

    ok2, err2 = set_power_limit(
        400.0, gpu_index=0, min_limit=100.0, max_limit=370.0
    )
    assert not ok2
    assert "Value must be between 100.0 and 370.0 W" in err2


def test_format_memory():
    text, pct = format_memory(2048, 10240)
    assert "2.0 GB / 10.0 GB" in text
    assert round(pct, 1) == 20.0

    text_mb, _ = format_memory(512, 1000)
    assert "512 MB / 1000 MB" in text_mb

    text_na, pct_na = format_memory(None, 10240)
    assert text_na == "N/A"
    assert pct_na == 0.0

    text_na2, pct_na2 = format_memory(2048, 0)
    assert text_na2 == "N/A"
    assert pct_na2 == 0.0


def test_format_power():
    text, pct = format_power(50.0, 100.0)
    assert "50.0 W / 100.0 W" in text
    assert pct == 50.0

    text_na, _ = format_power(None, None)
    assert text_na == "N/A"

    text_na_draw, _ = format_power(None, 100.0)
    assert "N/A / 100.0 W" in text_na_draw

    text_na_limit, _ = format_power(50.0, None)
    assert "50.0 W / N/A" in text_na_limit


def test_color_helpers():
    assert color_for_percent(10.0) == "green"
    assert color_for_percent(75.0) == "orange"
    assert color_for_percent(95.0) == "red"
    assert color_for_percent(None) == "gray"

    assert color_for_temp(40) == "green"
    assert color_for_temp(70) == "orange"
    assert color_for_temp(85) == "red"
    assert color_for_temp(None) == "gray"

    assert color_for_power(50.0, 100.0) == "green"
    assert color_for_power(85.0, 100.0) == "orange"
    assert color_for_power(98.0, 100.0) == "red"
    assert color_for_power(None, 100.0) == "gray"
    assert color_for_power(50.0, None) == "gray"


def test_config_load_and_save():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_cfg = os.path.join(tmpdir, "config.json")
        cfg = load_config(tmp_cfg)
        assert cfg["refresh_rate"] == DEFAULT_CONFIG["refresh_rate"]
        assert not cfg["dark_mode"]

        cfg["dark_mode"] = True
        cfg["refresh_rate"] = 5000
        assert save_config(cfg, tmp_cfg)

        reloaded = load_config(tmp_cfg)
        assert reloaded["dark_mode"] is True
        assert reloaded["refresh_rate"] == 5000


class NvidiaSmiTestCase(unittest.TestCase):
    def test_all_pure_functions(self):
        test_parse_numeric()
        test_parse_single_gpu()
        test_parse_multi_gpu()
        test_parse_laptop_na_fields()
        test_parse_not_supported()
        test_parse_legacy_9col()
        test_parse_error_or_empty()
        test_parse_processes_windows()
        test_parse_processes_linux()
        test_parse_power_limits_single()
        test_parse_power_limits_multi()
        test_power_limit_validation()
        test_format_memory()
        test_format_power()
        test_color_helpers()
        test_config_load_and_save()


if __name__ == "__main__":
    unittest.main()
