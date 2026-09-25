"""
Google Antigravity (Gemini & 3P) Provider.
Auto-discovers the local language server port and CSRF token without any user configuration.
"""

import os
import re
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple, List
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .base import BaseProvider
from ..core.models import ProviderUsageState, UsageWindow
from ..ui.theme import GEMINI_BLUE


class AntigravityProvider(BaseProvider):
    provider_id = "antigravity"
    name = "Antigravity (Gemini)"
    icon_symbol = "✦"
    brand_color = GEMINI_BLUE

    def __init__(self):
        self.cached_port: Optional[int] = None
        self.cached_csrf: Optional[str] = None
        self.last_port: Optional[int] = None

    def _get_appdata_log_path(self) -> Path:
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Antigravity" / "logs" / "main.log"

    def _extract_csrf_from_process(self) -> Tuple[Optional[str], List[int]]:
        """Inspect running language_server process to extract the active --csrf_token
        and any listening TCP ports.
        """
        csrf = None
        candidate_ports: List[int] = []

        try:
            import psutil
            for proc in psutil.process_iter(['name', 'cmdline']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    if 'language_server' in name:
                        cmdline = proc.info.get('cmdline') or []
                        for i, arg in enumerate(cmdline):
                            if arg == '--csrf_token' and i + 1 < len(cmdline):
                                csrf = cmdline[i + 1]
                                break
                            elif arg.startswith('--csrf_token='):
                                csrf = arg.split('=', 1)[1]
                                break

                        try:
                            for conn in proc.net_connections(kind='tcp'):
                                if conn.status == psutil.CONN_LISTEN and conn.laddr:
                                    candidate_ports.append(conn.laddr.port)
                        except Exception:
                            pass

                        if csrf:
                            break
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except ImportError:
            pass

        # Windows fallback: query via CIM if psutil didn't find csrf
        if not csrf and os.name == 'nt':
            try:
                import subprocess
                out = subprocess.check_output(
                    [
                        "powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name LIKE '%language_server%'\" | Select-Object -ExpandProperty CommandLine"
                    ],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    text=True,
                    timeout=2.0
                )
                match = re.search(r"--csrf_token(?:\s+|=)([a-f0-9\-]+)", out)
                if match:
                    csrf = match.group(1)
            except Exception:
                pass

        return csrf, candidate_ports

    def discover_all_credentials(self) -> Tuple[List[int], Optional[str]]:
        """Discover candidate ports and active CSRF token across process cmdline and logs."""
        csrf, candidate_ports = self._extract_csrf_from_process()

        log_port = None
        log_path = self._get_appdata_log_path()
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(max(0, size - 65536))
                    content = f.read()

                ports = re.findall(r"Reloading all windows with URL:\s*https://127\.0\.0\.1:(\d+)/", content)
                if not ports:
                    ports = re.findall(r"Local:\s*https://127\.0\.0\.1:(\d+)/", content)
                if ports:
                    log_port = int(ports[-1])

                if not csrf:
                    tokens = re.findall(r"--csrf_token\s+([a-f0-9\-]+)", content)
                    if tokens:
                        csrf = tokens[-1]
            except Exception as e:
                print(f"[AntigravityProvider] Log inspection error: {e}")

        ordered_ports: List[int] = []
        if log_port:
            ordered_ports.append(log_port)
        for cp in candidate_ports:
            if cp not in ordered_ports:
                ordered_ports.append(cp)

        return ordered_ports, csrf

    def discover_credentials(self) -> Tuple[Optional[int], Optional[str]]:
        ports, csrf = self.discover_all_credentials()
        return (ports[0] if ports else None), csrf

    def _query_quota(self, port: int, csrf: str, timeout: float = 2.0) -> requests.Response:
        url = f"https://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
        headers = {
            "Content-Type": "application/json",
            "x-codeium-csrf-token": csrf,
            "Connect-Protocol-Version": "1",
        }
        return requests.post(url, headers=headers, json={}, verify=False, timeout=timeout)

    def fetch_usage(self, demo_mode: bool = False) -> ProviderUsageState:
        now = datetime.now(timezone.utc)
        if demo_mode:
            return self._generate_demo_state()

        # 1. Fast path: try cached credentials
        if self.cached_port and self.cached_csrf:
            try:
                resp = self._query_quota(self.cached_port, self.cached_csrf, timeout=2.0)
                if resp.status_code == 200:
                    windows = self._parse_response(resp.json())
                    self.last_port = self.cached_port
                    return ProviderUsageState(
                        provider_id=self.provider_id,
                        provider_name=self.name,
                        icon_symbol=self.icon_symbol,
                        brand_color=self.brand_color,
                        windows=windows,
                        is_connected=True,
                        status_message=f"Connected (Port {self.cached_port})",
                        last_updated=now
                    )
                # Stale token or wrong port -> invalidate cache and rediscover
                self.cached_port = None
                self.cached_csrf = None
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                self.cached_port = None
                self.cached_csrf = None

        # 2. Rediscover credentials
        candidate_ports, csrf = self.discover_all_credentials()
        if not csrf or not candidate_ports:
            return ProviderUsageState(
                provider_id=self.provider_id,
                provider_name=self.name,
                icon_symbol=self.icon_symbol,
                brand_color=self.brand_color,
                windows=[],
                is_connected=False,
                error_message="Antigravity language server not running. (Open Antigravity IDE)",
                status_message="Waiting for Antigravity...",
                last_updated=now
            )

        last_error = None
        for port in candidate_ports:
            try:
                resp = self._query_quota(port, csrf, timeout=2.0)
                if resp.status_code == 200:
                    self.cached_port = port
                    self.cached_csrf = csrf
                    self.last_port = port
                    windows = self._parse_response(resp.json())
                    return ProviderUsageState(
                        provider_id=self.provider_id,
                        provider_name=self.name,
                        icon_symbol=self.icon_symbol,
                        brand_color=self.brand_color,
                        windows=windows,
                        is_connected=True,
                        status_message=f"Connected (Port {port})",
                        last_updated=now
                    )
                elif resp.status_code == 401:
                    last_error = "HTTP 401 (Authentication failed)"
                else:
                    last_error = f"HTTP {resp.status_code}"
            except requests.exceptions.SSLError:
                # Ignore non-HTTPS ports (e.g. internal HTTP UI ports)
                continue
            except requests.exceptions.ConnectionError:
                last_error = "Could not connect to Antigravity (Connection refused)."
            except Exception as e:
                last_error = str(e)

        error_msg = last_error or "Could not connect to Antigravity."
        status_msg = (
            "Antigravity is offline"
            if "Connection refused" in error_msg
            else "Authentication error"
            if "401" in error_msg
            else f"Error: {error_msg}"
        )
        return ProviderUsageState(
            provider_id=self.provider_id,
            provider_name=self.name,
            icon_symbol=self.icon_symbol,
            brand_color=self.brand_color,
            windows=[],
            is_connected=False,
            error_message=error_msg,
            status_message=status_msg,
            last_updated=now
        )

    def _parse_response(self, data: dict) -> List[UsageWindow]:
        windows: List[UsageWindow] = []
        groups = data.get("response", {}).get("groups", [])

        for group in groups:
            group_name = group.get("displayName", "")
            buckets = group.get("buckets", [])

            for bucket in buckets:
                raw_id = bucket.get("bucketId", "").lower()
                display_name = bucket.get("displayName", "")
                window_type = bucket.get("window", "").lower()
                rem_frac = float(bucket.get("remainingFraction", 1.0))
                rem_frac = max(0.0, min(1.0, rem_frac))

                is_5h = ("5h" in window_type or "5h" in raw_id or "five" in display_name.lower())
                is_gemini = "gemini" in group_name.lower() or "gemini" in raw_id

                if is_gemini:
                    key = "gemini_5h" if is_5h else "gemini_weekly"
                    label = "Gemini · 5-Hour" if is_5h else "Gemini · Weekly"
                else:
                    key = "3p_5h" if is_5h else "3p_weekly"
                    label = "Claude & GPT · 5-Hour" if is_5h else "Claude & GPT · Weekly"

                window_hours = 5 if is_5h else 168
                utilization = round((1.0 - rem_frac) * 100.0, 1)

                reset_time_str = bucket.get("resetTime", "")
                resets_at = None
                if reset_time_str:
                    try:
                        resets_at = datetime.fromisoformat(reset_time_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                w = UsageWindow(
                    key=key,
                    label=label,
                    provider_id=self.provider_id,
                    icon_symbol=self.icon_symbol,
                    brand_color=self.brand_color,
                    utilization=utilization,
                    resets_at=resets_at,
                    window_hours=window_hours
                )
                windows.append(w)

        order = {"gemini_5h": 0, "gemini_weekly": 1, "3p_5h": 2, "3p_weekly": 3}
        windows.sort(key=lambda w: order.get(w.key, 99))
        return windows

    def _generate_demo_state(self) -> ProviderUsageState:
        now = datetime.now(timezone.utc)
        w1 = UsageWindow(
            key="gemini_5h",
            label="Gemini · 5-Hour Limit",
            provider_id=self.provider_id,
            icon_symbol=self.icon_symbol,
            brand_color=self.brand_color,
            utilization=14.3,
            resets_at=now + timedelta(hours=4, minutes=18),
            window_hours=5
        )
        w2 = UsageWindow(
            key="gemini_weekly",
            label="Gemini · Weekly Limit",
            provider_id=self.provider_id,
            icon_symbol=self.icon_symbol,
            brand_color=self.brand_color,
            utilization=2.4,
            resets_at=now + timedelta(days=6, hours=23),
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
            status_message="Demo Mode",
            last_updated=now
        )
