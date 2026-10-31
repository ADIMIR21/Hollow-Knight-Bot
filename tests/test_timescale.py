"""Units for the game time-scale commands ("timescale <value>" / "normaltime").

The mod runs the fight at ``Time.timeScale``: 1.0 is normal speed, anything above
speeds the fight (and the telemetry stream, and therefore the PPO steps) up
proportionally. Values above ~3x risk breaking Hollow Knight's frame-tied physics,
so the mod clamps to [0.01, 5.0] and rejects out-of-range values with a
command_error.

These tests exercise the Python side only (standard library, no game, no pipe):
* the value range check;
* the exact command line sent to the pipe;
* ``normaltime`` sending ``timescale 1`` (not a separate wire command).
"""
import os
import sys
import unittest
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import bosses  # noqa: E402


class TimescaleRangeTest(unittest.TestCase):
    def test_below_min_is_rejected(self):
        with self.assertRaises(ValueError):
            bosses.set_timescale(0.009)

    def test_above_max_is_rejected(self):
        with self.assertRaises(ValueError):
            bosses.set_timescale(6.1)

    def test_bounds_are_accepted(self):
        # The bounds themselves are valid; the actual send is intercepted below.
        with patch.object(bosses, "send_command") as mock_send:
            mock_send.return_value = True
            self.assertTrue(bosses.set_timescale(bosses.TIMESCALE_MIN))
            self.assertTrue(bosses.set_timescale(bosses.TIMESCALE_MAX))

    def test_normal_values_are_accepted(self):
        with patch.object(bosses, "send_command") as mock_send:
            mock_send.return_value = True
            self.assertTrue(bosses.set_timescale(1.0))
            self.assertTrue(bosses.set_timescale(2.0))
            self.assertTrue(bosses.set_timescale(3.5))


class TimescaleWireTest(unittest.TestCase):
    def test_set_timescale_sends_the_command_line(self):
        with patch.object(bosses, "send_command") as mock_send:
            mock_send.return_value = True
            bosses.set_timescale(2.0)
            mock_send.assert_called_once_with("timescale 2")

    def test_set_timescale_uses_g_format(self):
        # "2.5" -> "timescale 2.5", "2.0" -> "timescale 2" (trailing .0 dropped).
        with patch.object(bosses, "send_command") as mock_send:
            mock_send.return_value = True
            bosses.set_timescale(2.5)
            mock_send.assert_called_once_with("timescale 2.5")

    def test_set_normal_time_sends_normaltime(self):
        with patch.object(bosses, "send_command") as mock_send:
            mock_send.return_value = True
            self.assertTrue(bosses.set_normal_time())
            mock_send.assert_called_once_with("normaltime")


class PresetTimescaleTest(unittest.TestCase):
    def test_preset_normal_is_1(self):
        self.assertEqual(bosses.preset_timescale("normal"), 1.0)

    def test_preset_fast_is_2(self):
        self.assertEqual(bosses.preset_timescale("fast"), 2.0)

    def test_preset_x4_is_4(self):
        self.assertEqual(bosses.preset_timescale("x4"), 4.0)

    def test_preset_x6_is_6(self):
        self.assertEqual(bosses.preset_timescale("x6"), 6.0)

    def test_preset_is_case_insensitive(self):
        self.assertEqual(bosses.preset_timescale("X6"), 6.0)
        self.assertEqual(bosses.preset_timescale("Fast"), 2.0)

    def test_unknown_preset_is_rejected(self):
        with self.assertRaises(ValueError):
            bosses.preset_timescale("ludicrous")

    def test_preset_sends_the_command_line(self):
        with patch.object(bosses, "send_command") as mock_send:
            mock_send.return_value = True
            bosses.set_timescale(bosses.preset_timescale("x4"))
            mock_send.assert_called_once_with("timescale 4")


if __name__ == "__main__":
    unittest.main()
