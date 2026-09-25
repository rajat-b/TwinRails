"""
Design system and color palette for TwinRails.
Covers Claude and Google Antigravity / Gemini.
"""

# Claude Signature Brand Colors
CLAUDE_CORAL = "#D97756"        # Signature terracotta/coral
CLAUDE_CORAL_HOVER = "#E0896B"
CLAUDE_BLUE = "#2C84DB"         # Weekly bar blue
CLAUDE_BLUE_LIGHT = "#5AA6FF"
CLAUDE_VIOLET = "#8B5CF6"       # Per-model weekly window accent
CREDITS_GREEN = "#10B981"       # Usage-credits ($ balance) accent

# Google Antigravity / Gemini Brand Colors
GEMINI_BLUE = "#1A73E8"         # Authentic Google Blue
GEMINI_BLUE_HOVER = "#4285F4"   # Lighter Google Blue
GEMINI_BLUE_ACTIVE = "#6BA4F8"  # Active/pressed state
GEMINI_PURPLE = "#7C3AED"       # Gemini gradient purple
GEMINI_TEAL = "#06B6D4"         # Tertiary accent

# Claude's weekly usage by product -- the flyout's stacked bar. Keyed by the
# API's stable product id, so a product keeps its colour whatever its rank.
#
# Chosen with the dataviz skill's palette validator against BG_CARD, with
# --pairs all rather than the usual adjacent-only check: a product at 0% has
# no segment, so ANY two of these can end up touching. Of the reference
# palette's four-colour sets only two pass all-pairs, and this is the one
# without blue (Gemini's colour in this same flyout). Rejected on the way:
# violet/aqua/magenta + grey "Other" (magenta vs grey reads identical to
# deuteranopes, delta-E 1.1) and orange-first orders (orange vs green 2.7).
# The weakest pair, green vs yellow, is 6.9 -- the validator's floor band,
# legal only with secondary encoding, which the 2px gaps and the labelled
# legend provide. Yellow is on "Other", usually the smallest segment, to keep
# it from reading as WARNING_AMBER. Unknown product ids fold into "Other".
BREAKDOWN_ORDER = ("claude_code", "chat", "cowork", "other")
BREAKDOWN_COLORS = {
    "claude_code": "#9085E9",   # violet
    "chat": "#D55181",          # magenta
    "cowork": "#008300",        # green
    "other": "#C98500",         # yellow
}

# Status / Pacing Colors
WARNING_AMBER = "#F59E0B"       # Warning color for >=80%
DANGER_RED = "#EF4444"          # Danger color for >=95%
SUCCESS_GREEN = "#10B981"

# Windows 11 Dark / Fluent Palette
BG_MAIN = "#1C1B1A"             # Main window background
BG_CARD = "#262422"             # Container card background
BG_HOVER = "#32302C"            # Card hover state
BG_TRACK = "#3B3835"            # Progress bar background track
BORDER_CARD = "#383633"         # Subtle borders
BORDER_ACTIVE = "#5A5650"

# Text Colors
TEXT_PRIMARY = "#FAF9F5"        # High contrast white
TEXT_SECONDARY = "#A8A29E"      # Secondary gray
# Dim helper text. Was #78716C: 3.2:1 on BG_CARD, under the 4.5:1 that small
# text needs, and most helper text here is small. Lightened on 2026-09-25 to
# the first step of the same grey that clears it (4.7:1 card, 5.3:1 main).
TEXT_MUTED = "#948D88"
TEXT_ACCENT = "#D97756"

# Time Marker
MARKER_COLOR = "#FFFFFF"
# Outline drawn around every progress track. Fixed for a solid panel (it
# only ever sits on one of two known backgrounds); derived per-backdrop in
# transparent mode, where it is what actually defines the bar's extent.
TRACK_OUTLINE = "#383838"
# Projected-exhaustion marker -- the "you'll run out here" line. Fixed (not
# adaptive-themed) like the pacing fill colors, since red reads clearly
# against both the light and dark track colors.
EXHAUSTION_MARKER_COLOR = DANGER_RED

# Fonts
FONT_FAMILY = "Segoe UI"
# Text sizes for the flyout and Settings, in points (Tk scales points with the
# display DPI). The taskbar bar keeps its own sizes: its height is fixed by the
# taskbar. Raised one step on 2026-09-25 -- captions used to be 8pt, below
# Windows 11's smallest style (Caption, 12px = 9pt), and read as too small,
# Settings especially. Change them here, not at each widget.
FONT_SIZE_CAPTION = 9       # helper text, stats, legends
FONT_SIZE_BODY = 10         # labels, options, card titles
FONT_SIZE_HEADING = 11      # Settings section headings


# --- Light/dark adaptive helpers -------------------------------------------

def relative_luminance(hex_color: str) -> float:
    """0.0 (black) to 1.0 (white), perceptual (ITU-R BT.601) weighting."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    except Exception:
        return 0.0


def is_light_color(hex_color: str) -> bool:
    return relative_luminance(hex_color) > 0.55


def blend_hex(c1: str, c2: str, t: float) -> str:
    """Linearly blend from c1 (t=0.0) to c2 (t=1.0)."""
    t = max(0.0, min(1.0, t))
    h1, h2 = c1.lstrip("#"), c2.lstrip("#")
    r1, g1, b1 = int(h1[0:2], 16), int(h1[2:4], 16), int(h1[4:6], 16)
    r2, g2, b2 = int(h2[0:2], 16), int(h2[2:4], 16), int(h2[4:6], 16)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    b = round(b1 + (b2 - b1) * t)
    return f"#{r:02X}{g:02X}{b:02X}"


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG-style contrast ratio between two colors, order-independent."""
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def ensure_contrast(hex_color: str, bg_hex: str, min_ratio: float) -> str:
    """Darkens or lightens hex_color (blending toward black/white) just
    enough to clear min_ratio contrast against bg_hex; returns hex_color
    unchanged if it already clears the bar.

    Needed because a fixed hex tuned for legibility against one background
    silently stops working against another: contrast ratio is non-linear
    near the ends of the luminance range, so the same raw RGB gap that
    reads as a clear ~3:1 edge near black collapses to a barely-there
    ~1.1:1 near white.

    Written originally on experiment/taskbar-bar-contrast-fixes, where it
    was aimed at the wrong problem (the bar looked washed out because it
    was being additively blended into the taskbar, which no choice of
    foreground color can fix -- see taskbar_bar._make_embedded_layered).
    It is the right tool for TRANSPARENT bar mode though, where the text
    genuinely does sit on the taskbar itself and its exact color matters.

    The blend direction (toward black vs toward white) is picked by which
    extreme can actually REACH min_ratio against bg_hex, not by comparing
    hex_color's own luminance to bg_hex's -- confirmed as a real bug: for a
    saturated mid-luminance background (e.g. a green #008800, luminance
    ~0.31), a color lighter than the background (like Claude's terracotta,
    luminance ~0.57) got pushed toward WHITE for "more contrast" -- but
    white against that background only ever reaches ~2.9:1, so the search
    exhausted every step without hitting a 3:1 floor and fell through to
    flat white, discarding the brand color entirely. Darkening the same
    terracotta toward black reaches 7:1+ well before fully desaturating.
    Comparing the two extremes' achievable ratios up front picks whichever
    direction the specific background allows.
    """
    if contrast_ratio(hex_color, bg_hex) >= min_ratio:
        return hex_color
    bg_lum = relative_luminance(bg_hex)
    max_against_black = (bg_lum + 0.05) / 0.05
    max_against_white = 1.05 / (bg_lum + 0.05)
    # "#0A0A0A"/"#FAFAFA" rather than literal black/white: in transparent
    # bar mode the panel color itself is the colorkey, and landing exactly
    # on a keyed-out value would make the text disappear rather than merely
    # look wrong. Staying a nudge away from both 0 and 255 is also visually
    # indistinguishable from true black/white.
    target = "#0A0A0A" if max_against_black >= max_against_white else "#FAFAFA"
    for i in range(1, 21):
        candidate = blend_hex(hex_color, target, i / 20.0)
        if contrast_ratio(candidate, bg_hex) >= min_ratio:
            return candidate
    return target


# Minimum contrast the percentage text keeps against the backdrop when the
# bar has no painted background. Deliberately 2.8 rather than a textbook
# 4.5: this is the one place a PACING color is used as text, and its color
# is the message ("coral = fine, amber = watch it, red = trouble").
# Measured against a pale taskbar, forcing 4.5 drives coral, amber and red
# all to near-identical dark browns -- legible, but no longer telling the
# user anything, which is a worse outcome than a slightly softer number.
# 2.8 keeps each one recognisably its own hue while still reading cleanly
# at 9pt bold.
PACING_TEXT_MIN_CONTRAST = 2.8


def transparent_bar_theme(backdrop_hex: str) -> dict:
    """Palette for the taskbar bar when its background is transparent (the
    real taskbar shows through) rather than a painted panel.

    `backdrop_hex` is the best estimate of what is actually BEHIND the bar
    (see taskbar_bar._backdrop_estimate()). It does double duty: every
    foreground color is derived to stay legible against it, and it is also
    returned as `panel_bg`, which is both what the canvas is painted in and
    what win32 punches out to transparency.

    Painting in the same color that is keyed out means the background is
    never seen -- except at the anti-aliased EDGES of text glyphs, which
    are blended against it, do not match it exactly, and so stay opaque.
    That is why the estimate is used here rather than the raw configured
    color: a configured color that disagrees with the real taskbar would
    give every glyph a faint halo of the wrong shade. Confirmed directly --
    a near-black configured color keyed out over a pale taskbar left
    visibly fuzzy, embossed-looking text; switching the key to a neutral of
    the correct lightness made it crisp again.

    Why derive at all: with a painted panel, foregrounds only ever had to
    work against two known backgrounds (the light preset and the dark one).
    With the panel gone, the tag/percentage/countdown text and the pulse
    mark sit directly on whatever the user's taskbar is -- on Windows 11
    with transparency effects on, a wallpaper-tinted color that is neither.
    #A8A29E secondary text reads clearly on a near-black panel but reaches
    only ~1.9:1 against a pale wallpaper tint.

    Thresholds stay modest (2.5-4.5, not maxed out) on purpose: demanding
    7:1+ against a saturated mid-luminance backdrop forces every color
    fighting for it to the SAME extreme, which just recreates the original
    problem between different elements -- secondary text collapsing to the
    same shade as the marker, and so on.
    """
    base = adaptive_bar_theme(backdrop_hex)
    # The unfilled part of the bar is HOLLOW -- painted in the colorkey, so
    # it is punched out along with the rest of the background and the
    # taskbar shows through it too. The outline, the filled portion and thin
    # time guides are drawn; no background slab is painted inside the track.
    #
    # Two tinted tracks were tried before this and both were wrong for a
    # transparent bar. A real contrast ratio (1.35) lands on a mid-grey
    # against a pale taskbar (#918F8B against a #B6DBF2 backdrop), so the
    # EMPTY part of every bar reads as a heavy filled block -- the opposite
    # of what it means. Dropping to a quiet 1.2 fixed the weight but not
    # the substance: it is still a painted slab sitting on an otherwise
    # transparent widget, which reads as the bar's background rather than
    # as the taskbar continuing through it. Hollow says "nothing here"
    # without having to pick a shade at all, and it is the only version
    # that stays right when the taskbar changes color across the bar's own
    # width.
    track_color = backdrop_hex
    # With a hollow track the outline is the ONLY thing marking where the
    # bar begins and ends, so it is doing more work than before -- but it
    # still should not shout. Half-way between the secondary text and the
    # backdrop, floored at a modest 2.0. Deriving it straight from
    # text_secondary was tried and is too loud on a dark taskbar (a bright
    # #A8A29E box around every bar).
    track_outline = ensure_contrast(
        blend_hex(base["text_secondary"], backdrop_hex, 0.5), backdrop_hex, 2.0
    )
    # Only one contrast check needed now: with the track hollow, the marker
    # is against the taskbar along its whole length, not just where it
    # protrudes past the track.
    marker_color = ensure_contrast(base["marker_color"], backdrop_hex, 3.0)
    return {
        "panel_bg": backdrop_hex,
        "text_primary": ensure_contrast(base["text_primary"], backdrop_hex, 4.5),
        "text_secondary": ensure_contrast(base["text_secondary"], backdrop_hex, 3.0),
        "track_color": track_color,
        "track_outline": track_outline,
        "marker_color": marker_color,
    }


def adaptive_bar_theme(panel_bg_hex: str) -> dict:
    """Given the bar's configured panel background color, returns matching
    text/track/marker colors.

    Note which color the light/dark decision is made against: the PANEL,
    not the taskbar. The embedded bar paints an opaque panel over the
    taskbar (see taskbar_bar._make_opaque_layered), so the taskbar behind
    it is not visible and contributes nothing to legibility -- only the
    panel color the foreground actually sits on matters.

    That is worth spelling out because Settings presents the choice as
    "Dark taskbar / Light taskbar", which reads like it selects this
    light/dark set directly. It does not: it only selects WHICH of the two
    stored colors (bar_bg_color_dark / bar_bg_color_light) is used as the
    panel, and the foreground set is then derived from that color's own
    luminance. So picking "Light taskbar" with a dark color configured
    (e.g. a saturated #0000FF, luminance 0.11) correctly yields the
    dark-panel foreground set -- light text -- which looks like the mode
    switch was ignored, but is the only legible choice for that panel."""
    if is_light_color(panel_bg_hex):
        return {
            "panel_bg": panel_bg_hex,
            "text_primary": "#1A1A1A",
            "text_secondary": "#5A5650",
            "track_color": "#D9D6D0",
            "track_outline": TRACK_OUTLINE,
            "marker_color": "#3A3733",
        }
    return {
        "panel_bg": panel_bg_hex,
        "text_primary": "#FFFFFF",
        "text_secondary": "#A8A29E",
        "track_color": "#000000",
        "track_outline": TRACK_OUTLINE,
        "marker_color": "#E8E4DC",
    }
