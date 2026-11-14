"""The one deliberate way past the configuration fingerprint.

A mismatch normally sends the run back to scratch, because a value function learned under a
different reward is worse than no value function at all. The escape hatch exists for the opposite
decision - a maintainer who has just changed one reward constant and would rather keep the policy
than restart it - so it has to require an explicit word, and the code that uses it has to say what
it is accepting.
"""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hk_run_config import (REWARD_OVERRIDES_ENV, RESUME_OVERRIDE_ENV,  # noqa: E402
                           apply_reward_overrides, build_config, matches, override_requested)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ResumeOverrideTest(unittest.TestCase):
    def test_it_is_off_unless_asked_for(self):
        self.assertFalse(override_requested({}))
        self.assertFalse(override_requested({RESUME_OVERRIDE_ENV: ""}))
        self.assertFalse(override_requested({RESUME_OVERRIDE_ENV: "   "}))
        self.assertFalse(override_requested({RESUME_OVERRIDE_ENV: "0"}))
        self.assertFalse(override_requested({RESUME_OVERRIDE_ENV: "no"}))
        self.assertFalse(override_requested({RESUME_OVERRIDE_ENV: "maybe"}))

    def test_an_affirmative_value_turns_it_on(self):
        for value in ("1", "true", "TRUE", " yes ", "on"):
            self.assertTrue(override_requested({RESUME_OVERRIDE_ENV: value}), value)

    def test_the_default_refusal_is_untouched(self):
        # The override is a decision made by the caller, not a change to the comparison itself.
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "run_config.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"gamma": 0.9995}')
            self.assertFalse(matches(path, {"gamma": 0.9995, "victory_mask_bonus": 400.0}))
            self.assertTrue(matches(path, {"gamma": 0.9995}))
            self.assertEqual(build_config({"gamma": 0.9995}), {"gamma": 0.9995})

    def test_the_trainer_actually_asks(self):
        source = open(os.path.join(ROOT, "train.py"), encoding="utf-8").read()
        found = re.search(r"resume_allowed = run_config\.matches\([^)]*\)(.*?)\n\s*\n", source, re.S)
        self.assertIsNotNone(found, "train.py no longer computes resume_allowed from run_config")
        self.assertIn("override_requested", found.group(1),
                      "the override exists but the trainer never consults it")
        # Announcing the resume is not resuming: without this the run prints that it is resuming
        # across the change and then starts from scratch anyway. The branch may say it either
        # by assignment (resume_allowed = True) or by returning straight away (return True).
        self.assertRegex(found.group(1), r"(resume_allowed = True|return True)",
                         "the override says it is resuming but never allows the resume")


class RewardOverridesTest(unittest.TestCase):
    """The sweep bench's A/B reward overrides, applied to a module's globals."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, content, name="overrides.json"):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_unset_variable_is_a_no_op(self):
        values = {"HEALTH_PENALTY_PER_MASK": 200.0}
        self.assertEqual(apply_reward_overrides(values, environ={}), [])
        self.assertEqual(values, {"HEALTH_PENALTY_PER_MASK": 200.0})

    def test_overrides_replace_the_named_constants(self):
        path = self._write('{"HEALTH_PENALTY_PER_MASK": 400, "OPEN_WINDOW_DAMAGE_MULTIPLIER": 6}')
        values = {"HEALTH_PENALTY_PER_MASK": 200.0, "OPEN_WINDOW_DAMAGE_MULTIPLIER": 3.0}
        applied = apply_reward_overrides(values, path=path)
        self.assertEqual(applied, ["HEALTH_PENALTY_PER_MASK", "OPEN_WINDOW_DAMAGE_MULTIPLIER"])
        self.assertEqual(values["HEALTH_PENALTY_PER_MASK"], 400.0)
        self.assertEqual(values["OPEN_WINDOW_DAMAGE_MULTIPLIER"], 6.0)

    def test_the_environment_variable_selects_the_file(self):
        path = self._write('{"VICTORY_REWARD": 500}')
        values = {"VICTORY_REWARD": 1000.0}
        apply_reward_overrides(values, environ={REWARD_OVERRIDES_ENV: path})
        self.assertEqual(values["VICTORY_REWARD"], 500.0)

    def test_a_lowercase_key_is_refused(self):
        path = self._write('{"health_penalty_per_mask": 400}')
        with self.assertRaises(SystemExit):
            apply_reward_overrides({}, path=path)

    def test_a_non_number_value_is_refused(self):
        path = self._write('{"VICTORY_REWARD": "big"}')
        with self.assertRaises(SystemExit):
            apply_reward_overrides({}, path=path)
        path = self._write('{"VICTORY_REWARD": true}')
        with self.assertRaises(SystemExit):
            apply_reward_overrides({}, path=path)

    def test_a_non_object_file_is_refused(self):
        path = self._write("[1, 2, 3]")
        with self.assertRaises(SystemExit):
            apply_reward_overrides({}, path=path)

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(SystemExit):
            apply_reward_overrides({}, path=os.path.join(self._tmp.name, "gone.json"))


if __name__ == "__main__":
    unittest.main()
