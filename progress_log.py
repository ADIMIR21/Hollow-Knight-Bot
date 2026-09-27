"""Reading the training journal.

`train.py` appends two kinds of record to `logs/progress.txt`. Metrics arrive as a table of
`| key | value |` rows, with the group name on a row of its own (`| rollout/ |`) - so a key is only
complete once the current group is known, and `ep_rew_mean` has to become `rollout/ep_rew_mean`.
Fights arrive as one line per episode or step-limit window, carrying the outcome, the reward and
the length.

This module is pure: no numpy, no matplotlib, no torch. The plotting side owns the heavy imports.
"""

import re

_GROUP = re.compile(r"^\|\s+([\w./]+/)\s+\|")
_ROW = re.compile(r"^\|\s+(\S+)\s+\|\s+(-?[\d.eE+-]+)\s+\|")
_SEPARATOR = re.compile(r"^-{5,}\s*$")
_STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
_EPISODE = re.compile(
    r"^\[([\d\- :]+)\]\s+EPISODE #(\d+)\s+step=(\d+)\s+outcome=(\w+)\s+reward=(-?[\d.]+)\s+len=(\d+)")
_WINDOW = re.compile(r"^\[([\d\- :]+)\]\s+WINDOW step=(\d+)\s+reward=(-?[\d.]+)\s+len=(\d+)")


def parse_journal(text):
    """Splits the journal into (blocks, episodes).

    A block is one metrics table: ``(timestamp, {full/key: float})``. An episode is one fight:
    ``(timestamp, number, outcome, reward, length)``. Step-limit windows are returned as episodes
    too, with outcome ``"window"`` and the number they were logged under, because they are the same
    kind of event - a stretch of play with an end, a reward and a length.
    """
    blocks = []
    episodes = []
    current = {}
    group = ""
    stamp = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        found = _STAMP.match(line.strip())
        if found:
            stamp = found.group(1)

        episode = _EPISODE.match(line.strip()) or _WINDOW.match(line.strip())
        if episode:
            parts = episode.groups()
            if len(parts) == 6:
                episodes.append((parts[0], int(parts[1]), parts[3], float(parts[4]), int(parts[5])))
            else:
                episodes.append((parts[0], 0, "window", float(parts[2]), int(parts[3])))
            continue

        header = _GROUP.match(line)
        if header:
            group = header.group(1)
            continue

        row = _ROW.match(line)
        if row:
            key = row.group(1)
            current[key if "/" in key else group + key] = float(row.group(2))
            continue

        if _SEPARATOR.match(line.strip()) and current:
            blocks.append((stamp, current))
            current = {}
            group = ""

    if current:
        blocks.append((stamp, current))
    return blocks, episodes


def series(blocks, key):
    """The values of one metric across the blocks, skipping tables that do not carry it."""
    return [(stamp, values[key]) for stamp, values in blocks if key in values]


def run_starts(episodes):
    """Every index where a run begins, oldest first.

    A run is delimited by its episode counter falling back to 1, which is what a fresh `train.py`
    does. Index 0 always counts: the journal may open in the middle of a run.
    """
    starts = [0]
    for index, (_, number, _, _, _) in enumerate(episodes):
        if number == 1 and index > 0:
            starts.append(index)
    return starts


def run_start(episodes):
    """Index of the first episode of the newest run.

    A run is delimited by its episode counter falling back to 1, which is what a fresh
    `train.py` does; without the split, an older run's long flat tail hides the newest one.
    """
    start = 0
    for index, (_, number, _, _, _) in enumerate(episodes):
        if number == 1 and index > 0:
            start = index
    return start
