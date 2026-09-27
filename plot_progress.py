"""Plot the training journal: is the policy getting better, and in which way?

Reads `logs/progress.txt` (the record shape is documented in `progress_log.py`) and draws one
figure whose panels answer the questions that matter while a run is live:

* rewards - the running episode reward plus every fight's own reward, with the count of wins on a
  second axis, because a rising reward can also come from a reward change rather than from skill;
* outcomes - the win rate against the share of fights that ended in death, so a run that stops
  winning but keeps dying is visible at a glance;
* fight length - how long a fight lasts, which tends to fall as the policy learns to go straight
  for the kill;
* optimiser health - entropy (is it still exploring, or has it locked onto one line) and explained
  variance (does the value function know the fight).

The journal is appended across runs and the episode counter restarts at 1, so only the newest run
is drawn by default; `--all` shows everything.

    python plot_progress.py [--all] [--out logs/progress.png] [--show]
"""

import argparse
import datetime
import os
import sys

from progress_log import parse_journal, run_start, run_starts, series

JOURNAL = os.path.join("logs", "progress.txt")


def _stamps(pairs):
    """Timestamps to datetimes, so gaps in a run (a restart) read as gaps rather than as strides."""
    out = []
    for stamp, value in pairs:
        try:
            out.append((datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S"), value))
        except ValueError:
            continue
    return out


def _times(values):
    return [when for when, _ in values]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--journal", default=JOURNAL)
    parser.add_argument("--out", default=os.path.join("logs", "progress.png"))
    parser.add_argument("--all", action="store_true", help="draw every run in the journal")
    parser.add_argument("--runs", type=int, default=1,
                        help="how many of the newest runs to draw (default 1)")
    parser.add_argument("--hours", type=float, default=None,
                        help="trim the drawn runs to the last N hours")
    parser.add_argument("--show", action="store_true", help="open a window as well as saving")
    args = parser.parse_args()

    with open(args.journal, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    blocks, episodes = parse_journal(text)
    if not blocks:
        print("no metrics tables in %s - nothing to draw" % args.journal)
        return 1

    if not args.all and episodes:
        # A run starts where the episode counter restarts. The newest one is drawn by default;
        # --runs takes in more of them, which is what a stretch of restarts needs - and --hours
        # then trims whatever was selected, so it can only ever cut, never widen.
        starts = run_starts(episodes)
        first_index = starts[max(0, len(starts) - max(1, args.runs))] if starts else 0
        first = episodes[first_index][0]
        if args.hours is not None:
            newest = max(stamp for stamp, _ in blocks if stamp)
            cutoff = (datetime.datetime.strptime(newest, "%Y-%m-%d %H:%M:%S")
                      - datetime.timedelta(hours=args.hours)).strftime("%Y-%m-%d %H:%M:%S")
            first = max(first, cutoff)
        blocks = [(stamp, values) for stamp, values in blocks if stamp >= first]
        episodes = [fight for fight in episodes if fight[0] >= first]

    import matplotlib
    if not args.show:
        matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    wins = []
    seen = 0
    for when, _, outcome, _, _ in episodes:
        if outcome == "victory":
            seen += 1
        wins.append((when, seen))
    fight_rewards = [(when, reward) for when, _, outcome, reward, _ in episodes]
    fight_lengths = [(when, length) for when, _, outcome, length, _ in episodes
                     if outcome != "window"]

    figure, axes = plt.subplots(4, 1, figsize=(12, 13), sharex=True)
    figure.suptitle("Hollow Knight PPO - %s" % ("all runs" if args.all else "newest run"))

    top = axes[0]
    reward_line = _stamps(series(blocks, "rollout/ep_rew_mean"))
    if reward_line:
        top.plot(_times(reward_line), [v for _, v in reward_line], color="tab:blue", lw=2,
                 label="ep_rew_mean")
    fights = _stamps(fight_rewards)
    if fights:
        top.plot(_times(fights), [v for _, v in fights], color="tab:blue", alpha=0.25, lw=0.8,
                 label="fight reward")
    top.set_ylabel("reward")
    top.grid(alpha=0.3)
    win_axis = top.twinx()
    if wins:
        win_axis.step(_times(_stamps(wins)), [v for _, v in wins], color="tab:green", lw=2,
                      where="post", label="wins (cumulative)")
    win_axis.set_ylabel("wins")
    handles = top.get_lines() + win_axis.get_lines()
    top.legend(handles, [h.get_label() for h in handles], loc="upper left", fontsize=9)

    outcomes = axes[1]
    for key, colour, label in (("custom/win_rate", "tab:green", "win rate (last 100)"),
                               ("custom/last100_death", "tab:red", "death share (last 100)"),
                               ("custom/last100_timeout", "tab:orange", "timeout share (last 100)")):
        values = _stamps(series(blocks, key))
        if values:
            outcomes.plot(_times(values), [v for _, v in values], color=colour, lw=1.8, label=label)
    outcomes.set_ylabel("share")
    outcomes.set_ylim(-0.02, 1.02)
    outcomes.grid(alpha=0.3)
    outcomes.legend(loc="center left", fontsize=9)

    lengths = axes[2]
    values = _stamps(series(blocks, "rollout/ep_len_mean"))
    if values:
        lengths.plot(_times(values), [v for _, v in values], color="tab:purple", lw=1.8,
                     label="ep_len_mean")
    if fight_lengths:
        lengths.plot(_times(_stamps(fight_lengths)), [v for _, v in fight_lengths],
                     color="tab:purple", alpha=0.25, lw=0.8, label="fight length")
    lengths.set_ylabel("steps")
    lengths.grid(alpha=0.3)
    lengths.legend(loc="upper right", fontsize=9)

    health = axes[3]
    for key, colour, label in (("train/entropy_loss", "tab:brown", "entropy loss"),
                               ("train/explained_variance", "tab:cyan", "explained variance")):
        values = _stamps(series(blocks, key))
        if values:
            health.plot(_times(values), [v for _, v in values], color=colour, lw=1.8, label=label)
    health.set_ylabel("value")
    health.grid(alpha=0.3)
    health.legend(loc="lower right", fontsize=9)
    health.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    for label in health.get_xticklabels():
        label.set_rotation(30)

    figure.tight_layout(rect=(0, 0, 1, 0.98))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    figure.savefig(args.out, dpi=110)
    print("wrote %s" % args.out)
    print("blocks: %d, fights: %d, wins: %d" % (len(blocks), len(episodes), seen))
    if args.show:
        plt.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
