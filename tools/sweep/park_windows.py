"""Parks every game window in its boss arena before a training batch.

Why this exists: a window that boots from a save parked mid-world (or mid-death
scenario) has to cross into Godhome while the trainer's restart loop is already
firing - and the restart deferral plus the world-scenario clean-up can chew tens
of minutes per window (observed live: 143-467 s "the game state never freed up",
in a cycle, with zero fights detected). Parking does the same thing the trainer
would, but with patience: one window at a time, load the save, wait for the
teleport to finish, freeze the window, move to the next one. The trainer then
starts with every window already in a live fight.

    python tools/sweep/park_windows.py --windows 4 --boss GG_False_Knight
    python tools/sweep/park_windows.py --windows 2 --slots 2,3

Save slots default to the launcher's cycling (2, 3, 4, 1 by window number). Each
parked window is left paused; the trainer lifts the pause on the way in (both
train.py and the orchestrator ask the mod to resume first thing).
"""

import argparse
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "multibox"))


def _window_slot(window):
    """The launcher's save-slot cycling: window 1 -> slot 2 ... window 4 -> slot 1."""
    return (window - 1 + 1) % 4 + 1


def park_one(window, slot, boss, wait_s=180.0, pause=True):
    """Launches one window and walks it into the arena. True when a fight is live."""
    import hk_multibox
    proc, state = hk_multibox.launch_one(window, wait_s)
    print(f"[PARK] window {window}: {state}")
    if state != "ok":
        return False

    pipe_name = hk_multibox.PIPE_BASE if window == 1 else f"{hk_multibox.PIPE_BASE}_{window}"
    os.environ["HK_PIPE_NAME"] = pipe_name

    import bosses
    if not bosses.ensure_save_loaded(slot, timeout=wait_s, verbose=True):
        print(f"[PARK] window {window}: save slot {slot} never loaded")
        return False

    # Teleport to the arena ("boss <query>" = set scene + restart), then wait for a
    # live fight with the same conditions the trainer's awaiting loop uses.
    bosses.send_command(f"boss {boss}")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        telemetry = bosses.get_shared_client().get_telemetry() or {}
        if (telemetry.get("status") == "fight"
                and int(telemetry.get("restart_pending", 1)) == 0
                and float(telemetry.get("hp", 0)) > 0
                and float(telemetry.get("boss_hp", 0)) > 0):
            print(f"[PARK] window {window}: fight is live in {telemetry.get('scene')}")
            if pause:
                bosses.send_command("pause")
                time.sleep(0.5)
            return True
        time.sleep(1.0)
    print(f"[PARK] window {window}: no live fight within {wait_s:.0f} s "
          f"(last: {telemetry})")
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="park_windows",
        description="Walk every game window into its boss arena before a training batch.")
    parser.add_argument("--windows", type=int, default=4)
    parser.add_argument("--slots", default=None,
                        help="comma-separated save slots, one per window "
                             "(default: the launcher's 2,3,4,1 cycling)")
    parser.add_argument("--boss", default="GG_False_Knight")
    parser.add_argument("--wait", type=float, default=180.0,
                        help="seconds to wait per window (cold boots are slow)")
    parser.add_argument("--no-pause", action="store_true",
                        help="leave the parked windows running (default: pause)")
    args = parser.parse_args(argv)

    slots = ([int(s) for s in args.slots.split(",")] if args.slots
             else [_window_slot(w) for w in range(1, args.windows + 1)])
    if len(slots) != args.windows:
        raise SystemExit(f"--slots must carry exactly {args.windows} values.")

    results = {}
    for window, slot in enumerate(slots, start=1):
        results[window] = park_one(window, slot, args.boss, wait_s=args.wait,
                                   pause=not args.no_pause)
    failed = [window for window, ok in results.items() if not ok]
    if failed:
        print(f"[PARK] windows not parked: {failed} - the trainer will still try to "
              f"restart them itself, but expect a slow start.")
        return 1
    print("[PARK] all windows are in the arena and paused; start the batch with "
          "the sweep runner (--no-launch).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
