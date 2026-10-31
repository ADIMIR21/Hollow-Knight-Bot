"""Sync Hollow Knight save slots: make every parallel window boot from the
same hero.

The save folder lives in the user profile,
%USERPROFILE%\\AppData\\LocalLow\\Team Cherry\\Hollow Knight, and is shared by
all multibox windows. The slot index maps straight to the file name
(user2.dat = slot 2; the game's Platform.GetSaveSlotFileName formats the
index directly - verified in the IL probe). Parallel windows must boot with
the same hero (the same max HP, upgrades, nail): a window on an early-game
save fights a different boss battle than a window on a finished save, and
the PPO run mixes incommensurable episodes. This tool copies one slot's
file over the others, backing up every overwritten file first.

The game must be closed: an open game may rewrite its slot at any moment and
a copy taken under it is torn.

Usage:
    python tools/multibox/sync_saves.py --list
    python tools/multibox/sync_saves.py --from 2 --to 1,3,4
"""

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for hk_multibox

DEFAULT_SAVE_DIR = os.path.join(
    os.environ.get("USERPROFILE", ""), "AppData", "LocalLow", "Team Cherry", "Hollow Knight")
SLOT_MIN = 1
SLOT_MAX = 4


def slot_path(slot, save_dir=None):
    """The file name of one save slot: user2.dat for slot 2 (IL-probed mapping)."""
    if not SLOT_MIN <= slot <= SLOT_MAX:
        raise ValueError(f"save slot {slot} out of range {SLOT_MIN}..{SLOT_MAX}")
    return os.path.join(save_dir or DEFAULT_SAVE_DIR, f"user{slot}.dat")


def parse_slots(text):
    """'1,3,4' -> [1, 3, 4]; a single number is fine too."""
    try:
        slots = [int(part.strip()) for part in text.split(",") if part.strip()]
    except ValueError:
        raise SystemExit(f"Cannot read the slot list {text!r}: expected numbers, e.g. 1,3,4")
    if not slots:
        raise SystemExit("The slot list is empty - nothing to sync.")
    for slot in slots:
        if not SLOT_MIN <= slot <= SLOT_MAX:
            raise SystemExit(f"Save slot {slot} out of range {SLOT_MIN}..{SLOT_MAX}.")
    return slots


def game_is_running():
    import hk_multibox
    return bool(hk_multibox.game_processes())


def backup(path, save_dir=None):
    """A timestamped copy next to the file; returns the backup path or None."""
    if not os.path.exists(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path + f".bak-{stamp}"
    shutil.copy2(path, target)
    return target


def list_slots(save_dir=None):
    save_dir = save_dir or DEFAULT_SAVE_DIR
    print(f"Save folder: {save_dir}")
    for slot in range(SLOT_MIN, SLOT_MAX + 1):
        path = slot_path(slot, save_dir)
        if os.path.exists(path):
            size = os.path.getsize(path) / 1024
            when = time.strftime("%d.%m.%Y %H:%M", time.localtime(os.path.getmtime(path)))
            print(f"  slot {slot}: user{slot}.dat  {size:7.1f} KB  {when}")
        else:
            print(f"  slot {slot}: user{slot}.dat  (no file - an empty slot)")


def sync_slots(source, targets, save_dir=None, game_running=None):
    """Copies user<source>.dat over every user<target>.dat, backing up first.

    Returns the list of (target, backup_path) pairs. Refuses to guess: the
    game must be closed, the source file must exist, targets must differ from
    the source and from each other.
    """
    save_dir = save_dir or DEFAULT_SAVE_DIR
    if game_running is None:
        game_running = game_is_running
    if game_running():
        raise SystemExit("The game is running - close every window first "
                         "(python tools/multibox/hk_multibox.py stop).")
    if source in targets:
        raise SystemExit("The source slot must not be among the targets.")
    if len(set(targets)) != len(targets):
        raise SystemExit("The target list has duplicates.")

    source_path = slot_path(source, save_dir)
    if not os.path.exists(source_path):
        raise SystemExit(f"Slot {source} has no save file ({source_path}) - nothing to copy from.")

    done = []
    for slot in targets:
        target_path = slot_path(slot, save_dir)
        bak = backup(target_path, save_dir)
        shutil.copy2(source_path, target_path)
        if bak:
            print(f"  slot {slot}: overwritten (the old file is in {os.path.basename(bak)})")
        else:
            print(f"  slot {slot}: created (the slot was empty)")
        done.append((slot, bak))
    print(f"Done: slot {source} copied to {', '.join(str(s) for s in targets)} "
          f"({os.path.basename(source_path)} is {os.path.getsize(source_path) / 1024:.1f} KB).")
    return done


def main():
    parser = argparse.ArgumentParser(
        prog="sync_saves",
        description="Make every parallel window boot from the same hero: "
                    "copy one save slot over the others (with backups).")
    parser.add_argument("--from", dest="source", type=int, metavar="N",
                        help="the slot to copy FROM (e.g. your main save, 2)")
    parser.add_argument("--to", dest="targets", metavar="1,3,4",
                        help="comma-separated slots to copy INTO")
    parser.add_argument("--list", action="store_true", help="show the slots and exit")
    args = parser.parse_args()

    if args.list:
        list_slots()
        return 0
    if not args.source or not args.targets:
        parser.print_help()
        return 1
    sync_slots(args.source, parse_slots(args.targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
