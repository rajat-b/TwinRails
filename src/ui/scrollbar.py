"""
The scrollbar used by the flyout and Settings: Windows' own dark scrollbar,
hidden whenever there is nothing to scroll.

Why this and not something drawn by hand: tk.Scrollbar on Windows is the real
Win32 scrollbar control, so one documented call -- SetWindowTheme with the
"DarkMode_Explorer" theme class -- makes Windows draw it the way Explorer's
dark mode does: on Windows 11 a slim thumb on a dark track that widens, with
arrows, on hover. Checked 2026-09-25 by rendering both side by side: it looks
the same with or without the undocumented uxtheme ordinals (133/135) that
dark-mode guides usually add, so those are not used.

Rejected: a flat ttk scrollbar built from "clam" elements (works, but a
blocky thumb that looks less native), and a hand-drawn canvas scrollbar (more
code to own for drag, click and hover). ttk.Scrollbar with the default
"vista" theme cannot be darkened this way at all -- Tk draws it itself rather
than using a native control, which is why Settings now uses this too.

On anything older than Windows 10 1809 the theme class does not exist and
the call quietly leaves the classic look in place.
"""

import ctypes
import tkinter as tk
from ctypes import wintypes


def _apply_dark_theme(scrollbar: tk.Scrollbar) -> None:
    try:
        ctypes.windll.uxtheme.SetWindowTheme(
            wintypes.HWND(scrollbar.winfo_id()), "DarkMode_Explorer", None
        )
    except Exception:
        pass    # not Windows, or no uxtheme: keep the classic scrollbar


class DarkScrollbar(tk.Scrollbar):
    """A native scrollbar in Windows' dark style that hides itself while its
    view already shows everything. Place it with auto_pack(), not pack()."""

    def __init__(self, master, **options):
        super().__init__(master, **options)
        self._pack_options = None
        self._next_sibling = None
        _apply_dark_theme(self)

    def auto_pack(self, **options):
        """pack() it, and remember how, so set() can hide and restore it."""
        self._pack_options = options
        self.pack(**options)

    def set(self, first, last):
        if self._pack_options is not None:
            needed = float(first) > 0.0 or float(last) < 1.0
            if needed and not self.winfo_manager():
                # Back in its original slot. A bare pack() would append it
                # after a sibling that already took all the space (the
                # flyout packs its scrollbar BEFORE the expanding canvas).
                before = self._next_sibling
                if before is not None and before.winfo_exists() and before.winfo_manager() == "pack":
                    self.pack(**self._pack_options, before=before)
                else:
                    self.pack(**self._pack_options)
            elif not needed and self.winfo_manager() == "pack":
                siblings = self.master.pack_slaves()
                index = siblings.index(self)
                self._next_sibling = siblings[index + 1] if index + 1 < len(siblings) else None
                self.pack_forget()
        super().set(first, last)
