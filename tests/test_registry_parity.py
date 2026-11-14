# -*- coding: utf-8 -*-
"""Parity check between the mod's boss registry (C#) and its Python mirror (bosses.py).

bosses.py states that it mirrors BossRegistry in Mod/AiTrainHK/BossRegistry.cs, but nothing
enforced it: an entry could be added, renamed or lost on one side only, and Python would then
happily ask the mod to teleport into a scene it does not know (the mod answers
"boss not recognized" and the run stalls). This test reads the C# source directly, so it needs
neither a compiler nor the game - it is meant for CI.

Kept in sync with: BossRegistry, ExtraAliases, DEFAULT_BOSS_SCENE and DEFAULT_ENTRY_GATE.
"""
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import bosses  # noqa: E402

REGISTRY_SOURCE = os.path.join(ROOT, "Mod", "AiTrainHK", "BossRegistry.cs")

ENTRY_RE = re.compile(r'new\s+BossEntry\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)')
ALIAS_RE = re.compile(r'\{\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\}')
CONST_RE = re.compile(r'const\s+string\s+(\w+)\s*=\s*"([^"]*)"')


def read_mod_source():
    with open(REGISTRY_SOURCE, "r", encoding="utf-8") as handle:
        return handle.read()


def mod_registry(source):
    start = source.index("Entries = new BossEntry[]")
    end = source.index("};", start)
    return ENTRY_RE.findall(source[start:end])


def mod_aliases(source):
    start = source.index("ExtraAliases = new Dictionary<string, string>")
    end = source.index("};", start)
    return dict(ALIAS_RE.findall(source[start:end]))


def mod_constants(source):
    return dict(CONST_RE.findall(source))


class RegistryParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = read_mod_source()
        cls.registry = mod_registry(cls.source)
        cls.aliases = mod_aliases(cls.source)
        cls.constants = mod_constants(cls.source)

    def test_the_source_is_parsable(self):
        # If the mod's registry is ever reformatted, the regexes above stop matching and the
        # rest of this file would pass vacuously - fail loudly instead.
        self.assertGreater(len(self.registry), 0, "no new BossEntry(...) lines found in the mod")
        self.assertGreater(len(self.aliases), 0, "no ExtraAliases entries found in the mod")

    def test_counts_match(self):
        self.assertEqual(len(bosses.BOSS_LIST), len(self.registry),
                         "bosses.py and the mod's BossRegistry.Entries hold a different number of entries")

    def test_entries_match_in_order(self):
        self.assertEqual([tuple(entry) for entry in bosses.BOSS_LIST], self.registry,
                         "bosses.py has drifted from the mod's BossRegistry (order, scenes or labels)")

    def test_aliases_match(self):
        self.assertEqual(bosses._EXTRA_ALIASES, self.aliases,
                         "bosses.py aliases have drifted from ExtraAliases in the mod")

    def test_default_scene_matches(self):
        self.assertEqual(bosses.DEFAULT_SCENE, self.constants.get("DefaultBossScene"))

    def test_default_gate_matches(self):
        self.assertEqual(bosses.DEFAULT_GATE, self.constants.get("DefaultEntryGate"))


class RegistrySelfCheckProjectTest(unittest.TestCase):
    """The C# self-check (tests/csharp/RegistryTests) executes this registry in CI.

    It has to keep compiling the real registry as a link - not a copy - and it has to
    run on whatever .NET the machine has: pipe_sim and the mod set RollForward, and a
    self-check without it dies on a machine whose newest runtime is not net8.0 (the
    maintainer's own machine), which quietly removes the C# half of the parity proof.
    """

    def setUp(self):
        self.csproj = os.path.join(
            ROOT, "tests", "csharp", "RegistryTests", "RegistryTests.csproj")
        with open(self.csproj, "r", encoding="utf-8") as handle:
            self.project = handle.read()

    def test_it_executes_the_real_registry_as_a_link(self):
        self.assertIn('Include="..\\..\\..\\Mod\\AiTrainHK\\BossRegistry.cs"', self.project,
                      "RegistryTests stopped compiling the mod's own BossRegistry.cs")

    def test_it_runs_on_any_installed_runtime(self):
        self.assertIn("<RollForward>LatestMajor</RollForward>", self.project,
                      "RegistryTests.csproj lost RollForward - the self-check fails on "
                      "machines without the exact net8.0 runtime (CI only installs 8.0.x)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
