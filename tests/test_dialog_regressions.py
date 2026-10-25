"""Regression tests for the asynchronous power-limit dialog."""

from __future__ import annotations

import os
import subprocess
import threading
import time
import tkinter as actual_tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ui.dialogs as dialogs
import nvidia_smi


MAIN_THREAD_ID = threading.get_ident()


class FakeVar:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeWidget:
    created = []

    def __init__(self, master=None, **kwargs):
        self._check_main_thread()
        self.master = master
        self.options = dict(kwargs)
        self.text = kwargs.get("text")
        self.textvariable = kwargs.get("textvariable")
        self.state = kwargs.get("state", "normal")
        self.command = kwargs.get("command")
        self.bindings = {}
        self.values = kwargs.get("values", ())
        self.selected = -1
        self.value = ""
        self.exists = True
        self.after_callbacks = []
        self.after_counter = 0
        self.protocols = {}
        self.destroy_count = 0
        self.viewable = False
        self.viewable_error = False
        self.grab_error = False
        self.grab_count = 0
        self.after_idle_callbacks = []
        FakeWidget.created.append(self)

    @staticmethod
    def _check_main_thread():
        if threading.get_ident() != MAIN_THREAD_ID:
            raise AssertionError("Tk widget accessed outside the main thread")

    def pack(self, **_kwargs):
        self._check_main_thread()

    def config(self, **kwargs):
        self._check_main_thread()
        self.options.update(kwargs)
        if "state" in kwargs:
            self.state = kwargs["state"]
        if "text" in kwargs:
            self.text = kwargs["text"]

    configure = config

    def bind(self, event, callback, add=None):
        self._check_main_thread()
        if add and event in self.bindings:
            existing = self.bindings[event]
            added_callback = callback

            def chained_callback(*args):
                existing(*args)
                added_callback(*args)

            callback = chained_callback
        self.bindings[event] = callback

    def current(self, index=None):
        self._check_main_thread()
        if index is not None:
            self.selected = index
        return self.selected

    def get(self):
        self._check_main_thread()
        return self.value

    def insert(self, _index, value):
        self._check_main_thread()
        self.value = value

    def focus(self):
        self._check_main_thread()

    def title(self, _text):
        self._check_main_thread()

    def resizable(self, *_args):
        self._check_main_thread()

    def grab_set(self):
        self._check_main_thread()
        self.grab_count += 1
        if self.grab_error:
            raise RuntimeError("window was destroyed before grab")

    def grab_release(self):
        self._check_main_thread()

    def lift(self):
        self._check_main_thread()

    def protocol(self, name, callback):
        self._check_main_thread()
        self.protocols[name] = callback

    def winfo_exists(self):
        self._check_main_thread()
        return self.exists

    def winfo_viewable(self):
        self._check_main_thread()
        if self.viewable_error:
            raise RuntimeError("window was destroyed before visibility check")
        return self.exists and self.viewable

    def after(self, _delay_ms, callback):
        self._check_main_thread()
        self.after_counter += 1
        callback_id = f"after-{self.after_counter}"
        self.after_callbacks.append((callback_id, callback))
        return callback_id

    def after_cancel(self, callback_id):
        self._check_main_thread()
        self.after_callbacks = [
            item for item in self.after_callbacks if item[0] != callback_id
        ]

    def after_idle(self, callback):
        self._check_main_thread()
        self.after_counter += 1
        callback_id = f"idle-{self.after_counter}"
        self.after_idle_callbacks.append((callback_id, callback))
        return callback_id

    def run_one_idle(self):
        self._check_main_thread()
        if self.after_idle_callbacks:
            _callback_id, callback = self.after_idle_callbacks.pop(0)
            callback()

    def map(self, *, viewable=True):
        self._check_main_thread()
        self.viewable = viewable
        callback = self.bindings.get("<Map>")
        if callback is not None:
            callback(SimpleNamespace(widget=self))

    def run_one_after(self):
        self._check_main_thread()
        if self.after_callbacks:
            _callback_id, callback = self.after_callbacks.pop(0)
            callback()

    def destroy(self):
        self._check_main_thread()
        self.exists = False
        self.destroy_count += 1


class FakeEntry(FakeWidget):
    pass


class FakeButton(FakeWidget):
    def invoke(self):
        self._check_main_thread()
        self.command()


class FakeCombobox(FakeWidget):
    pass


class FakeToplevel(FakeWidget):
    pass


class PowerDialogRegressionTests(unittest.TestCase):
    def setUp(self):
        FakeWidget.created = []
        if hasattr(dialogs, "power_win"):
            dialogs.power_win = None
        self.after_patchers = []
        self.fake_tk = SimpleNamespace(
            Toplevel=FakeToplevel,
            StringVar=FakeVar,
            BooleanVar=FakeVar,
            TclError=RuntimeError,
        )
        self.fake_ttk = SimpleNamespace(
            Frame=FakeWidget,
            Label=FakeWidget,
            Entry=FakeEntry,
            Button=FakeButton,
            Combobox=FakeCombobox,
            Checkbutton=FakeWidget,
        )
        self.tk_patcher = patch.object(dialogs, "tk", self.fake_tk)
        self.ttk_patcher = patch.object(dialogs, "ttk", self.fake_ttk)
        self.tk_patcher.start()
        self.ttk_patcher.start()
        self.addCleanup(self.tk_patcher.stop)
        self.addCleanup(self.ttk_patcher.stop)
        self.message_errors = []
        self.message_infos = []
        self.messagebox_patcher = patch.object(
            dialogs,
            "messagebox",
            SimpleNamespace(
                showerror=lambda *args: self.message_errors.append(args),
                showinfo=lambda *args: self.message_infos.append(args),
            ),
        )
        self.messagebox_patcher.start()
        self.addCleanup(self.messagebox_patcher.stop)

    @staticmethod
    def make_app(gpus):
        return SimpleNamespace(
            root=object(),
            config={"dark_mode": False},
            current_gpus=gpus,
        )

    @staticmethod
    def find_widget(widget_type, **attributes):
        for widget in FakeWidget.created:
            if not isinstance(widget, widget_type):
                continue
            if all(
                getattr(widget, key, None) == value for key, value in attributes.items()
            ):
                return widget
        raise AssertionError(f"Widget not found: {widget_type.__name__} {attributes}")

    @staticmethod
    def find_message_var():
        for widget in FakeWidget.created:
            if isinstance(widget, FakeWidget) and widget.textvariable is not None:
                if isinstance(widget.textvariable, FakeVar):
                    return widget.textvariable
        raise AssertionError("Message variable not found")

    @staticmethod
    def pump_until(window, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            window.run_one_after()
            if predicate():
                return
            time.sleep(0.002)
        raise AssertionError("Timed out waiting for dialog callback")

    def test_limits_are_bound_to_sparse_gpu_selection_and_request_generation(self):
        release = {4: threading.Event(), 9: threading.Event()}
        requests = []
        apply_calls = []

        def get_limits(gpu_index):
            requests.append((gpu_index, threading.get_ident()))
            release[gpu_index].wait(1)
            return (100.0, 40.0, 180.0) if gpu_index == 4 else (200.0, 80.0, 300.0)

        def set_limit(value, **kwargs):
            apply_calls.append((value, kwargs, threading.get_ident()))
            return False, "test backend diagnostic"

        with (
            patch.object(dialogs, "get_power_limits", side_effect=get_limits),
            patch.object(dialogs, "set_power_limit", side_effect=set_limit),
        ):
            dialogs.open_power_limit_window(
                self.make_app(
                    [
                        {"index": 4, "name": "First GPU"},
                        {"index": 9, "name": "Second GPU"},
                    ]
                ),
                initial_gpu_index=4,
            )
            window = FakeWidget.created[0]
            combo = self.find_widget(FakeCombobox)
            entry = self.find_widget(FakeEntry)
            apply_button = self.find_widget(FakeButton, text="Apply Changes")

            self.assertEqual(combo.current(), 0)
            self.assertEqual(entry.state, "disabled")
            self.assertEqual(apply_button.state, "disabled")
            entry.value = "120"
            entry.bindings["<Return>"](None)
            self.assertEqual(apply_calls, [])

            combo.current(1)
            combo.bindings["<<ComboboxSelected>>"](None)
            self.assertEqual(entry.state, "disabled")
            self.assertEqual(apply_button.state, "disabled")
            release[9].set()
            self.pump_until(window, lambda: entry.state == "normal")

            self.assertEqual(requests[0][0], 4)
            self.assertEqual(requests[1][0], 9)
            self.assertNotEqual(requests[0][1], MAIN_THREAD_ID)
            self.assertNotEqual(requests[1][1], MAIN_THREAD_ID)
            self.assertIn(
                "80.0 - 300.0 W",
                " ".join(str(widget.text) for widget in FakeWidget.created),
            )

            entry.value = "250"
            apply_button.invoke()
            self.pump_until(
                window,
                lambda: "test backend diagnostic" in self.find_message_var().get(),
            )
            self.assertEqual(apply_calls[0][1]["gpu_index"], 9)
            self.assertNotEqual(apply_calls[0][2], MAIN_THREAD_ID)

            release[4].set()
            # Let the stale GPU-4 completion run after GPU 9 is already selected.
            self.pump_until(window, lambda: len(requests) == 2)
            time.sleep(0.01)
            for _ in range(4):
                window.run_one_after()
            self.assertIn(
                "80.0 - 300.0 W",
                " ".join(str(widget.text) for widget in FakeWidget.created),
            )

    def test_apply_stays_disabled_when_backend_returns_invalid_bounds(self):
        with patch.object(
            dialogs, "get_power_limits", return_value=(100.0, float("nan"), 300.0)
        ):
            dialogs.open_power_limit_window(
                self.make_app([{"index": 0, "name": "GPU"}])
            )
            window = FakeWidget.created[0]
            entry = self.find_widget(FakeEntry)
            apply_button = self.find_widget(FakeButton, text="Apply Changes")

            self.pump_until(window, lambda: bool(self.find_message_var().get()))

            self.assertEqual(entry.state, "disabled")
            self.assertEqual(apply_button.state, "disabled")
            self.assertIn("invalid", self.find_message_var().get().lower())

    def test_limit_query_exception_is_shown_and_does_not_keep_polling(self):
        with patch.object(
            dialogs, "get_power_limits", side_effect=RuntimeError("driver query failed")
        ):
            dialogs.open_power_limit_window(
                self.make_app([{"index": 0, "name": "GPU"}])
            )
            window = FakeWidget.created[0]
            apply_button = self.find_widget(FakeButton, text="Apply Changes")
            message = self.find_message_var()
            self.pump_until(window, lambda: "driver query failed" in message.get())

            self.assertEqual(apply_button.state, "disabled")
            self.assertEqual(window.after_callbacks, [])

    def test_captured_power_query_error_is_visible_in_dialog(self):
        error = subprocess.CalledProcessError(
            1,
            ["nvidia-smi"],
            output="Insufficient Permissions while querying GPU power limits.",
        )

        with (
            patch.object(nvidia_smi.subprocess, "check_output", side_effect=error),
            patch.object(nvidia_smi.time, "sleep"),
        ):
            dialogs.open_power_limit_window(
                self.make_app([{"index": 0, "name": "GPU"}])
            )
            window = FakeWidget.created[0]
            apply_button = self.find_widget(FakeButton, text="Apply Changes")
            message = self.find_message_var()
            self.pump_until(window, lambda: "Insufficient Permissions" in message.get())

            self.assertEqual(apply_button.state, "disabled")

    def test_nonfinite_or_out_of_range_values_and_duplicate_submit_are_rejected(self):
        entered = threading.Event()
        finish = threading.Event()
        calls = []

        def set_limit(value, **kwargs):
            calls.append((value, kwargs, threading.get_ident()))
            entered.set()
            finish.wait(1)
            return False, "driver rejected update: diagnostic details"

        with (
            patch.object(
                dialogs, "get_power_limits", return_value=(100.0, 50.0, 200.0)
            ),
            patch.object(dialogs, "set_power_limit", side_effect=set_limit),
        ):
            dialogs.open_power_limit_window(
                self.make_app([{"index": 0, "name": "GPU"}])
            )
            window = FakeWidget.created[0]
            entry = self.find_widget(FakeEntry)
            apply_button = self.find_widget(FakeButton, text="Apply Changes")
            message = self.find_message_var()
            self.pump_until(window, lambda: apply_button.state == "normal")

            for value in ("nan", "inf", "-inf", "49.9", "200.1", "not-a-number"):
                entry.value = value
                apply_button.invoke()
                self.assertEqual(calls, [], value)
            self.assertIn("finite", message.get().lower())

            entry.value = "150"
            apply_button.invoke()
            self.assertTrue(entered.wait(1))
            entry.bindings["<Return>"](None)
            apply_button.invoke()
            self.assertEqual(len(calls), 1)
            self.assertEqual(entry.state, "disabled")
            self.assertEqual(apply_button.state, "disabled")
            self.assertNotEqual(calls[0][2], MAIN_THREAD_ID)

            finish.set()
            self.pump_until(window, lambda: "diagnostic details" in message.get())
            self.assertIn("failed", message.get().lower())
            self.assertEqual(apply_button.state, "normal")

    def test_set_limit_exception_is_shown_and_controls_recover(self):
        with (
            patch.object(
                dialogs, "get_power_limits", return_value=(100.0, 50.0, 200.0)
            ),
            patch.object(
                dialogs, "set_power_limit", side_effect=OSError("driver command failed")
            ),
        ):
            dialogs.open_power_limit_window(
                self.make_app([{"index": 0, "name": "GPU"}])
            )
            window = FakeWidget.created[0]
            entry = self.find_widget(FakeEntry)
            apply_button = self.find_widget(FakeButton, text="Apply Changes")
            message = self.find_message_var()
            self.pump_until(window, lambda: apply_button.state == "normal")
            entry.value = "150"
            apply_button.invoke()
            self.pump_until(window, lambda: "driver command failed" in message.get())

            self.assertIn("failed", message.get().lower())
            self.assertEqual(entry.state, "normal")
            self.assertEqual(apply_button.state, "normal")
            self.assertEqual(window.after_callbacks, [])

    def test_closed_dialog_worker_cannot_update_a_reopened_dialog(self):
        started = threading.Event()
        release = threading.Event()
        call_count = 0

        def get_limits(gpu_index):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                started.set()
                release.wait(1)
                return (100.0, 50.0, 200.0)
            return (150.0, 75.0, 250.0)

        with patch.object(dialogs, "get_power_limits", side_effect=get_limits):
            app = self.make_app([{"index": 0, "name": "GPU"}])
            dialogs.open_power_limit_window(app)
            first_window = FakeWidget.created[0]
            self.assertTrue(started.wait(1))
            first_window.protocols["WM_DELETE_WINDOW"]()

            dialogs.open_power_limit_window(app)
            second_window = [
                w for w in FakeWidget.created if isinstance(w, FakeToplevel)
            ][1]
            second_entry = [w for w in FakeWidget.created if isinstance(w, FakeEntry)][
                1
            ]
            second_apply = [
                w
                for w in FakeWidget.created
                if isinstance(w, FakeButton) and w.text == "Apply Changes"
            ][1]
            self.pump_until(second_window, lambda: second_apply.state == "normal")
            second_range = next(
                widget
                for widget in FakeWidget.created
                if (getattr(widget, "text", "") or "").startswith("Allowed Range:")
            )

            release.set()
            time.sleep(0.02)
            first_window.run_one_after()
            self.assertIn("75.0 - 250.0 W", second_range.text)
            self.assertEqual(second_entry.state, "normal")
            self.assertEqual(second_apply.state, "normal")

    def test_modal_dialogs_wait_until_mapped_before_grabbing(self):
        cases = (
            (
                dialogs.open_power_limit_window,
                self.make_app([{"index": 0, "name": "GPU"}]),
            ),
            (
                dialogs.open_refresh_rate_dialog,
                SimpleNamespace(root=object(), config={"refresh_rate": 3000}),
            ),
            (
                dialogs.open_alert_settings_dialog,
                SimpleNamespace(
                    root=object(),
                    config={"alert_temp": 80, "alert_util": 90, "alert_enabled": True},
                ),
            ),
        )

        with patch.object(dialogs, "get_power_limits", return_value=(100, 50, 200)):
            for open_dialog, app in cases:
                with self.subTest(dialog=open_dialog.__name__):
                    FakeWidget.created = []
                    open_dialog(app)
                    window = next(
                        widget
                        for widget in FakeWidget.created
                        if isinstance(widget, FakeToplevel)
                    )

                    self.assertEqual(window.grab_count, 0)
                    self.assertIn("<Map>", window.bindings)
                    self.assertEqual(len(window.after_idle_callbacks), 1)

                    # A hidden parent can consume its one idle check without spinning.
                    window.run_one_idle()
                    self.assertEqual(window.grab_count, 0)
                    self.assertEqual(window.after_idle_callbacks, [])

                    window.map(viewable=False)
                    window.run_one_idle()
                    self.assertEqual(window.grab_count, 0)

                    window.map(viewable=True)
                    window.run_one_idle()
                    self.assertEqual(window.grab_count, 1)

                    window.destroy()

    def test_modal_grab_callback_tolerates_destroy_and_tcl_failures(self):
        for failure in ("destroyed", "visibility", "grab"):
            with self.subTest(failure=failure):
                window = FakeToplevel()
                dialogs._grab_when_viewable(window)

                if failure == "destroyed":
                    window.destroy()
                elif failure == "visibility":
                    window.viewable_error = True
                else:
                    window.grab_error = True

                window.map(viewable=True)
                window.run_one_idle()

                if failure == "grab":
                    self.assertEqual(window.grab_count, 1)
                else:
                    self.assertEqual(window.grab_count, 0)

    def test_success_message_includes_backend_diagnostic(self):
        with (
            patch.object(
                dialogs, "get_power_limits", return_value=(100.0, 50.0, 200.0)
            ),
            patch.object(
                dialogs,
                "set_power_limit",
                return_value=(True, "driver confirmed the update"),
            ) as set_limit,
        ):
            dialogs.open_power_limit_window(
                self.make_app([{"index": 0, "name": "GPU"}])
            )
            window = FakeWidget.created[0]
            entry = self.find_widget(FakeEntry)
            apply_button = self.find_widget(FakeButton, text="Apply Changes")
            message = self.find_message_var()
            self.pump_until(window, lambda: apply_button.state == "normal")
            entry.value = "150"
            apply_button.invoke()
            self.pump_until(window, lambda: "driver confirmed" in message.get())

            self.assertIn("successfully", message.get())
            self.assertEqual(set_limit.call_count, 1)

    def test_unconfirmed_elevation_result_is_not_reported_as_success(self):
        with (
            patch.object(
                dialogs, "get_power_limits", return_value=(100.0, 50.0, 200.0)
            ),
            patch.object(
                dialogs,
                "set_power_limit",
                return_value=(
                    True,
                    "Elevation prompt requested. Power limit update submitted.",
                ),
            ),
        ):
            dialogs.open_power_limit_window(
                self.make_app([{"index": 0, "name": "GPU"}])
            )
            window = FakeWidget.created[0]
            entry = self.find_widget(FakeEntry)
            apply_button = self.find_widget(FakeButton, text="Apply Changes")
            message = self.find_message_var()
            self.pump_until(window, lambda: apply_button.state == "normal")
            entry.value = "150"
            apply_button.invoke()
            self.pump_until(window, lambda: "not confirmed" in message.get())

            self.assertNotIn("successfully", message.get())
            self.assertEqual(entry.state, "disabled")
            self.assertEqual(apply_button.state, "disabled")

    def test_readback_failure_invalidates_bounds_and_disables_apply(self):
        with (
            patch.object(
                dialogs, "get_power_limits", return_value=(100.0, 50.0, 200.0)
            ),
            patch.object(
                dialogs,
                "set_power_limit",
                return_value=(
                    False,
                    "Elevated command exited successfully, but power-limit readback "
                    "failed: NVML readback failed: driver not loaded; command outcome "
                    "is unconfirmed.",
                ),
            ),
        ):
            dialogs.open_power_limit_window(
                self.make_app([{"index": 0, "name": "GPU"}])
            )
            window = FakeWidget.created[0]
            entry = self.find_widget(FakeEntry)
            apply_button = self.find_widget(FakeButton, text="Apply Changes")
            message = self.find_message_var()
            self.pump_until(window, lambda: apply_button.state == "normal")
            entry.value = "150"
            apply_button.invoke()
            self.pump_until(window, lambda: "unconfirmed" in message.get().lower())

            self.assertIn("NVML readback failed: driver not loaded", message.get())
            self.assertIn("command outcome is unconfirmed", message.get().lower())
            self.assertEqual(entry.state, "disabled")
            self.assertEqual(apply_button.state, "disabled")

    def test_elevation_cancellation_keeps_apply_available(self):
        with (
            patch.object(
                dialogs, "get_power_limits", return_value=(100.0, 50.0, 200.0)
            ),
            patch.object(
                dialogs,
                "set_power_limit",
                return_value=(False, "Administrator elevation was cancelled."),
            ),
        ):
            dialogs.open_power_limit_window(
                self.make_app([{"index": 0, "name": "GPU"}])
            )
            window = FakeWidget.created[0]
            entry = self.find_widget(FakeEntry)
            apply_button = self.find_widget(FakeButton, text="Apply Changes")
            message = self.find_message_var()
            self.pump_until(window, lambda: apply_button.state == "normal")
            entry.value = "150"
            apply_button.invoke()
            self.pump_until(window, lambda: "cancelled" in message.get().lower())

            self.assertEqual(entry.state, "normal")
            self.assertEqual(apply_button.state, "normal")

    def test_refresh_rate_rejects_nonfinite_and_out_of_range_values(self):
        app = SimpleNamespace(root=object(), config={"refresh_rate": 3000})
        with patch.object(dialogs, "save_config") as save:
            dialogs.open_refresh_rate_dialog(app)
            dialog = next(w for w in FakeWidget.created if isinstance(w, FakeToplevel))
            entry = self.find_widget(FakeEntry)
            apply_button = self.find_widget(FakeButton, text="Apply")

            for value in ("nan", "inf", "-inf", "0.49", "60.01", "bad"):
                entry.value = value
                apply_button.invoke()
                self.assertEqual(app.config["refresh_rate"], 3000, value)
                self.assertTrue(dialog.exists, value)
            save.assert_not_called()
            self.assertGreaterEqual(len(self.message_errors), 6)

    def test_refresh_rate_updates_only_after_successful_persistence(self):
        app = SimpleNamespace(root=object(), config={"refresh_rate": 3000})
        with patch.object(dialogs, "save_config", return_value=True) as save:
            dialogs.open_refresh_rate_dialog(app)
            dialog = next(w for w in FakeWidget.created if isinstance(w, FakeToplevel))
            entry = self.find_widget(FakeEntry)
            entry.value = "1.25"
            self.find_widget(FakeButton, text="Apply").invoke()

            self.assertEqual(app.config["refresh_rate"], 1250)
            self.assertFalse(dialog.exists)
            save.assert_called_once_with({"refresh_rate": 1250})

    def test_refresh_rate_save_failure_is_visible_and_does_not_change_config(self):
        app = SimpleNamespace(root=object(), config={"refresh_rate": 3000})
        with patch.object(dialogs, "save_config", return_value=False):
            dialogs.open_refresh_rate_dialog(app)
            dialog = next(w for w in FakeWidget.created if isinstance(w, FakeToplevel))
            self.find_widget(FakeEntry).value = "2.0"
            self.find_widget(FakeButton, text="Apply").invoke()

            self.assertEqual(app.config["refresh_rate"], 3000)
            self.assertTrue(dialog.exists)
            self.assertEqual(len(self.message_errors), 1)
            self.assertIn("save", self.message_errors[0][1].lower())

    def test_alert_thresholds_reject_values_outside_config_contract(self):
        cases = (
            ("-1", "90"),
            ("151", "90"),
            ("80", "-1"),
            ("80", "101"),
            ("nan", "90"),
        )
        for temp, util in cases:
            with self.subTest(temp=temp, util=util):
                FakeWidget.created = []
                self.message_errors.clear()
                app = SimpleNamespace(
                    root=object(),
                    config={"alert_temp": 80, "alert_util": 90, "alert_enabled": True},
                )
                with patch.object(dialogs, "save_config") as save:
                    dialogs.open_alert_settings_dialog(app)
                    dialog = next(
                        w for w in FakeWidget.created if isinstance(w, FakeToplevel)
                    )
                    entries = [
                        w for w in FakeWidget.created if isinstance(w, FakeEntry)
                    ]
                    entries[0].value = temp
                    entries[1].value = util
                    self.find_widget(FakeButton, text="Apply").invoke()

                    self.assertEqual(app.config["alert_temp"], 80)
                    self.assertEqual(app.config["alert_util"], 90)
                    self.assertTrue(dialog.exists)
                    save.assert_not_called()
                    self.assertEqual(len(self.message_errors), 1)

    def test_alert_settings_save_failure_is_visible_and_does_not_change_config(self):
        app = SimpleNamespace(
            root=object(),
            config={"alert_temp": 80, "alert_util": 90, "alert_enabled": True},
        )
        with patch.object(dialogs, "save_config", return_value=False):
            dialogs.open_alert_settings_dialog(app)
            dialog = next(w for w in FakeWidget.created if isinstance(w, FakeToplevel))
            entries = [w for w in FakeWidget.created if isinstance(w, FakeEntry)]
            entries[0].value = "100"
            entries[1].value = "75"
            self.find_widget(FakeButton, text="Apply").invoke()

            self.assertEqual(app.config["alert_temp"], 80)
            self.assertEqual(app.config["alert_util"], 90)
            self.assertTrue(dialog.exists)
            self.assertEqual(len(self.message_errors), 1)
            self.assertIn("save", self.message_errors[0][1].lower())

    def test_alert_settings_persist_valid_thresholds(self):
        app = SimpleNamespace(
            root=object(),
            config={"alert_temp": 80, "alert_util": 90, "alert_enabled": True},
        )
        with patch.object(dialogs, "save_config", return_value=True) as save:
            dialogs.open_alert_settings_dialog(app)
            dialog = next(w for w in FakeWidget.created if isinstance(w, FakeToplevel))
            entries = [w for w in FakeWidget.created if isinstance(w, FakeEntry)]
            entries[0].value = "120"
            entries[1].value = "75"
            self.find_widget(FakeButton, text="Apply").invoke()

            self.assertEqual(app.config["alert_temp"], 120)
            self.assertEqual(app.config["alert_util"], 75)
            self.assertFalse(dialog.exists)
            save.assert_called_once()


@unittest.skipUnless(
    os.name != "nt" and os.environ.get("DISPLAY"), "requires a real Tk display"
)
class TkMapGrabIntegrationTests(unittest.TestCase):
    def test_modal_grab_is_applied_after_the_dialog_maps(self):
        root = actual_tk.Tk()
        dialog = None
        try:
            root.withdraw()
            dialog = actual_tk.Toplevel(root)
            dialog.withdraw()
            dialogs._grab_when_viewable(dialog)

            root.update_idletasks()
            self.assertIsNone(dialog.grab_current())

            root.deiconify()
            dialog.deiconify()
            root.update()

            self.assertEqual(dialog.grab_status(), "local")
        finally:
            if dialog is not None:
                try:
                    dialog.grab_release()
                except actual_tk.TclError:
                    pass
            try:
                root.destroy()
            except actual_tk.TclError:
                pass


if __name__ == "__main__":
    unittest.main()
