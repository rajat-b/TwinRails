"""
Claude Client for fetching usage limits from claude.ai API.
Uses curl_cffi for real browser TLS impersonation to seamlessly bypass Cloudflare challenges.
"""

import re
import copy
import json
import time
import hashlib
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple
import requests as plain_requests
from curl_cffi import requests

from .claude_oauth import ClaudeTokenReadError, ClaudeTokenUnavailable, read_claude_code_token
from .models import ClaudeUsageState, UsageWindow, CreditsMetric, format_ago

# api.anthropic.com/api/oauth/usage rate-limits hard, and often answers 429
# with "Retry-After: 0" (anthropics/claude-code issues #30930, #31021, #31637;
# reproduced here 2026-09-25). The old fixed 60s cooldown, retried on a 60s
# refresh, kept it limited indefinitely. So when the server names no wait,
# back off 5 -> 10 -> 20 -> 40 -> 60 minutes, resetting on the next success.
_OAUTH_BACKOFF_START = 300
_OAUTH_BACKOFF_MAX = 3600
# Sign-in checks are spaced at least this far apart whatever the refresh
# interval; in between, the last sign-in reading is reused with its age shown.
# Chosen 2026-09-25, after 60s polling had the endpoint refusing within the
# hour. The cookie route and Gemini still follow the refresh interval, so a
# user who wants minute-by-minute Claude numbers picks "Session cookie only".
_OAUTH_MIN_INTERVAL = 300
# How long the last good reading may stand in for a rate-limited fetch before
# the Claude cards give way to just the error. Its age is always shown.
_STALE_TOLERANCE = timedelta(minutes=30)

# Known top-level keys in the /usage response that are handled explicitly below
# (five_hour/seven_day), or that a real account response confirmed are simply
# irrelevant to usage metrics -- feature-flag-looking codenames and an unrelated
# dashboard-availability bool, all observed as inactive/unrelated on 2026-09-03.
#
# This set is load-bearing, not just log hygiene. _extract_extra_windows_by_shape
# skips these keys, and at least one of them (nimbus_quill) is shaped exactly
# like a window -- a numeric utilization plus a resets_at key -- so dropping it
# from here adds a bogus bar to the flyout. The set also keeps these keys out
# of the "unrecognized" debug log. If one of them turns out to matter later,
# this is where it needs removing from.
#
# One already did: iguana_necktie is the cloud-session credit grant (found
# 2026-09-25). It stays listed here so the window fallback keeps skipping
# it; _extract_included_credits reads it, by shape, as a dollar balance.
_KNOWN_KEYS = {
    "five_hour", "seven_day", "limits", "spend", "extra_usage",
    "seven_day_breakdown",
    "seven_day_oauth_apps", "seven_day_opus", "seven_day_sonnet",
    "seven_day_cowork", "seven_day_omelette", "member_dashboard_available",
    "tangelo", "iguana_necktie", "omelette_promotional", "cinder_cove",
    "amber_ladder", "juniper_tide", "nimbus_quill",
}

# Fallback candidate key/field names for the usage-credits balance, used only
# if neither of the confirmed real fields below (data['spend'], data
# ['extra_usage']) is present -- e.g. a differently-shaped account/plan.
_CREDITS_KEY_CANDIDATES = ("usage_credits", "credits", "credit_balance", "credits_balance")
_CREDITS_USED_FIELDS = ("used", "spent", "consumed", "balance_used")
_CREDITS_TOTAL_FIELDS = ("total", "limit", "allowance", "balance_total")

_DEBUG_LOG_PATH = Path.home() / ".ai_session_limits" / "usage_response_debug.log"


def _percent(raw) -> float:
    """Reads a utilization/percent field off the /usage response as 0-100.

    These fields are on a 0-100 scale. Confirmed against a real account on
    2026-09-04: data['five_hour']['utilization'] was 8.0 at the same moment
    data['limits'] carried {"kind": "session", "percent": 8} -- the same
    number in both places, and 'percent' is unambiguously 0-100.

    This replaces a `raw if raw > 1.0 else raw * 100.0` guess that tried to
    support both scales. On a 0-100 scale that guess multiplies anything at
    or below 1.0 by a hundred, so a genuine 1% weekly usage was displayed as
    100% -- caught live on 2026-09-04 with seven_day.utilization == 1.0. Any
    value under 1% is the common case early in a window, so the widget went
    solid red for the first stretch of most weeks. Do not reintroduce the
    dual-scale guess: it cannot distinguish 1% from 100% even in principle."""
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, pct))


def parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        # Handles 2026-09-02T23:42:00.000Z or similar
        clean_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_str)
    except Exception:
        return None


def _label_for_extra_window_key(key: str) -> str:
    """Turns an unrecognized key like 'seven_day_opus' into 'Weekly · Opus'.
    Falls back to a title-cased version of the raw key when it doesn't match
    the 'seven_day_<model>' pattern Claude's naming so far suggests. Used only
    by the shape-based fallback discovery below -- the confirmed 'limits' path
    derives its label from scope.model.display_name instead, which is more
    reliable when it's present."""
    m = re.match(r"^seven_day_(.+)$", key)
    if m:
        return f"Weekly · {m.group(1).replace('_', ' ').title()}"
    return key.replace("_", " ").title()


def _extract_limits_windows(data: dict) -> list:
    """Parses data['limits'] -- confirmed against a real account response on
    2026-09-03 to be a list of {kind, group, percent (0-100, NOT 0-1 like
    five_hour/seven_day above), resets_at, scope, is_active} objects. 'kind'
    values seen: 'session' and 'weekly_all' duplicate five_hour/seven_day
    above (same numbers, same reset times) so are skipped here to avoid a
    duplicate card; 'weekly_scoped' is the per-model weekly window Claude's
    usage popover shows as e.g. 'Weekly · Fable', with the model name at
    scope.model.display_name. Any other 'kind' this hasn't seen yet is still
    picked up generically (group=='weekly' -> 168h window, else 5h) rather
    than silently dropped, since Anthropic has already added kinds here once."""
    found = []
    limits = data.get("limits")
    if not isinstance(limits, list):
        return found

    seen_keys = set()
    for item in limits:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind in ("session", "weekly_all"):
            continue  # already covered by five_hour/seven_day above

        pct = item.get("percent")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct):
            continue

        scope = item.get("scope")
        model = (scope or {}).get("model") if isinstance(scope, dict) else None
        model_name = model.get("display_name") if isinstance(model, dict) else None
        model_name = model_name.strip() if isinstance(model_name, str) else None
        kind = kind if isinstance(kind, str) and kind else "other"

        window_hours = 168 if item.get("group") == "weekly" else 5
        period = "Weekly" if window_hours == 168 else "Session"
        if model_name:
            label = f"{period} · {model_name}"
        else:
            label = f"{period} · {kind.replace('_', ' ').title()}"

        # Several models can share kind='weekly_scoped'. Include the scope in
        # the saved metric key so Settings can select each one independently.
        # Hashing the stable scope also avoids collisions when names slug alike.
        if isinstance(scope, dict) and scope:
            # Model IDs survive display-name changes; only fall back to the
            # name when the API has no ID (as in the observed Fable response).
            model_id = model.get("id") if isinstance(model, dict) else None
            model_id = model_id if isinstance(model_id, str) and model_id else None
            slug_source = model_id or model_name
            slug = re.sub(r"[^a-z0-9]+", "_", (slug_source or "scope").casefold()).strip("_") or "scope"
            stable_scope = dict(scope)
            if model_id:
                stable_scope["model"] = {k: v for k, v in model.items() if k != "display_name"}
            identity = json.dumps({"kind": kind, "group": item.get("group"), "scope": stable_scope},
                                  sort_keys=True, default=str)
            suffix = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
            key = f"limits_{kind}_{slug}_{suffix}"
        else:
            key = f"limits_{kind}"
        if key in seen_keys:
            continue
        seen_keys.add(key)

        found.append(UsageWindow(
            key=key,
            label=label,
            utilization=_percent(pct),
            resets_at=parse_iso_datetime(item.get("resets_at")),
            window_hours=window_hours
        ))
    return found


def _extract_extra_windows_by_shape(data: dict, already_matched_keys: set) -> list:
    """Fallback for any weekly-style metric that isn't in data['limits'] at all
    (e.g. if a future account/plan shape drops 'limits' entirely) -- scans for
    any other top-level dict value shaped like the known windows ({'utilization':
    ..., 'resets_at': ...}), so whatever Anthropic calls it, it's picked up by
    shape rather than by a name this code has never seen."""
    found = []
    for key, val in data.items():
        if key in _KNOWN_KEYS or key in already_matched_keys or not isinstance(val, dict):
            continue
        if "utilization" not in val or "resets_at" not in val:
            continue
        # A dollar grant also carries utilization + resets_at, but it is
        # money with an expiry date, not a usage window -- it is shown by
        # _extract_included_credits instead. Without this it would appear
        # twice, once as a bogus 5-hour bar.
        if val.get("limit_dollars") is not None:
            continue
        if not isinstance(val.get("utilization"), (int, float)):
            continue
        pct = _percent(val.get("utilization"))

        window_hours = val.get("window_hours")
        if not isinstance(window_hours, (int, float)) or window_hours <= 0:
            window_hours = 168 if key.startswith("seven_day") else 5

        found.append(UsageWindow(
            key=key,
            label=_label_for_extra_window_key(key),
            utilization=pct,
            resets_at=parse_iso_datetime(val.get("resets_at")),
            window_hours=int(window_hours)
        ))
    return found


def _extract_credits(data: dict) -> Optional[CreditsMetric]:
    """Confirmed against a real account response on 2026-09-03: the credits
    balance lives at data['spend'], as minor-unit amounts -- e.g.
    {"used": {"amount_minor": 483, "exponent": 2}, "limit": {"amount_minor":
    500, "exponent": 2}} means $4.83 of $5.00. data['extra_usage'] carries the
    same numbers in a simpler (already-decimal) shape and is tried second, in
    case an account ever has one field but not the other. The original
    best-effort key/field-name guessing is kept as a last-resort fallback for
    an account shaped differently than this one."""
    spend = data.get("spend")
    if isinstance(spend, dict):
        used, limit = spend.get("used"), spend.get("limit")
        if isinstance(used, dict) and isinstance(limit, dict):
            try:
                used_minor, limit_minor = used["amount_minor"], limit["amount_minor"]
                used_exp, limit_exp = used.get("exponent", 2), limit.get("exponent", 2)
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                       for v in (used_minor, limit_minor, used_exp, limit_exp)):
                    raise ValueError("Invalid money amount")
                if not (float(used_exp).is_integer() and float(limit_exp).is_integer()
                        and 0 <= used_exp <= 6 and 0 <= limit_exp <= 6):
                    raise ValueError("Invalid money exponent")
                used_currency, limit_currency = used.get("currency"), limit.get("currency")
                if used_currency and limit_currency and used_currency.upper() != limit_currency.upper():
                    raise ValueError("Mismatched currencies")
                used_amt = float(used_minor) / (10 ** int(used_exp))
                total_amt = float(limit_minor) / (10 ** int(limit_exp))
                if total_amt > 0 and used_amt >= 0:
                    currency = used_currency or limit_currency
                    currency = currency.upper() if isinstance(currency, str) and len(currency) == 3 and currency.isalpha() else "USD"
                    return CreditsMetric(key="usage_credits", label="Usage Credits",
                                         used=used_amt, total=total_amt, currency=currency)
            except (KeyError, TypeError, ValueError, AttributeError, ZeroDivisionError):
                pass

    extra = data.get("extra_usage")
    if isinstance(extra, dict):
        used, total = extra.get("used_credits"), extra.get("monthly_limit")
        if (isinstance(used, (int, float)) and not isinstance(used, bool) and math.isfinite(used)
                and isinstance(total, (int, float)) and not isinstance(total, bool) and math.isfinite(total)
                and used >= 0 and total > 0):
            currency = extra.get("currency")
            currency = currency.upper() if isinstance(currency, str) and len(currency) == 3 and currency.isalpha() else "USD"
            return CreditsMetric(key="usage_credits", label="Usage Credits",
                                 used=float(used), total=float(total), currency=currency)

    for key in _CREDITS_KEY_CANDIDATES:
        val = data.get(key)
        if not isinstance(val, dict):
            continue
        used = next((val[f] for f in _CREDITS_USED_FIELDS if f in val), None)
        total = next((val[f] for f in _CREDITS_TOTAL_FIELDS if f in val), None)
        if used is None or total is None:
            continue
        try:
            used_f, total_f = float(used), float(total)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(used_f) or not math.isfinite(total_f) or used_f < 0 or total_f <= 0:
            continue
        currency = val.get("currency")
        currency = currency.upper() if isinstance(currency, str) and len(currency) == 3 and currency.isalpha() else "USD"
        return CreditsMetric(key="usage_credits", label="Usage Credits",
                             used=used_f, total=total_f, currency=currency)
    return None


# Keys this client already reads for something else; never treated as grants.
_HANDLED_USAGE_KEYS = {"five_hour", "seven_day", "limits", "spend", "extra_usage",
                       "seven_day_breakdown"}

# Stable metric key and label for grants whose purpose is known. Grants are
# FOUND by shape (below), not by these names: the names are internal code
# names that can change without notice, and when one does the grant still
# shows, just under the generic "Included Credit" label.
_INCLUDED_CREDIT_NAMES = {
    # Confirmed 2026-09-25 against claude.ai's usage page: $250 limit,
    # $34.71 used, $215.29 left, resets_at 07:59 UTC on Nov 5 -- the page's
    # "Cloud session credits · Included credit · Expires ... November 5".
    "iguana_necktie": ("cloud_session_credits", "Cloud Session Credits"),
}


def _extract_included_credits(data: dict) -> list[tuple[str, CreditsMetric]]:
    """Dollar grants Claude reports next to the usage windows, e.g. the
    included credit for cloud sessions. Returns (source key, metric) pairs.

    Recognised by shape: a top-level object with a positive numeric
    `limit_dollars` and a non-negative `used_dollars`. Its `resets_at` is when
    the grant lapses, so it becomes the metric's expiry rather than a reset
    window -- nothing is paced against it. Seen on the claude.ai cookie
    route; the OAuth response carries the same top-level keys, but this one
    was null there on 2026-09-20, so OAuth delivery is unverified."""
    found = []
    for source, val in data.items():
        if source in _HANDLED_USAGE_KEYS or not isinstance(val, dict):
            continue
        limit, used = val.get("limit_dollars"), val.get("used_dollars")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (limit, used)):
            continue
        if limit <= 0 or used < 0:
            continue
        key, label = _INCLUDED_CREDIT_NAMES.get(
            source, (f"included_credit_{source}", "Included Credit"))
        found.append((source, CreditsMetric(
            key=key, label=label, used=float(used), total=float(limit),
            currency="USD", expires_at=parse_iso_datetime(val.get("resets_at")),
        )))
    return found


def _extract_extra_usage_notes(data: dict) -> list[str]:
    """Report Claude's explicit extra-usage flags without guessing a balance."""
    extra = data.get("extra_usage")
    spend = data.get("spend")
    enabled = extra.get("is_enabled") if isinstance(extra, dict) else None
    if not isinstance(enabled, bool) and isinstance(spend, dict):
        enabled = spend.get("enabled")
    notes = []
    if isinstance(enabled, bool):
        notes.append(f"Extra usage: {'On' if enabled else 'Off'}")
    if isinstance(extra, dict) and extra.get("spend_limit_reached") is True:
        notes.append("Extra usage spending cap reached")
    return notes


def _extract_weekly_breakdown(data: dict) -> list[tuple[str, float, str]]:
    """Keep Claude's labeled breakdown rows as reported, without relabeling
    them as limits: (display name, percent, stable key). The key falls back
    to the lower-cased name if a row ever arrives without one."""
    breakdown = data.get("seven_day_breakdown")
    if not isinstance(breakdown, dict) or not isinstance(breakdown.get("rows"), list):
        return []
    rows = []
    for row in breakdown["rows"]:
        if not isinstance(row, dict):
            continue
        name, percent = row.get("display_name"), row.get("percent")
        if (isinstance(name, str) and name.strip() and isinstance(percent, (int, float))
                and not isinstance(percent, bool) and math.isfinite(percent)):
            key = row.get("key")
            key = key.strip() if isinstance(key, str) and key.strip() else name.strip().lower()
            rows.append((name.strip(), float(percent), key))
    return rows


def _log_unrecognized_usage_shape(data: dict, matched_keys: set):
    """Best-effort diagnostic: when the response has top-level keys this client
    neither knows by name nor matched via auto-discovery, append their raw shape
    (key names and value TYPES only -- never values, so no account data is ever
    written to disk) to a local debug log. This is how the exact field names for
    a metric this version misses can be confirmed from a real account, without
    ever needing the session key itself -- share this file, not the key."""
    unrecognized = {k: type(v).__name__ for k, v in data.items()
                     if k not in _KNOWN_KEYS and k not in matched_keys}
    if not unrecognized:
        return
    try:
        _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(),
                "unrecognized_top_level_keys": unrecognized,
            }) + "\n")
    except Exception:
        pass


class ClaudeClient:
    def __init__(self, session_key: str = "", org_id: str = "", auth_mode: str = "auto"):
        self.session_key = session_key.strip()
        self.org_id = org_id.strip()
        self.auth_mode = auth_mode
        self._oauth_retry_until = 0.0
        self._oauth_backoff = 0.0
        self._oauth_status = None           # HTTP status of the last OAuth request
        self._last_good: Optional[ClaudeUsageState] = None
        self._last_good_at: Optional[datetime] = None
        self._oauth_reading: Optional[ClaudeUsageState] = None   # last OAuth success
        self._oauth_reading_at: Optional[datetime] = None
        self._oauth_reading_mono = 0.0

    def update_credentials(self, session_key: str, org_id: str = "", auth_mode: str = "auto"):
        self.session_key = session_key.strip()
        self.org_id = org_id.strip()
        self.auth_mode = auth_mode

    def get_demo_state(self) -> ClaudeUsageState:
        """Returns realistic demo data matching the user's weekly Friday 1:29 AM reset."""
        now = datetime.now(timezone.utc)
        # Session: resets in 1h 28m
        session_resets = now + timedelta(hours=1, minutes=28)

        # Weekly limit: fixed at every Friday at 1:29 AM local time
        now_local = datetime.now().astimezone()
        days_ahead = (4 - now_local.weekday()) % 7  # 4 is Friday
        target_friday = now_local.replace(hour=1, minute=29, second=0, microsecond=0) + timedelta(days=days_ahead)
        if target_friday <= now_local:
            target_friday += timedelta(days=7)
        weekly_resets = target_friday.astimezone(timezone.utc)

        return ClaudeUsageState(
            session=UsageWindow(
                key="five_hour", label="Session Limit",
                utilization=100.0,
                resets_at=session_resets,
                window_hours=5
            ),
            weekly=UsageWindow(
                key="seven_day", label="Weekly · All Models",
                utilization=48.0,
                resets_at=weekly_resets,
                window_hours=168
            ),
            # These two are newer metrics Claude's own usage popover added later
            # (a per-model weekly window, and a dollar credits balance) -- shown
            # here in demo mode too so the flyout/settings picker can be built
            # and previewed without a live account. See fetch_usage() for the
            # real (best-effort) parsing of these from the actual API.
            weekly_extras=[UsageWindow(
                key=_extract_limits_windows({"limits": [{
                    "kind": "weekly_scoped", "group": "weekly", "percent": 62,
                    "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
                }]})[0].key, label="Weekly · Fable",
                utilization=62.0,
                resets_at=weekly_resets,
                window_hours=168
            )],
            credits=CreditsMetric(
                key="usage_credits", label="Usage Credits",
                used=4.83, total=5.00
            ),
            included_credits=[CreditsMetric(
                key="cloud_session_credits", label="Cloud Session Credits",
                used=34.71, total=250.00, expires_at=now + timedelta(days=41)
            )],
            extra_usage_notes=["Extra usage: On"],
            # Shares of this week's usage, so they sum to 100.
            weekly_breakdown=[("Claude Code", 64.0, "claude_code"), ("Chats", 24.0, "chat"),
                              ("Cowork", 8.0, "cowork"), ("Other", 4.0, "other")],
            last_updated=now,
            is_demo=True,
            error_message=None
        )

    def fetch_usage(self) -> ClaudeUsageState:
        """Use Claude Code OAuth when available, or the selected cookie path."""
        if self.auth_mode not in ("auto", "oauth", "cookie"):
            return ClaudeUsageState(error_message="Unknown Claude authentication mode. Check Settings.")

        if self.auth_mode != "cookie":
            try:
                token, _subscription_type = read_claude_code_token()
            except ClaudeTokenUnavailable as exc:   # includes an expired token
                if self.auth_mode == "oauth" or not self.session_key:
                    return ClaudeUsageState(error_message=str(exc))
                # Auto uses the cookie when there is no usable token.
            except ClaudeTokenReadError as exc:
                return ClaudeUsageState(error_message=str(exc))
            else:
                recent = self._recent_oauth_reading()
                if recent is not None:
                    return recent
                state = self._fetch_oauth_usage(token)
                rate_limited = self._oauth_rate_limited()
                token_refused = self._oauth_status in (401, 403)
                if not (rate_limited or token_refused):
                    if not state.error_message:
                        self._oauth_reading = copy.deepcopy(state)
                        self._oauth_reading_at = datetime.now(timezone.utc)
                        self._oauth_reading_mono = time.monotonic()
                    return self._remember(state)
                # Auto with a saved cookie keeps going on the cookie. This is
                # still not a fallback on *network* errors -- a flaky
                # connection must not silently switch transports -- only on
                # the server explicitly refusing this token or throttling it.
                if self.auth_mode == "auto" and self.session_key:
                    cookie_state = self._fetch_cookie_usage()
                    if not cookie_state.error_message:
                        cookie_state.extra_usage_notes.append(
                            "Claude Code sign-in is rate limited; using the session cookie for now."
                            if rate_limited else
                            "Claude Code sign-in was refused; using the session cookie for now."
                        )
                        return self._remember(cookie_state)
                return self._stale_or(state) if rate_limited else state

        return self._remember(self._fetch_cookie_usage())

    def _recent_oauth_reading(self) -> Optional[ClaudeUsageState]:
        """The last sign-in reading, if it is under _OAUTH_MIN_INTERVAL old.

        Says its age once it is a minute or more old, so a 60-second refresh
        that shows unchanged Claude numbers explains itself."""
        if self._oauth_reading is None or self._oauth_reading_at is None:
            return None
        if time.monotonic() - self._oauth_reading_mono >= _OAUTH_MIN_INTERVAL:
            return None
        reading = copy.deepcopy(self._oauth_reading)
        if datetime.now(timezone.utc) - self._oauth_reading_at >= timedelta(minutes=1):
            reading.extra_usage_notes.append(
                f"Claude Code sign-in reading from {format_ago(self._oauth_reading_at)}; "
                f"it updates at most every {_OAUTH_MIN_INTERVAL // 60} minutes."
            )
        return reading

    def _oauth_rate_limited(self) -> bool:
        return time.monotonic() < self._oauth_retry_until

    def _rate_limit_message(self) -> str:
        minutes = max(1, math.ceil((self._oauth_retry_until - time.monotonic()) / 60))
        return f"Claude Code sign-in is rate limited by Anthropic; retrying in {minutes} min."

    def _remember(self, state: ClaudeUsageState) -> ClaudeUsageState:
        """Keep a copy of the last reading that worked, for _stale_or()."""
        if not state.error_message and (state.session or state.weekly or state.weekly_extras):
            self._last_good = copy.deepcopy(state)
            self._last_good_at = datetime.now(timezone.utc)
        return state

    def _stale_or(self, failed: ClaudeUsageState) -> ClaudeUsageState:
        """The last good reading, labelled with its age, if it is recent enough.

        It still carries an error, so the provider counts as disconnected and
        the taskbar tints Claude's rows amber -- the existing "not live" cue --
        rather than the cards vanishing for the whole cooldown."""
        if self._last_good is None or self._last_good_at is None:
            return failed
        if datetime.now(timezone.utc) - self._last_good_at > _STALE_TOLERANCE:
            return failed
        stale = copy.deepcopy(self._last_good)
        stale.error_message = (f"{failed.error_message} "
                               f"Showing data from {format_ago(self._last_good_at)}.")
        return stale

    def _fetch_oauth_usage(self, token: str) -> ClaudeUsageState:
        self._oauth_status = None
        if self._oauth_rate_limited():
            return ClaudeUsageState(error_message=self._rate_limit_message())
        try:
            response = plain_requests.get(
                "https://api.anthropic.com/api/oauth/usage",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": "oauth-2025-04-20",
                },
                timeout=15,
            )
        except plain_requests.exceptions.RequestException:
            return ClaudeUsageState(error_message="Claude OAuth connection failed. Check your network.")

        self._oauth_status = response.status_code
        if response.status_code in (401, 403):
            return ClaudeUsageState(
                error_message="Claude Code token expired. Run claude once to refresh it."
            )
        if response.status_code == 429:
            try:
                retry_seconds = int(response.headers.get("Retry-After", "0"))
            except (TypeError, ValueError):
                retry_seconds = 0
            if retry_seconds > 0:
                wait = max(60, min(_OAUTH_BACKOFF_MAX, retry_seconds))
            else:
                self._oauth_backoff = min(_OAUTH_BACKOFF_MAX,
                                          self._oauth_backoff * 2 or _OAUTH_BACKOFF_START)
                wait = self._oauth_backoff
            self._oauth_retry_until = time.monotonic() + wait
            return ClaudeUsageState(error_message=self._rate_limit_message())
        if not response.ok:
            return ClaudeUsageState(
                error_message=f"Claude OAuth API returned status {response.status_code}."
            )
        try:
            data = response.json()
        except (ValueError, UnicodeError):
            return ClaudeUsageState(error_message="Claude OAuth returned an unreadable response.")
        self._oauth_retry_until = 0.0
        self._oauth_backoff = 0.0

        if not isinstance(data, dict):
            return ClaudeUsageState(error_message="Claude OAuth returned an unexpected response.")
        # On this account's 2026-09-20 OAuth response, spend.limit and
        # extra_usage.used_credits were null. The shared _extract_credits
        # helper returns None in that case, so the credits metric is hidden.
        # Do not fetch claude.ai with a cookie just to fill this one metric.
        return self._state_from_usage_data(data)

    def _fetch_cookie_usage(self) -> ClaudeUsageState:
        """Fetch from claude.ai using the browser TLS fingerprint it requires."""
        if not self.session_key:
            return ClaudeUsageState(error_message="Session key not configured. Open Settings to set it.")

        try:
            session = requests.Session(impersonate="chrome124")
            cookies = {"sessionKey": self.session_key}
            if self.org_id:
                cookies["lastActiveOrg"] = self.org_id

            headers = {
                "Accept": "application/json",
                "Referer": "https://claude.ai/",
                "Origin": "https://claude.ai"
            }

            # Discover org_id if not known
            org_id = self.org_id
            if not org_id:
                org_resp = session.get(
                    "https://claude.ai/api/organizations",
                    headers=headers,
                    cookies=cookies,
                    timeout=15
                )
                if org_resp.status_code in (401, 403):
                    return ClaudeUsageState(
                        is_demo=False,
                        error_message="Session key is invalid or expired. Please update in Settings."
                    )
                if org_resp.status_code == 200:
                    orgs = org_resp.json()
                    if isinstance(orgs, list) and len(orgs) > 0:
                        org_id = orgs[0].get("uuid", "")
                        self.org_id = org_id

            if not org_id:
                return ClaudeUsageState(
                    is_demo=False,
                    error_message="Could not detect Claude organization. Please check session key."
                )

            # Query usage endpoint
            usage_url = f"https://claude.ai/api/organizations/{org_id}/usage"
            usage_resp = session.get(
                usage_url,
                headers=headers,
                cookies=cookies,
                timeout=15
            )

            if usage_resp.status_code in (401, 403):
                # A key can expire mid-session (this was only checked on the
                # org-discovery call above, which is skipped once org_id is
                # cached) -- give the same actionable message instead of a
                # bare status code.
                return ClaudeUsageState(
                    is_demo=False,
                    error_message="Session key is invalid or expired. Please update in Settings."
                )
            if usage_resp.status_code != 200:
                return ClaudeUsageState(
                    is_demo=False,
                    error_message=f"Claude API returned status {usage_resp.status_code}"
                )

            data = usage_resp.json()
            return self._state_from_usage_data(data)

        except Exception as e:
            return ClaudeUsageState(
                is_demo=False,
                error_message=f"Connection error: {str(e)[:60]}"
            )

    def _state_from_usage_data(self, data: dict) -> ClaudeUsageState:
        """Use the existing response parsers for either Claude transport."""
        now = datetime.now(timezone.utc)

        # Parse five_hour (session)
        session_window = None
        if "five_hour" in data and isinstance(data["five_hour"], dict):
            fh = data["five_hour"]
            session_window = UsageWindow(
                key="five_hour", label="Session Limit",
                utilization=_percent(fh.get("utilization", 0.0)),
                resets_at=parse_iso_datetime(fh.get("resets_at")),
                window_hours=5
            )

        # Parse seven_day (weekly)
        weekly_window = None
        if "seven_day" in data and isinstance(data["seven_day"], dict):
            sd = data["seven_day"]
            weekly_window = UsageWindow(
                key="seven_day", label="Weekly · All Models",
                utilization=_percent(sd.get("utilization", 0.0)),
                resets_at=parse_iso_datetime(sd.get("resets_at")),
                window_hours=168
            )

        # --- Multi-metric parsing ------------------------------------------
        # Claude's own usage popover now shows up to 4 things: the 5-hour and
        # all-model-weekly limits above, a per-model weekly window (e.g.
        # "Weekly · Fable"), and a dollar usage-credits balance. The exact
        # field names for the latter two (data['limits'] and data['spend'])
        # were confirmed against a real account response on 2026-09-03 --
        # see _extract_limits_windows / _extract_credits. The shape-based
        # fallback stays in place for any account/plan where 'limits' is
        # absent entirely; if even that finds nothing, those metrics are
        # just absent -- not shown as broken -- and whatever wasn't
        # recognized at all gets logged to ~/.claude_taskbar_widget/
        # usage_response_debug.log for confirming against future changes.
        extra_windows = _extract_limits_windows(data)
        if not extra_windows:
            extra_windows = _extract_extra_windows_by_shape(data, set())
        credits = _extract_credits(data)
        # credits.key is now always the stable id "usage_credits" (see
        # _extract_credits), not the raw API field it came from, so for
        # debug-log purposes recover which top-level key actually fed it.
        credits_source_key = next(
            (k for k in ("spend", "extra_usage", *_CREDITS_KEY_CANDIDATES) if k in data),
            None
        ) if credits else None
        included = _extract_included_credits(data)
        matched_keys = {w.key for w in extra_windows} | ({credits_source_key} if credits_source_key else set())
        matched_keys |= {source for source, _ in included}
        _log_unrecognized_usage_shape(data, matched_keys)

        return ClaudeUsageState(
            session=session_window,
            weekly=weekly_window,
            # All per-model weekly windows (Fable, Opus, Sonnet, ...), not
            # just the first -- see ClaudeUsageState.weekly_extras.
            weekly_extras=extra_windows,
            credits=credits,
            included_credits=[metric for _, metric in included],
            extra_usage_notes=_extract_extra_usage_notes(data),
            weekly_breakdown=_extract_weekly_breakdown(data),
            last_updated=now,
            is_demo=False,
            error_message=None
        )
