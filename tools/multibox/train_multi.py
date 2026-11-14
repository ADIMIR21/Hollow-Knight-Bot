"""Phase 3: the multi-window training orchestrator.

Every window trains the SAME boss (the maintainer's decision). The target
topology is ONE trainer over N windows: a SubprocVecEnv whose workers each
serve one game window through its own named pipe (variant B). All windows
share the boss, the timescale, the checkpoints and the journal; the rollout
collects from every window, so sample throughput scales with the window
count. The interim variant A runs one independent train.py per window with
its own --pipe/--tag/--save-slot namespace instead - useful for splitting
the arena list across windows, or for a controlled A/B of two policies on
the same boss.

Plan file (JSON) - a list of windows:

    [{"window": 1, "save_slot": 2},
     {"window": 2, "save_slot": 3}]

"pipe" is optional and defaults to the launcher's naming (hk_ai_mod for
window 1, hk_ai_mod_<N> otherwise). "save_slot" is optional and defaults
to cycling 2, 3, 4, 1 by window number, so two windows never boot from the
same save by accident.

The game windows must simulate while unfocused: the mod sets
Application.runInBackground = true (deploy the current DLL first).
A capture-free run needs no window layout: the observation is telemetry
only, so overlapping windows are fine.

Usage:
    python train_multi.py start <plan.json> [--boss <query>] [--timescale <t>]
        [--variant b|a] [--no-launch] [--smoke-steps N]
    python train_multi.py stop
    python train_multi.py status

Variant B keeps the trainer in this process: a window that dies takes the
rollout down with it (SubprocVecEnv cannot replace a worker) - restart the
orchestrator; checkpoints, the journal and the normalization statistics
resume where they stopped. Variant A restarts a dead window+trainer pair on
its own (up to three times per window). "stop" from another console kills
the trainer hard: the last up-to-20000 steps are not saved, because the
regular checkpoints (every 20000 steps) are. Ctrl+C in the trainer's own
console is the graceful way out - it saves before exiting.

A --smoke-steps run is isolated from the real checkpoints: it trains in the
<scene>__smoke folder and journals to logs/progress__smoke.txt, so it can
never overwrite a real model.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# tools/multibox/train_multi.py -> the repository root two levels up, so the
# repo-root modules (bosses, train, hk_gym) import no matter where the
# orchestrator is launched from. The script's own folder (tools/multibox) is
# already on sys.path when this file is the main script, and multiprocessing
# spawn passes the parent's sys.path to the workers.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

STATE_FILE = _REPO_ROOT / "logs" / "multi_trainer.json"
# The save-slot default cycles by window number so two windows never boot from
# the same save by accident: window 1 -> slot 2 ... window 4 -> slot 1.
_SAVE_SLOT_CYCLE_START = 2
_SAVE_SLOT_CYCLE_LEN = 4
_PAIR_RESTARTS_LIMIT = 3
_LAUNCH_WAIT = 90  # seconds per window (a cold machine boots the game slowly)
SMOKE_TAG = "smoke"
# The orchestrator's own knobs are stdlib-only at import time: CI compiles this
# file without torch/sb3, so those (and hk_gym with its cv2/mss imports) are
# only ever imported inside main() and inside the worker factory.


def _default_pipe(window):
    """The launcher's pipe naming: hk_ai_mod for window 1, hk_ai_mod_<N> beyond."""
    return "hk_ai_mod" if window == 1 else f"hk_ai_mod_{window}"


def _default_slot(window):
    return (window - 1 + _SAVE_SLOT_CYCLE_START - 1) % _SAVE_SLOT_CYCLE_LEN + 1


def parse_plan(path):
    """Loads and validates a run plan. Returns a list of window configs.

    Every config has "window" (int >= 1), "pipe" (str) and "save_slot" (1..4).
    Raises SystemExit with a readable message on anything malformed - the
    orchestrator must refuse to guess.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read the plan {path}: {exc}")

    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"The plan {path} must be a non-empty JSON list of windows.")

    configs = []
    seen_windows = set()
    seen_pipes = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"The plan {path}, entry {index}: must be an object.")
        window = item.get("window")
        if not isinstance(window, int) or isinstance(window, bool) or window < 1:
            raise SystemExit(f"The plan {path}, entry {index}: 'window' must be a positive integer.")
        if window in seen_windows:
            raise SystemExit(f"The plan {path}: window {window} is listed twice.")
        seen_windows.add(window)

        pipe = item.get("pipe") or _default_pipe(window)
        if not isinstance(pipe, str) or not pipe.strip():
            raise SystemExit(f"The plan {path}, entry {index}: 'pipe' must be a non-empty name.")
        pipe = pipe.strip()
        if pipe in seen_pipes:
            raise SystemExit(f"The plan {path}: two windows share the pipe '{pipe}'.")
        seen_pipes.add(pipe)

        slot = item.get("save_slot", _default_slot(window))
        if not isinstance(slot, int) or isinstance(slot, bool) or not 1 <= slot <= 4:
            raise SystemExit(f"The plan {path}, entry {index}: 'save_slot' must be 1..4.")

        configs.append({"window": window, "pipe": pipe, "save_slot": slot})
    return configs


def worker_env_vars(cfg, boss_scene, entry_gate):
    """Environment variables one worker process must run under.

    The worker process owns exactly one window: its own pipe (hk_pipe resolves
    HK_PIPE_NAME lazily, so setting it before the first client call is enough),
    its own save slot, and the boss scene hk_gym reads at import time. No run
    tag: variant B shares the single untagged run folder.
    """
    return {
        "HK_PIPE_NAME": cfg["pipe"],
        "HK_SAVE_SLOT": str(cfg["save_slot"]),
        "HK_BOSS_SCENE": boss_scene,
        "HK_ENTRY_GATE": entry_gate,
    }


def train_command(cfg, boss, timescale, tag):
    """The variant A command line: one independent train.py per window."""
    return [
        sys.executable, str(_REPO_ROOT / "train.py"),
        "--pipe", cfg["pipe"],
        "--save-slot", str(cfg["save_slot"]),
        "--tag", tag,
        "--boss", boss,
        "--timescale", str(timescale),
    ]


def read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def write_state(state):
    STATE_FILE.parent.mkdir(exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def clear_state():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def _pid_alive(pid):
    """Is a process with this pid alive?

    This is NOT os.kill(pid, 0): on Windows os.kill terminates the target
    (TerminateProcess with the signal as the exit code) instead of probing it,
    so the old probe killed the very watcher it was reporting on - "status"
    stopped the orchestrator, and _kill() always murdered the process before
    its own taskkill could run (whose failure capture_output then swallowed).
    """
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)      # POSIX: signal 0 probes without delivering anything
        return True
    except OSError:
        return False


def _windows_pid_alive(pid):
    """The Windows liveness probe: OpenProcess + GetExitCodeProcess."""
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False         # ERROR_ACCESS_LEVEL / ERROR_INVALID_PARAMETER: gone
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _kill(pid):
    if not pid:
        return False
    if not _pid_alive(pid):
        return False
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    return True


def _window_running(window):
    """Is the launcher's process for this instance already up?"""
    import hk_multibox
    return f"hollow_knight_{window}" in {name for _, name in hk_multibox.game_processes()}


def _launch_windows(plan, width=None, height=None):
    """Starts every window of the plan that is not already running."""
    import hk_multibox
    if width or height:
        print(f"[MULTI] Launching windows at {width or 'default'}x{height or 'default'}")
    for cfg in plan:
        if _window_running(cfg["window"]):
            print(f"[MULTI] window {cfg['window']} is already up (skipping launch).")
            continue
        print(f"[MULTI] launching window {cfg['window']}...", flush=True)
        proc, state = hk_multibox.launch_one(cfg["window"], _LAUNCH_WAIT,
                                             width=width, height=height)
        print(f"[MULTI]   window {cfg['window']}: {state}")
    if not _wait_pipes(plan):
        print("[MULTI] Some windows never came up. Use --no-launch if they are "
              "already running, or hk_multibox.py stop to clean up.")
        return False
    return True


def _wait_pipes(plan):
    """Blocks until every window's pipe is up (the windows may still be booting)."""
    import hk_multibox
    waiting = {cfg["pipe"] for cfg in plan}
    deadline = time.monotonic() + _LAUNCH_WAIT * max(1, len(plan))
    while waiting and time.monotonic() < deadline:
        waiting -= hk_multibox.mod_pipes()
        if not waiting:
            return True
        time.sleep(1.0)
    if waiting:
        print(f"[MULTI] No pipe after {_LAUNCH_WAIT * len(plan)} s: {sorted(waiting)}")
        return False
    return True


# ------------------------------------------------------------------ variant B

def _worker_env(cfg, boss_scene, entry_gate):
    """The env factory one SubprocVecEnv worker runs (in its own process).

    Runs after spawn with the worker's fresh interpreter: the environment
    variables are fixed BEFORE anything imports the stack, then the worker
    boots its window into the game (each window loads its own save slot) and
    builds its own gym environment.
    """
    for key, value in worker_env_vars(cfg, boss_scene, entry_gate).items():
        os.environ[key] = value
    os.environ.pop("HK_RUN_TAG", None)  # variant B shares the untagged folder

    import bosses
    if not bosses.ensure_save_loaded(cfg["save_slot"], timeout=180.0, verbose=True):
        raise RuntimeError(
            f"window {cfg['window']} ({cfg['pipe']}): the game never left the "
            f"main menu (save slot {cfg['save_slot']})")
    # The scene was fixed above; hk_gym reads HK_BOSS_SCENE at import time.
    from hk_gym import HollowKnightGym
    from stable_baselines3.common.monitor import Monitor
    return Monitor(HollowKnightGym())


def run_shared_trainer(plan, boss_scene, boss_label, entry_gate, timescale, smoke_steps,
                       eval_episodes=3, eval_every=20):
    """Variant B: one PPO run over one pipe per window (this process)."""
    import train
    import hk_logging
    from stable_baselines3.common.vec_env import SubprocVecEnv
    from bosses import resolve_timescale
    from progress_log import boss_dir_name, journal_path, trainer_log_path

    # A smoke run is isolated from the real checkpoints, and its trainer log is
    # namespaced the same way so it cannot overwrite the real one either.
    tag = SMOKE_TAG if smoke_steps else None
    hk_logging.setup_logging(trainer_log_path(tag))
    LOG = hk_logging.get_logger()

    print(f"[MULTI] Shared trainer over {len(plan)} window(s), boss: "
          f"{boss_label} ({boss_scene})")
    for cfg in plan:
        print(f"[MULTI]   window {cfg['window']}: pipe {cfg['pipe']}, save slot {cfg['save_slot']}")

    factories = [(lambda cfg=cfg: _worker_env(cfg, boss_scene, entry_gate)) for cfg in plan]
    base_vec_env = SubprocVecEnv(factories, start_method="spawn")

    # The same time scale in every window: the command goes to each worker's
    # own pipe through its own controller (the same resolver train.py uses).
    scale = resolve_timescale(timescale,
                              problem=lambda msg: print(f"[MULTI] {msg}"))
    base_vec_env.env_method("set_timescale", scale)
    actual = max(base_vec_env.env_method("get_time_scale"))
    print(f"[MULTI] Game time scale in the windows: {actual:g}")
    if actual > 3.0:
        print("[MULTI]  WARNING: above 3x risks breaking frame-tied physics")
    # The mod's telemetry is the source of truth for what speed the checkpoints
    # train at: run_config_values() fingerprints train.GLOBAL_TIMESCALE, and
    # leaving it at the default 1.0 wrote a wrong fingerprint for every run
    # above normal speed - a mismatched checkpoint would resume silently (or a
    # matching one be refused) with nothing in the logs to say why.
    train.GLOBAL_TIMESCALE = actual

    # The shared run folder is the plain per-boss layout (no tag): one policy,
    # one journal, checkpoints resumable across a changed window count. A smoke
    # run is isolated under the __smoke tag so it cannot touch real checkpoints.
    boss_dir = os.path.join(train.MODELS_ROOT, boss_dir_name(boss_scene, tag))
    os.makedirs(boss_dir, exist_ok=True)
    train.migrate_legacy_model(boss_scene, boss_dir)
    vecnorm_path = os.path.join(boss_dir, "vecnormalize.pkl")
    print(f"[MULTI] Shared training folder: {boss_dir}")

    resume_allowed = train.decide_resume(boss_dir, log=LOG.info)

    vec_env = train.load_compatible_vecnorm(base_vec_env, vecnorm_path, resume=resume_allowed)
    # A pause left behind by a killed trainer is lifted on the way in.
    try:
        vec_env.env_method("resume_game")
    except Exception as exc:
        print(f"[MULTI] Could not ask the windows to resume: {exc}")

    model = train.load_or_create_model(vec_env, boss_dir, resume_allowed, log=LOG.info)
    train.write_run_manifest(boss_dir, model, source="train_multi.py (variant b)", log=LOG.info)

    callbacks, game_pause = train.build_callbacks(
        vec_env, boss_dir, vecnorm_path, journal_path(tag),
        eval_episodes=eval_episodes, eval_every=eval_every)

    total = smoke_steps if smoke_steps else 5000000
    if smoke_steps:
        print(f"[MULTI] SMOKE RUN: {smoke_steps} steps in {boss_dir}.")
    print("[MULTI] Ctrl+C saves and exits (the per-window console keys are not available here).")
    try:
        model.learn(total_timesteps=total, reset_num_timesteps=False, callback=callbacks)
    except KeyboardInterrupt:
        print("\n[MULTI] Interrupted. Saving what we have...")
    finally:
        # build_callbacks hands the pause callback back precisely for this: Ctrl+C
        # can land inside the PPO update with every window still frozen.
        game_pause.resume()
        final_path = os.path.join(boss_dir, "hk_model_final")
        model.save(final_path)
        vec_env.save(vecnorm_path)
        print(f"[MULTI] Model saved to {final_path}.zip, statistics to {vecnorm_path}")
        clear_state()
    return 0


# ------------------------------------------------------------------ variant A

def run_per_window(plan, boss, timescale):
    """Variant A: one independent train.py per window, watched and restarted."""
    import hk_multibox

    processes = {}  # window -> Popen
    restarts = {}
    for cfg in plan:
        processes[cfg["window"]] = subprocess.Popen(
            train_command(cfg, boss, timescale, tag=f"w{cfg['window']}"),
            cwd=str(_REPO_ROOT))
        restarts[cfg["window"]] = 0

    write_state({
        "variant": "a",
        "trainer_pid": os.getpid(),                      # the watcher itself
        "trainer_pids": {str(w): p.pid for w, p in processes.items()},
        "plan": plan,
        "boss": boss,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    def window_is_down(cfg):
        proc = processes[cfg["window"]]
        return proc.poll() is not None and restarts[cfg["window"]] >= _PAIR_RESTARTS_LIMIT

    try:
        while True:
            time.sleep(5.0)
            for cfg in plan:
                window = cfg["window"]
                proc = processes[window]
                if proc.poll() is None:
                    continue
                code = proc.returncode
                if restarts[window] >= _PAIR_RESTARTS_LIMIT:
                    print(f"[MULTI] window {window}: trainer exited (code {code}), "
                          f"restart limit reached - leaving it down.")
                    continue
                restarts[window] += 1
                # The window may have died together with its trainer: make sure
                # the pipe is there again before starting a new trainer.
                if cfg["pipe"] not in hk_multibox.mod_pipes():
                    print(f"[MULTI] window {window}: relaunching the game window...")
                    hk_multibox.launch_one(window, _LAUNCH_WAIT)
                    if cfg["pipe"] not in hk_multibox.mod_pipes():
                        print(f"[MULTI] window {window}: the window did not come back.")
                        continue
                print(f"[MULTI] window {window}: restarting the trainer "
                      f"(attempt {restarts[window]}/{_PAIR_RESTARTS_LIMIT})...", flush=True)
                processes[window] = subprocess.Popen(
                    train_command(cfg, boss, timescale, tag=f"w{window}"),
                    cwd=str(_REPO_ROOT))
            if all(window_is_down(cfg) for cfg in plan):
                print("[MULTI] All windows are done (or past the restart limit).")
                break
        clear_state()
    except KeyboardInterrupt:
        # Keep the state file: "stop" needs the pids to end what keeps running.
        print("\n[MULTI] The watcher stopped. The trainers keep running - "
              "end them with: python train_multi.py stop")
    return 0


# ----------------------------------------------------------------------- cli

def cmd_start(args):
    # No plan given: the maintainer's standard setup - four windows on the
    # second monitor (tools/multibox/plan_4windows.json).
    if not args.plan:
        args.plan = str(_REPO_ROOT / "tools" / "multibox" / "plan_4windows.json")
        print(f"[MULTI] No plan given, using the default 4-window plan: {args.plan}")
    plan = parse_plan(args.plan)
    import bosses
    resolved = bosses.resolve_query(args.boss)
    boss_scene, boss_label = resolved if resolved else (args.boss, args.boss)
    if args.variant == "a" and args.smoke_steps:
        print("[MULTI] --smoke-steps is a variant B feature (variant A runs the "
              "full train.py flow).")
        return 1

    if not args.no_launch and not _launch_windows(plan, width=args.width, height=args.height):
        return 2
    if not _wait_pipes(plan):
        return 2

    write_state({
        "variant": args.variant,
        "trainer_pid": os.getpid(),
        "plan": plan,
        "boss": boss_scene,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    if args.variant == "b":
        return run_shared_trainer(plan, boss_scene, boss_label,
                                  args.entry_gate, args.timescale, args.smoke_steps,
                                  eval_episodes=args.eval_episodes,
                                  eval_every=args.eval_every)
    return run_per_window(plan, boss_scene, args.timescale)


def cmd_stop(args):
    state = read_state()
    if state is None:
        print("No orchestrator state found (nothing was started, or it already stopped).")
    else:
        variant = state.get("variant")
        watcher = state.get("trainer_pid")
        if _kill(watcher):
            print(f"Stopped the orchestrator watcher (variant {variant}, PID {watcher}).")
            time.sleep(2.0)
        else:
            print(f"The watcher (variant {variant}, PID {watcher}) is not running.")
        for pid in (state.get("trainer_pids") or {}).values():
            if _kill(pid):
                print(f"Stopped a trainer subprocess (PID {pid}).")
        clear_state()

    import hk_multibox
    return hk_multibox.cmd_stop(args)


def cmd_status(args):
    state = read_state()
    if state:
        pid = state.get("trainer_pid")
        alive = pid and _pid_alive(pid)
        print(f"Orchestrator: variant {state.get('variant')}, boss {state.get('boss')}, "
              f"started {state.get('started')}, watcher PID {pid} "
              f"({'RUNNING' if alive else 'not running'})")
        for cfg in state.get("plan", []):
            print(f"  window {cfg['window']}: pipe {cfg['pipe']}, save slot {cfg['save_slot']}")
    else:
        print("Orchestrator: no state file (nothing started).")

    import hk_multibox
    hk_multibox.cmd_status(args)
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="train_multi",
        description="Phase 3: train over several game windows from one plan.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start", help="start the plan: windows + the trainer")
    p.add_argument("plan", nargs="?", default=None,
                   help="path to the run plan (JSON); defaults to the 4-window "
                        "plan tools/multibox/plan_4windows.json")
    p.add_argument("--boss", default=os.environ.get("HK_BOSS_SCENE", "GG_False_Knight"),
                   help="the boss ALL windows train on (default GG_False_Knight)")
    p.add_argument("--entry-gate", default=os.environ.get("HK_ENTRY_GATE", "door_dreamEnter"))
    p.add_argument("--timescale", default=os.environ.get("HK_TIMESCALE", "normal"))
    p.add_argument("--variant", choices=["a", "b"], default="b",
                   help="b: one trainer over all windows (the goal). "
                        "a: one independent train.py per window (interim)")
    p.add_argument("--no-launch", action="store_true",
                   help="do not start the game windows (they are already running)")
    p.add_argument("--smoke-steps", type=int, default=0, metavar="N",
                   help="stop after N steps instead of the full run (a live check "
                        "isolated in the <scene>__smoke folder)")
    p.add_argument("--eval-episodes", type=int, default=3, metavar="N",
                   help="deterministic evaluation episodes between rollouts "
                        "(0 disables the probe; same meaning as train.py's flag)")
    p.add_argument("--eval-every", type=int, default=20, metavar="ROLLOUTS",
                   help="run the deterministic probe every ROLLOUTS rollouts")
    p.add_argument("--width", type=int, metavar="N",
                   help="window render width (Unity -screen-width). Cuts GPU load "
                        "per window; telemetry observation is unaffected. "
                        "Env: HKMB_WIDTH")
    p.add_argument("--height", type=int, metavar="N",
                   help="window render height (Unity -screen-height). "
                        "Env: HKMB_HEIGHT")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("stop", help="stop the trainer and close the windows")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("status", help="orchestrator + windows + pipes")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
