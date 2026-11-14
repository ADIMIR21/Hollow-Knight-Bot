"""The sweep bench (tools/sweep): config grammar, isolation and the comparison report.

The bench's whole value is trustworthy comparisons: an experiment must be
bounded, isolated under its own tag and recorded the same way as the next one.
These tests pin the config grammar, the isolation mechanics (tags, plan,
override env) and the report aggregation - all stdlib-only, no trainer process.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for name in ("tools", "tools/sweep", "tools/multibox"):
    sys.path.insert(0, os.path.join(REPO_ROOT, name))
sys.path.insert(0, REPO_ROOT)

import run_experiment  # noqa: E402
import report  # noqa: E402


def write_json(folder, name, payload):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


class ParseExperimentTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _parse(self, payload):
        return run_experiment.parse_experiment(write_json(self._tmp.name, "exp.json", payload))

    def test_defaults(self):
        exp = self._parse({"name": "baseline"})
        self.assertEqual(exp["tag"], "exp-baseline")
        self.assertEqual(exp["boss"], "GG_False_Knight")
        self.assertEqual(exp["timescale"], "fast")
        self.assertEqual(exp["steps"], run_experiment.DEFAULT_STEPS)
        self.assertEqual(exp["variant"], "b")
        self.assertEqual(exp["windows"], run_experiment.DEFAULT_WINDOWS)
        self.assertEqual(exp["eval_episodes"], 3)
        self.assertEqual(exp["eval_every"], 20)
        self.assertEqual(exp["reward"], {})
        self.assertEqual(exp["ppo"], {})

    def test_a_bad_name_is_refused(self):
        for name in ("", "Bad Name", "has_underscore", "-leading", "x" * 100):
            with self.assertRaises(SystemExit, msg=name):
                self._parse({"name": name})

    def test_bad_steps_variant_windows_are_refused(self):
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "steps": -5})
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "steps": True})
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "variant": "c"})
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "windows": 0})
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "windows": 99})
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "variant": "a", "windows": 2})

    def test_bad_reward_and_ppo_values_are_refused(self):
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "reward": {"lowercase": 1}})
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "reward": {"HEALTH_PENALTY_PER_MASK": "big"}})
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "ppo": {"unknown_knob": 0.1}})
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "ppo": {"ent_coef": False}})
        with self.assertRaises(SystemExit):
            self._parse({"name": "a", "eval_every": 0})

    def test_broken_json_is_reported(self):
        path = os.path.join(self._tmp.name, "broken.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(SystemExit):
            run_experiment.parse_experiment(path)


class IsolationTest(unittest.TestCase):
    """An experiment must land under its own tag and never touch the default layout."""

    def test_the_plan_uses_the_launcher_derived_defaults(self):
        self.assertEqual(run_experiment.plan_json(3),
                         [{"window": 1}, {"window": 2}, {"window": 3}])

    def test_the_override_env_only_carries_what_was_set(self):
        exp = run_experiment.parse_experiment.__wrapped__ if False else None  # noqa: F841
        exp = {"reward": {"VICTORY_REWARD": 500.0}, "ppo": {},
               "name": "x", "tag": "exp-x"}
        with tempfile.TemporaryDirectory() as folder:
            overrides = Path(folder) / "reward_overrides.json"
            overrides.write_text('{"VICTORY_REWARD": 500.0}', encoding="utf-8")
            env = run_experiment.experiment_env(exp, overrides, environ={"HK_RUN_TAG": "stale"})
            self.assertEqual(env["HK_REWARD_OVERRIDES"], str(overrides))
            self.assertNotIn("HK_PPO_ENT_COEF", env)

    def test_an_override_free_experiment_stays_byte_identical_to_the_default(self):
        exp = {"reward": {}, "ppo": {}, "name": "x", "tag": "exp-x"}
        env = run_experiment.experiment_env(
            exp, None, environ={"HK_REWARD_OVERRIDES": "/leftover/from/another/run"})
        self.assertNotIn("HK_REWARD_OVERRIDES", env,
                         "a leftover override from the shell would poison a clean experiment")

    def test_the_ppo_knobs_reach_the_trainer_env(self):
        exp = {"reward": {}, "ppo": {"ent_coef": 0.05}, "name": "x", "tag": "exp-x"}
        env = run_experiment.experiment_env(exp, None, environ={})
        self.assertEqual(env["HK_PPO_ENT_COEF"], "0.05")

    def test_prepare_work_dir_writes_the_files(self):
        with tempfile.TemporaryDirectory() as folder:
            exp = {"name": "ab-test", "tag": "exp-ab-test", "reward": {"STEP_PENALTY": 0.1},
                   "ppo": {}, "variant": "b", "windows": 2}
            paths = run_experiment.prepare_work_dir(exp, repo_root=folder)
            overrides = json.loads(Path(paths["overrides"]).read_text(encoding="utf-8"))
            self.assertEqual(overrides, {"STEP_PENALTY": 0.1})
            plan = json.loads(Path(paths["plan"]).read_text(encoding="utf-8"))
            self.assertEqual(plan, [{"window": 1}, {"window": 2}])

    def test_experiment_is_new_checks_folder_and_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            exp = {"name": "fresh", "tag": "exp-fresh", "boss": "GG_False_Knight"}
            self.assertTrue(run_experiment.experiment_is_new(exp, repo_root=folder))
            models = Path(folder) / "models" / "ppo_hk" / "GG_False_Knight__exp-fresh"
            models.mkdir(parents=True)
            self.assertFalse(run_experiment.experiment_is_new(exp, repo_root=folder))

    def test_the_runner_command_carries_tag_budget_and_no_launch(self):
        exp = {"name": "x", "tag": "exp-x", "boss": "GG_Hornet_1", "timescale": "fast",
               "steps": 200000, "eval_episodes": 2, "eval_every": 4,
               "variant": "b", "_no_launch": True}
        cmd = run_experiment.runner_command(exp, "plan.json", repo_root=REPO_ROOT)
        self.assertTrue(cmd[1].endswith("train_multi.py"))
        self.assertEqual(cmd[cmd.index("--tag") + 1], "exp-x")
        self.assertEqual(cmd[cmd.index("--steps") + 1], "200000")
        self.assertEqual(cmd[cmd.index("--eval-every") + 1], "4")
        self.assertIn("--no-launch", cmd)

    def test_the_multiwindow_tag_namespaces_variant_a_trainers(self):
        self.assertEqual(run_experiment.__name__ and True, True)  # import sanity
        import train_multi
        self.assertEqual(train_multi._window_tag(None, 2), "w2")
        self.assertEqual(train_multi._window_tag("exp-x", 2), "exp-x-w2")


JOURNAL = """[2026-11-14 01:00:00] EPISODE #1 step=8000 outcome=death reward=-320.00 len=1800 | wins=0/1
[2026-11-14 01:05:00] WINDOW step=9000 reward=12.00 len=1000 | step limit reached, the fight continues
[2026-11-14 01:10:00] EPISODE #2 step=12000 outcome=victory reward=2900.00 len=1500 | wins=1/2
[2026-11-14 01:15:00] EPISODE #3 step=16000 outcome=death reward=-100.00 len=1600 | wins=1/3
[2026-11-14 01:20:00] EPISODE #4 step=21000 outcome=victory reward=3100.00 len=1400 | wins=2/4
-----------------------------------------
| rollout/                |             |
|    ep_len_mean          | 1575        |
| eval/                   |             |
|    win_rate             | 0.333       |
|    mean_reward          | 1200        |
"""


class SummarizeJournalTest(unittest.TestCase):
    def test_the_decision_numbers(self):
        summary = report.summarize_journal(JOURNAL)
        self.assertEqual(summary["episodes"], 4)
        self.assertEqual(summary["victories"], 2)
        self.assertAlmostEqual(summary["train_win_rate"], 0.5)
        self.assertEqual(summary["deaths"], 2)
        self.assertEqual(summary["timeouts"], 0)
        self.assertEqual(summary["step_limit_windows"], 1)
        self.assertEqual(summary["first_win_step"], 12000)
        self.assertAlmostEqual(summary["eval_win_rate"], 0.333)
        self.assertAlmostEqual(summary["ep_len_mean"], 1575)

    def test_an_empty_journal_is_all_none(self):
        summary = report.summarize_journal("")
        self.assertEqual(summary["episodes"], 0)
        self.assertIsNone(summary["train_win_rate"])
        self.assertIsNone(summary["first_win_step"])
        self.assertIsNone(summary["eval_win_rate"])


class CollectTest(unittest.TestCase):
    def test_experiments_are_found_by_their_tag_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for tag in ("exp-mask400", "exp-baseline"):
                boss_dir = root / "models" / "ppo_hk" / f"GG_False_Knight__{tag}"
                boss_dir.mkdir(parents=True)
                (boss_dir / "run_config.json").write_text(
                    json.dumps({"health_penalty_per_mask": 400.0, "gamma": 0.9995}),
                    encoding="utf-8")
                (boss_dir / "run_manifest.json").write_text(
                    json.dumps({"git_commit": "abc1234",
                                "hyperparameters": {"ent_coef": 0.05}}),
                    encoding="utf-8")
            logs = root / "logs"
            logs.mkdir()
            (logs / "progress__exp-mask400.txt").write_text(JOURNAL, encoding="utf-8")
            # exp-baseline has no journal: it must be reported as an error, not dropped.

            rows = report.collect("GG_False_Knight", repo_root=folder)
            self.assertEqual([row["tag"] for row in rows], ["exp-baseline", "exp-mask400"])
            by_tag = {row["tag"]: row for row in rows}
            self.assertEqual(by_tag["exp-mask400"]["reward"]["health_penalty_per_mask"], 400.0)
            self.assertEqual(by_tag["exp-mask400"]["ent_coef"], 0.05)
            self.assertEqual(by_tag["exp-mask400"]["git_commit"], "abc1234")
            self.assertEqual(by_tag["exp-mask400"]["victories"], 2)
            self.assertEqual(by_tag["exp-baseline"]["episodes"], 0,
                             "an experiment without a journal reports zeros, not silence")
            self.assertIn("no journal", by_tag["exp-baseline"]["error"])

    def test_the_plain_boss_folder_shows_only_with_all(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "models" / "ppo_hk" / "GG_False_Knight").mkdir(parents=True)
            (root / "models" / "ppo_hk" / "GG_False_Knight__exp-x").mkdir(parents=True)
            self.assertEqual([row["tag"] for row in report.collect("GG_False_Knight", repo_root=folder)],
                             ["exp-x"])
            rows = report.collect("GG_False_Knight", repo_root=folder, include_baseline=True)
            self.assertIn("(baseline)", [row["tag"] for row in rows])

    def test_the_table_names_the_columns(self):
        text = report.build_table([])
        self.assertIn("No experiments", text)
        rows = [{"tag": "exp-x", "reward": {}, "episodes": 0, "victories": 0,
                 "train_win_rate": None, "eval_win_rate": None, "first_win_step": None,
                 "ep_len_mean": None, "ent_coef": None, "git_commit": None, "error": None}]
        table = report.build_table(rows)
        self.assertIn("eval_win", table)
        self.assertIn("exp-x", table)


if __name__ == "__main__":
    unittest.main()
