# -*- coding: utf-8 -*-
"""Parity check between the mod's fight telemetry (C#) and the Python that reads it.

The trainer talks to the mod over one JSON line per frame, and every field it reads goes through
`.get(name, default)`: a field renamed or dropped on the mod's side raises nothing, it quietly
becomes the default, and the policy trains on a constant instead of on the game. The reverse
direction is just as bad - a field the mod sends that nothing consumes is invisible work, and
nothing says whether the build in the game is the one the code describes.

This test reads the mod's source directly (no compiler, no game, CI-safe) and holds two lines: the
fight telemetry must carry every field hk_gym.py reads, and it must carry the arena fields that
ai_receiver.py prints. Kept in sync with the telemetry template in AiDataExporter.cs.
"""
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

MOD_SOURCE = os.path.join(ROOT, "Mod", "AiTrainHK", "AiDataExporter.cs")
CONSUMER = os.path.join(ROOT, "hk_gym.py")
DEBUG_TOOL = os.path.join(ROOT, "ai_receiver.py")

ARENA_FIELDS = ("arena_bosses", "arena_alive", "arena_hp", "arena_detail")

# \"name\": — every key of the interpolated fight-telemetry template the mod concatenates.
FIELD_RE = re.compile(r'\\"(\w+)\\":')
READ_RE = re.compile(r'telemetry\.get\(\s*"(\w+)"')
TEMPLATE_START = 'string data = $"{{\\"status\\": \\"fight\\"'


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def fight_fields(source):
    """Keys of the `status: fight` telemetry line, taken from the C# template itself."""
    start = source.index(TEMPLATE_START)
    end = source.index("Publish(data);", start)
    return set(FIELD_RE.findall(source[start:end]))


class TelemetryParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fields = fight_fields(read(MOD_SOURCE))
        cls.consumer = read(CONSUMER)
        cls.debug_tool = read(DEBUG_TOOL)

    def test_the_source_is_parsable(self):
        # If the template is ever reformatted the regex stops matching and the rest of this file
        # would pass vacuously - fail loudly instead.
        self.assertGreater(len(self.fields), 20, "no telemetry fields found in the mod's template")
        self.assertIn("boss_hp", self.fields)

    def test_the_arena_is_reported(self):
        for name in ARENA_FIELDS:
            self.assertIn(name, self.fields, f"the fight telemetry lost {name}")

    def test_every_field_the_trainer_reads_exists(self):
        read_names = set(READ_RE.findall(self.consumer))
        self.assertGreater(len(read_names), 0, "no telemetry.get() reads found in hk_gym.py")
        missing = sorted(read_names - self.fields)
        self.assertEqual(missing, [], f"hk_gym.py reads fields the mod does not send: {missing}")

    def test_the_live_debugger_shows_the_arena(self):
        for name in ARENA_FIELDS:
            self.assertIn(name, self.debug_tool, f"ai_receiver.py stopped showing {name}")

    def test_the_reward_is_driven_by_the_damage_counter(self):
        # The boss field describes a pool the game repairs in the middle of the fight (the armour
        # drains 260 -> 4, the boss falls, the pool is back at 260), so a damage term of
        # "started at, minus now" collapses to zero on every repair: one -3840 step, seven times
        # the death penalty, charged for the hit that opens the stunned window where the fight is
        # actually won. The dense term has to come from the mod's monotone counter instead.
        self.assertIn('telemetry.get("scene_damage_total"', self.consumer,
                      "hk_gym.py no longer reads the mod's cumulative damage counter")
        self.assertNotIn("_boss_hp_start", self.consumer,
                         "hk_gym.py went back to measuring damage against the boss field")


if __name__ == "__main__":
    unittest.main()
