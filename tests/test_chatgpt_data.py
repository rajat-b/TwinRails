"""Tests for ChatGPT (OpenAI) quota parsing, demo state, and credentials handling."""

import unittest
from datetime import datetime, timezone
from src.providers.chatgpt import ChatGPTProvider


class ChatGPTDataTests(unittest.TestCase):
    def setUp(self):
        self.provider = ChatGPTProvider()

    def test_parse_response_primary_and_secondary_windows(self):
        sample_data = {
            "user_id": "user-12345",
            "plan_type": "plus",
            "rate_limit": {
                "allowed": True,
                "limit_reached": False,
                "primary_window": {
                    "used_percent": 42.5,
                    "limit_window_seconds": 18000,
                    "reset_after_seconds": 9000,
                    "reset_at": 1784530149
                },
                "secondary_window": {
                    "used_percent": 18.0,
                    "limit_window_seconds": 604800,
                    "reset_after_seconds": 400000,
                    "reset_at": 1785000000
                }
            }
        }

        windows, credits_metric, plan_type = self.provider._parse_response(sample_data)
        self.assertEqual(len(windows), 2)
        self.assertEqual(plan_type, "plus")
        self.assertIsNone(credits_metric)

        w1 = windows[0]
        self.assertEqual(w1.key, "chatgpt_five_hour")
        self.assertEqual(w1.label, "ChatGPT · 5-Hour")
        self.assertEqual(w1.utilization, 42.5)
        self.assertEqual(w1.window_hours, 5)
        self.assertEqual(w1.provider_id, "chatgpt")
        self.assertEqual(w1.resets_at, datetime.fromtimestamp(1784530149, timezone.utc))

        w2 = windows[1]
        self.assertEqual(w2.key, "chatgpt_weekly")
        self.assertEqual(w2.label, "ChatGPT · Weekly")
        self.assertEqual(w2.utilization, 18.0)
        self.assertEqual(w2.window_hours, 168)
        self.assertEqual(w2.resets_at, datetime.fromtimestamp(1785000000, timezone.utc))

    def test_parse_additional_rate_limits_and_code_review(self):
        sample_data = {
            "plan_type": "pro",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 10.0,
                    "limit_window_seconds": 18000,
                    "reset_at": 1784530000
                }
            },
            "additional_rate_limits": [
                {
                    "id": "codex-spark",
                    "title": "Codex Spark 5-hour",
                    "primary_window": {
                        "used_percent": 25.0,
                        "limit_window_seconds": 18000,
                        "reset_at": 1784535000
                    }
                }
            ],
            "code_review_rate_limit": {
                "primary_window": {
                    "used_percent": 5.0,
                    "limit_window_seconds": 86400,
                    "reset_at": 1784600000
                }
            },
            "credits": {
                "has_credits": True,
                "balance": "25.50"
            }
        }

        windows, credits_metric, plan_type = self.provider._parse_response(sample_data)
        self.assertEqual(len(windows), 3)
        self.assertEqual(plan_type, "pro")
        self.assertIsNotNone(credits_metric)
        self.assertEqual(credits_metric.total, 25.50)
        self.assertEqual(credits_metric.key, "chatgpt_credits")

        keys = [w.key for w in windows]
        self.assertIn("chatgpt_five_hour", keys)
        self.assertIn("chatgpt_codex-spark", keys)
        self.assertIn("chatgpt_code_review", keys)

        spark_win = next(w for w in windows if w.key == "chatgpt_codex-spark")
        self.assertEqual(spark_win.label, "ChatGPT · Codex Spark 5-hour")
        self.assertEqual(spark_win.utilization, 25.0)

        cr_win = next(w for w in windows if w.key == "chatgpt_code_review")
        self.assertEqual(cr_win.label, "ChatGPT · Code Review")
        self.assertEqual(cr_win.window_hours, 24)

    def test_demo_state_generation(self):
        demo = self.provider.fetch_usage(demo_mode=True)
        self.assertTrue(demo.is_connected)
        self.assertTrue(demo.is_demo)
        self.assertEqual(demo.provider_id, "chatgpt")
        self.assertEqual(len(demo.windows), 2)
        self.assertEqual(demo.windows[0].key, "chatgpt_five_hour")
        self.assertEqual(demo.windows[1].key, "chatgpt_weekly")

    def test_resolve_token_token_mode_missing(self):
        p = ChatGPTProvider(token="", auth_mode="token")
        tok, acc, err = p._resolve_token()
        self.assertIsNone(tok)
        self.assertIn("Access token not configured", err)

    def test_resolve_token_token_mode_present(self):
        p = ChatGPTProvider(token="custom-token-xyz", account_id="acc-123", auth_mode="token")
        tok, acc, err = p._resolve_token()
        self.assertEqual(tok, "custom-token-xyz")
        self.assertEqual(acc, "acc-123")
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
