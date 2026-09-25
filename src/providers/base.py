"""
Base provider interface for all AI usage limit providers.
"""

from abc import ABC, abstractmethod
from ..core.models import ProviderUsageState


class BaseProvider(ABC):
    provider_id: str = ""
    name: str = ""
    icon_symbol: str = ""
    brand_color: str = "#FFFFFF"

    @abstractmethod
    def fetch_usage(self, demo_mode: bool = False) -> ProviderUsageState:
        """Fetch current usage quotas and limits from this provider."""
        pass
