# -*- coding: utf-8 -*-
"""The aim rule while the boss is open: attacks travel towards it, everything else is untouched."""

import re
import unittest

from hk_features import (ACTION_ATTACK, ACTION_DASH, ACTION_JUMP, ACTION_JUMP_ATTACK, ACTION_LEFT,
                         ACTION_LEFT_ATTACK, ACTION_NONE, ACTION_RIGHT_ATTACK,
                         ACTION_UP_ATTACK, AIM_THRESHOLD, redirect_action)


class AimWhileOpenTest(unittest.TestCase):
    def test_a_closed_boss_keeps_the_plain_aiming_rule(self):
        # The old rule, unchanged: only the plain attack is aimed, by the threshold.
        self.assertEqual(redirect_action(ACTION_ATTACK, 0.9, False), ACTION_RIGHT_ATTACK)
        self.assertEqual(redirect_action(ACTION_ATTACK, -0.9, False), ACTION_LEFT_ATTACK)
        self.assertEqual(redirect_action(ACTION_JUMP_ATTACK, 0.9, False), ACTION_JUMP_ATTACK)
        self.assertEqual(redirect_action(ACTION_ATTACK, AIM_THRESHOLD / 2, False), ACTION_ATTACK)

    def test_an_open_boss_pulls_every_attack_towards_it(self):
        # The body of an open boss does not bleed; moving swings are what reach the part that does.
        for action in (ACTION_ATTACK, ACTION_JUMP_ATTACK, ACTION_UP_ATTACK):
            self.assertEqual(redirect_action(action, 0.9, True), ACTION_RIGHT_ATTACK, action)
            self.assertEqual(redirect_action(action, -0.9, True), ACTION_LEFT_ATTACK, action)

    def test_an_open_boss_leaves_non_attacks_alone(self):
        # Movement the policy asked for is its own business, and so is casting.
        for action in (ACTION_NONE, ACTION_JUMP, ACTION_DASH, ACTION_LEFT):
            self.assertEqual(redirect_action(action, 0.9, True), action)

    def test_an_open_boss_dead_ahead_changes_nothing(self):
        # Nothing to travel towards: the old rule decides, and it is already centred.
        self.assertEqual(redirect_action(ACTION_ATTACK, 0.0, True), ACTION_ATTACK)

    def test_the_environment_passes_the_state_in(self):
        body = open("hk_gym.py", encoding="utf-8").read()
        call = re.search(r"redirect_action\(action, self\.last_dx_to_boss(.*)\)", body)
        self.assertIsNotNone(call, "hk_gym.py no longer calls redirect_action")
        self.assertIn("last_boss_open", call.group(1),
                      "the aim rule is called without the boss state, so it cannot react to it")
        self.assertIn("self.last_boss_open = obs[IDX[\"boss_open\"]] > 0.5", body,
                      "hk_gym.py stopped tracking whether the boss is open")


if __name__ == "__main__":
    unittest.main()
