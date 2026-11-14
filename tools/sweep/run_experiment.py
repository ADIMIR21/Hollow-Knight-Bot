"""The sweep bench's runner: one JSON config -> one isolated, tagged training run.

The bench exists because reward tweaks used to be judged by watching a noisy
training statistic and hoping: an experiment must be bounded, isolated and
recorded the same way as the next one before anything can be compared. The
runner is the mechanics; the human stays the decision.

    python tools/sweep/run_experiment.py experiments/baseline.json [--force] [--dry-run]

Config (JSON):

    {
        "name": "baseline",              // required, becomes the run tag "exp-<name>"
        "boss": "GG_False_Knight",       // scene name, alias or list number (default GG_False_Knight)
        "timescale": "fast",             // preset or raw value (default fast)
        "steps": 200000,                 // training budget (default 200000)
        "variant": "b",                  // b: one trainer over N windows (default)
                                         // a: one independent train.py per window
        "windows": 4,                    // variant b only: how many windows (default 4)
        "eval_episodes": 3,              // deterministic probe size (default 3)
        "reward": {                      // optional A/B overrides of hk_gym's reward block
            "HEALTH_PENALTY_PER_MASK": 400
        },
        "ppo": {"ent_coef": 0.05}        // optional PPO overrides (ent_coef)
    }

Everything lands under the run's own tag: ``models/ppo_hk/<scene>__exp-<name>/``,
``logs/progress__exp-<name>.txt``, ``logs/train__exp-<name>.log`` - so two
experiments never touch each other's checkpoints and the comparison report
(`tools/sweep/report.py`) can line them up. An experiment whose tag already
exists is refused unless --force, because silently appending to a finished
run is how comparisons rot.

Stdlib only at import: CI compiles this file without torch/sb3, and the heavy
stack lives only inside the launched trainer process.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    # tools/sweep/run_experiment.py -> the repository root, so the repo-root modules
    # (progress_log) import no matter where the runner is launched from.
    sys.path.insert(0, str(_REPO_ROOT))

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TAG_PREFIX = "exp-"
SWEEP_WORK_DIR = Path("logs") / "sweep"
DEFAULT_STEPS = 200000
DEFAULT_WINDOWS = 4
VARIANTS = ("a", "b")
# The knobs train.py reads from the environment (HK_PPO_*); kept in one place so the
# config validation and the actual reader cannot drift apart.
PPO_ENV_KEYS = {
    "ent_coef": "HK_PPO_ENT_COEF",
}


def experiment_tag(name):
    """The run tag of an experiment: ``exp-<name>``."""
    return _TAG_PREFIX + name


def parse_experiment(path):
    """Loads and validates an experiment config. Raises SystemExit on anything malformed.

    The trainer processes refuse bad values on their own (timescale, reward overrides),
    but a config that cannot even describe an experiment should die here, before any
    game window is launched.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read the experiment config {path}: {exc}")
    if not isinstance(raw, dict):
        raise SystemExit(f"The experiment config {path} must be a JSON object.")

    name = raw.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name) or len(name) > 40:
        raise SystemExit(
            f"{path}: 'name' is required and must match {_NAME_RE.pattern} "
            f"(up to 40 chars; it becomes the run tag exp-<name>)")

    steps = raw.get("steps", DEFAULT_STEPS)
    if not isinstance(steps, int) or isinstance(steps, bool) or steps <= 0:
        raise SystemExit(f"{path}: 'steps' must be a positive integer.")

    variant = raw.get("variant", "b")
    if variant not in VARIANTS:
        raise SystemExit(f"{path}: 'variant' must be one of {list(VARIANTS)}.")

    windows = raw.get("windows", DEFAULT_WINDOWS)
    if not isinstance(windows, int) or isinstance(windows, bool) or not 1 <= windows <= 8:
        raise SystemExit(f"{path}: 'windows' must be an integer in 1..8.")
    if variant == "a" and windows != DEFAULT_WINDOWS:
        raise SystemExit(f"{path}: 'windows' is a variant b knob (variant a spawns one "
                         f"trainer per plan window).")

    reward = raw.get("reward", {})
    if not isinstance(reward, dict):
        raise SystemExit(f"{path}: 'reward' must be an object of constant name -> number.")
    for key, value in reward.items():
        if not (isinstance(key, str) and key.isidentifier() and key.isupper()):
            raise SystemExit(f"{path}: reward key {key!r} is not a CONSTANT_NAME.")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SystemExit(f"{path}: reward {key}: not a number: {value!r}")

    ppo = raw.get("ppo", {})
    if not isinstance(ppo, dict):
        raise SystemExit(f"{path}: 'ppo' must be an object of {sorted(PPO_ENV_KEYS)}.")
    for key, value in ppo.items():
        if key not in PPO_ENV_KEYS:
            raise SystemExit(f"{path}: unknown ppo knob {key!r} (known: {sorted(PPO_ENV_KEYS)})")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SystemExit(f"{path}: ppo {key}: not a number: {value!r}")

    eval_episodes = raw.get("eval_episodes", 3)
    if not isinstance(eval_episodes, int) or isinstance(eval_episodes, bool) or eval_episodes < 0:
        raise SystemExit(f"{path}: 'eval_episodes' must be a non-negative integer.")
    eval_every = raw.get("eval_every", 20)
    if not isinstance(eval_every, int) or isinstance(eval_every, bool) or eval_every <= 0:
        raise SystemExit(f"{path}: 'eval_every' must be a positive integer (rollouts between "
                         f"probes; a bounded run has ~steps/n_steps/n_windows of them).")

    return {
        "name": name,
        "tag": experiment_tag(name),
        "boss": raw.get("boss", "GG_False_Knight"),
        "timescale": raw.get("timescale", "fast"),
        "steps": steps,
        "variant": variant,
        "windows": windows,
        "eval_episodes": eval_episodes,
        "eval_every": eval_every,
        "reward": {str(k): float(v) for k, v in reward.items()},
        "ppo": {str(k): float(v) for k, v in ppo.items()},
    }


def plan_json(windows):
    """The multi-window plan for a variant b run: window numbers only.

    train_multi.parse_plan derives the pipes and the save-slot cycling from the window
    numbers, exactly like the launcher's naming - the sweep must not invent its own.
    """
    return [{"window": window} for window in range(1, windows + 1)]


def experiment_env(experiment, overrides_path, environ=None):
    """The environment the trainer process must run under.

    Empty when there is nothing to override: an experiment that only varies the
    budget must train byte-for-byte like the default run.
    """
    environ = os.environ if environ is None else environ
    env = dict(environ)
    if experiment["reward"]:
        env["HK_REWARD_OVERRIDES"] = str(overrides_path)
    else:
        env.pop("HK_REWARD_OVERRIDES", None)
    for key, env_name in PPO_ENV_KEYS.items():
        if key in experiment["ppo"]:
            env[env_name] = str(experiment["ppo"][key])
        else:
            env.pop(env_name, None)
    return env


def runner_command(experiment, config_path, repo_root=_REPO_ROOT):
    """The command that runs the experiment: one train_multi invocation for both variants.

    Variant b (and its window launching) is the orchestrator's job already; variant a
    goes through the same entry point so the runner owns nothing about launching.
    """
    command = [
        sys.executable, str(Path(repo_root) / "tools" / "multibox" / "train_multi.py"),
        "start", str(config_path),
        "--boss", experiment["boss"],
        "--timescale", str(experiment["timescale"]),
        "--variant", experiment["variant"],
        "--steps", str(experiment["steps"]),
        "--eval-episodes", str(experiment["eval_episodes"]),
        "--eval-every", str(experiment["eval_every"]),
        "--tag", experiment["tag"],
    ]
    if experiment.get("_no_launch"):
        command += ["--no-launch"]
    return command


def prepare_work_dir(experiment, repo_root=_REPO_ROOT):
    """Writes the experiment's working files and returns their paths.

    The reward overrides become a real file because hk_gym reads one from
    HK_REWARD_OVERRIDES; the plan becomes a real file because train_multi takes one.
    """
    folder = Path(repo_root) / SWEEP_WORK_DIR / experiment["name"]
    folder.mkdir(parents=True, exist_ok=True)
    paths = {"folder": folder}
    overrides_path = None
    if experiment["reward"]:
        overrides_path = folder / "reward_overrides.json"
        with open(overrides_path, "w", encoding="utf-8") as handle:
            json.dump(experiment["reward"], handle, indent=2, sort_keys=True)
            handle.write("\n")
    paths["overrides"] = overrides_path
    if experiment["variant"] == "b":
        plan_path = folder / "plan.json"
        with open(plan_path, "w", encoding="utf-8") as handle:
            json.dump(plan_json(experiment["windows"]), handle, indent=2)
            handle.write("\n")
        paths["plan"] = plan_path
    return paths


def experiment_is_new(experiment, repo_root=_REPO_ROOT):
    """Whether the experiment's tag is free: no checkpoint folder and no journal yet."""
    from progress_log import boss_dir_name, journal_path
    tag = experiment["tag"]
    folder = Path(repo_root) / "models" / "ppo_hk" / boss_dir_name(experiment["boss"], tag)
    journal = Path(repo_root) / journal_path(tag)
    return not folder.exists() and not journal.exists()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="run_experiment",
        description="Run one bounded, isolated training experiment from a JSON config.")
    parser.add_argument("config", help="path to the experiment config (JSON)")
    parser.add_argument("--force", action="store_true",
                        help="run even when the experiment's tag already has checkpoints "
                             "or a journal (appends to them)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would run (command, env, files) and exit")
    parser.add_argument("--no-launch", action="store_true",
                        help="do not start the game windows (they are already running "
                             "from a previous experiment of this batch)")
    args = parser.parse_args(argv)

    experiment = parse_experiment(args.config)
    if not args.force and not experiment_is_new(experiment):
        raise SystemExit(
            f"The tag {experiment['tag']} already has checkpoints or a journal "
            f"(models/ppo_hk/{experiment['boss']}__{experiment['tag']}). "
            f"Refusing to append silently - rename the experiment or pass --force.")

    if args.no_launch:
        experiment["_no_launch"] = True
    paths = prepare_work_dir(experiment)
    env = experiment_env(experiment, paths["overrides"])

    if experiment["variant"] == "b":
        config_path = paths["plan"]
        print(f"[SWEEP] variant b over {experiment['windows']} window(s)")
    else:
        # Variant a is driven from the same orchestrator; the plan keeps its default
        # 4-window layout unless the config says otherwise.
        config_path = paths.get("plan")
        if config_path is None:
            config_path = Path(paths["folder"]) / "plan.json"
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump(plan_json(DEFAULT_WINDOWS), handle, indent=2)
                handle.write("\n")
        print(f"[SWEEP] variant a: one independent train.py per window")

    command = runner_command(experiment, config_path)
    print(f"[SWEEP] experiment {experiment['name']} (tag {experiment['tag']})")
    print(f"[SWEEP] boss {experiment['boss']}, timescale {experiment['timescale']}, "
          f"budget {experiment['steps']} steps")
    if experiment["reward"]:
        print(f"[SWEEP] reward overrides: {experiment['reward']}")
    if experiment["ppo"]:
        print(f"[SWEEP] ppo overrides: {experiment['ppo']}")
    print(f"[SWEEP] command: {' '.join(command)}")
    for key in sorted(env):
        if key.startswith("HK_") and key != "HK_PIPE_NAME":
            print(f"[SWEEP] env {key}={env[key]}")

    if args.dry_run:
        print("[SWEEP] dry run - nothing was started")
        return 0

    try:
        result = subprocess.run(command, env=env, cwd=str(_REPO_ROOT))
    except KeyboardInterrupt:
        print("\n[SWEEP] interrupted - the orchestrator keeps running; end it with: "
              "python tools/multibox/train_multi.py stop")
        return 130
    if result.returncode != 0:
        print(f"[SWEEP] the trainer exited with code {result.returncode}")
    print(f"[SWEEP] journal: logs/progress__{experiment['tag']}.txt")
    print(f"[SWEEP] compare with: python tools/sweep/report.py --boss {experiment['boss']}")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
