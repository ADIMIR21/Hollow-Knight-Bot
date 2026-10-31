"""Unit tests for the save-slot sync tool (tools/multibox/sync_saves.py).

Parallel windows must boot from the same hero: the tool copies one slot's
user<N>.dat over the others with backups. The slot-to-file mapping is the
game's own (Platform.GetSaveSlotFileName formats the index straight into
"user{0}.dat", IL probe) - the tests pin it, the refusals that protect the
user's saves, and the backup behavior.
"""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "multibox"))

import sync_saves  # noqa: E402


class SlotPathTest(unittest.TestCase):
    def test_the_slot_index_maps_straight_into_the_file_name(self):
        self.assertTrue(sync_saves.slot_path(2).endswith("user2.dat"))
        self.assertTrue(sync_saves.slot_path(4).endswith("user4.dat"))

    def test_a_slot_out_of_range_is_refused(self):
        for slot in (0, 5, -1):
            with self.assertRaises(ValueError):
                sync_saves.slot_path(slot)


class ParseSlotsTest(unittest.TestCase):
    def test_a_comma_list_is_parsed(self):
        self.assertEqual(sync_saves.parse_slots("1,3,4"), [1, 3, 4])
        self.assertEqual(sync_saves.parse_slots("2"), [2])
        self.assertEqual(sync_saves.parse_slots(" 1 , 4 "), [1, 4])

    def test_garbage_is_refused(self):
        with self.assertRaises(SystemExit):
            sync_saves.parse_slots("one,two")

    def test_out_of_range_slots_are_refused(self):
        with self.assertRaises(SystemExit):
            sync_saves.parse_slots("0,2")
        with self.assertRaises(SystemExit):
            sync_saves.parse_slots("2,5")


class SyncSlotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.save_dir = self.tmp.name
        with open(sync_saves.slot_path(2, self.save_dir), "wb") as handle:
            handle.write(b"main hero save")

    def _sync(self, source, targets, game_running=False):
        return sync_saves.sync_slots(source, targets, save_dir=self.save_dir,
                                     game_running=lambda: game_running)

    def test_the_source_is_copied_into_every_target(self):
        self._sync(2, [1, 3, 4])
        for slot in (1, 3, 4):
            with open(sync_saves.slot_path(slot, self.save_dir), "rb") as handle:
                self.assertEqual(handle.read(), b"main hero save")

    def test_an_overwritten_target_is_backed_up_first(self):
        with open(sync_saves.slot_path(1, self.save_dir), "wb") as handle:
            handle.write(b"old slot 1 content")
        self._sync(2, [1])
        backups = [f for f in os.listdir(self.save_dir) if f.startswith("user1.dat.bak-")]
        self.assertEqual(len(backups), 1, "the overwritten save must leave a timestamped backup")
        with open(os.path.join(self.save_dir, backups[0]), "rb") as handle:
            self.assertEqual(handle.read(), b"old slot 1 content")

    def test_an_empty_target_is_created_without_a_backup(self):
        result = self._sync(2, [4])
        self.assertEqual(result, [(4, None)])
        self.assertFalse(any(f.startswith("user4.dat.bak-") for f in os.listdir(self.save_dir)))

    def test_the_source_is_untouched(self):
        self._sync(2, [1, 3])
        with open(sync_saves.slot_path(2, self.save_dir), "rb") as handle:
            self.assertEqual(handle.read(), b"main hero save")

    def test_the_source_must_not_be_its_own_target(self):
        with self.assertRaises(SystemExit):
            self._sync(2, [2, 3])

    def test_duplicate_targets_are_refused(self):
        with self.assertRaises(SystemExit):
            self._sync(2, [1, 1])

    def test_a_missing_source_is_refused(self):
        with self.assertRaises(SystemExit):
            self._sync(3, [1])

    def test_a_running_game_is_refused(self):
        with self.assertRaises(SystemExit):
            self._sync(2, [1, 3], game_running=True)
        # and nothing was written
        self.assertFalse(os.path.exists(sync_saves.slot_path(1, self.save_dir)))


if __name__ == "__main__":
    unittest.main()
