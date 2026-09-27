"""The journal parser has to find real records, not silently nothing.

A regex that stops matching would leave the plots empty with no error at all, so the fixture below
carries every record shape verbatim - a metrics table whose group sits on its own row, an episode
line and a step-limit window - and the test asserts what came back out of it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from progress_log import parse_journal, run_start, series  # noqa: E402

TABLE = """[2026-09-27 17:41:10] EPISODE #64 step=347195 outcome=death reward=7223.80 len=2324 | wins=3/64 win_rate(100)=0.047 death=0.953 timeout=0.000
-----------------------------------------
| rollout/                |             |
|    ep_rew_mean          | 1.47e+03    |
|    ep_len_mean          | 2.38e+03    |
| train/                  |             |
|    entropy_loss         | -2.9        |
|    explained_variance   | 0.941       |
| custom/                 |             |
|    victories            | 3           |
|    win_rate             | 0.047       |
-----------------------------------------
"""

EPISODES = """[2026-09-27 17:41:57] EPISODE #65 step=351289 outcome=victory reward=4105.35 len=1093 | wins=4/65 win_rate(100)=0.062 death=0.938 timeout=0.000
[2026-09-27 16:39:16] WINDOW step=30937 reward=-670.05 len=3001 | step limit reached, the fight continues
"""


class ProgressJournalTest(unittest.TestCase):
    def setUp(self):
        self.blocks, self.episodes = parse_journal(TABLE + EPISODES)
        self.assertGreater(len(self.blocks), 0, "the metrics table did not parse at all")
        self.assertGreater(len(self.episodes), 0, "no fight records parsed at all")

    def test_metrics_are_group_qualified(self):
        values = self.blocks[0][1]
        self.assertEqual(values["rollout/ep_rew_mean"], 1470.0)
        self.assertEqual(values["rollout/ep_len_mean"], 2380.0)
        self.assertEqual(values["train/entropy_loss"], -2.9)
        self.assertEqual(values["train/explained_variance"], 0.941)
        self.assertEqual(values["custom/victories"], 3.0)
        # A bare key must not survive: "victories" alone is ambiguous across the groups.
        self.assertNotIn("victories", values)

    def test_a_block_is_timestamped_by_the_line_before_it(self):
        self.assertEqual(self.blocks[0][0], "2026-09-27 17:41:10")

    def test_fights_carry_outcome_reward_and_length(self):
        fights = [(number, outcome, reward, length) for _, number, outcome, reward, length
                  in self.episodes]
        self.assertIn((65, "victory", 4105.35, 1093), fights)
        self.assertIn((64, "death", 7223.80, 2324), fights)
        # A step-limit window is a stretch of play too, so it is reported rather than dropped.
        self.assertIn((0, "window", -670.05, 3001), fights)

    def test_series_reads_one_metric_across_blocks(self):
        self.assertEqual(series(self.blocks, "custom/victories"), [("2026-09-27 17:41:10", 3.0)])
        self.assertEqual(series(self.blocks, "custom/not_there"), [])

    def test_an_empty_journal_parses_to_nothing(self):
        self.assertEqual(parse_journal(""), ([], []))
        self.assertEqual(parse_journal("just some text\n"), ([], []))


class RunBoundaryTest(unittest.TestCase):
    def test_the_newest_run_starts_at_its_first_episode(self):
        _, episodes = parse_journal(
            "[2026-09-27 15:00:00] EPISODE #41 step=100 outcome=death reward=1.0 len=10\n"
            "[2026-09-27 15:10:00] EPISODE #1 step=1 outcome=death reward=2.0 len=20\n"
            "[2026-09-27 15:20:00] EPISODE #2 step=2 outcome=victory reward=3.0 len=30\n")
        self.assertEqual(run_start(episodes), 1)
        self.assertEqual([number for _, number, _, _, _ in episodes[run_start(episodes):]], [1, 2])


if __name__ == "__main__":
    unittest.main()
