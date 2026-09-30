"""The dash is parked: the actions that press it keep their other buttons.

The emulated pad never pressed the dash, so the older checkpoints were trained in a world where
dashing did nothing. Parking the button again reproduces that world while keeping the action
space 19 wide, which is what lets those checkpoints be resumed rather than retrained.
"""

import unittest

import hk_features as F


class PressDashTest(unittest.TestCase):
    def test_the_dash_is_dropped_and_the_other_buttons_are_kept(self):
        self.assertEqual(F.press_dash(F.ACTION_DASH), F.ACTION_NONE)
        self.assertEqual(F.press_dash(F.ACTION_DASH_ATTACK), F.ACTION_ATTACK)
        self.assertEqual(F.press_dash(F.ACTION_JUMP_DASH), F.ACTION_JUMP)
        self.assertEqual(F.press_dash(F.ACTION_LEFT_DASH), F.ACTION_LEFT)
        self.assertEqual(F.press_dash(F.ACTION_RIGHT_DASH), F.ACTION_RIGHT)

    def test_every_action_without_a_dash_is_untouched(self):
        for action in range(F.ACTION_COUNT):
            if action in F.DASH_ACTIONS:
                continue
            self.assertEqual(F.press_dash(action), action, "action %d was changed" % action)

    def test_the_space_stays_nineteen_wide(self):
        """A parked button must not reshape the space: the older checkpoints are Discrete(19)."""
        self.assertEqual(F.ACTION_COUNT, 19)
        self.assertEqual(len(F.DASH_ACTIONS), 5)

    def test_the_button_can_be_given_back(self):
        self.assertEqual(F.press_dash(F.ACTION_DASH, dash_disabled=False), F.ACTION_DASH)
        self.assertEqual(F.press_dash(F.ACTION_DASH_ATTACK, dash_disabled=False),
                         F.ACTION_DASH_ATTACK)


if __name__ == "__main__":
    unittest.main()
