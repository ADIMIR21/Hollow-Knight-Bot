"""The episode boundary of the non-blocking reset: the shaping baseline and the
restart resend.

After a death hk_gym.reset() returns before any live observation exists, so neither
the reset itself nor the awaiting steps can take the shaping baseline from a live
fight. The baseline must come from the first acting step of the new episode: a
baseline left over from the end of the previous episode hands that step the whole
phi-drop of the old one (~+200 per mask lost after a death, a large penalty after a
victory). And while a window waits for its fight scene, fresh telemetry is not
success: a restart command lost to a reconnect gap leaves the idle game streaming
telemetry forever, so the resend must fire on live telemetry too. Both rules live in
the pure hk_features.EpisodeShaping / awaiting_decision and are pinned here;
hk_gym only does the wiring.
"""

import unittest

from hk_features import EpisodeShaping, awaiting_decision, RESTART_RESEND_AFTER


def potential(hp, max_hp, weighted_damage, weighted_damage_start):
    """The shape of hk_gym's _potential with its live numbers, so the tests below
    speak the same units as the reward block (15 per damage point, 200 per mask)."""
    return (15.0 * max(0.0, weighted_damage - weighted_damage_start)
            - 200.0 * max(0.0, max_hp - hp))


class EpisodeShapingTest(unittest.TestCase):
    def shaper(self):
        return EpisodeShaping(potential)

    def test_within_an_episode_shaping_is_the_change_of_phi(self):
        s = self.shaper()
        s.rebaseline(5, 5, 0.0, 0.0)  # the live fight starts
        self.assertAlmostEqual(
            s.delta(5, 5, 30.0, 0.0), 15.0 * 30.0,
            msg="damage must pay its phi change")
        self.assertAlmostEqual(
            s.delta(3, 5, 30.0, 0.0), -200.0 * 2.0,
            msg="losing masks must pay its phi change")

    def test_the_first_episode_baselines_itself_without_a_reset(self):
        """A fresh env has no previous episode: the first acting step still starts
        from its own observation, not from the constructor's zero."""
        s = self.shaper()
        self.assertAlmostEqual(s.delta(5, 5, 0.0, 0.0), 0.0)
        self.assertAlmostEqual(s.delta(5, 5, 12.0, 0.0), 15.0 * 12.0)

    def test_a_death_does_not_credit_the_next_episode(self):
        """The bug this class replaces: the first acting step after a death must not
        pay back the phi of the dying episode. Dying with 20 weighted damage dealt
        ends at phi = 15*20 - 200*5 = -700, so a stale baseline paid +700 into the
        next episode's first step - more than DEATH_PENALTY - for doing nothing."""
        s = self.shaper()
        s.delta(5, 5, 0.0, 0.0)  # the fight starts
        s.delta(5, 5, 20.0, 0.0)  # some damage dealt
        s.delta(0, 5, 20.0, 0.0)  # the death step
        s.restart_fired()
        # The fight is up again: the first acting step baselines from its own
        # observation and starts at zero.
        self.assertAlmostEqual(s.delta(5, 5, 20.0, 20.0), 0.0)
        # The next step shapes from the new episode's own state again.
        self.assertAlmostEqual(s.delta(5, 5, 50.0, 20.0), 15.0 * 30.0)

    def test_a_victory_does_not_punish_the_next_episode(self):
        """Winning ends at a large positive phi; handed to the next episode's first
        step as a baseline, it punished the new fight for the old one's damage."""
        s = self.shaper()
        s.delta(5, 5, 0.0, 0.0)
        s.delta(4, 5, 400.0, 0.0)  # the boss falls
        s.restart_fired()
        self.assertAlmostEqual(s.delta(5, 5, 400.0, 400.0), 0.0)

    def test_a_truncated_episode_that_continues_keeps_its_shaping_continuous(self):
        """A step-limit reset continues the same live fight: reset() takes the
        baseline from its live observation against the re-set damage baseline, so
        only the increment pays."""
        s = self.shaper()
        s.rebaseline(5, 5, 0.0, 0.0)
        s.delta(3, 5, 60.0, 0.0)  # the step the limit hits
        s.rebaseline(3, 5, 60.0, 60.0)  # reset(): the damage baseline is re-set
        self.assertAlmostEqual(s.delta(3, 5, 80.0, 60.0), 15.0 * 20.0)


class AwaitingDecisionTest(unittest.TestCase):
    def test_a_fresh_frame_with_a_live_fight_starts_the_fight(self):
        self.assertEqual(awaiting_decision(True, True, 1.0), "fight_up")

    def test_the_fight_is_only_declared_on_fresh_telemetry(self):
        """The cached frame can be from before a scene change; only a fresh one may
        declare the fight up."""
        self.assertEqual(awaiting_decision(False, True, 1.0), "wait")

    def test_a_lost_restart_resends_even_while_telemetry_flows(self):
        """The bug this replaces: the resend lived only in the stalled-stream
        branch, so a lost restart command with streaming telemetry waited forever."""
        self.assertEqual(awaiting_decision(True, False, RESTART_RESEND_AFTER + 1.0),
                         "resend")

    def test_a_stalled_stream_resends_after_the_timeout(self):
        self.assertEqual(awaiting_decision(False, False, RESTART_RESEND_AFTER + 1.0),
                         "resend")

    def test_a_fight_that_comes_up_in_time_never_resends(self):
        self.assertEqual(awaiting_decision(True, True, RESTART_RESEND_AFTER + 60.0),
                         "fight_up")

    def test_young_awaiting_steps_wait(self):
        self.assertEqual(awaiting_decision(True, False, 1.0), "wait")
        self.assertEqual(awaiting_decision(False, False, 1.0), "wait")

    def test_the_timeout_boundary_resends(self):
        self.assertEqual(awaiting_decision(False, False, RESTART_RESEND_AFTER),
                         "resend")


if __name__ == "__main__":
    unittest.main()
