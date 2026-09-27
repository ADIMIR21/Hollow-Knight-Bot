# -*- coding: utf-8 -*-
"""The seek rule: while an open boss takes no damage the knight walks instead of swinging."""

import re
import unittest

from hk_features import (ACTION_ATTACK, ACTION_LEFT, ACTION_NONE, ACTION_RIGHT, SEEK_AFTER_FRAMES,
                         SEEK_DISTANCE, seek_action)

CLOSE = SEEK_DISTANCE - 1.0
FAR = SEEK_DISTANCE + 1.0


class SeekActionTest(unittest.TestCase):
    def test_a_closed_boss_is_never_touched(self):
        self.assertEqual(seek_action(ACTION_ATTACK, False, 999, CLOSE, 0.0, 1.0), ACTION_ATTACK)

    def test_an_open_boss_is_left_alone_until_nothing_lands(self):
        self.assertEqual(seek_action(ACTION_ATTACK, True, SEEK_AFTER_FRAMES - 1, CLOSE, 0.0, 1.0),
                         ACTION_ATTACK)

    def test_nothing_landing_close_to_the_boss_walks_the_way_the_knight_faces(self):
        # Facing right, so the knight was heading right: keep crossing the body that way.
        self.assertEqual(seek_action(ACTION_ATTACK, True, SEEK_AFTER_FRAMES, CLOSE, 0.0, 1.0),
                         ACTION_RIGHT)
        self.assertEqual(seek_action(ACTION_ATTACK, True, SEEK_AFTER_FRAMES, CLOSE, 0.0, 0.0),
                         ACTION_LEFT)

    def test_nothing_landing_far_from_the_boss_turns_round_and_comes_back(self):
        self.assertEqual(seek_action(ACTION_ATTACK, True, SEEK_AFTER_FRAMES, FAR, 0.5, 0.0),
                         ACTION_RIGHT)
        self.assertEqual(seek_action(ACTION_ATTACK, True, SEEK_AFTER_FRAMES, FAR, -0.5, 1.0),
                         ACTION_LEFT)

    def test_every_action_is_overruled_not_only_attacks(self):
        # Standing still is what loses the window, so the rule cannot be limited to attacks.
        for action in (ACTION_NONE, ACTION_ATTACK):
            self.assertEqual(seek_action(action, True, SEEK_AFTER_FRAMES, CLOSE, 0.0, 1.0),
                             ACTION_RIGHT, action)

    def test_the_environment_counts_the_frames_and_passes_them_in(self):
        body = open("hk_gym.py", encoding="utf-8").read()
        self.assertIn("self._no_damage_frames = 0 if damage_now > 0 else self._no_damage_frames + 1",
                      body, "hk_gym.py stopped counting the frames without damage")
        call = re.search(r"seek_action\((.*?)\)", body, re.S)
        self.assertIsNotNone(call, "hk_gym.py no longer calls seek_action")
        for needed in ("last_boss_open", "_no_damage_frames", "last_facing_right"):
            self.assertIn(needed, call.group(1), "seek_action is called without " + needed)


if __name__ == "__main__":
    unittest.main()
