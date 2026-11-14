"""Units for the game time-scale commands ("timescale <value>" / "normaltime").

The mod runs the fight at ``Time.timeScale``: 1.0 is normal speed, anything above
speeds the fight (and the telemetry stream, and therefore the PPO steps) up
proportionally. Values above ~3x risk breaking Hollow Knight's frame-tied physics,
so the mod clamps to [0.01, 6.0] and rejects out-of-range values with a
command_error.

These tests exercise the Python side only (standard library, no game, no pipe):
* the value range check;
* the exact command line sent to the pipe;
* ``normaltime`` sending ``timescale 1`` (not a separate wire command);
* the shared command-line resolver (preset/raw/invalid -> a validated value);
* the clamp bounds parity between bosses.py, the mod's C# and the pipe mock.
"""
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import bosses  # noqa: E402

MOD_SOURCE = Path(REPO) / "Mod" / "AiTrainHK" / "AiDataExporter.cs"
MOCK_SOURCE = Path(REPO) / "tests" / "pipe_sim" / "Program.cs"


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


class ResolveTimescaleTest(unittest.TestCase):
    """The command-line resolver both entry points share."""

    def test_a_preset_name_resolves_to_its_value(self):
        self.assertEqual(bosses.resolve_timescale("fast"), 2.0)
        self.assertEqual(bosses.resolve_timescale("x4"), 4.0)

    def test_a_raw_float_passes_through(self):
        self.assertEqual(bosses.resolve_timescale("2.5"), 2.5)
        self.assertEqual(bosses.resolve_timescale(3.0), 3.0)

    def test_an_invalid_value_falls_back_to_normal_speed(self):
        problems = []
        self.assertEqual(
            bosses.resolve_timescale("ludicrous", problem=problems.append), 1.0)
        self.assertEqual(len(problems), 1, "the console is not told why the request was dropped")

    def test_an_out_of_range_value_falls_back_to_normal_speed(self):
        problems = []
        self.assertEqual(bosses.resolve_timescale("99", problem=problems.append), 1.0)
        self.assertEqual(bosses.resolve_timescale("0", problem=problems.append), 1.0)
        self.assertEqual(len(problems), 2)

    def test_a_none_value_falls_back_to_normal_speed(self):
        self.assertEqual(bosses.resolve_timescale(None), 1.0)


class TimescaleClampParityTest(unittest.TestCase):
    """bosses.py, the mod and the pipe mock must clamp to the same range.

    Three constants describe one wire rule; nothing at runtime compares them.
    The drift already happened once on paper (this file's docstring said 5.0
    while the mod clamps to 6.0), so the sources are read and compared here -
    with an explicit parsable guard, per the house rule.
    """

    def test_the_sources_are_parsable(self):
        mod = MOD_SOURCE.read_text(encoding="utf-8", errors="replace")
        mock = MOCK_SOURCE.read_text(encoding="utf-8", errors="replace")
        self.assertIn("TIMESCALE_MIN", mod)
        self.assertIn("timescale", mock)

    def test_the_mod_clamps_where_bosses_says(self):
        mod = MOD_SOURCE.read_text(encoding="utf-8", errors="replace")
        bounds = re.findall(
            r"private const float TIMESCALE_(MIN|MAX) = ([0-9.]+)f;", mod)
        self.assertEqual(
            len(bounds), 2, "the mod's TIMESCALE_MIN/MAX constants could not be parsed")
        values = dict(bounds)
        self.assertAlmostEqual(float(values["MIN"]), bosses.TIMESCALE_MIN)
        self.assertAlmostEqual(float(values["MAX"]), bosses.TIMESCALE_MAX)

    def test_the_mock_clamps_where_the_mod_does(self):
        mod = MOD_SOURCE.read_text(encoding="utf-8", errors="replace")
        mock = MOCK_SOURCE.read_text(encoding="utf-8", errors="replace")
        mod_bounds = re.findall(
            r"private const float TIMESCALE_(?:MIN|MAX) = ([0-9.]+)f;", mod)
        mock_bounds = re.search(
            r"newScale < ([0-9.]+)f \|\| newScale > ([0-9.]+)f", mock)
        self.assertTrue(
            mod_bounds, "the mod's clamp constants could not be parsed")
        self.assertIsNotNone(
            mock_bounds, "the mock's timescale clamp could not be parsed")
        self.assertEqual(
            [float(v) for v in mock_bounds.groups()], [float(v) for v in mod_bounds],
            "the mock and the mod clamp timescale differently - the harness would "
            "pass a command the real mod rejects (or the reverse)")


if __name__ == "__main__":
    unittest.main()
