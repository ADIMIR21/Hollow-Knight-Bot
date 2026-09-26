# -*- coding: utf-8 -*-
"""Unit tests for the Python side that runs without the game and without pip packages.

Covers the boss registry (bosses.py) and its query resolver: that is what teleport.py,
train.py and the teleport menu rely on to pick a fight. Only the standard library is used,
so the suite runs on any OS and in CI without installing torch/vgamepad:

    python -m unittest discover -s tests -p "test_*.py" -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bosses  # noqa: E402


class NormalizeTest(unittest.TestCase):
    def test_lowercase_and_strip(self):
        self.assertEqual(bosses.normalize("  Hornet  "), "hornet")

    def test_spaces_and_dashes_become_underscores(self):
        self.assertEqual(bosses.normalize("False Knight"), "false_knight")
        self.assertEqual(bosses.normalize("false-knight"), "false_knight")

    def test_repeated_separators_collapse(self):
        self.assertEqual(bosses.normalize("false   knight"), "false_knight")

    def test_empty_and_none(self):
        self.assertEqual(bosses.normalize(None), "")
        self.assertEqual(bosses.normalize("   "), "")


class RegistryTest(unittest.TestCase):
    def test_registry_size(self):
        self.assertEqual(len(bosses.BOSS_LIST), 60)

    def test_scenes_are_unique(self):
        scenes = [scene for scene, _ in bosses.BOSS_LIST]
        self.assertEqual(len(scenes), len(set(scenes)))

    def test_labels_are_unique(self):
        labels = [label for _, label in bosses.BOSS_LIST]
        self.assertEqual(len(labels), len(set(labels)))

    def test_default_scene_is_in_the_registry(self):
        self.assertIn(bosses.DEFAULT_SCENE, [scene for scene, _ in bosses.BOSS_LIST])

    def test_default_gate_is_the_godhome_entrance(self):
        # door_dreamEnter is the only TransitionPoint in a GG_* arena: with any other name
        # the game searches for a gate that does not exist and the screen stays on a white
        # fade forever (see README, "Known pitfalls").
        self.assertEqual(bosses.DEFAULT_GATE, "door_dreamEnter")

    def test_aliases_point_at_real_scenes(self):
        scenes = {scene for scene, _ in bosses.BOSS_LIST}
        for alias, scene in bosses._EXTRA_ALIASES.items():
            self.assertIn(scene, scenes, f"alias {alias!r} points at unknown scene {scene!r}")

    def test_aliases_are_already_normalized(self):
        # resolve_query() normalizes the incoming query before looking an alias up, so an
        # alias stored with a space or in upper case would be unreachable.
        for alias in bosses._EXTRA_ALIASES:
            self.assertEqual(bosses.normalize(alias), alias)


class ResolveQueryTest(unittest.TestCase):
    def test_by_list_number(self):
        self.assertEqual(bosses.resolve_query("1"), bosses.BOSS_LIST[0])
        self.assertEqual(bosses.resolve_query(str(len(bosses.BOSS_LIST))), bosses.BOSS_LIST[-1])

    def test_out_of_range_number_is_rejected(self):
        self.assertIsNone(bosses.resolve_query("0"))
        self.assertIsNone(bosses.resolve_query(str(len(bosses.BOSS_LIST) + 1)))

    def test_scene_name_in_any_case(self):
        self.assertEqual(bosses.resolve_query("gg_false_knight"), ("GG_False_Knight", "False Knight"))
        self.assertEqual(bosses.resolve_query("GG_Radiance"), ("GG_Radiance", "The Radiance"))

    def test_alias(self):
        self.assertEqual(bosses.resolve_query("nkg")[0], "GG_Grimm_Nightmare")
        self.assertEqual(bosses.resolve_query("sisters")[0], "GG_Mantis_Lords_V")
        self.assertEqual(bosses.resolve_query("thk")[0], "GG_Hollow_Knight")

    def test_exact_label(self):
        self.assertEqual(bosses.resolve_query("Hornet Protector")[0], "GG_Hornet_1")
        self.assertEqual(bosses.resolve_query("hornet sentinel")[0], "GG_Hornet_2")

    def test_partial_label_prefers_the_base_version(self):
        # "vengefly" matches both the normal fight and the _V variant (Ascended/Radiant);
        # the ambiguity rule has to pick the base one, not the variant.
        self.assertEqual(bosses.resolve_query("vengefly")[0], "GG_Vengefly")
        self.assertEqual(bosses.resolve_query("markoth")[0], "GG_Ghost_Markoth")

    def test_unknown_query(self):
        self.assertIsNone(bosses.resolve_query("GG_No_Such_Boss_Scene"))
        self.assertIsNone(bosses.resolve_query(""))
        self.assertIsNone(bosses.resolve_query(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
