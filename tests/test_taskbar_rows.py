"""Fitting the bar's two rows inside the taskbar: the designed layout where
it fits, and a trimmed one, with every row drawn in full, where it doesn't.
Loads only the taskbar module, not src/ui's package __init__."""

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

bar = importlib.import_module("src.ui.taskbar_bar")

DESIGNED = (bar._ROW_H_1X, bar._ROW_GAP_1X, bar._PAD_Y_1X, bar._BAR_HEIGHT_1X)


def layout(taskbar_h):
    bar._calibrate_rows(taskbar_h)
    canvas_h = bar.PAD_Y * 2 + bar.ROW_H * 2 + bar.ROW_GAP
    drawing_ends = bar.PAD_Y + bar.ROW_H + bar.ROW_GAP + bar._row_extent(bar.BAR_HEIGHT)
    return canvas_h, drawing_ends


def current():
    return (bar.ROW_H, bar.ROW_GAP, bar.PAD_Y, bar.BAR_HEIGHT)


class RowFitTests(unittest.TestCase):
    def tearDown(self):
        bar._calibrate_rows(None)

    def test_designed_layout_where_it_already_fits(self):
        # No taskbar found, and Windows 11 at 125% and 150%.
        for taskbar_h in (None, 60, 72):
            layout(taskbar_h)
            self.assertEqual(current(), DESIGNED, taskbar_h)

    def test_thin_taskbars_hold_both_rows_in_full(self):
        # Windows 11 at 100% (48px), Windows 10 at 100% (40px), and between.
        for taskbar_h in (40, 44, 47, 48, 50, 53, 56):
            canvas_h, drawing_ends = layout(taskbar_h)
            self.assertLessEqual(canvas_h, taskbar_h, taskbar_h)
            self.assertLessEqual(drawing_ends, canvas_h, taskbar_h)
            # Rows never overlap, and the bar only shrinks when it must.
            self.assertGreaterEqual(bar.ROW_H, bar._row_extent(bar.BAR_HEIGHT))
            self.assertGreaterEqual(bar.BAR_HEIGHT, bar._MIN_BAR_HEIGHT)

    def test_windows_11_at_100_percent_keeps_nearly_the_whole_bar(self):
        layout(48)
        self.assertEqual(bar.BAR_HEIGHT, bar._BAR_HEIGHT_1X - 1)

    def test_idempotent(self):
        layout(40)
        layout(None)
        self.assertEqual(current(), DESIGNED)


if __name__ == "__main__":
    unittest.main()
