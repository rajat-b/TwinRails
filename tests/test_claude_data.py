"""Claude usage shape checks that run without the optional app dependencies."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


for package in ("src", "src.core"):
    module = types.ModuleType(package)
    module.__path__ = [str(ROOT / package.replace(".", "/"))]
    sys.modules[package] = module

sys.modules.setdefault("requests", types.ModuleType("requests"))
curl = types.ModuleType("curl_cffi")
curl.requests = types.ModuleType("curl_cffi.requests")
sys.modules.setdefault("curl_cffi", curl)
sys.modules.setdefault("curl_cffi.requests", curl.requests)

models = _load("src.core.models", ROOT / "src/core/models.py")
client = _load("src.core.claude_client", ROOT / "src/core/claude_client.py")


class ClaudeDataTests(unittest.TestCase):
    def test_scoped_windows_have_distinct_stable_keys(self):
        def row(model_id, name):
            return {"kind": "weekly_scoped", "group": "weekly", "percent": 23,
                    "scope": {"model": {"id": model_id, "display_name": name}, "surface": None}}

        windows = client._extract_limits_windows({"limits": [row("a", "Fable"), row("b", "Opus")]})
        self.assertEqual(len(windows), 2)
        self.assertNotEqual(windows[0].key, windows[1].key)
        self.assertEqual([window.short_tag for window in windows], ["FAB", "OPU"])
        renamed = client._extract_limits_windows({"limits": [row("a", "Fable New")]})[0]
        self.assertEqual(windows[0].key, renamed.key)
        self.assertEqual(renamed.label, "Weekly · Fable New")

    def test_balance_requires_real_matching_cap(self):
        no_cap = {"spend": {"used": {"amount_minor": 483, "currency": "USD", "exponent": 2},
                            "limit": None}}
        self.assertIsNone(client._extract_credits(no_cap))
        capped = {"spend": {"used": {"amount_minor": 483, "currency": "USD", "exponent": 2},
                           "limit": {"amount_minor": 500, "currency": "USD", "exponent": 2}}}
        self.assertEqual(client._extract_credits(capped).remaining_label, "$0.17 left")
        capped["spend"]["limit"]["currency"] = "EUR"
        self.assertIsNone(client._extract_credits(capped))
        del capped["spend"]["used"]["amount_minor"]
        self.assertIsNone(client._extract_credits(capped))

    def test_flags_and_breakdown_are_only_reported_when_explicit(self):
        data = {"extra_usage": {"is_enabled": False, "spend_limit_reached": True},
                "seven_day_breakdown": {"rows": [{"display_name": "Claude Code", "percent": 36},
                                                  {"display_name": "Unknown", "percent": None}]}}
        self.assertEqual(client._extract_extra_usage_notes(data),
                         ["Extra usage: Off", "Extra usage spending cap reached"])
        # No "key" on the row: the lower-cased name stands in for it.
        self.assertEqual(client._extract_weekly_breakdown(data), [("Claude Code", 36.0, "claude code")])

    def test_breakdown_keeps_the_stable_product_key(self):
        data = {"seven_day_breakdown": {"rows": [
            {"key": "claude_code", "display_name": "Claude Code", "percent": 100},
            {"key": "chat", "display_name": "Chats", "percent": 0}]}}
        self.assertEqual(client._extract_weekly_breakdown(data),
                         [("Claude Code", 100.0, "claude_code"), ("Chats", 0.0, "chat")])

    # Shape of the grant as the claude.ai usage endpoint returned it on
    # 2026-09-25 (values as seen; field names are Claude's own).
    GRANT = {"utilization": 13.8838932, "resets_at": "2026-11-05T07:59:00+00:00",
             "limit_dollars": 250, "used_dollars": 34.709733, "remaining_dollars": 215.290267}

    def test_included_credit_is_found_by_shape_and_named_when_known(self):
        found = client._extract_included_credits({
            "five_hour": {"utilization": 14.0, "resets_at": None, "limit_dollars": None},
            "iguana_necktie": dict(self.GRANT),
            "some_new_grant": {"limit_dollars": 10, "used_dollars": 2.5},
            "tangelo": None,
        })
        self.assertEqual([source for source, _ in found], ["iguana_necktie", "some_new_grant"])
        cloud, other = found[0][1], found[1][1]
        self.assertEqual((cloud.key, cloud.label), ("cloud_session_credits", "Cloud Session Credits"))
        self.assertEqual(cloud.remaining_label, "$215.29 left")
        self.assertEqual(cloud.expires_at.isoformat(), "2026-11-05T07:59:00+00:00")
        self.assertFalse(cloud.has_time_window)
        self.assertEqual((other.key, other.label), ("included_credit_some_new_grant", "Included Credit"))
        self.assertIsNone(other.expires_at)

    def test_included_credit_rejects_bad_amounts(self):
        for bad in ({"limit_dollars": 0, "used_dollars": 1}, {"limit_dollars": 5, "used_dollars": -1},
                    {"limit_dollars": True, "used_dollars": 1}, {"limit_dollars": 5, "used_dollars": None},
                    {"limit_dollars": float("inf"), "used_dollars": 1}):
            self.assertEqual(client._extract_included_credits({"grant": bad}), [], bad)

    def test_window_fallback_skips_dollar_grants(self):
        # Under an unlisted name the grant is still window-shaped; it must not
        # also become a bogus 5-hour bar.
        data = {"renamed_grant": dict(self.GRANT),
                "new_window": {"utilization": 40.0, "resets_at": "2026-10-01T20:00:00+00:00"}}
        self.assertEqual([w.key for w in client._extract_extra_windows_by_shape(data, set())],
                         ["new_window"])


oauth = sys.modules["src.core.claude_oauth"]


class RateLimitAndTokenTests(unittest.TestCase):
    """How Claude fetching behaves when the sign-in route is throttled or stale."""

    def setUp(self):
        self.real_read = client.read_claude_code_token
        client.read_claude_code_token = lambda: ("token", "max")

    def tearDown(self):
        client.read_claude_code_token = self.real_read
        if hasattr(client.plain_requests, "get"):
            del client.plain_requests.get

    @staticmethod
    def live():
        return models.ClaudeUsageState(session=models.UsageWindow(key="five_hour", utilization=12.0))

    @staticmethod
    def limiter(c):
        def limited(token):
            c._oauth_retry_until = client.time.monotonic() + 300
            return models.ClaudeUsageState(error_message="Rate limited.")
        return limited

    def test_backoff_doubles_when_the_server_names_no_wait(self):
        c = client.ClaudeClient(auth_mode="oauth")

        class Throttled:
            status_code, headers, ok = 429, {"Retry-After": "0"}, False
        client.plain_requests.get = lambda *a, **k: Throttled()
        waits = []
        for _ in range(6):
            c._oauth_retry_until = 0.0          # the previous cooldown has ended
            c._fetch_oauth_usage("token")
            waits.append(c._oauth_backoff)
        self.assertEqual(waits, [300, 600, 1200, 2400, 3600, 3600])
        self.assertIn("retrying in", c._fetch_oauth_usage("token").error_message)

    def test_auto_with_a_cookie_keeps_going_while_rate_limited(self):
        c = client.ClaudeClient(session_key="key", auth_mode="auto")
        c._fetch_oauth_usage = self.limiter(c)
        c._fetch_cookie_usage = self.live
        state = c.fetch_usage()
        self.assertIsNone(state.error_message)
        self.assertEqual(state.session.utilization, 12.0)
        self.assertIn("using the session cookie", state.extra_usage_notes[-1])

    def test_signin_is_checked_at_most_every_5_minutes(self):
        c = client.ClaudeClient(auth_mode="oauth")
        calls = []
        c._fetch_oauth_usage = lambda token: calls.append(token) or self.live()
        c.fetch_usage()
        again = c.fetch_usage()                     # a 60-second refresh later
        self.assertEqual(len(calls), 1)
        self.assertEqual(again.session.utilization, 12.0)
        self.assertEqual(again.extra_usage_notes, [])     # under a minute old: no note
        c._oauth_reading_at -= client.timedelta(minutes=2)
        self.assertIn("updates at most every 5 minutes", c.fetch_usage().extra_usage_notes[-1])
        c._oauth_reading_mono -= client._OAUTH_MIN_INTERVAL
        c.fetch_usage()
        self.assertEqual(len(calls), 2)

    def test_without_a_cookie_the_last_reading_stands_in_for_30_minutes(self):
        c = client.ClaudeClient(auth_mode="oauth")
        c._fetch_oauth_usage = lambda token: self.live()
        c.fetch_usage()
        c._oauth_reading_mono -= client._OAUTH_MIN_INTERVAL     # next check is due
        c._fetch_oauth_usage = self.limiter(c)
        stale = c.fetch_usage()
        self.assertEqual(stale.session.utilization, 12.0)
        self.assertTrue(stale.error_message.startswith("Rate limited. Showing data from"))
        c._last_good_at -= client.timedelta(minutes=31)
        self.assertIsNone(c.fetch_usage().session)

    def test_expired_token_is_no_token(self):
        import json, os, tempfile
        with tempfile.TemporaryDirectory() as folder:
            def write(expires_ms):
                with open(os.path.join(folder, ".credentials.json"), "w", encoding="utf-8") as f:
                    json.dump({"claudeAiOauth": {"accessToken": "x", "expiresAt": expires_ms}}, f)
            previous = os.environ.get("CLAUDE_CONFIG_DIR")
            os.environ["CLAUDE_CONFIG_DIR"] = folder
            try:
                write((client.time.time() - 60) * 1000)
                with self.assertRaises(oauth.ClaudeTokenExpired):
                    self.real_read()
                write((client.time.time() + 3600) * 1000)
                self.assertEqual(self.real_read()[0], "x")
            finally:
                if previous is None:
                    del os.environ["CLAUDE_CONFIG_DIR"]
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = previous

    def test_auto_uses_the_cookie_when_the_token_has_expired(self):
        def expired():
            raise oauth.ClaudeTokenExpired("expired")
        client.read_claude_code_token = expired
        c = client.ClaudeClient(session_key="key", auth_mode="auto")
        c._fetch_cookie_usage = self.live
        self.assertEqual(c.fetch_usage().session.utilization, 12.0)


if __name__ == "__main__":
    unittest.main()
