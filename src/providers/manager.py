"""
Provider Manager coordinates polling across all registered and enabled AI providers.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, Optional, List
from .base import BaseProvider
from .claude import ClaudeProvider
from .antigravity import AntigravityProvider
from ..core.models import UnifiedUsageState, ProviderUsageState, ClaudeUsageState
from ..core.config import WidgetConfig


class ProviderManager:
    def __init__(self, config: WidgetConfig):
        self.config = config
        self.providers: Dict[str, BaseProvider] = {
            "claude": ClaudeProvider(
                session_key=config.session_key,
                org_id=config.org_id,
                auth_mode=config.claude_auth_mode,
            ),
            "antigravity": AntigravityProvider(),
        }

    def update_config(self, config: WidgetConfig):
        self.config = config
        if "claude" in self.providers:
            claude_prov: ClaudeProvider = self.providers["claude"]
            claude_prov.update_credentials(config.session_key, config.org_id, config.claude_auth_mode)

    def fetch_all(self, demo_mode: bool = False) -> UnifiedUsageState:
        enabled = self.config.enabled_providers or ["claude", "antigravity"]
        provider_states: Dict[str, ProviderUsageState] = {}
        now = datetime.now(timezone.utc)

        with ThreadPoolExecutor(max_workers=len(self.providers)) as executor:
            future_to_id = {
                executor.submit(prov.fetch_usage, demo_mode): pid
                for pid, prov in self.providers.items()
                if pid in enabled
            }
            for future in as_completed(future_to_id):
                pid = future_to_id[future]
                try:
                    state = future.result()
                    provider_states[pid] = state
                except Exception as e:
                    prov = self.providers[pid]
                    provider_states[pid] = ProviderUsageState(
                        provider_id=pid,
                        provider_name=prov.name,
                        icon_symbol=prov.icon_symbol,
                        brand_color=prov.brand_color,
                        windows=[],
                        is_connected=False,
                        error_message=str(e),
                        status_message=f"Error: {e}",
                        last_updated=now
                    )

        # Preserve standard display order
        ordered_states = {}
        for pid in ["claude", "antigravity"]:
            if pid in provider_states:
                ordered_states[pid] = provider_states[pid]

        return UnifiedUsageState(
            providers=ordered_states,
            last_updated=now,
            is_demo=demo_mode
        )
