"""Modal dialogs for settings, alerts, and power limits."""

from __future__ import annotations

import math
import platform
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from config import save_config
from nvidia_smi import get_power_limits_checked as get_power_limits, set_power_limit
from ui.theme import DARK_THEME, LIGHT_THEME


def open_power_limit_window(parent_app: Any, initial_gpu_index: int = 0) -> None:
    """Open a GPU-specific dialog whose asynchronous work stays with it."""
    dialog = tk.Toplevel(parent_app.root)
    dialog.title("Power Limit Configuration")
    dialog.resizable(False, False)
    dialog.grab_set()

    theme = DARK_THEME if parent_app.config["dark_mode"] else LIGHT_THEME
    dialog.configure(bg=theme["bg"])

    detected_gpus = list(parent_app.current_gpus or [])
    if not detected_gpus:
        detected_gpus = [{"index": 0, "name": "GPU 0"}]
    indexed_gpus = []
    for position, gpu in enumerate(detected_gpus):
        try:
            gpu_index = int(gpu.get("index", position))
        except (AttributeError, TypeError, ValueError):
            continue
        indexed_gpus.append((gpu_index, gpu))
    if not indexed_gpus:
        indexed_gpus = [(0, {"index": 0, "name": "GPU 0"})]

    selected_position = next(
        (
            position
            for position, (gpu_index, _gpu) in enumerate(indexed_gpus)
            if gpu_index == initial_gpu_index
        ),
        0,
    )
    selected_gpu_var = tk.StringVar(value=str(indexed_gpus[selected_position][0]))

    main_frame = ttk.Frame(dialog)
    main_frame.pack(padx=25, pady=20, fill="both", expand=True)
    ttk.Label(main_frame, text="GPU POWER LIMIT", style="Title.TLabel").pack(
        pady=(0, 15)
    )

    combo = None
    if len(indexed_gpus) > 1:
        selector_frame = ttk.Frame(main_frame)
        selector_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(selector_frame, text="Select GPU:", font=("Segoe UI", 9)).pack(
            side="left", padx=(0, 8)
        )
        gpu_options = [
            f"GPU {gpu_index}: {gpu.get('name', 'NVIDIA GPU')}"
            for gpu_index, gpu in indexed_gpus
        ]
        combo = ttk.Combobox(
            selector_frame, values=gpu_options, state="readonly", width=35
        )
        combo.current(selected_position)
        combo.pack(side="left", fill="x", expand=True)

    info_frame = ttk.Frame(main_frame, style="Card.TFrame")
    info_frame.pack(fill="x", pady=(0, 15), ipady=10, ipadx=10)
    cur_label = ttk.Label(
        info_frame, text="Fetching power limits...", font=("Segoe UI Semibold", 10)
    )
    cur_label.pack(pady=3)
    range_label = ttk.Label(
        info_frame, text="", font=("Segoe UI", 9), foreground=theme["muted"]
    )
    range_label.pack(pady=3)

    ttk.Label(main_frame, text="New Power Limit (Watts):", font=("Segoe UI", 9)).pack(
        pady=(0, 5), anchor="w"
    )
    entry = ttk.Entry(main_frame, width=20, font=("Segoe UI", 11), state="disabled")
    entry.pack(pady=(0, 10), ipady=4, fill="x")
    msg = tk.StringVar()
    msg_label = ttk.Label(
        main_frame, textvariable=msg, font=("Segoe UI", 9), wraplength=350
    )
    msg_label.pack(pady=8)
    button_frame = ttk.Frame(main_frame)
    button_frame.pack(pady=15)

    limits: dict[str, float | None] = {"cur": None, "min": None, "max": None}
    limits_ready = False
    applying = False
    closed = False
    request_generation = 0
    pending_after_ids: set[Any] = set()

    def is_open() -> bool:
        if closed:
            return False
        try:
            return bool(dialog.winfo_exists())
        except (tk.TclError, RuntimeError):
            return False

    def schedule(callback: Any) -> None:
        if not is_open():
            return
        callback_id_holder: list[Any] = []

        def run_callback() -> None:
            if callback_id_holder:
                pending_after_ids.discard(callback_id_holder[0])
            if is_open():
                callback()

        callback_id = dialog.after(50, run_callback)
        callback_id_holder.append(callback_id)
        pending_after_ids.add(callback_id)

    def update_controls() -> None:
        enabled = is_open() and limits_ready and not applying
        entry.config(state="normal" if enabled else "disabled")
        apply_button.config(state="normal" if enabled else "disabled")
        if combo is not None:
            combo.config(state="disabled" if applying or not is_open() else "readonly")

    def set_message(text: str, color: str) -> None:
        if is_open():
            msg.set(text)
            msg_label.config(foreground=color)

    def close_dialog() -> None:
        nonlocal closed, request_generation
        if closed:
            return
        closed = True
        request_generation += 1
        for callback_id in tuple(pending_after_ids):
            try:
                dialog.after_cancel(callback_id)
            except (tk.TclError, RuntimeError):
                pass
        pending_after_ids.clear()
        try:
            dialog.grab_release()
        except (tk.TclError, RuntimeError):
            pass
        try:
            dialog.destroy()
        except (tk.TclError, RuntimeError):
            pass

    def fetch_limits_async(gpu_index: int, preserve_message: bool = False) -> None:
        nonlocal limits_ready, request_generation
        request_generation += 1
        generation = request_generation
        limits_ready = False
        limits.update(cur=None, min=None, max=None)
        cur_label.config(text="Fetching power limits...")
        range_label.config(text="")
        if not preserve_message:
            set_message("", theme["muted"])
        update_controls()
        result_box: dict[str, Any] = {"done": False, "value": None, "error": None}

        def worker() -> None:
            try:
                result_box["value"] = get_power_limits(gpu_index=gpu_index)
            except Exception as exc:  # Report worker failures through the Tk thread.
                result_box["error"] = str(exc) or exc.__class__.__name__
            finally:
                result_box["done"] = True

        threading.Thread(target=worker, daemon=True).start()

        def poll_result() -> None:
            nonlocal limits_ready
            if not is_open() or generation != request_generation:
                return
            if not result_box["done"]:
                schedule(poll_result)
                return

            result = result_box["value"]
            reason = result_box["error"]
            current = minimum = maximum = None
            if reason is None:
                if not isinstance(result, (tuple, list)) or len(result) != 3:
                    reason = "backend returned an invalid response"
                else:
                    raw_current, raw_minimum, raw_maximum = result

                    def finite_limit(value: Any) -> float | None:
                        if value is None or isinstance(value, bool):
                            return None
                        try:
                            number = float(value)
                        except (TypeError, ValueError, OverflowError):
                            return None
                        return number if math.isfinite(number) else None

                    current = finite_limit(raw_current)
                    minimum = finite_limit(raw_minimum)
                    maximum = finite_limit(raw_maximum)
                    if (
                        minimum is None
                        or maximum is None
                        or minimum <= 0
                        or maximum <= 0
                        or minimum > maximum
                    ):
                        reason = "backend returned unavailable or invalid bounds"

            if reason is not None:
                cur_label.config(text="Current: Unknown")
                range_label.config(text="Allowed Range: Unavailable")
                set_message(f"Power limits unavailable: {reason}.", theme["danger"])
                update_controls()
                return

            limits.update(cur=current, min=minimum, max=maximum)
            current_text = f"{current:.1f} W" if current is not None else "Unknown"
            cur_label.config(text=f"Current: {current_text}")
            range_label.config(text=f"Allowed Range: {minimum:.1f} - {maximum:.1f} W")
            limits_ready = True
            update_controls()

        schedule(poll_result)

    def apply_limit() -> None:
        nonlocal applying
        if not is_open() or applying:
            return
        if not limits_ready:
            set_message(
                "Wait for valid GPU power limits before applying.", theme["danger"]
            )
            return
        try:
            value = float(entry.get().strip())
        except (TypeError, ValueError):
            set_message("Invalid input - enter a finite number.", theme["danger"])
            return
        if not math.isfinite(value):
            set_message("Invalid input - enter a finite number.", theme["danger"])
            return

        minimum = limits["min"]
        maximum = limits["max"]
        if minimum is None or maximum is None:
            set_message(
                "Wait for valid GPU power limits before applying.", theme["danger"]
            )
            update_controls()
            return
        if value < minimum or value > maximum:
            set_message(
                f"Value must be between {minimum:.1f} and {maximum:.1f} W",
                theme["danger"],
            )
            return
        try:
            gpu_index = int(selected_gpu_var.get())
        except (TypeError, ValueError):
            set_message("No valid GPU is selected.", theme["danger"])
            return

        applying = True
        generation = request_generation
        update_controls()
        set_message("Applying power limit...", theme["info"])
        apply_result: dict[str, Any] = {"done": False, "value": None, "error": None}

        def worker() -> None:
            try:
                apply_result["value"] = set_power_limit(
                    value,
                    gpu_index=gpu_index,
                    min_limit=minimum,
                    max_limit=maximum,
                )
            except Exception as exc:  # Report worker failures through the Tk thread.
                apply_result["error"] = str(exc) or exc.__class__.__name__
            finally:
                apply_result["done"] = True

        threading.Thread(target=worker, daemon=True).start()

        def poll_apply() -> None:
            nonlocal applying, limits_ready
            if not is_open() or generation != request_generation:
                return
            if not apply_result["done"]:
                schedule(poll_apply)
                return

            applying = False
            if apply_result["error"] is not None:
                update_controls()
                set_message(f"Failed: {apply_result['error']}", theme["danger"])
                return
            result = apply_result["value"]
            if not isinstance(result, (tuple, list)) or len(result) != 2:
                update_controls()
                set_message(
                    "Failed: backend returned an invalid response.", theme["danger"]
                )
                return

            succeeded = result[0] is True
            diagnostic = result[1]
            diagnostic_text = str(diagnostic or "").strip()
            lower_diagnostic = diagnostic_text.lower()
            submitted = bool(succeeded) and any(
                marker in lower_diagnostic
                for marker in ("elevation prompt requested", "submitted", "pending")
            )
            unconfirmed = any(
                marker in lower_diagnostic
                for marker in (
                    "unconfirmed",
                    "completion is unknown",
                    "completion unknown",
                    "could not be confirmed",
                )
            )
            if submitted or unconfirmed:
                limits_ready = False
                limits.update(cur=None, min=None, max=None)
                update_controls()
                detail = f" {diagnostic_text}" if diagnostic_text else ""
                set_message(
                    (
                        "Power limit request submitted; completion is not confirmed."
                        if submitted
                        else f"Failed: {diagnostic_text or 'Power limit completion is unconfirmed.'}"
                    )
                    + (detail if submitted else ""),
                    theme["info"] if submitted else theme["danger"],
                )
            elif succeeded:
                detail = f" {diagnostic_text}" if diagnostic_text else ""
                set_message(
                    "Power limit update applied successfully." + detail,
                    theme["success"],
                )
                fetch_limits_async(gpu_index, preserve_message=True)
            else:
                update_controls()
                detail = diagnostic_text or "No backend diagnostic was provided."
                set_message(f"Failed: {detail}", theme["danger"])

        schedule(poll_apply)

    apply_button = ttk.Button(
        button_frame,
        text="Apply Changes",
        style="Accent.TButton",
        state="disabled",
        command=apply_limit,
    )
    apply_button.pack(side="left", padx=5)
    ttk.Button(button_frame, text="Cancel", command=close_dialog).pack(
        side="left", padx=5
    )
    entry.bind("<Return>", lambda _event: apply_limit())
    entry.bind("<Escape>", lambda _event: close_dialog())
    dialog.protocol("WM_DELETE_WINDOW", close_dialog)

    if combo is not None:

        def on_gpu_changed(_event: Any) -> None:
            if applying:
                return
            position = combo.current()
            if 0 <= position < len(indexed_gpus):
                gpu_index = indexed_gpus[position][0]
                selected_gpu_var.set(str(gpu_index))
                fetch_limits_async(gpu_index)

        combo.bind("<<ComboboxSelected>>", on_gpu_changed)

    fetch_limits_async(indexed_gpus[selected_position][0])


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
            if not math.isfinite(val) or val < 0.5 or val > 60:
                messagebox.showerror(
                    "Invalid Input",
                    "Refresh rate must be between 0.5 and 60 seconds",
                )
                return
            _save_dialog_config(
                parent_app,
                dialog,
                {"refresh_rate": int(round(val * 1000))},
                "Refresh rate updated successfully.",
            )
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter a valid number.")

    ttk.Button(dialog, text="Apply", command=apply).pack(pady=10)


def _save_dialog_config(
    parent_app: Any,
    dialog: tk.Toplevel,
    updates: dict[str, Any],
    success_message: str,
) -> bool:
    """Persist settings before applying them to the live application config."""
    updated_config = dict(parent_app.config)
    updated_config.update(updates)
    try:
        saved = save_config(updated_config)
    except Exception as exc:
        messagebox.showerror("Save Failed", f"Could not save settings: {exc}")
        return False
    if not saved:
        messagebox.showerror(
            "Save Failed",
            "Could not save settings. Check the configuration file permissions.",
        )
        return False

    parent_app.config.update(updates)
    messagebox.showinfo("Success", success_message)
    dialog.destroy()
    return True


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
            alert_temp = int(temp_entry.get())
            alert_util = int(util_entry.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter valid numbers.")
            return
        if not 0 <= alert_temp <= 150:
            messagebox.showerror(
                "Invalid Input", "Temperature threshold must be between 0 and 150 °C."
            )
            return
        if not 0 <= alert_util <= 100:
            messagebox.showerror(
                "Invalid Input", "Utilization threshold must be between 0 and 100 %."
            )
            return
        _save_dialog_config(
            parent_app,
            dialog,
            {
                "alert_temp": alert_temp,
                "alert_util": alert_util,
                "alert_enabled": bool(enabled_var.get()),
            },
            "Alert settings updated.",
        )

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
