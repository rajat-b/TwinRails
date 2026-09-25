"""
TwinRails taskbar widget
Main application entry point for Windows Taskbar & System Tray monitoring.
Supports Claude, Google Antigravity / Gemini, and future providers.
"""

import sys
import os
import argparse
import ctypes
import threading
import time
import queue
from ctypes import wintypes
from datetime import datetime, timezone
import tkinter as tk
from PIL import ImageTk

from src.core import ConfigManager, WidgetConfig
from src.core.models import UnifiedUsageState, ClaudeUsageState
from src.providers import ProviderManager
from src.ui import (
    TrayWidget, FlyoutWidget, TaskbarBarWidget, SettingsWindow,
    enable_per_monitor_dpi_v2, render_tray_icon
)

# Enable Per-Monitor V2 DPI awareness for razor-sharp rendering
enable_per_monitor_dpi_v2()

# TwinRails' own taskbar identity. Without it Windows files the Settings
# window under pythonw.exe and shows Python's icon on its taskbar button,
# whatever icon Tk is given. The tray icon is unaffected: its pinned/unpinned
# setting is keyed on (executable path, uID), not on this -- see
# docs/tray-icon.md.
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TwinRails.Widget")
except (AttributeError, OSError):
    pass


def workstation_locked() -> bool:
    """Read this process's Windows session lock flag; allow polling if unknown.

    WTS_SESSIONSTATE_LOCK is 0 on supported Windows 10/11 builds. The older
    Windows 7 reversal does not apply to this widget's supported versions.
    """
    class SessionInfoLevel1Prefix(ctypes.Structure):
        _fields_ = [
            ("session_id", wintypes.DWORD),
            ("connection_state", wintypes.DWORD),
            ("session_flags", wintypes.DWORD),
        ]

    class SessionInfoUnion(ctypes.Union):
        # The real union contains 64-bit fields; matching its alignment keeps
        # the level-one prefix at the right offset on 64-bit Windows.
        _fields_ = [
            ("level1", SessionInfoLevel1Prefix),
            ("alignment", ctypes.c_uint64),
        ]

    class SessionInfoEx(ctypes.Structure):
        _fields_ = [
            ("level", wintypes.DWORD),
            ("data", SessionInfoUnion),
        ]

    try:
        wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
        wts.WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)
        ]
        wts.WTSQuerySessionInformationW.restype = wintypes.BOOL
        wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        buffer = ctypes.c_void_p()
        size = wintypes.DWORD()
        if not wts.WTSQuerySessionInformationW(
            None, 0xFFFFFFFF, 25, ctypes.byref(buffer), ctypes.byref(size)
        ):
            return False
        try:
            if size.value < ctypes.sizeof(SessionInfoEx):
                return False
            info = ctypes.cast(buffer, ctypes.POINTER(SessionInfoEx)).contents
            return info.level == 1 and info.data.level1.session_flags == 0
        finally:
            wts.WTSFreeMemory(buffer)
    except (OSError, AttributeError):
        return False


class AITaskbarApp:
    def __init__(self, force_demo: bool = False, force_bar: bool = False):
        self.root = tk.Tk()
        self.root.withdraw()
        # The TwinRails mark instead of Tk's feather, as the default for every
        # window that has a title bar (today only Settings). Two sizes so
        # Windows can pick a sharp one for the title bar and the taskbar.
        self._window_icons = [
            ImageTk.PhotoImage(render_tray_icon(size), master=self.root) for size in (64, 32)
        ]
        self.root.iconphoto(True, *self._window_icons)

        self.cfg_mgr = ConfigManager()
        if force_demo:
            self.cfg_mgr.config.demo_mode = True
        if force_bar:
            self.cfg_mgr.config.show_docked_bar = True

        self.dispatch_queue = queue.Queue()
        self._process_dispatch_queue()

        self.provider_mgr = ProviderManager(self.cfg_mgr.config)
        self.current_state: UnifiedUsageState = UnifiedUsageState(
            is_loading=True,
            is_demo=self.cfg_mgr.config.demo_mode
        )

        self.is_fetching = False
        self._last_fetch_time = 0.0
        self._consecutive_errors = 0

        # UI Components
        self.flyout = FlyoutWidget(
            self.root,
            on_refresh=self.refresh_usage,
            on_open_settings=self.open_settings,
            on_toggle_bar=self.toggle_taskbar_bar,
            config_manager=self.cfg_mgr
        )

        self.taskbar_bar = TaskbarBarWidget(
            self.root,
            self.cfg_mgr,
            on_open_flyout=self.open_flyout_at,
            on_close_flyout=self.flyout.hide,
            on_refresh=self.refresh_usage,
            on_open_settings=self.open_settings,
            is_flyout_active=lambda: self.flyout.is_visible,
            schedule_flyout_hide=self.flyout.schedule_auto_hide,
            cancel_flyout_hide=self.flyout.cancel_auto_hide,
            get_refresh_progress=self.refresh_progress
        )

        # pystray runs its menu callbacks on its own background thread, not
        # the Tk thread -- Tkinter widgets aren't safe to touch off-thread.
        # handle_tray_click already learned this the hard way (it dispatches
        # via root.after); toggle_taskbar_bar, open_settings, and exit_app
        # create/move actual Tk windows, so they need the same treatment, or
        # a right-click during a fetch can freeze the app or throw
        # "main thread is not in main loop". on_refresh and on_toggle_demo
        # only touch plain config state and spawn a worker thread, so they're
        # safe as-is, but routing them through root.after too costs nothing
        # and keeps this list uniform if that ever changes.
        self.tray = TrayWidget(
            self.cfg_mgr,
            on_left_click=self.handle_tray_click,
            on_toggle_bar=lambda: self.root.after(0, self.toggle_taskbar_bar),
            on_refresh=lambda: self.root.after(0, self.refresh_usage),
            on_open_settings=lambda: self.root.after(0, self.open_settings),
            on_toggle_demo=lambda: self.root.after(0, self.toggle_demo_mode),
            on_exit=lambda: self.root.after(0, self.exit_app)
        )

        self.settings_win = None

    def start(self):
        self.tray.start(self.current_state)

        if self.cfg_mgr.config.show_docked_bar:
            self.taskbar_bar.show()

        self.tick()

        self.root.after(1500, self.tray.maybe_show_first_run_hint)
        self.root.mainloop()

    def handle_tray_click(self):
        self.root.after(0, self.flyout.toggle)

    def open_flyout_at(self, bx: int, by: int, bw: int, bh: int):
        self.root.after(0, lambda: self.flyout.show(anchor_x=bx, anchor_y=by, anchor_w=bw, anchor_h=bh))

    def toggle_taskbar_bar(self):
        cur = self.cfg_mgr.config.show_docked_bar
        new_val = not cur
        self.cfg_mgr.config.show_docked_bar = new_val
        self.cfg_mgr.save()
        if new_val:
            self.taskbar_bar.show()
        else:
            self.taskbar_bar.hide()

    def toggle_demo_mode(self):
        self.cfg_mgr.config.demo_mode = not self.cfg_mgr.config.demo_mode
        self.cfg_mgr.save()
        self.refresh_usage()

    def open_settings(self):
        if self.settings_win and self.settings_win.window.winfo_exists():
            self.settings_win.window.lift()
            self.settings_win.window.focus_force()
            return

        self.settings_win = SettingsWindow(
            self.root,
            self.cfg_mgr,
            on_save_callback=self.on_settings_saved,
            current_state=self.current_state
        )

    def on_settings_saved(self, new_cfg: WidgetConfig):
        self.provider_mgr.update_config(new_cfg)
        self.taskbar_bar.refresh_theme()
        if self.current_state is not None:
            self.flyout.update_state(self.current_state)
        if new_cfg.show_docked_bar:
            self.taskbar_bar.show()
        else:
            self.taskbar_bar.hide()
        self.refresh_usage()

    def refresh_usage(self):
        if self.is_fetching:
            return
        self.is_fetching = True
        # Dims the bar immediately on click rather than waiting for the next
        # tick() (up to 1s away) or for the fetch itself to finish -- the
        # whole point is instant feedback that the click registered.
        self.taskbar_bar.update_state(self.current_state, is_refreshing=True)

        def _worker():
            try:
                state = self.provider_mgr.fetch_all(demo_mode=self.cfg_mgr.config.demo_mode)
                self.dispatch_queue.put(("apply_state", state))
            except Exception as e:
                # fetch_all() already catches per-provider failures internally
                # (an offline Antigravity or a bad Claude key becomes an
                # error_message on that provider's own ProviderUsageState, not
                # an exception here) -- this only catches something breaking
                # fetch_all() itself. That used to build a blank
                # UnifiedUsageState with an empty `providers` dict and no
                # error_message field to put anything in, silently wiping
                # every card/bar the user was looking at with nothing to
                # explain why, and the exception itself went to a bare
                # `except ... : pass`-style discard. Print it so it's visible
                # under pythonw's captured output, and keep showing the last
                # known-good providers instead of blanking the display.
                print(f"[AITaskbarApp] refresh_usage failed: {e}")
                err_state = UnifiedUsageState(
                    providers=self.current_state.providers,
                    is_loading=False,
                    is_demo=self.cfg_mgr.config.demo_mode
                )
                self.dispatch_queue.put(("apply_state", err_state))
            finally:
                self.is_fetching = False
                self._last_fetch_time = time.time()

        threading.Thread(target=_worker, daemon=True).start()

    def poll_interval(self) -> int:
        """The effective interval, including the floor and error backoff.

        The taskbar countdown uses this same interval, so it predicts the
        actual next automatic fetch even after a failed request.
        """
        base = max(60, self.cfg_mgr.config.poll_interval_seconds)
        return min(3600, base * (2 ** min(self._consecutive_errors, 4)))

    def refresh_progress(self) -> tuple:
        """(fraction of the interval elapsed, whether a fetch is running)
        for the taskbar bar's countdown outline.

        Before the first fetch ever completes, _last_fetch_time is still 0.0
        and the elapsed fraction would come out as decades. Clamping to 1.0
        makes that read as "due now", which is exactly what it is."""
        elapsed = (time.time() - self._last_fetch_time) / self.poll_interval()
        return (max(0.0, min(1.0, elapsed)), self.is_fetching)

    def apply_state(self, state: UnifiedUsageState):
        self.current_state = state
        provider_states = tuple(state.providers.values())
        # Back off only when the whole polling round failed. One optional
        # provider being unavailable (most commonly Antigravity while its IDE
        # is closed) must not slow every working provider or make the shared
        # countdown appear frozen.
        whole_round_failed = (
            bool(provider_states)
            and all(bool(provider.error_message) for provider in provider_states)
        )
        if state.is_demo or not whole_round_failed:
            self._consecutive_errors = 0
        else:
            self._consecutive_errors = min(self._consecutive_errors + 1, 4)
        self.tray.update_state(state)
        self.flyout.update_state(state)
        self.taskbar_bar.update_state(state, is_refreshing=False)

    def _process_dispatch_queue(self):
        try:
            while True:
                msg_type, payload = self.dispatch_queue.get_nowait()
                if msg_type == "apply_state":
                    self.apply_state(payload)
                self.dispatch_queue.task_done()
        except queue.Empty:
            pass
        self.root.after(100, self._process_dispatch_queue)

    def tick(self):
        try:
            now_ts = time.time()

            # The tray tooltip also shows live usage when the taskbar bar is
            # hidden. Keep fetching in tray-only mode; only a locked Windows
            # session pauses automatic requests.
            if (
                (now_ts - self._last_fetch_time) >= self.poll_interval()
                and not workstation_locked()
            ):
                self.refresh_usage()

            # Update visual countdowns. When the bar is configured as shown,
            # redraw even during a transient Tk visibility mismatch caused by
            # Explorer reparenting.
            if self.taskbar_bar and self.cfg_mgr.config.show_docked_bar:
                self.taskbar_bar.update_state(
                    self.current_state, is_refreshing=self.is_fetching
                )
            if self.flyout and self.flyout.is_visible:
                self.flyout.update_state(self.current_state)
        except Exception as exc:
            # A Tk callback that raises before scheduling its next `after`
            # call otherwise kills the timer permanently.
            print(f"[AITaskbarApp] tick failed: {exc}")
        finally:
            self.root.after(1000, self.tick)

    def exit_app(self):
        self.tray.stop()
        self.root.quit()
        sys.exit(0)


# Backward compatibility alias
ClaudeTaskbarApp = AITaskbarApp


def main():
    parser = argparse.ArgumentParser(description="TwinRails taskbar widget")
    parser.add_argument("--demo", action="store_true", help="Launch in demo mode with sample limits")
    parser.add_argument("--bar", action="store_true", help="Ensure docked taskbar bar is visible on launch")
    args = parser.parse_args()

    app = AITaskbarApp(force_demo=args.demo, force_bar=args.bar)
    app.start()


if __name__ == "__main__":
    main()
