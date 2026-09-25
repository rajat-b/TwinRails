# Documentation

Notes on how this widget works, and on the things that were surprising enough
to be worth writing down. User-facing setup lives in the top-level
[README](../README.md); this folder is for the parts that bite you when you
change the code.

## Pages

- **[Reading the bar](reading-the-bar.md)** — what the two tracks in each
  row actually measure, why the projected run-out point is drawn on its own
  time rail instead of across the usage bar, and how the row's three time
  fields are sized. Read this before changing anything about what a row
  draws or says.
- **[The refresh countdown](refresh-countdown.md)** — the hollow gauge in
  the mark's needle that empties as the auto-refresh interval runs down, why it
  replaced the octagonal outline (still one line away), the Tk rounding catch
  in drawing it, why drawing it with Pillow really would break transparent
  mode, and how polling backoff and hidden or locked sessions affect what it
  predicts.
- **[Taskbar embedding and rendering](taskbar-embedding.md)** — how the bar
  gets inside `Shell_TrayWnd`, why it must be an *opaque layered* window (and
  what it looks like when it isn't), and the Tk window that gets left behind,
  which eats clicks and can show up as a black box above the taskbar unless it
  is made both click-through and see-through. Read this before touching any
  win32 call in `src/ui/taskbar_bar.py`.
- **[Choosing the bar's colors](bar-colors.md)** — what the "Dark taskbar /
  Light taskbar" setting actually does, why no single color can match a
  translucent Windows 11 taskbar, and how the rest of the palette is derived.
- **[The tray icon's identity](tray-icon.md)** — why the tray code patches
  pystray to send an explicit `uID`. Without it every pystray icon on
  Windows shares one id, so two Python tray apps can't be pinned
  independently.
- **[Fetching Claude's usage data](fetching-claude-data.md)** — the verified
  OAuth response shape, the cookie fallback, and why that fallback still needs
  a Chrome TLS fingerprint. Read this before touching `claude_client.py`.
