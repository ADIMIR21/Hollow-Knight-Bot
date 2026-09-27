"""Invariants of the PPO configuration in `train.py`.

`train.py` cannot be imported here: it pulls in torch, vgamepad and the gym
environment, and CI installs none of them (`tests/` is standard library only).
So, like `tests/test_registry_parity.py` does for the mod's C#, the file is read
as source and its numbers are checked against the invariants that keep a night of
training from being wasted.

Every check below stands for one way the learning signal was lost in the
26-27.09 overnight run (550 episodes, 96 victories, 2 033 664 steps):

* an update that covers less than one fight learns from a single outcome, so
  every gradient carries that fight's luck with it;
* a discount factor that looks only ~100 steps ahead turns the real victory and
  death rewards into effectively zero, leaving only the dense shaping;
* a learning rate that decays to zero before a run ends keeps the clock running
  while the policy is frozen;
* a resumed model is configured by the values pickled inside its file, so the
  load path has to repeat whatever a fresh model was given.
"""

import os
import re
import unittest

# HK_TRAIN_PY points the checks at another copy of the file: that is how the
# red case is reproduced (against a pristine or a perturbed train.py) without
# having to modify the tree.
TRAIN_PY = os.environ.get(
    "HK_TRAIN_PY",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "train.py"),
)

# Measured on that run: `rollout/ep_len_mean` around 1950 steps and ~75 steps per
# second, so one False Knight fight is 13-26 seconds of real play.
FIGHT_STEPS = 1950
STEPS_PER_SECOND = 75.0


def read_source():
    with open(TRAIN_PY, "r", encoding="utf-8") as handle:
        return handle.read()


def module_number(source, name):
    """The value of a module-level `NAME = <number>` assignment, or None."""
    pattern = r"^%s\s*=\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\s*$" % re.escape(name)
    match = re.search(pattern, source, re.MULTILINE)
    return float(match.group(1)) if match else None


def call_text(source, call, start=0):
    """The source of a call expression, including its nested parentheses."""
    opening = source.find(call, start)
    if opening < 0:
        return None
    index = opening + len(call) - 1  # the position of the opening parenthesis
    depth = 0
    while index < len(source):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                return source[opening : index + 1]
        index += 1
    return None


def function_body(source, name):
    """The body of a top-level `def name(...)`, or None."""
    match = re.search(r"^def %s\(" % re.escape(name), source, re.MULTILINE)
    if not match:
        return None
    following = re.search(r"\n\S", source[match.end() :])
    stop = match.end() + following.start() + 1 if following else len(source)
    return source[match.start() : stop]


class TrainingConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.source = read_source()

    def test_the_source_is_parsable(self):
        """A regex that silently stops matching must fail, not skip."""
        for needle in ("def make_model(", "PPO(", "PPO.load(", "def constant_lr("):
            self.assertTrue(
                needle in self.source, "train.py no longer contains %r" % needle
            )
        self.assertIsNotNone(
            call_text(self.source, "make_model"),
            "the make_model() call could not be located in train.py",
        )
        self.assertIsNotNone(
            call_text(self.source, "PPO.load("),
            "the PPO.load() call could not be located in train.py",
        )

    def test_the_hyperparameters_are_named_constants(self):
        for name in ("N_STEPS", "BATCH_SIZE", "GAMMA", "LEARNING_RATE"):
            self.assertIsNotNone(
                module_number(self.source, name),
                "%s is not a module-level number in train.py" % name,
            )

    def test_an_update_covers_more_than_one_fight(self):
        n_steps = module_number(self.source, "N_STEPS")
        self.assertIsNotNone(n_steps, "N_STEPS is missing from train.py")
        self.assertGreaterEqual(
            n_steps,
            2 * FIGHT_STEPS,
            "N_STEPS=%s covers less than two fights (~%d steps each): an update "
            "built from a single fight learns from that fight's luck"
            % (n_steps, FIGHT_STEPS),
        )
        batch_size = module_number(self.source, "BATCH_SIZE")
        self.assertIsNotNone(batch_size, "BATCH_SIZE is missing from train.py")
        self.assertLessEqual(
            batch_size,
            n_steps / 4,
            "BATCH_SIZE=%s is not a fraction of N_STEPS=%s" % (batch_size, n_steps),
        )

    def test_the_discount_keeps_a_useful_horizon(self):
        gamma = module_number(self.source, "GAMMA")
        self.assertIsNotNone(gamma, "GAMMA is missing from train.py")
        horizon = 1.0 / (1.0 - gamma) if gamma < 1.0 else float("inf")
        self.assertGreaterEqual(
            gamma,
            0.995,
            "GAMMA=%s looks %.0f steps (%.1f s) ahead, while a fight lasts %.0f "
            "steps (%.1f s), so the victory and death rewards are discounted to "
            "nothing" % (gamma, horizon, horizon / STEPS_PER_SECOND, FIGHT_STEPS,
                         FIGHT_STEPS / STEPS_PER_SECOND),
        )
        self.assertLess(gamma, 1.0, "GAMMA must discount, or the critic is unbounded")

    def test_the_learning_rate_does_not_decay_to_zero(self):
        self.assertFalse(
            re.search(r"learning_rate\s*=\s*linear_schedule", self.source),
            "a schedule is scaled to the current learn() call, so it always ends "
            "at zero - that freezes the policy for the last hours of a night",
        )
        body = function_body(self.source, "constant_lr")
        self.assertIsNotNone(body, "constant_lr() is missing from train.py")
        after_signature = body.split(")", 1)[1] if ")" in body else body
        self.assertNotIn(
            "progress_remaining",
            after_signature,
            "constant_lr() must ignore progress_remaining, or the rate decays again",
        )
        self.assertIn("return", body, "constant_lr() does not return a rate")

    def test_a_fresh_model_uses_the_constants(self):
        self.assertIsNotNone(
            call_text(self.source, "make_model"), "make_model() is missing"
        )
        call = call_text(self.source, "PPO(", self.source.find("def make_model("))
        self.assertIsNotNone(call, "make_model() no longer builds a PPO")
        for binding in (
            "n_steps=N_STEPS",
            "batch_size=BATCH_SIZE",
            "gamma=GAMMA",
            "learning_rate=constant_lr",
        ):
            self.assertTrue(
                binding in call,
                "a fresh model must use the configuration constants: %s" % binding,
            )

    def test_a_resumed_model_uses_the_same_constants(self):
        call = call_text(self.source, "PPO.load(")
        self.assertIsNotNone(call, "PPO.load() is missing from train.py")
        # Stable-Baselines3 applies the load kwargs after the pickled data, so
        # these are what a resumed run really trains with.
        for binding in ("n_steps=N_STEPS", "batch_size=BATCH_SIZE", "gamma=GAMMA"):
            self.assertTrue(
                binding in call,
                "PPO.load() must override %s, otherwise a loaded model silently "
                "keeps the values from its file" % binding,
            )
        self.assertTrue(
            "constant_lr" in call,
            "PPO.load() must replace the pickled learning rate schedule",
        )

    def test_the_reward_normalization_discounts_like_the_policy(self):
        fresh = call_text(
            self.source, "VecNormalize(", self.source.find("def fresh_vecnorm(")
        )
        self.assertIsNotNone(fresh, "fresh_vecnorm() no longer builds a VecNormalize")
        self.assertTrue(
            "gamma=GAMMA" in fresh,
            "the reward normalization must discount like the policy",
        )
        loaded = function_body(self.source, "load_compatible_vecnorm")
        self.assertIsNotNone(loaded, "load_compatible_vecnorm() is missing from train.py")
        self.assertTrue(
            "loaded.gamma = GAMMA" in loaded,
            "statistics restored from a file must be discounted like the policy too",
        )


if __name__ == "__main__":
    unittest.main()
