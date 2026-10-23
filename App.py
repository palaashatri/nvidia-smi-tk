"""NVIDIA-SMI GUI Monitor - Real-time GPU monitoring tool using Tkinter."""

from __future__ import annotations

import csv
import json
import os
import platform
import re
import subprocess
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import Menu, messagebox, ttk
from typing import Any

from config import load_config, save_config
from nvidia_smi import (
    color_for_percent,
    color_for_power,
    color_for_temp,
    format_memory,
    format_power,
    get_gpu_info,
    get_nvidia_smi_output,
    get_power_limits,
    parse_gpu_metrics,
    parse_processes,
    set_power_limit,
)

try:
    import matplotlib

    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

try:
    import pystray
    from PIL import Image, ImageDraw

    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False

try:
    from plyer import notification

    PLYER_AVAILABLE = True
except ImportError:
    PLYER_AVAILABLE = False

VERSION = "2.1.0"
HISTORY_SIZE = 60

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

power_win = None
gpu_data_cache: dict[str, Any] = {}
static_cache: dict[str, Any] = {}
update_thread = None
stop_update = False
history_data = {
    "time": deque(maxlen=HISTORY_SIZE),
    "utilization": deque(maxlen=HISTORY_SIZE),
    "temperature": deque(maxlen=HISTORY_SIZE),
    "power": deque(maxlen=HISTORY_SIZE),
    "memory": deque(maxlen=HISTORY_SIZE),
}
last_alert_time: dict[str, float] = {}
tray_icon = None


def export_to_csv(filename: str) -> bool:
    """Export historical GPU metrics to CSV."""
    try:
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "Timestamp",
                    "Utilization (%)",
                    "Temperature (C)",
                    "Power (W)",
                    "Memory (%)",
                ]
            )
            for i in range(len(history_data["time"])):
                writer.writerow(
                    [
                        history_data["time"][i],
                        history_data["utilization"][i],
                        history_data["temperature"][i],
                        history_data["power"][i],
                        history_data["memory"][i],
                    ]
                )
        return True
    except OSError:
        return False


def export_to_json(filename: str) -> bool:
    """Export historical GPU metrics to JSON."""
    try:
        data = {
            "export_time": datetime.now().isoformat(),
            "gpu_name": static_cache.get("gpu_name", "Unknown"),
            "data": [],
        }
        for i in range(len(history_data["time"])):
            data["data"].append(
                {
                    "timestamp": history_data["time"][i],
                    "utilization": history_data["utilization"][i],
                    "temperature": history_data["temperature"][i],
                    "power": history_data["power"][i],
                    "memory": history_data["memory"][i],
                }
            )
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError:
        return False


def show_notification(title: str, message: str) -> None:
    """Trigger desktop alert notification."""
    if not PLYER_AVAILABLE:
        return
    try:
        notification.notify(
            title=title,
            message=message,
            app_name="NVIDIA-SMI Monitor",
            timeout=5,
        )
    except Exception:
        pass


def check_alerts(metrics: dict[str, Any], config: dict[str, Any]) -> None:
    """Check temperature and utilization thresholds for desktop notifications."""
    current_time = time.time()

    if not config.get("alert_enabled", True):
        return

    temp = metrics.get("temperature")
    util = metrics.get("utilization")
    alert_temp = config.get("alert_temp", 80)
    alert_util = config.get("alert_util", 90)

    if (
        temp is not None
        and temp >= alert_temp
        and current_time - last_alert_time.get("temp", 0) > 300
    ):
        show_notification(
            "Temperature Alert",
            f"GPU temperature is {temp}°C (threshold: {alert_temp}°C)",
        )
        last_alert_time["temp"] = current_time

    if (
        util is not None
        and util >= alert_util
        and current_time - last_alert_time.get("util", 0) > 300
    ):
        show_notification(
            "Utilization Alert",
            f"GPU utilization is {util:.1f}% (threshold: {alert_util}%)",
        )
        last_alert_time["util"] = current_time


def create_tooltip(widget: tk.Widget, text: str) -> None:
    """Attach a hover tooltip to a Tkinter widget."""
    tooltip: tk.Toplevel | None = None

    def on_enter(_event):
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

    def on_leave(_event):
        nonlocal tooltip
        if tooltip:
            tooltip.destroy()
            tooltip = None

    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)


def create_tray_icon_image(theme: dict[str, str]):
    """Generate NVIDIA-themed tray icon image."""
    if not PYSTRAY_AVAILABLE or not Image:
        return None
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


def open_power_limit_window(
    parent_app: GPUMonitorApp, initial_gpu_index: int = 0
) -> None:
    """Open Power Limit dialog with GPU selector and min/max validation."""
    global power_win
    if power_win is not None and tk.Toplevel.winfo_exists(power_win):
        power_win.lift()
        return

    power_win = tk.Toplevel(parent_app.root)
    power_win.title("Power Limit Configuration")
    power_win.resizable(False, False)
    power_win.grab_set()

    theme = DARK_THEME if parent_app.config["dark_mode"] else LIGHT_THEME
    power_win.configure(bg=theme["bg"])

    main_frame = ttk.Frame(power_win)
    main_frame.pack(padx=25, pady=20, fill="both", expand=True)

    title = ttk.Label(main_frame, text="GPU POWER LIMIT", style="Title.TLabel")
    title.pack(pady=(0, 15))

    detected_gpus = parent_app.current_gpus or [{"index": 0, "name": "GPU 0"}]
    selected_gpu_var = tk.StringVar(value=str(initial_gpu_index))

    if len(detected_gpus) > 1:
        selector_frame = ttk.Frame(main_frame)
        selector_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(selector_frame, text="Select GPU:", font=("Segoe UI", 9)).pack(
            side="left", padx=(0, 8)
        )
        gpu_options = [
            f"GPU {g['index']}: {g.get('name', 'NVIDIA GPU')}" for g in detected_gpus
        ]
        combo = ttk.Combobox(
            selector_frame, values=gpu_options, state="readonly", width=35
        )
        combo.current(min(initial_gpu_index, len(gpu_options) - 1))
        combo.pack(side="left", fill="x", expand=True)

    info_frame = ttk.Frame(main_frame, style="Card.TFrame")
    info_frame.pack(fill="x", pady=(0, 15), ipady=10, ipadx=10)

    cur_label = ttk.Label(
        info_frame, text="Loading limits...", font=("Segoe UI Semibold", 10)
    )
    cur_label.pack(pady=3)
    range_label = ttk.Label(
        info_frame, text="", font=("Segoe UI", 9), foreground=theme["muted"]
    )
    range_label.pack(pady=3)

    cur_limits: dict[str, float | None] = {"cur": None, "min": None, "max": None}

    def refresh_limits_for_gpu(gpu_idx: int):
        c, mn, mx = get_power_limits(gpu_index=gpu_idx)
        cur_limits["cur"] = c
        cur_limits["min"] = mn
        cur_limits["max"] = mx
        cur_str = f"{c} W" if c is not None else "Unknown"
        range_str = (
            f"{mn} - {mx} W"
            if mn is not None and mx is not None
            else "Unknown"
        )
        cur_label.config(text=f"Current: {cur_str}")
        range_label.config(text=f"Allowed Range: {range_str}")

    refresh_limits_for_gpu(initial_gpu_index)

    if len(detected_gpus) > 1:

        def on_gpu_changed(_event):
            idx = combo.current()
            if 0 <= idx < len(detected_gpus):
                target_idx = detected_gpus[idx]["index"]
                selected_gpu_var.set(str(target_idx))
                refresh_limits_for_gpu(target_idx)

        combo.bind("<<ComboboxSelected>>", on_gpu_changed)

    ttk.Label(main_frame, text="New Power Limit (Watts):", font=("Segoe UI", 9)).pack(
        pady=(0, 5), anchor="w"
    )
    entry = ttk.Entry(main_frame, width=20, font=("Segoe UI", 11))
    entry.pack(pady=(0, 10), ipady=4, fill="x")
    entry.focus()

    msg = tk.StringVar()
    msg_label = ttk.Label(
        main_frame, textvariable=msg, font=("Segoe UI", 9), wraplength=350
    )
    msg_label.pack(pady=8)

    def apply_limit():
        try:
            val = float(entry.get())
            idx = int(selected_gpu_var.get())
            ok, out = set_power_limit(
                val,
                gpu_index=idx,
                min_limit=cur_limits["min"],
                max_limit=cur_limits["max"],
            )
            if ok:
                msg.set("Power limit applied successfully.")
                msg_label.config(foreground=theme["success"])
                refresh_limits_for_gpu(idx)
            else:
                msg.set(f"Failed: {out}")
                msg_label.config(foreground=theme["danger"])
        except ValueError:
            msg.set("Invalid input - please enter a valid number.")
            msg_label.config(foreground=theme["danger"])

    button_frame = ttk.Frame(main_frame)
    button_frame.pack(pady=15)
    ttk.Button(
        button_frame, text="Apply Changes", style="Accent.TButton", command=apply_limit
    ).pack(side="left", padx=5)
    ttk.Button(button_frame, text="Cancel", command=lambda: power_win.destroy()).pack(
        side="left", padx=5
    )

    entry.bind("<Return>", lambda _e: apply_limit())
    entry.bind("<Escape>", lambda _e: power_win.destroy())

    if platform.system() == "Windows":
        note_text = "Requires administrator privileges to modify power limits."
    else:
        note_text = "Uses pkexec (polkit) or sudo to authenticate privilege elevation."

    ttk.Label(
        main_frame, text=note_text, font=("Segoe UI", 8), foreground=theme["muted"]
    ).pack(pady=(5, 0))

    def on_close():
        global power_win
        power_win.destroy()
        power_win = None

    power_win.protocol("WM_DELETE_WINDOW", on_close)


class GPUMonitorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()
        self.labels: dict[str, Any] = {}
        self.gpu_cards: dict[int, dict[str, Any]] = {}
        self.current_gpus: list[dict[str, Any]] = []
        self.sort_column = None
        self.sort_reverse = False
        self.graph_window = None
        self.last_process_list: list[dict[str, str]] = []
        self.hover_row = None
        self.proc_search_query = ""

        self.root.title(f"NVIDIA-SMI GPU Monitor v{VERSION}")
        self.root.geometry(
            f"{self.config['window_width']}x{self.config['window_height']}+"
            f"{self.config['window_x']}+{self.config['window_y']}"
        )

        self.setup_menu()
        self.apply_theme()
        self.init_gui()
        self.setup_keyboard_shortcuts()

        if self.config.get("always_on_top", False):
            self.root.attributes("-topmost", True)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.start_background_update()

    def setup_menu(self):
        menubar = Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Export to CSV", command=self.export_csv)
        file_menu.add_command(label="Export to JSON", command=self.export_json)
        file_menu.add_separator()
        file_menu.add_command(
            label="Exit", command=self.on_closing, accelerator="Ctrl+Q"
        )

        view_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(
            label="Toggle Dark Mode",
            command=self.toggle_theme,
            accelerator="Ctrl+D",
        )
        view_menu.add_checkbutton(
            label="Always on Top",
            command=self.toggle_always_on_top,
            variable=tk.BooleanVar(value=self.config.get("always_on_top", False)),
        )
        view_menu.add_command(
            label="Refresh", command=self.manual_refresh, accelerator="F5"
        )
        if MATPLOTLIB_AVAILABLE:
            view_menu.add_command(
                label="Show Graphs",
                command=self.show_graphs,
                accelerator="Ctrl+G",
            )

        settings_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        settings_menu.add_command(
            label="Adjust Refresh Rate", command=self.adjust_refresh_rate
        )
        settings_menu.add_command(label="Alert Settings", command=self.alert_settings)
        settings_menu.add_command(
            label="Power Limit", command=lambda: open_power_limit_window(self)
        )

        help_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)

    def setup_keyboard_shortcuts(self):
        self.root.bind("<F5>", lambda _e: self.manual_refresh())
        self.root.bind("<Control-q>", lambda _e: self.on_closing())
        self.root.bind("<Control-d>", lambda _e: self.toggle_theme())
        if MATPLOTLIB_AVAILABLE:
            self.root.bind("<Control-g>", lambda _e: self.show_graphs())

    def apply_theme(self):
        self.current_theme = DARK_THEME if self.config["dark_mode"] else LIGHT_THEME
        theme = self.current_theme
        self.root.configure(bg=theme["bg"])

        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            ".",
            background=theme["bg"],
            foreground=theme["fg"],
            borderwidth=0,
            relief="flat",
        )
        style.configure("TFrame", background=theme["bg"])
        style.configure("TLabel", background=theme["bg"], foreground=theme["fg"])

        style.configure(
            "Title.TLabel",
            background=theme["bg"],
            foreground=theme["accent"],
            font=("Segoe UI", 12, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=theme["bg"],
            foreground=theme["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Header.TLabel",
            background=theme["card_bg"],
            foreground=theme["fg"],
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Metric.TLabel",
            background=theme["card_bg"],
            foreground=theme["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Value.TLabel",
            background=theme["card_bg"],
            foreground=theme["fg"],
            font=("Segoe UI Semibold", 11),
        )
        style.configure(
            "Accent.TButton",
            background=theme["button_bg"],
            foreground=theme["button_fg"],
            borderwidth=0,
            relief="flat",
            padding=(16, 6),
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", theme["button_hover"]),
                ("pressed", theme["button_hover"]),
            ],
            relief=[("pressed", "flat")],
        )

        style.configure(
            "TButton",
            background=theme["card_bg"],
            foreground=theme["fg"],
            borderwidth=1,
            relief="flat",
            padding=(10, 5),
        )
        style.map(
            "TButton",
            background=[
                ("active", theme["hover_bg"]),
                ("pressed", theme["select_bg"]),
            ],
            foreground=[("active", theme["fg"])],
        )

        style.configure(
            "Treeview",
            background=theme["tree_bg"],
            foreground=theme["tree_fg"],
            fieldbackground=theme["tree_bg"],
            borderwidth=0,
            relief="flat",
            rowheight=26,
        )
        style.configure(
            "Treeview.Heading",
            background=theme["card_bg"],
            foreground=theme["accent"],
            borderwidth=0,
            relief="flat",
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "Treeview.Heading",
            background=[("active", theme["hover_bg"])],
            relief=[("active", "flat")],
        )
        style.map(
            "Treeview",
            background=[("selected", theme["tree_select_bg"])],
            foreground=[("selected", theme["tree_select_fg"])],
        )

        style.configure(
            "Card.TFrame",
            background=theme["card_bg"],
            relief="flat",
            borderwidth=1,
            bordercolor=theme["card_border"],
        )
        style.configure(
            "Separator.TFrame",
            background=theme["separator"],
            relief="flat",
        )
        style.configure(
            "Metric.Horizontal.TProgressbar",
            background=theme["accent"],
            troughcolor=theme["card_bg"],
            bordercolor=theme["card_border"],
            lightcolor=theme["accent"],
            darkcolor=theme["accent"],
            thickness=6,
        )
        style.configure("MainContainer.TFrame", background=theme["bg"])

    def update_text_widget_colors(self):
        theme = DARK_THEME if self.config["dark_mode"] else LIGHT_THEME
        if "full_output_text" in self.labels:
            self.labels["full_output_text"].config(
                bg=theme["text_bg"], fg=theme["text_fg"]
            )

    def toggle_theme(self):
        self.config["dark_mode"] = not self.config["dark_mode"]
        save_config(self.config)
        self.apply_theme()
        self.update_text_widget_colors()
        self.rebuild_gpu_cards()

    def toggle_always_on_top(self):
        self.config["always_on_top"] = not self.config.get("always_on_top", False)
        self.root.attributes("-topmost", self.config["always_on_top"])
        save_config(self.config)

    def adjust_refresh_rate(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Adjust Refresh Rate")
        dialog.resizable(False, False)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text=f"Current refresh rate: {self.config['refresh_rate'] / 1000:.1f}s",
            font=("Segoe UI", 10),
        ).pack(pady=10)
        ttk.Label(
            dialog,
            text="Enter new refresh rate (seconds):",
            font=("Segoe UI", 10),
        ).pack()

        entry = ttk.Entry(dialog, width=12)
        entry.insert(0, str(self.config["refresh_rate"] / 1000))
        entry.pack(pady=5)

        def apply():
            try:
                val = float(entry.get())
                if val < 0.5 or val > 60:
                    messagebox.showerror(
                        "Invalid Input",
                        "Refresh rate must be between 0.5 and 60 seconds",
                    )
                    return
                self.config["refresh_rate"] = int(val * 1000)
                save_config(self.config)
                messagebox.showinfo("Success", "Refresh rate updated successfully.")
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Invalid Input", "Please enter a valid number.")

        ttk.Button(dialog, text="Apply", command=apply).pack(pady=10)

    def alert_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Alert Settings")
        dialog.resizable(False, False)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="Temperature Alert Threshold (°C):",
            font=("Segoe UI", 10),
        ).pack(pady=5)
        temp_entry = ttk.Entry(dialog, width=12)
        temp_entry.insert(0, str(self.config.get("alert_temp", 80)))
        temp_entry.pack()

        ttk.Label(
            dialog,
            text="Utilization Alert Threshold (%):",
            font=("Segoe UI", 10),
        ).pack(pady=5)
        util_entry = ttk.Entry(dialog, width=12)
        util_entry.insert(0, str(self.config.get("alert_util", 90)))
        util_entry.pack()

        enabled_var = tk.BooleanVar(value=self.config.get("alert_enabled", True))
        ttk.Checkbutton(
            dialog, text="Enable Desktop Alerts", variable=enabled_var
        ).pack(pady=10)

        if not PLYER_AVAILABLE:
            ttk.Label(
                dialog,
                text="Note: Install 'plyer' for desktop notifications",
                font=("Segoe UI", 9, "italic"),
                foreground="orange",
            ).pack()

        def apply():
            try:
                self.config["alert_temp"] = int(temp_entry.get())
                self.config["alert_util"] = int(util_entry.get())
                self.config["alert_enabled"] = enabled_var.get()
                save_config(self.config)
                messagebox.showinfo("Success", "Alert settings updated.")
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Invalid Input", "Please enter valid numbers.")

        ttk.Button(dialog, text="Apply", command=apply).pack(pady=10)

    def export_csv(self):
        from tkinter import filedialog

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")]
        )
        if filename:
            if export_to_csv(filename):
                messagebox.showinfo("Success", f"Data exported to {filename}")
            else:
                messagebox.showerror("Error", "Failed to export CSV.")

    def export_json(self):
        from tkinter import filedialog

        filename = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON files", "*.json")]
        )
        if filename:
            if export_to_json(filename):
                messagebox.showinfo("Success", f"Data exported to {filename}")
            else:
                messagebox.showerror("Error", "Failed to export JSON.")

    def show_about(self):
        about_text = f"""NVIDIA-SMI GPU Monitor
Version {VERSION}

A robust real-time GPU monitoring tool:
• Multi-GPU support with individual cards and aggregate view
• Defensive handling for unsupported laptop/cloud metrics
• Desktop alerts & Polkit/UAC elevated power-limit configuration
• Process table with instant search filter & task management
• Real-time history graphs and data export

GPU(s): {static_cache.get("gpu_name", "Unknown")}
Platform: {platform.system()} {platform.release()}

© 2026 - MIT License"""
        messagebox.showinfo("About", about_text)

    def manual_refresh(self):
        global stop_update
        stop_update = True
        if update_thread and update_thread.is_alive():
            update_thread.join(timeout=1)
        stop_update = False
        self.start_background_update()

    def show_graphs(self):
        if not MATPLOTLIB_AVAILABLE:
            messagebox.showwarning(
                "Matplotlib Not Available",
                "Install matplotlib to view graphs:\npip install matplotlib",
            )
            return

        if self.graph_window and tk.Toplevel.winfo_exists(self.graph_window):
            self.graph_window.lift()
            return

        self.graph_window = tk.Toplevel(self.root)
        self.graph_window.title("GPU History Graphs")
        self.graph_window.geometry("1000x700")

        theme = DARK_THEME if self.config["dark_mode"] else LIGHT_THEME
        fig = Figure(figsize=(10, 7), dpi=100)
        fig.patch.set_facecolor(theme["bg"])

        ax1 = fig.add_subplot(2, 2, 1)
        ax2 = fig.add_subplot(2, 2, 2)
        ax3 = fig.add_subplot(2, 2, 3)
        ax4 = fig.add_subplot(2, 2, 4)

        canvas = FigureCanvasTkAgg(fig, master=self.graph_window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        def update_graphs():
            if not self.graph_window or not tk.Toplevel.winfo_exists(self.graph_window):
                return

            if len(history_data["time"]) > 0:
                time_range = list(range(len(history_data["time"])))

                for ax, data_key, title, ylabel, color in [
                    (
                        ax1,
                        "utilization",
                        "GPU Utilization",
                        "Utilization (%)",
                        theme["accent"],
                    ),
                    (ax2, "temperature", "Temperature", "Temperature (°C)", "#d79b00"),
                    (ax3, "power", "Power Draw", "Power (W)", theme["accent"]),
                    (ax4, "memory", "Memory Usage", "Memory (%)", "#5a8f00"),
                ]:
                    ax.clear()
                    ax.set_facecolor(theme["card_bg"])
                    ax.grid(True, alpha=0.25, color=theme["muted"])
                    ax.plot(
                        time_range,
                        list(history_data[data_key]),
                        color=color,
                        linewidth=2,
                    )
                    ax.set_title(title, color=theme["fg"])
                    ax.set_ylabel(ylabel, color=theme["fg"])
                    ax.tick_params(colors=theme["fg"])
                    for spine in ax.spines.values():
                        spine.set_edgecolor(theme["card_border"])

                fig.tight_layout()
                canvas.draw()

            self.root.after(2000, update_graphs)

        update_graphs()

        def on_graph_close():
            self.graph_window.destroy()
            self.graph_window = None

        self.graph_window.protocol("WM_DELETE_WINDOW", on_graph_close)

    def sort_treeview(self, col: str):
        if self.sort_column == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = col
            self.sort_reverse = False

        items = [
            (self.labels["proc_table"].set(k, col), k)
            for k in self.labels["proc_table"].get_children("")
        ]

        try:
            items.sort(
                key=lambda t: float(t[0].split()[0]) if t[0] else 0,
                reverse=self.sort_reverse,
            )
        except Exception:
            items.sort(reverse=self.sort_reverse)

        for index, (_val, k) in enumerate(items):
            self.labels["proc_table"].move(k, "", index)

    def copy_selected_value(self, column_index: int):
        selection = self.labels["proc_table"].selection()
        if not selection:
            return
        values = self.labels["proc_table"].item(selection[0], "values")
        if column_index >= len(values):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(str(values[column_index]))
        self.labels["status"].config(text=f"Copied: {values[column_index]}")

    def end_selected_task(self):
        selection = self.labels["proc_table"].selection()
        if not selection:
            return
        values = self.labels["proc_table"].item(selection[0], "values")
        if len(values) < 3:
            return
        pid, name = values[1], values[2]
        if not messagebox.askyesno("End Task", f"End process {name} (PID {pid})?"):
            return
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    check=True,
                    capture_output=True,
                )
            else:
                os.kill(int(pid), 9)
            self.labels["status"].config(text=f"Ended process {pid}")
        except Exception as exc:
            messagebox.showerror("Error", f"Could not end task: {exc}")

    def init_gui(self):
        name = get_gpu_info()
        static_cache["gpu_name"] = name
        theme = self.current_theme

        # Top Header Banner
        header_outer = tk.Frame(self.root, bg=theme["accent"], height=80)
        header_outer.pack(fill="x", padx=0, pady=0)
        header_outer.pack_propagate(False)

        header_content = tk.Frame(header_outer, bg=theme["accent"])
        header_content.pack(side="left", padx=20, pady=12)

        gpu_title = tk.Label(
            header_content,
            text="NVIDIA GPU MONITOR",
            font=("Segoe UI", 8, "bold"),
            bg=theme["accent"],
            fg="#c8d1dc",
        )
        gpu_title.pack(anchor="w")

        self.labels["gpu"] = tk.Label(
            header_content,
            text=name,
            font=("Segoe UI", 16, "bold"),
            bg=theme["accent"],
            fg="#ffffff",
        )
        self.labels["gpu"].pack(anchor="w", pady=(2, 0))

        btn_container = tk.Frame(header_outer, bg=theme["accent"])
        btn_container.pack(side="right", padx=20, pady=12)

        power_btn = tk.Button(
            btn_container,
            text="Power Settings",
            bg=theme["button_bg"],
            fg="#ffffff",
            font=("Segoe UI Semibold", 9),
            relief="flat",
            borderwidth=1,
            highlightthickness=0,
            padx=16,
            pady=6,
            cursor="hand2",
            command=lambda: open_power_limit_window(self),
        )
        power_btn.pack()

        divider = tk.Frame(self.root, bg=theme["header_gradient_end"], height=3)
        divider.pack(fill="x", padx=0, pady=0)

        # Toolbar
        toolbar = tk.Frame(self.root, bg=theme["bg"], height=38)
        toolbar.pack(fill="x", padx=0, pady=0)
        toolbar.pack_propagate(False)

        toolbar_inner = tk.Frame(toolbar, bg=theme["bg"])
        toolbar_inner.pack(side="left", padx=15, pady=6)

        def make_toolbar_btn(text, cmd, tooltip):
            btn = tk.Button(
                toolbar_inner,
                text=text,
                bg=theme["card_bg"],
                fg=theme["accent"],
                font=("Segoe UI", 8),
                relief="flat",
                borderwidth=1,
                highlightthickness=0,
                padx=12,
                pady=3,
                cursor="hand2",
                command=cmd,
                activebackground=theme["hover_bg"],
                activeforeground=theme["accent"],
            )
            btn.pack(side="left", padx=4)
            create_tooltip(btn, tooltip)
            return btn

        make_toolbar_btn("Refresh", self.manual_refresh, "Refresh now (F5)")
        if MATPLOTLIB_AVAILABLE:
            make_toolbar_btn("Graphs", self.show_graphs, "Open graphs (Ctrl+G)")
        make_toolbar_btn("Theme", self.toggle_theme, "Toggle theme (Ctrl+D)")

        # Main scrollable container for GPU cards and content
        self.cards_parent_frame = ttk.Frame(self.root, style="MainContainer.TFrame")
        self.cards_parent_frame.pack(fill="both", expand=True, padx=20, pady=(10, 0))

        # Dynamic container where GPU cards are rendered
        self.gpu_cards_container = ttk.Frame(
            self.cards_parent_frame, style="MainContainer.TFrame"
        )
        self.gpu_cards_container.pack(fill="x", pady=(0, 10))

        # Combined summary card placeholder (shown when >1 GPU detected)
        self.combined_frame = ttk.Frame(self.gpu_cards_container, style="Card.TFrame")

        # Running Processes Section
        proc_container = ttk.Frame(
            self.cards_parent_frame, style="MainContainer.TFrame"
        )
        proc_container.pack(fill="both", expand=True, pady=(5, 10))

        proc_header = ttk.Frame(proc_container, style="MainContainer.TFrame")
        proc_header.pack(fill="x", pady=(5, 6))

        self.labels["proc_label"] = ttk.Label(
            proc_header, text="RUNNING PROCESSES", style="Title.TLabel"
        )
        self.labels["proc_label"].pack(side="left", anchor="w")

        # Process filter search box
        filter_box = ttk.Frame(proc_header, style="MainContainer.TFrame")
        filter_box.pack(side="right")
        ttk.Label(filter_box, text="Filter:", font=("Segoe UI", 9)).pack(
            side="left", padx=(0, 5)
        )
        self.filter_entry_var = tk.StringVar()
        self.filter_entry_var.trace_add("write", lambda *args: self.apply_proc_filter())
        filter_entry = ttk.Entry(
            filter_box, textvariable=self.filter_entry_var, width=18
        )
        filter_entry.pack(side="left")

        proc_card = ttk.Frame(proc_container, style="Card.TFrame")
        proc_card.pack(fill="both", expand=True)

        proc_inner = ttk.Frame(proc_card, style="Card.TFrame")
        proc_inner.pack(padx=10, pady=10, fill="both", expand=True)

        self.labels["proc_table"] = ttk.Treeview(
            proc_inner,
            columns=("GPU", "PID", "Name", "Memory"),
            show="headings",
            height=6,
        )
        self.labels["proc_table"].heading(
            "GPU", text="GPU", command=lambda: self.sort_treeview("GPU")
        )
        self.labels["proc_table"].heading(
            "PID", text="PROCESS ID", command=lambda: self.sort_treeview("PID")
        )
        self.labels["proc_table"].heading(
            "Name", text="APPLICATION NAME", command=lambda: self.sort_treeview("Name")
        )
        self.labels["proc_table"].heading(
            "Memory", text="VRAM USAGE", command=lambda: self.sort_treeview("Memory")
        )

        self.labels["proc_table"].column("GPU", width=50, anchor="center")
        self.labels["proc_table"].column("PID", width=90, anchor="center")
        self.labels["proc_table"].column("Name", width=380, anchor="w")
        self.labels["proc_table"].column("Memory", width=120, anchor="center")

        self.proc_context = Menu(proc_inner, tearoff=0)
        self.proc_context.add_command(
            label="Copy PID", command=lambda: self.copy_selected_value(1)
        )
        self.proc_context.add_command(
            label="Copy Name", command=lambda: self.copy_selected_value(2)
        )
        self.proc_context.add_separator()
        self.proc_context.add_command(label="End Task", command=self.end_selected_task)

        scrollbar = ttk.Scrollbar(
            proc_inner, orient="vertical", command=self.labels["proc_table"].yview
        )
        self.labels["proc_table"].configure(yscrollcommand=scrollbar.set)
        self.labels["proc_table"].pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def on_motion(event):
            row = self.labels["proc_table"].identify_row(event.y)
            if row == self.hover_row:
                return
            if self.hover_row:
                tags = [
                    t
                    for t in self.labels["proc_table"].item(self.hover_row, "tags")
                    if t != "hover"
                ]
                self.labels["proc_table"].item(self.hover_row, tags=tags)
            self.hover_row = row
            if row:
                tags = list(self.labels["proc_table"].item(row, "tags"))
                if "hover" not in tags:
                    tags.append("hover")
                self.labels["proc_table"].item(row, tags=tags)

        self.labels["proc_table"].bind("<Motion>", on_motion)

        def show_context(event):
            row = self.labels["proc_table"].identify_row(event.y)
            if row:
                self.labels["proc_table"].selection_set(row)
            try:
                self.proc_context.tk_popup(event.x_root, event.y_root)
            finally:
                self.proc_context.grab_release()

        self.labels["proc_table"].bind("<Button-3>", show_context)

        # Full Output Collapsible Section
        def toggle_full_output():
            if self.labels["full_output_frame"].winfo_ismapped():
                self.labels["full_output_frame"].pack_forget()
                self.labels["show_btn"].config(text="Show Detailed Output")
            else:
                self.labels["full_output_frame"].pack(
                    fill="both", expand=True, pady=(5, 5)
                )
                self.labels["show_btn"].config(text="Hide Detailed Output")

        self.labels["show_btn"] = ttk.Button(
            self.cards_parent_frame,
            text="Show Detailed Output",
            command=toggle_full_output,
        )
        self.labels["show_btn"].pack(pady=(4, 6))

        self.labels["full_output_frame"] = ttk.Frame(
            self.cards_parent_frame, style="Card.TFrame"
        )
        text_inner = ttk.Frame(self.labels["full_output_frame"], style="Card.TFrame")
        text_inner.pack(padx=10, pady=10, fill="both", expand=True)

        self.labels["full_output_text"] = tk.Text(
            text_inner,
            height=10,
            width=90,
            font=("Consolas", 9),
            wrap="none",
            bg=theme["text_bg"],
            fg=theme["text_fg"],
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=6,
        )
        self.labels["full_output_text"].pack(fill="both", expand=True)
        self.labels["full_output_text"].config(state="disabled")

        # Status Bar
        status_frame = tk.Frame(self.root, bg=theme["accent_light"], height=30)
        status_frame.pack(side="bottom", fill="x")
        status_frame.pack_propagate(False)

        self.labels["status"] = tk.Label(
            status_frame,
            text="Initializing system...",
            font=("Segoe UI", 8),
            anchor="w",
            bg=theme["accent_light"],
            fg=theme["fg"],
        )
        self.labels["status"].pack(side="left", fill="x", expand=True, padx=20)

    def create_gpu_metric_card(
        self, parent: ttk.Frame, gpu_index: int, gpu_name: str, show_header: bool = True
    ) -> dict[str, Any]:
        """Create a dedicated UI card displaying metrics for an individual GPU."""
        theme = self.current_theme
        card_frame = ttk.Frame(parent, style="Card.TFrame")
        card_frame.pack(fill="x", pady=(0, 8), padx=0)

        card_widgets: dict[str, Any] = {"frame": card_frame}

        if show_header:
            header_bar = ttk.Frame(card_frame, style="Card.TFrame")
            header_bar.pack(fill="x", padx=15, pady=(10, 4))

            title_lbl = ttk.Label(
                header_bar,
                text=f"GPU {gpu_index}: {gpu_name}",
                font=("Segoe UI", 10, "bold"),
                style="Header.TLabel",
            )
            title_lbl.pack(side="left")

            card_pwr_btn = tk.Button(
                header_bar,
                text="Power Limit",
                bg=theme["card_bg"],
                fg=theme["accent"],
                font=("Segoe UI", 8),
                relief="flat",
                borderwidth=1,
                cursor="hand2",
                command=lambda idx=gpu_index: open_power_limit_window(
                    self, initial_gpu_index=idx
                ),
            )
            card_pwr_btn.pack(side="right")

        inner = ttk.Frame(card_frame, style="Card.TFrame")
        inner.pack(padx=12, pady=(4, 12), fill="x")

        # Row 1: Utilization, Memory, Temperature, Power
        row1 = ttk.Frame(inner, style="Card.TFrame")
        row1.pack(fill="x")

        metrics_def = [
            ("util", "GPU UTILIZATION", "Percentage of GPU compute being used", 100),
            ("mem", "MEMORY USAGE", "VRAM used out of total available", 100),
            ("temp", "TEMPERATURE", "GPU core temperature in Celsius", 100),
            ("power", "POWER DRAW", "Current power consumption vs limit", 120),
        ]

        for i, (key, title, tooltip, max_val) in enumerate(metrics_def):
            subcard = ttk.Frame(row1, style="Card.TFrame")
            subcard.pack(
                side="left", fill="both", expand=True, padx=(0 if i == 0 else 6, 0)
            )

            lbl = ttk.Label(subcard, text=title, style="Metric.TLabel")
            lbl.pack(anchor="w")
            create_tooltip(lbl, tooltip)

            val_lbl = ttk.Label(subcard, text="--", style="Value.TLabel")
            val_lbl.pack(anchor="w", pady=(2, 0))
            card_widgets[key] = val_lbl

            bar = ttk.Progressbar(
                subcard,
                style="Metric.Horizontal.TProgressbar",
                mode="determinate",
                maximum=max_val,
            )
            bar.pack(fill="x", pady=(4, 0))
            card_widgets[f"{key}_bar"] = bar

        # Row 2: Fan Speed, GPU Clock, Memory Clock
        row2 = ttk.Frame(inner, style="Card.TFrame")
        row2.pack(fill="x", pady=(8, 0))

        metrics2_def = [
            ("fan", "FAN SPEED", "Cooling fan RPM percentage"),
            ("clock_gpu", "GPU CLOCK", "Core processing frequency"),
            ("clock_mem", "MEMORY CLOCK", "VRAM operating frequency"),
        ]

        for i, (key, title, tooltip) in enumerate(metrics2_def):
            subcard2 = ttk.Frame(row2, style="Card.TFrame")
            subcard2.pack(
                side="left", fill="both", expand=True, padx=(0 if i == 0 else 6, 0)
            )

            lbl2 = ttk.Label(subcard2, text=title, style="Metric.TLabel")
            lbl2.pack(anchor="w")
            create_tooltip(lbl2, tooltip)

            val_lbl2 = ttk.Label(subcard2, text="--", style="Value.TLabel")
            val_lbl2.pack(anchor="w", pady=(2, 0))
            card_widgets[key] = val_lbl2

        return card_widgets

    def create_combined_summary_card(self, parent: ttk.Frame) -> dict[str, Any]:
        """Create a high-level summary overview for multi-GPU configurations."""
        card_frame = ttk.Frame(parent, style="Card.TFrame")
        card_frame.pack(fill="x", pady=(0, 10))

        header = ttk.Frame(card_frame, style="Card.TFrame")
        header.pack(fill="x", padx=15, pady=(8, 4))
        ttk.Label(
            header,
            text="COMBINED OVERVIEW",
            font=("Segoe UI", 10, "bold"),
            style="Header.TLabel",
        ).pack(side="left")

        inner = ttk.Frame(card_frame, style="Card.TFrame")
        inner.pack(padx=12, pady=(4, 10), fill="x")

        summary_widgets: dict[str, Any] = {"frame": card_frame}
        summary_defs = [
            (
                "combined_util",
                "AVG UTILIZATION",
                "Average compute utilization across all GPUs",
            ),
            ("combined_mem", "TOTAL VRAM", "Combined VRAM allocated across all GPUs"),
            ("combined_temp", "PEAK TEMP", "Highest core temperature among GPUs"),
            ("combined_power", "TOTAL POWER", "Aggregate power draw of all GPUs"),
        ]

        for i, (key, title, tooltip) in enumerate(summary_defs):
            subcard = ttk.Frame(inner, style="Card.TFrame")
            subcard.pack(
                side="left", fill="both", expand=True, padx=(0 if i == 0 else 6, 0)
            )

            lbl = ttk.Label(subcard, text=title, style="Metric.TLabel")
            lbl.pack(anchor="w")
            create_tooltip(lbl, tooltip)

            val_lbl = ttk.Label(subcard, text="--", style="Value.TLabel")
            val_lbl.pack(anchor="w", pady=(2, 0))
            summary_widgets[key] = val_lbl

        return summary_widgets

    def rebuild_gpu_cards(self):
        """Reconstruct GPU metric cards when GPUs are discovered or theme toggled."""
        for child in self.gpu_cards_container.winfo_children():
            child.destroy()

        self.gpu_cards.clear()
        gpus = self.current_gpus or [
            {"index": 0, "name": static_cache.get("gpu_name", "NVIDIA GPU")}
        ]

        if len(gpus) > 1:
            self.combined_widgets = self.create_combined_summary_card(
                self.gpu_cards_container
            )
        else:
            self.combined_widgets = {}

        for gpu in gpus:
            idx = gpu.get("index", 0)
            name = gpu.get("name", f"GPU {idx}")
            show_hdr = len(gpus) > 1
            card_widgets = self.create_gpu_metric_card(
                self.gpu_cards_container,
                gpu_index=idx,
                gpu_name=name,
                show_header=show_hdr,
            )
            self.gpu_cards[idx] = card_widgets

        # Alias primary GPU labels for backward compatibility
        if 0 in self.gpu_cards:
            for k, w in self.gpu_cards[0].items():
                if k != "frame":
                    self.labels[k] = w

    def apply_proc_filter(self):
        """Filter running processes in the treeview based on user search query."""
        query = self.filter_entry_var.get().strip().lower()
        self.proc_search_query = query
        self.refresh_process_table(self.last_process_list, force=True)

    def refresh_process_table(
        self, processes: list[dict[str, str]], force: bool = False
    ):
        """Update process treeview with optional filtering and striping."""
        filtered = [
            p
            for p in processes
            if not self.proc_search_query
            or self.proc_search_query in p.get("name", "").lower()
            or self.proc_search_query in p.get("pid", "").lower()
        ]

        current_items = [
            self.labels["proc_table"].item(c)["values"]
            for c in self.labels["proc_table"].get_children()
        ]
        new_items = [
            (p.get("gpu", "0"), p["pid"], p["name"], p["mem"]) for p in filtered
        ]

        if force or current_items != new_items:
            self.labels["proc_table"].delete(*self.labels["proc_table"].get_children())
            theme = self.current_theme
            self.labels["proc_table"].tag_configure(
                "even", background=theme["tree_bg"], foreground=theme["tree_fg"]
            )
            self.labels["proc_table"].tag_configure(
                "odd", background=theme["tree_alt_bg"], foreground=theme["tree_fg"]
            )
            self.labels["proc_table"].tag_configure(
                "hover", background=theme["hover_bg"], foreground=theme["fg"]
            )
            for idx, proc in enumerate(filtered):
                tag = "even" if idx % 2 == 0 else "odd"
                self.labels["proc_table"].insert(
                    "",
                    "end",
                    values=(
                        proc.get("gpu", "0"),
                        proc["pid"],
                        proc["name"],
                        proc["mem"],
                    ),
                    tags=(tag,),
                )

    def start_background_update(self):
        global update_thread, stop_update
        stop_update = False

        def background_worker():
            full_output_counter = 0
            while not stop_update:
                try:
                    gpu_output, proc_output = get_nvidia_smi_output()
                    gpu_data_cache["gpu_output"] = gpu_output
                    gpu_data_cache["proc_output"] = proc_output
                    gpu_data_cache["timestamp"] = datetime.now()

                    full_output_counter += 1
                    if full_output_counter >= 5:
                        try:
                            creationflags = (
                                0x08000000 if platform.system() == "Windows" else 0
                            )
                            gpu_data_cache["full_output"] = subprocess.check_output(
                                ["nvidia-smi"],
                                encoding="utf-8",
                                stderr=subprocess.STDOUT,
                                creationflags=creationflags,
                                timeout=5,
                            )
                        except Exception:
                            pass
                        full_output_counter = 0
                except Exception as exc:
                    gpu_data_cache["error"] = str(exc)

                for _ in range(self.config["refresh_rate"] // 100):
                    if stop_update:
                        break
                    time.sleep(0.1)

        update_thread = threading.Thread(target=background_worker, daemon=True)
        update_thread.start()
        self.update_gui()

    def update_gui(self):
        if stop_update:
            return

        if "error" in gpu_data_cache:
            self.labels["gpu"].config(text=f"Error: {gpu_data_cache['error']}")
            self.root.after(3000, self.update_gui)
            return

        if "gpu_output" not in gpu_data_cache:
            self.root.after(500, self.update_gui)
            return

        gpu_output = gpu_data_cache.get("gpu_output", "")
        proc_output = gpu_data_cache.get("proc_output", "")
        timestamp = gpu_data_cache.get("timestamp", datetime.now())

        if gpu_output.startswith("Error"):
            self.labels["gpu"].config(text=gpu_output)
            self.labels["status"].config(
                text=f"Error at {timestamp.strftime('%H:%M:%S')}"
            )
            self.root.after(3000, self.update_gui)
            return

        gpus = parse_gpu_metrics(gpu_output)
        processes = parse_processes(proc_output)
        self.last_process_list = processes

        # Synchronize GPU cards if GPU count or indices changed
        existing_indices = set(self.gpu_cards.keys())
        new_indices = {g["index"] for g in gpus}
        if existing_indices != new_indices or not self.gpu_cards:
            self.current_gpus = list(gpus)
            self.rebuild_gpu_cards()

        # Update metrics for each individual GPU card
        for gpu in gpus:
            idx = gpu["index"]
            widgets = self.gpu_cards.get(idx)
            if not widgets:
                continue

            util_val = gpu.get("utilization")
            if util_val is not None:
                widgets["util"].config(
                    text=f"{util_val:.1f}%", foreground=color_for_percent(util_val)
                )
                widgets["util_bar"]["value"] = max(0, min(100, util_val))
            else:
                widgets["util"].config(text="N/A", foreground="gray")
                widgets["util_bar"]["value"] = 0

            mem_used = gpu.get("mem_used")
            mem_total = gpu.get("mem_total")
            mem_str, mem_pct = format_memory(mem_used, mem_total)
            widgets["mem"].config(
                text=mem_str,
                foreground=color_for_percent(mem_pct if mem_used is not None else None),
            )
            widgets["mem_bar"]["value"] = max(0, min(100, mem_pct))

            temp = gpu.get("temperature")
            if temp is not None:
                widgets["temp"].config(
                    text=f"{temp}°C", foreground=color_for_temp(temp)
                )
                widgets["temp_bar"]["value"] = max(0, min(100, temp))
            else:
                widgets["temp"].config(text="N/A", foreground="gray")
                widgets["temp_bar"]["value"] = 0

            p_draw = gpu.get("power_draw")
            p_limit = gpu.get("power_limit")
            p_str, p_pct = format_power(p_draw, p_limit)
            widgets["power"].config(
                text=p_str, foreground=color_for_power(p_draw, p_limit)
            )
            widgets["power_bar"]["value"] = max(0, min(120, p_pct))

            fan = gpu.get("fan_speed")
            if fan is not None:
                widgets["fan"].config(text=f"{fan:.0f}%", foreground="#3388ff")
            else:
                widgets["fan"].config(text="N/A", foreground="gray")

            clk_g = gpu.get("clock_gpu")
            widgets["clock_gpu"].config(
                text=f"{clk_g:.0f} MHz" if clk_g is not None else "N/A",
                foreground="purple",
            )

            clk_m = gpu.get("clock_mem")
            widgets["clock_mem"].config(
                text=f"{clk_m:.0f} MHz" if clk_m is not None else "N/A",
                foreground="purple",
            )

        # Update combined summary if multi-GPU
        if (
            len(gpus) > 1
            and hasattr(self, "combined_widgets")
            and self.combined_widgets
        ):
            valid_utils = [
                g["utilization"] for g in gpus if g.get("utilization") is not None
            ]
            avg_util = sum(valid_utils) / len(valid_utils) if valid_utils else None
            self.combined_widgets["combined_util"].config(
                text=f"{avg_util:.1f}%" if avg_util is not None else "N/A",
                foreground=color_for_percent(avg_util),
            )

            tot_used = sum(g["mem_used"] for g in gpus if g.get("mem_used") is not None)
            tot_mem = sum(
                g["mem_total"] for g in gpus if g.get("mem_total") is not None
            )
            tot_str, tot_pct = format_memory(tot_used, tot_mem)
            self.combined_widgets["combined_mem"].config(
                text=tot_str, foreground=color_for_percent(tot_pct)
            )

            valid_temps = [
                g["temperature"] for g in gpus if g.get("temperature") is not None
            ]
            max_temp = max(valid_temps) if valid_temps else None
            self.combined_widgets["combined_temp"].config(
                text=f"{max_temp}°C" if max_temp is not None else "N/A",
                foreground=color_for_temp(max_temp),
            )

            valid_powers = [
                g["power_draw"] for g in gpus if g.get("power_draw") is not None
            ]
            valid_caps = [
                g["power_limit"] for g in gpus if g.get("power_limit") is not None
            ]
            tot_draw = sum(valid_powers) if valid_powers else None
            tot_cap = sum(valid_caps) if valid_caps else None
            tot_p_str, _ = format_power(tot_draw, tot_cap)
            self.combined_widgets["combined_power"].config(
                text=tot_p_str, foreground=color_for_power(tot_draw, tot_cap)
            )

        # Record history from primary GPU (or combined)
        primary_gpu = gpus[0] if gpus else {}
        history_data["time"].append(timestamp.strftime("%H:%M:%S"))
        history_data["utilization"].append(primary_gpu.get("utilization") or 0)
        history_data["temperature"].append(primary_gpu.get("temperature") or 0)
        history_data["power"].append(primary_gpu.get("power_draw") or 0)
        p_used = primary_gpu.get("mem_used") or 0
        p_total = primary_gpu.get("mem_total") or 1
        history_data["memory"].append((p_used / p_total) * 100 if p_total else 0)

        check_alerts(primary_gpu, self.config)
        self.refresh_process_table(processes)

        # Lazy update full text output if visible
        if (
            "full_output_text" in self.labels
            and self.labels["full_output_text"].winfo_viewable()
            and "full_output" in gpu_data_cache
        ):
            self.labels["full_output_text"].config(state="normal")
            self.labels["full_output_text"].delete("1.0", tk.END)
            self.labels["full_output_text"].insert("1.0", gpu_data_cache["full_output"])
            self.labels["full_output_text"].config(state="disabled")

        rate_sec = self.config["refresh_rate"] / 1000
        time_str = timestamp.strftime("%H:%M:%S")
        self.labels["status"].config(
            text=f"Last updated {time_str} · Refresh rate {rate_sec:.1f}s"
        )

        if PYSTRAY_AVAILABLE and tray_icon and int(time.time()) % 2 == 0:
            u_num = primary_gpu.get("utilization") or 0
            t_num = primary_gpu.get("temperature") or 0
            tray_icon.title = f"GPU: {u_num:.0f}% | Temp: {t_num}°C"

        self.root.after(self.config["refresh_rate"], self.update_gui)

    def on_closing(self):
        global stop_update
        stop_update = True

        geometry = self.root.geometry()
        match = re.match(r"(\d+)x(\d+)\+(\d+)\+(\d+)", geometry)
        if match:
            self.config["window_width"] = int(match.group(1))
            self.config["window_height"] = int(match.group(2))
            self.config["window_x"] = int(match.group(3))
            self.config["window_y"] = int(match.group(4))

        save_config(self.config)

        if tray_icon:
            try:
                tray_icon.stop()
            except Exception:
                pass

        self.root.quit()
        self.root.destroy()


def setup_tray_icon(app_instance: GPUMonitorApp):
    """Set up system tray icon with context menu."""
    if not PYSTRAY_AVAILABLE:
        return None

    try:
        theme = DARK_THEME if app_instance.config.get("dark_mode") else LIGHT_THEME
        image = create_tray_icon_image(theme)
        if image is None:
            return None

        def show_window(_icon, _item):
            app_instance.root.deiconify()
            app_instance.root.lift()

        def quit_app(icon_obj, _item):
            icon_obj.stop()
            app_instance.on_closing()

        menu = pystray.Menu(
            pystray.MenuItem("Show", show_window, default=True),
            pystray.MenuItem("Quit", quit_app),
        )

        icon = pystray.Icon("nvidia_monitor", image, "NVIDIA GPU Monitor", menu)

        def run_tray():
            icon.run()

        tray_thread = threading.Thread(target=run_tray, daemon=True)
        tray_thread.start()
        return icon
    except Exception:
        return None


if __name__ == "__main__":
    root = tk.Tk()
    app = GPUMonitorApp(root)

    if PYSTRAY_AVAILABLE:
        tray_icon = setup_tray_icon(app)

    root.mainloop()
