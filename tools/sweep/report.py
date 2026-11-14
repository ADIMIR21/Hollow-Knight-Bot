"""The sweep bench's comparison report: every experiment of a boss in one table.

Each experiment leaves the same traces (that is the whole point of the runner):
a journal ``logs/progress__exp-<name>.txt``, a checkpoint folder
``models/ppo_hk/<scene>__exp-<name>/`` with the fingerprint that records the
reward values actually trained under, and a manifest with the library versions
and hyperparameters. This tool lines them up:

    python tools/sweep/report.py --boss GG_False_Knight
    python tools/sweep/report.py --boss GG_False_Knight --all   # + the untagged baseline

The columns are the ones a decision needs: how often the bot won (both the
exploring policy's training win rate and the deterministic probe's), how fast
the first win arrived, how long fights lasted and what the reward actually was
- read from the fingerprint, not from the config, because the fingerprint is
what the run really trained under.

Stdlib only at import (the aggregation is pure); CI compiles and tests it.
"""

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from progress_log import (boss_dir_name, journal_path,  # noqa: E402
                          parse_journal, series, victory_steps)
from run_experiment import experiment_tag  # noqa: E402

# Fingerprint keys the report shows as "what the reward actually was". The rest of the
# fingerprint (gamma, sizes) is either constant across experiments or not a decision here.
REWARD_COLUMNS = (
    "damage_reward_per_hp",
    "health_penalty_per_mask",
    "open_window_damage_multiplier",
    "victory_reward",
    "step_penalty",
)
METRIC_COLUMNS = ("eval_win_rate", "train_win_rate", "first_win_step", "ep_len_mean")


def summarize_journal(text, window=100):
    """The decision numbers one journal carries, as a flat dict.

    All rates are fractions in [0, 1]; ``first_win_step`` is None while the bot has
    not won yet. The step-limit windows (the fight-continues bookkeeping) are not
    episodes and count nowhere.
    """
    blocks, episodes = parse_journal(text)
    real = [episode for episode in episodes if episode[2] != "window"]
    outcomes = [episode[2] for episode in real]
    victories = outcomes.count("victory")
    last = outcomes[-window:]
    metrics = dict(blocks[-1][1]) if blocks else {}

    def last_metric(key):
        values = series(blocks, key)
        return values[-1][1] if values else None

    won_steps = victory_steps(text)
    return {
        "episodes": len(real),
        "victories": victories,
        "train_win_rate": victories / len(real) if real else None,
        "train_win_rate_window": victories / len(last) if last else None,
        "deaths": outcomes.count("death"),
        "timeouts": outcomes.count("timeout"),
        "step_limit_windows": len(episodes) - len(real),
        "first_win_step": min(won_steps) if won_steps else None,
        "last_step": max((episode[1] for episode in real), default=None),
        "eval_win_rate": last_metric("eval/win_rate"),
        "eval_batch_win_rate": last_metric("eval/batch_win_rate"),
        "eval_mean_reward": last_metric("eval/mean_reward"),
        "ep_len_mean": last_metric("rollout/ep_len_mean"),
        "ep_rew_mean": last_metric("rollout/ep_rew_mean"),
    }


def collect(boss, repo_root=_REPO_ROOT, include_baseline=False):
    """Every experiment of one boss, oldest tag first, as report rows.

    A row is the experiment's identity (tag), what it trained under (the fingerprint's
    reward values + the manifest's git commit and ent_coef) and what came out of it
    (summarize_journal). Experiments whose journal is missing are reported as such
    instead of silently dropped - an experiment that ran but left no journal is a
    finding, not noise.
    """
    repo_root = Path(repo_root)
    rows = []
    models_root = repo_root / "models" / "ppo_hk"
    prefixes = [boss_dir_name(boss, experiment_tag(""))]
    if include_baseline:
        prefixes.append(boss)          # the plain, untagged folder
    if not models_root.exists():
        return rows
    folders = sorted(folder for folder in models_root.iterdir()
                     if folder.is_dir() and any(folder.name == p or folder.name.startswith(p)
                                                for p in prefixes))
    for folder in folders:
        tag = folder.name[len(boss) + 2:] if folder.name != boss else ""
        row = {
            "tag": tag or "(baseline)",
            "folder": folder,
            "error": None,
        }
        fingerprint = _read_json(folder / "run_config.json")
        manifest = _read_json(folder / "run_manifest.json")
        row["reward"] = {key: fingerprint[key] for key in REWARD_COLUMNS
                         if fingerprint and key in fingerprint}
        if manifest:
            hyper = manifest.get("hyperparameters") or {}
            row["ent_coef"] = hyper.get("ent_coef")
            row["git_commit"] = manifest.get("git_commit")
        else:
            row["ent_coef"] = None
            row["git_commit"] = None
        journal = repo_root / journal_path(tag or None)
        row.update(summarize_journal(""))
        if journal.exists():
            row.update(summarize_journal(journal.read_text(encoding="utf-8")))
        else:
            row["error"] = f"no journal: {journal_path(tag or None)}"
        rows.append(row)
    return rows


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _fmt(value, width, none="-"):
    if value is None:
        text = none
    elif isinstance(value, float):
        text = f"{value:.3f}" if abs(value) < 1000 else f"{value:.0f}"
    else:
        text = str(value)
    return text.rjust(width)


def build_table(rows):
    """The report as aligned text, one experiment per line."""
    if not rows:
        return "No experiments found (look for models/ppo_hk/<scene>__exp-*/ folders)."
    widths = {"tag": max(len(row["tag"]) for row in rows) + 1}
    lines = []
    header = "tag".ljust(widths["tag"])
    for key in REWARD_COLUMNS:
        short = key.replace("damage_reward_per_hp", "dmg/hp") \
                   .replace("health_penalty_per_mask", "mask") \
                   .replace("open_window_damage_multiplier", "openx") \
                   .replace("victory_reward", "win") \
                   .replace("step_penalty", "step")
        header += short.rjust(8)
    header += "eps".rjust(6) + "eval_win".rjust(9) + "train_win".rjust(10) \
              + "1st_win".rjust(9) + "ep_len".rjust(8) + "ent".rjust(7) + "  note"
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        line = row["tag"].ljust(widths["tag"])
        for key in REWARD_COLUMNS:
            line += _fmt(row["reward"].get(key), 8)
        line += _fmt(row.get("episodes"), 6)
        line += _fmt(row.get("eval_win_rate"), 9)
        line += _fmt(row.get("train_win_rate"), 10)
        line += _fmt(row.get("first_win_step"), 9)
        line += _fmt(row.get("ep_len_mean"), 8)
        line += _fmt(row.get("ent_coef"), 7)
        note = row.get("error") or ""
        if row.get("git_commit"):
            note = (note + " " if note else "") + f"@{row['git_commit']}"
        line += "  " + note
        lines.append(line)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="report",
        description="Compare the sweep experiments of one boss (see docs/sweep.md).")
    parser.add_argument("--boss", required=True,
                        help="the boss scene the experiments trained on")
    parser.add_argument("--all", action="store_true",
                        help="also show the untagged baseline folder")
    args = parser.parse_args(argv)
    rows = collect(args.boss, include_baseline=args.all)
    print(build_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
