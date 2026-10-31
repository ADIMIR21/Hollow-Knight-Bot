"""The dash is unparked: dash actions keep their dash button by default.

The emulated pad never pressed the dash, so older checkpoints were trained in a world where
dashing did nothing and cannot be resumed without retraining. The action space stays 19 wide
either way - flipping DASH_DISABLED gives the button back without reshaping the space.
"""

import unittest

import hk_features as F


class PressDashTest(unittest.TestCase):
    def test_the_dash_is_kept_by_default(self):
        self.assertEqual(F.press_dash(F.ACTION_DASH), F.ACTION_DASH)
        self.assertEqual(F.press_dash(F.ACTION_DASH_ATTACK), F.ACTION_DASH_ATTACK)
        self.assertEqual(F.press_dash(F.ACTION_JUMP_DASH), F.ACTION_JUMP_DASH)
        self.assertEqual(F.press_dash(F.ACTION_LEFT_DASH), F.ACTION_LEFT_DASH)
        self.assertEqual(F.press_dash(F.ACTION_RIGHT_DASH), F.ACTION_RIGHT_DASH)

    def test_every_action_without_a_dash_is_untouched(self):
        for action in range(F.ACTION_COUNT):
            if action in F.DASH_ACTIONS:
                continue
            self.assertEqual(F.press_dash(action), action, "action %d was changed" % action)

    def test_the_space_stays_nineteen_wide(self):
        """An unparked button must not reshape the space: the policy still sees Discrete(19)."""
        self.assertEqual(F.ACTION_COUNT, 19)
        self.assertEqual(len(F.DASH_ACTIONS), 5)

    def test_the_button_can_be_parked(self):
        """The dash can still be dropped when DASH_DISABLED is set."""
        self.assertEqual(F.press_dash(F.ACTION_DASH, dash_disabled=True), F.ACTION_NONE)
        self.assertEqual(F.press_dash(F.ACTION_DASH_ATTACK, dash_disabled=True),
                         F.ACTION_ATTACK)
        self.assertEqual(F.press_dash(F.ACTION_JUMP_DASH, dash_disabled=True), F.ACTION_JUMP)
        self.assertEqual(F.press_dash(F.ACTION_LEFT_DASH, dash_disabled=True), F.ACTION_LEFT)
        self.assertEqual(F.press_dash(F.ACTION_RIGHT_DASH, dash_disabled=True), F.ACTION_RIGHT)


if __name__ == "__main__":
    unittest.main()
