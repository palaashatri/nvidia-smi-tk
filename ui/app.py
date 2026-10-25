"""Main Tkinter GUI Application for NVIDIA-SMI GPU Monitor."""

from __future__ import annotations

import csv
import json
import os
import platform
import queue
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
    get_full_output,
    get_nvidia_smi_output,
    parse_gpu_metrics,
    parse_processes,
)
from ui.dialogs import (
    open_alert_settings_dialog,
    open_power_limit_window,
    open_refresh_rate_dialog,
    show_about_dialog,
)
from ui.graphs import HistoryGraphWindow
from ui.theme import DARK_THEME, LIGHT_THEME, create_tooltip
from ui.tray import setup_tray_icon

VERSION = "2.1.0"
HISTORY_SIZE = 60

try:
    from plyer import notification

    PLYER_AVAILABLE = True
except ImportError:
    PLYER_AVAILABLE = False


class GPUMonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.config = load_config()
        self.labels: dict[str, Any] = {}
        self.gpu_cards: dict[int, dict[str, Any]] = {}
        self.current_gpus: list[dict[str, Any]] = []
        self.sort_column: str | None = None
        self.sort_reverse = False
        self.graph_window: HistoryGraphWindow | None = None
        self.last_process_list: list[dict[str, str]] = []
        self.hover_row: str | None = None
        self.proc_search_query = ""
        self.tray_icon: Any = None
        self.last_alert_time: dict[str, float] = {}
        self.current_gpu_identity: tuple[tuple[Any, Any, Any], ...] = ()
        self.last_history_timestamp: datetime | None = None

        self.history_data = {
            "time": deque(maxlen=HISTORY_SIZE),
            "utilization": deque(maxlen=HISTORY_SIZE),
            "temperature": deque(maxlen=HISTORY_SIZE),
            "power": deque(maxlen=HISTORY_SIZE),
            "memory": deque(maxlen=HISTORY_SIZE),
        }

        self.gpu_data_cache: dict[str, Any] = {}
        self._cache_lock = threading.Lock()
        self.tray_event_queue: queue.Queue[str] = queue.Queue()
        self._tray_event_after_id: str | None = None
        self.stop_update = False
        self.update_event = threading.Event()
        self.update_thread: threading.Thread | None = None

        self.root.title(f"NVIDIA-SMI GPU Monitor v{VERSION}")
        self.root.geometry(
            f"{self.config['window_width']}x{self.config['window_height']}"
            f"{self.config['window_x']:+d}{self.config['window_y']:+d}"
        )

        self.setup_menu()
        self.apply_theme()
        self.init_gui()
        self.setup_keyboard_shortcuts()

        if self.config.get("always_on_top", False):
            self.root.attributes("-topmost", True)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._tray_event_after_id = self.root.after(50, self._poll_tray_events)
        self.start_background_update()
        self.tray_icon = setup_tray_icon(self)

    def setup_menu(self) -> None:
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
        self.always_on_top_var: tk.BooleanVar = tk.BooleanVar(
            value=self.config.get("always_on_top", False)
        )
        view_menu.add_checkbutton(
            label="Always on Top",
            command=self.toggle_always_on_top,
            variable=self.always_on_top_var,
        )
        view_menu.add_command(
            label="Refresh", command=self.manual_refresh, accelerator="F5"
        )
        view_menu.add_command(
            label="Show Graphs", command=self.show_graphs, accelerator="Ctrl+G"
        )

        settings_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        settings_menu.add_command(
            label="Adjust Refresh Rate", command=lambda: open_refresh_rate_dialog(self)
        )
        settings_menu.add_command(
            label="Alert Settings", command=lambda: open_alert_settings_dialog(self)
        )
        settings_menu.add_command(
            label="Power Limit", command=lambda: open_power_limit_window(self)
        )

        help_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(
            label="About",
            command=lambda: show_about_dialog(VERSION, self.labels["gpu"].cget("text")),
        )

    def setup_keyboard_shortcuts(self) -> None:
        self.root.bind("<F5>", lambda _e: self.manual_refresh())
        self.root.bind("<Control-q>", lambda _e: self.on_closing())
        self.root.bind("<Control-d>", lambda _e: self.toggle_theme())
        self.root.bind("<Control-g>", lambda _e: self.show_graphs())

    def apply_theme(self) -> None:
        self.current_theme = DARK_THEME if self.config["dark_mode"] else LIGHT_THEME
        theme = self.current_theme
        self.root.configure(bg=theme["bg"])
        if hasattr(self, "content_canvas"):
            self.content_canvas.configure(bg=theme["bg"])

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

    def update_text_widget_colors(self) -> None:
        theme = DARK_THEME if self.config["dark_mode"] else LIGHT_THEME
        if "full_output_text" in self.labels:
            self.labels["full_output_text"].config(
                bg=theme["text_bg"], fg=theme["text_fg"]
            )

    def toggle_theme(self) -> None:
        self.config["dark_mode"] = not self.config["dark_mode"]
        save_config(self.config)
        self.apply_theme()
        self.update_text_widget_colors()
        self.rebuild_gpu_cards()

    def toggle_always_on_top(self) -> None:
        self.config["always_on_top"] = not self.config.get("always_on_top", False)
        if hasattr(self, "always_on_top_var"):
            self.always_on_top_var.set(self.config["always_on_top"])
        self.root.attributes("-topmost", self.config["always_on_top"])
        save_config(self.config)

    def show_graphs(self) -> None:
        if not self.graph_window:
            self.graph_window = HistoryGraphWindow(self)
        self.graph_window.show()

    def export_csv(self) -> None:
        from tkinter import filedialog

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")]
        )
        if not filename:
            return
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
                for i in range(len(self.history_data["time"])):
                    writer.writerow(
                        [
                            self.history_data["time"][i],
                            self.history_data["utilization"][i],
                            self.history_data["temperature"][i],
                            self.history_data["power"][i],
                            self.history_data["memory"][i],
                        ]
                    )
            messagebox.showinfo("Success", f"Data exported to {filename}")
        except OSError:
            messagebox.showerror("Error", "Failed to export CSV.")

    def export_json(self) -> None:
        from tkinter import filedialog

        filename = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON files", "*.json")]
        )
        if not filename:
            return
        try:
            data = {
                "export_time": datetime.now().isoformat(),
                "gpu_name": self.labels["gpu"].cget("text"),
                "data": [],
            }
            for i in range(len(self.history_data["time"])):
                data["data"].append(
                    {
                        "timestamp": self.history_data["time"][i],
                        "utilization": self.history_data["utilization"][i],
                        "temperature": self.history_data["temperature"][i],
                        "power": self.history_data["power"][i],
                        "memory": self.history_data["memory"][i],
                    }
                )
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            messagebox.showinfo("Success", f"Data exported to {filename}")
        except OSError:
            messagebox.showerror("Error", "Failed to export JSON.")

    def sort_treeview(self, col: str) -> None:
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
                key=lambda t: float(t[0].split()[0]) if t[0] and t[0] != "N/A" else 0,
                reverse=self.sort_reverse,
            )
        except (ValueError, IndexError):
            items.sort(reverse=self.sort_reverse)

        for index, (_val, k) in enumerate(items):
            self.labels["proc_table"].move(k, "", index)

    def copy_selected_value(self, column_index: int) -> None:
        selection = self.labels["proc_table"].selection()
        if not selection:
            return
        values = self.labels["proc_table"].item(selection[0], "values")
        if column_index >= len(values):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(str(values[column_index]))
        self.labels["status"].config(text=f"Copied: {values[column_index]}")

    def end_selected_task(self) -> None:
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
        except (subprocess.SubprocessError, OSError) as exc:
            messagebox.showerror("Error", f"Could not end task: {exc}")

    def init_gui(self) -> None:
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

        # Start with Detecting GPU... (never blocks the main thread on startup)
        self.labels["gpu"] = tk.Label(
            header_content,
            text="Detecting GPU...",
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

        def make_toolbar_btn(text: str, cmd: Any, tooltip: str) -> tk.Button:
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
        make_toolbar_btn("Graphs", self.show_graphs, "Open graphs (Ctrl+G)")
        make_toolbar_btn("Theme", self.toggle_theme, "Toggle theme (Ctrl+D)")

        # Place GPU cards and process details inside a real scrollable canvas.
        content_shell = tk.Frame(self.root, bg=theme["bg"])
        content_shell.pack(fill="both", expand=True, padx=20, pady=(10, 0))
        self.content_canvas = tk.Canvas(
            content_shell,
            bg=theme["bg"],
            highlightthickness=0,
            yscrollincrement=24,
        )
        self.content_canvas.pack(side="left", fill="both", expand=True)
        content_scrollbar = ttk.Scrollbar(
            content_shell, orient="vertical", command=self.content_canvas.yview
        )
        content_scrollbar.pack(side="right", fill="y")
        self.content_canvas.configure(yscrollcommand=content_scrollbar.set)

        self.cards_parent_frame = ttk.Frame(
            self.content_canvas, style="MainContainer.TFrame"
        )
        self._cards_window = self.content_canvas.create_window(
            (0, 0), window=self.cards_parent_frame, anchor="nw"
        )
        self.cards_parent_frame.bind("<Configure>", self._update_content_scrollregion)
        self.content_canvas.bind("<Configure>", self._resize_content_window)
        self.content_canvas.bind("<MouseWheel>", self._scroll_content)
        self.content_canvas.bind("<Button-4>", self._scroll_content)
        self.content_canvas.bind("<Button-5>", self._scroll_content)

        self.gpu_cards_container = ttk.Frame(
            self.cards_parent_frame, style="MainContainer.TFrame"
        )
        self.gpu_cards_container.pack(fill="x", pady=(0, 10))

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

        self.labels["proc_table"].bind("<Motion>", self._on_process_tree_motion)

        def show_context(event: tk.Event) -> None:
            row = self.labels["proc_table"].identify_row(event.y)
            if row:
                self.labels["proc_table"].selection_set(row)
            try:
                self.proc_context.tk_popup(event.x_root, event.y_root)
            finally:
                self.proc_context.grab_release()

        self.labels["proc_table"].bind("<Button-3>", show_context)

        # Collapsible Full Output
        def toggle_full_output() -> None:
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
        """Create a summary overview banner for multi-GPU systems."""
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

    def rebuild_gpu_cards(self) -> None:
        """Reconstruct GPU metric cards when GPUs are discovered or theme toggled."""
        for child in self.gpu_cards_container.winfo_children():
            child.destroy()

        self.gpu_cards.clear()
        metric_aliases = (
            "util",
            "util_bar",
            "mem",
            "mem_bar",
            "temp",
            "temp_bar",
            "power",
            "power_bar",
            "fan",
            "clock_gpu",
            "clock_mem",
        )
        for key in metric_aliases:
            self.labels.pop(key, None)

        gpus = self.current_gpus
        if not gpus:
            self.combined_widgets = {}
            return

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

        # Keep aliases pointed at the first currently detected GPU card.
        primary_widgets = next(iter(self.gpu_cards.values()), {})
        for key, widget in primary_widgets.items():
            if key != "frame":
                self.labels[key] = widget

    def apply_proc_filter(self) -> None:
        """Filter running processes in the treeview based on user search query."""
        self.proc_search_query = self.filter_entry_var.get().strip().lower()
        self.refresh_process_table(self.last_process_list, force=True)

    def refresh_process_table(
        self, processes: list[dict[str, str]], force: bool = False
    ) -> None:
        """Update process treeview with optional filtering and striping."""
        filtered = [
            p
            for p in processes
            if not self.proc_search_query
            or self.proc_search_query in p.get("name", "").lower()
            or self.proc_search_query in p.get("pid", "").lower()
        ]

        selected_key: tuple[str, str, str] | None = None
        selection = self.labels["proc_table"].selection()
        if selection:
            selected_values = self.labels["proc_table"].item(selection[0], "values")
            if len(selected_values) >= 3:
                selected_key = tuple(str(value) for value in selected_values[:3])

        new_items = [
            (
                str(p.get("gpu", "0")),
                str(p.get("pid", "")),
                str(p.get("name", "")),
                str(p.get("mem", "")),
            )
            for p in filtered
        ]
        if self.sort_column:
            column_index = {"GPU": 0, "PID": 1, "Name": 2, "Memory": 3}.get(
                self.sort_column
            )
            if column_index is not None:

                def sort_key(row: tuple[str, str, str, str]) -> tuple[int, Any]:
                    value = row[column_index]
                    try:
                        return 0, float(value.split()[0])
                    except (ValueError, IndexError):
                        return 1, value.casefold()

                new_items.sort(key=sort_key, reverse=self.sort_reverse)

        current_items = [
            self.labels["proc_table"].item(c)["values"]
            for c in self.labels["proc_table"].get_children()
        ]

        if force or current_items != new_items:
            self.hover_row = None
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
            selected_item: str | None = None
            for idx, row in enumerate(new_items):
                tag = "even" if idx % 2 == 0 else "odd"
                item_id = self.labels["proc_table"].insert(
                    "",
                    "end",
                    values=row,
                    tags=(tag,),
                )
                if selected_key == row[:3]:
                    selected_item = item_id
            if selected_item is not None:
                self.labels["proc_table"].selection_set(selected_item)

    def _on_process_tree_motion(self, event: tk.Event) -> None:
        tree = self.labels["proc_table"]
        row = tree.identify_row(event.y)
        if row == self.hover_row:
            return
        if self.hover_row:
            try:
                tags = [
                    tag for tag in tree.item(self.hover_row, "tags") if tag != "hover"
                ]
                tree.item(self.hover_row, tags=tags)
            except tk.TclError:
                pass
        self.hover_row = row
        if row:
            tags = list(tree.item(row, "tags"))
            if "hover" not in tags:
                tags.append("hover")
            tree.item(row, tags=tags)

    def _update_content_scrollregion(self, _event: tk.Event | None = None) -> None:
        self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))

    def _resize_content_window(self, event: tk.Event) -> None:
        self.content_canvas.itemconfigure(self._cards_window, width=event.width)

    def _scroll_content(self, event: tk.Event) -> str:
        if getattr(event, "num", None) == 4:
            self.content_canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self.content_canvas.yview_scroll(1, "units")
        else:
            self.content_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def check_alerts(self, gpus: list[dict[str, Any]]) -> None:
        """Check all GPUs against configured alert thresholds."""
        if not self.config.get("alert_enabled", True) or not PLYER_AVAILABLE:
            return

        current_time = time.time()
        alert_temp = self.config.get("alert_temp", 80)
        alert_util = self.config.get("alert_util", 90)

        for gpu in gpus:
            idx = gpu.get("index", 0)
            temp = gpu.get("temperature")
            util = gpu.get("utilization")

            temp_key = f"temp_{idx}"
            if (
                temp is not None
                and temp >= alert_temp
                and current_time - self.last_alert_time.get(temp_key, 0) > 300
            ):
                try:
                    notification.notify(
                        title=f"GPU {idx} Temperature Alert",
                        message=f"GPU {idx} is {temp}°C (threshold: {alert_temp}°C)",
                        app_name="NVIDIA-SMI Monitor",
                        timeout=5,
                    )
                except (OSError, RuntimeError):
                    pass
                self.last_alert_time[temp_key] = current_time

            util_key = f"util_{idx}"
            if (
                util is not None
                and util >= alert_util
                and current_time - self.last_alert_time.get(util_key, 0) > 300
            ):
                try:
                    notification.notify(
                        title=f"GPU {idx} Utilization Alert",
                        message=(
                            f"GPU {idx} utilization is {util:.1f}% "
                            f"(threshold: {alert_util}%)"
                        ),
                        app_name="NVIDIA-SMI Monitor",
                        timeout=5,
                    )
                except (OSError, RuntimeError):
                    pass
                self.last_alert_time[util_key] = current_time

    def _publish_gpu_data_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Atomically publish a complete cache snapshot from the worker."""
        with self._cache_lock:
            updated = dict(self.gpu_data_cache)
            if "error" in snapshot:
                updated["error"] = snapshot["error"]
            else:
                updated.pop("error", None)
            updated.update(snapshot)
            self.gpu_data_cache = updated

    def _read_gpu_data_snapshot(self) -> dict[str, Any]:
        """Return one consistent copy of the latest worker snapshot."""
        with self._cache_lock:
            return dict(self.gpu_data_cache)

    def start_background_update(self) -> None:
        """Start single background worker thread to query nvidia-smi."""
        self.stop_update = False

        def background_worker() -> None:
            full_output_counter = 0
            while not self.stop_update:
                try:
                    gpu_output, proc_output = get_nvidia_smi_output()
                    snapshot: dict[str, Any] = {
                        "gpu_output": gpu_output,
                        "proc_output": proc_output,
                        "timestamp": datetime.now(),
                    }

                    fetch_full_output = full_output_counter == 0
                    full_output_counter = (full_output_counter + 1) % 5
                    if fetch_full_output:
                        snapshot["full_output"] = get_full_output(timeout=5)
                    self._publish_gpu_data_snapshot(snapshot)
                except (subprocess.SubprocessError, OSError, ValueError) as exc:
                    self._publish_gpu_data_snapshot({"error": str(exc)})

                # Sleep in increments or wait for manual refresh event
                wait_sec = self.config["refresh_rate"] / 1000
                self.update_event.wait(timeout=wait_sec)
                self.update_event.clear()

        self.update_thread = threading.Thread(target=background_worker, daemon=True)
        self.update_thread.start()
        self.update_gui()

    def manual_refresh(self) -> None:
        """Trigger immediate background fetch without spawning duplicate loops."""
        self.labels["status"].config(text="Refreshing metrics...")
        self.update_event.set()

    def update_gui(self) -> None:
        """Main thread GUI refresh function."""
        if self.stop_update:
            return

        data_snapshot = self._read_gpu_data_snapshot()
        if "error" in data_snapshot:
            self.labels["gpu"].config(text=f"Error: {data_snapshot['error']}")
            self.root.after(3000, self.update_gui)
            return

        if "gpu_output" not in data_snapshot:
            self.root.after(300, self.update_gui)
            return

        gpu_output = data_snapshot.get("gpu_output", "")
        proc_output = data_snapshot.get("proc_output", "")
        timestamp = data_snapshot.get("timestamp", datetime.now())

        if gpu_output.startswith("Error"):
            self.labels["gpu"].config(text=gpu_output)
            self.labels["status"].config(
                text=f"Error at {timestamp.strftime('%H:%M:%S')}"
            )
            self.root.after(3000, self.update_gui)
            return

        # Clear any prior transient error once data is good
        gpus = parse_gpu_metrics(gpu_output)

        # Build UUID -> Index mapping for processes
        uuid_to_idx = {g["uuid"]: g["index"] for g in gpus if g.get("uuid") is not None}
        processes = parse_processes(proc_output, uuid_to_index=uuid_to_idx)
        if not gpus:
            processes = []
        self.last_process_list = processes

        # Synchronize header label
        if gpus:
            if len(gpus) == 1:
                self.labels["gpu"].config(text=gpus[0]["name"])
            else:
                self.labels["gpu"].config(
                    text=f"{gpus[0]['name']} (+{len(gpus) - 1} more)"
                )
        else:
            self.labels["gpu"].config(text="No NVIDIA GPUs detected")

        # Synchronize GPU cards if indices changed
        identity = tuple(
            (gpu.get("index"), gpu.get("name"), gpu.get("uuid")) for gpu in gpus
        )
        existing_indices = set(self.gpu_cards.keys())
        new_indices = {g["index"] for g in gpus}
        identity_changed = identity != self.current_gpu_identity
        self.current_gpus = [dict(gpu) for gpu in gpus]
        self.current_gpu_identity = identity
        if (
            identity_changed
            or existing_indices != new_indices
            or (gpus and not self.gpu_cards)
        ):
            self.rebuild_gpu_cards()

        if not gpus:
            self.refresh_process_table(processes)
            time_str = timestamp.strftime("%H:%M:%S")
            self.labels["status"].config(
                text=f"No NVIDIA GPUs detected · Last checked {time_str}"
            )
            self.root.after(self.config["refresh_rate"], self.update_gui)
            return

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

        # Record one history row for each distinct GPU query result.
        primary_gpu = gpus[0] if gpus else {}
        self._record_history_sample(timestamp, primary_gpu)

        self.check_alerts(gpus)
        self.refresh_process_table(processes)

        # Lazy update full text output if visible
        if (
            "full_output_text" in self.labels
            and self.labels["full_output_text"].winfo_viewable()
            and "full_output" in data_snapshot
        ):
            self.labels["full_output_text"].config(state="normal")
            self.labels["full_output_text"].delete("1.0", tk.END)
            self.labels["full_output_text"].insert("1.0", data_snapshot["full_output"])
            self.labels["full_output_text"].config(state="disabled")

        rate_sec = self.config["refresh_rate"] / 1000
        time_str = timestamp.strftime("%H:%M:%S")
        self.labels["status"].config(
            text=f"Last updated {time_str} · Refresh rate {rate_sec:.1f}s"
        )

        if self.tray_icon and int(time.time()) % 2 == 0:
            peak_u = max((g.get("utilization") or 0) for g in gpus) if gpus else 0
            peak_t = max((g.get("temperature") or 0) for g in gpus) if gpus else 0
            self.tray_icon.title = f"GPU: {peak_u:.0f}% | Temp: {peak_t}°C"

        self.root.after(self.config["refresh_rate"], self.update_gui)

    def _record_history_sample(self, timestamp: datetime, gpu: dict[str, Any]) -> bool:
        """Append a history sample once, preserving unsupported values as gaps."""
        if timestamp == self.last_history_timestamp:
            return False

        self.history_data["time"].append(timestamp.strftime("%H:%M:%S"))
        self.history_data["utilization"].append(gpu.get("utilization"))
        self.history_data["temperature"].append(gpu.get("temperature"))
        self.history_data["power"].append(gpu.get("power_draw"))

        mem_used = gpu.get("mem_used")
        mem_total = gpu.get("mem_total")
        memory_percent = (
            (mem_used / mem_total) * 100
            if mem_used is not None and mem_total is not None and mem_total > 0
            else None
        )
        self.history_data["memory"].append(memory_percent)
        self.last_history_timestamp = timestamp
        return True

    def _poll_tray_events(self) -> None:
        """Apply tray requests on Tk's main thread."""
        self._tray_event_after_id = None
        if self.stop_update:
            return

        while True:
            try:
                event = self.tray_event_queue.get_nowait()
            except queue.Empty:
                break
            if event == "show":
                self.root.deiconify()
                self.root.lift()
            elif event == "quit":
                self.on_closing()
                return

        if not self.stop_update:
            self._tray_event_after_id = self.root.after(50, self._poll_tray_events)

    def on_closing(self) -> None:
        if self.stop_update:
            return
        self.stop_update = True
        self.update_event.set()
        if self._tray_event_after_id is not None:
            try:
                self.root.after_cancel(self._tray_event_after_id)
            except tk.TclError:
                pass
            self._tray_event_after_id = None

        geometry = self.root.geometry()
        match = re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geometry)
        if match:
            self.config["window_width"] = int(match.group(1))
            self.config["window_height"] = int(match.group(2))
            self.config["window_x"] = int(match.group(3))
            self.config["window_y"] = int(match.group(4))

        save_config(self.config)

        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except (OSError, RuntimeError, AttributeError):
                pass

        self.root.quit()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    _app = GPUMonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
