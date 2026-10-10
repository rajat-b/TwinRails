"""
ChatGPT (OpenAI) Provider for fetching session and weekly usage limits
from chatgpt.com backend API (used by Codex CLI and ChatGPT web sessions).
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple
import requests

from .base import BaseProvider
from ..core.chatgpt_auth import (
    read_codex_token,
    ChatGPTTokenUnavailable,
    ChatGPTTokenExpired,
    ChatGPTTokenReadError,
)
from ..core.models import ProviderUsageState, UsageWindow, CreditsMetric
from ..ui.theme import CHATGPT_GREEN, CHATGPT_SYMBOL


class ChatGPTProvider(BaseProvider):
    provider_id = "chatgpt"
    name = "ChatGPT (OpenAI)"
    icon_symbol = CHATGPT_SYMBOL
    brand_color = CHATGPT_GREEN

    USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"

    def __init__(self, token: str = "", account_id: str = "", auth_mode: str = "auto"):
        self.token = token
        self.account_id = account_id
        self.auth_mode = auth_mode

    def update_credentials(self, token: str, account_id: str = "", auth_mode: str = "auto"):
        self.token = token
        self.account_id = account_id
        self.auth_mode = auth_mode

    def _resolve_token(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Resolve (token, account_id, error_message) according to auth_mode."""
        if self.auth_mode == "auto":
            try:
                cli_tok, cli_acc = read_codex_token()
                if cli_tok:
                    return cli_tok, (cli_acc or self.account_id), None
            except Exception as e:
                if self.token:
                    return self.token, self.account_id, None
                return None, None, str(e)
            if self.token:
                return self.token, self.account_id, None
            return None, None, "Codex CLI not signed in and no access token configured."

        elif self.auth_mode == "codex":
            try:
                cli_tok, cli_acc = read_codex_token()
                return cli_tok, (cli_acc or self.account_id), None
            except Exception as e:
                return None, None, str(e)

        elif self.auth_mode == "token":
            if not self.token:
                return None, None, "Access token not configured. Enter an access token in Settings."
            return self.token, self.account_id, None

        return None, None, f"Unknown auth mode: {self.auth_mode}"

    def fetch_usage(self, demo_mode: bool = False) -> ProviderUsageState:
        now = datetime.now(timezone.utc)
        if demo_mode:
            return self._generate_demo_state()

        token, account_id, resolve_err = self._resolve_token()
        if not token or resolve_err:
            return ProviderUsageState(
                provider_id=self.provider_id,
                provider_name=self.name,
                icon_symbol=self.icon_symbol,
                brand_color=self.brand_color,
                windows=[],
                is_connected=False,
                error_message=resolve_err or "No token available",
                status_message=resolve_err or "Authentication required",
                last_updated=now
            )

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "TwinRails/1.1.0",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id

        resp = None
        try:
            resp = requests.get(self.USAGE_URL, headers=headers, timeout=5.0)
        except Exception:
            # Fallback to curl_cffi with browser impersonation if standard requests fails (e.g. Cloudflare)
            try:
                from curl_cffi import requests as cffi_requests
                resp = cffi_requests.get(self.USAGE_URL, headers=headers, impersonate="chrome124", timeout=5.0)
            except Exception as e:
                return ProviderUsageState(
                    provider_id=self.provider_id,
                    provider_name=self.name,
                    icon_symbol=self.icon_symbol,
                    brand_color=self.brand_color,
                    windows=[],
                    is_connected=False,
                    error_message=f"Network error: {e}",
                    status_message=f"Network error: {e}",
                    last_updated=now
                )

        if resp.status_code == 200:
            try:
                data = resp.json()
                windows, credits_metric, plan_type = self._parse_response(data)
                plan_label = f"Connected ({plan_type.title()})" if plan_type else "Connected"
                return ProviderUsageState(
                    provider_id=self.provider_id,
                    provider_name=self.name,
                    icon_symbol=self.icon_symbol,
                    brand_color=self.brand_color,
                    windows=windows,
                    credits=credits_metric,
                    is_connected=True,
                    status_message=plan_label,
                    last_updated=now
                )
            except Exception as e:
                return ProviderUsageState(
                    provider_id=self.provider_id,
                    provider_name=self.name,
                    icon_symbol=self.icon_symbol,
                    brand_color=self.brand_color,
                    windows=[],
                    is_connected=False,
                    error_message=f"Failed to parse usage data: {e}",
                    status_message="Data parse error",
                    last_updated=now
                )
        elif resp.status_code == 401:
            err = "ChatGPT access token expired or invalid. Sign in with 'codex' or update token in Settings."
            return ProviderUsageState(
                provider_id=self.provider_id,
                provider_name=self.name,
                icon_symbol=self.icon_symbol,
                brand_color=self.brand_color,
                windows=[],
                is_connected=False,
                error_message=err,
                status_message="Token expired",
                last_updated=now
            )
        elif resp.status_code == 429:
            err = "ChatGPT rate limit reached (HTTP 429). Will retry later."
            return ProviderUsageState(
                provider_id=self.provider_id,
                provider_name=self.name,
                icon_symbol=self.icon_symbol,
                brand_color=self.brand_color,
                windows=[],
                is_connected=False,
                error_message=err,
                status_message="Rate limited (429)",
                last_updated=now
            )
        else:
            err = f"ChatGPT HTTP error {resp.status_code}"
            return ProviderUsageState(
                provider_id=self.provider_id,
                provider_name=self.name,
                icon_symbol=self.icon_symbol,
                brand_color=self.brand_color,
                windows=[],
                is_connected=False,
                error_message=err,
                status_message=err,
                last_updated=now
            )

    def _parse_window(
        self,
        raw_window: dict,
        key: str,
        label: str,
        default_hours: int,
        now: datetime
    ) -> Optional[UsageWindow]:
        if not raw_window or not isinstance(raw_window, dict):
            return None

        used_pct = float(raw_window.get("used_percent", 0.0))
        limit_sec = raw_window.get("limit_window_seconds")
        window_hours = (int(limit_sec) // 3600) if limit_sec else default_hours
        window_hours = max(1, window_hours)

        resets_at = None
        reset_at_ts = raw_window.get("reset_at")
        if reset_at_ts:
            try:
                resets_at = datetime.fromtimestamp(float(reset_at_ts), timezone.utc)
            except Exception:
                pass
        if not resets_at and raw_window.get("reset_after_seconds") is not None:
            try:
                resets_at = now + timedelta(seconds=float(raw_window["reset_after_seconds"]))
            except Exception:
                pass

        return UsageWindow(
            key=key,
            label=label,
            provider_id=self.provider_id,
            icon_symbol=self.icon_symbol,
            brand_color=self.brand_color,
            utilization=round(used_pct, 1),
            resets_at=resets_at,
            window_hours=window_hours
        )

    def _parse_response(self, data: dict) -> Tuple[List[UsageWindow], Optional[CreditsMetric], Optional[str]]:
        windows: List[UsageWindow] = []
        now = datetime.now(timezone.utc)
        plan_type = data.get("plan_type")

        rate_limit = data.get("rate_limit") or {}
        pw = self._parse_window(
            rate_limit.get("primary_window"),
            key="chatgpt_five_hour",
            label="ChatGPT · 5-Hour",
            default_hours=5,
            now=now
        )
        if pw:
            windows.append(pw)

        sw = self._parse_window(
            rate_limit.get("secondary_window"),
            key="chatgpt_weekly",
            label="ChatGPT · Weekly",
            default_hours=168,
            now=now
        )
        if sw:
            windows.append(sw)

        # Parse additional rate limits if any
        for item in (data.get("additional_rate_limits") or []):
            if not isinstance(item, dict):
                continue
            item_id = item.get("id", "limit")
            title = item.get("title", item_id.replace("-", " ").title())
            if item.get("primary_window"):
                extra_w = self._parse_window(
                    item["primary_window"],
                    key=f"chatgpt_{item_id}",
                    label=f"ChatGPT · {title}",
                    default_hours=5,
                    now=now
                )
                if extra_w:
                    windows.append(extra_w)

        # Parse code review rate limit if present
        code_review = data.get("code_review_rate_limit")
        if isinstance(code_review, dict) and code_review.get("primary_window"):
            cr_w = self._parse_window(
                code_review["primary_window"],
                key="chatgpt_code_review",
                label="ChatGPT · Code Review",
                default_hours=5,
                now=now
            )
            if cr_w:
                windows.append(cr_w)

        # Parse credits if present
        credits_metric = None
        credits_data = data.get("credits")
        if isinstance(credits_data, dict) and credits_data.get("has_credits"):
            balance_raw = credits_data.get("balance", "0")
            try:
                balance_val = float(balance_raw)
                credits_metric = CreditsMetric(
                    key="chatgpt_credits",
                    label="ChatGPT · Credits",
                    provider_id=self.provider_id,
                    icon_symbol=self.icon_symbol,
                    brand_color=self.brand_color,
                    used=0.0,
                    total=balance_val,
                    currency="USD"
                )
            except (ValueError, TypeError):
                pass

        return windows, credits_metric, plan_type

    def _generate_demo_state(self) -> ProviderUsageState:
        now = datetime.now(timezone.utc)
        w1 = UsageWindow(
            key="chatgpt_five_hour",
            label="ChatGPT · 5-Hour Limit",
            provider_id=self.provider_id,
            icon_symbol=self.icon_symbol,
            brand_color=self.brand_color,
            utilization=48.0,
            resets_at=now + timedelta(hours=3, minutes=12),
            window_hours=5
        )
        w2 = UsageWindow(
            key="chatgpt_weekly",
            label="ChatGPT · Weekly Limit",
            provider_id=self.provider_id,
            icon_symbol=self.icon_symbol,
            brand_color=self.brand_color,
            utilization=15.5,
            resets_at=now + timedelta(days=5, hours=18),
            window_hours=168
        )
        return ProviderUsageState(
            provider_id=self.provider_id,
            provider_name=self.name,
            icon_symbol=self.icon_symbol,
            brand_color=self.brand_color,
            windows=[w1, w2],
            is_connected=True,
            is_demo=True,
            status_message="Demo Mode (Plus)",
            last_updated=now
        )
