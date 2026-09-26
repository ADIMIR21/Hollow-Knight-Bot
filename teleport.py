"""Teleport to pantheon bosses (Godhome) — boss selection and training launch.

Requires the HK_AI_Mod mod in the game (access to boss/teleport/bosses/warp commands).

Usage:
  python teleport.py                  — interactive boss selection menu;
                                        after teleporting it will ask about training
  python teleport.py --train          — the same, but training starts right away
  python teleport.py --list           — show the boss list and exit
  python teleport.py --boss hornet    — teleport to a boss by name/number/alias
  python teleport.py --boss 8         — teleport to a boss by list number
  python teleport.py --boss nkg --train — teleport + training right away (train.py)
  python teleport.py --restart        — restart the current fight
  python teleport.py --warp           — return the hero to the arena gate

Training for each boss is stored automatically:
models/ppo_hk/<scene>/ — checkpoints, hk_model_final.zip, vecnormalize.pkl.
Nothing needs to be created manually.

Interactive commands in the menu:
  <number/name/alias>  teleport to a boss (e.g.: 5, hornet, nkg, false_knight)
  r                  restart the current fight
  w                  warp to the arena gate (if the hero was knocked out of the fight)
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
    mod_has_scene_field,
    read_telemetry,
    request_boss,
    request_restart,
    request_warp,
    resolve_query,
    set_boss_scene,
    wait_for_scene,
)


def print_boss_list():
    print("\n=== Godhome Bosses (Pantheons) ===")
    for i, (scene, label) in enumerate(BOSS_LIST, start=1):
        print(f"  {i:>2}. {label:<28} {scene}")
    print(
        "\nTips: you can enter a number, scene name (gg_hornet_1), "
        "an alias (hornet, nkg, sisters, oro) or part of the name."
    )
    print("Variants with (Variant) — harder fight versions for the Pantheon of Hallownest.\n")


def check_mod():
    data = read_telemetry()
    if data is None:
        print("[TELEPORT] No telemetry — is the game with the HK_AI_Mod mod not running?")
        print("           Start the game (the mod writes %TEMP%/hk_ai_data.json) and try again.")
        return False
    if not mod_has_scene_field():
        print("[TELEPORT] Telemetry has no 'scene' field — an old mod version is installed.")
        print("           Update the DLL: dotnet build Mod/HK_AI_Mod/HK_AI_Mod.csproj -c Release")
        print("           and copy bin/Release/net472/HK_AI_Mod.dll into the Mods folder.")
        return False
    return True


def run_train(scene):
    """Starts training (train.py) for the selected boss in this same console.

    Ctrl+C in train.py correctly saves the model and normalization statistics
    in models/ppo_hk/<scene>/.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"\n[TRAINING] Starting train.py for {scene}. Ctrl+C — interrupt and save.\n")
    try:
        return subprocess.call(
            [sys.executable, os.path.join(script_dir, "train.py"), "--boss", scene],
            cwd=script_dir,
        )
    except KeyboardInterrupt:
        print("\n[TRAINING] Interrupted (Ctrl+C).")
        return 130


def teleport_to(query, timeout=45.0):
    """Teleport to a boss by query and wait for the fight to start.

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
        # Already in the right scene — the mod will just reload the arena (restart the fight).
        print("[TELEPORT] Scene already active — restarting the arena.")

    request_boss(scene)

    def progress(current, status):
        if current and current != before:
            print(f"[TELEPORT] Loading: {current} ({status})...")

    ok = wait_for_scene(scene, timeout=timeout, on_progress=progress)
    if ok:
        print(f"[TELEPORT] Done: the fight on arena {scene} has started!")
    else:
        print("[TELEPORT] The fight did not come up within the allotted time.")
        print("           Check ModLog (the mod logs the scene's active gates and chosen entrance) — "
              "the mod picks an existing gate itself, manual edits are rarely needed: hk_ai_gate.txt.")
    return ok, scene


def legacy_fallback(query):
    """Fallback for the old mod: write the scene to the config and send restart."""
    resolved = resolve_query(query)
    if resolved is None:
        print(f"[TELEPORT] Boss not recognized: {query!r}.")
        return None
    scene, label = resolved
    print(f"[TELEPORT] Old mod: setting scene {scene} and restarting.")
    set_boss_scene(scene)
    request_restart()
    ok = wait_for_scene(scene, timeout=45.0)
    print("[TELEPORT] Done!" if ok else "[TELEPORT] The fight did not come up.")
    return scene if ok else None


# Godhome hubs without bosses — training there makes no sense.
NON_TRAIN_SCENES = {"GG_Atrium", "GG_Workshop", "GG_Boss_Door_Entrance"}


def maybe_train(scene, auto_train):
    """Starts training for the scene (or asks), except for bossless hubs."""
    if not scene:
        return
    if scene in NON_TRAIN_SCENES:
        print(f"[TRAINING] {scene} — a hub without a boss, not starting training.")
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
    if answer in ("", "y", "yes"):
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
            print("\n[TELEPORT] Quitting.")
            return

        if not query:
            continue
        low = query.lower()
        if low in ("q", "quit", "exit"):
            print("[TELEPORT] Quitting.")
            return
        if low == "list":
            print_boss_list()
            continue
        if low == "r":
            print("[TELEPORT] Restarting the fight...")
            request_restart()
            if wait_for_scene(current_scene(), timeout=30.0):
                print("[TELEPORT] Fight restarted.")
            else:
                print("[TELEPORT] The fight did not restart.")
            continue
        if low == "w":
            print("[TELEPORT] Warping to the arena gate...")
            request_warp()
            continue
        if low in ("l", "ls", "--list"):
            print_boss_list()
            continue

        ok, scene = teleport_to(query)
        if ok:
            maybe_train(scene, auto_train)
        print()


def main():
    parser = argparse.ArgumentParser(description="Teleport to Hollow Knight pantheon bosses")
    parser.add_argument("--boss", metavar="NAME", help="boss number/scene name/alias")
    parser.add_argument("--train", action="store_true",
                        help="after teleporting, start train.py for the selected boss right away")
    parser.add_argument("--list", action="store_true", help="show the boss list")
    parser.add_argument("--restart", action="store_true", help="restart the fight in the current scene")
    parser.add_argument("--warp", action="store_true", help="warp the hero to the arena gate")
    args = parser.parse_args()

    if args.list:
        print_boss_list()
        return

    have_mod = check_mod()

    if args.restart:
        request_restart()
        print("[TELEPORT] Restart command sent.")
        return
    if args.warp:
        request_warp()
        print("[TELEPORT] Warp command sent.")
        return
    if args.boss:
        if have_mod:
            ok, scene = teleport_to(args.boss)
            if ok:
                maybe_train(scene, auto_train=args.train)
        else:
            scene = legacy_fallback(args.boss)
            if scene:
                maybe_train(scene, auto_train=args.train)
        return

    # Interactive menu
    print_boss_list()
    if have_mod:
        interactive_loop(auto_train=args.train)
        return

    print("[TELEPORT] Interactive mode requires the mod and a running game.")
    print("           You can try the fallback for the old mod.")
    try:
        query = input("boss > ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if query and query.lower() not in ("q", "quit"):
        legacy_fallback(query)


if __name__ == "__main__":
    main()
