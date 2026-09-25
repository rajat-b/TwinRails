"""The flyout's product-breakdown bar: folding rows into colour slots and
laying segments out in pixels. Loads only the flyout module, not src/ui's
package __init__, which pulls in the tray and its optional dependencies."""

import importlib
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

for package in ("src", "src.core", "src.ui"):
    module = types.ModuleType(package)
    module.__path__ = [str(ROOT / package.replace(".", "/"))]
    sys.modules.setdefault(package, module)

flyout = importlib.import_module("src.ui.flyout_widget")
theme = importlib.import_module("src.ui.theme")


class BreakdownTests(unittest.TestCase):
    def test_fold_keeps_zero_rows_and_fixed_order(self):
        rows = [("Other", 0.0, "other"), ("Claude Code", 100.0, "claude_code"),
                ("Chats", 0.0, "chat"), ("Cowork", 0.0, "cowork")]
        self.assertEqual(flyout.fold_breakdown(rows), [
            ("Claude Code", 100.0, "claude_code"), ("Chats", 0.0, "chat"),
            ("Cowork", 0.0, "cowork"), ("Other", 0.0, "other")])

    def test_unknown_products_fold_into_other(self):
        rows = [("Claude Code", 70.0, "claude_code"), ("Design", 20.0, "design"),
                ("Other", 10.0, "other")]
        self.assertEqual(flyout.fold_breakdown(rows),
                         [("Claude Code", 70.0, "claude_code"), ("Other", 30.0, "other")])
        # With no "other" row of its own, the folded slot is still called Other.
        self.assertEqual(flyout.fold_breakdown([("Design", 5.0, "design")]),
                         [("Other", 5.0, "other")])

    def test_spans_fill_the_width_with_gaps_and_skip_zeros(self):
        segments = [("Claude Code", 64.0, "claude_code"), ("Chats", 0.0, "chat"),
                    ("Cowork", 24.0, "cowork"), ("Other", 12.0, "other")]
        spans = flyout.breakdown_spans(segments, 400)
        self.assertEqual([key for _, _, key in spans], ["claude_code", "cowork", "other"])
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], 400)
        for (_, end, _), (start, _, _) in zip(spans, spans[1:]):
            self.assertEqual(start - end, flyout.BREAKDOWN_GAP)

    def test_spans_normalise_rounded_percents_and_keep_slivers(self):
        spans = flyout.breakdown_spans([("A", 99.0, "claude_code"), ("B", 0.4, "chat")], 300)
        self.assertEqual(spans[-1][1], 300)              # sums to 99.4, still fills
        self.assertGreaterEqual(spans[1][1] - spans[1][0], 2)
        self.assertEqual(flyout.breakdown_spans([("A", 0.0, "claude_code")], 300), [])

    def test_every_product_slot_has_a_colour(self):
        self.assertEqual(set(theme.BREAKDOWN_ORDER), set(theme.BREAKDOWN_COLORS))


if __name__ == "__main__":
    unittest.main()
