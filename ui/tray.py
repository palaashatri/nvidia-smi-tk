"""System tray icon integration using Pystray (optional dependency)."""

from __future__ import annotations

import threading
from typing import Any

from ui.theme import DARK_THEME, LIGHT_THEME, create_tray_icon_image

try:
    import pystray

    PYSTRAY_AVAILABLE = True
except Exception:
    pystray = None
    PYSTRAY_AVAILABLE = False


def setup_tray_icon(app_instance: Any) -> Any:
    """Set up system tray icon with context menu if pystray is installed."""
    if not PYSTRAY_AVAILABLE:
        return None

    try:
        event_queue = getattr(app_instance, "tray_event_queue", None)
        if event_queue is None:
            return None

        theme = DARK_THEME if app_instance.config.get("dark_mode") else LIGHT_THEME
        image = create_tray_icon_image(theme)
        if image is None:
            return None

        def show_window(_icon: Any, _item: Any) -> None:
            event_queue.put("show")

        def quit_app(icon_obj: Any, _item: Any) -> None:
            icon_obj.stop()
            event_queue.put("quit")

        menu = pystray.Menu(
            pystray.MenuItem("Show", show_window, default=True),
            pystray.MenuItem("Quit", quit_app),
        )

        icon = pystray.Icon("nvidia_monitor", image, "NVIDIA GPU Monitor", menu)

        def run_tray() -> None:
            try:
                icon.run()
            except Exception:
                # Tray support is optional; backend failures must not stop the monitor.
                return

        tray_thread = threading.Thread(target=run_tray, daemon=True)
        tray_thread.start()
        return icon
    except Exception:
        return None
