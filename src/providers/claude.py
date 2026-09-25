"""
Claude AI Provider for fetching usage limits from claude.ai API.
"""

from typing import Optional
from .base import BaseProvider
from ..core.claude_client import ClaudeClient
from ..core.models import ProviderUsageState, UsageWindow, CreditsMetric
from ..ui.theme import CLAUDE_CORAL


class ClaudeProvider(BaseProvider):
    provider_id = "claude"
    name = "Claude (claude.ai)"
    icon_symbol = "✱"
    brand_color = CLAUDE_CORAL

    def __init__(self, session_key: str = "", org_id: str = "", auth_mode: str = "auto"):
        self.session_key = session_key
        self.org_id = org_id
        self.client = ClaudeClient(session_key=session_key, org_id=org_id, auth_mode=auth_mode)

    def update_credentials(self, session_key: str, org_id: str = "", auth_mode: str = "auto"):
        self.session_key = session_key
        self.org_id = org_id
        self.client.update_credentials(session_key, org_id, auth_mode)

    def fetch_usage(self, demo_mode: bool = False) -> ProviderUsageState:
        if demo_mode:
            raw_state = self.client.get_demo_state()
        else:
            raw_state = self.client.fetch_usage()

        windows = []
        if raw_state.session:
            w = raw_state.session
            w.provider_id = self.provider_id
            w.icon_symbol = self.icon_symbol
            w.brand_color = self.brand_color
            if not w.key or w.key == "five_hour":
                w.key = "claude_five_hour"
            windows.append(w)

        if raw_state.weekly:
            w = raw_state.weekly
            w.provider_id = self.provider_id
            w.icon_symbol = self.icon_symbol
            w.brand_color = self.brand_color
            if not w.key or w.key == "seven_day":
                w.key = "claude_seven_day"
            windows.append(w)

        # All per-model weekly windows (Fable, Opus, Sonnet, ...) -- was
        # `raw_state.weekly_extra` (singular), which forwarded only the first
        # and dropped the rest whenever an account had more than one.
        for w in raw_state.weekly_extras:
            w.provider_id = self.provider_id
            w.icon_symbol = self.icon_symbol
            w.brand_color = self.brand_color
            windows.append(w)

        credits = None
        if raw_state.credits:
            credits = raw_state.credits
            credits.provider_id = self.provider_id
            credits.icon_symbol = self.icon_symbol
            credits.brand_color = self.brand_color

        for grant in raw_state.included_credits:
            grant.provider_id = self.provider_id
            grant.icon_symbol = self.icon_symbol
            grant.brand_color = self.brand_color

        is_connected = not bool(raw_state.error_message) and bool(
            windows or credits or raw_state.included_credits
            or raw_state.extra_usage_notes or raw_state.weekly_breakdown
        )

        return ProviderUsageState(
            provider_id=self.provider_id,
            provider_name=self.name,
            icon_symbol=self.icon_symbol,
            brand_color=self.brand_color,
            windows=windows,
            credits=credits,
            included_credits=raw_state.included_credits,
            extra_usage_notes=raw_state.extra_usage_notes,
            weekly_breakdown=raw_state.weekly_breakdown,
            is_connected=is_connected,
            is_demo=demo_mode,
            is_loading=raw_state.is_loading,
            error_message=raw_state.error_message,
            status_message="Connected" if is_connected else (raw_state.error_message or "Offline"),
            last_updated=raw_state.last_updated
        )
