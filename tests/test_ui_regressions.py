"""Focused regression tests for UI state, optional integrations, and Tk lifecycle."""

from __future__ import annotations

import importlib
import queue
import sys
import threading
import types
import unittest
from collections import deque
from datetime import datetime
from unittest.mock import patch

from config import DEFAULT_CONFIG
from ui import graphs, tray
from ui.app import GPUMonitorApp
from ui.theme import LIGHT_THEME


class FakeRoot:
    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []
        self.cancelled_after: list[str] = []
        self.deiconify_calls = 0
        self.lift_calls = 0
        self._after_index = 0

    def after(self, delay: int, callback: object) -> str:
        self._after_index += 1
        callback_id = f"after#{self._after_index}"
        self.after_calls.append((delay, callback))
        return callback_id

    def after_cancel(self, callback_id: str) -> None:
        self.cancelled_after.append(callback_id)

    def deiconify(self) -> None:
        self.deiconify_calls += 1

    def lift(self) -> None:
        self.lift_calls += 1


class FakeWidget:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}
        self.destroyed = False

    def config(self, **options: object) -> None:
        self.options.update(options)

    def __setitem__(self, key: str, value: object) -> None:
        self.options[key] = value

    def destroy(self) -> None:
        self.destroyed = True


class FakeGpuContainer:
    def __init__(self, children: list[FakeWidget] | None = None) -> None:
        self.children = children or []

    def winfo_children(self) -> tuple[FakeWidget, ...]:
        return tuple(child for child in self.children if not child.destroyed)


class FakeTreeview:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, object]] = {}
        self._children: list[str] = []
        self._selection: list[str] = []
        self._next_id = 0
        self.configured_tags: dict[str, dict[str, object]] = {}

    def get_children(self, _item: str = "") -> tuple[str, ...]:
        return tuple(self._children)

    def item(self, item_id: str, option: str | None = None, **kwargs: object):
        item = self._items[item_id]
        if kwargs:
            item.update(kwargs)
        if option == "values":
            return item["values"]
        if option == "tags":
            return item["tags"]
        return dict(item)

    def set(self, item_id: str, column: str) -> str:
        columns = {"GPU": 0, "PID": 1, "Name": 2, "Memory": 3}
        return str(self._items[item_id]["values"][columns[column]])

    def delete(self, *item_ids: str) -> None:
        for item_id in item_ids:
            self._items.pop(item_id, None)
            if item_id in self._children:
                self._children.remove(item_id)
            if item_id in self._selection:
                self._selection.remove(item_id)

    def insert(
        self,
        _parent: str,
        index: str,
        *,
        values: tuple[str, ...],
        tags: tuple[str, ...],
    ) -> str:
        self._next_id += 1
        item_id = f"row{self._next_id}"
        self._items[item_id] = {"values": values, "tags": tags}
        if index == "end":
            self._children.append(item_id)
        else:
            self._children.insert(int(index), item_id)
        return item_id

    def move(self, item_id: str, _parent: str, index: int) -> None:
        self._children.remove(item_id)
        self._children.insert(index, item_id)

    def selection(self) -> tuple[str, ...]:
        return tuple(self._selection)

    def selection_set(self, item_id: str) -> None:
        self._selection = [item_id]

    def tag_configure(self, tag: str, **options: object) -> None:
        self.configured_tags[tag] = options


def make_gpu(index: int = 0, name: str = "GPU", **changes: object) -> dict[str, object]:
    gpu: dict[str, object] = {
        "index": index,
        "uuid": f"uuid-{index}",
        "name": name,
        "utilization": 23.0,
        "mem_used": 1024.0,
        "mem_total": 8192.0,
        "temperature": 55,
        "power_draw": 120.0,
        "power_limit": 220.0,
        "fan_speed": 40.0,
        "clock_gpu": 1500.0,
        "clock_mem": 7000.0,
    }
    gpu.update(changes)
    return gpu


def make_ui_app(timestamp: datetime, gpu: dict[str, object]) -> GPUMonitorApp:
    app = GPUMonitorApp.__new__(GPUMonitorApp)
    app.root = FakeRoot()
    app.stop_update = False
    app.update_event = threading.Event()
    app.config = {"refresh_rate": 1000, "alert_enabled": False, "dark_mode": False}
    app.labels = {"gpu": FakeWidget(), "status": FakeWidget()}
    app.current_gpus = [dict(gpu)]
    app.current_gpu_identity = ((gpu["index"], gpu["name"], gpu["uuid"]),)
    app.gpu_cards = {
        int(gpu["index"]): {
            key: FakeWidget()
            for key in (
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
        }
    }
    app.combined_widgets = {}
    app.history_data = {
        key: deque(maxlen=60)
        for key in ("time", "utilization", "temperature", "power", "memory")
    }
    app.last_history_timestamp = None
    app.last_process_list = []
    app.proc_search_query = ""
    app.current_theme = LIGHT_THEME
    app.tray_icon = None
    app.last_alert_time = {}
    app.gpu_data_cache = {
        "gpu_output": "current",
        "proc_output": "",
        "timestamp": timestamp,
    }
    app._cache_lock = threading.Lock()
    app.check_alerts = lambda _gpus: None
    app.refresh_process_table = lambda _processes, force=False: None
    app.rebuild_gpu_cards = lambda: None
    return app


class UiStateRegressionTests(unittest.TestCase):
    def test_always_on_top_menu_check_tracks_config_before_and_after_clicks(
        self,
    ) -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display is unavailable")

        try:
            root.withdraw()
            app = GPUMonitorApp.__new__(GPUMonitorApp)
            app.root = root
            app.config = {"always_on_top": True}
            root.attributes("-topmost", True)
            with patch("ui.app.save_config"):
                app.setup_menu()
                menubar = root.nametowidget(root.cget("menu"))
                view_menu = root.nametowidget(menubar.entrycget("View", "menu"))
                variable_name = view_menu.entrycget("Always on Top", "variable")

                def checked() -> bool | None:
                    try:
                        return bool(int(root.getvar(variable_name)))
                    except tk.TclError:
                        return None

                observed = [checked()]
                view_menu.invoke("Always on Top")
                observed.append(checked())
                observed.append(app.config["always_on_top"])
                observed.append(bool(int(root.attributes("-topmost"))))
                view_menu.invoke("Always on Top")
                observed.append(checked())
                observed.append(app.config["always_on_top"])
                observed.append(bool(int(root.attributes("-topmost"))))

            self.assertEqual(observed, [True, False, False, False, True, True, True])
        finally:
            root.destroy()

    def test_worker_cache_publishes_and_reads_whole_snapshots(self) -> None:
        app = GPUMonitorApp.__new__(GPUMonitorApp)
        app._cache_lock = threading.Lock()
        app.gpu_data_cache = {}

        app._publish_gpu_data_snapshot(
            {"gpu_output": "GPU A", "proc_output": "process A"}
        )
        first = app._read_gpu_data_snapshot()
        app._publish_gpu_data_snapshot(
            {"gpu_output": "GPU B", "proc_output": "process B"}
        )
        second = app._read_gpu_data_snapshot()

        self.assertEqual(
            (first["gpu_output"], first["proc_output"]), ("GPU A", "process A")
        )
        self.assertEqual(
            (second["gpu_output"], second["proc_output"]), ("GPU B", "process B")
        )

    def test_init_gui_places_content_in_a_scrollable_canvas(self) -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display is unavailable")

        try:
            root.overrideredirect(True)
            root.geometry("700x240+0+0")
            try:
                root.attributes("-alpha", 0.0)
            except tk.TclError:
                pass
            app = GPUMonitorApp.__new__(GPUMonitorApp)
            app.root = root
            app.current_theme = LIGHT_THEME
            app.labels = {}
            app.init_gui()
            root.update()

            self.assertIs(app.cards_parent_frame.master, app.content_canvas)
            first, last = app.content_canvas.yview()
            self.assertEqual(first, 0.0)
            self.assertLess(last, 1.0)
            app.content_canvas.yview_scroll(1, "pages")
            root.update_idletasks()
            self.assertGreater(app.content_canvas.yview()[0], first)
        finally:
            root.destroy()

    def test_window_geometry_round_trips_signed_positions(self) -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display is unavailable")

        app = None
        error = None
        config = dict(DEFAULT_CONFIG)
        config.update(
            window_width=600,
            window_height=500,
            window_x=-100,
            window_y=150,
        )
        try:
            root.overrideredirect(True)
            try:
                root.attributes("-alpha", 0.0)
            except tk.TclError:
                pass
            with (
                patch("ui.app.load_config", return_value=config),
                patch("ui.app.save_config"),
                patch("ui.app.setup_tray_icon", return_value=None),
                patch("ui.app.get_nvidia_smi_output", return_value=("Error: test", "")),
                patch("ui.app.get_full_output", return_value=""),
            ):
                try:
                    app = GPUMonitorApp(root)
                except tk.TclError as exc:
                    error = exc

                self.assertIsNone(
                    error, "saved negative offsets must form valid Tk geometry"
                )
                self.assertTrue(
                    root.geometry().endswith("-100+150"),
                    f"expected signed initial geometry, got {root.geometry()}",
                )
                self.assertNotIn("+-", root.geometry())
                root.geometry("600x500-80-60")
                app.on_closing()

            self.assertEqual(config["window_x"], -80)
            self.assertEqual(config["window_y"], -60)
            app.update_thread.join(timeout=1)
        finally:
            if app is None:
                root.destroy()

    def test_successful_query_clears_a_previous_worker_error(self) -> None:
        app = GPUMonitorApp.__new__(GPUMonitorApp)
        app.stop_update = False
        app.config = {"refresh_rate": 0}
        app.update_event = threading.Event()
        app.gpu_data_cache = {}
        app._cache_lock = threading.Lock()
        app.update_gui = lambda: None
        calls = 0

        def query() -> tuple[str, str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("temporary query failure")
            app.stop_update = True
            return "fresh GPU rows", "fresh process rows"

        with patch("ui.app.get_nvidia_smi_output", side_effect=query):
            app.start_background_update()
            app.update_thread.join(timeout=2)

        self.assertFalse(app.update_thread.is_alive())
        self.assertEqual(app.gpu_data_cache["gpu_output"], "fresh GPU rows")
        self.assertNotIn("error", app.gpu_data_cache)

    def test_detailed_output_is_fetched_on_the_first_query(self) -> None:
        app = GPUMonitorApp.__new__(GPUMonitorApp)
        app.stop_update = False
        app.config = {"refresh_rate": 0}
        app.update_event = threading.Event()
        app.gpu_data_cache = {}
        app._cache_lock = threading.Lock()
        app.update_gui = lambda: None

        def query() -> tuple[str, str]:
            app.stop_update = True
            return "GPU rows", "process rows"

        with (
            patch("ui.app.get_nvidia_smi_output", side_effect=query),
            patch(
                "ui.app.get_full_output", return_value="full nvidia-smi report"
            ) as full_query,
        ):
            app.start_background_update()
            app.update_thread.join(timeout=2)

        self.assertFalse(app.update_thread.is_alive())
        full_query.assert_called_once_with(timeout=5)
        self.assertEqual(app.gpu_data_cache["full_output"], "full nvidia-smi report")

    def test_detailed_output_query_error_is_exposed_in_output(self) -> None:
        app = GPUMonitorApp.__new__(GPUMonitorApp)
        app.stop_update = False
        app.config = {"refresh_rate": 0}
        app.update_event = threading.Event()
        app.gpu_data_cache = {}
        app._cache_lock = threading.Lock()
        app.update_gui = lambda: None

        def query() -> tuple[str, str]:
            app.stop_update = True
            return "GPU rows", "process rows"

        with (
            patch("ui.app.get_nvidia_smi_output", side_effect=query),
            patch(
                "ui.app.get_full_output",
                return_value="Error: driver rejected query",
            ),
        ):
            app.start_background_update()
            app.update_thread.join(timeout=2)

        self.assertFalse(app.update_thread.is_alive())
        self.assertIn(
            "driver rejected query", app.gpu_data_cache.get("full_output", "")
        )

    def test_update_gui_does_not_duplicate_a_history_timestamp(self) -> None:
        timestamp = datetime(2026, 10, 25, 12, 0, 0)
        gpu = make_gpu()
        app = make_ui_app(timestamp, gpu)

        with (
            patch("ui.app.parse_gpu_metrics", return_value=[gpu]),
            patch("ui.app.parse_processes", return_value=[]),
        ):
            app.update_gui()
            app.update_gui()

        self.assertEqual(list(app.history_data["time"]), ["12:00:00"])
        self.assertEqual(list(app.history_data["utilization"]), [23.0])

    def test_update_gui_preserves_unavailable_history_values_as_none(self) -> None:
        timestamp = datetime(2026, 10, 25, 12, 0, 0)
        gpu = make_gpu(
            utilization=None,
            mem_used=None,
            mem_total=None,
            temperature=None,
            power_draw=None,
        )
        app = make_ui_app(timestamp, gpu)

        with (
            patch("ui.app.parse_gpu_metrics", return_value=[gpu]),
            patch("ui.app.parse_processes", return_value=[]),
        ):
            app.update_gui()

        self.assertEqual(list(app.history_data["utilization"]), [None])
        self.assertEqual(list(app.history_data["temperature"]), [None])
        self.assertEqual(list(app.history_data["power"]), [None])
        self.assertEqual(list(app.history_data["memory"]), [None])

    def test_current_gpu_identity_refreshes_when_name_changes(self) -> None:
        timestamp = datetime(2026, 10, 25, 12, 0, 0)
        gpu = make_gpu(name="Renamed GPU")
        app = make_ui_app(timestamp, make_gpu(name="Old GPU"))
        rebuilds: list[bool] = []
        app.rebuild_gpu_cards = lambda: rebuilds.append(True)

        with (
            patch("ui.app.parse_gpu_metrics", return_value=[gpu]),
            patch("ui.app.parse_processes", return_value=[]),
        ):
            app.update_gui()

        self.assertEqual(app.current_gpus[0]["name"], "Renamed GPU")
        self.assertEqual(rebuilds, [True])

    def test_empty_gpu_query_reports_unavailable_without_recording_history(
        self,
    ) -> None:
        timestamp = datetime(2026, 10, 25, 12, 0, 0)
        app = make_ui_app(timestamp, make_gpu())
        app.gpu_data_cache["gpu_output"] = "No devices were found."
        app.gpu_cards_container = FakeGpuContainer([FakeWidget()])
        app.create_gpu_metric_card = lambda *_args, **_kwargs: {"frame": FakeWidget()}
        del app.rebuild_gpu_cards
        process_refreshes: list[list[dict[str, str]]] = []
        app.refresh_process_table = (
            lambda processes, force=False: process_refreshes.append(processes)
        )

        app.update_gui()

        self.assertEqual(
            app.labels["gpu"].options.get("text"), "No NVIDIA GPUs detected"
        )
        self.assertIn(
            "No NVIDIA GPUs detected", app.labels["status"].options.get("text", "")
        )
        self.assertEqual(list(app.history_data["time"]), [])
        self.assertEqual(app.gpu_cards, {})
        self.assertEqual(app.current_gpus, [])
        self.assertEqual(len(process_refreshes), 1)

    def test_rebuild_gpu_cards_does_not_create_a_placeholder_when_no_gpu_exists(
        self,
    ) -> None:
        app = GPUMonitorApp.__new__(GPUMonitorApp)
        app.current_gpus = []
        stale_metric = FakeWidget()
        app.labels = {"util": stale_metric}
        app.gpu_cards = {0: {"util": stale_metric}}
        app.gpu_cards_container = FakeGpuContainer([FakeWidget()])
        app.create_gpu_metric_card = lambda *_args, **_kwargs: {"frame": FakeWidget()}
        app.create_combined_summary_card = lambda _parent: {}

        app.rebuild_gpu_cards()

        self.assertEqual(app.gpu_cards, {})
        self.assertNotIn("util", app.labels)
        self.assertEqual(app.gpu_cards_container.winfo_children(), ())

    def test_process_refresh_preserves_selected_process_and_sort_order(self) -> None:
        app = GPUMonitorApp.__new__(GPUMonitorApp)
        tree = FakeTreeview()
        app.labels = {"proc_table": tree}
        app.proc_search_query = ""
        app.current_theme = LIGHT_THEME
        app.sort_column = "Name"
        app.sort_reverse = False
        selected = tree.insert(
            "", "end", values=("0", "101", "beta", "10 MiB"), tags=("even",)
        )
        tree.selection_set(selected)

        app.refresh_process_table(
            [
                {"gpu": "0", "pid": "202", "name": "alpha", "mem": "4 MiB"},
                {"gpu": "0", "pid": "101", "name": "beta", "mem": "11 MiB"},
            ]
        )

        rows = [tree.item(item, "values") for item in tree.get_children()]
        self.assertEqual([row[2] for row in rows], ["alpha", "beta"])
        self.assertEqual(len(tree.selection()), 1)
        self.assertEqual(
            tree.item(tree.selection()[0], "values")[:3], ("0", "101", "beta")
        )

    def test_refresh_clears_hovered_row_before_deleting_it(self) -> None:
        try:
            import tkinter as tk
            from tkinter import ttk

            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display is unavailable")

        try:
            root.overrideredirect(True)
            root.geometry("300x100+0+0")
            try:
                root.attributes("-alpha", 0.0)
            except tk.TclError:
                pass
            tree = ttk.Treeview(root, columns=("GPU", "PID", "Name", "Memory"))
            tree.pack()
            app = GPUMonitorApp.__new__(GPUMonitorApp)
            app.labels = {"proc_table": tree}
            app.proc_search_query = ""
            app.current_theme = LIGHT_THEME
            app.sort_column = None
            app.sort_reverse = False
            old_row = tree.insert(
                "", "end", values=("0", "101", "beta", "10 MiB"), tags=("even", "hover")
            )
            app.hover_row = old_row

            app.refresh_process_table(
                [{"gpu": "0", "pid": "101", "name": "beta", "mem": "11 MiB"}]
            )

            self.assertIsNone(app.hover_row)
            self.assertNotIn(old_row, tree.get_children())
            root.update()
            row_id = tree.get_children()[0]
            _x, y, _width, height = tree.bbox(row_id, "#1")
            app._on_process_tree_motion(types.SimpleNamespace(y=y + height // 2))
            self.assertIn("hover", tree.item(row_id, "tags"))
        finally:
            root.destroy()


class TrayRegressionTests(unittest.TestCase):
    def test_tray_callbacks_enqueue_requests_without_touching_tk(self) -> None:
        class MenuItem:
            def __init__(
                self, label: str, action: object, default: bool = False
            ) -> None:
                self.label = label
                self.action = action
                self.default = default

        class Menu:
            def __init__(self, *items: MenuItem) -> None:
                self.items = items

        class Icon:
            instance = None

            def __init__(self, *_args: object) -> None:
                self.stopped = False
                self.menu = _args[3]
                Icon.instance = self

            def run(self) -> None:
                return

            def stop(self) -> None:
                self.stopped = True

        fake_pystray = types.SimpleNamespace(Menu=Menu, MenuItem=MenuItem, Icon=Icon)
        events: queue.Queue[str] = queue.Queue()

        class App:
            config = {"dark_mode": False}
            tray_event_queue = events

            @property
            def root(self):
                raise AssertionError("tray callbacks must not touch Tk")

            def on_closing(self) -> None:
                raise AssertionError("tray callbacks must enqueue quit")

        with (
            patch("ui.tray.PYSTRAY_AVAILABLE", True),
            patch("ui.tray.pystray", fake_pystray),
            patch("ui.tray.create_tray_icon_image", return_value=object()),
        ):
            tray.setup_tray_icon(App())

        icon = Icon.instance
        actions = {item.label: item.action for item in icon.menu.items}
        actions["Show"](icon, None)
        actions["Quit"](icon, None)

        self.assertEqual([events.get_nowait(), events.get_nowait()], ["show", "quit"])
        self.assertTrue(icon.stopped)

    def test_unavailable_tray_backend_does_not_break_app_startup(self) -> None:
        class MenuItem:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                return

        class Menu:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                return

        class Icon:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                raise ValueError("no supported tray backend")

        fake_pystray = types.SimpleNamespace(Menu=Menu, MenuItem=MenuItem, Icon=Icon)
        app = types.SimpleNamespace(
            config={"dark_mode": False}, tray_event_queue=queue.Queue()
        )
        with (
            patch("ui.tray.PYSTRAY_AVAILABLE", True),
            patch("ui.tray.pystray", fake_pystray),
            patch("ui.tray.create_tray_icon_image", return_value=object()),
        ):
            try:
                result = tray.setup_tray_icon(app)
            except ValueError:
                result = "backend error escaped"

        self.assertIsNone(result)


class GraphLifecycleTests(unittest.TestCase):
    def test_closing_graph_cancels_pending_refresh_callback(self) -> None:
        class FakeAxis:
            spines: dict[str, object] = {}

            def clear(self) -> None:
                return

            def set_facecolor(self, _color: str) -> None:
                return

            def grid(self, *_args: object, **_kwargs: object) -> None:
                return

            def plot(self, *_args: object, **_kwargs: object) -> None:
                return

            def set_title(self, *_args: object, **_kwargs: object) -> None:
                return

            def set_ylabel(self, *_args: object, **_kwargs: object) -> None:
                return

            def tick_params(self, *_args: object, **_kwargs: object) -> None:
                return

        class Spine:
            def set_edgecolor(self, _color: str) -> None:
                return

        class FakeFigure:
            def __init__(self, **_kwargs: object) -> None:
                self.patch = types.SimpleNamespace(set_facecolor=lambda _color: None)

            def add_subplot(self, *_args: object) -> FakeAxis:
                axis = FakeAxis()
                axis.spines = {"top": Spine()}
                return axis

            def tight_layout(self) -> None:
                return

        class FakeTkWidget:
            def pack(self, **_kwargs: object) -> None:
                return

        class FakeCanvas:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                return

            def draw(self) -> None:
                return

            def get_tk_widget(self) -> FakeTkWidget:
                return FakeTkWidget()

        class FakeWindow:
            def __init__(self, _parent: object) -> None:
                self.exists = True
                self.protocols: dict[str, object] = {}

            def title(self, _title: str) -> None:
                return

            def geometry(self, _geometry: str) -> None:
                return

            def protocol(self, name: str, callback: object) -> None:
                self.protocols[name] = callback

            def destroy(self) -> None:
                self.exists = False

            def lift(self) -> None:
                return

            def winfo_exists(self) -> bool:
                return self.exists

        parent = types.SimpleNamespace(
            root=FakeRoot(),
            config={"dark_mode": False},
            history_data={
                key: deque()
                for key in ("time", "utilization", "temperature", "power", "memory")
            },
        )
        graph = graphs.HistoryGraphWindow(parent)

        with (
            patch("ui.graphs.MATPLOTLIB_AVAILABLE", True),
            patch("ui.graphs.Figure", FakeFigure),
            patch("ui.graphs.FigureCanvasTkAgg", FakeCanvas),
            patch("ui.graphs.tk.Toplevel", FakeWindow),
        ):
            graph.show()
            callback_id = "after#1"
            close = graph.window.protocols["WM_DELETE_WINDOW"]
            close()

        self.assertIsNone(graph.window)
        self.assertEqual(parent.root.cancelled_after, [callback_id])


class OptionalBackendTests(unittest.TestCase):
    def test_matplotlib_backend_failure_disables_optional_graphs(self) -> None:
        fake_matplotlib = types.ModuleType("matplotlib")

        def fail_backend(_backend: str) -> None:
            raise RuntimeError("Tk backend is unavailable")

        fake_matplotlib.use = fail_backend
        escaped_error: Exception | None = None
        try:
            with patch.dict(sys.modules, {"matplotlib": fake_matplotlib}):
                try:
                    importlib.reload(graphs)
                except RuntimeError as exc:
                    escaped_error = exc
                backend_available = graphs.MATPLOTLIB_AVAILABLE
        finally:
            importlib.reload(graphs)

        self.assertFalse(backend_available)
        self.assertIsNone(escaped_error)


if __name__ == "__main__":
    unittest.main()
