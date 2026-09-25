# The refresh countdown

The icon at the far left of the bar shows how long until the next automatic
refresh. Today that is a **hollow gauge in the mark's needle** (next section).
The rest of this page describes the **octagonal outline** it replaced, which
is still one line away (`COUNTDOWN_STYLE` in `taskbar_bar.py`), and whose
reasoning still governs anything drawn there.

It exists to answer one question without hovering for the tooltip: *is what I
am looking at current, or am I reading a number from four minutes ago?*

It costs nothing to run. `main.py`'s `tick()` already fires once a second and
already calls `taskbar_bar.update_state()` on every one of those ticks while
the bar is visible, so the countdown adds a single `coords()` call per second
to a pass that was happening anyway. It has no timer of its own, and when the
bar is hidden it is not drawn and not computed.

## Current design: the hollow needle

Adopted on 2026-09-25, after trying it on the real taskbar. The mark's needle
is drawn as a small hollow gauge. It is a 1px outline in the row needles'
theme-derived colour (`_marker_color`). Its inside is painted in the track
colour, which is the colorkey in transparent mode, so the taskbar shows through
exactly like the usage bars' unfilled part. A solid part fills it:

- It is whole the moment fresh data lands.
- It drains from the **bottom**, so the needle keeps crossing the coral quota
  rail, the part that makes the mark recognisable, for as long as possible.
- It is amber for its full length while a fetch is in flight, so a hung fetch
  stays visibly amber.
- Only the hollow outline is left once the next fetch is due.

With no outline around it, the mark grows into the octagon's space
(`_NEEDLE_MARK_BOOST = 1.2`). At 125% the rails are 26px wide and the icon
column is 30px, 4px narrower than the octagon version. The gauge overhangs the
rails by the same whole number of rows above and below: 4 at 125%, measured
on the live bar.

**Why it replaced the octagon.** The countdown only has to answer three
things: fresh, getting stale, or updating now. Solid versus hollow answers
them clearly, so the octagon's extra precision bought little. It is the
minimal option, one mark instead of a mark inside a ring, and that lets the
mark be bigger. It also speaks the widget's own language, since the usage bars
are likewise solid for used and hollow for empty.

**What it gives up.** The octagon's roughly 110px of moving outline is easier
to catch in peripheral vision than a roughly 22px gauge. And the taskbar
mark's needle is now a small box, while the tray icon and the README logo keep
the thin needle.

**Tried on the way, all on the real taskbar:**

- **A plain needle draining from the top over a faint track.** It read as a
  needle cut short, and for half of every cycle the mark lost its coral
  crossing and looked like a different logo.
- **A light-grey drained part.** Grey against white was too close to tell
  apart at taskbar size. Hollow against solid replaced it.
- **The mark at a 1.4 boost** (rails 31px). It read as a fifth usage bar.
- **Sizing the gauge from the 1x needle.** After rounding it stood 1 row above
  the coral rail and 4 below the grey one. The rows are now derived from where
  the rails actually land.

**Gotcha: Tk rounds the two kinds of rectangle differently.** A 1px outline
on `x = n + 0.5` lands on pixel `n + 1`, but a fill-only rectangle covers
`[x0, x1)`. An inset of "outline + 1" therefore put the solid part one pixel
left, over the outline, with a dark gap on the right, as measured on the live
bar. The gauge is placed on half-pixel edges and its inside is derived from
where the outline actually lands. See the needle block in `_build_ui`.

**Switching back** is one line: `COUNTDOWN_STYLE = "octagon"`. The octagon
keeps the 1px and 2px lines it was designed with (scaling them to 3px read as
too heavy), and the mark returns to the octagon's size. Everything below
describes it.

## The shape went circle, square, octagon, circle, octagon

Worth recording in full, because the obvious justification for the octagon is
the wrong one and has already been tried and discarded.

**The wrong reason.** Tk's canvas draws through GDI, which does no
antialiasing, so the first moves away from a circle were made on the assumption
that a hairline circle this small would come out visibly stair-stepped. That
assumption was asserted from theory and checked only against a browser
simulation that thresholded alpha — which exaggerates precisely this artifact.
It is false at this size. A real Tk circle at `r=12` renders cleanly; GDI's
ellipse rasteriser handles this radius well, and the steps are small and
regular enough to read as a curve.

So the countdown went back to being a circle. And then came off it again, on
the only evidence that outranks a zoomed side-by-side render: **how it looks on
a real taskbar at real size.** At 1:1 the circle reads soft and indistinct; the
octagon reads sharp. That is the whole reason the octagon is here.

**The octagon is an appearance choice, not a correctness one.** A circle is a
perfectly legitimate shape that renders fine. Do not "restore" it on the theory
that the octagon was an aliasing workaround — that theory was tested and is
wrong, and the circle was tested and lost on looks.

That said, the octagon *is* crisp, and for a real reason: after horizontal and
vertical, a 45-degree line is the cleanest thing GDI draws, stepping one pixel
across for one pixel down forever. An octagon is made of those two angles and
nothing else. The only edge angles in `_countdown_polygon()` are 0, 45 and 90
degrees, and every vertex lands on an integer pixel.

A **hexagon** was rendered too and fails twice over. Its edges sit at 30/60
degrees, which step irregularly — 2, 2, 1, 2, 2, 1 — and look it at this size.
It also does not fit: a hexagon's widest point is a single vertex, so it
narrows away from the midline fast enough that the pulse mark's ends run into
the sloping edges.

## The one real hazard, which is not about shape

Drawing this with **Pillow** would break transparent-background mode outright.
The bar is a layered window keying out one exact colour, and antialiased edge
pixels are by definition *not* that exact colour, so they survive the key — the
outline would carry a dark halo around it on a light taskbar.

That constraint holds whatever shape is drawn. Every mark on this canvas is a
hard-edged Tk primitive because of it. Do not "improve" this into a
`PhotoImage`.

## Why it costs no space

An early version of this reasoning held that a ring cost space a square did
not. It does not, and that claim was an artifact of squeezing `r=10` strictly
inside the 26px icon column. At that radius the pulse mark — which reaches ±9
plus half of its 2px stroke, so ±10 — had 1px of clearance, which reads as a
collision, and the mark would have had to shrink about 20%.

`COUNTDOWN_HALF = 12` clears the mark by 2px at the midline, where the two come
closest, and by 5px above and below, so the mark keeps exactly the size it
always had. It pokes 1px past the nominal icon column on each side, into space
nothing else uses. A square, an octagon and a circle at this half-extent all
have the same footprint.

Cutting the corners to make the octagon takes away nothing that mattered: the
mark spans ±10 horizontally and ±7 vertically, so it never reached the corners.
`COUNTDOWN_CUT = 7` comes from `2h = (2 + √2)c`, which makes all eight sides
equal, rounded to whole pixels so every vertex sits on the grid.

**Every number on this page is the 1x design.** Since 2026-09-25,
`_calibrate_icon()` scales the whole icon at startup. It uses Tk's display
scaling times a deliberate 1.1 boost, and scales the mark's offsets, the
half-extent, the cut and the line widths together. Until then they were fixed
pixels, so at 125% the text around the icon grew 25% while the icon did not,
and it read as too small.

| | half-extent | cut | icon column | clearance at the midline |
|---|---|---|---|---|
| 1x design | 12 | 7 | 26px | 3px |
| 100% display | 13 | 8 | 28px | 3px |
| 125% display | 16 | 9 | 34px | 4px |

The cut is recomputed from the same `2h = (2 + √2)c` rule and the column is
still the octagon plus 1px each side, so every property above still holds:
whole-pixel vertices, only 0/45/90-degree edges, and the same clearance
ratio. The cost is width, 8px at 125%.

**Filling the logo instead, reconsidered.** The pulse-fill rejection below
predates the current rails mark, so it was re-checked on 2026-09-25. The rate
problem (a fill crawling along flat parts and lurching through a spike) is
gone, because the rails are straight. Filling the *rails* was still rejected:

- Hover brightens them.
- A rail is about 16px long at 1x against about 80px of perimeter.
- The rails mark *is* a miniature usage row, so filling it over time would
  read as a fifth, tiny metric beside the real rows.

Filling the *needle* avoids all three, and was then tried and adopted. See
"Current design: the hollow needle" above.

## Why the faded track underneath is not optional

On its own, a partly-drawn box border reads as a rendering glitch rather than a
gauge — most of an outline with a piece missing looks like something failed to
finish painting. The full shape is always drawn underneath in a faded colour,
so the perimeter is always closed and only its *brightness* varies around the
loop. That is what makes a part-drawn state legible as a value.

## Why it drains instead of filling

Both directions were mocked up. Filling has one real argument for it: every
other bar in this widget fills as something is consumed, and ink proportional
to staleness means the taskbar is quietest when the data is freshest.

Draining won because the remaining perimeter *is* the remaining time, with no
mental inversion to do. It unwinds like a kitchen timer. The head retreats back
towards the start point rather than a tail chasing it round the loop, which is
how countdown rings behave everywhere else.

The path starts at the **middle of the top edge**, not at a vertex. From a
vertex the outline empties along two edges at once near the end, and the eye
cannot tell which of them is the head.

## Why it goes amber mid-fetch

While a fetch is actually in flight the whole perimeter is drawn in
`WARNING_AMBER`. Left to the ordinary rule this moment would draw *nothing* — a
fetch is due exactly when the remaining time has reached zero — so the one
moment the widget is visibly working would be its blankest.

It also makes a hung fetch visible. Amber that never clears is a symptom; an
empty outline looks merely idle.

Amber is already the "provider disconnected" tint on the bars themselves. The
reuse is deliberate and low-risk: that tint is a persistent state on a *row*,
this is a transient state on the *icon*, so the two never appear in the same
place meaning different things.

## Hover

Hovering brightens the outline along with the pulse mark. The whole icon is one
click target, so lighting only half of it would read as the outline being a
separate control.

This is only safe because the countdown encodes its value as a **length**.
Brightness is therefore free to mean something else. That is not a small
detail — it is exactly what killed the most elegant alternative below.

## Alternatives that were mocked up and rejected

- **A small horizontal bar under the mark.** The sharpest option and the
  simplest code, but it is a small horizontal progress bar sitting immediately
  left of four large horizontal progress bars. It reads as a fifth metric that
  has been cut off. An enclosing outline avoids that confusion for free.
- **The pulse mark itself filling left to right.** Costs no space at all and was
  the prettiest idea. It breaks against code already there: the mark brightens
  on hover, and a value made of brightness cannot share 18 pixels with a hover
  state made of brightness. It also has a rate problem — the mark is a flat line
  with one spike in the middle, so the fill crawls along the flat parts and
  lurches through the spike, looking like it stalls and then catches up.
- **Segmented blocks under the mark**, lighting one at a time. Crisp and clearly
  not a usage bar, but 10 seconds per segment on the default interval throws
  away resolution the outline keeps for free.
- **A uniform 2px ring (both track and indicator at 2px)**. Having both at
  2px made the empty track heavy and left no difference in mass between filled
  and unfilled states. Drawing the active fill at 2px over a 1px track gives
  the indicator clear definition while keeping the unfilled perimeter subtle.
- **A circle, a square and a hexagon**, all covered above.
- **A setting to choose the shape.** Considered and deliberately not built. It
  would add a config field, a settings control, docs and a second code path for
  a decorative choice set once and never revisited. Changing `COUNTDOWN_CUT` to
  0 gives a square; swapping the polygon for an arc gives a circle.

## The one thing it does not tell you

**The countdown tracks the poll schedule, not whether the data is any good.**
`_last_fetch_time` is set in a `finally` block, so it resets when a fetch
*finishes*, including when that fetch failed and the widget is still showing the
previous numbers. A freshly-whole outline means "we tried recently", not
"these numbers are current".

That is deliberate. It is a countdown to the next attempt, and it has to stay in
step with the schedule it is predicting or it would misrepresent the one thing
it exists for. Per-provider staleness has its own signal: the affected row's bar
stays dim with an amber tint until that provider reconnects. See
[Reading the bar](reading-the-bar.md).

Clicking the mark to refresh manually resets the countdown, because it also
resets the poll clock — the next automatic fetch really is a full interval away again.

## Wiring, and the 60s floor

The bar takes a `get_refresh_progress` callback rather than having the numbers
pushed in through `update_state()`. The poll clock lives in the app — it is the
same `_last_fetch_time` that decides when a fetch actually happens — and
duplicating it in the widget would let the drawn countdown and the real schedule
drift apart. Passing `None` simply hides the countdown.

`AITaskbarApp.poll_interval()` applies the 60-second floor and consecutive-error
backoff in one place. The default is five minutes. When every enabled provider
fails in the same polling round, the interval doubles, with a one-hour cap; a
round in which any provider succeeds resets it. The countdown uses that same
effective interval so it still predicts the next automatic attempt. A single
optional provider being offline therefore cannot make working providers — or
the shared countdown — appear frozen.

Changing the default alone did not update existing installs: `config.json`
already contains the previous 60-second value because the app saves every
setting. On the first launch with the new config migration, saved 30- and
60-second values become 300 seconds. A saved 120-second choice stays unchanged.
The migration records that it has run, so selecting 60 seconds afterward stays
at 60. An old explicit choice of 60 seconds cannot be distinguished from the
old default and is changed once; the user can select it again in Settings.

This distinction was fixed on 2026-09-22 after the first Task 2 draft treated
*any* provider error as a failed batch. With a saved 60-second interval and an
offline Antigravity provider, that draft stretched the shared Claude timer to
2, 4, 8, then 16 minutes even though Claude was still succeeding. The outline
moved so slowly that it looked stuck.

Automatic fetches pause while the workstation is locked. They continue when
the taskbar bar and flyout are hidden because the tray tooltip still shows
live usage. The saved `show_docked_bar` setting controls whether the bar is
redrawn; Tk can briefly report a reparented bar as not viewable even while
Explorer shows it. That temporary viewability mismatch must not stop the
fetch clock. After unlocking, an overdue fetch runs on the next tick.

The tray-only polling bug was confirmed on 2026-09-23: the prior visibility
gate made zero automatic requests whenever the bar was switched off and the
flyout was closed. The tray tooltip then stayed on its startup value. The
fetch condition now depends on elapsed time and the Windows lock state.

`tick()` always schedules its next run in a `finally` block. A one-off UI or
Windows query failure is logged and can skip one update, but cannot silently
stop the timer for the rest of the process.

Lock detection reads the current session's
[`WTSINFOEX_LEVEL1` session flag](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ns-wtsapi32-wtsinfoex_level1_w).
If Windows does not return that information, polling continues so an unusual
session setup does not freeze the widget. The unlocked query path and skip
logic were exercised locally; a live lock/unlock cycle was not tested during
this release pass.

Before the first fetch ever completes, `_last_fetch_time` is still `0.0` and the
elapsed fraction would come out as decades. It is clamped to 1.0, which reads as
"due now" — which is exactly what it is.

## The track colour, and why it is derived

`_countdown_track_color()` blends from the panel background toward the
countdown's own colour rather than reusing `TRACK_OUTLINE` the way the usage
bars do. `TRACK_OUTLINE` is a fixed dark grey, which on a *light* panel is
darker than `text_secondary` — so a fixed track would out-shout the live segment
it is supposed to sit behind, exactly backwards. Deriving it keeps "the track is
a faded version of the mark" true in both themes, and that is the only thing
separating the two here: both are 1px lines, so there is no difference in mass
to fall back on.

The blend factor is **0.30**, chosen by rendering 0.45 / 0.38 / 0.30 / 0.22 /
0.15 side by side on both a `#1C1B1A` and an `#F3F3F3` panel. The two themes
want different numbers, and 0.30 is the compromise:

| Panel | Wants | Why |
|---|---|---|
| dark | ~0.22 | blending up from near-black reaches `text_secondary`'s brightness early, so a high value leaves the track nearly as bright as the live segment |
| light | ~0.40 | blending down from near-white barely darkens at all at low values, so the track disappears — losing the closed box that stops a part-drawn perimeter reading as a glitch |

Anything outside roughly 0.25–0.40 visibly fails one theme or the other.
Re-check both if it is ever touched.
