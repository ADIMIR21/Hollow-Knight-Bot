"""Teleport to pantheon bosses (Godhome) — boss selection and training launch.

Requires the AiTrainHK mod in the game: the boss/teleport/bosses/warp commands
go through the named pipe ``\\\\.\\pipe\\hk_ai_mod``. The %TEMP% file protocol
is gone, there is no fallback for the old mod v1.1 anymore.

Usage:
  python teleport.py                  — interactive boss selection menu;
                                        after the teleport it asks about training
  python teleport.py --train          — same, but training starts right away
  python teleport.py --list           — show the boss list and exit
  python teleport.py --verify         — compare the Python and mod registries over the pipe
  python teleport.py --boss hornet    — teleport to a boss by name/number/alias
  python teleport.py --boss 8         — teleport to a boss by list number
  python teleport.py --boss nkg --train — teleport + training right away (train.py)
  python teleport.py --restart        — restart the current fight
  python teleport.py --warp           — return the hero to the arena gate

Training for every boss is stored automatically:
models/ppo_hk/<scene>/ — checkpoints, hk_model_final.zip, vecnormalize.pkl.
There is no need to create anything by hand.

Interactive commands in the menu:
  <number/name/alias>  teleport to a boss (for example: 5, hornet, nkg, false_knight)
  r                  restart the current fight
  w                  warp to the arena gate (if the hero got thrown out of the fight)
  list               show the boss list
  q                  quit
"""

import argparse
import os
import subprocess
import sys

from bosses import (
    BOSS_LIST,
    current_scene,
    fetch_boss_list,
    is_connected,
    mod_has_scene_field,
    mod_protocol,
    mod_version,
    request_boss,
    request_restart,
    request_warp,
    resolve_query,
    wait_for_scene,
    wait_hello,
)
from hk_pipe import REQUIRED_PROTOCOL, PIPE_PATH


def print_boss_list():
    print("\n=== Godhome bosses (pantheons) ===")
    for i, (scene, label) in enumerate(BOSS_LIST, start=1):
        print(f"  {i:>2}. {label:<28} {scene}")
    print(
        "\nHints: you can enter a number, a scene name (gg_hornet_1), "
        "an alias (hornet, nkg, sisters, oro) or part of a label."
    )
    print("(Variant) entries are harder versions of the fights for the Pantheon of Hallownest.\n")


def check_mod():
    """Checks that the mod pipe is connected and its protocol supports pantheon commands."""
    if not is_connected(timeout=10.0):
        print("[TELEPORT] The mod pipe is not responding — is the game running with the AiTrainHK mod?")
        print(f"           The mod hosts the server {PIPE_PATH}. Check that the game is running")
        print("           and that AiTrainHK.dll is in the Mods folder.")
        return False

    if wait_hello(timeout=3.0) is None:
        print("[TELEPORT] The pipe is open, but no hello message came from the mod.")
        print("           Looks like the pipe is held by something other than the AiTrainHK mod.")
        return False

    proto = mod_protocol()
    if proto < REQUIRED_PROTOCOL:
        print(f"[TELEPORT] The mod responds, but protocol {proto} — pantheon commands "
              f"require >= {REQUIRED_PROTOCOL}.")
        print("           Update the DLL: dotnet build Mod/AiTrainHK/AiTrainHK.csproj -c Release")
        print("           and copy bin/Release/net472/AiTrainHK.dll into the Mods folder.")
        return False

    if not mod_has_scene_field():
        print("[TELEPORT] The mod is connected, but the 'scene' field is missing from telemetry.")
        return False

    print(f"[TELEPORT] Mod connected: {mod_version()} (protocol {proto}).")
    return True


def verify_registry():
    """Compares the local registry (bosses.BOSS_LIST) with the mod's registry over the pipe."""
    data = fetch_boss_list(timeout=3.0)
    if data is None:
        print("[VERIFY] The mod did not answer with the boss_list event within 3 seconds.")
        return False

    mod_bosses = data.get("bosses") or []
    mod_scenes = [b.get("scene") for b in mod_bosses]
    local_scenes = [scene for scene, _ in BOSS_LIST]

    print(f"[VERIFY] Mod: {data.get('count')} entries, Python: {len(BOSS_LIST)}.")
    print(f"[VERIFY] Mod target scene: {data.get('target_scene')}")

    only_local = [s for s in local_scenes if s not in mod_scenes]
    only_mod = [s for s in mod_scenes if s not in local_scenes]
    if only_local:
        print(f"[VERIFY] Only in Python: {', '.join(only_local)}")
    if only_mod:
        print(f"[VERIFY] Only in the mod: {', '.join(only_mod)}")
    if not only_local and not only_mod:
        print("[VERIFY] The registries match.")
        return True
    return False


def run_train(scene):
    """Runs training (train.py) for the selected boss in this same console.

    Ctrl+C in train.py correctly saves the model and the normalization
    statistics to models/ppo_hk/<scene>/.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"\n[TRAIN] Starting train.py for {scene}. Ctrl+C — interrupt and save.\n")
    try:
        return subprocess.call(
            [sys.executable, os.path.join(script_dir, "train.py"), "--boss", scene],
            cwd=script_dir,
        )
    except KeyboardInterrupt:
        print("\n[TRAIN] Interrupted (Ctrl+C).")
        return 130


def teleport_to(query, timeout=45.0):
    """Teleports to a boss by query and waits for the fight to start.

    Returns (ok, scene) — the scene is resolved locally.
    """
    resolved = resolve_query(query)
    if resolved is None:
        print(f"[TELEPORT] Boss not recognized: {query!r}. See: python teleport.py --list")
        return False, None

    scene, label = resolved
    before = current_scene()
    print(f"[TELEPORT] Boss: {label} ({scene})")

    if before == scene:
        # Already in the required scene — the mod will simply reload the arena (fight restart).
        print("[TELEPORT] The scene is already active — restarting the arena.")

    request_boss(scene)

    def progress(current, status):
        if current and current != before:
            print(f"[TELEPORT] Loading: {current} ({status})...")

    ok = wait_for_scene(scene, timeout=timeout, on_progress=progress)
    if ok:
        print(f"[TELEPORT] Done: the fight in arena {scene} has started!")
    else:
        print("[TELEPORT] The fight did not come up within the allotted time.")
        print("           Check the ModLog (the mod logs the scene gates and the chosen entry) — "
              "the mod picks an existing gate on its own, manual edits are rarely needed:")
        print("           with the set_gate <gate> command or the HK_ENTRY_GATE variable.")
    return ok, scene


# Godhome hubs without bosses — training there makes no sense.
NON_TRAIN_SCENES = {"GG_Atrium", "GG_Workshop", "GG_Boss_Door_Entrance"}


def maybe_train(scene, auto_train):
    """Starts training for the scene (or asks), except for hubs without bosses."""
    if not scene:
        return
    if scene in NON_TRAIN_SCENES:
        print(f"[TRAIN] {scene} — a hub without a boss, not starting training.")
        print("           Pick an arena with a boss (python teleport.py --list).")
        return
    if auto_train:
        run_train(scene)
        return
    try:
        answer = input("Start training for this boss? [Y/n]> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    # "\u0434" / "\u0434\u0430" are the Russian "y" / "yes" aliases, kept so the usual
    # keyboard habit still works (escaped so the source stays ASCII).
    if answer in ("", "y", "yes", "\u0434", "\u0434\u0430"):
        run_train(scene)


def interactive_loop(auto_train=False):
    current = current_scene()
    if current:
        print(f"[TELEPORT] Current scene: {current}")
    print("[TELEPORT] Enter a boss number/name, r (restart), w (warp to gate), list, q (quit).\n")

    while True:
        try:
            query = input("boss> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[TELEPORT] Exiting.")
            return

        if not query:
            continue
        low = query.lower()
        if low in ("q", "quit", "exit"):
            print("[TELEPORT] Exiting.")
            return
        if low in ("list", "l", "ls", "--list"):
            print_boss_list()
            continue
        if low == "r":
            print("[TELEPORT] Restarting the fight...")
            request_restart()
            if wait_for_scene(current_scene(), timeout=30.0):
                print("[TELEPORT] The fight has been restarted.")
            else:
                print("[TELEPORT] The fight was not restarted.")
            continue
        if low == "w":
            print("[TELEPORT] Warping to the arena gate...")
            request_warp()
            continue

        ok, scene = teleport_to(query)
        if ok:
            maybe_train(scene, auto_train)
        print()


def main():
    parser = argparse.ArgumentParser(description="Teleport to Hollow Knight pantheon bosses")
    parser.add_argument("--boss", metavar="NAME", help="boss list number/scene name/alias")
    parser.add_argument("--train", action="store_true",
                        help="run train.py for the selected boss right after the teleport")
    parser.add_argument("--list", action="store_true", help="show the boss list")
    parser.add_argument("--verify", action="store_true",
                        help="compare the Python and mod boss registries (over the pipe)")
    parser.add_argument("--restart", action="store_true", help="restart the fight in the current scene")
    parser.add_argument("--warp", action="store_true", help="warp the hero to the arena gate")
    args = parser.parse_args()

    if args.list:
        print_boss_list()
        return

    have_mod = check_mod()

    if args.verify:
        sys.exit(0 if (have_mod and verify_registry()) else 1)

    if args.restart:
        if not have_mod:
            sys.exit(1)
        request_restart()
        print("[TELEPORT] Restart command sent.")
        return

    if args.warp:
        if not have_mod:
            sys.exit(1)
        request_warp()
        print("[TELEPORT] Warp command sent.")
        return

    if args.boss:
        if not have_mod:
            sys.exit(1)
        ok, scene = teleport_to(args.boss)
        if ok:
            maybe_train(scene, auto_train=args.train)
        return

    # Interactive menu
    print_boss_list()
    if not have_mod:
        print("[TELEPORT] Interactive mode requires the game running with the mod.")
        sys.exit(1)
    interactive_loop(auto_train=args.train)


if __name__ == "__main__":
    main()
