"""Configuration management for nvidia-smi-tk."""

import json
import os
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


def load_config(config_file: str = CONFIG_FILE) -> dict[str, Any]:
    """Load configuration from JSON file with fallback to defaults."""
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                user_config = json.load(f)
                if isinstance(user_config, dict):
                    config.update(user_config)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: dict[str, Any], config_file: str = CONFIG_FILE) -> bool:
    """Save configuration dictionary to JSON file."""
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return True
    except OSError:
        return False
