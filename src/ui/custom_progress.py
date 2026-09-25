"""
Custom progress bar widget: a rounded quota track with an animated fill, and
beneath it a slim time rail carrying everything measured in time rather than
in quota.

The two are kept on separate rails on purpose. The bar's length means "how
much of the quota is spent", and its only limit is the right-hand edge. The
projected run-out point is a moment in TIME, so drawing it across the bar --
as this widget used to, as a dashed vertical line -- put a point in time on
an axis of quantity, and made the fill look like it was racing toward a wall
that was not there. The rail gives time its own axis so the two can never
collide. The one mark that legitimately spans both is the "now" needle,
which exists precisely to be read against the fill front.
"""

import tkinter as tk
from typing import Optional
from .theme import (
    BG_TRACK, CLAUDE_CORAL, DANGER_RED, WARNING_AMBER, MARKER_COLOR,
    EXHAUSTION_MARKER_COLOR, TEXT_SECONDARY
)


class CustomProgressBar(tk.Canvas):
    """
    A modern, sleek progress bar widget drawn on Tkinter Canvas.
    Supports:
    - Rounded ends
    - Dynamic fill based on utilization percentage
    - Vertical time marker line showing position in the reset window
    """
    def __init__(
        self,
        master,
        height: int = 14,
        fill_color: str = CLAUDE_CORAL,
        track_color: str = BG_TRACK,
        marker_color: str = MARKER_COLOR,
        exhaustion_marker_color: str = EXHAUSTION_MARKER_COLOR,
        outline_color: Optional[str] = None,
        rail_track_color: Optional[str] = None,
        rail_elapsed_color: Optional[str] = None,
        marker_protrusion: int = 2,
        marker_width: int = 2,
        rail_height: int = 3,
        rail_gap: int = 3,
        **kwargs
    ):
        self.bar_height = height
        self.rail_height = max(1, rail_height)
        self.rail_gap = max(0, rail_gap)
        self.marker_protrusion = max(0, marker_protrusion)
        self.marker_width = max(1, marker_width)
        self.default_fill_color = fill_color
        self.track_color = track_color
        self.marker_color = marker_color
        self.exhaustion_marker_color = exhaustion_marker_color
        self.outline_color = outline_color
        self.rail_track_color = rail_track_color or (outline_color or track_color)
        self.rail_elapsed_color = rail_elapsed_color or TEXT_SECONDARY

        self.utilization = 0.0  # 0.0 to 100.0
        self.elapsed_ratio = 0.0  # 0.0 to 1.0
        self.exhaustion_ratio: Optional[float] = None  # 0.0 to 1.0, or None
        self.has_time_window = True  # False hides the rail entirely
        self.time_divisions = 0
        self.override_fill_color: Optional[str] = None

        total_canvas_height = (
            height + self.rail_gap + self.rail_height + 2 * self.marker_protrusion
        )
        super().__init__(
            master,
            height=total_canvas_height,
            highlightthickness=0,
            bd=0,
            bg=kwargs.pop("bg", master.cget("bg")),
            **kwargs
        )

        self.bind("<Configure>", self._on_resize)

    def set_values(
        self,
        utilization_pct: float,
        elapsed_ratio: float = 0.0,
        fill_color: Optional[str] = None,
        exhaustion_ratio: Optional[float] = None,
        has_time_window: bool = True,
        time_divisions: int = 0
    ):
        self.utilization = max(0.0, min(100.0, utilization_pct))
        self.elapsed_ratio = max(0.0, min(1.0, elapsed_ratio))
        self.exhaustion_ratio = (
            max(0.0, min(1.0, exhaustion_ratio)) if exhaustion_ratio is not None else None
        )
        self.has_time_window = has_time_window
        self.time_divisions = max(0, time_divisions)
        self.override_fill_color = fill_color
        self._redraw()

    def set_theme(
        self,
        bg: Optional[str] = None,
        track_color: Optional[str] = None,
        marker_color: Optional[str] = None
    ):
        """Re-themes an already-built bar in place (canvas bg, track, and marker
        color) -- used by the taskbar bar to match whatever color the real
        taskbar is actually rendering (light or dark), sampled at runtime."""
        if bg is not None:
            self.configure(bg=bg)
        if track_color is not None:
            self.track_color = track_color
        if marker_color is not None:
            self.marker_color = marker_color
        self._redraw()

    def _on_resize(self, event=None):
        self._redraw()

    def _draw_rounded_rect(self, x1, y1, x2, y2, radius, color, outline=""):
        """Draw a smooth rounded rectangle on canvas."""
        if x2 <= x1 or y2 <= y1:
            return
        width = x2 - x1
        height = y2 - y1
        r = min(radius, height // 2, width // 2)

        if r <= 0:
            self.create_rectangle(x1, y1, x2, y2, fill=color, outline=outline)
            return

        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1
        ]
        self.create_polygon(points, fill=color, outline=outline, smooth=True)

    def _redraw(self):
        self.delete("all")
        width = self.winfo_width()
        canvas_height = self.winfo_height()
        if width <= 2 or canvas_height <= 2:
            return

        # The bar and the rail are laid out as one block, top-aligned within
        # whatever height the canvas ended up with, so the rail always hangs
        # directly under the bar rather than floating at the canvas floor.
        rail_block = (self.rail_gap + self.rail_height) if self.has_time_window else 0
        track_h = min(self.bar_height, max(1, canvas_height - rail_block))
        track_y1 = max(
            self.marker_protrusion,
            (canvas_height - rail_block - track_h) // 2
        )
        track_y2 = track_y1 + track_h
        rail_y1 = track_y2 + self.rail_gap
        rail_y2 = rail_y1 + self.rail_height
        radius = track_h // 2

        # 1. Background Track (with optional outline)
        self._draw_rounded_rect(0, track_y1, width, track_y2, radius, self.track_color, outline=self.outline_color or "")

        # 2. Fill Color selection
        if self.override_fill_color:
            current_fill = self.override_fill_color
        elif self.utilization >= 95.0:
            current_fill = DANGER_RED
        elif self.utilization >= 80.0:
            current_fill = WARNING_AMBER
        else:
            current_fill = self.default_fill_color

        # 3. Progress Fill
        fill_width = (self.utilization / 100.0) * width
        if fill_width > 2:
            self._draw_rounded_rect(0, track_y1, fill_width, track_y2, radius, current_fill)

        # 4. Time rail -- the window's own axis, drawn only for metrics that
        # sit on a clock. A balance has no window to be part-way through, and
        # an empty rail would read as "no time has passed" rather than as
        # "not applicable".
        if self.has_time_window:
            stop_item = None
            self._draw_rounded_rect(
                0, rail_y1, width, rail_y2, self.rail_height // 2, self.rail_track_color
            )
            if self.elapsed_ratio > 0.0:
                self._draw_rounded_rect(
                    0, rail_y1, int(self.elapsed_ratio * width), rail_y2,
                    self.rail_height // 2, self.rail_elapsed_color
                )

            # 5. Lockout tail -- from the projected run-dry moment to the END
            # of the window, never back to "now". It is the stretch of the
            # window you would spend with nothing left, so it grows as you
            # overspend. (Drawn from "now" instead, it would be the lead you
            # have left, and would shrink as things got worse.) The stop cap
            # at its head is the moment itself.
            if self.exhaustion_ratio is not None and 0.0 < self.exhaustion_ratio < 1.0:
                ex_x = int(self.exhaustion_ratio * width)
                ex_x = max(1, min(width - 1, ex_x))
                self._draw_rounded_rect(
                    ex_x, rail_y1, width, rail_y2,
                    self.rail_height // 2, self.exhaustion_marker_color
                )
                stop_item = self.create_line(
                    ex_x, rail_y1 - self.marker_protrusion - 1,
                    ex_x, rail_y2 + self.marker_protrusion + 1,
                    fill=self.exhaustion_marker_color,
                    width=self.marker_width,
                    capstyle=tk.ROUND
                )

            # Quiet guides repeat across the quota bar and the time rail.
            # The bar's fill still measures quota; only the rail gives these
            # equal sections a literal hour/day meaning. Drawing the filled
            # ticks in the card background makes 1px gaps without muddying
            # the pacing color. The now needle is drawn after them.
            if self.time_divisions > 1:
                bg_color = self.cget("bg")
                empty_tick_color = self.outline_color or self.rail_track_color
                for index in range(1, self.time_divisions):
                    tick_x = round(index * width / self.time_divisions)
                    top_color = (
                        bg_color if tick_x < fill_width else empty_tick_color
                    )
                    self.create_line(
                        tick_x, track_y1 + 1, tick_x, track_y2 - 1,
                        fill=top_color, width=1
                    )
                    self.create_line(
                        tick_x, rail_y1 - 1, tick_x, rail_y2 + 1,
                        fill=bg_color, width=1
                    )
                if stop_item is not None:
                    self.tag_raise(stop_item)

        # 6. The "now" needle, drawn last so nothing overlaps it, and run
        # through BOTH the bar and the rail as one continuous line. It is the
        # only mark that means the same thing on either rail, and spanning
        # them is what makes the pair read as one gauge: fill front ahead of
        # the needle means you are burning faster than the clock.
        if 0.0 < self.elapsed_ratio < 1.0:
            marker_x = int(self.elapsed_ratio * width)
            marker_x = max(2, min(width - 2, marker_x))
            cap_inset = self.marker_width // 2
            bottom = rail_y2 if self.has_time_window else track_y2
            marker_y1 = max(cap_inset, track_y1 - self.marker_protrusion + cap_inset)
            marker_y2 = min(canvas_height - cap_inset, bottom + self.marker_protrusion - cap_inset)
            self.create_line(
                marker_x, marker_y1,
                marker_x, marker_y2,
                fill=self.marker_color,
                width=self.marker_width,
                capstyle=tk.ROUND
            )
