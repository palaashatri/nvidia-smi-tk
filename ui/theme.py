"""UI theme definitions and visual helper utilities."""

from __future__ import annotations

import tkinter as tk
from typing import Any

LIGHT_THEME = {
    "bg": "#f2f5f0",
    "fg": "#1a1a1a",
    "card_bg": "#ffffff",
    "card_border": "#d6e4cc",
    "hover_bg": "#ecf3e7",
    "select_bg": "#5a8f00",
    "select_fg": "#ffffff",
    "accent": "#76b900",  # NVIDIA green
    "accent_light": "#e7f2d6",
    "tree_bg": "#f7faf4",
    "tree_fg": "#1a1a1a",
    "tree_select_bg": "#76b900",
    "tree_select_fg": "#ffffff",
    "tree_alt_bg": "#f2f7ed",
    "button_bg": "#76b900",
    "button_fg": "#ffffff",
    "button_hover": "#5a8f00",
    "text_bg": "#ffffff",
    "text_fg": "#1a1a1a",
    "header_gradient_start": "#6aa300",
    "header_gradient_end": "#4f7d00",
    "success": "#4c9e00",
    "warning": "#ff8c00",
    "danger": "#d13438",
    "info": "#76b900",
    "muted": "#5d6b55",
    "separator": "#d6e4cc",
}

DARK_THEME = {
    "bg": "#11160f",
    "fg": "#e6f0dd",
    "card_bg": "#1a2415",
    "card_border": "#273220",
    "hover_bg": "#1f2c1b",
    "select_bg": "#6fb300",
    "select_fg": "#ffffff",
    "accent": "#76b900",
    "accent_light": "#274014",
    "tree_bg": "#161f13",
    "tree_fg": "#dce9cf",
    "tree_select_bg": "#6fb300",
    "tree_select_fg": "#ffffff",
    "tree_alt_bg": "#1a2415",
    "button_bg": "#76b900",
    "button_fg": "#ffffff",
    "button_hover": "#5a8f00",
    "text_bg": "#131a11",
    "text_fg": "#dce9cf",
    "header_gradient_start": "#5f9700",
    "header_gradient_end": "#416900",
    "success": "#4c9e00",
    "warning": "#d79b00",
    "danger": "#c5524a",
    "info": "#76b900",
    "muted": "#7a8a72",
    "separator": "#273220",
}


def create_tooltip(widget: tk.Widget, text: str) -> None:
    """Attach a hover tooltip to a Tkinter widget."""
    tooltip: tk.Toplevel | None = None

    def on_enter(_event: Any) -> None:
        nonlocal tooltip
        x, y, _, _ = widget.bbox("insert") if hasattr(widget, "bbox") else (0, 0, 0, 0)
        x += widget.winfo_rootx() + 25
        y += widget.winfo_rooty() + 25

        tooltip = tk.Toplevel(widget)
        tooltip.wm_overrideredirect(True)
        tooltip.wm_geometry(f"+{x}+{y}")
        try:
            tooltip.attributes("-alpha", 0.95)
        except tk.TclError:
            pass

        label = tk.Label(
            tooltip,
            text=text,
            background="#1a1a1a",
            foreground="#e8e8e8",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 9),
            padx=12,
            pady=6,
        )
        label.pack()

    def on_leave(_event: Any) -> None:
        nonlocal tooltip
        if tooltip:
            tooltip.destroy()
            tooltip = None

    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)


def create_tray_icon_image(theme: dict[str, str]) -> Any:
    """Generate NVIDIA-themed tray icon image if Pillow is available."""
    try:
        from PIL import Image, ImageDraw

        size = 64
        img = Image.new("RGBA", (size, size), theme["accent"])
        draw = ImageDraw.Draw(img)
        margin = 10
        draw.ellipse([margin, margin, size - margin, size - margin], fill="white")
        inner_margin = 22
        draw.ellipse(
            [inner_margin, inner_margin, size - inner_margin, size - inner_margin],
            fill=theme["accent"],
        )
        return img
    except ImportError:
        return None
