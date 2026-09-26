"""
Horizontal taskbar bar widget for TwinRails.
Genuinely embedded in the Windows taskbar itself (SetParent into Shell_TrayWnd,
docked next to the tray/clock cluster).

Features:
- Multi-provider support (Claude, Antigravity / Gemini, and future providers)
- Left pulse-line mark, click to refresh, light/dark adaptive coloring
- An octagonal countdown around that mark, emptying as the auto-refresh
  interval elapses (see COUNTDOWN_HALF for the shape's history)
- Hover tooltip showing action prompt and last updated timestamp
- Clicking anywhere on progress bars toggles the Windows 11 Flyout widget
- Vertically stacked 14px bars with high-contrast extended time markers
- Bars dim while a refresh is in flight, and stay dim with an amber tint
  for any metric whose provider is disconnected, until it reconnects
- Adaptive pacing color system (Dynamic Halfway Midpoint default vs Fixed Delta)
- Manual light/dark background color (Settings + a quick toggle in the
  flyout) -- an earlier attempt at auto-detecting the real taskbar color via
  GetPixel was dropped: unreliable in practice, and tangled up with
  unrelated Tk/win32 embedding flakiness on some systems. See
  theme.adaptive_bar_theme() for how a single configured color expands into
  matching text/track/marker colors.
- Explorer right-click menu active suppression

Two win32 details are what make the embedded bar actually behave, and both
are easy to lose in a refactor because nothing crashes without them:

1. The embedded window MUST be an opaque layered window
   (_make_embedded_layered). Without it, Windows composites the bar
   ADDITIVELY onto the taskbar behind it, which looks acceptable on a dark
   taskbar and completely washes out on a light one.
2. Tk's leftover wrapper window MUST be made both click-through
   (_set_wrapper_click_through) and see-through (_set_wrapper_see_through).
   Without the first it silently swallows every click in the strip of screen
   directly above the taskbar; without the second it can show there as a
   solid black box the width of the bar.
"""

import tkinter as tk
import tkinter.font as tkfont
import ctypes
import math
from ctypes import wintypes
from typing import Optional, Callable, Union

from .theme import (
    CLAUDE_CORAL, CLAUDE_CORAL_HOVER, CREDITS_GREEN,
    DANGER_RED, WARNING_AMBER, FONT_FAMILY, BG_MAIN, BORDER_CARD,
    TEXT_PRIMARY, TEXT_SECONDARY, EXHAUSTION_MARKER_COLOR,
    adaptive_bar_theme, transparent_bar_theme, ensure_contrast,
    is_light_color, blend_hex, TRACK_OUTLINE, PACING_TEXT_MIN_CONTRAST
)
from ..core.models import (
    UsageWindow, CreditsMetric, UnifiedUsageState, ClaudeUsageState, format_ago
)
from ..core.config import ConfigManager
from .dpi import get_work_area_for_point_or_window

TRANS_KEY = "#000001"
FLOATING_PANEL_BG = BG_MAIN
BAR_WIDTH = 170
BAR_HEIGHT = 14

# All content (icon + up to 4 rows, as one or two columns of 2) is drawn on
# a single tk.Canvas instead of the previous tree of nested
# Frames/Labels/CustomProgressBar. Confirmed directly with an isolated
# reproduction: once genuinely embedded as a child of Shell_TrayWnd, a deep
# widget tree (window -> container -> rows_frame -> row -> label/canvas, 5
# levels) never renders even after forcing repaints, while a shallow one
# (window -> label/canvas directly, 2 levels) does once forced to repaint.
# One flat Canvas is as shallow as this content can get.
PAD = 4
ICON_W = 26
ICON_GAP = 6
TAG_W = 42
BAR_GAP = 5
PCT_GAP = 8
PCT_W = 34
TIME_GAP = 4
# Was 122 back when this slot held "1h 28m (4:12 PM)". It now holds the
# exact reset clock time with its countdown bracketed after it, and the pace
# projection lives in its own slot beside it so the two can be coloured
# separately -- and, in the run-out case, carries a bracketed countdown of
# its own.
#
# The four text slot widths below (TAG_W, PCT_W, TIME_W, PACE_W) are only
# their values at 100% Windows display scaling. _calibrate_text_slots()
# replaces them at startup with the widest text each slot can hold, measured
# in the running app's own fonts, plus the slack in _TEXT_SLOT_SLACK. Read
# its docstring before hard-coding a width here again.
#
# There is no slack to reclaim elsewhere in the row. The width lever, if one
# is ever needed, is dropping the weekday prefix from these clock times: the
# bracketed countdown already rules out reading "12:34p" as today, so "Mo" is
# convenience rather than information, and removing it from both fields
# would give back roughly 54px per column at 100%.
TIME_W = 110
PACE_GAP = 8
PACE_W = 120
ROW_H = 26
ROW_GAP = 2
# The canvas's top and bottom padding. PAD stays the horizontal one; the two
# were the same until _calibrate_rows() started trimming this one.
PAD_Y = PAD
COL_GAP = 14
MAX_METRICS = 4
ROWS_PER_COL = 2
ROW_CONTENT_W_FULL = (TAG_W + BAR_GAP + BAR_WIDTH + PCT_GAP + PCT_W
                      + TIME_GAP + TIME_W + PACE_GAP + PACE_W)
ROW_CONTENT_W_COMPACT = (TAG_W + PCT_GAP + PCT_W
                        + TIME_GAP + TIME_W + PACE_GAP + PACE_W)
ROW_CONTENT_W = ROW_CONTENT_W_FULL
CANVAS_CONTENT_W = ICON_W + ICON_GAP + ROW_CONTENT_W
MARKER_PROTRUSION = 2

# Widest text each slot must hold, and the slack added on top. At 100%
# scaling these reproduce the old hard-coded widths (42/34/110/120) to
# within a pixel -- "Mo" turned out 1px wider than the "We" they were
# measured from.
_DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_TIME_SAMPLES = tuple(f"{d} 12:34{ap} (6d 23h)" for d in _DAYS for ap in "ap")
_TEXT_SLOT_SAMPLES = {
    "TAG_W": ((FONT_FAMILY, 8, "bold"),
              ("✱ OPU", "✱ SON", "✱ FAB", "✱ 5H", "✱ 7D", "✦ 5H", "✦ 7D", "✱ $")),
    "PCT_W": ((FONT_FAMILY, 9, "bold"), ("100%",)),
    "TIME_W": ((FONT_FAMILY, 9), _TIME_SAMPLES),
    "PACE_W": ((FONT_FAMILY, 9), tuple("→" + s for s in _TIME_SAMPLES)),
}
_TEXT_SLOT_SLACK = {"TAG_W": 3, "PCT_W": 3, "TIME_W": 10, "PACE_W": 10}


def _calibrate_text_slots(root) -> None:
    """Size the row's text slots from the fonts as this process draws them.

    The slot widths used to be fixed pixel counts measured at 100% display
    scaling, and the geometry around them is fixed pixels too. But the app is
    DPI-aware (dpi.py), so Tk draws point-sized text at the display's real
    DPI: 25% wider at 125%, 50% at 150%. Found in a 125% capture on
    2026-09-25: "We 5:26p (4d 21h)" ran straight into its projection and read
    "(4d 21h)→47%", and a red run-out time ended a few px from the next
    column. Measured with the app's own awareness on at 125%, the worst cases
    needed 129px (time, slot 110), 142 (projection, 120), 40 ("100%", 34)
    and 46 (tag, 42).

    Measuring here, with this process's own fonts, fixes that at any scaling.
    A standalone `python -c` measurement does not: without the app's DPI
    awareness it always reports 96-DPI widths, which is how the old numbers
    were wrong in the first place.

    Only the text slots change. The bar, rail and gaps stay fixed pixels --
    the taskbar's height is what limits them, and it does not grow with the
    text -- so on a scaled display the whole bar is wider than at 100%
    (a four-metric layout at 125% is ~1210px instead of 1056).

    Tk fixes its scaling at startup and nothing in this app changes it, so
    measuring once, before the first row is laid out, matches every later
    draw. Idempotent: it measures, it does not multiply."""
    global TAG_W, PCT_W, TIME_W, PACE_W
    global ROW_CONTENT_W_FULL, ROW_CONTENT_W_COMPACT, ROW_CONTENT_W, CANVAS_CONTENT_W
    widths = {}
    for name, ((family, size, *style), samples) in _TEXT_SLOT_SAMPLES.items():
        font = tkfont.Font(root=root, family=family, size=size,
                           weight=style[0] if style else "normal")
        widths[name] = max(font.measure(s) for s in samples) + _TEXT_SLOT_SLACK[name]
    TAG_W, PCT_W = widths["TAG_W"], widths["PCT_W"]
    TIME_W, PACE_W = widths["TIME_W"], widths["PACE_W"]
    ROW_CONTENT_W_FULL = (TAG_W + BAR_GAP + BAR_WIDTH + PCT_GAP + PCT_W
                          + TIME_GAP + TIME_W + PACE_GAP + PACE_W)
    ROW_CONTENT_W_COMPACT = (TAG_W + PCT_GAP + PCT_W
                             + TIME_GAP + TIME_W + PACE_GAP + PACE_W)
    ROW_CONTENT_W = ROW_CONTENT_W_FULL
    CANVAS_CONTENT_W = ICON_W + ICON_GAP + ROW_CONTENT_W

# The refresh countdown: an octagon traced around the pulse mark that
# empties as the auto-refresh interval elapses (drawn at 2px over a 1px track),
# so "is what I'm looking at stale?" is answerable at a glance instead of by
# hovering for the tooltip.
#
# The shape went circle -> square -> octagon -> circle -> octagon. Read the
# next two paragraphs before changing it, because the obvious reason for
# the octagon is the WRONG one and was already tried.
#
# The wrong reason: "Tk draws through GDI, which does no antialiasing, so a
# hairline circle this small comes out stair-stepped." That was asserted
# from theory, checked only against a simulation that thresholded alpha
# (which exaggerates exactly this artifact), and it is FALSE at this size.
# A real Tk circle was eventually rendered at r=12 and GDI's ellipse
# rasteriser draws it cleanly. So the countdown was moved back to a circle
# -- and then moved off it again, on the only evidence that outranks a
# zoomed render: how it actually looks on a real taskbar at real size,
# where the circle reads soft and indistinct and the octagon reads sharp.
#
# So the octagon is here on APPEARANCE, not on correctness. A circle is a
# legitimate choice that renders fine; it just loses at 1:1. Do not
# "restore" it on the theory that the octagon was an aliasing workaround.
#
# What IS a correctness constraint: drawing this with Pillow would break
# transparent-background mode outright. The bar is a layered window keying
# out one exact colour, and antialiased edge pixels are by definition not
# that exact colour, so they survive the key as a dark halo on a light
# taskbar. Every mark on this canvas is a hard-edged Tk primitive for that
# reason, and this one must stay that way whatever shape it is.
#
# A hexagon was rendered too and fails twice over: its 30/60-degree edges
# step irregularly (2,2,1,2,2,1) and look it, and its widest point is a
# single vertex, so it narrows away from the midline fast enough that the
# pulse mark's ends run into the sloping edges.
#
# A half-drawn outline would read as a rendering glitch on its own, which
# is why the faded full shape underneath is not optional -- the perimeter
# is always closed, and only its brightness varies around the loop.
#
# 12 is the largest half-extent that still clears the pulse mark: the mark
# reaches +/-9 plus half of its 2px stroke, so +/-10, leaving a 2px gap at
# the midline where the two come closest. That is what lets the mark keep
# its existing size. It pokes 1px past the nominal 26px icon column on each
# side, into space nothing else uses.
COUNTDOWN_HALF = 12
# Corner cut that turns the bounding square into a regular octagon. A
# square was the shape before this and is equally crisp -- every edge
# axis-aligned -- just plain. The octagon costs nothing over it and reads
# considerably better.
#
# Both are exactly crisp because after horizontal and vertical, a
# 45-degree line is the cleanest thing GDI can draw, stepping one pixel
# across for one pixel down forever. An octagon is those two angles and
# nothing else. That is why the octagon renders sharply -- but see the
# block above for why it is NOT why the octagon was chosen over a circle.
#
# 2h = (2 + sqrt2)c makes all eight sides equal; at h=12 that is c=7.03,
# rounded to whole pixels because every vertex has to land on the grid for
# the edges to stay crisp.
COUNTDOWN_CUT = 7


def _countdown_polygon(cx: float, cy: float) -> list:
    """Octagon vertices, clockwise from the MIDDLE of the top edge.

    Starting mid-edge rather than at a vertex is what keeps the countdown
    readable: from a corner the shape empties along two edges at once near
    the end and the eye cannot tell which one is the head."""
    h, c = COUNTDOWN_HALF, COUNTDOWN_CUT
    k = h - c
    return [
        (cx, cy - h),
        (cx + k, cy - h), (cx + h, cy - k),
        (cx + h, cy + k), (cx + k, cy + h),
        (cx - k, cy + h), (cx - h, cy + k),
        (cx - h, cy - k), (cx - k, cy - h),
        (cx, cy - h),
    ]


# Measured off the polygon rather than written down, so the shape stays the
# single source of truth if the geometry is ever retuned.
def _polygon_perimeter() -> float:
    poly = _countdown_polygon(0, 0)
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(poly, poly[1:]))


COUNTDOWN_PERIMETER = _polygon_perimeter()

# The icon -- the rails mark and the countdown octagon around it -- scales
# with the display, like the row text, plus a deliberate 10% boost. All the
# numbers above (ICON_W 26, COUNTDOWN_HALF 12, COUNTDOWN_CUT 7) are the 1x
# design; _calibrate_icon() replaces them at startup. Until 2026-09-25 they
# were used as fixed pixels, so at 125% the mark stayed at its 100% size
# while the text around it grew 25%, and it read as too small. Cost: the
# icon column grows 26 -> 34px at 125% (28 at 100%). The octagon keeps
# every property the blocks above insist on: whole-pixel vertices, only
# 0/45/90-degree edges, the same clearance ratio around the mark.
_ICON_BOOST = 1.1
_COUNTDOWN_HALF_1X = COUNTDOWN_HALF
ICON_SCALE = 1.0

# How the time to the next refresh is shown. "octagon": the outline above
# drains round the mark. "needle": no outline; the mark's own white needle
# drains from the top, and the mark grows into the octagon's space.
# "needle" was adopted on 2026-09-25 after a trial on the real taskbar, which
# the octagon's history says is the only test that counts. It is drawn as a
# small hollow gauge; the reasons, what it gives up and the variants tried are
# in docs/refresh-countdown.md. "octagon" stays one line away.
COUNTDOWN_STYLE = "needle"
# The mark's extra size in the octagon's freed space. 1.4 was tried first
# (rails 31px wide at 125%) and read as a fifth usage bar; 1.2 gives 26px.
_NEEDLE_MARK_BOOST = 1.2


def _mark(v: float) -> float:
    """A 1x mark offset at the calibrated scale, snapped to the half pixel --
    the rails' outlines were drawn on half-pixel coordinates to land crisply."""
    return round(v * ICON_SCALE * 2) / 2


def _calibrate_icon(root) -> None:
    """Size the icon from the display scaling Tk started with (see above).

    Must run before _calibrate_text_slots(), which folds ICON_W into the
    canvas width. Idempotent: it derives from the 1x values every time."""
    global ICON_SCALE, ICON_W, COUNTDOWN_HALF, COUNTDOWN_CUT, COUNTDOWN_PERIMETER
    try:
        display = float(root.tk.call("tk", "scaling")) / (96 / 72)
    except (tk.TclError, ValueError):
        display = 1.0
    ICON_SCALE = max(1.0, display) * _ICON_BOOST
    COUNTDOWN_HALF = round(_COUNTDOWN_HALF_1X * ICON_SCALE)
    # Same rule as the 1x cut: 2h = (2 + sqrt2)c keeps the eight sides equal.
    COUNTDOWN_CUT = round(2 * COUNTDOWN_HALF / (2 + math.sqrt(2)))
    # The column is the octagon plus 1px each side, exactly as at 1x (12 -> 26).
    ICON_W = 2 * COUNTDOWN_HALF + 2
    COUNTDOWN_PERIMETER = _polygon_perimeter()
    if COUNTDOWN_STYLE == "needle":
        # No outline to fit inside, so the mark takes its space: rails' full
        # width (16px at 1x) plus 2px each side. At 125% about 35px.
        ICON_SCALE *= _NEEDLE_MARK_BOOST
        ICON_W = round(16 * ICON_SCALE) + 4

# The time rail: a hairline under each usage bar carrying everything that is
# measured in TIME rather than in quota -- how far through the window you
# are, and the stretch at the end you would spend locked out at this pace.
#
# It exists because the projected-exhaustion mark used to be a dashed line
# drawn straight across the usage bar, which made the fill look like it was
# racing towards a wall. It was not: the fill's only wall is the right-hand
# edge, always. The dashed line was a point in TIME, sharing an axis with a
# quantity it has nothing to do with. Splitting time onto its own rail makes
# that collision impossible to draw. The one marker that legitimately spans
# both is the "now" needle, which is meant to be read against the fill front
# (fill ahead of the needle = burning faster than the clock).
RAIL_HEIGHT = 3
RAIL_GAP = 3
MAX_TIME_DIVIDERS = 6  # Six internal boundaries make seven day sections.
# Vertical stop cap at the head of the lockout tail, marking the moment the
# quota runs dry. Without it the tail reads as a vague red smear that shrinks
# as the window advances; with it, the cap is the event and the tail is its
# consequence.
RAIL_STOP_OVERHANG = 3

SHELL_TRAY_CLASS = "Shell_TrayWnd"
TRAY_NOTIFY_CLASS = "TrayNotifyWnd"
EMBED_GAP = 4

# The rows' vertical metrics as designed. _calibrate_rows() trims copies of
# them when the taskbar is too thin to hold the design.
_ROW_H_1X, _ROW_GAP_1X, _PAD_Y_1X, _BAR_HEIGHT_1X = ROW_H, ROW_GAP, PAD_Y, BAR_HEIGHT
# Thinnest usage bar the rows shrink to. A taskbar too thin even for that
# (Windows 10 with small taskbar buttons, 30px) still clips the second row.
_MIN_BAR_HEIGHT = 6


def _row_extent(bar_height: int) -> int:
    """How tall one row's drawing is: the needle's overhang above the bar
    (the `y0 + 2` in _build_bar_row), the bar, the time rail under it, and
    the lockout stop cap, which hangs lowest. 25px at the designed 14px bar."""
    return 2 + bar_height + RAIL_GAP + RAIL_HEIGHT + RAIL_STOP_OVERHANG


def _taskbar_thickness() -> Optional[int]:
    """The primary taskbar's height (its width when docked left or right), in
    the physical pixels the canvas is drawn in, or None if there is none."""
    try:
        hwnd = ctypes.windll.user32.FindWindowW(SHELL_TRAY_CLASS, None)
        rect = wintypes.RECT()
        if not hwnd or not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    except (AttributeError, OSError):
        return None
    return min(rect.right - rect.left, rect.bottom - rect.top) or None


def _calibrate_rows(taskbar_h: Optional[int]) -> None:
    """Fit both stacked rows inside a taskbar taskbar_h pixels thick.

    As designed the canvas is 62px tall, and a canvas taller than the taskbar
    is pinned to the taskbar's top edge (see _reposition_embedded), so
    whatever hangs below is cut off. On Windows 11 at 125% (a 60px taskbar)
    that is only bottom padding, and the layout is left exactly as designed.
    At 100% (48px) it was the second row's bar and rail, and Windows 10's
    40px taskbar lost more.

    When the rows' drawing would be cut, space goes in order of how little it
    shows: padding and the gap between rows first, then bar height, down to
    _MIN_BAR_HEIGHT. The rail, needle and text keep their sizes, and the
    block ends up centred. Runs once before the canvas is built, so a taskbar
    resized while TwinRails runs keeps the old fit until it restarts.
    Idempotent: it starts from the designed values every time."""
    global ROW_H, ROW_GAP, PAD_Y, BAR_HEIGHT
    ROW_H, ROW_GAP, PAD_Y, BAR_HEIGHT = _ROW_H_1X, _ROW_GAP_1X, _PAD_Y_1X, _BAR_HEIGHT_1X
    if not taskbar_h:
        return
    if PAD_Y + ROW_H + ROW_GAP + _row_extent(BAR_HEIGHT) <= taskbar_h:
        return
    BAR_HEIGHT = max(_MIN_BAR_HEIGHT, min(_BAR_HEIGHT_1X, taskbar_h // 2 - _row_extent(0)))
    ROW_H = _row_extent(BAR_HEIGHT)
    ROW_GAP = min(_ROW_GAP_1X, max(0, taskbar_h - 2 * ROW_H))
    PAD_Y = max(0, (taskbar_h - 2 * ROW_H - ROW_GAP) // 2)

# Genuine SetParent-into-Shell_TrayWnd embedding is enabled. It looked
# broken for a long stretch of this feature's development -- GetAncestor
# checks (both in-process and from a separate polling process) kept showing
# the window's parent reverting away from Shell_TrayWnd within well under a
# second. The actual cause: a Tk Toplevel on Windows is really two win32
# windows -- an outer "wrapper" that keeps the title (what wm_frame() and
# any title-based EnumWindows search find) and an inner content window
# (what winfo_id() returns) that the visible widgets live in. _resolve_hwnd()
# was resolving to the wrapper, so SetParent was reparenting the wrong
# window; the wrapper (empty, orphaned) stayed put while every "is it really
# embedded" check kept looking at that same wrong window. Once
# _resolve_hwnd() was fixed to use winfo_id(), the embed genuinely holds.
# It still didn't render, though: a reparented window's content doesn't
# repaint on its own (see _force_full_repaint()), and a deep nested-widget
# tree didn't respond to a forced repaint the way a flat one does -- see the
# comment on CANVAS_CONTENT_W above for why this file draws everything on a
# single Canvas instead of nested Frames/Labels.
ATTEMPT_GENUINE_EMBED = True

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
# Makes a window invisible to hit-testing -- every click at its coordinates
# goes to whatever is behind it instead. Used on the leftover Tk wrapper
# window after embedding; see _set_wrapper_click_through().
WS_EX_TRANSPARENT = 0x00000020
LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002


def _set_ex_style_bit(hwnd: int, bit: int, enabled: bool):
    user32 = ctypes.windll.user32
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    style_u = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    new_u = (style_u | bit) if enabled else (style_u & ~bit)
    new_u &= 0xFFFFFFFF
    new_signed = new_u - 0x100000000 if new_u >= 0x80000000 else new_u
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_signed)


def _set_layered(hwnd: int, enabled: bool):
    _set_ex_style_bit(hwnd, WS_EX_LAYERED, enabled)


def _system_uses_light_theme() -> Optional[bool]:
    """True if Windows is currently drawing the taskbar in its light theme,
    False for dark, None if it can't be determined.

    Note this is SystemUsesLightTheme, not AppsUseLightTheme -- Windows
    tracks those two independently and it is entirely normal to run light
    apps with a dark taskbar, or the reverse. Only the system one describes
    the taskbar.

    This is a settings lookup, not a pixel sample -- deliberately, given
    this project's earlier experience with GetPixel-sampling the taskbar
    (unreliable, and it can't be sampled where the bar itself covers it
    anyway). It doesn't give an exact color, and with transparency effects
    on the real taskbar is wallpaper-tinted rather than either preset
    shade. But it answers the only question that has to be right for the
    bar to stay readable -- is the thing behind it light or dark -- and it
    answers it reliably."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return bool(value)
    except Exception:
        return None


def _to_colorref(hex_color: str) -> int:
    """#RRGGBB -> win32 COLORREF (0x00BBGGRR). The byte order is reversed
    from HTML hex; getting it wrong silently keys out the wrong color."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def _make_embedded_layered(hwnd: int, colorkey: Optional[str] = None) -> bool:
    """Marks hwnd as a layered window -- fully opaque, or with `colorkey`
    punched out to true transparency.

    This is what stops the embedded bar from being blended INTO the taskbar
    behind it rather than drawn on top of it. Confirmed directly with an
    isolated reproduction (six known color swatches painted on a Tk canvas
    reparented into Shell_TrayWnd, then read back from a real screen
    capture): as a plain, non-layered child of Shell_TrayWnd, every pixel
    came back as exactly `swatch + taskbar_pixel`, saturating at 255 --
    i.e. ADDITIVE compositing, not opaque drawing:

        black  #000000 -> the taskbar's own color, unchanged (invisible)
        blue   #0000FF -> taskbar color with the blue channel forced to 255
        coral  #D97756 -> #FFFFFF (pure white)
        gray   #808080 -> #FFFFFF (pure white)

    The cause is the alpha channel. Windows 11's taskbar is composited by
    DWM from a surface WITH per-pixel alpha; plain GDI drawing (which is
    all Tk does) writes RGB and leaves alpha at 0. DWM then treats those
    pixels as premultiplied-alpha source, so `dst = src + dst * (1 - 0)`
    -- the additive result above.

    Turning the window into an opaque layered window hands compositing to
    the window manager instead, which supplies a real alpha. With
    WS_EX_LAYERED + LWA_ALPHA(255) applied, the same six swatches read back
    pixel-EXACT (#000000 stayed #000000, #D97756 stayed #D97756, etc.).

    This is also why the bar only ever looked right against a dark taskbar:
    adding a color to a near-black background is nearly a no-op, so dark
    mode accidentally looked correct. Against a light taskbar everything
    saturates toward white and no configured panel color, and no amount of
    contrast tuning on the foreground colors, can recover it -- additive
    blending can only ever brighten. An earlier attempt to fix the
    washed-out look by deriving WCAG-contrast-corrected foreground colors
    could not have worked for this reason (see
    experiment/taskbar-bar-contrast-fixes).

    Passing `colorkey` additionally punches every pixel of exactly that
    color out to full transparency, so the real taskbar shows through where
    the panel background would otherwise be. That is the only way to match
    a translucent Windows 11 taskbar, whose color is wallpaper-derived and
    varies across its own width -- measured across one 3840px taskbar:
    #D2C37C at x=200, #DCF2B9 at x=800, #DDF2B6 at x=2900. No single
    painted color can be right in more than one place.

    Note this is NOT the additive blend above coming back. Additive
    blending was Windows compositing our pixels wrongly because they had no
    alpha; a colorkey is Windows compositing them correctly, having been
    told which ones to drop. Everything not keyed out still renders
    pixel-exact.
    """
    try:
        _set_layered(hwnd, True)
        if colorkey:
            return bool(ctypes.windll.user32.SetLayeredWindowAttributes(
                hwnd, _to_colorref(colorkey), 255, LWA_COLORKEY | LWA_ALPHA
            ))
        # crKey is ignored when only LWA_ALPHA is passed.
        return bool(ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA))
    except Exception:
        return False


def _has_layered_attrs(hwnd: int, colorkey: Optional[str] = None) -> bool:
    """True if hwnd currently has exactly the layered attributes
    _make_embedded_layered(hwnd, colorkey) would apply. Used to re-assert
    them cheaply if something (Tk re-applying its own -transparentcolor, a
    theme change, a deiconify) clears them out from under the embedded
    window."""
    try:
        user32 = ctypes.windll.user32
        if not (user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF) & WS_EX_LAYERED:
            return False
        crkey = wintypes.DWORD()
        alpha = ctypes.c_byte()
        flags = wintypes.DWORD()
        if not user32.GetLayeredWindowAttributes(
            hwnd, ctypes.byref(crkey), ctypes.byref(alpha), ctypes.byref(flags)
        ):
            return False
        if (alpha.value & 0xFF) != 255:
            return False
        if colorkey:
            return (flags.value == (LWA_COLORKEY | LWA_ALPHA)
                    and crkey.value == _to_colorref(colorkey))
        return flags.value == LWA_ALPHA
    except Exception:
        return False


def _read_layered_attrs(hwnd: int) -> Optional[tuple]:
    """(colorkey as a COLORREF, alpha, LWA_* flags) as set by
    SetLayeredWindowAttributes, or None if hwnd has none to read."""
    try:
        crkey = wintypes.DWORD()
        alpha = ctypes.c_ubyte()
        flags = wintypes.DWORD()
        if not ctypes.windll.user32.GetLayeredWindowAttributes(
            hwnd, ctypes.byref(crkey), ctypes.byref(alpha), ctypes.byref(flags)
        ):
            return None
        return crkey.value, alpha.value, flags.value
    except Exception:
        return None


def _set_layered_alpha(hwnd: int, alpha: int) -> bool:
    """Changes only a layered window's opacity, leaving its colorkey and
    flags as they are.

    Written for Tk's wrapper window, whose colorkey is Tk's own
    -transparentcolor. Keeping that intact is what lets 255 hand the
    floating panel back exactly as Tk had it -- confirmed: a floating test
    window taken to 0 and back showed its content and its keyed-out
    background just as before."""
    attrs = _read_layered_attrs(hwnd)
    if attrs is None:
        return False
    crkey, _, flags = attrs
    try:
        return bool(ctypes.windll.user32.SetLayeredWindowAttributes(
            hwnd, crkey, alpha, flags | LWA_ALPHA
        ))
    except Exception:
        return False


def get_work_area(pt_or_hwnd: Optional[Union[tuple, int]] = None):
    return get_work_area_for_point_or_window(pt_or_hwnd)


def get_cursor_pos():
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class TaskbarBarWidget:
    def __init__(
        self,
        master: tk.Tk,
        config_manager: ConfigManager,
        on_open_flyout: Callable[..., None],
        on_refresh: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_close_flyout: Optional[Callable[[], None]] = None,
        is_flyout_active: Optional[Callable[[], bool]] = None,
        schedule_flyout_hide: Optional[Callable[..., None]] = None,
        cancel_flyout_hide: Optional[Callable[[], None]] = None,
        get_refresh_progress: Optional[Callable[[], tuple]] = None
    ):
        self.master = master
        self.cfg_mgr = config_manager
        self.on_open_flyout = on_open_flyout
        self.on_close_flyout = on_close_flyout
        self.on_refresh = on_refresh
        self.on_open_settings = on_open_settings
        self.is_flyout_active = is_flyout_active
        self.schedule_flyout_hide = schedule_flyout_hide
        self.cancel_flyout_hide = cancel_flyout_hide
        # Returns (elapsed_fraction, is_fetching) for the auto-refresh
        # interval -- a callback rather than state pushed in through
        # update_state(), because the poll clock lives in the app (it is the
        # same _last_fetch_time that decides when to actually fetch) and
        # duplicating it here would let the drawn countdown and the real
        # schedule drift apart. None simply hides the countdown.
        self.get_refresh_progress = get_refresh_progress

        self.window = tk.Toplevel(master)
        self.window.title("TwinRailsBar")
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.wm_attributes("-topmost", True)

        self.window.wm_attributes("-transparentcolor", TRANS_KEY)
        self.window.configure(bg=TRANS_KEY)

        self.drag_start_x = 0
        self.drag_start_y = 0
        self.click_start_x_root = 0
        self.click_start_y_root = 0
        self._press_was_icon = False
        self.is_locked = self.cfg_mgr.config.docked_bar_locked

        self._tooltip_win: Optional[tk.Toplevel] = None
        self._icon_hovered = False

        self._embedded = False
        self._shell_tray_hwnd = 0
        # Tk's own outer "wrapper" window for this Toplevel (see
        # _resolve_hwnd's docstring). Captured before the first SetParent,
        # because after embedding it is no longer this window's ancestor
        # and can't be looked up that way any more.
        self._wrapper_hwnd = 0
        self._wrapper_click_through = False
        # Set True the first time a SetParent-into-Shell_TrayWnd is attempted
        # and verified NOT to have actually stuck (see _try_embed /
        # _verify_embed_stuck).
        #
        # Historical note, because an earlier version of this comment
        # asserted the opposite as settled fact: embedding DOES stick, and
        # has been re-confirmed from a separate process (the reparented
        # content window shows up under Shell_TrayWnd in EnumChildWindows,
        # visible, at the right rect, with the bar's real content readable
        # in a screen capture). The old "it silently reverts within
        # milliseconds" reading came from checking the wrong hwnd -- Tk's
        # wrapper window rather than the content window (see
        # _resolve_hwnd's docstring) -- which of course never got
        # reparented, because it was never the window passed to SetParent.
        #
        # This flag is still worth keeping as a fallback for systems or
        # future Windows builds where the reparent genuinely is refused:
        # retrying every second there would move the window to
        # embedded-style coordinates for the ~150ms verification window
        # each time before snapping back to the floating position, i.e. a
        # constant once-a-second flicker for no benefit. Once verification
        # fails it stops retrying until Explorer actually restarts (a
        # genuinely different situation worth one more fair attempt).
        self._embed_verified_failed = False

        self._panel_bg = FLOATING_PANEL_BG
        self._text_primary = TEXT_PRIMARY
        self._text_secondary = TEXT_SECONDARY
        self._track_color = "#1E1E1E"
        self._track_outline = TRACK_OUTLINE
        # Seeded so the first _watch_system_theme() tick is a no-op rather
        # than a redundant re-theme -- _apply_configured_theme() has
        # already run against this same value by then.
        self._last_system_light = _system_uses_light_theme()
        self._marker_color = "#FFFFFF"
        self._maintenance_tick_count = 0
        self._last_state: Optional[Union[UnifiedUsageState, ClaudeUsageState]] = None
        self._visible_row_count: Optional[int] = None
        self._is_refreshing = False

        self._embed_hover = False
        self._embed_lbutton_was_down = False
        self._embed_click_started_inside = False
        self._embed_click_was_icon = False
        self._embed_rbutton_was_down = False

        self._build_ui()
        self._bind_mouse_events()
        self._apply_configured_theme()

        # Deferred, not called synchronously here: main.py constructs this
        # widget before ever calling show() (tray/flyout come first, show()
        # comes later in start()). A synchronous first tick attempted
        # embedding immediately, on a window that was still withdrawn and
        # had never been positioned at all -- and since a "successful"
        # SetParent call optimistically set self._embedded True right away,
        # by the time show() ran moments later self._embedded already looked
        # True, so show()'s entire floating-fallback positioning branch
        # (snap_to_taskbar() etc.) got skipped every single time, leaving
        # the window whatever Tk's default placement happened to be.
        # Deferring even one event-loop turn lets show() run first and
        # establish a real position before any embedding is attempted.
        self.window.after(0, self._maintenance_tick)
        self.window.after(0, self._poll_embedded_input)

    def _resolve_hwnd(self) -> int:
        """Returns the real, visible top-level win32 window -- the one
        Explorer/EnumWindows/SetParent all need to agree on -- for this
        overrideredirect Toplevel.

        This used to try wm_frame() first: Tk's "wrapper window" concept,
        meant for a window manager's added decorative frame. That makes no
        sense for an overrideredirect window (there IS no WM-added frame by
        definition), and empirically it does NOT throw here as the except
        fallback below assumed -- it silently returns a DIFFERENT, unrelated
        Tk-internal handle (confirmed directly: winfo_id() and wm_frame()
        returned two completely different hwnds for the same window on this
        Tcl/Tk 8.6). Every win32 call in this class using that wrong handle
        was quietly a no-op: SetParent "succeeded" against some other window,
        so the actual visible bar never moved, never got reparented, and
        never had WS_EX_LAYERED cleared -- while genuinely looking, from a
        raw SetParent return value alone, like it had worked.

        winfo_id() is the actual content window's hwnd and is what needs to
        be passed to SetParent/GetWindowRect/etc. throughout this file."""
        try:
            return self.window.winfo_id()
        except Exception:
            return 0

    def _resolve_wrapper_hwnd(self) -> int:
        """Returns Tk's outer wrapper window for this Toplevel -- the OTHER
        half of the two win32 windows a Tk Toplevel is really made of (see
        _resolve_hwnd). Cached, because it stops being the content window's
        ancestor the moment the content window is reparented into the
        taskbar."""
        if self._wrapper_hwnd and ctypes.windll.user32.IsWindow(self._wrapper_hwnd):
            return self._wrapper_hwnd
        try:
            content = self._resolve_hwnd()
            if not content:
                return 0
            GA_PARENT = 1
            parent = ctypes.windll.user32.GetAncestor(content, GA_PARENT)
            # Accept the result only if it really is a Tk wrapper. Checking
            # "not Shell_TrayWnd" alone is not enough: after a detach
            # (Explorer restart -> SetParent(content, None)) the content
            # window's parent is the DESKTOP window, and caching that here
            # would mean _set_wrapper_click_through() going on to set
            # WS_EX_TRANSPARENT on the desktop itself.
            if parent and parent != self._shell_tray_hwnd:
                cls = ctypes.create_unicode_buffer(64)
                ctypes.windll.user32.GetClassNameW(parent, cls, 64)
                if cls.value == "TkTopLevel":
                    self._wrapper_hwnd = parent
            return self._wrapper_hwnd
        except Exception:
            return 0

    def _set_wrapper_click_through(self, enabled: bool):
        """Stops (or restores) the leftover Tk wrapper window from eating
        mouse clicks.

        SetParent moves only the CONTENT window into the taskbar. Tk's outer
        wrapper window stays behind as a normal top-level, still visible,
        still topmost, still exactly the size of the bar, sitting at the
        floating position the bar had before it embedded -- flush above the
        taskbar, near the right edge, overlapping the area above the tray
        icons. It is meant to draw nothing (its surface is supposed to be
        the -transparentcolor colorkey; see _set_wrapper_see_through() for
        why that does not reliably hold), and it is NOT click-through:
        confirmed directly with WindowFromPoint, which returned that wrapper
        across its entire width. Every click in that empty strip above the
        taskbar therefore went to a stale, contentless window instead of
        through to the desktop, and Tk reacted to it by re-laying-out the
        bar.

        WS_EX_TRANSPARENT is used rather than hiding or moving the wrapper
        because both of those break the embedded content, which is a
        separate window but still one Tk thinks it owns -- confirmed
        directly: ShowWindow(SW_HIDE) on the wrapper left the embedded
        content window unmapped (IsWindowVisible false, nothing rendered at
        all), and SetWindowPos'ing the wrapper off-screen dragged the
        embedded content along with it, out of its taskbar slot. Clipping
        the wrapper to an empty region (SetWindowRgn) did the same. Leaving
        the wrapper exactly where it is and only removing it from
        hit-testing is the one option that leaves the embedded content
        untouched."""
        wrapper = self._resolve_wrapper_hwnd()
        if not wrapper:
            return
        try:
            _set_ex_style_bit(wrapper, WS_EX_TRANSPARENT, enabled)
            self._wrapper_click_through = enabled
        except Exception:
            pass

    def _set_wrapper_see_through(self, enabled: bool):
        """Turns the leftover Tk wrapper window's opacity down to 0 (or back
        up to 255), so it cannot show on screen.

        The wrapper is not reliably invisible on its own. Nothing paints it
        once the content has gone, and it keys out TRANS_KEY (#000001), so
        whether it shows depends on what its surface happens to hold. Left
        alone that is nothing visible. But any part that gets drawn fresh
        comes up #000000 -- one step off the key -- and shows as a solid
        black box above the taskbar: topmost, the width of the bar, and,
        because the wrapper stays at the old floating position and grows
        rightwards, able to spill onto the neighbouring monitor.

        What redraws it is only partly pinned down, which is why this hides
        the window rather than trying to avoid the triggers. In an isolated
        reproduction set up like this bar, the one thing that turned it
        black was widening the canvas after the content had been shown
        inside the wrapper and then reparented away -- exactly the
        newly-added strip went black. With the content reparented before
        the first show, as this class does, nothing tried turned it black:
        growing, shrinking and regrowing, RedrawWindow, SWP_FRAMECHANGED,
        withdraw/deiconify. Yet the live wrapper was found black across its
        whole width, colorkey intact, after about 18 hours of running. A
        sleep/wake or display change is the likeliest cause; not confirmed.

        Opacity 0 hides the window whatever its surface holds, and unlike
        hiding or moving it (see _set_wrapper_click_through) it leaves the
        embedded content alone -- confirmed on the live bar: the box
        vanished and 2 pixels of the bar changed, both the countdown
        ticking. It stayed at 0 through later resizes, a Tk wm_attributes
        call, and a withdraw/deiconify cycle.

        Painting the wrapper with the key color instead was rejected: that
        means subclassing Tk's window procedure to answer WM_PAINT, then
        repainting after every resize, to rebuild by hand what opacity 0
        gives for free."""
        wrapper = self._resolve_wrapper_hwnd()
        if not wrapper:
            return
        _set_layered_alpha(wrapper, 0 if enabled else 255)

    def _force_topmost(self):
        if not self.window.winfo_exists() or not self.window.winfo_viewable():
            return
        if self.is_flyout_active and self.is_flyout_active():
            return
        try:
            hwnd = self._resolve_hwnd()
            if hwnd:
                HWND_TOPMOST = -1
                ctypes.windll.user32.SetWindowPos(
                    hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                    0x0002 | 0x0001 | 0x0010
                )
        except Exception:
            pass

    def _get_screen_rect(self):
        try:
            hwnd = self._resolve_hwnd()
            if not hwnd:
                return None
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            return None

    def _poll_embedded_input(self):
        if not self.window.winfo_exists():
            return
        if self._embedded and self.window.winfo_viewable():
            try:
                user32 = ctypes.windll.user32
                inside = self.is_cursor_inside()
                lbutton_down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
                rbutton_down = bool(user32.GetAsyncKeyState(0x02) & 0x8000)

                cx, cy = get_cursor_pos()
                rect = self._get_screen_rect()
                over_icon = False
                if rect and inside:
                    wx, wy, wx2, wy2 = rect
                    over_icon = self._is_icon_x(cx - wx)

                if over_icon != self._icon_hovered:
                    self._icon_hovered = over_icon
                    self._update_icon()
                    if over_icon:
                        self._show_icon_tooltip()
                    else:
                        self._hide_icon_tooltip()

                if inside != self._embed_hover:
                    self._embed_hover = inside
                    if inside:
                        self._force_topmost()
                        if self.cancel_flyout_hide:
                            self.cancel_flyout_hide()
                    else:
                        self._hide_icon_tooltip()
                        if self.schedule_flyout_hide and self.is_flyout_active and self.is_flyout_active():
                            self.schedule_flyout_hide(delay_ms=350, is_cursor_in_bar_fn=self.is_cursor_inside)

                # Left Click Detection
                if lbutton_down and not self._embed_lbutton_was_down:
                    self._embed_click_started_inside = inside
                    self._embed_click_was_icon = over_icon
                elif not lbutton_down and self._embed_lbutton_was_down:
                    if self._embed_click_started_inside and inside:
                        if self._embed_click_was_icon:
                            self._hide_icon_tooltip()
                            self.on_refresh()
                        else:
                            self._handle_bars_click()
                    self._embed_click_started_inside = False
                    self._embed_click_was_icon = False
                self._embed_lbutton_was_down = lbutton_down

                # Right Click Detection -> Suppress Explorer menu
                if rbutton_down and not self._embed_rbutton_was_down:
                    if inside:
                        self._dismiss_explorer_menu()
                self._embed_rbutton_was_down = rbutton_down

            except Exception:
                pass

        self.window.after(50, self._poll_embedded_input)

    def is_cursor_inside(self) -> bool:
        rect = self._get_screen_rect()
        if not rect:
            return False
        left, top, right, bottom = rect
        cx, cy = get_cursor_pos()
        return left <= cx <= right and top <= cy <= bottom

    def _maintenance_tick(self):
        if not self.window.winfo_exists():
            return
        self._maintenance_tick_count += 1
        self._watch_system_theme()

        if not self._embedded:
            # While not embedded, the window needs to be actively kept on top
            # (see _force_topmost) -- a floating "always on top" window loses
            # that fight the instant the real taskbar gets focus, so this has
            # to be reasserted continuously, not just once. Position itself
            # is NOT recomputed every tick here -- snap_to_taskbar() runs
            # once in show() and again if Explorer restarts below, then the
            # window just stays put (or follows a drag) until the next of
            # those triggers.
            if self._embed_verified_failed:
                # Genuine embedding was already tried and confirmed not to
                # stick (see _verify_embed_stuck) -- don't retry every
                # second, since each attempt briefly moves the window to
                # embedded-style coordinates before the ~150ms verification
                # catches the failure, which would be a constant once-a-
                # second flicker for no benefit. Explorer restarting is a
                # genuinely different situation, so watch for that (the old
                # Shell_TrayWnd handle going invalid) and give it one more
                # fair attempt when it happens.
                if self._shell_tray_hwnd and not ctypes.windll.user32.IsWindow(self._shell_tray_hwnd):
                    self._embed_verified_failed = False
                    self._shell_tray_hwnd = 0
                self._reclamp_floating_position()
                self._force_topmost()
            elif not ATTEMPT_GENUINE_EMBED or not self._try_embed():
                self._reclamp_floating_position()
                self._force_topmost()
        else:
            if not ctypes.windll.user32.IsWindow(self._shell_tray_hwnd):
                # Explorer restarted -- our old Shell_TrayWnd handle is dead.
                # Detach cleanly and re-snap to the floating flush-above-
                # taskbar position until re-embedding succeeds (attempted
                # above on the next tick, since self._embedded is now False).
                self._embedded = False
                self._shell_tray_hwnd = 0
                try:
                    ctypes.windll.user32.SetParent(self._resolve_hwnd(), None)
                except Exception:
                    pass
                self._undo_embed_window_styles()
                self._reset_to_floating_theme()
                self.snap_to_taskbar()
                self._apply_rounded_corners()
            else:
                self._reassert_embed_window_styles()
                self._reposition_embedded()

        self.window.after(1000, self._maintenance_tick)

    def _watch_system_theme(self):
        """Re-themes the bar if Windows switches its own light/dark theme.

        Only matters with a transparent background, where Windows' setting
        decides which way the text goes (see _backdrop_estimate). Without
        this, flipping Windows from light to dark leaves the bar with dark
        text on what is now a dark taskbar until the app is restarted or
        Settings is touched -- i.e. an invisible bar, arrived at without the
        user changing anything in this app at all.

        Polled every 10 ticks (~10s) rather than every tick: this reads the
        registry, and nobody flips their system theme often enough to need
        it checked once a second."""
        if self._maintenance_tick_count % 10 != 1:
            return
        current = _system_uses_light_theme()
        if current != self._last_system_light:
            self._last_system_light = current
            self._apply_configured_theme()

    def _try_embed(self) -> bool:
        try:
            shell_hwnd = ctypes.windll.user32.FindWindowW(SHELL_TRAY_CLASS, None)
            if not shell_hwnd:
                return False

            our_hwnd = self._resolve_hwnd()
            if not our_hwnd:
                return False

            # Must be resolved BEFORE the SetParent below -- afterwards this
            # window's ancestor is Shell_TrayWnd and the wrapper can no
            # longer be found from it.
            self._resolve_wrapper_hwnd()

            result = ctypes.windll.user32.SetParent(our_hwnd, shell_hwnd)
            if not result:
                # SetParent returns NULL on failure. Without this check,
                # self._embedded got set True unconditionally below -- so a
                # failed reparent still looked "embedded" to the rest of this
                # class, and _reposition_embedded() then ran its
                # parent-relative coordinate math against a window that was
                # actually still top-level, landing it at a plausible-looking
                # but wrong screen position instead of failing visibly.
                return False

            # Deliberately does NOT call self.window.wm_attributes(
            # "-transparentcolor", "") here. Clearing the raw WS_EX_LAYERED
            # bit directly on the content hwnd below is enough on its own to
            # make the embedded content render -- confirmed directly.
            # Clearing Tk's own -transparentcolor attribute was tried too,
            # on the theory that Tk might reassert the layered bit from its
            # own tracked state otherwise, but it made things WORSE: Tk
            # creates a second, independent top-level HWND alongside this
            # one (same title, no win32 parent/owner link back to this
            # window at all -- confirmed via EnumWindows) that it keeps
            # showing in parallel, sized and positioned like the old
            # floating panel. That second window is normally invisible
            # (colorkey-transparent, like this one), but clearing the
            # Tk-level attribute here turns IT opaque too, showing as a
            # solid near-black rectangle floating above the taskbar with
            # nothing ever drawn into it. Every attempt to hide or banish
            # that second window directly (ShowWindow, SetWindowPos) instead
            # broke the real embedded content's rendering, timing-dependent
            # and highly fragile -- leaving its transparency alone entirely
            # avoids the problem at the source: not clearing it here is part
            # of what keeps that window invisible. Only part -- it can still
            # come up black with the colorkey intact; see
            # _set_wrapper_see_through().
            #
            # This used to CLEAR WS_EX_LAYERED on the content window
            # instead. That did make the embedded content appear -- but
            # appear additively blended into the taskbar behind it, which is
            # the actual cause of the long-standing "looks fine on a dark
            # taskbar, washes out to nothing on a light one" bug. Making it
            # an opaque layered window instead renders it pixel-exact; see
            # _make_embedded_layered() for the measurements.
            _make_embedded_layered(our_hwnd, self._embed_colorkey())

            # The wrapper is left behind by SetParent, still swallowing
            # clicks in the strip above the taskbar and liable to show there
            # as a black box. See _set_wrapper_click_through() and
            # _set_wrapper_see_through().
            self._set_wrapper_click_through(True)
            self._set_wrapper_see_through(True)

            # SetParent can clear WS_VISIBLE as a side effect of reparenting
            # out from under the desktop -- confirmed directly: after a
            # "successful" SetParent + correct positioning, GetWindowLongW
            # showed WS_VISIBLE unset on both this window and its child
            # Canvas widget, so nothing could ever render regardless of
            # content, no matter how many times paint was forced. Neither
            # _reposition_embedded()'s SetWindowPos (SWP_NOZORDER |
            # SWP_NOACTIVATE, no SWP_SHOWWINDOW) nor anything else in this
            # class ever re-asserted it.
            SW_SHOW = 5
            ctypes.windll.user32.ShowWindow(our_hwnd, SW_SHOW)

            self._shell_tray_hwnd = shell_hwnd
            self._embedded = True

            self._clear_window_region()
            self._reposition_embedded()
            self._apply_configured_theme()
            self._force_full_repaint()
            # A second pass, slightly delayed: the isolated reproduction
            # that validated _force_full_repaint() needed a real time gap
            # (~200ms) between the two update() calls for it to take
            # effect, not just two calls back to back -- giving DWM/Explorer
            # a moment to actually process the paint message Tk queues,
            # not just queuing it.
            self.window.after(250, self._force_full_repaint)
            self.window.after(600, self._force_full_repaint)

            # Don't trust this yet -- a SetParent return value alone isn't
            # proof it actually stuck (confirmed directly: it can revert
            # within well under 50ms, before any external observer, Explorer
            # included, ever sees the reparented state). Verify shortly
            # after control returns to Tk's own event loop.
            self.window.after(150, lambda: self._verify_embed_stuck(our_hwnd, shell_hwnd))
            return True
        except Exception as e:
            print(f"[TaskbarBar] Embedding failed: {e}")
            self._embedded = False
            return False

    def _verify_embed_stuck(self, our_hwnd: int, shell_hwnd: int, checks_left: int = 5):
        if not self._embedded or self._shell_tray_hwnd != shell_hwnd:
            return  # already detached/re-tried by something else in the meantime
        GA_PARENT = 1
        actual_parent = ctypes.windll.user32.GetAncestor(our_hwnd, GA_PARENT)
        if actual_parent == shell_hwnd:
            if checks_left > 0:
                # Several agreeing checks spread over a full second, not
                # one snapshot. This was originally written to catch a
                # "revert" that turned out not to be real (it was the wrong
                # hwnd being checked -- see the note on
                # _embed_verified_failed), but re-checking a few times is
                # still the right shape for the case it now actually
                # guards: a system where the reparent really is refused or
                # undone, where a single lucky snapshot would skip the
                # floating fallback's positioning/re-clamp logic for a
                # window that never renders embedded.
                self.window.after(200, lambda: self._verify_embed_stuck(our_hwnd, shell_hwnd, checks_left - 1))
            return  # (still) looks stuck -- nothing to do yet
        # Reverted. Stop believing we're embedded and fall back to the
        # floating panel immediately rather than leaving the window at
        # embedded-style coordinates that no longer mean anything once its
        # real parent silently changed back.
        self._embedded = False
        self._embed_verified_failed = True
        self._undo_embed_window_styles()
        self._reset_to_floating_theme()
        self.snap_to_taskbar()
        self._apply_rounded_corners()
        # The SetParent(shell_hwnd) call in _try_embed() -- even though it
        # reverted -- leaves Tk believing it already painted this window.
        # Confirmed directly (PrintWindow capture + a live desktop
        # screenshot both agreed): after this revert, the window kept
        # showing Windows' own blank placeholder (just the raw title text)
        # forever after, never the actual Canvas content, even though
        # nothing was ever wrong with the canvas itself -- only a genuine
        # WM_PAINT, which Tk never re-requests on its own after a raw win32
        # SetParent, would fix it. _try_embed()'s OWN success path already
        # knows this (see _force_full_repaint()'s docstring) and forces two
        # repaints after embedding -- this revert path took the same
        # SetParent detour (embed, then SetParent back out) but never did
        # the same, so the exact same fix belongs here too.
        self._force_full_repaint()
        self.window.after(250, self._force_full_repaint)

    def _undo_embed_window_styles(self):
        """Reverses everything _try_embed() changed at the win32 level, so a
        fall back to the floating panel gets a plain window again.

        All three matter. Leaving WS_EX_TRANSPARENT set on the wrapper
        would make the FLOATING bar click-through -- invisible to every
        click, so the icon-click refresh and the click-to-open-flyout would
        both silently stop working, with nothing on screen to explain why.
        Leaving the wrapper at opacity 0 would be worse: wherever the
        content is back inside it, the floating bar would not show at all
        (confirmed: a floating test window at opacity 0 showed nothing).
        Leaving the opaque-layered attributes on the content window would
        fight Tk's own colorkey transparency, which the floating panel needs
        for its rounded corners and see-through background."""
        self._set_wrapper_click_through(False)
        self._set_wrapper_see_through(False)
        try:
            hwnd = self._resolve_hwnd()
            if hwnd:
                _set_layered(hwnd, False)
        except Exception:
            pass

    def _reassert_embed_window_styles(self):
        """Re-applies the opaque-layered attributes if something cleared
        them while embedded.

        Tk re-applies its own -transparentcolor (i.e. a colorkey, not
        LWA_ALPHA) to this window on its own schedule -- any wm_attributes
        call, a deiconify, and _reset_to_floating_theme() all do it -- and
        the moment it does, the embedded bar silently goes back to being
        additively blended into the taskbar. Cheap to check (one
        GetLayeredWindowAttributes) and only actually writes when it has
        drifted, so this does not cause a repaint every tick."""
        if not self._embedded:
            return
        hwnd = self._resolve_hwnd()
        key = self._embed_colorkey()
        if hwnd and not _has_layered_attrs(hwnd, key):
            _make_embedded_layered(hwnd, key)
        if not self._wrapper_click_through:
            self._set_wrapper_click_through(True)
        # Read back from the window rather than trusted to stick, like the
        # content window above: these are Tk's layered attributes, not ours.
        # Not actually seen to drift -- a withdraw/deiconify cycle and a
        # wm_attributes call both left the opacity at 0 in testing -- but if
        # it ever did, the black box would come back with nothing to undo it.
        wrapper = self._resolve_wrapper_hwnd()
        attrs = _read_layered_attrs(wrapper) if wrapper else None
        if attrs and not (attrs[2] & LWA_ALPHA and attrs[1] == 0):
            self._set_wrapper_see_through(True)

    def _force_full_repaint(self):
        """Forces Tk to fully reprocess geometry and paint after a raw
        SetParent embed.

        Confirmed directly with an isolated minimal reproduction (a single
        Label + a single Canvas, embedded the same way): right after
        SetParent, content doesn't render at all -- not even background
        color, just a stray default-colored rectangle. It starts rendering
        once Tk is forced through an extra update_idletasks()+update()
        cycle post-embed (Tk's own painting was simply never triggered for
        the reparented content by the raw win32 SetParent call, since Tk
        doesn't know anything changed)."""
        try:
            self.window.update_idletasks()
            self.window.update()
        except Exception:
            pass

    def _reposition_embedded(self):
        try:
            hwnd = self._resolve_hwnd()
            if not hwnd or not self._shell_tray_hwnd:
                return
            # Deliberately does NOT re-issue SetParent here. A single call
            # in _try_embed() is enough -- it genuinely holds (confirmed
            # directly by checking the true content hwnd, not the Tk
            # wrapper window that keeps the title; see _resolve_hwnd's
            # docstring). Reasserting on every reposition was tried while
            # chasing what turned out to be a hwnd-identification bug, not a
            # real revert, and added nothing once that bug was fixed.
            tray_rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self._shell_tray_hwnd, ctypes.byref(tray_rect))

            notify_hwnd = ctypes.windll.user32.FindWindowExW(self._shell_tray_hwnd, None, TRAY_NOTIFY_CLASS, None)
            notify_rect = wintypes.RECT()
            if notify_hwnd:
                ctypes.windll.user32.GetWindowRect(notify_hwnd, ctypes.byref(notify_rect))
            else:
                notify_rect.left = tray_rect.right

            w = self.window.winfo_reqwidth()
            h = self.window.winfo_reqheight()
            tray_h = tray_rect.bottom - tray_rect.top

            x = (notify_rect.left - w - EMBED_GAP) - tray_rect.left
            y = ((tray_h - h) // 2) if tray_h > h else 0

            # SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW -- the show flag
            # is a defensive backup for the explicit ShowWindow() call in
            # _try_embed(); see the comment there for why it's needed at all.
            ctypes.windll.user32.SetWindowPos(hwnd, None, x, y, w, h, 0x0004 | 0x0010 | 0x0040)
        except Exception:
            pass

    def _clear_window_region(self):
        try:
            hwnd = self._resolve_hwnd()
            if hwnd:
                ctypes.windll.user32.SetWindowRgn(hwnd, None, True)
        except Exception:
            pass

    def _apply_theme(self, panel_bg: str, text_primary: str, text_secondary: str,
                     track_color: str, marker_color: str,
                     track_outline: str = TRACK_OUTLINE):
        self._panel_bg = panel_bg
        self._text_primary = text_primary
        self._text_secondary = text_secondary
        self._track_color = track_color
        self._marker_color = marker_color
        self._track_outline = track_outline

        if hasattr(self, "canvas") and self.canvas.winfo_exists():
            self.canvas.configure(bg=panel_bg)
            self.canvas.itemconfigure(self.countdown_track_item,
                                      fill=self._countdown_track_color())
            self._update_icon()
            for row in self.bar_rows:
                self.canvas.itemconfigure(row["tag_item"], fill=text_secondary)
                self.canvas.itemconfigure(row["track_item"], fill=track_color, outline=track_outline)
                self.canvas.itemconfigure(row["marker_item"], fill=marker_color)
                # The rail's own two neutral tones follow the sampled theme
                # the same way the track and needle do. Its red parts do not
                # -- they go through _legible_marking_color() per update, for
                # the same reason the percentage text does.
                self.canvas.itemconfigure(row["rail_track_item"], fill=track_outline)
                self.canvas.itemconfigure(row["rail_elapsed_item"], fill=text_secondary)
            self.canvas.configure(highlightthickness=0 if self._embedded else 1)
            # The 1px floating-mode border used to always be the constant
            # BORDER_CARD, never touched by theme sampling -- so even a
            # perfect background color match still traced a visible fixed
            # gray rectangle around the panel. Matching it to the sampled
            # panel_bg makes the edge blend in the same way the fill does.
            self.canvas.configure(highlightbackground=panel_bg, highlightcolor=panel_bg)

        # The colorkey IS panel_bg in transparent mode, so a theme change is
        # also a win32 change, not just a canvas one. Skipping this leaves
        # the OLD color keyed out -- which means the new background paints
        # solid while stray pixels of the old color anywhere on the canvas
        # go see-through, i.e. exactly backwards.
        if self._embedded:
            hwnd = self._resolve_hwnd()
            if hwnd:
                _make_embedded_layered(hwnd, self._embed_colorkey())

        if self._last_state is not None:
            self.update_state(self._last_state)

    def _countdown_track_color(self) -> str:
        """The always-drawn faded outline under the countdown.

        Blended from the panel toward the same color the countdown itself
        uses, rather than reusing TRACK_OUTLINE the way the usage bars do.
        TRACK_OUTLINE is a fixed dark gray, which on a LIGHT panel is darker
        than text_secondary -- so a fixed track would out-shout the live
        part it is supposed to sit behind, i.e. exactly backwards. Deriving
        it keeps "track is a faded version of the mark" true in both
        themes. The live countdown is drawn at 2px over this 1px track,
        giving the active fill distinct weight and mass over the unfilled track.

        0.30 was picked by rendering 0.45/0.38/0.30/0.22/0.15 side by side
        on both a #1C1B1A and a #F3F3F3 panel, not by taste. The two themes
        genuinely want different numbers and 0.30 is the compromise: dark
        wants roughly 0.22, because blending UP from near-black reaches
        text_secondary's brightness early and a high value leaves the track
        nearly as bright as the live segment; light wants roughly 0.40,
        because blending DOWN from near-white barely darkens at all at low
        values and the track disappears entirely -- which would lose the
        closed box that stops a part-drawn perimeter reading as a glitch.
        Anything outside about 0.25-0.40 visibly fails one theme or the
        other, so re-check both if this is ever touched."""
        return blend_hex(self._panel_bg, self._text_secondary, 0.30)

    def _countdown_points(self, frac: float) -> list:
        """Perimeter path from the top edge's midpoint, clockwise, covering
        `frac` of the way round. Walks whatever _countdown_polygon() gives
        it, so the shape is defined in exactly one place."""
        poly = _countdown_polygon(self._icon_cx, self._icon_cy)
        want = COUNTDOWN_PERIMETER * max(0.0, min(1.0, frac))
        pts = [poly[0][0], poly[0][1]]
        for (ax, ay), (bx, by) in zip(poly, poly[1:]):
            if want <= 0:
                break
            leg = math.hypot(bx - ax, by - ay)
            t = min(1.0, want / leg)
            pts.extend([ax + (bx - ax) * t, ay + (by - ay) * t])
            want -= leg
        return pts

    def _update_countdown(self):
        """Redraws the refresh countdown. Called on every update_state(),
        which main.py's tick() already runs once a second while the bar is
        visible -- so this costs one coords() call per second and needs no
        timer of its own."""
        if not hasattr(self, "countdown_item") or not self.canvas.winfo_exists():
            return
        if self.get_refresh_progress is None:
            self.canvas.itemconfigure(self.countdown_item, state="hidden")
            self.canvas.itemconfigure(self.countdown_track_item, state="hidden")
            return

        try:
            elapsed, fetching = self.get_refresh_progress()
        except Exception:
            return

        if COUNTDOWN_STYLE == "needle":
            self._update_needle_countdown(1.0 - max(0.0, min(1.0, elapsed)), fetching)
            return

        self.canvas.itemconfigure(self.countdown_track_item, state="normal")

        # Drains rather than fills: the outline is whole the instant fresh
        # data lands and is gone by the time the next fetch is due, so the
        # remaining perimeter IS the remaining time. The head retreats back
        # towards the top-middle start point, the way a kitchen timer
        # unwinds, instead of the tail chasing it round.
        remaining = 1.0 - max(0.0, min(1.0, elapsed))

        if fetching:
            # A fetch is in flight: show the whole perimeter in amber. Left
            # to the normal rule this moment would draw NOTHING (a fetch is
            # due exactly when remaining has reached zero), so the one time
            # the widget is actually doing something would be its blankest.
            # It also makes a hung fetch visible -- amber that never clears
            # rather than an empty outline that looks merely idle.
            self.canvas.coords(self.countdown_item, *self._countdown_points(1.0))
            self.canvas.itemconfigure(self.countdown_item, fill=WARNING_AMBER, state="normal")
            return

        if remaining <= 0.0:
            self.canvas.itemconfigure(self.countdown_item, state="hidden")
            return

        fg = self._text_primary if self._icon_hovered else self._text_secondary
        self.canvas.coords(self.countdown_item, *self._countdown_points(remaining))
        self.canvas.itemconfigure(self.countdown_item, fill=fg, state="normal")

    def _update_needle_countdown(self, remaining: float, fetching: bool):
        """The needle version of the countdown (COUNTDOWN_STYLE "needle").

        Same rules as the octagon, on a shorter track: whole the moment fresh
        data lands, then drains so the bright part left IS the time left;
        amber for the whole length while a fetch is in flight (a hung fetch
        stays visibly amber); only the grey track once a fetch is due.

        It drains from the BOTTOM: the bright part stays anchored at the top,
        so the needle keeps crossing the coral quota rail -- the part that
        makes the mark recognisable -- for as long as possible. Draining from
        the top was tried first and turned the mark into a different-looking
        logo for half of every cycle.

        Drawn in the row needles' own theme-derived colour (_marker_color),
        not fixed white, so it stays visible on a light taskbar too."""
        ix0, iy0, ix1, iy1 = self._needle_inner
        if fetching:
            self.canvas.coords(self.needle_fill, ix0, iy0, ix1, iy1)
            self.canvas.itemconfigure(self.needle_fill, fill=WARNING_AMBER, state="normal")
            return
        if remaining <= 0.0:
            self.canvas.itemconfigure(self.needle_fill, state="hidden")
            return
        # Whole pixels, so the solid part grows and shrinks a row at a time
        # instead of Tk rounding a fractional edge differently each tick.
        self.canvas.coords(self.needle_fill, ix0, iy0, ix1, iy0 + round((iy1 - iy0) * remaining))
        self.canvas.itemconfigure(self.needle_fill, fill=self._marker_color, state="normal")

    def _update_icon(self):
        if not hasattr(self, "canvas") or not self.canvas.winfo_exists():
            return
        coral = CLAUDE_CORAL_HOVER if self._icon_hovered else CLAUDE_CORAL
        time_fill = self._text_primary if self._icon_hovered else self._text_secondary
        if hasattr(self, "icon_top_track"):
            # The quota rail is hollow where the bars are (track_color is the
            # colorkey in transparent mode) and the time rail's unfilled part
            # is the rows' rail_track_item colour; see _apply_theme.
            self.canvas.itemconfigure(self.icon_top_track, fill=self._track_color,
                                      outline=self._track_outline)
            self.canvas.itemconfigure(self.icon_bot_track, fill=self._track_outline)
        if hasattr(self, "icon_top_fill"):
            self.canvas.itemconfigure(self.icon_top_fill, fill=coral)
        if hasattr(self, "icon_bot_fill"):
            self.canvas.itemconfigure(self.icon_bot_fill, fill=time_fill)
        if hasattr(self, "icon_needle"):
            if COUNTDOWN_STYLE == "needle":
                # The hollow gauge's outline and hollow inside follow the
                # theme; see the needle block in _build_ui.
                self.canvas.itemconfigure(self.icon_needle, outline=self._marker_color,
                                          fill=self._track_color)
            else:
                self.canvas.itemconfigure(self.icon_needle, fill="#FFFFFF")
        self._update_countdown()

    def _on_icon_enter(self):
        self._icon_hovered = True
        self._update_icon()
        self._show_icon_tooltip()

    def _on_icon_leave(self):
        self._icon_hovered = False
        self._update_icon()
        self._hide_icon_tooltip()

    def _show_icon_tooltip(self):
        if self._tooltip_win and self._tooltip_win.winfo_exists():
            return
        self._tooltip_win = tk.Toplevel(self.master)
        self._tooltip_win.overrideredirect(True)
        self._tooltip_win.wm_attributes("-topmost", True)

        t_bg = "#202124" if not is_light_color(self._panel_bg) else "#FFFFFF"
        t_fg = "#F8F9FA" if not is_light_color(self._panel_bg) else "#202124"
        t_muted = "#9AA0A6" if not is_light_color(self._panel_bg) else "#5F6368"
        t_border = "#3C4043" if not is_light_color(self._panel_bg) else "#DADCE0"

        outer = tk.Frame(self._tooltip_win, bg=t_border, padx=1, pady=1)
        outer.pack()
        box = tk.Frame(outer, bg=t_bg, padx=8, pady=5)
        box.pack()

        hdr = tk.Label(box, text="∿ TwinRails", font=(FONT_FAMILY, 9, "bold"), fg=t_fg, bg=t_bg)
        hdr.pack(anchor="w")

        ago_text = "Just now"
        if self._last_state and self._last_state.last_updated:
            ago_text = format_ago(self._last_state.last_updated)

        body = tk.Label(box, text=f"Click to refresh  ·  Updated {ago_text}", font=(FONT_FAMILY, 8), fg=t_muted, bg=t_bg)
        body.pack(anchor="w", pady=(1, 0))

        self._tooltip_win.update_idletasks()
        tw = self._tooltip_win.winfo_reqwidth()
        th = self._tooltip_win.winfo_reqheight()

        rect = self._get_screen_rect()
        if rect:
            bx, by, bx2, by2 = rect
            tx = bx + 4
            ty = by - th - 6
            if ty < 0:
                ty = by2 + 6
            self._tooltip_win.geometry(f"+{tx}+{ty}")

    def _hide_icon_tooltip(self):
        if self._tooltip_win and self._tooltip_win.winfo_exists():
            self._tooltip_win.destroy()
            self._tooltip_win = None

    def _configured_bg_color(self) -> str:
        """The saved color for the taskbar Windows is showing right now: the
        light one on a light taskbar, the dark one on a dark taskbar.

        This used to follow a manual Dark/Light setting, flipped from Settings
        or the flyout's Theme button. Removed 2026-09-25: Windows already
        knows, _backdrop_estimate() overruled any disagreement anyway, and
        _watch_system_theme() re-applies the theme when the user switches.
        bar_theme_mode is only the fallback when the registry can't be read."""
        cfg = self.cfg_mgr.config
        light = _system_uses_light_theme()
        if light is None:
            light = getattr(cfg, "bar_theme_mode", "dark") == "light"
        return cfg.bar_bg_color_light if light else cfg.bar_bg_color_dark

    def _wants_transparent_bg(self) -> bool:
        return bool(getattr(self.cfg_mgr.config, "bar_transparent_bg", True))

    def _backdrop_estimate(self) -> str:
        """Best guess at what is actually behind the bar, used only to keep
        the text legible against it.

        Normally just the configured color -- it is meant to match the
        user's taskbar, so it is the right thing to contrast against.

        The exception is what makes transparent mode safe to ship. With a
        painted panel, a badly-chosen color is merely ugly: the text is
        still derived from the same color that gets painted, so it always
        matches whatever is actually on screen. With no panel, a color that
        disagrees with the real taskbar is actively harmful -- a dark
        configured color on a light taskbar yields WHITE text drawn onto a
        near-white taskbar, i.e. a bar that vanishes completely. That is a
        real scenario, not a hypothetical: a color set purely as a test
        (#0000FF, which reads as "dark" at luminance 0.11) sitting in a
        config alongside a light taskbar is exactly how this feature would
        have been met.

        So Windows gets the final say on light-vs-dark, since it actually
        knows. If the configured color contradicts it, its brightness is
        discarded and a neutral of the correct brightness is contrasted
        against instead. The configured color is still used as the
        colorkey either way -- that part has to match what is painted."""
        color = self._configured_bg_color()
        system_light = _system_uses_light_theme()
        if system_light is None or is_light_color(color) == system_light:
            return color
        return "#F3F3F3" if system_light else "#1C1B1A"

    def _embed_colorkey(self) -> Optional[str]:
        """Which color, if any, the embedded window should punch out to
        transparency. None means a solid painted panel.

        Only ever applies while embedded. The floating fallback gets its
        transparency from Tk's own -transparentcolor on the wrapper window
        instead, and giving the content window a second, different colorkey
        on top of that fights it."""
        if not self._embedded or not self._wants_transparent_bg():
            return None
        return self._panel_bg

    def _apply_configured_theme(self):
        """Applies the saved background color for the current Windows taskbar
        theme (Settings -> Taskbar Bar Appearance; see _configured_bg_color)
        -- the replacement for the old GetPixel-sampled auto color match.

        What that color means depends on the background mode:

        - Transparent (default): the color is never painted. It is the
          colorkey, and it stands in for "what is behind the bar" when
          deriving text/track/marker colors that have to stay legible
          against the real taskbar. See theme.transparent_bar_theme().
        - Solid: the color is painted as the panel, and the foreground
          colors only ever have to work against it. See
          theme.adaptive_bar_theme().

        Both paths end in _apply_theme(), which re-applies the layered
        window attributes -- necessary because the colorkey IS this color,
        so changing it has to be pushed down to win32, not just to the
        canvas."""
        color = self._configured_bg_color()
        theme = (transparent_bar_theme(self._backdrop_estimate())
                 if self._wants_transparent_bg() else adaptive_bar_theme(color))
        self._apply_theme(
            panel_bg=theme["panel_bg"],
            text_primary=theme["text_primary"],
            text_secondary=theme["text_secondary"],
            track_color=theme["track_color"],
            marker_color=theme["marker_color"],
            track_outline=theme.get("track_outline", TRACK_OUTLINE),
        )

    def refresh_theme(self):
        """Public: re-applies the background. Call after Settings saves new
        colors or a transparency change, so it shows without a restart."""
        self._apply_configured_theme()

    def _reset_to_floating_theme(self):
        # Reasserts colorkey transparency defensively on every fallback to
        # floating. _try_embed() no longer touches Tk's own -transparentcolor
        # attribute at all (see the comment there), only the raw
        # WS_EX_LAYERED bit on the content hwnd directly -- but if that ever
        # changes again, skipping this restore would be a real visible bug:
        # the floating panel would render as a solid opaque black rectangle
        # (TRANS_KEY's near-black colorkey with no transparency behind it,
        # rather than showing through to the desktop/taskbar with real
        # content drawn on top).
        self.window.wm_attributes("-transparentcolor", TRANS_KEY)
        self._apply_configured_theme()

    def snap_to_taskbar(self):
        """Places the floating fallback flush against the primary taskbar's
        top edge, near the tray/clock cluster, so it reads as part of the
        taskbar rather than a stray rectangle sitting wherever Tk happened to
        put it by default.

        This -- along with the equivalent logic in show() and _on_release()
        -- existed in the pre-unification version of this file and was lost
        when this class was rewritten for the multi-provider architecture:
        nothing here ever called self.window.geometry() for the non-embedded
        case, so the floating window was left at Tk's arbitrary default
        Toplevel placement. On a multi-monitor setup that can land far from
        the real taskbar entirely (confirmed: top edge of a secondary
        monitor, nowhere near Shell_TrayWnd). No-ops while genuinely
        embedded -- position there is fully automatic."""
        if self._embedded:
            return
        self.window.update_idletasks()
        w = self.window.winfo_reqwidth()
        h = self.window.winfo_reqheight()
        wl, wt, wr, wb = get_work_area(self._resolve_hwnd() or self.window.winfo_id())

        # Anchored near the right edge (where tray widgets/clock naturally
        # sit), touching the taskbar's top edge with zero gap.
        x = wr - w - 16
        y = wb - h
        self.window.geometry(f"+{x}+{y}")
        self.cfg_mgr.config.docked_bar_x = x
        self.cfg_mgr.config.docked_bar_y = y
        self.cfg_mgr.save()
        self._force_topmost()

    def _reclamp_floating_position(self):
        """Keeps the floating panel's right edge pinned to the work area.

        snap_to_taskbar() runs once, early in startup, before the first
        real usage fetch completes -- at that point the rows still show
        placeholder "--" text, measurably narrower than real content
        (confirmed: ~284px placeholder vs ~440px with real percentages and
        countdowns). Its anchor position is computed from THAT width, so
        once real data loads and the window grows wider, the extra width
        extended the right edge past the actual screen edge instead of
        extending further left, where the original anchor already left
        room. Called every maintenance tick (~1/s) while floating and
        unlocked so any width change gets caught, not just the first one.
        """
        if self.is_locked:
            return
        # No self._embedded gate here on purpose, but NOT for the reason an
        # earlier version of this comment gave (it claimed embedding never
        # really holds, which was wrong -- see the note on
        # _embed_verified_failed). Every caller of this method is already
        # on a not-embedded branch, so gating again would be dead code;
        # what actually matters is that this is only ever reached while
        # floating, where re-clamping the right edge is always the correct
        # thing to do.
        self.window.update_idletasks()
        wl, wt, wr, wb = get_work_area(self._resolve_hwnd() or self.window.winfo_id())
        # Uses raw GetWindowRect for BOTH position and width, not Tk's own
        # winfo_x()/winfo_y()/winfo_reqwidth() tracking. Those go stale once
        # SetParent has touched this window (even a failed/reverted embed
        # attempt -- _try_embed() always calls it), and separately,
        # winfo_reqwidth() was confirmed to report a stale, too-small value
        # here after _reposition_embedded() raw-SetWindowPos'd the window to
        # an explicit size while self._embedded was (wrongly, momentarily)
        # True -- Tk's "requested size" bookkeeping doesn't necessarily
        # track a size that was set by a raw win32 call bypassing its own
        # geometry manager. GetWindowRect reflects reality regardless.
        rect = self._get_screen_rect()
        if not rect:
            return
        cur_x, cur_y = rect[0], rect[1]
        w = rect[2] - rect[0]
        if cur_x + w > wr or cur_x < wl:
            new_x = max(wl, wr - w - 16)
            self.window.geometry(f"+{new_x}+{cur_y}")
            self.cfg_mgr.config.docked_bar_x = new_x
            self.cfg_mgr.config.docked_bar_y = cur_y
            self.cfg_mgr.save()

    def _apply_rounded_corners(self, radius: int = 10):
        """Clips the floating window to a rounded rectangle -- matches the
        look of Windows 11's own taskbar flyouts (WiFi/volume), reinforcing
        that this panel belongs to the taskbar rather than being a plain
        floating rectangle. No-op once embedded (SetWindowRgn is cleared by
        _clear_window_region there instead)."""
        try:
            hwnd = self._resolve_hwnd()
            if not hwnd:
                return
            w = self.window.winfo_width()
            h = self.window.winfo_height()
            if w <= 1 or h <= 1:
                return
            hrgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius, radius)
            if hrgn:
                ctypes.windll.user32.SetWindowRgn(hwnd, hrgn, True)
        except Exception:
            pass

    def _build_ui(self):
        # Must run before any width below is read -- see their docstrings.
        # Icon first: the text calibration folds ICON_W into the canvas width.
        _calibrate_icon(self.window)
        _calibrate_text_slots(self.window)
        # No border allowance: the 1px highlight below is dropped once the
        # bar is embedded (see _apply_theme), and embedded is where the
        # taskbar's thickness is the limit.
        _calibrate_rows(_taskbar_thickness())

        # A single flat Canvas -- see the comment on CANVAS_CONTENT_W above
        # for why: this is the shallowest possible widget tree, which is
        # what actually renders once genuinely embedded as a child of
        # Shell_TrayWnd.
        self.canvas_w = PAD * 2 + CANVAS_CONTENT_W
        self.canvas_h = PAD_Y * 2 + ROW_H * 2 + ROW_GAP
        self.canvas = tk.Canvas(
            self.window,
            width=self.canvas_w,
            height=self.canvas_h,
            bg=self._panel_bg,
            highlightthickness=1,
            highlightbackground=BORDER_CARD,
            cursor="hand2"
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        icon_cx = PAD + ICON_W // 2
        icon_cy = self.canvas_h // 2
        self._icon_cx = icon_cx
        self._icon_cy = icon_cy

        # Drawn BEFORE the pulse mark so the mark stays on top of it -- the
        # countdown is an annotation of the icon, not a thing competing
        # with it. See COUNTDOWN_HALF and COUNTDOWN_CUT for the shape.
        poly = _countdown_polygon(icon_cx, icon_cy)
        # Line widths stay at their 1x 1px/2px: scaled with the shape they
        # came out at 3px, which read as too heavy on the real taskbar.
        octagon_state = "normal" if COUNTDOWN_STYLE == "octagon" else "hidden"
        self.countdown_track_item = self.canvas.create_line(
            *[v for pt in poly for v in pt],
            fill=self._countdown_track_color(), width=1, state=octagon_state
        )
        # One polyline whose point list is rewritten each tick, rather than
        # four per-side items that get shown/hidden -- Tk's coords() happily
        # changes a line's number of points, so the whole partial perimeter
        # is one call. Drawn at 2px width so the active fill stands out
        # clearly against the 1px unfilled track underneath.
        self.countdown_item = self.canvas.create_line(
            *poly[0], *poly[0],
            fill=self._text_secondary, width=2, state="hidden"
        )

        # Colored 'Twin Rails' icon: directly portrays the widget's defining
        # "two rails, not one" architecture inside the octagonal countdown perimeter.
        # Top Rail (Quota track + Claude Coral fill)
        # Offsets are the 1x design, scaled by _mark() -- see _calibrate_icon.
        # The two tracks take the rows' own theme colours (set in
        # _update_icon), so the mark's empty parts look like the bars' empty
        # parts. They were fixed dark greys copied from the tray icon: fine
        # on a dark taskbar, but a solid black box on a light one, and there
        # the time rail's track matched its elapsed part exactly.
        self.icon_top_track = self.canvas.create_rectangle(
            icon_cx - _mark(8), icon_cy - _mark(5.5), icon_cx + _mark(8), icon_cy - _mark(1.5),
            fill=self._track_color, outline=self._track_outline, width=1
        )
        self.icon_top_fill = self.canvas.create_rectangle(
            icon_cx - _mark(8), icon_cy - _mark(5.5), icon_cx, icon_cy - _mark(1.5),
            fill=CLAUDE_CORAL, outline="", width=0
        )
        # Bottom Rail (Time track + Elapsed Gray + Danger Red tail)
        self.icon_bot_track = self.canvas.create_rectangle(
            icon_cx - _mark(8), icon_cy + _mark(1.5), icon_cx + _mark(8), icon_cy + _mark(4),
            fill=self._track_outline, outline="", width=0
        )
        self.icon_bot_fill = self.canvas.create_rectangle(
            icon_cx - _mark(8), icon_cy + _mark(1.5), icon_cx, icon_cy + _mark(4),
            fill=self._text_secondary, outline="", width=0
        )
        self.icon_bot_tail = self.canvas.create_rectangle(
            icon_cx + _mark(4.5), icon_cy + _mark(1.5), icon_cx + _mark(8), icon_cy + _mark(4),
            fill=DANGER_RED, outline="", width=0
        )
        # Synchronized White Needle (spanning both tracks at center).
        needle_top, needle_bot = icon_cy - _mark(6.5), icon_cy + _mark(5.5)
        if COUNTDOWN_STYLE == "needle":
            # A small hollow gauge: a 1px outline whose inside is painted in
            # the track colour -- the colorkey in transparent mode, so the
            # taskbar shows through exactly like the usage bars' unfilled
            # part -- with needle_fill as the solid part inside it. Hollow
            # versus solid replaced a grey versus white track, which was too
            # close to tell apart at taskbar size. Half-pixel x edges keep the
            # 1px outline crisp around an odd-width interior.
            #
            # Tk rounds an outlined rectangle and a fill-only one differently:
            # a 1px outline on x = n + 0.5 lands on pixel n + 1, while a
            # fill-only rectangle covers [x0, x1). Measured on the live bar:
            # an inset of "outline + 1" left the solid part one pixel left,
            # over the outline, with a dark gap on the right. So the box is
            # put on half-pixel edges and the inside is derived from where
            # its outline actually lands.
            half_w = max(2, round(1.25 * ICON_SCALE)) + 0.5
            bx0, bx1 = icon_cx - half_w, icon_cx + half_w
            # Vertically the gauge overhangs the rails by the same number of
            # whole rows above as below. It was first sized from the 1x
            # needle, which after rounding stood 1 row above the coral rail
            # and 4 below the grey one -- lopsided, and 3 rows short of the
            # travel it could have. Rows are taken from where the rails
            # actually land: the top rail's first row, the bottom rail's last.
            overhang = max(2, round(2.4 * ICON_SCALE))
            top_row = math.ceil(icon_cy - _mark(5.5)) - overhang
            bottom_row = math.ceil(icon_cy + _mark(4)) - 1 + overhang
            by0, by1 = top_row - 0.5, bottom_row - 0.5
            self._needle_box = (bx0, by0, bx1, by1)
            self._needle_inner = (bx0 + 1.5, by0 + 1.5, bx1 + 0.5, by1 + 0.5)
            self.icon_needle = self.canvas.create_rectangle(
                *self._needle_box, fill=self._track_color, outline="#FFFFFF", width=1
            )
            self.needle_fill = self.canvas.create_rectangle(
                *self._needle_inner, fill="#FFFFFF", outline="", width=0
            )
        else:
            self.icon_needle = self.canvas.create_line(
                icon_cx, needle_top, icon_cx, needle_bot,
                fill="#FFFFFF", width=max(2, round(1.6 * ICON_SCALE)), capstyle=tk.ROUND
            )
        self.icon_item = self.icon_needle

        # All 4 row slots (2 columns x 2 rows) are built up front, regardless
        # of how many metrics are configured right now -- update_state()
        # shows/hides them and resizes the canvas width to match each time
        # it runs, so a live change in Settings (2 metrics <-> 4) just works
        # without ever needing to tear down and recreate canvas items.
        self.bar_rows = [self._build_bar_row(i) for i in range(MAX_METRICS)]

    def _build_bar_row(self, index: int) -> dict:
        col = index // ROWS_PER_COL
        row_in_col = index % ROWS_PER_COL
        col0_x0 = PAD + ICON_W + ICON_GAP
        x0 = col0_x0 if col == 0 else (col0_x0 + ROW_CONTENT_W + COL_GAP)
        y0 = PAD_Y + row_in_col * (ROW_H + ROW_GAP)

        # The usage bar no longer sits on the row's centre line: the bar and
        # the time rail under it form one block (14 + 3 + 3 = 20px) that has
        # to fit inside ROW_H (26) along with 2px of needle overhang top and
        # bottom. Nudging the block up by 2 leaves exactly that, and 2px of
        # slack below which -- with ROW_GAP -- keeps stacked rows apart.
        bar_y0 = y0 + 2
        bar_y1 = bar_y0 + BAR_HEIGHT
        rail_y0 = bar_y1 + RAIL_GAP
        rail_y1 = rail_y0 + RAIL_HEIGHT
        # Text lines up with the usage bar rather than with the row box, so
        # the percentage reads as belonging to the bar and the rail hangs
        # below the whole line as an annotation of it.
        cy = bar_y0 + BAR_HEIGHT // 2

        tag_item = self.canvas.create_text(
            x0, cy, text="", anchor="w",
            font=(FONT_FAMILY, 8, "bold"), fill=TEXT_SECONDARY
        )

        bar_x0 = x0 + TAG_W + BAR_GAP
        track_item = self.canvas.create_rectangle(
            bar_x0, bar_y0, bar_x0 + BAR_WIDTH, bar_y1,
            fill=self._track_color, outline=self._track_outline
        )
        fill_item = self.canvas.create_rectangle(
            bar_x0, bar_y0, bar_x0, bar_y1,
            fill=CLAUDE_CORAL, outline=""
        )
        # --- time rail, drawn before the needle so the needle stays on top ---
        rail_track_item = self.canvas.create_rectangle(
            bar_x0, rail_y0, bar_x0 + BAR_WIDTH, rail_y1,
            fill=self._track_outline, outline="", state="hidden"
        )
        rail_elapsed_item = self.canvas.create_rectangle(
            bar_x0, rail_y0, bar_x0, rail_y1,
            fill=self._text_secondary, outline="", state="hidden"
        )
        rail_lockout_item = self.canvas.create_rectangle(
            bar_x0, rail_y0, bar_x0, rail_y1,
            fill=EXHAUSTION_MARKER_COLOR, outline="", state="hidden"
        )
        rail_stop_item = self.canvas.create_line(
            bar_x0, rail_y0 - RAIL_STOP_OVERHANG,
            bar_x0, rail_y1 + RAIL_STOP_OVERHANG,
            fill=EXHAUSTION_MARKER_COLOR, width=2, state="hidden"
        )
        # Build the six possible boundaries once, just like the rest of the
        # flat canvas row. Recreating canvas items on every one-second update
        # would make the embedded taskbar repaint needlessly expensive.
        bar_divider_items = [
            self.canvas.create_line(
                bar_x0, bar_y0 + 1, bar_x0, bar_y1 - 1,
                fill=self._panel_bg, width=1, state="hidden"
            )
            for _ in range(MAX_TIME_DIVIDERS)
        ]
        rail_divider_items = [
            self.canvas.create_line(
                bar_x0, rail_y0 - 1, bar_x0, rail_y1 + 1,
                fill=self._panel_bg, width=1, state="hidden"
            )
            for _ in range(MAX_TIME_DIVIDERS)
        ]
        # A red run-out cap must stay above a coincident faint guide, while
        # the now needle (created next) remains above both.
        self.canvas.tag_raise(rail_stop_item, rail_divider_items[-1])

        # The needle spans the usage bar AND the rail as one continuous line:
        # it is the only thing on the row that means the same in both places
        # ("now"), and running it through both is what ties the two tracks
        # together as one reading rather than two unrelated strips.
        marker_item = self.canvas.create_line(
            bar_x0, bar_y0 - MARKER_PROTRUSION, bar_x0, rail_y1 + MARKER_PROTRUSION,
            fill=self._marker_color, width=2, state="hidden"
        )

        pct_x = bar_x0 + BAR_WIDTH + PCT_GAP
        pct_item = self.canvas.create_text(
            pct_x, cy, text="--", anchor="w",
            font=(FONT_FAMILY, 9, "bold"), fill=TEXT_PRIMARY
        )

        time_x = pct_x + PCT_W + TIME_GAP
        time_item = self.canvas.create_text(
            time_x, cy, text="--", anchor="w",
            font=(FONT_FAMILY, 9), fill=TEXT_SECONDARY
        )

        # Its own item, not more text appended to time_item, purely so it can
        # be coloured independently -- red when the projection is "you run
        # dry at 3:05p", neutral when it is "you finish around 78%". Same
        # field, opposite news, and the colour is what tells them apart at a
        # glance without the row having to spell either out in words.
        pace_x = time_x + TIME_W + PACE_GAP
        pace_item = self.canvas.create_text(
            pace_x, cy, text="", anchor="w",
            font=(FONT_FAMILY, 9), fill=TEXT_SECONDARY
        )

        return {
            "y0": y0, "bar_x0": bar_x0, "bar_y0": bar_y0, "bar_y1": bar_y1,
            "rail_y0": rail_y0, "rail_y1": rail_y1,
            "tag_item": tag_item, "track_item": track_item, "fill_item": fill_item,
            "rail_track_item": rail_track_item, "rail_elapsed_item": rail_elapsed_item,
            "rail_lockout_item": rail_lockout_item, "rail_stop_item": rail_stop_item,
            "bar_divider_items": bar_divider_items,
            "rail_divider_items": rail_divider_items,
            "marker_item": marker_item,
            "pct_item": pct_item, "time_item": time_item, "pace_item": pace_item,
            "key": None
        }

    def _is_icon_x(self, x: int) -> bool:
        return x <= (PAD + ICON_W + ICON_GAP)

    def _bind_mouse_events(self):
        # A single Canvas now, so hit-testing (icon vs bars) is done by
        # comparing event.x against the icon column's width, the same way
        # _poll_embedded_input() already had to for the embedded case
        # (Tk's own bindings don't fire there at all, embedded or not, once
        # this was reparented -- see _poll_embedded_input's docstring).
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", lambda e: self._dismiss_explorer_menu())
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Enter>", self._on_mouse_enter)
        self.canvas.bind("<Leave>", self._on_mouse_leave)

    def _on_canvas_motion(self, event):
        over_icon = self._is_icon_x(event.x)
        if over_icon != self._icon_hovered:
            self._icon_hovered = over_icon
            self._update_icon()
            if over_icon:
                self._show_icon_tooltip()
            else:
                self._hide_icon_tooltip()

    def _on_icon_click(self, event=None):
        self._hide_icon_tooltip()
        self.on_refresh()

    def _handle_bars_click(self):
        if self.is_flyout_active and self.is_flyout_active():
            if self.cancel_flyout_hide:
                self.cancel_flyout_hide()
            if self.on_close_flyout:
                self.on_close_flyout()
        else:
            self.window.update_idletasks()
            rect = self._get_screen_rect()
            if rect:
                bx, by, bx2, by2 = rect
                self.on_open_flyout(bx, by, bx2 - bx, by2 - by)

    def _on_mouse_enter(self, event=None):
        if not self._embedded:
            self._force_topmost()
        if self.cancel_flyout_hide:
            self.cancel_flyout_hide()

    def _on_mouse_leave(self, event=None):
        self._hide_icon_tooltip()
        if self.schedule_flyout_hide and self.is_flyout_active and self.is_flyout_active():
            self.schedule_flyout_hide(delay_ms=350, is_cursor_in_bar_fn=self.is_cursor_inside)

    def _on_press(self, event):
        if not self._embedded:
            self._force_topmost()
        self._press_was_icon = self._is_icon_x(event.x)
        self.click_start_x_root = event.x_root
        self.click_start_y_root = event.y_root
        self.drag_start_x = event.x_root - self.window.winfo_x()
        self.drag_start_y = event.y_root - self.window.winfo_y()

    def _on_drag(self, event):
        if self.is_locked or self._embedded:
            return
        if abs(event.x_root - self.click_start_x_root) > 3 or abs(event.y_root - self.click_start_y_root) > 3:
            x = event.x_root - self.drag_start_x
            y = event.y_root - self.drag_start_y
            _, _, _, wb = get_work_area(self._resolve_hwnd() or self.window.winfo_id())
            max_y = wb - self.window.winfo_height()
            if y > max_y:
                y = max_y
            self.window.geometry(f"+{x}+{y}")

    def _on_release(self, event):
        self._force_topmost()
        dx = abs(event.x_root - self.click_start_x_root)
        dy = abs(event.y_root - self.click_start_y_root)
        if dx <= 4 and dy <= 4:
            if self._press_was_icon:
                self._on_icon_click()
            else:
                self._handle_bars_click()
        elif not self._embedded:
            # Drag ended -- persist the new floating position so it survives
            # the next launch instead of resetting to the default corner
            # every time (this used to be dropped entirely, silently -- the
            # docked_bar_x/y config fields were written by nothing).
            self.cfg_mgr.config.docked_bar_x = self.window.winfo_x()
            self.cfg_mgr.config.docked_bar_y = self.window.winfo_y()
            self.cfg_mgr.save()

    def _dismiss_explorer_menu(self):
        try:
            user32 = ctypes.windll.user32
            if self._shell_tray_hwnd:
                user32.PostMessageW(self._shell_tray_hwnd, 0x001F, 0, 0)
            menu_hwnd = user32.FindWindowW("#32768", None)
            if menu_hwnd:
                user32.PostMessageW(menu_hwnd, 0x0010, 0, 0)
        except Exception:
            pass

    def _not_keyed_out(self, color: str) -> str:
        """Nudges a color one step away from the colorkey if it lands
        exactly on it.

        Only the bar FILL needs this. Every other drawn color goes through
        ensure_contrast() against the key, so none of them can collide with
        it by construction. The fill is the exception: it is a raw pacing
        color (or a dim/tint blend of one), deliberately left unadjusted
        because it sits inside the bar rather than on the taskbar. If a
        user's configured color happened to be exactly Claude coral, the
        fill would silently vanish instead of merely clashing. One RGB unit
        is invisible and removes the whole failure mode."""
        key = self._embed_colorkey()
        if not key or color.upper() != key.upper():
            return color
        h = color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return "#%02X%02X%02X" % (r, g, min(255, b + 1) if b < 255 else b - 1)

    def _legible_marking_color(self, color: str) -> str:
        """Keeps a fixed status color readable where it is drawn on the
        TASKBAR rather than inside the bar -- the percentage text, and the
        projected-exhaustion marker.

        Neither has anything behind it once the background is transparent
        and the track is hollow. That matters for exactly the colors these
        use: amber (#F59E0B) manages only ~2:1 against a pale taskbar,
        Claude's terracotta ~2.3:1, and danger red ~2.4:1. All three read
        as a smudge rather than as a number or a line. Nudged just far
        enough to clear the threshold, which keeps each one recognisably
        amber/coral/red -- the point of coloring them at all -- rather than
        flattening them to plain dark text.

        The bar FILL deliberately does NOT go through here: it sits inside
        the bar where the outline frames it, so leaving it at the true
        brand color is both safe and more accurate.

        No-op with a solid panel, where the theme's own colors already
        account for the background."""
        # self._panel_bg IS the backdrop estimate in transparent mode (it is
        # what gets painted and keyed out), so this needs no second lookup --
        # worth being deliberate about, since this runs per row per update
        # and _backdrop_estimate() reads the registry.
        key = self._embed_colorkey()
        if not key:
            return color
        return ensure_contrast(color, key, PACING_TEXT_MIN_CONTRAST)

    def _compute_pacing_color(self, metric: Union[UsageWindow, CreditsMetric]) -> str:
        base_color = getattr(metric, "brand_color", CLAUDE_CORAL)
        if isinstance(metric, CreditsMetric):
            pct = metric.utilization
            if pct >= 95.0:
                return DANGER_RED
            if pct >= 80.0:
                return WARNING_AMBER
            return CREDITS_GREEN

        pct = metric.utilization
        elapsed_pct = (metric.elapsed_ratio or 0.0) * 100.0

        if pct <= elapsed_pct:
            return base_color

        mode = getattr(self.cfg_mgr.config, "pacing_color_mode", "dynamic")
        if mode == "fixed":
            delta = getattr(self.cfg_mgr.config, "pacing_fixed_delta", 10.0)
            red_threshold = elapsed_pct + delta
        else:
            red_threshold = elapsed_pct + (100.0 - elapsed_pct) / 2.0

        if pct >= red_threshold:
            return DANGER_RED
        return WARNING_AMBER

    def update_state(self, state: Union[UnifiedUsageState, ClaudeUsageState], is_refreshing: Optional[bool] = None):
        self._last_state = state
        if is_refreshing is not None:
            self._is_refreshing = is_refreshing

        # Bidirectional alias for the pre-unification Claude metric key
        # names ("five_hour"/"seven_day", used before the multi-provider
        # rewrite renamed them to "claude_five_hour"/"claude_seven_day").
        # Only the forward direction was handled before, so a config saved
        # by an older version of the app -- which stores the OLD names in
        # widget_metric_keys -- resolved neither name, silently fell through
        # to "first 2 metrics found" instead of what Settings actually shows
        # as selected, and would show two same-provider rows instead of the
        # picked ones whenever that happened to be what "first 2" landed on.
        _legacy_key_aliases = {
            "claude_five_hour": "five_hour", "five_hour": "claude_five_hour",
            "claude_seven_day": "seven_day", "seven_day": "claude_seven_day",
        }
        wanted_keys = self.cfg_mgr.config.widget_metric_keys
        metrics = []
        for key in wanted_keys:
            m = state.metric_by_key(key)
            if not m and key in _legacy_key_aliases:
                m = state.metric_by_key(_legacy_key_aliases[key])
            if not m and key == "limits_weekly_scoped":
                # The old parser used one shared key for every scoped model.
                # It resolved the first model; retain that behavior until the
                # user saves a specific model choice in Settings.
                m = next(
                    (candidate for candidate in state.all_metrics
                     if candidate.provider_id == "claude"
                     and candidate.key.startswith("limits_weekly_scoped_")),
                    None,
                )
            if m:
                metrics.append(m)

        if not metrics and hasattr(state, "all_metrics") and state.all_metrics:
            metrics = state.all_metrics[:2]

        visible_count = min(len(metrics), MAX_METRICS)
        if self._visible_row_count != visible_count:
            self._visible_row_count = visible_count

        # A 2nd column only exists once more than one column's worth of rows
        # is actually needed -- grows/shrinks the canvas (and, downstream,
        # the whole floating/embedded window) to match on every update, so
        # changing the metric selection in Settings between <=2 and >2 while
        # the app is running just works without rebuilding canvas items.
        compact_keys = set(getattr(self.cfg_mgr.config, "compact_metric_keys", []) or [])

        # A 2nd column only exists once more than one column's worth of rows
        # is actually needed -- grows/shrinks the canvas (and, downstream,
        # the whole floating/embedded window) to match on every update.
        # If all metrics in a column are compact (no progress bar), that column
        # uses ROW_CONTENT_W_COMPACT, saving ~175px width.
        col0_metrics = metrics[:ROWS_PER_COL]
        col1_metrics = metrics[ROWS_PER_COL:MAX_METRICS]
        col0_is_compact = bool(col0_metrics) and all(m.key in compact_keys for m in col0_metrics)
        col1_is_compact = bool(col1_metrics) and all(m.key in compact_keys for m in col1_metrics)
        col0_w = ROW_CONTENT_W_COMPACT if col0_is_compact else ROW_CONTENT_W_FULL
        col1_w = ROW_CONTENT_W_COMPACT if col1_is_compact else ROW_CONTENT_W_FULL

        needs_2col = visible_count > ROWS_PER_COL
        new_canvas_w = PAD * 2 + ICON_W + ICON_GAP + col0_w + ((COL_GAP + col1_w) if needs_2col else 0)
        if new_canvas_w != self.canvas_w:
            self.canvas_w = new_canvas_w
            self.canvas.configure(width=self.canvas_w)

        self._update_icon()

        is_demo = getattr(state, "is_demo", False)

        is_remaining = (self.cfg_mgr.config.percentage_display_mode == "remaining")

        for i, row in enumerate(self.bar_rows):
            if i < len(metrics):
                m = metrics[i]
                row["key"] = m.key

                col = i // ROWS_PER_COL
                row_in_col = i % ROWS_PER_COL
                col0_x0 = PAD + ICON_W + ICON_GAP
                x0 = col0_x0 if col == 0 else (col0_x0 + col0_w + COL_GAP)
                y0 = PAD_Y + row_in_col * (ROW_H + ROW_GAP)
                bar_y0 = y0 + 2
                bar_y1 = bar_y0 + BAR_HEIGHT
                cy = bar_y0 + BAR_HEIGHT // 2

                icon = getattr(m, "icon_symbol", "")
                tag = getattr(m, "short_tag", "")
                self.canvas.coords(row["tag_item"], x0, cy)
                self.canvas.itemconfigure(row["tag_item"], text=f"{icon} {tag}" if icon else tag, state="normal")

                is_compact_row = m.key in compact_keys
                pacing_color = self._compute_pacing_color(m)

                # Fetch-state feedback on the fill color only (percentage/time
                # text stays fully legible). Dimming while a refresh is in
                # flight is transient -- it clears the moment update_state()
                # is next called with is_refreshing=False. The error tint is
                # NOT transient: it's keyed off the metric's own provider
                # still being disconnected, so it stays lit across every
                # subsequent tick/refresh until that provider actually
                # reconnects, rather than clearing just because a refresh
                # attempt finished. That's the point -- it needs to read
                # differently from "still loading", not just briefly flash.
                # Deliberately doesn't cover fetch_all() itself throwing
                # (main.py's own outer except) -- that's a rare defensive
                # catch-all that keeps showing the last-known-good provider
                # states, so there's no per-provider signal to key off here.
                if hasattr(state, "providers"):
                    prov = state.providers.get(getattr(m, "provider_id", ""))
                    has_error = (not is_demo) and prov is not None and not prov.is_connected
                else:
                    has_error = (not is_demo) and bool(getattr(state, "error_message", None))

                # Dim/tint toward whatever the fill is actually READ
                # against -- the panel color with a solid panel, and the
                # taskbar itself once the background is transparent and the
                # track hollow (self._track_color is the colorkey then, so
                # these are the same value; named this way so it stays
                # correct if the track ever gets a tint back).
                dim_target = self._panel_bg
                if self._is_refreshing:
                    display_color = blend_hex(pacing_color, dim_target, 0.55)
                elif has_error:
                    display_color = blend_hex(blend_hex(pacing_color, WARNING_AMBER, 0.6), dim_target, 0.25)
                else:
                    display_color = pacing_color

                if is_compact_row:
                    # In compact row, hide progress bar and rail items
                    self.canvas.itemconfigure(row["fill_item"], state="hidden")
                    self.canvas.itemconfigure(row["track_item"], state="hidden")
                    self.canvas.itemconfigure(row["rail_track_item"], state="hidden")
                    self.canvas.itemconfigure(row["rail_elapsed_item"], state="hidden")
                    self.canvas.itemconfigure(row["rail_lockout_item"], state="hidden")
                    self.canvas.itemconfigure(row["rail_stop_item"], state="hidden")
                    self.canvas.itemconfigure(row["marker_item"], state="hidden")
                    for divider_item in (*row["bar_divider_items"], *row["rail_divider_items"]):
                        self.canvas.itemconfigure(divider_item, state="hidden")

                    pct_x = x0 + TAG_W + PCT_GAP
                    time_x = pct_x + PCT_W + TIME_GAP
                    pace_x = time_x + TIME_W + PACE_GAP
                else:
                    bar_x0 = x0 + TAG_W + BAR_GAP
                    row["bar_x0"] = bar_x0
                    row["bar_y0"] = bar_y0
                    row["bar_y1"] = bar_y1
                    row["rail_y0"] = bar_y1 + RAIL_GAP
                    row["rail_y1"] = row["rail_y0"] + RAIL_HEIGHT
                    ry0, ry1 = row["rail_y0"], row["rail_y1"]

                    elapsed = max(0.0, min(1.0, getattr(m, "elapsed_ratio", 0.0) or 0.0))
                    util = max(0.0, min(100.0, m.utilization))
                    fill_w = (util / 100.0) * BAR_WIDTH
                    self.canvas.coords(row["track_item"], bar_x0, bar_y0, bar_x0 + BAR_WIDTH, bar_y1)
                    self.canvas.itemconfigure(row["track_item"], state="normal")
                    self.canvas.coords(row["fill_item"], bar_x0, bar_y0, bar_x0 + fill_w, bar_y1)
                    self.canvas.itemconfigure(row["fill_item"], fill=self._not_keyed_out(display_color), state="normal")

                    # --- the time rail ---------------------------------------
                    has_window = bool(getattr(m, "has_time_window", False))
                    rail_x0, rail_x1 = bar_x0, bar_x0 + BAR_WIDTH
                    if has_window:
                        self.canvas.coords(row["rail_track_item"], rail_x0, ry0, rail_x1, ry1)
                        self.canvas.itemconfigure(row["rail_track_item"], state="normal")
                        if elapsed > 0.0:
                            self.canvas.coords(
                                row["rail_elapsed_item"],
                                rail_x0, ry0, rail_x0 + elapsed * BAR_WIDTH, ry1
                            )
                            self.canvas.itemconfigure(row["rail_elapsed_item"], state="normal")
                        else:
                            self.canvas.itemconfigure(row["rail_elapsed_item"], state="hidden")
                    else:
                        self.canvas.itemconfigure(row["rail_track_item"], state="hidden")
                        self.canvas.itemconfigure(row["rail_elapsed_item"], state="hidden")

                    # --- lockout tail ---
                    exhaustion_ratio = getattr(m, "exhaustion_ratio", None)
                    if has_window and exhaustion_ratio is not None and 0.0 < exhaustion_ratio < 1.0:
                        ex_x = rail_x0 + exhaustion_ratio * BAR_WIDTH
                        danger = self._legible_marking_color(EXHAUSTION_MARKER_COLOR)
                        self.canvas.coords(row["rail_lockout_item"], ex_x, ry0, rail_x1, ry1)
                        self.canvas.itemconfigure(row["rail_lockout_item"], state="normal", fill=danger)
                        self.canvas.coords(
                            row["rail_stop_item"],
                            ex_x, ry0 - RAIL_STOP_OVERHANG, ex_x, ry1 + RAIL_STOP_OVERHANG
                        )
                        self.canvas.itemconfigure(row["rail_stop_item"], state="normal", fill=danger)
                    else:
                        self.canvas.itemconfigure(row["rail_lockout_item"], state="hidden")
                        self.canvas.itemconfigure(row["rail_stop_item"], state="hidden")

                    # Repeat the time sections on the usage bar as faint
                    # guides. The hollow track remains the theme's colorkey:
                    # only the 1px guides are painted over its empty area.
                    # The rail below is the actual time axis.
                    division_count = (
                        getattr(m, "time_division_count", 0)
                        if has_window and self.cfg_mgr.config.show_time_divisions else 0
                    )
                    for divider_index, (bar_tick, rail_tick) in enumerate(
                        zip(row["bar_divider_items"], row["rail_divider_items"]),
                        start=1
                    ):
                        if divider_index >= division_count:
                            self.canvas.itemconfigure(bar_tick, state="hidden")
                            self.canvas.itemconfigure(rail_tick, state="hidden")
                            continue
                        tick_offset = divider_index * BAR_WIDTH / division_count
                        tick_x = round(bar_x0 + tick_offset)
                        top_color = (
                            self._panel_bg if tick_offset < fill_w
                            else blend_hex(self._panel_bg, self._track_outline, 0.65)
                        )
                        self.canvas.coords(bar_tick, tick_x, bar_y0 + 1, tick_x, bar_y1 - 1)
                        self.canvas.itemconfigure(bar_tick, fill=top_color, state="normal")
                        self.canvas.coords(rail_tick, tick_x, ry0 - 1, tick_x, ry1 + 1)
                        self.canvas.itemconfigure(rail_tick, fill=self._panel_bg, state="normal")

                    # --- marker ---
                    if 0.0 < elapsed < 1.0:
                        marker_x = bar_x0 + elapsed * BAR_WIDTH
                        self.canvas.coords(
                            row["marker_item"],
                            marker_x, bar_y0 - MARKER_PROTRUSION,
                            marker_x, (ry1 if has_window else bar_y1) + MARKER_PROTRUSION
                        )
                        self.canvas.itemconfigure(row["marker_item"], state="normal")
                    else:
                        self.canvas.itemconfigure(row["marker_item"], state="hidden")

                    pct_x = bar_x0 + BAR_WIDTH + PCT_GAP
                    time_x = pct_x + PCT_W + TIME_GAP
                    pace_x = time_x + TIME_W + PACE_GAP

                disp_pct = m.remaining_pct if is_remaining else m.utilization
                self.canvas.coords(row["pct_item"], pct_x, cy)
                self.canvas.itemconfigure(
                    row["pct_item"], text=f"{disp_pct:.0f}%",
                    fill=self._legible_marking_color(pacing_color), state="normal"
                )
                self.canvas.coords(row["time_item"], time_x, cy)
                self.canvas.itemconfigure(row["time_item"], text=m.compact_reset_label, fill=self._text_secondary, state="normal")

                pace_text = getattr(m, "pace_label", "") or ""
                self.canvas.coords(row["pace_item"], pace_x, cy)
                if pace_text:
                    pace_color = (
                        self._legible_marking_color(EXHAUSTION_MARKER_COLOR)
                        if getattr(m, "is_pace_critical", False)
                        else self._text_secondary
                    )
                    self.canvas.itemconfigure(
                        row["pace_item"], text=pace_text, fill=pace_color, state="normal"
                    )
                else:
                    self.canvas.itemconfigure(row["pace_item"], state="hidden")
            else:
                row["key"] = None
                for item_key in ("tag_item", "track_item", "fill_item",
                                 "rail_track_item", "rail_elapsed_item",
                                 "rail_lockout_item", "rail_stop_item",
                                 "marker_item", "pct_item", "time_item", "pace_item"):
                    self.canvas.itemconfigure(row[item_key], state="hidden")
                for divider_item in (*row["bar_divider_items"], *row["rail_divider_items"]):
                    self.canvas.itemconfigure(divider_item, state="hidden")

        if self._embedded:
            self._reposition_embedded()
            # Content just changed (percentages, countdowns) -- confirmed
            # directly that a reparented Canvas doesn't repaint on its own
            # after items are updated via coords()/itemconfigure(), same
            # root cause as the one-time post-embed repaint in
            # _force_full_repaint(). Cheap enough to do every second.
            self._force_full_repaint()
        else:
            # Independent of _maintenance_tick()'s own reclamp call --
            # main.py's tick() calls update_state() every second regardless,
            # so this is a second, reliable trigger point for the same
            # right-edge correction (see _reclamp_floating_position) that
            # doesn't depend on _maintenance_tick() ever taking its "not
            # embedded" branch. Must stay mutually exclusive with the
            # embedded branch above -- confirmed directly as a real bug:
            # calling this unconditionally overwrote _reposition_embedded()'s
            # just-applied SetWindowPos every single second, so a genuine
            # embed attempt's position never had a chance to actually show.
            self._reclamp_floating_position()

    def show(self):
        self.window.update_idletasks()

        if self._embedded:
            # Already embedded from a previous show/hide cycle -- position is
            # fully automatic, just resync it once before revealing.
            self._reposition_embedded()
        else:
            # Not embedded yet: give the floating fallback an actual
            # position instead of leaving it at Tk's arbitrary default
            # Toplevel placement (which can land far from the real taskbar
            # on a multi-monitor system). Reuse the last dragged position if
            # it's still a sane one -- flush above the taskbar's work area --
            # otherwise snap back to the default corner.
            wl, wt, wr, wb = get_work_area(self._resolve_hwnd() or self.window.winfo_id())
            x = self.cfg_mgr.config.docked_bar_x
            y = self.cfg_mgr.config.docked_bar_y
            max_y = wb - self.window.winfo_reqheight()
            # A saved position only counts as valid if it's actually near the
            # taskbar's top edge -- not merely "somewhere on screen". A plain
            # y > max_y bounds check accepts anything from y=0 down, so a
            # stale value from a much older run (confirmed: this project had
            # docked_bar_x/y = (3192, 0) sitting in config.json from before
            # this persistence logic was lost in a refactor -- top of the
            # screen, nowhere near the taskbar) would pass it and reproduce
            # the exact "floating in a random spot" bug on the very first
            # restart.
            near_taskbar = (max_y - 150) <= y <= max_y
            if x < wl or x > (wr - 50) or not near_taskbar:
                self.snap_to_taskbar()
            else:
                self.window.geometry(f"+{x}+{y}")
            if ATTEMPT_GENUINE_EMBED:
                self._try_embed()

        self.window.deiconify()
        self.window.lift()
        if self._embedded:
            # deiconify() re-maps the wrapper, and Tk re-applies its own
            # window attributes (including -transparentcolor) as part of
            # that -- which would put the embedded bar straight back to
            # being additively blended into the taskbar. Re-assert after,
            # not before.
            self._reassert_embed_window_styles()
            self._clear_window_region()
            self._force_full_repaint()
        else:
            self._force_topmost()
            self._apply_rounded_corners()

    def hide(self):
        self.window.withdraw()
        self._hide_icon_tooltip()
