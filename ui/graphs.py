"""Historical metrics graphing window using Matplotlib (optional dependency)."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any

from ui.theme import DARK_THEME, LIGHT_THEME

try:
    import matplotlib

    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


class HistoryGraphWindow:
    """Toplevel window for plotting real-time GPU history graphs."""

    def __init__(self, parent_app: Any):
        self.parent = parent_app
        self.window: tk.Toplevel | None = None

    def show(self) -> None:
        if not MATPLOTLIB_AVAILABLE:
            messagebox.showwarning(
                "Matplotlib Not Available",
                "Install matplotlib to view graphs:\npip install matplotlib",
            )
            return

        if self.window and tk.Toplevel.winfo_exists(self.window):
            self.window.lift()
            return

        self.window = tk.Toplevel(self.parent.root)
        self.window.title("GPU History Graphs")
        self.window.geometry("1000x700")

        theme = DARK_THEME if self.parent.config["dark_mode"] else LIGHT_THEME
        fig = Figure(figsize=(10, 7), dpi=100)
        fig.patch.set_facecolor(theme["bg"])

        ax1 = fig.add_subplot(2, 2, 1)
        ax2 = fig.add_subplot(2, 2, 2)
        ax3 = fig.add_subplot(2, 2, 3)
        ax4 = fig.add_subplot(2, 2, 4)

        canvas = FigureCanvasTkAgg(fig, master=self.window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        history_data = self.parent.history_data

        def update_graphs():
            if not self.window or not tk.Toplevel.winfo_exists(self.window):
                return

            if len(history_data["time"]) > 0:
                time_range = list(range(len(history_data["time"])))

                plots = [
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
                ]
                for ax, data_key, title, ylabel, color in plots:
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

            self.parent.root.after(2000, update_graphs)

        update_graphs()

        def on_close():
            if self.window:
                self.window.destroy()
                self.window = None

        self.window.protocol("WM_DELETE_WINDOW", on_close)
