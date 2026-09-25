"""
Dark title bar for the app's one titled window (Settings); the flyout, the
taskbar bar and its tooltip have no title bar.

One documented call: DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE).
The attribute is 20 on Windows 10 20H1 and later, and was 19 on the earlier
builds that had it, so 19 is tried if 20 is refused. Where neither exists the
title bar simply stays light.

It must target Tk's outer wrapper window (wm_frame), which Tk only creates
when the window is first mapped -- hence the <Map> hook -- and a frame change
is then forced so Windows repaints a title bar it may already have drawn.
"""

import ctypes
import tkinter as tk
from ctypes import wintypes

_DWMWA_USE_IMMERSIVE_DARK_MODE = (20, 19)
_SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020   # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED


def _apply(window: tk.Toplevel) -> None:
    try:
        hwnd = wintypes.HWND(int(window.wm_frame(), 16))
        on = ctypes.c_int(1)
        for attribute in _DWMWA_USE_IMMERSIVE_DARK_MODE:
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _SWP_FLAGS)
    except Exception:
        pass    # not Windows, or no DWM: keep the default title bar


def use_dark_title_bar(window: tk.Toplevel) -> None:
    """Give this toplevel a dark title bar as soon as it is first shown."""
    def on_map(event):
        # A binding on a toplevel also receives its children's events.
        if event.widget is window:
            window.unbind("<Map>", binding)
            _apply(window)
    binding = window.bind("<Map>", on_map, add="+")
