"""The stunned window has to be the most valuable place in the fight.

The fight cannot be finished while the armour is up - it takes the hits and the game repairs it -
so the pool that ends the fight is exposed only while the boss is down. An observation flag is not
enough on its own: when a hit paid the same inside and outside the window, the policy hovered
instead of committing, attacking in about half the frames of a window and jumping around in the
rest, which is what the live telemetry showed.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hk_features import damage_weight  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GYM = os.path.join(ROOT, "hk_gym.py")
CONFIG = os.path.join(ROOT, "train.py")


class OpenWindowRewardTest(unittest.TestCase):
    def test_damage_inside_the_window_is_worth_more(self):
        self.assertEqual(damage_weight(32, False, 3.0), 32)
        self.assertGreater(damage_weight(32, True, 3.0), damage_weight(32, False, 3.0))
        self.assertEqual(damage_weight(0, True, 3.0), 0)

    def test_the_multiplier_is_a_real_incentive_and_part_of_the_checkpoint(self):
        source = open(GYM, encoding="utf-8").read()
        match = re.search(r"^OPEN_WINDOW_DAMAGE_MULTIPLIER = ([\d.]+)$", source, re.M)
        # Never assert on a parse that found nothing: a rename has to fail here, not pass.
        self.assertIsNotNone(match, "hk_gym.py no longer defines OPEN_WINDOW_DAMAGE_MULTIPLIER")
        self.assertGreater(float(match.group(1)), 1.0,
                           "the window pays no more than a hit outside it")
        # The call is wrapped across lines in hk_gym.py, so match it as a pattern rather than as
        # one literal string - and still fail loudly if the weighing disappears.
        weighed = re.search(
            r"damage_weight\(\s*damage_now,\s*boss_is_open,\s*OPEN_WINDOW_DAMAGE_MULTIPLIER\)",
            source)
        self.assertIsNotNone(weighed, "hk_gym.py stopped weighing damage by the open window")
        fingerprint = open(CONFIG, encoding="utf-8").read()
        self.assertIn("open_window_damage_multiplier", fingerprint,
                      "the multiplier is not part of the checkpoint fingerprint, so a model "
                      "trained without it would be resumed")


if __name__ == "__main__":
    unittest.main()
