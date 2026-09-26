# Taskbar embedding and rendering

How the usage bar gets *inside* the Windows taskbar, and the two win32 details
that decide whether it looks and behaves correctly once it is there.

All of this lives in [`src/ui/taskbar_bar.py`](../src/ui/taskbar_bar.py).

---

## A Tk window is really two windows

This is the fact everything else here depends on.

A `tk.Toplevel` on Windows is **two** win32 windows:

| | class | what it is | how to get it |
|---|---|---|---|
| outer | `TkTopLevel` | the "wrapper" — owns the title, what `EnumWindows` and title searches find | `wm_frame()`, or `GetAncestor(content, GA_PARENT)` before embedding |
| inner | `TkChild` | the "content" window — where the widgets actually draw | `winfo_id()` |

`SetParent` must be given the **content** window. Passing the wrapper reparents
an empty window and leaves the visible one untouched, while every "did it
work?" check that also looks at the wrapper keeps agreeing that it didn't.
That mistake is the origin of a long-standing belief in this codebase that
embedding "never sticks" — it does stick, and has been re-confirmed from a
separate process (the reparented window shows up under `Shell_TrayWnd` in
`EnumChildWindows`, visible, at the right rectangle, with real content
readable in a screen capture).

---

## The bar must be an *opaque layered* window

**Symptom:** the bar looks fine on a dark taskbar and washes out to nothing on
a light one. Panel color barely shows. Dark colors vanish entirely.

**Cause:** Windows 11 composites the taskbar from a surface *with an alpha
channel*. Plain GDI drawing — which is all Tk does — writes RGB and leaves
alpha at 0. DWM then treats those pixels as premultiplied-alpha source, so:

```
result = source + background * (1 - 0)
       = source + background        <-- additive
```

Every pixel of the bar is **added** to the taskbar pixel behind it, saturating
at 255.

Measured directly — six known swatches painted on a canvas reparented into
`Shell_TrayWnd`, then read back from a real screen capture against a
`#DAEFB7` taskbar:

| painted | as a plain child (actual) | additive prediction | as an opaque layered window |
|---|---|---|---|
| `#000000` | `#DBF1B8` (invisible) | `#DAEFB7` | `#000000` |
| `#0000FF` | `#DBF1FF` | `#DAEFFF` | `#0000FF` |
| `#D97756` | `#FFFFFF` | `#FFFFFF` | `#D97756` |
| `#808080` | `#FFFFFF` | `#FFFFFF` | `#808080` |
| `#303030` | `#FFFFE9` | `#FFFFE7` | `#303030` |

This is why dark mode *accidentally* worked: adding a color to a near-black
background is nearly a no-op.

It is also why **no amount of foreground contrast tuning can fix light mode.**
Additive blending can only ever brighten; there is no source color that
produces a dark pixel on a light taskbar. A WCAG-contrast-driven palette was
attempted on the `experiment/taskbar-bar-contrast-fixes` branch and could not
have worked for this reason.

**Fix:** `_make_opaque_layered()` — set `WS_EX_LAYERED` and
`SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)` on the *content*
window. That hands compositing to the window manager, which supplies a real
alpha. Colors then render pixel-exact (right-hand column above).

Tk re-applies its own `-transparentcolor` (a colorkey, not `LWA_ALPHA`) on its
own schedule — any `wm_attributes` call, any `deiconify`. The moment it does,
the bar silently goes back to being additive. `_reassert_embed_window_styles()`
re-checks once per second and only writes when it has drifted, so it costs one
`GetLayeredWindowAttributes` per tick and causes no repaint.

### Transparent background, on top of the same mechanism

Once the window is layered, adding `LWA_COLORKEY` alongside `LWA_ALPHA`
punches one chosen color out to full transparency, so the real taskbar
shows through where the panel would be. That is what
`bar_transparent_bg` (the default) uses, and it is the only way to match a
translucent taskbar whose color varies across its own width.

This is **not** the additive blend coming back. Additive blending was
Windows compositing our pixels wrongly because they had no alpha; a
colorkey is Windows compositing them correctly, having been told which ones
to drop. Everything not keyed out still renders pixel-exact — verified: the
coral fill reads back as exactly `#D97756` with the colorkey active.

Two consequences worth knowing:

- **The keyed color is still visible at glyph edges.** Anti-aliased text is
  blended against the background it was drawn on, so those edge pixels are
  near the key but not equal to it, and stay opaque. Key on a color far
  from the real taskbar and every glyph gets a halo. This is why the key is
  the *backdrop estimate* and not the raw configured color — see
  [Choosing the bar's colors](bar-colors.md).
- **Keyed pixels are click-through**, so clicks on the bar's background go
  to the taskbar underneath. This costs nothing here because input is
  polled from the mouse rather than delivered as window messages (below),
  and a left click on empty taskbar does nothing anyway. Verified by
  really clicking the transparent area: the flyout opens, and closes on a
  second click.
- **The unfilled part of each progress bar is keyed out too**, so the
  taskbar shows through it and the bar reads as hollow with an outline
  rather than as a painted trough. See
  [Choosing the bar's colors](bar-colors.md).

The colorkey is the panel color, so **a theme change is a win32 change**,
not just a canvas one. `_apply_theme()` re-applies the layered attributes
for that reason; without it the *old* color stays keyed out, which means
the new background paints solid while stray pixels of the old color go
see-through — exactly backwards.

The floating fallback does not use this. Its transparency comes from Tk's
own `-transparentcolor` on the wrapper window, and a second, different
colorkey on the content window fights it.

---

## The wrapper window gets left behind

`SetParent` moves only the *content* window into the taskbar. Tk's wrapper
stays behind as a normal top-level: still visible, still topmost, exactly the
bar's size, at whatever floating position the bar had before it embedded. It
keeps growing and shrinking with the bar from that spot, so on a
multi-monitor desk it can reach across onto the next screen.

It causes two separate problems, and each needs its own fix.

### It eats clicks

**Symptom:** an invisible rectangle the size of the bar, sitting in the strip
of screen directly above the taskbar and extending over the area above the
tray icons. Clicking there does nothing visible but disturbs the bar.

**Cause:** the wrapper is **not** click-through: `WindowFromPoint` returned it
across its entire width.

**Fix:** `_set_wrapper_click_through()` — set `WS_EX_TRANSPARENT` on the
wrapper. It stays exactly where it is; it just stops being hit-tested.

### It turns into a black box

**Symptom:** a solid black rectangle as wide as the bar, just above the
taskbar near the tray, covering whatever window is underneath. With two
columns of metrics it is wide enough to spill onto the neighbouring monitor;
switch to one column and it shrinks to match.

**Cause:** nothing paints the wrapper once the content has gone. It keys out
`#000001` (Tk's `-transparentcolor`), so whether it shows depends on what its
surface happens to hold. Left alone that is nothing visible, but any part that
gets drawn fresh comes up `#000000` — one step off the key, so fully opaque.
Measured on the live widget: every pixel of the wrapper's rect on the main
monitor was exactly `#000000`, with the colorkey still set correctly
(`GetLayeredWindowAttributes`: key `#000001`, flags `LWA_COLORKEY | LWA_ALPHA`).

What turns it black is only partly pinned down:

| in an isolated reproduction | wrapper afterwards |
|---|---|
| content reparented away before the first show — what this class does | clear |
| …then grown, shrunk and regrown; `RedrawWindow`; `SWP_FRAMECHANGED`; withdraw + deiconify | still clear |
| content shown inside the wrapper first, then reparented away, then the bar widened | the newly-added strip goes black |

None of that explains the live case, where the wrapper was black across its
*whole* width after the widget had run for about 18 hours. A sleep/wake or
display change is the likeliest cause. It is not confirmed.

**Fix:** `_set_wrapper_see_through()` — set the wrapper's opacity to 0 with
`SetLayeredWindowAttributes`, keeping its colorkey. That hides it whatever its
surface holds, so the fix does not depend on knowing every trigger. Checked on
the live black box: it vanished, and 2 pixels of the embedded bar changed
(the countdown ticking). In the reproduction the opacity also survived
resizes, a Tk `wm_attributes` call, and a withdraw/deiconify cycle.
`_reassert_embed_window_styles()` reads it back once a second anyway, since
these are Tk's layered attributes rather than ours.

Painting the wrapper with the key color was the alternative, and was rejected:
it means subclassing Tk's window procedure to answer `WM_PAINT` and repainting
after every resize, to rebuild by hand what opacity 0 gives for free.

**Not to be confused with** the near-black rectangle from before embedding
worked properly (see the note in `_try_embed()`). That one came from clearing
Tk's `-transparentcolor`, which made the wrapper opaque outright. This one
happens with the colorkey fully intact, so keeping the colorkey is necessary
but not enough.

### Why not simply hide or move it

Both were tried and both break the embedded content, which is a separate
window but still one Tk believes it owns:

| approach | result |
|---|---|
| `ShowWindow(wrapper, SW_HIDE)` | embedded content unmapped — `IsWindowVisible` false, nothing renders at all |
| `SetWindowPos(wrapper, -32000, -32000)` | embedded content dragged along, out of its taskbar slot |
| `SetWindowRgn(wrapper, empty)` | same as above — content moved out of position |
| `WS_EX_TRANSPARENT` | content untouched, still at the right rect, still renders, wrapper stops eating clicks |
| opacity 0 (`SetLayeredWindowAttributes`, colorkey kept) | content untouched, wrapper no longer visible whatever its surface holds |

Both must be **undone** when falling back to the floating panel
(`_undo_embed_window_styles()`):

- Leave `WS_EX_TRANSPARENT` on and the floating bar becomes click-through, so
  the icon-refresh and click-to-open-flyout silently stop working with nothing
  on screen to explain why.
- Leave the opacity at 0 and, wherever the content is back inside the wrapper,
  the floating bar does not show at all. A floating test window at opacity 0
  showed nothing. `_set_layered_alpha()` changes only the opacity, so setting
  255 hands back Tk's own colorkey transparency exactly — verified on the same
  test window.

---

## Why the bar polls the mouse instead of using Tk bindings

Once reparented, Tk's own `<Button-1>` / `<Enter>` / `<Leave>` bindings do not
fire. `_poll_embedded_input()` polls `GetAsyncKeyState` plus the cursor
position against `GetWindowRect` every 50 ms instead.

A useful consequence: **input does not depend on any of the window styles
above.** Layering, colorkeys and hit-testing changes can't break clicking on
the bar, because nothing in the input path goes through window messages.

---

## Repainting

A reparented window does not repaint on its own. `_force_full_repaint()`
(`update_idletasks()` + `update()`) is needed after embedding, and again after
content changes. The post-embed case needs a real time gap (~250 ms) between
the two passes, not two calls back to back — DWM/Explorer needs a moment to
actually process the paint message Tk queues, not merely to have it queued.

This is also why all bar content is drawn on **one flat `tk.Canvas`** rather
than a tree of nested frames and labels: a deep widget tree never renders once
embedded, even when forced to repaint, while a shallow one does.

## Fitting the taskbar's height

A child window is clipped to its parent, and `_reposition_embedded()` pins a
bar taller than the taskbar to its top edge. The two rows as designed need
62px, which Windows 11's taskbar only has at 125% scaling and above (60px, the
last 2px being padding). At 100% it is 48px, and the second row lost its time
rail, lockout tail included; Windows 10's is 40px, which cut its bar in half
too.

So before the canvas is built, `_calibrate_rows()` reads `Shell_TrayWnd`'s
thickness and, only if the rows' drawing would be cut, trims padding and the
row gap, then bar height (14px → 13 at 48px, 9 at 40px). It runs once, so a
taskbar resized while TwinRails runs needs a restart. A 30px taskbar (Windows
10's small buttons) is too thin for two rows at any bar height.

---

## Verifying a change

Screen pixels are the only trustworthy check here — every one of these bugs
looks fine to in-process introspection. A useful loop:

1. Enumerate `Shell_TrayWnd`'s children from a **separate** process and
   confirm the bar is among them, with the expected rect and ex-styles.
   Read back `GetLayeredWindowAttributes`: you want `flags=2` (`LWA_ALPHA`),
   `alpha=255`.
2. Capture the screen and compare the bar's pixels against the exact hex
   colors the theme should have produced. If they instead equal
   `configured_color + taskbar_color`, the opaque-layered attributes are gone.
3. `WindowFromPoint` at the wrapper's rect — it must **not** return the
   wrapper.
4. `GetLayeredWindowAttributes` on the wrapper should read `alpha=0`, and a
   capture of the wrapper's rect should contain no exact `#000000`. The
   wrapper is found with `FindWindowW("TkTopLevel", "TwinRailsBar")`.

Any capture code must enable Per-Monitor V2 DPI awareness first (e.g. `SetProcessDpiAwarenessContext(-4)`, or fallback `SetProcessDpiAwareness(2)`), and offset by
the virtual-desktop origin (`SM_XVIRTUALSCREEN` / `SM_YVIRTUALSCREEN`), which
is not `(0, 0)` on a multi-monitor setup. Getting either wrong silently
samples the wrong part of the screen and looks like a rendering bug.

With Pillow, `ImageGrab.grab(bbox, include_layered_windows=True,
all_screens=True)` handles the origin for you (`bbox` is in virtual-desktop
coordinates). `include_layered_windows` defaults to off, and the bar and its
wrapper are both layered windows.
