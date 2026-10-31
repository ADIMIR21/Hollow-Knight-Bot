"""Unit tests for the multi-window orchestrator's pure parts (tools/multibox/train_multi.py).

The module must import without torch/sb3/gym: CI compiles it but never runs
training, and the orchestrator itself only pulls the heavy stack inside
main()/the worker factory. These tests pin the plan grammar, the derived
defaults and the per-window command/env-var builders.
"""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "multibox"))

import train_multi  # noqa: E402


class ImportPurityTest(unittest.TestCase):
    def test_the_module_imports_without_the_training_stack(self):
        # The orchestrator's pure parts must stay stdlib-only: torch/sb3/cv2
        # are not installed in CI, and a module-level import would break it.
        self.assertFalse(any(name.startswith(("torch", "stable_baselines3", "cv2", "mss"))
                             for name in sys.modules))

    def test_train_py_helpers_are_reachable_from_the_tools_dir(self):
        # train_multi inserts the repo root into sys.path at import; the plan
        # doc's helpers (progress_log) must be importable from there.
        import progress_log  # noqa: F401


class DefaultDerivationTest(unittest.TestCase):
    def test_window_one_uses_the_production_pipe_name(self):
        self.assertEqual(train_multi._default_pipe(1), "hk_ai_mod")

    def test_higher_windows_use_the_suffixed_pipe(self):
        self.assertEqual(train_multi._default_pipe(2), "hk_ai_mod_2")
        self.assertEqual(train_multi._default_pipe(10), "hk_ai_mod_10")

    def test_save_slots_cycle_so_two_windows_never_share_one_by_default(self):
        slots = [train_multi._default_slot(w) for w in (1, 2, 3, 4, 5, 6)]
        self.assertEqual(slots, [2, 3, 4, 1, 2, 3])
        self.assertEqual(len(set(slots[:4])), 4)


class ParsePlanTest(unittest.TestCase):
    def _plan(self, data):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(data, handle)
        handle.close()
        self.addCleanup(os.remove, handle.name)
        return handle.name

    def test_full_plan_passes_through(self):
        path = self._plan([
            {"window": 1, "pipe": "hk_ai_mod", "save_slot": 2},
            {"window": 2, "pipe": "hk_ai_mod_2", "save_slot": 3},
        ])
        configs = train_multi.parse_plan(path)
        self.assertEqual(configs, [
            {"window": 1, "pipe": "hk_ai_mod", "save_slot": 2},
            {"window": 2, "pipe": "hk_ai_mod_2", "save_slot": 3},
        ])

    def test_defaults_are_derived_from_the_window_number(self):
        path = self._plan([{"window": 2}, {"window": 3}])
        configs = train_multi.parse_plan(path)
        self.assertEqual(configs[0]["pipe"], "hk_ai_mod_2")
        self.assertEqual(configs[0]["save_slot"], 3)
        self.assertEqual(configs[1]["pipe"], "hk_ai_mod_3")
        self.assertEqual(configs[1]["save_slot"], 4)

    def test_an_explicit_pipe_overrides_the_default(self):
        path = self._plan([{"window": 5, "pipe": "hk_ai_mod_custom"}])
        self.assertEqual(train_multi.parse_plan(path)[0]["pipe"], "hk_ai_mod_custom")

    def test_repeated_windows_are_refused(self):
        with self.assertRaises(SystemExit):
            train_multi.parse_plan(self._plan([{"window": 1}, {"window": 1}]))

    def test_repeated_pipes_are_refused(self):
        with self.assertRaises(SystemExit):
            train_multi.parse_plan(self._plan([
                {"window": 1, "pipe": "hk_ai_mod_x"},
                {"window": 2, "pipe": "hk_ai_mod_x"},
            ]))

    def test_a_slot_out_of_range_is_refused(self):
        for slot in (0, 5, -1):
            with self.assertRaises(SystemExit):
                train_multi.parse_plan(self._plan([{"window": 1, "save_slot": slot}]))

    def test_a_non_positive_window_is_refused(self):
        with self.assertRaises(SystemExit):
            train_multi.parse_plan(self._plan([{"window": 0}]))

    def test_a_bool_window_is_refused_not_treated_as_one(self):
        with self.assertRaises(SystemExit):
            train_multi.parse_plan(self._plan([{"window": True}]))

    def test_an_empty_plan_is_refused(self):
        with self.assertRaises(SystemExit):
            train_multi.parse_plan(self._plan([]))

    def test_a_non_list_plan_is_refused(self):
        with self.assertRaises(SystemExit):
            train_multi.parse_plan(self._plan({"window": 1}))

    def test_a_broken_json_is_reported_as_a_system_exit(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        handle.write("{not json")
        handle.close()
        self.addCleanup(os.remove, handle.name)
        with self.assertRaises(SystemExit):
            train_multi.parse_plan(handle.name)


class WorkerEnvVarsTest(unittest.TestCase):
    def test_a_worker_runs_under_its_own_pipe_slot_and_scene(self):
        cfg = {"window": 3, "pipe": "hk_ai_mod_3", "save_slot": 4}
        env = train_multi.worker_env_vars(cfg, "GG_Hornet_1", "door_dreamEnter")
        self.assertEqual(env, {
            "HK_PIPE_NAME": "hk_ai_mod_3",
            "HK_SAVE_SLOT": "4",
            "HK_BOSS_SCENE": "GG_Hornet_1",
            "HK_ENTRY_GATE": "door_dreamEnter",
        })


class TrainCommandTest(unittest.TestCase):
    def test_variant_a_spawns_one_namespaced_train_py_per_window(self):
        cfg = {"window": 2, "pipe": "hk_ai_mod_2", "save_slot": 3}
        cmd = train_multi.train_command(cfg, "GG_Hornet_1", "fast", tag="w2")
        self.assertEqual(cmd[0], sys.executable)
        self.assertTrue(cmd[1].endswith("train.py"))
        self.assertIn("--pipe", cmd)
        self.assertEqual(cmd[cmd.index("--pipe") + 1], "hk_ai_mod_2")
        self.assertEqual(cmd[cmd.index("--save-slot") + 1], "3")
        self.assertEqual(cmd[cmd.index("--tag") + 1], "w2")
        self.assertEqual(cmd[cmd.index("--boss") + 1], "GG_Hornet_1")
        self.assertEqual(cmd[cmd.index("--timescale") + 1], "fast")


class SmokeTagTest(unittest.TestCase):
    def test_the_smoke_run_is_namespaced_away_from_real_checkpoints(self):
        # The smoke folder must never collide with the shared run folder.
        from progress_log import boss_dir_name, journal_path
        self.assertNotEqual(boss_dir_name("GG_Hornet_1", None), boss_dir_name("GG_Hornet_1", "smoke"))
        self.assertNotEqual(journal_path(None), journal_path("smoke"))


if __name__ == "__main__":
    unittest.main()
