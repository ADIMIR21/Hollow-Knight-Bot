"""The run manifest and the trainer's log file.

The manifest (hk_run_config.write_manifest) records where a checkpoint came
from - code and library versions, the hyperparameters actually in effect - and
must never leak into the resume fingerprint (hk_run_config.matches): a different
commit does not make a value function wrong the way a changed reward scale does.
The trainer's logger (hk_logging) mirrors its lines into logs/train*.log with
timestamps; the console format stays byte-for-byte what print() produced.
"""
import json
import logging
import os
import re
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import hk_logging  # noqa: E402
import hk_run_config as run_config  # noqa: E402
from progress_log import trainer_log_path  # noqa: E402


class RunManifestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, run_config.MANIFEST_NAME)
        self.addCleanup(self._tmp.cleanup)

    def test_the_manifest_records_what_is_given_and_stamps_it(self):
        run_config.write_manifest(self.path, {"source": "train.py", "sb3": "2.3.2"})
        with open(self.path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["source"], "train.py")
        self.assertEqual(record["sb3"], "2.3.2")
        self.assertIn("written", record, "the manifest does not say when it was written")

    def test_non_json_values_survive_through_str(self):
        # net_arch lists are fine, but a schedule callable or a dict of policy kwargs
        # must not crash the write (default=str).
        run_config.write_manifest(self.path, {"hyperparameters": {"policy_kwargs": dict(net_arch=[256, 256])}})
        with open(self.path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertIn("net_arch", record["hyperparameters"]["policy_kwargs"])

    def test_the_manifest_is_not_the_fingerprint(self):
        # The two files live side by side under distinct names: a manifest write must
        # never overwrite run_config.json, and matches() must keep reading the config.
        config_path = run_config.config_path(self._tmp.name)
        run_config.write_config(config_path, {"gamma": 0.9995})
        run_config.write_manifest(
            run_config.config_path(self._tmp.name, run_config.MANIFEST_NAME),
            {"gamma": "poisoned"})
        self.assertTrue(run_config.matches(config_path, {"gamma": 0.9995}))

    def test_git_commit_is_a_hash_or_none(self):
        # Inside this checkout git is expected to answer; outside a repo (or without
        # git) the answer is None, which the manifest records as null.
        commit = run_config.git_commit(REPO)
        self.assertTrue(
            commit is None or re.fullmatch(r"[0-9a-f]{7,40}", commit),
            "git_commit returned %r - neither a hash nor None" % commit)


class TrainerLogPathTest(unittest.TestCase):
    def test_no_tag_uses_the_plain_name(self):
        self.assertEqual(trainer_log_path(), "logs/train.log")

    def test_a_tag_namespaces_the_log(self):
        self.assertEqual(trainer_log_path("w2"), "logs/train__w2.log")
        self.assertEqual(trainer_log_path("smoke"), "logs/train__smoke.log")


class SetupLoggingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log_path = os.path.join(self._tmp.name, "train.log")
        logger = logging.getLogger(hk_logging.LOGGER_NAME)
        # The test process may have configured it for another test: start clean.
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger._hk_configured = False

    def tearDown(self):
        logger = logging.getLogger(hk_logging.LOGGER_NAME)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        logger._hk_configured = False

    def test_lines_reach_the_file_with_a_timestamp(self):
        hk_logging.setup_logging(self.log_path)
        hk_logging.get_logger().info("[SYSTEM] hello file")
        with open(self.log_path, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("[SYSTEM] hello file", content)
        self.assertRegex(content, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(,\d+)? ")

    def test_setup_is_idempotent(self):
        hk_logging.setup_logging(self.log_path)
        hk_logging.setup_logging(self.log_path)
        hk_logging.get_logger().info("only once")
        with open(self.log_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read().count("only once"), 1)

    def test_the_console_format_is_the_old_print_format(self):
        handler = hk_logging.setup_logging(self.log_path).handlers[0]
        self.assertEqual(handler.format(logging.makeLogRecord({"msg": "plain text",
                                                               "levelname": "INFO"})),
                         "plain text",
                         "the console output changed shape - scripts and eyes expect "
                         "the print() look")


if __name__ == "__main__":
    unittest.main()
