"""
Windows 11 Fluent styled taskbar flyout widget for TwinRails.
Opens right above the taskbar tray icon or docked bar when clicked.
"""

import tkinter as tk
import ctypes
from ctypes import wintypes
from typing import Optional, Callable, Union, List

from .theme import (
    BG_MAIN, BG_CARD, BG_HOVER, BG_TRACK, BORDER_CARD, BORDER_ACTIVE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    CLAUDE_CORAL, CLAUDE_BLUE, CLAUDE_VIOLET, CREDITS_GREEN,
    DANGER_RED, WARNING_AMBER, SUCCESS_GREEN, FONT_FAMILY,
    FONT_SIZE_CAPTION, FONT_SIZE_BODY, FONT_SIZE_HEADING,
    BREAKDOWN_ORDER, BREAKDOWN_COLORS
)
from PIL import ImageTk

from .custom_progress import CustomProgressBar
from .icon_renderer import render_tray_icon
from .scrollbar import DarkScrollbar
from .text_wrap import wrap_to_width
from .dpi import (
    get_work_area_for_point_or_window,
    get_dpi_for_window,
    get_system_metrics_for_dpi,
    get_dpi_scale_for_window,
)
from ..core.models import (
    UsageWindow, CreditsMetric, UnifiedUsageState, ClaudeUsageState, format_ago
)
from ..core.config import ConfigManager


def get_taskbar_position_and_work_area(
    target_pt: Optional[tuple] = None, hwnd: Optional[int] = None
):
    wl, wt, wr, wb = get_work_area_for_point_or_window(target_pt or hwnd)
    dpi = get_dpi_for_window(hwnd)
    sw = get_system_metrics_for_dpi(0, dpi)
    sh = get_system_metrics_for_dpi(1, dpi)
    rect = wintypes.RECT(wl, wt, wr, wb)
    return rect, sw, sh


def get_cursor_pos():
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


BREAKDOWN_BAR_H = 10
BREAKDOWN_GAP = 2   # card-coloured gap between segments; part of the CVD encoding
BREAKDOWN_MIN_SEG = 2


def fold_breakdown(rows) -> list:
    """(name, share, key) per product in BREAKDOWN_ORDER, for the stacked bar.

    Product ids with no colour of their own fold into "other" rather than
    getting a generated fifth colour -- see BREAKDOWN_COLORS. Products the
    API reports at 0% are kept so the legend can show them as 0%."""
    shares, names = {}, {}
    for name, percent, key in rows:
        slot = key if key in BREAKDOWN_COLORS else "other"
        shares[slot] = shares.get(slot, 0.0) + max(0.0, percent)
        if slot == key:
            names[slot] = name
        else:
            names.setdefault(slot, "Other")
    return [(names[k], shares[k], k) for k in BREAKDOWN_ORDER if k in shares]


def breakdown_spans(segments, width: int) -> list:
    """Pixel (x0, x1, key) for each non-zero segment across `width`, with
    BREAKDOWN_GAP between neighbours. Shares are normalised by their own sum,
    since Claude's rounded percents need not add to exactly 100."""
    drawn = [(key, share) for _, share, key in segments if share > 0]
    total = sum(share for _, share in drawn)
    # Every drawn segment gets BREAKDOWN_MIN_SEG px up front so a 0.4% share
    # stays visible (the legend carries its exact value); only the rest is
    # shared out. Bumping slivers afterwards instead overran the width.
    avail = width - (BREAKDOWN_GAP + BREAKDOWN_MIN_SEG) * len(drawn) + BREAKDOWN_GAP
    if not drawn or total <= 0 or avail < 0:
        return []
    spans, cum, x0 = [], 0.0, 0
    for i, (key, share) in enumerate(drawn):
        cum += share
        x1 = round(avail * cum / total) + (BREAKDOWN_MIN_SEG + BREAKDOWN_GAP) * i + BREAKDOWN_MIN_SEG
        spans.append((x0, x1, key))
        x0 = x1 + BREAKDOWN_GAP
    return spans


class FlyoutWidget:
    def __init__(
        self,
        master: tk.Tk,
        on_refresh: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_toggle_bar: Callable[[], None],
        config_manager: Optional[ConfigManager] = None
    ):
        self.master = master
        self.cfg_mgr = config_manager
        self.on_refresh = on_refresh
        self.on_open_settings = on_open_settings
        self.on_toggle_bar = on_toggle_bar

        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.wm_attributes("-topmost", True)
        self.window.configure(bg=BORDER_CARD)

        # 460 was sized for 3 footer buttons (Refresh/Settings/Bar) -- adding
        # the Theme toggle made a 4th, and this window is a fixed-size
        # overrideredirect Toplevel that doesn't wrap or grow to fit content,
        # so the row just overflowed past the right edge and clipped Bar.
        self.width = 500
        self.height = 420
        self._available_height = None
        self.is_visible = False
        self._hide_timer = None
        self._last_state: Optional[Union[UnifiedUsageState, ClaudeUsageState]] = None

        self._build_ui()

        self.window.bind("<FocusOut>", self._on_focus_out)
        self.window.bind("<Escape>", lambda e: self.hide())
        self.window.bind("<Enter>", lambda e: self.cancel_auto_hide())
        self.window.bind("<Leave>", lambda e: self.schedule_auto_hide())

    def _build_ui(self):
        self.container = tk.Frame(self.window, bg=BG_MAIN, padx=16, pady=16)
        self.container.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # 1. Header (Logo, Title, Badge, Close Button)
        header_frame = tk.Frame(self.container, bg=BG_MAIN)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        # The real app mark -- the same image as the tray icon and the
        # README's social preview -- at a size that reads as the app's
        # identity. It used to be a 20px hand-drawn sketch of the mark,
        # unscaled for DPI, next to a solid green LIVE pill that took all the
        # attention (changed 2026-09-25). The status is now quiet text.
        try:
            scale = get_dpi_scale_for_window(self.window.winfo_id())
        except Exception:
            scale = 1.0
        self._logo_image = ImageTk.PhotoImage(
            render_tray_icon(round(32 * scale)), master=self.window
        )
        tk.Label(
            header_frame, image=self._logo_image, bg=BG_MAIN, bd=0
        ).pack(side=tk.LEFT, padx=(0, 10))

        title_lbl = tk.Label(
            header_frame,
            text="TwinRails",
            font=(FONT_FAMILY, 15, "bold"),
            fg=TEXT_PRIMARY,
            bg=BG_MAIN
        )
        title_lbl.pack(side=tk.LEFT)

        # "● Live": a small green dot, muted text. Demo mode turns both
        # amber, because fake numbers should still be hard to miss.
        self.status_dot = tk.Label(
            header_frame, text="●", font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=SUCCESS_GREEN, bg=BG_MAIN
        )
        self.status_dot.pack(side=tk.LEFT, padx=(12, 2))
        self.badge_lbl = tk.Label(
            header_frame, text="Live", font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=TEXT_MUTED, bg=BG_MAIN
        )
        self.badge_lbl.pack(side=tk.LEFT)

        close_btn = tk.Label(
            header_frame,
            text="✕",
            font=(FONT_FAMILY, 11),
            fg=TEXT_MUTED,
            bg=BG_MAIN,
            cursor="hand2"
        )
        close_btn.pack(side=tk.RIGHT)
        close_btn.bind("<Button-1>", lambda e: self.hide())
        close_btn.bind("<Enter>", lambda e: close_btn.configure(fg=TEXT_PRIMARY))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(fg=TEXT_MUTED))

        # 2. Main Content Frame. Claude can return several model windows and
        # breakdown rows, so let the content scroll when it exceeds the screen.
        self.content_canvas = tk.Canvas(self.container, bg=BG_MAIN,
                                        highlightthickness=0, bd=0)
        # Hidden while every card fits -- see scrollbar.py.
        self.content_scrollbar = DarkScrollbar(
            self.container, orient=tk.VERTICAL, command=self.content_canvas.yview,
        )
        self.content_frame = tk.Frame(self.content_canvas, bg=BG_MAIN)
        self.content_window = self.content_canvas.create_window(
            (0, 0), window=self.content_frame, anchor="nw",
        )
        self.content_frame.bind(
            "<Configure>", lambda _event: self.content_canvas.configure(
                scrollregion=self.content_canvas.bbox("all"),
            ),
        )
        self.content_canvas.bind(
            "<Configure>", lambda event: self.content_canvas.itemconfigure(
                self.content_window, width=event.width,
            ),
        )
        self.content_canvas.configure(yscrollcommand=self.content_scrollbar.set)
        self.window.bind("<MouseWheel>", self._on_mousewheel)

        # Error/status banners for providers that failed to connect (bad or
        # expired Claude key, Antigravity IDE not running, etc). These used
        # to be invisible here: a provider with no metrics contributed zero
        # cards and just vanished from the flyout with nothing explaining
        # why, and the only place its error_message ever showed up was the
        # tray tooltip. Packed above the metric cards, in its own frame so
        # it can be shown/hidden as a block without disturbing card layout.
        self.error_frame = tk.Frame(self.content_frame, bg=BG_MAIN)
        self.error_frame.pack(fill=tk.X, pady=(0, 4))
        self.error_views = []

        self.metrics_frame = tk.Frame(self.content_frame, bg=BG_MAIN)
        self.metrics_frame.pack(fill=tk.X)
        self.card_views = []

        # Claude returns useful account details alongside its quota windows.
        # Keep them below the metric cards: they are facts, not extra quotas.
        self.claude_details_frame = tk.Frame(
            self.content_frame, bg=BG_CARD, padx=12, pady=8,
            highlightthickness=1, highlightbackground=BORDER_CARD,
        )
        self.claude_details_title = tk.Label(
            self.claude_details_frame, text="Claude details",
            font=(FONT_FAMILY, FONT_SIZE_BODY, "bold"), fg=TEXT_PRIMARY, bg=BG_CARD,
        )
        self.claude_details_title.pack(anchor="w")
        self.claude_details_body = tk.Label(
            self.claude_details_frame, text="", font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=TEXT_SECONDARY, bg=BG_CARD, justify=tk.LEFT,
            anchor="w", wraplength=400,
        )
        self.claude_details_body.pack(fill=tk.X, pady=(3, 0))
        wrap_to_width(self.claude_details_body)

        # "This week by product": a 100% stacked bar and its legend, in place
        # of the old one-line-per-product text. The canvas asks for width=1
        # and fills X, so it takes the card's width without pushing the
        # flyout wider, and redraws itself whenever that width changes.
        self.breakdown_frame = tk.Frame(self.claude_details_frame, bg=BG_CARD)
        tk.Label(
            self.breakdown_frame, text="This week by product", font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=TEXT_SECONDARY, bg=BG_CARD, anchor="w",
        ).pack(fill=tk.X)
        self.breakdown_canvas = tk.Canvas(
            self.breakdown_frame, width=1, height=BREAKDOWN_BAR_H,
            bg=BG_CARD, highlightthickness=0,
        )
        self.breakdown_canvas.pack(fill=tk.X, pady=(4, 5))
        self.breakdown_canvas.bind("<Configure>", lambda e: self._draw_breakdown_bar())
        self.breakdown_legend = tk.Frame(self.breakdown_frame, bg=BG_CARD)
        self.breakdown_legend.pack(fill=tk.X)
        self._breakdown_segments = []
        self._breakdown_legend_for = None

        # 3. Footer (Last Updated + Action Buttons)
        self.footer_frame = tk.Frame(self.container, bg=BG_MAIN)
        self.footer_frame.pack(fill=tk.X, pady=(12, 0), side=tk.BOTTOM)

        self.updated_lbl = tk.Label(
            self.footer_frame,
            text="Updated: Just now",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=TEXT_MUTED,
            bg=BG_MAIN
        )
        self.updated_lbl.pack(side=tk.LEFT)

        btn_frame = tk.Frame(self.footer_frame, bg=BG_MAIN)
        btn_frame.pack(side=tk.RIGHT)

        def make_btn(parent, text, cmd):
            lbl = tk.Label(
                parent,
                text=text,
                font=(FONT_FAMILY, FONT_SIZE_BODY),
                fg=TEXT_SECONDARY,
                bg=BG_CARD,
                padx=8,
                pady=3,
                cursor="hand2",
                highlightthickness=1,
                highlightbackground=BORDER_CARD
            )
            lbl.pack(side=tk.LEFT, padx=3)
            lbl.bind("<Button-1>", lambda e: cmd())
            lbl.bind("<Enter>", lambda e: lbl.configure(bg=BG_HOVER, fg=TEXT_PRIMARY))
            lbl.bind("<Leave>", lambda e: lbl.configure(bg=BG_CARD, fg=TEXT_SECONDARY))
            return lbl

        make_btn(btn_frame, "⟳ Refresh", self.on_refresh)
        make_btn(btn_frame, "⚙ Settings", self.on_open_settings)
        make_btn(btn_frame, "▬ Bar", self.on_toggle_bar)

        # Pack the footer before the content so it stays reachable even when
        # the card list needs more vertical room than the monitor provides.
        self.content_scrollbar.auto_pack(side=tk.RIGHT, fill=tk.Y)
        self.content_canvas.pack(fill=tk.BOTH, expand=True)

    def _on_mousewheel(self, event):
        if self.is_visible:
            self.content_canvas.yview_scroll(-int(event.delta / 120), "units")

    def _build_metric_card(self, parent: tk.Widget) -> dict:
        card = tk.Frame(
            parent,
            bg=BG_CARD,
            padx=12,
            pady=8,
            highlightthickness=1,
            highlightbackground=BORDER_CARD
        )
        card.pack(fill=tk.X, pady=3)

        top_row = tk.Frame(card, bg=BG_CARD)
        top_row.pack(fill=tk.X, pady=(0, 2))

        title_lbl = tk.Label(
            top_row,
            text="",
            font=(FONT_FAMILY, FONT_SIZE_BODY, "bold"),
            fg=TEXT_PRIMARY,
            bg=BG_CARD
        )
        title_lbl.pack(side=tk.LEFT)

        reset_lbl = tk.Label(
            top_row,
            text="",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=TEXT_SECONDARY,
            bg=BG_CARD
        )
        reset_lbl.pack(side=tk.RIGHT)

        warning_lbl = tk.Label(
            card,
            text="",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=WARNING_AMBER,
            bg=BG_CARD,
            anchor="w"
        )
        warning_lbl.pack(fill=tk.X, pady=(0, 3))

        bar = CustomProgressBar(
            card,
            height=14,
            fill_color=CLAUDE_CORAL,
            track_color="#1E1E1E",
            outline_color="#383838",
            rail_track_color="#302E2B",
            rail_elapsed_color=TEXT_MUTED,
            bg=BG_CARD
        )
        bar.pack(fill=tk.X, pady=(1, 3))

        bot_row = tk.Frame(card, bg=BG_CARD)
        bot_row.pack(fill=tk.X, pady=(2, 0))

        stat_left = tk.Label(
            bot_row,
            text="",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION),
            fg=TEXT_MUTED,
            bg=BG_CARD
        )
        stat_left.pack(side=tk.LEFT)

        stat_right = tk.Label(
            bot_row,
            text="",
            font=(FONT_FAMILY, FONT_SIZE_CAPTION, "bold"),
            fg=TEXT_PRIMARY,
            bg=BG_CARD
        )
        stat_right.pack(side=tk.RIGHT)

        return {
            "frame": card,
            "title": title_lbl,
            "reset": reset_lbl,
            "warning": warning_lbl,
            "bar": bar,
            "stat_left": stat_left,
            "stat_right": stat_right
        }

    def _build_error_banner(self, parent: tk.Widget) -> dict:
        """A compact warning row for one disconnected/errored provider, e.g.
        '✱ Claude: Session key is invalid or expired.' Styled distinctly
        from a metric card (amber border, no progress bar) so it reads as a
        status notice rather than another usage number."""
        frame = tk.Frame(
            parent,
            bg=BG_CARD,
            padx=12,
            pady=7,
            highlightthickness=1,
            highlightbackground=WARNING_AMBER
        )
        frame.pack(fill=tk.X, pady=3)

        msg_lbl = tk.Label(
            frame,
            text="",
            font=(FONT_FAMILY, FONT_SIZE_BODY),
            fg=TEXT_PRIMARY,
            bg=BG_CARD,
            anchor="w",
            justify="left",
            wraplength=380
        )
        msg_lbl.pack(fill=tk.X)
        wrap_to_width(msg_lbl)

        return {"frame": frame, "msg": msg_lbl}

    def _provider_error_lines(self, state: UnifiedUsageState) -> list:
        """Returns '<icon> <name>: <message>' for every enabled provider that
        isn't connected -- skipped entirely in demo mode, where providers
        are expected to report canned data rather than real connectivity."""
        if state.is_demo:
            return []
        lines = []
        for p in state.providers.values():
            if p.is_connected:
                continue
            msg = p.error_message or p.status_message or "Offline"
            lines.append(f"{p.icon_symbol}  {p.provider_name}: {msg}")
        return lines

    def update_state(self, state: Union[UnifiedUsageState, ClaudeUsageState]):
        self._last_state = state

        # Determine all metrics across providers
        if isinstance(state, UnifiedUsageState):
            all_metrics = state.all_metrics
            is_demo = state.is_demo
            updated_str = format_ago(state.last_updated) if state.last_updated else "Just now"
            error_lines = self._provider_error_lines(state)
            claude_details = state.providers.get("claude")
        else:
            all_metrics = state.all_metrics
            is_demo = state.is_demo
            updated_str = format_ago(state.last_updated) if state.last_updated else "Just now"
            error_lines = [state.error_message] if (state.error_message and not is_demo) else []
            claude_details = state

        if self.cfg_mgr:
            cfg = self.cfg_mgr.config
            widget_keys = getattr(cfg, "widget_metric_keys", []) or []
            flyout_only_keys = getattr(cfg, "flyout_only_metric_keys", []) or []
            active_keys = set(widget_keys) | set(flyout_only_keys)
            _legacy_key_aliases = {
                "claude_five_hour": "five_hour", "five_hour": "claude_five_hour",
                "claude_seven_day": "seven_day", "seven_day": "claude_seven_day",
            }
            known_choice_keys = {
                "claude_five_hour", "gemini_5h", "claude_seven_day",
                "gemini_weekly", "3p_5h", "usage_credits", "five_hour", "seven_day"
            }
            if active_keys:
                filtered = []
                for m in all_metrics:
                    alias = _legacy_key_aliases.get(m.key, m.key)
                    # Claude's returned model limits and valid credit cap are
                    # useful in the flyout before the user visits Settings.
                    # Existing config has no "explicitly hidden" marker.
                    if m.provider_id == "claude" and m.key not in known_choice_keys:
                        filtered.append(m)
                    elif m.key == "usage_credits":
                        filtered.append(m)
                    elif m.key in known_choice_keys or alias in known_choice_keys:
                        if m.key in active_keys or alias in active_keys:
                            filtered.append(m)
                    else:
                        filtered.append(m)
                all_metrics = filtered

        if is_demo:
            self.status_dot.configure(fg=WARNING_AMBER)
            self.badge_lbl.configure(text="Demo data", fg=WARNING_AMBER)
        else:
            self.status_dot.configure(fg=SUCCESS_GREEN)
            self.badge_lbl.configure(text="Live", fg=TEXT_MUTED)

        self.updated_lbl.configure(text=f"Updated: {updated_str}")

        while len(self.error_views) < len(error_lines):
            self.error_views.append(self._build_error_banner(self.error_frame))

        for i, view in enumerate(self.error_views):
            if i < len(error_lines):
                view["msg"].configure(text=error_lines[i])
                view["frame"].pack(fill=tk.X, pady=3)
            else:
                view["frame"].pack_forget()

        num_metrics = len(all_metrics)
        while len(self.card_views) < num_metrics:
            self.card_views.append(self._build_metric_card(self.metrics_frame))

        for i, view in enumerate(self.card_views):
            if i < num_metrics:
                m = all_metrics[i]
                view["frame"].pack(fill=tk.X, pady=3)

                icon = getattr(m, "icon_symbol", "")
                view["title"].configure(text=f"{icon}  {m.label}" if icon else m.label)

                color = getattr(m, "brand_color", CLAUDE_CORAL)
                view["bar"].default_fill_color = color

                if isinstance(m, CreditsMetric):
                    expiry = m.expires_label
                    view["reset"].configure(
                        text=f"{m.value_label} · {expiry}" if expiry else m.value_label
                    )
                    view["warning"].pack_forget()
                    view["bar"].set_values(m.utilization, 0.0, has_time_window=False)
                    view["stat_left"].configure(text=f"Utilized: {m.utilization:.1f}%")
                    view["stat_right"].configure(text=m.remaining_label)
                else:
                    view["reset"].configure(text=m.reset_label)
                    # pace_warning_label now speaks to both outcomes, so the
                    # row can no longer be a warning by construction. Only the
                    # run-dry case gets the warning glyph and amber; landing
                    # comfortably inside the window is good news and is
                    # styled as the ordinary secondary text it is.
                    warn = getattr(m, "pace_warning_label", "")
                    if warn:
                        critical = bool(getattr(m, "is_pace_critical", False))
                        view["warning"].configure(
                            text=(f"⚠ {warn}" if critical else f"→ {warn}"),
                            fg=(WARNING_AMBER if critical else TEXT_MUTED)
                        )
                        # before= is load-bearing: a plain pack() after an
                        # earlier pack_forget() appends the label to the END
                        # of the card, below the stats row. Cards then drifted
                        # into a mixed layout depending on whether their line
                        # had ever been hidden, even within one flyout.
                        view["warning"].pack(fill=tk.X, pady=(0, 3), before=view["bar"])
                    else:
                        view["warning"].pack_forget()

                    view["bar"].set_values(
                        m.utilization,
                        getattr(m, "elapsed_ratio", 0.0),
                        exhaustion_ratio=getattr(m, "exhaustion_ratio", None),
                        has_time_window=bool(getattr(m, "has_time_window", True)),
                        time_divisions=(
                            m.time_division_count
                            if self.cfg_mgr is None or self.cfg_mgr.config.show_time_divisions
                            else 0
                        )
                    )
                    view["stat_left"].configure(text=f"Utilized: {m.utilization:.1f}%")
                    rem = getattr(m, "remaining_pct", 100.0 - m.utilization)
                    view["stat_right"].configure(text=f"{rem:.1f}% remaining")
            else:
                view["frame"].pack_forget()

        notes = list(claude_details.extra_usage_notes) if claude_details is not None else []
        segments = fold_breakdown(claude_details.weekly_breakdown) if claude_details is not None else []
        # Explicit after= on every re-pack, for the same reason as the
        # metric cards' warning line: a bare pack() after pack_forget()
        # appends to the end and the order drifts.
        if notes:
            self.claude_details_body.configure(text="\n".join(notes))
            self.claude_details_body.pack(fill=tk.X, pady=(3, 0), after=self.claude_details_title)
        else:
            self.claude_details_body.pack_forget()
        if segments:
            self._breakdown_segments = segments
            self._update_breakdown_legend(segments)
            self.breakdown_frame.pack(
                fill=tk.X, pady=(8 if notes else 4, 0),
                after=self.claude_details_body if notes else self.claude_details_title,
            )
            self._draw_breakdown_bar()
        else:
            self.breakdown_frame.pack_forget()
        if notes or segments:
            self.claude_details_frame.configure(
                highlightbackground=(
                    WARNING_AMBER if any("cap reached" in line for line in notes)
                    else BORDER_CARD
                )
            )
            self.claude_details_frame.pack(fill=tk.X, pady=(6, 0))
        else:
            self.claude_details_frame.pack_forget()

        self._resize_to_content()

    def _draw_breakdown_bar(self):
        canvas = self.breakdown_canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        spans = breakdown_spans(self._breakdown_segments, width)
        if not spans and width > 1 and self._breakdown_segments:
            # Every product at 0%: an empty track, the legend says why.
            canvas.create_rectangle(0, 0, width, BREAKDOWN_BAR_H, fill=BG_TRACK, outline="")
        for x0, x1, key in spans:
            canvas.create_rectangle(x0, 0, x1, BREAKDOWN_BAR_H,
                                    fill=BREAKDOWN_COLORS[key], outline="")

    def _update_breakdown_legend(self, segments):
        """One swatch + "Name 36%" per product. Rebuilt only when the
        numbers change, not on every refresh. Values stay in text colours;
        the swatch alone carries the product's colour."""
        signature = tuple(segments)
        if signature == self._breakdown_legend_for:
            return
        self._breakdown_legend_for = signature
        for child in self.breakdown_legend.winfo_children():
            child.destroy()
        for name, share, key in segments:
            item = tk.Frame(self.breakdown_legend, bg=BG_CARD)
            item.pack(side=tk.LEFT, padx=(0, 12))
            tk.Frame(item, width=8, height=8, bg=BREAKDOWN_COLORS[key]).pack(side=tk.LEFT, padx=(0, 4))
            tk.Label(
                item, text=f"{name} {share:g}%", font=(FONT_FAMILY, FONT_SIZE_CAPTION),
                fg=TEXT_SECONDARY if share > 0 else TEXT_MUTED, bg=BG_CARD,
            ).pack(side=tk.LEFT)

    def _resize_to_content(self):
        self.window.update_idletasks()
        try:
            scale = get_dpi_scale_for_window(self.window.winfo_id())
        except Exception:
            scale = 1.0
        req_w = self.container.winfo_reqwidth() + 2
        content_h = self.content_frame.winfo_reqheight()
        chrome_h = self.container.winfo_reqheight() - self.content_canvas.winfo_reqheight() + 2
        req_h = chrome_h + content_h
        self.width = max(round(500 * scale), req_w)
        desired_h = max(round(340 * scale), req_h)
        self.height = min(desired_h, self._available_height or desired_h)
        self.content_canvas.configure(height=max(80, self.height - chrome_h))
        self.window.geometry(f"{self.width}x{self.height}")

    def show(self, anchor_x: Optional[int] = None, anchor_y: Optional[int] = None,
             anchor_w: int = 0, anchor_h: int = 0):
        target_pt = (anchor_x, anchor_y) if (anchor_x is not None and anchor_y is not None) else get_cursor_pos()
        try:
            hwnd = self.window.winfo_id()
        except Exception:
            hwnd = None
        work_area, sw, sh = get_taskbar_position_and_work_area(target_pt, hwnd)
        wl, wt, wr, wb = work_area.left, work_area.top, work_area.right, work_area.bottom
        self._available_height = max(180, wb - wt - 24)
        self._resize_to_content()

        if anchor_x is not None and anchor_y is not None:
            cx = anchor_x + (anchor_w // 2)
            target_x = cx - (self.width // 2)
            target_y = anchor_y - self.height - 8
        else:
            mx, my = target_pt
            target_x = mx - (self.width // 2)
            target_y = wb - self.height - 12

        if target_x + self.width > wr - 12:
            target_x = wr - self.width - 12
        if target_x < wl + 12:
            target_x = wl + 12
        if target_y < wt + 12:
            target_y = wt + 12
        if target_y + self.height > wb - 12:
            target_y = wb - self.height - 12

        self.window.geometry(f"{self.width}x{self.height}+{target_x}+{target_y}")
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self.is_visible = True

    def hide(self):
        self.cancel_auto_hide()
        self.window.withdraw()
        self.is_visible = False

    def toggle(self):
        if self.is_visible:
            self.hide()
        else:
            self.show()

    def schedule_auto_hide(self, delay_ms: int = 400, is_cursor_in_bar_fn: Optional[Callable[[], bool]] = None):
        self.cancel_auto_hide()
        def _check_and_hide():
            if is_cursor_in_bar_fn and is_cursor_in_bar_fn():
                return
            if self.is_cursor_inside():
                return
            self.hide()
        self._hide_timer = self.window.after(delay_ms, _check_and_hide)

    def cancel_auto_hide(self):
        if self._hide_timer:
            self.window.after_cancel(self._hide_timer)
            self._hide_timer = None

    def is_cursor_inside(self) -> bool:
        if not self.window.winfo_exists() or not self.window.winfo_viewable():
            return False
        cx, cy = get_cursor_pos()
        wx = self.window.winfo_rootx()
        wy = self.window.winfo_rooty()
        ww = self.window.winfo_width()
        wh = self.window.winfo_height()
        return wx <= cx <= wx + ww and wy <= cy <= wy + wh

    def _on_focus_out(self, event=None):
        self.window.after(100, self._check_focus_lost)

    def _check_focus_lost(self):
        if not self.is_visible:
            return
        if self.is_cursor_inside():
            return
        self.hide()
