"""Units for `hk_features`: the action table, the aiming rule and the boss state.

`hk_features` imports nothing heavy (no numpy, no gymnasium, no vgamepad), so
unlike the rest of the environment it can be imported and exercised right here -
CI installs nothing for this job.
"""

import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import hk_features  # noqa: E402
from hk_features import (  # noqa: E402
    ACTION_ATTACK,
    ACTION_COUNT,
    ACTION_DASH_ATTACK,
    ACTION_JUMP_ATTACK,
    ACTION_LEFT_ATTACK,
    ACTION_NAMES,
    ACTION_RIGHT_ATTACK,
    AIM_THRESHOLD,
    BOSS_STATE_ATTACK,
    BOSS_STATE_DEAD,
    BOSS_STATE_FEATURES,
    BOSS_STATE_IDLE,
    BOSS_STATE_OPEN,
    BOSS_STATE_WINDUP,
    BossStateTracker,
    classify_boss_state,
    redirect_action,
)

# Boss FSM states observed in ModLog.txt during a live False Knight session, each
# with the class the tracker has to give it. The list is the fixture: if a rename
# or a lost fixture empties it, the checks below fail instead of passing vacuously
# (AGENTS.md, "Never write a test that can pass vacuously").
OBSERVED_STATES = {
    # nothing is happening
    "Idle": BOSS_STATE_IDLE, "First Idle": BOSS_STATE_IDLE, "Run": BOSS_STATE_IDLE,
    "Turn L": BOSS_STATE_IDLE, "Ready": BOSS_STATE_IDLE, "JA End": BOSS_STATE_IDLE,
    "JA Recoil": BOSS_STATE_IDLE, "Hit": BOSS_STATE_IDLE, "Hit 2": BOSS_STATE_IDLE,
    "Pause Short": BOSS_STATE_IDLE, "Steam": BOSS_STATE_IDLE, "Anim End": BOSS_STATE_IDLE,
    # the attack is announced, the hit has not landed yet
    "JA Antic": BOSS_STATE_WINDUP, "Jump Antic": BOSS_STATE_WINDUP,
    "S Antic": BOSS_STATE_WINDUP, "S Attack Antic": BOSS_STATE_WINDUP,
    "R Attack Antic": BOSS_STATE_WINDUP, "Rage Jump Antic": BOSS_STATE_WINDUP,
    "Run Antic": BOSS_STATE_WINDUP,
    # the attack is running
    "JA Slam": BOSS_STATE_ATTACK, "Slam": BOSS_STATE_ATTACK, "S Attack": BOSS_STATE_ATTACK,
    # stunned or recovering: the punish window
    "Opened": BOSS_STATE_OPEN, "Opened 2": BOSS_STATE_OPEN, "Open Uuup": BOSS_STATE_OPEN,
    "Stun In Air": BOSS_STATE_OPEN, "Stun Land": BOSS_STATE_OPEN, "Stun Fail": BOSS_STATE_OPEN,
    "Recover": BOSS_STATE_OPEN, "S Attack Recover": BOSS_STATE_OPEN,
    # the fight is over
    "Death Anim Start": BOSS_STATE_DEAD, "Death Open": BOSS_STATE_DEAD,
}


def source_of(name):
    with open(os.path.join(REPO, name), "r", encoding="utf-8") as handle:
        return handle.read()


class ActionTable(unittest.TestCase):
    def test_the_table_covers_the_whole_action_space(self):
        self.assertEqual(ACTION_COUNT, 19)
        self.assertEqual(sorted(ACTION_NAMES), list(range(ACTION_COUNT)))
        # Every action that presses the attack button, including the two vertical swings.
        self.assertEqual(sorted(hk_features.ATTACK_ACTIONS), [4, 6, 7, 8, 9, 14, 16])
        self.assertEqual(sorted(hk_features.ATTACK_ACTIONS),
                         sorted(a for a, name in ACTION_NAMES.items() if "attack" in name))

    def test_the_ids_match_the_controller(self):
        # The table above is documentation; ai_controller.py is what presses buttons.
        ids = {int(value) for value in re.findall(r"action_id == (\d+)", source_of("ai_controller.py"))}
        self.assertTrue(ids <= set(ACTION_NAMES), ids - set(ACTION_NAMES))


class AimingRule(unittest.TestCase):
    def test_only_the_plain_attack_is_aimed(self):
        right, left = 0.9, -0.9
        self.assertEqual(redirect_action(ACTION_ATTACK, right), ACTION_RIGHT_ATTACK)
        self.assertEqual(redirect_action(ACTION_ATTACK, left), ACTION_LEFT_ATTACK)
        self.assertEqual(redirect_action(ACTION_ATTACK, 0.0), ACTION_ATTACK)
        # These used to be rewritten to 8/9, which threw the extra button away.
        self.assertEqual(redirect_action(ACTION_JUMP_ATTACK, right), ACTION_JUMP_ATTACK)
        self.assertEqual(redirect_action(ACTION_DASH_ATTACK, right), ACTION_DASH_ATTACK)
        self.assertEqual(redirect_action(ACTION_LEFT_ATTACK, right), ACTION_LEFT_ATTACK)
        self.assertEqual(redirect_action(ACTION_RIGHT_ATTACK, left), ACTION_RIGHT_ATTACK)

    def test_non_attack_actions_are_untouched(self):
        others = sorted(a for a in ACTION_NAMES if a != ACTION_ATTACK)
        self.assertGreaterEqual(len(others), 18)
        for action in others:
            for dx in (-0.9, 0.0, 0.9):
                self.assertEqual(redirect_action(action, dx), action)

    def test_equality_with_the_threshold_does_not_aim(self):
        self.assertEqual(redirect_action(ACTION_ATTACK, AIM_THRESHOLD), ACTION_ATTACK)
        self.assertEqual(redirect_action(ACTION_ATTACK, -AIM_THRESHOLD), ACTION_ATTACK)
        self.assertEqual(redirect_action(ACTION_ATTACK, AIM_THRESHOLD + 0.01), ACTION_RIGHT_ATTACK)
        self.assertEqual(redirect_action(ACTION_ATTACK, -AIM_THRESHOLD - 0.01), ACTION_LEFT_ATTACK)


class BossStateClasses(unittest.TestCase):
    def test_the_fixture_is_populated(self):
        self.assertGreaterEqual(len(OBSERVED_STATES), 20)
        self.assertEqual(set(OBSERVED_STATES.values()),
                         {BOSS_STATE_IDLE, BOSS_STATE_WINDUP, BOSS_STATE_ATTACK,
                          BOSS_STATE_OPEN, BOSS_STATE_DEAD})

    def test_observed_states_are_classified(self):
        for state, expected in OBSERVED_STATES.items():
            self.assertEqual(classify_boss_state(state), expected, state)

    def test_unknown_and_empty_states_fall_back_to_idle(self):
        for state in ("", None, "Zote Mode", "   "):
            self.assertEqual(classify_boss_state(state), BOSS_STATE_IDLE, state)


class BossStateTrackerTests(unittest.TestCase):
    def test_the_age_grows_until_the_state_changes(self):
        tracker = BossStateTracker(phase_frames=10.0)
        self.assertEqual(tracker.update("Idle"), [0.0, 0.0, 0.0, 0.0, 0.0])
        self.assertEqual(tracker.update("Idle"), [0.0, 0.0, 0.0, 0.1, 0.0])
        self.assertEqual(tracker.update("Idle"), [0.0, 0.0, 0.0, 0.2, 0.0])
        self.assertEqual(tracker.update("JA Antic"), [1.0, 0.0, 0.0, 0.0, 1.0])
        self.assertEqual(tracker.update("JA Antic"), [1.0, 0.0, 0.0, 0.1, 0.0])

    def test_the_age_is_capped_at_one(self):
        tracker = BossStateTracker(phase_frames=5.0)
        features = [0.0]
        for _ in range(50):
            features = tracker.update("Idle")
        self.assertEqual(features[3], 1.0)

    def test_a_missing_frame_does_not_advance_the_state(self):
        tracker = BossStateTracker(phase_frames=10.0)
        tracker.update("Idle")
        tracker.update("Idle")
        cached = tracker.update(None)
        self.assertEqual(cached, [0.0, 0.0, 0.0, 0.1, 0.0])
        self.assertEqual(tracker.state, "Idle")
        self.assertEqual(tracker.update(None), cached)

    def test_open_dead_and_windup_are_reported(self):
        tracker = BossStateTracker()
        self.assertEqual(tracker.update("Opened")[1], 1.0)
        self.assertEqual(tracker.update("Death Anim Start")[2], 1.0)
        self.assertEqual(tracker.update("S Attack Antic")[0], 1.0)

    def test_reset_forgets_the_previous_fight(self):
        tracker = BossStateTracker()
        tracker.update("JA Antic")
        tracker.reset()
        self.assertEqual(tracker.state, None)
        # The first frame of an episode is the state the fight starts in, not a change.
        self.assertEqual(tracker.update("JA Antic"), [1.0, 0.0, 0.0, 0.0, 0.0])

    def test_the_feature_order_matches_the_observation(self):
        source = source_of("hk_gym.py")
        block = source[source.index("STAT_NAMES = ["):]
        block = block[:block.index("]")]
        names = re.findall(r'"([a-z_]+)"', block)
        self.assertGreaterEqual(len(names), 30, names)
        self.assertEqual(names[-len(BOSS_STATE_FEATURES):], list(BOSS_STATE_FEATURES))

        # The list above is only a promise until the vector itself is read: _get_obs is
        # what the policy sees, so it has to build exactly these names in this order.
        stats = source[source.index("stats = np.array([")::]
        stats = stats[stats.index("np.array([") + len("np.array([")::]
        stats = stats[:stats.index("]")]

        built = []
        without_comments = "\n".join(line.split("#", 1)[0] for line in stats.splitlines())
        for item in without_comments.split(","):
            item = item.strip()
            if not item:
                continue
            if item.startswith("*"):
                built.extend(BOSS_STATE_FEATURES)
            else:
                built.append(item)

        self.assertEqual(
            built, names, "the vector built in _get_obs does not match STAT_NAMES"
        )


if __name__ == "__main__":
    unittest.main()
