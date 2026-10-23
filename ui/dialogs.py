"""Modal dialogs for settings, alerts, and power limits."""

from __future__ import annotations

import platform
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from config import save_config
from nvidia_smi import get_power_limits, set_power_limit
from ui.theme import DARK_THEME, LIGHT_THEME

power_win: tk.Toplevel | None = None


def open_power_limit_window(parent_app: Any, initial_gpu_index: int = 0) -> None:
    """Open Power Limit dialog with GPU selector and non-blocking worker threads."""
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

    combo = None
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

    def fetch_limits_async(gpu_idx: int) -> None:
        """Fetch limits in a background thread without freezing the Tk main loop."""
        cur_label.config(text="Fetching power limits...")
        range_label.config(text="")
        result_box: list[tuple[float | None, float | None, float | None] | None] = [
            None
        ]

        def worker():
            result_box[0] = get_power_limits(gpu_index=gpu_idx)

        threading.Thread(target=worker, daemon=True).start()

        def poll_result():
            if not power_win or not tk.Toplevel.winfo_exists(power_win):
                return
            if result_box[0] is not None:
                c, mn, mx = result_box[0]
                cur_limits["cur"] = c
                cur_limits["min"] = mn
                cur_limits["max"] = mx
                cur_str = f"{c:.1f} W" if c is not None else "Unknown"
                range_str = (
                    f"{mn:.1f} - {mx:.1f} W"
                    if mn is not None and mx is not None
                    else "Unknown"
                )
                cur_label.config(text=f"Current: {cur_str}")
                range_label.config(text=f"Allowed Range: {range_str}")
            else:
                power_win.after(50, poll_result)

        power_win.after(50, poll_result)

    fetch_limits_async(initial_gpu_index)

    if combo is not None:

        def on_gpu_changed(_event: Any) -> None:
            idx = combo.current()
            if 0 <= idx < len(detected_gpus):
                target_idx = detected_gpus[idx]["index"]
                selected_gpu_var.set(str(target_idx))
                fetch_limits_async(target_idx)

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

    apply_btn = None

    def apply_limit() -> None:
        """Run power limit modification in a background thread."""
        try:
            val = float(entry.get())
            idx = int(selected_gpu_var.get())
            mn = cur_limits["min"]
            mx = cur_limits["max"]

            if mn is not None and mx is not None and (val < mn or val > mx):
                msg.set(f"Value must be between {mn:.1f} and {mx:.1f} W")
                msg_label.config(foreground=theme["danger"])
                return

            msg.set("Applying power limit...")
            msg_label.config(foreground=theme["info"])
            if apply_btn:
                apply_btn.config(state="disabled")

            apply_result: list[tuple[bool, str] | None] = [None]

            def worker():
                apply_result[0] = set_power_limit(
                    val, gpu_index=idx, min_limit=mn, max_limit=mx
                )

            threading.Thread(target=worker, daemon=True).start()

            def poll_apply():
                if not power_win or not tk.Toplevel.winfo_exists(power_win):
                    return
                if apply_result[0] is not None:
                    ok, out = apply_result[0]
                    if apply_btn:
                        apply_btn.config(state="normal")
                    if ok:
                        msg.set("Power limit update applied successfully.")
                        msg_label.config(foreground=theme["success"])
                        fetch_limits_async(idx)
                    else:
                        msg.set(f"Failed: {out}")
                        msg_label.config(foreground=theme["danger"])
                else:
                    power_win.after(50, poll_apply)

            power_win.after(50, poll_apply)

        except ValueError:
            msg.set("Invalid input - please enter a valid number.")
            msg_label.config(foreground=theme["danger"])

    button_frame = ttk.Frame(main_frame)
    button_frame.pack(pady=15)
    apply_btn = ttk.Button(
        button_frame, text="Apply Changes", style="Accent.TButton", command=apply_limit
    )
    apply_btn.pack(side="left", padx=5)
    ttk.Button(button_frame, text="Cancel", command=power_win.destroy).pack(
        side="left", padx=5
    )

    entry.bind("<Return>", lambda _e: apply_limit())
    entry.bind("<Escape>", lambda _e: power_win.destroy())

    if platform.system() == "Windows":
        note_text = (
            "Requires administrator privileges "
            "(will trigger Windows UAC elevation if needed)."
        )
    else:
        note_text = "Uses pkexec (polkit) or sudo to authenticate privilege elevation."

    ttk.Label(
        main_frame,
        text=note_text,
        font=("Segoe UI", 8),
        foreground=theme["muted"],
    ).pack(pady=(5, 0))

    def on_close():
        global power_win
        power_win.destroy()
        power_win = None

    power_win.protocol("WM_DELETE_WINDOW", on_close)


def open_refresh_rate_dialog(parent_app: Any) -> None:
    """Open dialog to adjust GUI refresh interval."""
    dialog = tk.Toplevel(parent_app.root)
    dialog.title("Adjust Refresh Rate")
    dialog.resizable(False, False)
    dialog.grab_set()

    current_sec = parent_app.config["refresh_rate"] / 1000
    ttk.Label(
        dialog,
        text=f"Current refresh rate: {current_sec:.1f}s",
        font=("Segoe UI", 10),
    ).pack(pady=10)
    ttk.Label(
        dialog, text="Enter new refresh rate (seconds):", font=("Segoe UI", 10)
    ).pack()

    entry = ttk.Entry(dialog, width=12)
    entry.insert(0, str(current_sec))
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
            parent_app.config["refresh_rate"] = int(val * 1000)
            save_config(parent_app.config)
            messagebox.showinfo("Success", "Refresh rate updated successfully.")
            dialog.destroy()
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter a valid number.")

    ttk.Button(dialog, text="Apply", command=apply).pack(pady=10)


def open_alert_settings_dialog(parent_app: Any) -> None:
    """Open dialog to configure temperature and utilization alert thresholds."""
    dialog = tk.Toplevel(parent_app.root)
    dialog.title("Alert Settings")
    dialog.resizable(False, False)
    dialog.grab_set()

    ttk.Label(
        dialog,
        text="Temperature Alert Threshold (°C):",
        font=("Segoe UI", 10),
    ).pack(pady=5)
    temp_entry = ttk.Entry(dialog, width=12)
    temp_entry.insert(0, str(parent_app.config.get("alert_temp", 80)))
    temp_entry.pack()

    ttk.Label(
        dialog,
        text="Utilization Alert Threshold (%):",
        font=("Segoe UI", 10),
    ).pack(pady=5)
    util_entry = ttk.Entry(dialog, width=12)
    util_entry.insert(0, str(parent_app.config.get("alert_util", 90)))
    util_entry.pack()

    enabled_var = tk.BooleanVar(value=parent_app.config.get("alert_enabled", True))
    ttk.Checkbutton(dialog, text="Enable Desktop Alerts", variable=enabled_var).pack(
        pady=10
    )

    try:
        import plyer  # noqa: F401
    except ImportError:
        ttk.Label(
            dialog,
            text="Note: Install 'plyer' for desktop notifications",
            font=("Segoe UI", 9, "italic"),
            foreground="orange",
        ).pack()

    def apply():
        try:
            parent_app.config["alert_temp"] = int(temp_entry.get())
            parent_app.config["alert_util"] = int(util_entry.get())
            parent_app.config["alert_enabled"] = enabled_var.get()
            save_config(parent_app.config)
            messagebox.showinfo("Success", "Alert settings updated.")
            dialog.destroy()
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter valid numbers.")

    ttk.Button(dialog, text="Apply", command=apply).pack(pady=10)


def show_about_dialog(version: str, gpu_name: str) -> None:
    """Show About application information modal."""
    about_text = f"""NVIDIA-SMI GPU Monitor
Version {version}

A robust real-time GPU monitoring tool:
• Multi-GPU support with dedicated cards and aggregate view
• Defensive handling for unsupported laptop/cloud metrics
• Desktop alerts & Polkit/UAC elevated power-limit configuration
• Process table with instant search filter & task management
• Real-time history graphs and data export

GPU(s): {gpu_name}
Platform: {platform.system()} {platform.release()}

© 2026 - MIT License"""
    messagebox.showinfo("About", about_text)
