"""Tests for Google Antigravity quota parsing and credentials discovery."""

import unittest
from datetime import datetime, timezone
from src.providers.antigravity import AntigravityProvider


class AntigravityDataTests(unittest.TestCase):
    def setUp(self):
        self.provider = AntigravityProvider()

    def test_parse_response_maps_quota_buckets_correctly(self):
        sample_data = {
            "response": {
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "gemini-weekly",
                                "displayName": "Weekly Limit Remaining",
                                "window": "weekly",
                                "remainingFraction": 0.85,
                                "resetTime": "2026-09-30T12:00:00Z"
                            },
                            {
                                "bucketId": "gemini-5h",
                                "displayName": "Five Hour Limit Remaining",
                                "window": "5h",
                                "remainingFraction": 0.90,
                                "resetTime": "2026-09-25T04:00:00Z"
                            }
                        ]
                    },
                    {
                        "displayName": "Claude and GPT models",
                        "buckets": [
                            {
                                "bucketId": "3p-weekly",
                                "displayName": "Weekly Limit Remaining",
                                "window": "weekly",
                                "remainingFraction": 1.0,
                                "resetTime": "2026-10-01T00:00:00Z"
                            },
                            {
                                "bucketId": "3p-5h",
                                "displayName": "Five Hour Limit Remaining",
                                "window": "5h",
                                "remainingFraction": 0.50,
                                "resetTime": "2026-09-25T05:00:00Z"
                            }
                        ]
                    }
                ]
            }
        }

        windows = self.provider._parse_response(sample_data)
        self.assertEqual(len(windows), 4)

        # Expected sorted order: gemini_5h, gemini_weekly, 3p_5h, 3p_weekly
        self.assertEqual([w.key for w in windows], ["gemini_5h", "gemini_weekly", "3p_5h", "3p_weekly"])

        w_gemini_5h = windows[0]
        self.assertEqual(w_gemini_5h.label, "Gemini · 5-Hour")
        self.assertEqual(w_gemini_5h.utilization, 10.0)
        self.assertEqual(w_gemini_5h.window_hours, 5)

        w_gemini_weekly = windows[1]
        self.assertEqual(w_gemini_weekly.label, "Gemini · Weekly")
        self.assertEqual(w_gemini_weekly.utilization, 15.0)
        self.assertEqual(w_gemini_weekly.window_hours, 168)

        w_3p_5h = windows[2]
        self.assertEqual(w_3p_5h.label, "Claude & GPT · 5-Hour")
        self.assertEqual(w_3p_5h.utilization, 50.0)

        w_3p_weekly = windows[3]
        self.assertEqual(w_3p_weekly.label, "Claude & GPT · Weekly")
        self.assertEqual(w_3p_weekly.utilization, 0.0)

    def test_demo_state_generation(self):
        demo_state = self.provider._generate_demo_state()
        self.assertTrue(demo_state.is_connected)
        self.assertTrue(demo_state.is_demo)
        self.assertEqual(len(demo_state.windows), 2)


if __name__ == "__main__":
    unittest.main()
