import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# The tool lives outside the package tree; put it on the path for the import.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "multibox"))


class MultiboxNamingTest(unittest.TestCase):
    """The multibox tool's naming conventions are load-bearing: the launcher,
    the mod's PIPE_SUFFIX handling and the training process must agree on
    them, so they get pinned here."""

    def setUp(self):
        self.mb = importlib.import_module("hk_multibox")

    def test_module_imports_with_the_stdlib_only(self):
        # Importable on any OS - CI (Linux) must not choke on it; the
        # Windows-only imports live inside the functions, not at module level.
        self.assertTrue(callable(self.mb.main))

    def test_instance_layout_names(self):
        with mock.patch.object(self.mb, "INSTANCES_ROOT", Path(r"X:\ wherever")):
            self.assertEqual(self.mb.instance_dir(3),
                             Path(r"X:\ wherever") / "HK_Instance3")
            self.assertEqual(
                self.mb.instance_exe(3),
                Path(r"X:\ wherever") / "HK_Instance3" / "hollow_knight_3.exe")

    def test_pipe_naming_matches_the_mod(self):
        # The mod serves pipe "hk_ai_mod" for an empty PIPE_SUFFIX and
        # "hk_ai_mod_<suffix>" otherwise; the launcher's per-window names
        # must follow the same rule.
        expected = {1: "hk_ai_mod", 2: "hk_ai_mod_2", 10: "hk_ai_mod_10"}
        for window, pipe in expected.items():
            suffix = "" if window == 1 else f"_{window}"
            self.assertEqual(self.mb.PIPE_BASE + suffix, pipe)

    def test_instances_root_default_is_the_games_volume(self):
        # Instance folders hold hardlinks to the game, so the root must be on
        # the same volume as the game; the default is the historical D: path.
        self.assertEqual(self.mb.INSTANCES_ROOT, self.mb.Path(r"D:\HKMultiInstance"))


if __name__ == "__main__":
    unittest.main()
