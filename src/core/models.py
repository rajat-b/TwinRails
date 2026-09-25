"""
Universal data models for tracking AI usage limits across multiple providers
(Claude and Google Antigravity / Gemini).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Union


def format_countdown(timestamp_or_seconds) -> str:
    """Format seconds or datetime into human-friendly countdown string (e.g. '1h 28m', '1d 3h')."""
    if isinstance(timestamp_or_seconds, datetime):
        now = datetime.now(timezone.utc)
        diff_sec = (timestamp_or_seconds - now).total_seconds()
    else:
        diff_sec = float(timestamp_or_seconds)

    if diff_sec <= 0:
        return "0s"

    total_seconds = int(diff_sec)
    if total_seconds < 60:
        return f"{total_seconds}s"

    total_minutes = round(total_seconds / 60)
    if total_minutes < 60:
        return f"{total_minutes}m"

    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours < 24:
        return f"{hours}h {minutes}m"

    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d {rem_hours}h"


def format_ago(dt: datetime) -> str:
    """Format a past datetime as a relative 'X ago' string (e.g. 'Just now', '5m ago', '2h 3m ago')."""
    now = datetime.now(timezone.utc)
    diff_sec = (now - dt).total_seconds()

    if diff_sec < 10:
        return "Just now"
    if diff_sec < 60:
        return f"{int(diff_sec)}s ago"

    total_minutes = int(diff_sec // 60)
    if total_minutes < 60:
        return f"{total_minutes}m ago"

    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours < 24:
        return f"{hours}h {minutes}m ago" if minutes else f"{hours}h ago"

    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d {rem_hours}h ago" if rem_hours else f"{days}d ago"


MIDDOT = "\u00b7"

# Windows' NOTIFYICONDATA.szTip is a 128-wchar buffer, so a tray tooltip has
# 127 usable characters. That is a hard OS limit, not a style choice.
TOOLTIP_MAX_CHARS = 127


def _fit_tooltip(lines) -> str:
    """Pack tooltip lines into the szTip budget without cutting mid-word.

    The old code just sliced the joined string at 127, which on a fully
    configured setup (two providers, six metrics) chopped the last line into
    a fragment like "$ Usa" -- worse than useless, because a truncated line
    still looks like data. This keeps whole lines only, and says how many it
    had to drop, so a short tooltip reads as "there is more" rather than as
    "that is everything".

    Note the capacity is genuinely small: six metrics with a countdown and an
    exact reset time each need roughly 190 characters, so a full setup cannot
    fit however it is formatted. Dropping lines is the honest failure, and
    the flyout is where the complete picture lives."""
    kept, used = [], 0
    dropped = 0
    for i, line in enumerate(lines):
        # +1 for the newline that would join this line to the previous one.
        cost = len(line) + (1 if kept else 0)
        remaining = len(lines) - i
        # Reserve room for the "+N more" footer, but only while there is
        # actually something left that might not fit.
        reserve = len(f"\n+{remaining} more") if remaining > 1 else 0
        if used + cost + reserve > TOOLTIP_MAX_CHARS:
            dropped = remaining
            break
        kept.append(line)
        used += cost

    if dropped:
        kept.append(f"+{dropped} more")
    return "\n".join(kept)[:TOOLTIP_MAX_CHARS]


@dataclass
class UsageWindow:
    """Represents a time-window usage limit (e.g. 5-hour sprint or 7-day weekly)."""
    key: str = ""                         # unique key, e.g. "claude_five_hour", "gemini_5h"
    label: str = ""                       # display label, e.g. "5-Hour Session", "Gemini 5h"
    provider_id: str = "claude"           # provider id: "claude", "antigravity"
    icon_symbol: str = "✱"                # "✱" for Claude, "✦" for Gemini
    brand_color: str = "#D97756"          # primary brand accent
    utilization: float = 0.0              # % utilized (0.0 to 100.0)
    resets_at: Optional[datetime] = None  # UTC reset datetime
    window_hours: int = 5                 # 5 for sprint, 168 for weekly

    @property
    def remaining_pct(self) -> float:
        """% remaining -- always derived, never stored.

        This used to be a stored field defaulting to 100.0, which only the
        Antigravity provider ever filled in. Every Claude window therefore
        reported "100% remaining" regardless of its real utilization, so the
        flyout card printed "Utilized: 48.0%" and "100.0% remaining" side by
        side and the whole "Show % Remaining" setting was dead for Claude.
        Deriving it makes that impossible to get wrong again."""
        return max(0.0, min(100.0, 100.0 - self.utilization))

    @property
    def short_tag(self) -> str:
        """Compact row-prefix tag for taskbar bar, e.g. '5H', '7D'."""
        if self.provider_id == "claude" and self.key.startswith("limits_weekly_scoped_"):
            # Two Claude weekly rows otherwise both say "7D" on the taskbar.
            # Three letters fit beside the provider mark in its 42px slot.
            model_name = self.label.rsplit(" · ", 1)[-1]
            letters = "".join(c for c in model_name if c.isascii() and c.isalnum())
            return letters[:3].upper() or "MOD"
        if self.window_hours <= 0:
            return ""
        if self.window_hours < 24:
            return f"{self.window_hours}H"
        days = self.window_hours // 24
        return f"{days}D"

    @property
    def time_division_count(self) -> int:
        """Hour/day guides for the two standard reset windows, if applicable."""
        if self.window_hours == 5:
            return 5
        if self.window_hours == 7 * 24:
            return 7
        return 0

    @property
    def is_warning(self) -> bool:
        return self.utilization >= 80.0

    @property
    def is_danger(self) -> bool:
        return self.utilization >= 95.0

    @property
    def seconds_remaining(self) -> float:
        if not self.resets_at:
            return 0.0
        now = datetime.now(timezone.utc)
        return max(0.0, (self.resets_at - now).total_seconds())

    @property
    def countdown_text(self) -> str:
        if not self.resets_at:
            return ""
        return format_countdown(self.resets_at)

    @property
    def exact_time_str(self) -> str:
        if not self.resets_at:
            return ""
        try:
            local_dt = self.resets_at.astimezone()
            return local_dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            return ""

    @property
    def exact_datetime_str(self) -> str:
        if not self.resets_at:
            return ""
        try:
            local_dt = self.resets_at.astimezone()
            weekday = local_dt.strftime("%a")[:2]
            s = f"{weekday}, {local_dt.strftime('%b %d at %I:%M %p')}"
            return s.replace(" 0", " ")
        except Exception:
            return ""

    @property
    def reset_label(self) -> str:
        cd = self.countdown_text
        if not cd:
            return ""
        if self.window_hours <= 24:
            t = self.exact_time_str
            return f"resets in {cd} ({t})" if t else f"resets in {cd}"
        else:
            dt = self.exact_datetime_str
            return f"resets in {cd} ({dt})" if dt else f"resets in {cd}"

    def _short_clock(self, dt: Optional[datetime]) -> str:
        """Compact local wall-clock stamp for the taskbar row -- "4:12p", or
        "We 4:12p" once the window is long enough that the day matters.

        Deliberately compact rather than the "4:12 PM" the flyout uses. The
        taskbar row now carries three time facts side by side (countdown,
        reset, projection) and the long form does not fit -- measured in the
        row's own Segoe UI 9pt, the longest short-form string is already
        163px against a 122px slot, and the long form is far worse."""
        if not dt:
            return ""
        try:
            local_dt = dt.astimezone()
            hm = local_dt.strftime("%I:%M").lstrip("0")
            ampm = local_dt.strftime("%p")[0].lower()
            if self.window_hours <= 24:
                return f"{hm}{ampm}"
            return f"{local_dt.strftime('%a')[:2]} {hm}{ampm}"
        except Exception:
            return ""

    @property
    def compact_reset_label(self) -> str:
        """Exact reset clock time with its countdown bracketed after it,
        e.g. "4:12p (1h 28m)".

        Both, not one or the other: the clock time answers "can I finish
        this before I have to leave", the countdown answers "how long have I
        got left". The clock time leads because it is the fixed fact -- the
        countdown is derived from it and from now.

        Bracketed rather than dot-separated so that the row's two time
        fields each read as one unit. Dot-separating them gave four
        equally-weighted values in a line and no grouping ("1h 28m · 4:12p
        · →3:05p · 1h 19m"), where brackets make the pairing
        obvious at a glance."""
        cd = self.countdown_text
        if not cd or not self.resets_at:
            return cd
        t = self._short_clock(self.resets_at)
        return f"{t} ({cd})" if t else cd

    @property
    def has_time_window(self) -> bool:
        """True when this metric actually sits on a clock, so the time rail
        drawn under its bar means something. False for balances."""
        return bool(self.resets_at) and self.window_hours > 0

    @property
    def projected_end_utilization(self) -> Optional[float]:
        """Utilization you would reach by the time the window resets, if the
        current burn rate held. Deliberately uncapped -- a value over 100 is
        exactly what "you will run dry early" looks like, and callers need
        to see how far over it goes to decide what to say.

        None until 3% of the window has passed, the same guard
        projected_exhaustion_at uses: before that the elapsed denominator is
        tiny and the projection swings wildly between refreshes."""
        if not self.has_time_window or self.utilization <= 0:
            return None
        # Already at (or past) the limit: there is nothing left to project,
        # the outcome has happened. Without this guard the caller falls
        # through to the "you will finish comfortably" branch, because
        # projected_exhaustion_at deliberately returns None at >= 100 too --
        # and a fully spent window would report "on pace to finish around
        # 99%", which is both wrong and reassuring at the worst moment.
        if self.utilization >= 100:
            return None
        ratio = self.elapsed_ratio
        if ratio < 0.03:
            return None
        return self.utilization / ratio

    @property
    def is_pace_critical(self) -> bool:
        """Whether the pace projection is the alarming kind (you run dry
        before the reset) rather than the reassuring kind. Drives the colour
        of pace_label and of the lockout tail on the time rail."""
        return self.projected_exhaustion_at is not None

    @property
    def pace_label(self) -> str:
        """The third field on the taskbar row: where this pace lands you.

        Two forms, one meaning. Both carry a leading arrow so the field
        always reads as a projection rather than as another fact about now:
          on pace to run out -> "→3:05p (1h 19m)"  (when you go dry)
          otherwise          -> "→78%"             (where you finish)

        The first form is bracketed exactly like compact_reset_label, and
        for the same reason: the clock time is the fact, the countdown is
        how long you have got, and the pair belongs together. Only this form
        gets a countdown -- there is no moment to count down to when the
        projection is a landing percentage.

        The arrow earns its pixels in the first form, where the value is a
        clock time sitting immediately after the reset clock time; without
        it the two are indistinguishable at a glance. Colour carries the
        rest of the distinction -- see taskbar_bar's pace colouring.

        The percentage is clamped to 99 rather than 100 on purpose: the
        100 case is unreachable here by construction (a projection at or
        over 100 produces an exhaustion time and takes the branch above),
        so printing "→100%" could only ever mean a rounding artifact of
        99.5-something, which would read as "you run out" when the whole
        point of this branch is that you do not."""
        eta = self.projected_exhaustion_at
        if eta:
            t = self._short_clock(eta)
            if not t:
                return ""
            cd = format_countdown(eta)
            return f"→{t} ({cd})" if cd else f"→{t}"
        projected = self.projected_end_utilization
        if projected is None:
            return ""
        return f"→{min(99.0, projected):.0f}%"

    @property
    def elapsed_ratio(self) -> float:
        if not self.resets_at or self.window_hours <= 0:
            return 0.0
        window_duration = timedelta(hours=self.window_hours)
        window_start = self.resets_at - window_duration
        now = datetime.now(timezone.utc)
        total_seconds = window_duration.total_seconds()
        elapsed_seconds = (now - window_start).total_seconds()
        return max(0.0, min(1.0, elapsed_seconds / total_seconds))

    @property
    def projected_exhaustion_at(self) -> Optional[datetime]:
        if not self.resets_at or self.window_hours <= 0:
            return None
        if self.utilization <= 0 or self.utilization >= 100:
            return None
        ratio = self.elapsed_ratio
        if ratio < 0.03:
            return None

        window_seconds = timedelta(hours=self.window_hours).total_seconds()
        elapsed_seconds = ratio * window_seconds
        if elapsed_seconds <= 0:
            return None

        rate_pct_per_sec = self.utilization / elapsed_seconds
        if rate_pct_per_sec <= 0:
            return None

        seconds_to_full = (100.0 - self.utilization) / rate_pct_per_sec
        eta = datetime.now(timezone.utc) + timedelta(seconds=seconds_to_full)
        if eta >= self.resets_at:
            return None
        return eta

    @property
    def exhaustion_ratio(self) -> Optional[float]:
        """Where the projected exhaustion time falls as a fraction of the
        whole reset window (0.0-1.0) -- the same axis the elapsed-time
        marker uses, so the two can be drawn as two vertical lines on one
        bar and compared directly. None when there's no projection (not
        enough data yet, or not on pace to run out before the reset)."""
        eta = self.projected_exhaustion_at
        if not eta or not self.resets_at or self.window_hours <= 0:
            return None
        window_duration = timedelta(hours=self.window_hours)
        window_start = self.resets_at - window_duration
        total_seconds = window_duration.total_seconds()
        if total_seconds <= 0:
            return None
        elapsed_seconds = (eta - window_start).total_seconds()
        return max(0.0, min(1.0, elapsed_seconds / total_seconds))

    @property
    def pace_warning_label(self) -> str:
        """The flyout's spelled-out version of pace_label. Long-form times
        here ("4:12 PM"), since the card has room the taskbar row does not,
        and both outcomes get a sentence -- being told you will finish
        comfortably is worth as much as being warned you will not."""
        eta = self.projected_exhaustion_at
        if eta:
            try:
                local_dt = eta.astimezone()
                if self.window_hours <= 24:
                    t = local_dt.strftime("%I:%M %p").lstrip("0")
                else:
                    weekday = local_dt.strftime("%a")[:2]
                    t = f"{weekday} {local_dt.strftime('%I:%M %p')}".replace(" 0", " ")
                return f"On pace to run out by {t} (in {format_countdown(eta)})"
            except Exception:
                return ""
        projected = self.projected_end_utilization
        if projected is None:
            return ""
        return f"On pace to finish around {min(99.0, projected):.0f}%"


@dataclass
class CreditsMetric:
    """Represents a capped monetary allowance (e.g. Claude extra usage)."""
    key: str = "usage_credits"
    label: str = "Usage Credits"
    provider_id: str = "claude"
    icon_symbol: str = "✱"
    brand_color: str = "#10B981"
    used: float = 0.0
    total: float = 0.0
    currency: str = "USD"
    # Set only for a grant that lapses on a date (e.g. cloud session
    # credits). A balance still has no time window: nothing is paced
    # against this date, it is just shown.
    expires_at: Optional[datetime] = None

    @property
    def expires_label(self) -> str:
        """Local expiry date, e.g. "expires Nov 5"; empty when there is none."""
        if not self.expires_at:
            return ""
        try:
            local_dt = self.expires_at.astimezone()
            return f"expires {local_dt.strftime('%b')} {local_dt.day}"
        except Exception:
            return ""

    def format_amount(self, amount: float) -> str:
        """Keep non-USD balances labeled with the currency Claude returned."""
        if self.currency.upper() == "USD":
            return f"${amount:,.2f}"
        return f"{self.currency.upper()} {amount:,.2f}"

    @property
    def utilization(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(100.0, (self.used / self.total) * 100.0))

    @property
    def remaining_pct(self) -> float:
        return max(0.0, 100.0 - self.utilization)

    @property
    def short_tag(self) -> str:
        return "$" if self.currency.upper() == "USD" else "¤"

    @property
    def is_warning(self) -> bool:
        return self.utilization >= 80.0

    @property
    def is_danger(self) -> bool:
        return self.utilization >= 95.0

    @property
    def value_label(self) -> str:
        return f"{self.format_amount(self.used)} of {self.format_amount(self.total)}"

    @property
    def remaining_value(self) -> float:
        return max(0.0, self.total - self.used)

    @property
    def remaining_label(self) -> str:
        return f"{self.format_amount(self.remaining_value)} left"

    @property
    def compact_reset_label(self) -> str:
        return self.remaining_label

    @property
    def elapsed_ratio(self) -> float:
        return 0.0

    @property
    def exhaustion_ratio(self) -> Optional[float]:
        return None

    @property
    def has_time_window(self) -> bool:
        """A balance is not on a clock, so no time rail is drawn under it."""
        return False

    @property
    def is_pace_critical(self) -> bool:
        return False

    @property
    def pace_label(self) -> str:
        return ""

    @property
    def pace_warning_label(self) -> str:
        return ""


@dataclass
class ProviderUsageState:
    """Usage snapshot for a single AI provider."""
    provider_id: str = ""
    provider_name: str = ""
    icon_symbol: str = ""
    brand_color: str = "#FFFFFF"
    windows: List[UsageWindow] = field(default_factory=list)
    credits: Optional[CreditsMetric] = None
    # Dollar grants separate from the usage-credits balance above, such as
    # Claude's cloud session credits. See claude_client._extract_included_credits.
    included_credits: List[CreditsMetric] = field(default_factory=list)
    extra_usage_notes: List[str] = field(default_factory=list)
    # (display name, percent, stable key) rows -- see ClaudeUsageState.
    weekly_breakdown: List[tuple[str, float, str]] = field(default_factory=list)
    is_connected: bool = False
    is_loading: bool = False
    is_demo: bool = False
    error_message: Optional[str] = None
    status_message: str = "Ready"
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def all_metrics(self) -> list:
        metrics = list(self.windows)
        if self.credits:
            metrics.append(self.credits)
        metrics.extend(self.included_credits)
        return metrics

    @property
    def primary_metric(self) -> Optional[Union[UsageWindow, CreditsMetric]]:
        return self.all_metrics[0] if self.all_metrics else None


@dataclass
class ClaudeUsageState:
    """Backward compatibility wrapper mapping to UnifiedUsageState."""
    session: Optional[UsageWindow] = None
    weekly: Optional[UsageWindow] = None
    # Every per-model weekly window Claude reports (Fable, Opus, Sonnet, ...).
    # This was a single `weekly_extra` slot, so an account with more than one
    # scoped weekly limit silently lost all but the first.
    weekly_extras: List[UsageWindow] = field(default_factory=list)
    credits: Optional[CreditsMetric] = None
    included_credits: List[CreditsMetric] = field(default_factory=list)
    extra_usage_notes: List[str] = field(default_factory=list)
    # Claude's "this week's usage by product": (display name, percent, key).
    # The percents are shares of this week's usage and sum to ~100 -- a 17%
    # weekly limit reported Claude Code at 100 -- not fractions of the limit.
    # The key is the stable product id ("claude_code", "chat", ...); the
    # flyout colours segments by it so a colour never moves with rank.
    weekly_breakdown: List[tuple[str, float, str]] = field(default_factory=list)
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_demo: bool = False
    error_message: Optional[str] = None
    is_loading: bool = False

    @property
    def session_pct(self) -> float:
        return self.session.utilization if self.session else 0.0

    @property
    def weekly_pct(self) -> float:
        return self.weekly.utilization if self.weekly else 0.0

    @property
    def weekly_extra(self) -> Optional[UsageWindow]:
        """First per-model weekly window, for older callers that expect one."""
        return self.weekly_extras[0] if self.weekly_extras else None

    @property
    def all_metrics(self) -> list:
        ordered = [self.session, self.weekly, *self.weekly_extras, self.credits,
                   *self.included_credits]
        return [m for m in ordered if m is not None]

    def metric_by_key(self, key: str):
        for m in self.all_metrics:
            if m.key == key:
                return m
        return None

    @property
    def updated_ago_label(self) -> str:
        if self.is_loading:
            return "Loading..."
        return f"Updated {format_ago(self.last_updated)}"

    @property
    def summary_tooltip(self) -> str:
        parts = ["TwinRails"]
        if self.is_demo:
            parts.append("[DEMO MODE]")
        if self.error_message:
            parts.append(f"Notice: {self.error_message}")
        parts.append(self.updated_ago_label)
        for m in self.all_metrics:
            if isinstance(m, CreditsMetric):
                parts.append(f"{m.label}: {m.remaining_label}")
            else:
                cd = f" · {m.reset_label}" if m.reset_label else ""
                parts.append(f"{m.label}: {m.utilization:.1f}%{cd}")
        return "\n".join(parts)[:127]


@dataclass
class UnifiedUsageState:
    """Aggregated usage state for all enabled providers."""
    providers: Dict[str, ProviderUsageState] = field(default_factory=dict)
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_loading: bool = False
    is_demo: bool = False

    @property
    def all_metrics(self) -> list:
        metrics = []
        for p in self.providers.values():
            metrics.extend(p.all_metrics)
        return metrics

    def metric_by_key(self, key: str) -> Optional[Union[UsageWindow, CreditsMetric]]:
        for m in self.all_metrics:
            if m.key == key:
                return m
        return None

    @property
    def primary_metric(self) -> Optional[Union[UsageWindow, CreditsMetric]]:
        """Returns the most constrained metric (highest % used), or the first available."""
        valid_windows = [m for m in self.all_metrics if isinstance(m, UsageWindow)]
        if not valid_windows:
            return self.all_metrics[0] if self.all_metrics else None
        return max(valid_windows, key=lambda w: w.utilization)

    @property
    def session_pct(self) -> float:
        pm = self.primary_metric
        return pm.utilization if pm else 0.0

    @property
    def updated_ago_label(self) -> str:
        if self.is_loading:
            return "Loading..."
        return f"Updated {format_ago(self.last_updated)}"

    @property
    def summary_tooltip(self) -> str:
        parts = ["TwinRails"]
        if self.is_demo:
            parts.append("[DEMO MODE]")
        parts.append(self.updated_ago_label)

        # Compact per-metric lines: short tag ("5H") rather than the full
        # label ("Gemini - 5-Hour Limit"), so the exact reset time fits
        # alongside the countdown. Every character here is contested -- see
        # the truncation note below.
        for pid, p in self.providers.items():
            if not p.is_connected and not self.is_demo:
                parts.append(f"{p.icon_symbol} {p.provider_name}: Offline")
                continue
            for m in p.all_metrics:
                if isinstance(m, CreditsMetric):
                    parts.append(f"{p.icon_symbol} {m.short_tag} {m.remaining_label}")
                else:
                    tag = m.short_tag or m.label
                    when = m.compact_reset_label
                    when = f" {MIDDOT} {when}" if when else ""
                    parts.append(f"{p.icon_symbol} {tag} {m.utilization:.0f}%{when}")

        return _fit_tooltip(parts)
