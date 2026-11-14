"""The deterministic evaluation probe (train.py) and its separation from training.

train.py cannot be imported here (torch, sb3, the game stack), so the probe's
behaviour inside SB3 is not executable in this tier - what is pinned here is the
wiring that keeps the probe's episodes OUT of the training statistics and its
own numbers IN the metrics: one shared env, flagged steps, frozen normalization.
A break in any of those would silently mix the exploration policy's luck into
the eval/win_rate (or the probe's fights into the training win rate), which is
the exact confusion the probe exists to end.
"""
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

TRAIN_PY = os.path.join(REPO, "train.py")
GYM_PY = os.path.join(REPO, "hk_gym.py")


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def method_body(source, name):
    """The body of `def name(...)` at any indent, or None."""
    match = re.search(r"^    def %s\(|^def %s\(" % (re.escape(name), re.escape(name)),
                      source, re.MULTILINE)
    if not match:
        return None
    following = re.search(r"\n    def |\n    @|\nclass |(?<!\n    )\ndef ", source[match.end():])
    stop = match.end() + following.start() if following else len(source)
    return source[match.start():stop]


class EvalProbeWiringTest(unittest.TestCase):
    def setUp(self):
        self.train = read(TRAIN_PY)
        self.gym = read(GYM_PY)

    def test_the_sources_are_parsable(self):
        for needle in ("class DeterministicEvalCallback", "def build_callbacks",
                       "set_eval_mode"):
            self.assertIn(needle, self.train,
                          "train.py no longer contains %r" % needle)
        self.assertIn("set_eval_mode", self.gym,
                      "hk_gym.py lost the eval-mode flag")

    def test_the_probe_runs_in_the_shared_callback_list(self):
        body = method_body(self.train, "build_callbacks")
        self.assertIsNotNone(body, "build_callbacks() is missing from train.py")
        self.assertIn("DeterministicEvalCallback(", body,
                      "the probe is not part of the shared callback list - variant B "
                      "would train without evaluation")
        # The probe must come after the pause callback (the game is resumed first)
        # and before the journal callback (which dumps the metrics table).
        self.assertLess(body.index("GamePauseCallback("),
                        body.index("DeterministicEvalCallback("))
        self.assertLess(body.index("DeterministicEvalCallback("),
                        body.index("ProgressFileCallback("))

    def test_the_probe_freezes_the_normalization_statistics(self):
        body = method_body(self.train, "_run_eval")
        self.assertIsNotNone(body, "_run_eval() is missing from train.py")
        self.assertIn("env.training = False", body,
                      "the probe lets the eval fights bend the running normalization")
        self.assertIn("env.training = True", body,
                      "the probe never restores the normalization training flag")

    def test_training_callbacks_skip_eval_episodes(self):
        # All three consumers of reward_parts/episode infos must ignore the probe.
        for name in ("_on_step",):
            win_rate = method_body(self.train, name)
        reward_cb = method_body(self.train, "_on_step")
        self.assertIsNotNone(reward_cb)
        self.assertIn("eval_episode", reward_cb,
                      "RewardComponentLoggingCallback counts the probe's rewards")
        win_rate_body = re.search(
            r"class WinRateLoggingCallback.*?(?=\nclass )", self.train, re.DOTALL)
        self.assertIsNotNone(win_rate_body)
        self.assertIn("eval_episode", win_rate_body.group(0),
                      "WinRateLoggingCallback counts the probe's episodes")
        progress_body = re.search(
            r"class ProgressFileCallback.*?(?=\nclass |\Z)", self.train, re.DOTALL)
        self.assertIsNotNone(progress_body)
        self.assertIn("eval_episode", progress_body.group(0),
                      "ProgressFileCallback journals the probe's episodes")

    def test_the_env_flags_eval_steps(self):
        self.assertIsNotNone(method_body(self.gym, "set_eval_mode"),
                             "hk_gym.py has no set_eval_mode to receive the probe")
        step = method_body(self.gym, "step")
        self.assertIsNotNone(step)
        self.assertIn('"eval_episode"', step,
                      "step() does not mark its infos - the probe's fights would "
                      "land in the training statistics")

    def test_the_outcome_rule_is_the_shared_one(self):
        body = method_body(self.train, "_eval_episode")
        self.assertIsNotNone(body, "_eval_episode() is missing from train.py")
        self.assertIn("classify_outcome(", body,
                      "the probe classifies outcomes by its own rule - the journal, "
                      "the training win rate and eval/win_rate could disagree")


if __name__ == "__main__":
    unittest.main()
