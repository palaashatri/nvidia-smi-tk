"""Configuration management for nvidia-smi-tk."""

import json
import os
import tempfile
from typing import Any

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".nvidia_smi_tk_config.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "refresh_rate": 3000,
    "window_x": 100,
    "window_y": 100,
    "window_width": 850,
    "window_height": 650,
    "dark_mode": False,
    "always_on_top": False,
    "alert_temp": 80,
    "alert_util": 90,
    "alert_enabled": True,
}

_INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "refresh_rate": (500, 60000),
    "window_x": (-100000, 100000),
    "window_y": (-100000, 100000),
    "window_width": (200, 32768),
    "window_height": (200, 32768),
    "alert_temp": (0, 150),
    "alert_util": (0, 100),
}
_BOOLEAN_FIELDS = {"dark_mode", "always_on_top", "alert_enabled"}


def _is_valid_field(key: str, value: Any) -> bool:
    """Return whether one known config value has the expected type and range."""
    if key in _BOOLEAN_FIELDS:
        return isinstance(value, bool)
    bounds = _INTEGER_RANGES.get(key)
    return (
        bounds is not None
        and isinstance(value, int)
        and not isinstance(value, bool)
        and bounds[0] <= value <= bounds[1]
    )


def _normalize_config(source: Any) -> dict[str, Any]:
    """Apply valid known values to defaults; ignore unknown or invalid data."""
    normalized = dict(DEFAULT_CONFIG)
    if not isinstance(source, dict):
        return normalized
    for key in DEFAULT_CONFIG:
        if key in source and _is_valid_field(key, source[key]):
            normalized[key] = source[key]
    return normalized


def load_config(config_file: str = CONFIG_FILE) -> dict[str, Any]:
    """Load validated configuration, falling back to defaults for bad values."""
    try:
        with open(config_file, "r", encoding="utf-8") as config_stream:
            user_config = json.load(config_stream)
    except (json.JSONDecodeError, OSError, UnicodeError):
        return dict(DEFAULT_CONFIG)
    return _normalize_config(user_config)


def save_config(config: dict[str, Any], config_file: str = CONFIG_FILE) -> bool:
    """Atomically save a validated config without truncating the prior file."""
    if not isinstance(config, dict):
        return False
    if any(
        not _is_valid_field(key, value)
        for key, value in config.items()
        if key in DEFAULT_CONFIG
    ):
        return False

    normalized = _normalize_config(config)
    directory = os.path.dirname(os.path.abspath(config_file))
    filename = os.path.basename(config_file)
    temporary_path: str | None = None

    try:
        file_descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{filename}.", suffix=".tmp", dir=directory
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(normalized, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, config_file)
        temporary_path = None
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
