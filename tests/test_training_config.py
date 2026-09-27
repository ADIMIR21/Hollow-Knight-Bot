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

# hk_gym.py cannot be imported here (numpy, torch, the gamepad), but the episode cap it
# enforces is what the worst case looks like: an update that covers two of those fights
# sees more than one fight even when every episode runs into the cap.
# HK_GYM_PY does the same job as HK_TRAIN_PY below: it points the checks at another copy of the
# file, which is how the red case is reproduced without touching the tree.
GYM_PY = os.environ.get(
    "HK_GYM_PY",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hk_gym.py"),
)
README_MD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md"
)


def read_source():
    with open(TRAIN_PY, "r", encoding="utf-8") as handle:
        return handle.read()


def read_gym_source():
    with open(GYM_PY, "r", encoding="utf-8") as handle:
        return handle.read()


def episode_step_cap(source):
    """The step cap hk_gym.py puts on one episode, or None.

    The cap is a named constant (it is part of the reward economics), so the name is resolved
    against the same source instead of assuming a literal.
    """
    match = re.search(r"episode_step\s*>\s*([A-Za-z_][A-Za-z0-9_]*)", source)
    if match:
        return module_number(source, match.group(1))
    match = re.search(r"episode_step\s*>\s*([0-9]+)", source)
    return int(match.group(1)) if match else None


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


def method_body(source, name):
    """The body of an indented method `def name(...)`, or None."""
    match = re.search(r"^    def %s\(" % re.escape(name), source, re.MULTILINE)
    if not match:
        return None
    following = re.search(r"\n    def |\nclass ", source[match.end() :])
    stop = match.end() + following.start() if following else len(source)
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

        # The mean fight is the easy case: an episode that runs into the cap is longer.
        cap = episode_step_cap(read_gym_source())
        self.assertIsNotNone(cap, "the episode step cap could not be read from hk_gym.py")
        self.assertGreaterEqual(
            n_steps,
            2 * cap,
            "N_STEPS=%s covers less than two capped episodes (%s steps each): an update "
            "collected from a single fight learns from that fight's luck"
            % (n_steps, cap),
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

        # "Long enough to look long" is not the point: the horizon has to reach the end of a
        # fight. One step is one fresh game frame, so at 0.995 a victory 1950 steps away was
        # weighted 0.995**1950 = 5.6e-05 - nothing in float32 - and the policy could only learn
        # the dense shaping (damage and health), which is exactly what "it trades hits instead
        # of winning" looked like from the outside.
        weight_at_the_end = gamma ** FIGHT_STEPS
        self.assertGreaterEqual(
            weight_at_the_end,
            0.25,
            "GAMMA=%s keeps %.2e of the weight of a win %d steps (one fight) away: the victory "
            "and death rewards are invisible, so only the shaping is learned"
            % (gamma, weight_at_the_end, FIGHT_STEPS),
        )
        # The same has to hold for the longest fight the cap allows, or the end of a slow fight
        # is invisible even if the mean fight is not.
        cap = episode_step_cap(read_gym_source())
        self.assertIsNotNone(cap, "the episode step cap could not be read from hk_gym.py")
        weight_at_the_cap = gamma ** cap
        self.assertGreaterEqual(
            weight_at_the_cap,
            0.05,
            "GAMMA=%s keeps %.2e of the weight of the end of a capped episode (%s steps)"
            % (gamma, weight_at_the_cap, cap),
        )

    def test_the_learning_rate_does_not_decay_to_zero(self):
        # Every binding site is checked, not one spelling of one schedule: a schedule is
        # scaled to the current learn() call, so it always ends at zero and freezes the
        # policy for the last hours of a night, whatever the schedule is called.
        bindings = re.findall(r"learning_rate\s*=\s*([A-Za-z_][A-Za-z0-9_.]*)", self.source)
        self.assertTrue(
            bindings, "train.py has no learning_rate= binding to check"
        )
        for binding in bindings:
            self.assertEqual(
                binding,
                "constant_lr",
                "learning_rate=%s: only the constant schedule is allowed, a decaying one "
                "ends at zero" % binding,
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


class RewardEconomicsTest(unittest.TestCase):
    """The reward has to describe the fight, not merely pay for damage.

    hk_gym.py cannot be imported here either (numpy, cv2, the gamepad), so its reward numbers are
    read as source and checked against the balance they are meant to produce - the balance is
    what decides whether the policy plays the fight or trades health for damage, and getting it
    wrong does not show up as an error, only as a bot that tanks everything.
    """

    # Observed in live telemetry: Godhome's False Knight has 260 hit points and the knight
    # carries 9 masks (the probe recorded boss_hp 260 with hp/max_hp 9).
    BOSS_HP = 260
    KNIGHT_MASKS = 9

    def setUp(self):
        self.gym = read_gym_source()

    def reward_numbers(self):
        numbers = {}
        for name in (
            "DAMAGE_REWARD_PER_HP",
            "HEALTH_PENALTY_PER_MASK",
            "VICTORY_REWARD",
            "DEATH_PENALTY",
            "STEP_PENALTY",
            "EPISODE_STEP_LIMIT",
        ):
            value = module_number(self.gym, name)
            self.assertIsNotNone(
                value, "%s is not a module-level number in hk_gym.py" % name
            )
            numbers[name] = value
        return numbers

    def test_the_potential_is_built_from_the_named_numbers(self):
        body = method_body(self.gym, "_potential")
        self.assertIsNotNone(body, "_potential() is missing from hk_gym.py")
        self.assertIn("DAMAGE_REWARD_PER_HP", body)
        self.assertIn("HEALTH_PENALTY_PER_MASK", body)
        # A literal next to the names is how the two drift apart: the constant block would stop
        # describing what is actually paid.
        literal = re.search(r"\b\d+\.\d+\s*\*", body)
        self.assertIsNone(
            literal, "the potential still scales by a literal: %r" % body.strip()
        )

    def test_the_rewards_are_paid_from_the_named_numbers(self):
        body = method_body(self.gym, "step")
        self.assertIsNotNone(body, "step() is missing from hk_gym.py")
        # assertTrue, not assertIn: a failure must name the missing needle, not print the whole
        # of step().
        # The win is paid through hk_features.victory_bonus, which also scales it by the masks still
        # standing, so what has to be in step() is the call carrying the named constant.
        for needle in ("victory_bonus(VICTORY_REWARD", "reward -= DEATH_PENALTY", "reward -= STEP_PENALTY"):
            self.assertTrue(needle in body, "step() does not pay %r" % needle)
        # A literal beside the names is how the block stops describing what is actually paid:
        # the victory bonus was still added as 1000.0 while the block named VICTORY_REWARD, so
        # changing the constant would have changed nothing.
        literal = re.search(r"reward[^=\n]*[+-]= [0-9]", body)
        self.assertIsNone(literal, "step() still pays a literal reward")

    def test_tanking_costs_more_than_the_hit_it_buys(self):
        numbers = self.reward_numbers()
        one_mask = numbers["HEALTH_PENALTY_PER_MASK"]
        # Five per cent of the boss's bar, expressed in the units the damage reward pays in.
        five_percent = 0.05 * self.BOSS_HP * numbers["DAMAGE_REWARD_PER_HP"]
        self.assertGreaterEqual(
            one_mask,
            five_percent,
            "a mask costs %.1f, i.e. %.1f boss hit points, so standing inside an attack to land "
            "one more hit is the better trade - that is the bot that tanks everything"
            % (one_mask, one_mask / numbers["DAMAGE_REWARD_PER_HP"]),
        )

    def test_attacking_is_worth_more_than_not_attacking(self):
        numbers = self.reward_numbers()
        passive = -numbers["STEP_PENALTY"] * numbers["EPISODE_STEP_LIMIT"]
        trade = (
            numbers["DAMAGE_REWARD_PER_HP"] * self.BOSS_HP
            - numbers["DEATH_PENALTY"]
            - numbers["HEALTH_PENALTY_PER_MASK"] * self.KNIGHT_MASKS
        )
        self.assertGreater(
            trade,
            passive,
            "trading a full kill for a death pays %.1f while refusing to engage pays %.1f: a "
            "reward that punishes dying harder than it pays for winning teaches the policy to "
            "run away instead" % (trade, passive),
        )

    def test_a_full_kill_out_pays_the_whole_health_bar(self):
        numbers = self.reward_numbers()
        kill = numbers["DAMAGE_REWARD_PER_HP"] * self.BOSS_HP
        health = numbers["HEALTH_PENALTY_PER_MASK"] * self.KNIGHT_MASKS
        self.assertGreater(
            kill,
            health,
            "killing the boss pays %.1f while losing every mask costs %.1f: the policy is "
            "better off protecting its health than winning" % (kill, health),
        )

    def test_the_readme_names_the_discount_it_ships_with(self):
        # The PPO table is what a human reads before a run; it has to name the value in the tree.
        with open(README_MD, "r", encoding="utf-8") as handle:
            readme = handle.read()
        match = re.search(r"\|\s*gamma\s*\|\s*([0-9.]+)", readme)
        self.assertIsNotNone(match, "the README's gamma row could not be read")
        self.assertEqual(
            float(match.group(1)),
            module_number(read_source(), "GAMMA"),
            "the README documents a different discount than train.py uses",
        )


class GamePauseCallbackTests(unittest.TestCase):
    """Update 9: the game is frozen while the PPO update runs.

    Same reason as the rest of this file: the game keeps running while learn()
    computes the gradient epochs, so the hero used to stand still for seconds and
    the boss kept hitting it.
    """

    def setUp(self):
        self.source = read_source()

    def test_the_callback_exists_and_freezes_the_game(self):
        self.assertIn("class GamePauseCallback(BaseCallback):", self.source)
        start = self.source.index("class GamePauseCallback(BaseCallback):")
        body = self.source[start:self.source.index("class WinRateLoggingCallback", start)]
        self.assertIn('env_method("pause_game")', body)
        self.assertIn('env_method("resume_game")', body)
        # BaseCallback declares _on_step abstract: a subclass without it cannot be
        # constructed at all, which happens before the first frame is collected.
        self.assertIn("def _on_step", body)
        self.assertIn("def _on_rollout_end", body)
        self.assertIn("def _on_rollout_start", body)

    def test_it_is_in_the_callback_list(self):
        callback_list = call_text(self.source, "CallbackList(")
        self.assertIsNotNone(callback_list)
        self.assertIn("game_pause_callback", callback_list)

    def test_a_crash_unfreezes_the_game(self):
        # Ctrl+C lands inside the update, and _on_training_end never runs then.
        finally_block = self.source[self.source.rindex("finally:"):]
        self.assertIn("game_pause_callback.resume()", finally_block)


if __name__ == "__main__":
    unittest.main()
