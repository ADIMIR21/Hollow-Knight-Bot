"""The potential's baseline discipline at episode boundaries.

The shaping a step pays is the change of the potential, so everything depends on where
the baseline sits. The one way this signal was lost: a fresh fight inherited the finished
fight's baseline, and its first step paid the whole previous fight's damage back as one
huge negative spike - on every episode boundary, several times the victory bonus.

hk_features.PotentialTracker owns the baseline, so its behaviour is tested by execution.
hk_gym.py itself cannot be imported here (numpy, cv2, the game stack), so the wiring -
that the tracker is re-based at both boundaries and that the stale ``_last_phi``
attribute is gone - is checked against the source, the same way
test_registry_parity.py reads the mod's C# (with an explicit parsable guard).
"""
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from hk_features import PotentialTracker  # noqa: E402

GYM_PY = os.path.join(REPO, "hk_gym.py")

# The reward economics the tracker is constructed with in hk_gym (hk_gym.py's block).
DAMAGE_REWARD_PER_HP = 15.0
HEALTH_PENALTY_PER_MASK = 200.0


def read_gym_source():
    with open(GYM_PY, "r", encoding="utf-8") as handle:
        return handle.read()


class PotentialTrackerTest(unittest.TestCase):
    def test_first_delta_after_rebaseline_is_zero(self):
        tracker = PotentialTracker(DAMAGE_REWARD_PER_HP, HEALTH_PENALTY_PER_MASK)
        tracker.rebaseline(0.0, 0.0)
        self.assertEqual(tracker.delta(0.0, 0.0), 0.0)

    def test_a_fresh_fight_does_not_pay_back_the_finished_one(self):
        """The red case: a finished fight's baseline must not leak into the next one."""
        tracker = PotentialTracker(DAMAGE_REWARD_PER_HP, HEALTH_PENALTY_PER_MASK)
        # The previous fight ends with its damage already paid (260 hp of a False Knight).
        tracker.delta(260.0, 0.0)
        self.assertAlmostEqual(tracker.last_phi, 260.0 * DAMAGE_REWARD_PER_HP)
        # The new fight starts: the baseline is re-taken at the arena, no damage done,
        # full health - the first step must not charge anything.
        tracker.rebaseline(0.0, 0.0)
        self.assertEqual(tracker.delta(0.0, 0.0), 0.0)
        # The next step pays only its own increment.
        self.assertAlmostEqual(
            tracker.delta(32.0, 0.0), 32.0 * DAMAGE_REWARD_PER_HP)

    def test_damage_pays_and_health_costs(self):
        tracker = PotentialTracker(DAMAGE_REWARD_PER_HP, HEALTH_PENALTY_PER_MASK)
        tracker.rebaseline(0.0, 0.0)
        self.assertAlmostEqual(
            tracker.delta(10.0, 1.0),
            10.0 * DAMAGE_REWARD_PER_HP - HEALTH_PENALTY_PER_MASK)

    def test_phi_evaluates_without_moving_the_baseline(self):
        tracker = PotentialTracker(DAMAGE_REWARD_PER_HP, HEALTH_PENALTY_PER_MASK)
        tracker.rebaseline(0.0, 0.0)
        before = tracker.last_phi
        self.assertAlmostEqual(
            tracker.phi(5.0, 0.5),
            5.0 * DAMAGE_REWARD_PER_HP - 0.5 * HEALTH_PENALTY_PER_MASK)
        self.assertEqual(tracker.last_phi, before)

    def test_a_mid_fight_step_pays_only_its_own_increment(self):
        tracker = PotentialTracker(DAMAGE_REWARD_PER_HP, HEALTH_PENALTY_PER_MASK)
        tracker.rebaseline(32.0, 0.0)
        # One more uncharged nail hit, no masks lost this step.
        self.assertAlmostEqual(tracker.delta(64.0, 0.0), 32.0 * DAMAGE_REWARD_PER_HP)


class GymWiringTest(unittest.TestCase):
    """The wiring hk_gym.py cannot be imported to check."""

    def setUp(self):
        self.gym = read_gym_source()

    def test_the_source_is_parsable(self):
        for needle in ("PotentialTracker(", "_phi.rebaseline(", "_phi.delta(",
                       "def _damage_done", "def reset", "def step"):
            self.assertIn(needle, self.gym,
                          "hk_gym.py no longer contains %r" % needle)

    def test_the_tracker_is_built_from_the_named_reward_numbers(self):
        match = re.search(
            r"PotentialTracker\(\s*DAMAGE_REWARD_PER_HP\s*,\s*HEALTH_PENALTY_PER_MASK\s*\)",
            self.gym)
        self.assertIsNotNone(
            match,
            "hk_gym.py does not construct PotentialTracker from the named reward constants")

    def test_reset_rebaselines_unconditionally(self):
        reset = self._method_body("reset")
        self.assertIsNotNone(reset, "reset() is missing from hk_gym.py")
        self.assertIn("_phi.rebaseline(", reset)
        # The old bug: the rebaseline sat behind `if self._episode_active:`, which the
        # fresh-fight branch had just set to False - so a new episode kept the finished
        # fight's baseline.
        self.assertNotIn(
            "if self._episode_active:", reset,
            "the potential rebaseline is gated again - a fresh fight would inherit the "
            "finished fight's baseline")

    def test_the_fight_ready_transition_rebaselines(self):
        step = self._method_body("step")
        self.assertIsNotNone(step, "step() is missing from hk_gym.py")
        # The rebaseline has to sit between the awaiting-fight branch and the shaping:
        # the obs at reset can still describe the finished fight.
        fight_ready = re.search(
            r"_fight_in_progress\(\).*?(?=elif time\.time\(\))", step, re.DOTALL)
        self.assertIsNotNone(
            fight_ready, "the fight-ready transition is missing from step()")
        self.assertIn("_phi.rebaseline(", fight_ready.group(0))

    def test_shaping_goes_through_the_tracker(self):
        step = self._method_body("step")
        self.assertIsNotNone(step, "step() is missing from hk_gym.py")
        self.assertIn("_phi.delta(", step)
        self.assertNotIn(
            "self._last_phi", self.gym,
            "the stale _last_phi attribute is back - the tracker owns the baseline now")

    def _method_body(self, name):
        match = re.search(r"^    def %s\(" % re.escape(name), self.gym, re.MULTILINE)
        if not match:
            return None
        following = re.search(r"\n    def |\nclass ", self.gym[match.end():])
        stop = match.end() + following.start() if following else len(self.gym)
        return self.gym[match.start():stop]


if __name__ == "__main__":
    unittest.main()
