# The tray icon's identity

Why [`tray_widget.py`](../src/ui/tray_widget.py) patches pystray to send an
explicit `uID`, and what breaks without it.

---

## The symptom

This widget's tray icon started following an **unrelated** Python app's
"pinned to the taskbar corner" setting. Pin that app, and this one pinned
too. There was no way to have one pinned and the other not.

## The cause

Windows 11 stores that setting under
`HKCU\Control Panel\NotifyIconSettings`, keyed on the pair
**(executable path, uID)** — `uID` being the icon's own identifier from
`NOTIFYICONDATA`. Explorer depends on this: one `explorer.exe` owns four
separate entries, told apart by exactly that field (and `IconGuid`).

**pystray never sets it.** Its `_message()` builds the struct with
`hID=id(self)` — but the field is called `uID`, not `hID`. ctypes doesn't
reject the unknown name; it quietly attaches it to the instance as an
ordinary Python attribute and the real struct field keeps its default of
**0**. Confirmed directly:

```python
>>> NOTIFYICONDATAW(hID=12345).uID
0
```

So *every* pystray tray icon on Windows ships with `uID = 0`. Two Python
tray apps run through the same interpreter therefore look like one
application to Windows, and share one pinned/unpinned setting.

Note the typo is a lucky one. `id(self)` is a memory address — different on
every launch — so a correctly-spelled version would have moved the icon to a
new registry entry each time it started, and it would have remembered
nothing at all. The bug is only harmless because it fails all the way to a
constant.

## The fix

Set a fixed, app-specific `uID` (`TRAY_ICON_UID`, derived from a CRC of the
original project slug so it is reproducible and obviously not arbitrary). It
must be **constant across runs** or the pin setting won't persist. The
TwinRails rename keeps this internal value so existing users keep their tray
pin setting.

Verified, under the very same `pythonw.exe` as the app it used to collide
with:

```
pythonw.exe   uID 1100983367                  <- this widget, its own entry
pythonw.exe   uID 0            IsPromoted=1   <- the other app, untouched
```

The launchers are irrelevant to this. Two earlier attempts tried to fix it
by giving the widget a different *executable* instead — first `python.exe`
rather than `pythonw.exe`, then a uniquely-named copy of the interpreter
built into a venv. Both worked, and both were the wrong layer:

- Swapping executables only changes *which* apps you collide with.
- The renamed interpreter was **quarantined by Bitdefender** on the next
  restart — a renamed Python interpreter in an unusual folder is a textbook
  antivirus heuristic trigger. Nothing about it was malicious, and nothing
  about it was worth arguing with an antivirus over.

Both were reverted. The launchers are back to their original form and this
one field does the whole job.

## Fragility

The patch reaches into pystray's private `_message` and its bundled ctypes
definitions, so a future pystray release could rename either. It is
best-effort and guarded:

- If the win32 backend or the `uID` field isn't there, it does nothing.
- Any failure leaves the previous behaviour — a working tray icon that
  shares its taskbar settings.

If pystray ever fixes the typo upstream, this becomes a harmless no-op.

## Changing the pin state

Windows owns that setting; the app never writes it, and neither should you
from code. Right-click the taskbar → Taskbar settings → "Other system tray
icons", or drag the icon in or out of the overflow flyout.

Stale entries accumulate there for icons that no longer exist (that key
holds a dozen historical WhatsApp versions). They are inert.

## Not to be confused with the taskbar identity

Since 2026-09-25, `main.py` calls `SetCurrentProcessExplicitAppUserModelID`
with `"TwinRails.Widget"` at startup, and gives every titled window the
TwinRails mark with `iconphoto`. Without the ID, Windows files the Settings
window under `pythonw.exe` and shows Python's icon on its taskbar button,
whatever icon Tk sets. That ID governs taskbar grouping only. The tray pin
setting above is still keyed on (executable path, `uID`), so the ID does not
touch it. Keep the ID constant too: changing it regroups the window under a
new identity.
