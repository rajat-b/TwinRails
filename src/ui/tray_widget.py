"""
System Tray manager using pystray for the Windows Taskbar Notification Area.
Renders real-time percentage icons, countdown tooltips, and context menus for all AI providers.
"""

import ctypes
import pystray
import zlib
from PIL import Image
import webbrowser
import subprocess
import os
from typing import Callable, Optional, Union
import threading

from .icon_renderer import render_tray_icon
from ..core.models import UnifiedUsageState, ClaudeUsageState
from ..core.config import ConfigManager

# Identifies THIS app's tray icon to Windows, so its taskbar settings are its
# own and not shared with every other Python app on the machine. Derived from
# a fixed string rather than written as a literal so it is reproducible and
# obviously not a magic number. Keep the historical string after the
# TwinRails rename: changing the uID would lose existing tray pin settings.
TRAY_ICON_UID = zlib.crc32(b"ai-session-limits-widget") & 0x7FFFFFFF


def _apply_tray_icon_uid():
    """Makes pystray send a real, stable, app-specific ``uID`` with this
    widget's tray icon.

    Windows keys a tray icon's "pinned to the taskbar corner" setting (under
    HKCU\\Control Panel\\NotifyIconSettings) on the pair
    (executable path, uID) -- not on the executable alone, as it first
    appears. Explorer relies on this: one explorer.exe owns four separate
    entries, told apart by exactly this field.

    pystray never sets it. Its ``_message()`` passes ``hID=id(self)`` to
    NOTIFYICONDATAW, which has no ``hID`` field -- it is ``uID``. ctypes does
    not reject the unknown name, it just attaches it to the instance as an
    ordinary Python attribute, so the real struct field silently keeps its
    default of 0. Confirmed directly: ``NOTIFYICONDATAW(hID=12345).uID``
    is 0.

    Every pystray icon on Windows therefore shares uID 0, so any two Python
    tray apps run through the same interpreter look like ONE app to Windows
    and cannot be pinned or unpinned independently. That is not theoretical
    -- it is exactly what happened here with an unrelated pythonw.exe app.

    Setting a distinct uID gives this widget an entry of its own no matter
    which interpreter runs it. Verified by registering a probe icon with
    uID 987654 under the same python.exe as this widget: a second, separate
    registry entry appeared for it immediately.

    The value must be CONSTANT across runs. ``id(self)`` would have been
    unusable even spelled correctly: it is a memory address, so the icon
    would land on a different registry entry every launch and never remember
    anything. That is likely why nobody noticed the typo.

    Best-effort. This reaches into pystray's private ``_message`` and its
    bundled ctypes definitions, so a future pystray release could rename
    either -- but it is also the upstream bug being worked around, so a
    release that fixes it makes this a harmless no-op. Any failure here just
    restores today's behaviour: a working tray icon that shares its taskbar
    settings.
    """
    try:
        from pystray._util import win32
    except Exception:
        return  # not the win32 backend (or pystray changed) -- nothing to do

    if not any(name == "uID" for name, _ in win32.NOTIFYICONDATAW._fields_):
        return

    def _message(self, code, flags, **kwargs):
        win32.Shell_NotifyIcon(code, win32.NOTIFYICONDATAW(
            cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW),
            hWnd=self._hwnd,
            uID=TRAY_ICON_UID,
            uFlags=flags,
            **kwargs))

    try:
        pystray._win32.Icon._message = _message
    except Exception:
        pass


_apply_tray_icon_uid()


class TrayWidget:
    def __init__(
        self,
        config_manager: ConfigManager,
        on_left_click: Callable[[], None],
        on_toggle_bar: Callable[[], None],
        on_refresh: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_toggle_demo: Callable[[], None],
        on_exit: Callable[[], None]
    ):
        self.cfg_mgr = config_manager
        self.on_left_click = on_left_click
        self.on_toggle_bar = on_toggle_bar
        self.on_refresh = on_refresh
        self.on_open_settings = on_open_settings
        self.on_toggle_demo = on_toggle_demo
        self.on_exit = on_exit

        self.icon: Optional[pystray.Icon] = None
        self.current_state: Optional[Union[UnifiedUsageState, ClaudeUsageState]] = None
        self._is_running = False

    def _open_antigravity(self):
        try:
            localapp = os.environ.get("LOCALAPPDATA", "")
            exe_path = f"{localapp}\\Programs\\antigravity\\Antigravity.exe"
            if os.path.exists(exe_path):
                subprocess.Popen([exe_path])
        except Exception as e:
            print(f"Error opening Antigravity: {e}")

    def _create_menu(self) -> pystray.Menu:
        is_demo = self.cfg_mgr.config.demo_mode
        bar_shown = self.cfg_mgr.config.show_docked_bar

        items = [
            pystray.MenuItem(
                "Show Details",
                lambda icon, item: self.on_left_click(),
                default=True
            ),
            pystray.MenuItem(
                "Open Claude.ai",
                lambda icon, item: webbrowser.open("https://claude.ai")
            ),
            pystray.MenuItem(
                "Open Antigravity IDE",
                lambda icon, item: self._open_antigravity()
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Hide Taskbar Bar" if bar_shown else "Show Taskbar Bar",
                lambda icon, item: self.on_toggle_bar()
            ),
            pystray.MenuItem(
                "Refresh Usage",
                lambda icon, item: self.on_refresh()
            ),
            pystray.MenuItem(
                "Settings...",
                lambda icon, item: self.on_open_settings()
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Demo Mode: Active" if is_demo else "Switch to Demo Mode",
                lambda icon, item: self.on_toggle_demo()
            ),
            pystray.MenuItem(
                "Exit",
                lambda icon, item: self.on_exit()
            )
        ]
        return pystray.Menu(*items)

    def start(self, initial_state: Union[UnifiedUsageState, ClaudeUsageState]):
        self.current_state = initial_state

        # The icon itself is fixed (see render_tray_icon) so it stays a
        # stable, recognizable shape in a crowded tray -- only the tooltip
        # and menu reflect live usage, in start() and update_state().
        initial_img = render_tray_icon()

        tooltip = getattr(initial_state, "summary_tooltip", "TwinRails")[:127]
        self.icon = pystray.Icon(
            name="AISessionLimitsWidget",  # Stable internal ID keeps existing tray behavior.
            icon=initial_img,
            title=tooltip,
            menu=self._create_menu()
        )

        self._is_running = True
        self.icon.run_detached()

    def update_state(self, state: Union[UnifiedUsageState, ClaudeUsageState]):
        self.current_state = state
        if not self.icon:
            return

        try:
            tooltip = getattr(state, "summary_tooltip", "TwinRails")[:127]
            self.icon.title = tooltip
            self.icon.menu = self._create_menu()
        except Exception as e:
            print(f"Error updating tray icon: {e}")

    def maybe_show_first_run_hint(self):
        if self.cfg_mgr.config.tray_hint_shown:
            return
        try:
            if self.icon and pystray.Icon.HAS_NOTIFICATION:
                self.icon.notify(
                    "Click the '^' arrow next to the clock and drag the TwinRails badge out to keep it visible.",
                    "TwinRails is running"
                )
        except Exception:
            pass
        self.cfg_mgr.config.tray_hint_shown = True
        self.cfg_mgr.save()

    def stop(self):
        if self.icon and self._is_running:
            self._is_running = False
            try:
                self.icon.stop()
            except Exception:
                pass
