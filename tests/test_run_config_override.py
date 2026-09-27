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
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hk_run_config import RESUME_OVERRIDE_ENV, build_config, matches, override_requested  # noqa: E402

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
        # across the change and then starts from scratch anyway.
        self.assertRegex(found.group(1), r"resume_allowed = True",
                         "the override says it is resuming but never allows the resume")


if __name__ == "__main__":
    unittest.main()
