"""Units for `hk_run_config`: the fingerprint a checkpoint is only valid under.

`hk_run_config` imports nothing but the standard library, so it is exercised directly here.

What the checks stand for: a model saved under one reward scale, discount or vector size and
resumed under another is trained against a value function and normalization statistics that
describe a different problem. Nothing errors - the run simply looks as if it had learned nothing,
which is the most expensive kind of bug to diagnose. So the fingerprint is written next to the
model, compared before a resume, and the two places that consume it are checked here as source
(train.py cannot be imported in this tier).
"""

import json
import os
import re
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import hk_run_config  # noqa: E402

TRAIN_PY = os.environ.get("HK_TRAIN_PY", os.path.join(REPO, "train.py"))
GYM_PY = os.path.join(REPO, "hk_gym.py")


def read_source(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def function_body(source, name):
    """The body of a top-level `def name(...)`, or None."""
    match = re.search(r"^def %s\(" % re.escape(name), source, re.MULTILINE)
    if not match:
        return None
    following = re.search(r"\n\S", source[match.end() :])
    stop = match.end() + following.start() + 1 if following else len(source)
    return source[match.start() : stop]


class FingerprintTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = hk_run_config.config_path(self.directory.name)
        self.values = {"gamma": 0.9995, "damage_reward_per_hp": 15.0}

    def test_a_saved_fingerprint_matches_itself(self):
        hk_run_config.write_config(self.path, self.values)
        self.assertTrue(hk_run_config.matches(self.path, self.values))

    def test_the_order_of_the_values_does_not_matter(self):
        hk_run_config.write_config(self.path, self.values)
        reordered = dict(reversed(list(self.values.items())))
        self.assertTrue(hk_run_config.matches(self.path, reordered))

    def test_representation_noise_is_not_a_different_run(self):
        hk_run_config.write_config(self.path, self.values)
        noisy = dict(self.values, gamma=0.9995 + 1e-12)
        self.assertTrue(hk_run_config.matches(self.path, noisy))

    def test_a_changed_value_does_not_match(self):
        hk_run_config.write_config(self.path, self.values)
        changed = dict(self.values, gamma=0.995)
        self.assertFalse(hk_run_config.matches(self.path, changed))
        # The difference is what a human gets to read, so it has to name the value.
        self.assertEqual(
            hk_run_config.differences(self.path, changed),
            ["gamma: 0.9995 -> 0.995"],
        )

    def test_an_added_value_does_not_match(self):
        hk_run_config.write_config(self.path, self.values)
        extended = dict(self.values, action_count=19)
        self.assertFalse(hk_run_config.matches(self.path, extended))
        differences = hk_run_config.differences(self.path, extended)
        self.assertEqual(len(differences), 1, differences)
        self.assertIn("action_count", differences[0])

    def test_a_missing_or_unusable_file_does_not_match(self):
        self.assertFalse(hk_run_config.matches(self.path, self.values))
        self.assertEqual(
            hk_run_config.differences(self.path, self.values), ["no stored configuration"]
        )
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertFalse(hk_run_config.matches(self.path, self.values))
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(["gamma"], handle)
        self.assertFalse(
            hk_run_config.matches(self.path, self.values),
            "a fingerprint that is not an object cannot describe a run",
        )


class TrainingUsesTheFingerprintTest(unittest.TestCase):
    def setUp(self):
        self.train = read_source(TRAIN_PY)

    def test_every_reward_number_is_part_of_the_fingerprint(self):
        """A reward knob that is not fingerprinted is a stale resume waiting to happen."""
        gym = read_source(GYM_PY)
        # Every module-level numeric constant in hk_gym.py belongs to the reward economics (the
        # other numbers there are read from the environment, so they are not literals).
        names = sorted(set(re.findall(r"^([A-Z][A-Z0-9_]*)\s*=\s*[0-9]", gym, re.MULTILINE)))
        self.assertGreaterEqual(len(names), 6, "parsed only %r from hk_gym.py" % names)
        body = function_body(self.train, "run_config_values")
        self.assertIsNotNone(body, "run_config_values() is missing from train.py")
        for name in names:
            self.assertIn(
                name.lower(),
                body,
                "hk_gym.%s is not part of the checkpoint fingerprint" % name,
            )

    def test_the_fingerprint_is_checked_before_anything_is_resumed(self):
        self.assertIn("run_config.matches(", self.train)
        self.assertIn("load_compatible_vecnorm(base_vec_env", self.train)
        self.assertLess(
            self.train.index("run_config.matches("),
            self.train.index("PPO.load("),
            "the fingerprint has to be checked before the model is loaded",
        )
        self.assertLess(
            self.train.index("run_config.matches("),
            self.train.index("load_compatible_vecnorm(base_vec_env"),
            "the fingerprint has to be checked before the statistics are loaded",
        )
        # The statistics are tied to the same problem as the value function: they must be
        # skipped together, not merely because the vector size changed.
        self.assertIn("resume=resume_allowed", self.train)
        self.assertIn("run_config.write_config(", self.train)


if __name__ == "__main__":
    unittest.main()
