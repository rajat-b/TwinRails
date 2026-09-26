# Choosing the bar's colors

What Settings → **Taskbar Bar Appearance** actually controls, and why the
color you pick matters even when it is never painted.

---

## Two background modes

**Transparent (default).** The bar paints no background. The real taskbar
shows through, so it matches perfectly — including a translucent Windows 11
taskbar whose color changes across its own width with the wallpaper behind
it. Only the bars, the text and the pulse mark are drawn.

**Solid.** The configured color is painted as a panel behind the bar. Use
this if the transparent look reads as too busy against your wallpaper.

Toggle it with "Transparent background" in Settings
(`bar_transparent_bg` in config). Unticking it reveals the two panel colors.

---

## What the color does in each mode

| | solid | transparent |
|---|---|---|
| painted as the background | yes | no — it is punched out to transparency |
| decides the text/track colors | yes | yes |
| visible anywhere | the whole panel | only in the anti-aliased edges of glyphs |

In transparent mode the color stops being decoration and becomes a
**description**: it tells the widget what is behind the bar, so it can pick
text colors that stay readable against it. Set it accurately and the text
is crisp and legible; set it far off and the text still stays legible (see
the safety net below) but glyph edges pick up a faint halo of the wrong
shade.

## Which of the two colors is used

Windows decides. The bar reads the same registry value as the safety net
below: `bar_bg_color_light` on a light taskbar, `bar_bg_color_dark` on a dark
one. `_watch_system_theme()` re-applies the theme when the user switches
Windows between light and dark, so the bar follows live.

This used to be a manual "Dark taskbar / Light taskbar" setting, with a
◐ Theme button in the flyout to flip it. Both were removed on 2026-09-25.
Windows already knows the answer, and the safety net overruled any
disagreement anyway, so the setting was a way to be wrong and nothing else.
`bar_theme_mode` is now only the fallback when the registry cannot be read.

**Settings shows the two colors only when transparency is off.** They are
still used in transparent mode, as the colorkey and as the "what is behind
the bar" estimate, but there they only tint the anti-aliased edges of the
glyphs. Hiding them keeps a control off screen that would change almost
nothing visible. The saved values stay in effect either way.

Neither color directly chooses light or dark text. That comes from the
color's own brightness.

---

## The safety net

A badly-chosen color is only cosmetic in solid mode — the text is derived
from the same color that gets painted, so the two always agree. With no
panel, a color that disagrees with the real taskbar is dangerous: a dark
color configured while the taskbar is light would produce **white text on a
near-white taskbar**, i.e. a bar that disappears.

So Windows gets the final say on light-vs-dark. `_backdrop_estimate()` in
[`taskbar_bar.py`](../src/ui/taskbar_bar.py) reads
`HKCU\…\Themes\Personalize\SystemUsesLightTheme` — note *System*, not
*Apps*; Windows tracks those separately and only the system one describes
the taskbar. If the configured color contradicts it, that color's
brightness is discarded and a neutral of the correct brightness is used
instead, for both the text colors and the colorkey.

Verified: with `#00060D` configured and the system in light mode, the bar
still renders dark, crisp, legible text on a pale taskbar.

This is a lookup, not a pixel sample — deliberately. It gives no exact
color, but it answers the one question that has to be right, and answers
it reliably. Sampling was tried before and dropped (see below).

---

## Derived palette

With a **solid** panel (`adaptive_bar_theme`), foregrounds are fixed, since
they only ever sit on one of two known backgrounds:

| | light panel | dark panel |
|---|---|---|
| `text_primary` | `#1A1A1A` | `#FFFFFF` |
| `text_secondary` | `#5A5650` | `#A8A29E` |
| `track_color` | `#D9D6D0` | `#000000` |
| `marker_color` | `#3A3733` | `#E8E4DC` |

With a **transparent** background (`transparent_bar_theme`), every value is
re-derived against the backdrop estimate to a minimum contrast ratio:

| element | min contrast | why that number |
|---|---|---|
| `text_primary` | 4.5 | ordinary body text |
| `text_secondary` | 3.0 | tags and countdowns, smaller and less critical |
| `track_outline` | 2.0 | the only thing marking the bar's extent; shouldn't shout |
| `track_color` | — | **hollow** — painted in the colorkey, so the taskbar shows through it too |
| `marker_color` | 3.0 vs backdrop, 1.8 vs track | it crosses both |
| percentage text | 2.8 | see below |

Two of those were settled by looking at the result rather than by picking a
standard:

- **The track is hollow, not tinted.** Two tinted versions were tried
  first. A real contrast ratio (1.35) lands on a mid-grey against a pale
  taskbar (`#918F8B` against a `#B6DBF2` backdrop), so the *empty* part of
  every bar reads as a heavy filled block — the opposite of what it means.
  Dropping to a quiet 1.2 fixed the weight but not the substance: still a
  painted slab on an otherwise transparent widget, reading as the bar's
  background rather than as the taskbar continuing through it. Hollow says
  "nothing here" without having to pick a shade at all, and it is the only
  version that stays right when the taskbar changes color across the bar's
  own width.
- **Percentage text, 2.8.** This is one of two places a fixed status color
  is drawn straight onto the taskbar, and the color *is* the message
  (coral = fine, amber = watch it, red = trouble). Forcing 4.5 against a
  pale taskbar drives coral, amber and red to near-identical dark browns —
  legible, but no longer telling you anything. The time rail's red parts —
  the lockout tail and its stop cap — get the same treatment for the same
  reason, and for the same reason again: they are drawn straight onto the
  taskbar with nothing behind them.

The time rail's two *neutral* tones are not fixed status colors and do not
need any of this. The unfilled rail reuses the track outline color and the
elapsed portion reuses the secondary text color, so both follow the sampled
theme automatically. See [Reading the bar](reading-the-bar.md).

**The mark on the left follows the same rules.** Its quota rail is painted
in `track_color` with a `track_outline` border, so it is hollow wherever
the bars are. Its time rail's unfilled part is the track outline colour,
and the elapsed part is the secondary text colour, exactly like a row.
Until 2026-09-26 the mark used fixed dark greys copied from the tray icon
(`#1E1E1E` with a `#57514C` border, and a `#3B3835` time rail). On a dark
taskbar that passed. On a light one the quota rail's empty half became a
solid black box, which reads as "full" and is the opposite of the bars next
to it. The time rail's unused part also landed on the same dark grey as
its elapsed part (`#3B3835` against `#3E3B38`), so the split disappeared.
The logo image (tray icon, flyout header, taskbar button, and the README's
`docs/logo.svg`) keeps fixed colours, because it is drawn on its own dark
tile and never meets the taskbar colour directly. Its empty half follows
the same idea by other means: since 2026-09-26 it is unpainted, and only a
thicker, lighter outline marks it. The old near-tile fill made that half
vanish in the flyout header. The comment in `render_tray_icon()` lists the
options rejected along the way.

The pacing *fills* keep their true brand colors and are never adjusted.
They sit inside the bar with the outline framing them, so they are read
against the fill's own edges rather than against the taskbar — which is
also why the colored bar, not the colored number, is the reliable signal on
a pale taskbar.

One guard covers the fill: if a fill color ever landed exactly on the
colorkey it would vanish rather than clash, so it is nudged one RGB unit
(`_not_keyed_out`). Everything else clears the key by construction, having
been through `ensure_contrast` against it.

---

## Why there is no auto-detection of the exact color

An earlier version sampled the taskbar with `GetPixel` and it was dropped
as unreliable.

Worth knowing before anyone tries again: with transparency effects on,
**the taskbar has no single color.** Measured across one 3840px taskbar:

```
x= 200  #D2C37C
x= 800  #DCF2B9
x=1400  #D8EDB5
x=2000  #DAEFB7
x=2900  #DDF2B6
```

A single sampled pixel would be right in one spot and wrong everywhere
else, it changes with the wallpaper, and it cannot be sampled where the bar
itself covers the taskbar. Transparent mode sidesteps the problem entirely:
rather than trying to match a color that varies, it paints nothing.

---

## Gotcha: color advice from before this fix is void

Before the embedded bar was made a layered window, its pixels were *added*
to the taskbar behind them, so the configured color barely showed and
light-mode colors washed out entirely — see
[Taskbar embedding and rendering](taskbar-embedding.md). Any color picked
by eye before that fix was tuned against a blend, not against the color
that was actually configured.
