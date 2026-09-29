"""The mod's action ids must mean what the Python action table says they mean.

The buttons now travel over the pipe as a bare index. Python owns the table the policy chooses
from; the mod owns the mapping from that index onto the hero's InControl actions. Nothing at
runtime compares the two, and a drift would not raise an error anywhere - it would just make the
bot play a different move than the policy asked for, which is close to impossible to spot in a
training curve. So the mirror is checked here: the mod's conditions are read out of its source
and compared with the names of the Python constants.

A parse that quietly stops matching must fail rather than pass, so the checks assert the parse
found something before comparing anything.
"""

import re
import unittest
from pathlib import Path

import hk_features

MOD_SOURCE = Path(__file__).resolve().parents[1] / "Mod" / "AiTrainHK" / "AiDataExporter.cs"

# What each word of a Python action name asks the hero to do. "cast" is the spell button, which is
# what the bot holds to heal, and InControl calls that action "focus" in this game.
WORDS = {
    "LEFT": "left",
    "RIGHT": "right",
    "UP": "up",
    "DOWN": "down",
    "JUMP": "jump",
    "ATTACK": "attack",
    "DASH": "dash",
    "CAST": "focus",
}

# A line of the mod's ApplyHeldAction, e.g.
#     Commit(a.jump,   id == 3 || id == 6 || id == 10, tick);
COMMIT_LINE = re.compile(r"Commit(?:Axis)?\(a\.(\w+),\s*(.+?),\s*tick\);")
ID_TERM = re.compile(r"id\s*==\s*(\d+)")


def mod_mapping():
    """The ids the mod presses for each hero action, read straight out of its source."""
    source = MOD_SOURCE.read_text(encoding="utf-8", errors="replace")
    mapping = {}
    for line in source.splitlines():
        match = COMMIT_LINE.search(line)
        if not match:
            continue
        action, conditions = match.group(1), match.group(2)
        mapping[action] = {int(value) for value in ID_TERM.findall(conditions)}
    return mapping


def python_expectation():
    """The hero actions each id should press, taken from the names of the Python constants."""
    names = getattr(hk_features, "ACTION_NAMES", None)
    if not isinstance(names, dict) or not names:
        raise AssertionError("hk_features.ACTION_NAMES is gone or empty; the ids have no names")

    expectation = {}
    for first, second in names.items():
        # ACTION_NAMES is an id -> name table, but a name may also be a key in it. Only the id keys
        # say anything about which button an id presses.
        try:
            action_id = int(first)
        except (TypeError, ValueError):
            continue
        name = str(second).upper()
        if name in ("NONE", ""):
            expectation[action_id] = set()
            continue
        actions = set()
        for word in re.split(r"[+_]", name):
            if word in WORDS:
                actions.add(WORDS[word])
        expectation[action_id] = actions
    return expectation


class ActionWireTest(unittest.TestCase):
    def test_the_mod_source_is_parsable(self):
        mapping = mod_mapping()
        self.assertTrue(mapping, "no Commit(a.<action>, ...) lines found in the mod")
        for action in ("jump", "attack", "dash", "focus", "left", "right", "up", "down"):
            self.assertIn(action, mapping, f"the mod never presses {action}")

    def test_every_id_the_policy_can_choose_is_reachable(self):
        mapping = mod_mapping()
        pressed = set().union(*mapping.values()) if mapping else set()
        expectation = python_expectation()
        missing = sorted(set(expectation) - pressed - {0})
        self.assertEqual(missing, [], f"ids the mod cannot press at all: {missing}")
        self.assertEqual(
            len(expectation),
            hk_features.ACTION_COUNT,
            "the named table and the action count disagree",
        )

    def test_the_mod_presses_what_the_python_names_say(self):
        mapping = mod_mapping()
        for action_id, wanted in sorted(python_expectation().items()):
            if action_id == 0:
                continue
            got = {action for action, ids in mapping.items() if action_id in ids}
            self.assertEqual(
                got,
                wanted,
                f"id {action_id} presses {sorted(got) or 'nothing'}, "
                f"the Python name asks for {sorted(wanted) or 'nothing'}",
            )

    def test_no_id_is_pressed_by_an_action_it_does_not_name(self):
        """The reverse direction: a hero action must not fire for ids that never ask for it."""
        mapping = mod_mapping()
        for action, ids in sorted(mapping.items()):
            for action_id in sorted(ids):
                wanted = python_expectation().get(action_id)
                self.assertIsNotNone(wanted, f"the mod presses {action} for unknown id {action_id}")
                self.assertIn(
                    action,
                    wanted,
                    f"id {action_id} ({hk_features.ACTION_NAMES.get(action_id)}) also presses {action}",
                )


if __name__ == "__main__":
    unittest.main()
