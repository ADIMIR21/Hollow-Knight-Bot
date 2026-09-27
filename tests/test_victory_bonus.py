"""A win has to be worth more the healthier it was won at.

Two wins are not the same fight: one at eight masks is control, one at a single mask is a trade that
happened to end well. Without a bonus that tracks the masks still standing, both pay the same, and a
policy willing to tank has nothing to choose between them.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hk_features import victory_bonus  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 1000.0


class VictoryMaskBonusTest(unittest.TestCase):
    def test_a_healthier_win_pays_more(self):
        self.assertEqual(victory_bonus(BASE, 0, 9.0), BASE)
        self.assertGreater(victory_bonus(BASE, 8, 9.0), victory_bonus(BASE, 1, 9.0))
        self.assertEqual(victory_bonus(BASE, 9, 9.0), 2.0 * BASE)

    def test_the_bonus_never_goes_negative(self):
        # A missing or nonsensical mask reading must not turn a win into a punishment.
        self.assertEqual(victory_bonus(BASE, -3, 9.0), BASE)

    def test_the_knob_is_a_real_incentive_and_part_of_the_checkpoint(self):
        gym = open(os.path.join(ROOT, "hk_gym.py"), encoding="utf-8").read()
        found = re.search(r"^HERO_MAX_MASKS = ([\d.]+)$", gym, re.M)
        # Never assert on a parse that found nothing: a rename must fail here, not pass.
        self.assertIsNotNone(found, "hk_gym.py no longer defines HERO_MAX_MASKS")
        self.assertGreater(float(found.group(1)), 0.0, "a mask left standing pays nothing extra")
        self.assertRegex(
            gym, r"victory_bonus\(\s*VICTORY_REWARD,\s*current_hp,\s*HERO_MAX_MASKS\)",
            "the win no longer pays for the masks still standing")
        fingerprint = open(os.path.join(ROOT, "train.py"), encoding="utf-8").read()
        self.assertIn("hero_max_masks", fingerprint,
                      "the bonus is not part of the checkpoint fingerprint, so a model trained "
                      "without it would be resumed")


if __name__ == "__main__":
    unittest.main()
