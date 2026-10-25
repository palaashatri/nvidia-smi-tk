"""Unit and regression tests for nvidia_smi using captured fixtures."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

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

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def read_fixture(filename: str) -> str:
    """Load text content of a fixture file."""
    path = FIXTURES_DIR / filename
    return path.read_text(encoding="utf-8")


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


def test_parse_real_rtx3080_fixture():
    raw = read_fixture("rtx3080_win_query_gpu_12col.csv")
    gpus = parse_gpu_metrics(raw)
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu["index"] == 0
    assert gpu["uuid"] == "GPU-7d01e0a7-c69f-519b-c6c7-f6d49fcdaf6f"
    assert gpu["name"] == "NVIDIA GeForce RTX 3080"
    assert gpu["utilization"] == 42.0
    assert gpu["mem_used"] == 1972.0
    assert gpu["mem_total"] == 10240.0
    assert gpu["temperature"] == 41
    assert gpu["power_draw"] == 33.73
    assert gpu["power_limit"] == 100.0
    assert gpu["fan_speed"] == 40.0
    assert gpu["clock_gpu"] == 795.0
    assert gpu["clock_mem"] == 810.0


def test_parse_multi_gpu_fixture():
    raw = read_fixture("multi_gpu_query.csv")
    gpus = parse_gpu_metrics(raw)
    assert len(gpus) == 2

    gpu0 = gpus[0]
    assert gpu0["index"] == 0
    assert gpu0["uuid"] == "GPU-7d01e0a7-c69f-519b-c6c7-f6d49fcdaf6f"
    assert gpu0["name"] == "NVIDIA GeForce RTX 3080"
    assert gpu0["utilization"] == 45.0
    assert gpu0["mem_total"] == 10240.0

    gpu1 = gpus[1]
    assert gpu1["index"] == 1
    assert gpu1["uuid"] == "GPU-8e12f1b8-d70a-620c-d7d8-e7e50fcebf70"
    assert gpu1["name"] == "NVIDIA GeForce RTX 3070"
    assert gpu1["utilization"] == 15.0
    assert gpu1["mem_total"] == 8192.0
    assert gpu1["power_limit"] == 220.0


def test_parse_laptop_na_fixture():
    raw = read_fixture("laptop_na_query.csv")
    gpus = parse_gpu_metrics(raw)
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu["name"] == "NVIDIA GeForce RTX 3060 Laptop GPU"
    assert gpu["utilization"] == 12.0
    assert gpu["temperature"] == 52
    assert gpu["fan_speed"] is None
    assert gpu["power_draw"] is None
    assert gpu["power_limit"] is None


def test_parse_server_not_supported_fixture():
    raw = read_fixture("server_not_supported.csv")
    gpus = parse_gpu_metrics(raw)
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu["name"] == "NVIDIA A100-SXM4-40GB"
    assert gpu["fan_speed"] is None
    assert gpu["power_draw"] == 250.0
    assert gpu["power_limit"] == 400.0


def test_parse_legacy_9col_fixture():
    raw = read_fixture("legacy_9col.csv")
    gpus = parse_gpu_metrics(raw)
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu["index"] == 0
    assert gpu["utilization"] == 20.0
    assert gpu["mem_used"] == 1000.0
    assert gpu["mem_total"] == 8000.0


def test_parse_no_devices_found():
    raw = read_fixture("no_devices_found.txt")
    gpus = parse_gpu_metrics(raw)
    assert len(gpus) == 0


def test_parse_empty_and_error():
    assert len(parse_gpu_metrics("")) == 0
    assert len(parse_gpu_metrics("Error: nvidia-smi not found.")) == 0


def test_parse_real_compute_apps():
    raw = read_fixture("rtx3080_win_compute_apps.csv")
    uuid_map = {"GPU-7d01e0a7-c69f-519b-c6c7-f6d49fcdaf6f": 0}
    procs = parse_processes(raw, uuid_to_index=uuid_map)
    assert len(procs) == 5
    assert procs[0]["gpu"] == "0"
    assert procs[0]["pid"] == "11688"
    assert "ShellHost.exe" in procs[0]["name"]
    assert procs[0]["mem"] == "N/A"
    assert procs[2]["pid"] == "11424"
    assert "explorer.exe" in procs[2]["name"]


def test_parse_processes_multi_gpu_mapping():
    raw = (
        "GPU-7d01e0a7-c69f-519b-c6c7-f6d49fcdaf6f, 1001, python.exe, 500\n"
        "GPU-8e12f1b8-d70a-620c-d7d8-e7e50fcebf70, 2002, blender.exe, 1200\n"
    )
    uuid_map = {
        "GPU-7d01e0a7-c69f-519b-c6c7-f6d49fcdaf6f": 0,
        "GPU-8e12f1b8-d70a-620c-d7d8-e7e50fcebf70": 1,
    }
    procs = parse_processes(raw, uuid_to_index=uuid_map)
    assert len(procs) == 2
    assert procs[0]["gpu"] == "0"
    assert procs[0]["name"] == "python.exe"
    assert procs[1]["gpu"] == "1"
    assert procs[1]["name"] == "blender.exe"


def test_parse_power_limits_real():
    raw = read_fixture("rtx3080_win_q_power.txt")
    cur, min_lim, max_lim = parse_power_limits(raw, gpu_index=0)
    assert cur == 100.0
    assert min_lim == 100.0
    assert max_lim == 370.0


def test_power_limit_validation():
    ok, err = set_power_limit(50.0, gpu_index=0, min_limit=100.0, max_limit=370.0)
    assert not ok
    assert "Value must be between 100.0 and 370.0 W" in err

    ok2, err2 = set_power_limit(400.0, gpu_index=0, min_limit=100.0, max_limit=370.0)
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
        test_parse_real_rtx3080_fixture()
        test_parse_multi_gpu_fixture()
        test_parse_laptop_na_fixture()
        test_parse_server_not_supported_fixture()
        test_parse_legacy_9col_fixture()
        test_parse_no_devices_found()
        test_parse_empty_and_error()
        test_parse_real_compute_apps()
        test_parse_processes_multi_gpu_mapping()
        test_parse_power_limits_real()
        test_power_limit_validation()
        test_format_memory()
        test_format_power()
        test_color_helpers()
        test_config_load_and_save()


if __name__ == "__main__":
    unittest.main()
