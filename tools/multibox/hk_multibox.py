"""HK Multi-Box - run several Hollow Knight windows from ONE game install.

Every instance is a folder of Windows links that costs ~0 bytes:

    HK_Instance<N>/hollow_knight_<N>.exe   hardlink to the game exe
    HK_Instance<N>/hollow_knight_<N>_Data  junction to hollow_knight_Data
    HK_Instance<N>/MonoBleedingEdge        junction
    HK_Instance<N>/UnityPlayer.dll         hardlink
    HK_Instance<N>/steam_appid.txt         "367520" (run without the Steam client)

The different exe name is what lets several windows run side by side: it
bypasses the game's single-instance check and the Steam DRM wrapper that
kills a direct launch of the original exe. Links share the same underlying
files, so game updates and mod deploys are picked up by every instance
automatically.

The AiTrainHK mod (if installed) reads PIPE_SUFFIX on start, so each window
serves its own named pipe: hk_ai_mod (instance 1), hk_ai_mod_2, ...

Usage:
    python hk_multibox.py setup 6       # create instances 1..6
    python hk_multibox.py launch 4      # safely launch 4 windows
    python hk_multibox.py stop          # close every game window
    python hk_multibox.py list          # instances + disk usage
    python hk_multibox.py status        # running windows + mod pipes

Requires: Windows, Python 3.9+, a legally owned copy of Hollow Knight
(Steam or GOG). The game and this tool must be on the same NTFS volume for
hardlinks to work.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Instance folders must live on the SAME volume as the game (hardlinks cannot
# cross volumes), so the default is the historical location on D: - not this
# script's folder, which may sit on another drive inside the repository.
# Override with the HKMB_INSTANCES environment variable.
INSTANCES_ROOT = Path(os.environ.get("HKMB_INSTANCES", r"D:\HKMultiInstance"))
STEAM_APPID = "367520"
PIPE_BASE = "hk_ai_mod"
MAX_REASONABLE = 10
DEFAULT_COUNT = 4
DEFAULT_WAIT = 60  # seconds to wait for one window to come up

# ---------------------------------------------------------------- game dir


def find_game_dir():
    """Locate the Hollow Knight install: $HK_GAME_DIR, Steam registry, defaults."""
    env = os.environ.get("HK_GAME_DIR")
    if env and (Path(env) / "hollow_knight.exe").exists():
        return Path(env)

    candidates = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\WOW6432Node\Valve\Steam") as key:
            steam = winreg.QueryValueEx(key, "InstallPath")[0]
        candidates.append(Path(steam))
        vdf = Path(steam) / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            for line in vdf.read_text(encoding="utf-8", errors="ignore").splitlines():
                if '"path"' in line:
                    candidates.append(Path(line.split('"')[3].replace("\\\\", "\\")))
    except OSError:
        pass

    candidates += [Path(r"C:\Program Files (x86)\Steam"),
                   Path(r"C:\Program Files\Steam")]
    for lib in candidates:
        game = lib / "steamapps" / "common" / "Hollow Knight"
        if (game / "hollow_knight.exe").exists():
            return game
    return None


# ------------------------------------------------------------- processes


def game_processes():
    """(pid, name) of every running hollow_knight*/UnityCrashHandler64 process."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process hollow_knight*,UnityCrashHandler64* -ErrorAction SilentlyContinue "
         "| ForEach-Object { \"{0}|{1}\" -f $_.Id, $_.ProcessName }"],
        capture_output=True, text=True, check=True).stdout
    procs = []
    for line in out.splitlines():
        line = line.strip()
        if "|" in line:
            pid, name = line.split("|", 1)
            if pid.isdigit():
                procs.append((int(pid), name))
    return procs


def mod_pipes():
    try:
        return {p for p in os.listdir("//./pipe/") if p.startswith(PIPE_BASE)}
    except OSError:
        return set()


# --------------------------------------------------------------- instances


def instance_dir(n):
    return INSTANCES_ROOT / f"HK_Instance{n}"


def instance_exe(n):
    return instance_dir(n) / f"hollow_knight_{n}.exe"


def create_instance(n, game_dir):
    inst = instance_dir(n)
    if instance_exe(n).exists():
        return False  # already exists

    inst.mkdir(exist_ok=True)
    import _winapi  # noqa: moved here so non-Windows gets a clean error earlier

    os.link(game_dir / "hollow_knight.exe", inst / f"hollow_knight_{n}.exe")
    _winapi.CreateJunction(str(game_dir / "hollow_knight_Data"),
                           str(inst / f"hollow_knight_{n}_Data"))
    mono = game_dir / "MonoBleedingEdge"
    if mono.exists():
        _winapi.CreateJunction(str(mono), str(inst / "MonoBleedingEdge"))
    os.link(game_dir / "UnityPlayer.dll", inst / "UnityPlayer.dll")
    crash = game_dir / "UnityCrashHandler64.exe"
    if crash.exists():
        os.link(crash, inst / "UnityCrashHandler64.exe")
    (inst / "steam_appid.txt").write_text(STEAM_APPID)
    return True


def real_bytes(path):
    """Disk space of private files only (junctions and hardlinks share data)."""
    total = 0

    def walk(directory):
        nonlocal total
        for entry in os.scandir(directory):
            if entry.is_dir(follow_symlinks=False):
                if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                    continue  # junction - shared data, costs nothing
                walk(entry.path)
            else:
                # os.stat, not entry.stat(): the DirEntry cache on Windows
                # reports st_nlink = 0 for every file.
                st = os.stat(entry.path)
                if st.st_nlink > 1:
                    continue  # hardlink - shares clusters with the original
                total += st.st_size

    walk(str(path))
    return total


def cmd_setup(args):
    game = find_game_dir()
    if game is None:
        print("Hollow Knight not found. Set the HK_GAME_DIR environment variable.")
        return 1
    if game_chars_are_on_other_volume(game):
        print("The game must be on the same volume as this tool (hardlink limit).")
        return 1
    procs = game_processes()
    if procs:
        print("Close all game windows first (running:", ", ".join(p[1] for p in procs), ")")
        return 1

    print(f"Game: {game}")
    for n in range(1, args.count + 1):
        try:
            created = create_instance(n, game)
        except OSError as e:
            print(f"  instance {n}: FAILED ({e})")
            return 1
        state = "created" if created else "already exists"
        print(f"  instance {n}: {state}")
    return 0


def game_chars_are_on_other_volume(game):
    try:
        return os.stat(str(game)).st_dev != os.stat(str(INSTANCES_ROOT)).st_dev
    except (OSError, AttributeError):
        return False


def cmd_list(args):
    total_real = 0
    found = False
    for inst in sorted(INSTANCES_ROOT.glob("HK_Instance*")):
        found = True
        data = next(inst.glob("*_Data"), None)
        linked = data is not None and os.path.realpath(str(data)) != str(data)
        real = real_bytes(inst)
        total_real += real
        kind = "LINKED" if linked else "COPY (not created by this tool)"
        print(f"  {inst.name:<15} [{kind}]  {real / 1024:8.1f} KB private files")
    if not found:
        print("  no instances yet - run: python hk_multibox.py setup 4")
    if total_real:
        print(f"  total private files: {total_real / 1024 / 1024 / 1024:.2f} GB")
    return 0


# ----------------------------------------------------------------- launch


def wait_for_window(proc, n, wait_s):
    """Wait until the window's mod pipe appears (the window is really up)."""
    pipe = PIPE_BASE if n == 1 else f"{PIPE_BASE}_{n}"
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return "died"
        if pipe in mod_pipes():
            return "ok"
        time.sleep(1.0)
    return "no-pipe"


def launch_one(n, wait_s):
    exe = instance_exe(n)
    if exe is None:
        return None, "missing"
    env = os.environ.copy()
    env["PIPE_SUFFIX"] = "" if n == 1 else f"_{n}"
    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env)
    state = wait_for_window(proc, n, wait_s)
    return proc, state


def cmd_launch(args):
    count = min(args.count, MAX_REASONABLE)
    game = find_game_dir()
    if game is None:
        print("Hollow Knight not found. Set the HK_GAME_DIR environment variable.")
        return 1

    missing = [n for n in range(1, count + 1) if not instance_exe(n).exists()]
    if missing:
        print(f"Creating missing instances: {missing}")
        for n in missing:
            try:
                create_instance(n, game)
            except OSError as e:
                print(f"  instance {n}: FAILED ({e})")
                return 1

    print(f"Launching {count} window(s), up to {args.wait}s each, one at a time...")
    results = {}  # n -> (proc, state)
    for n in range(1, count + 1):
        print(f"  instance {n}: starting...", flush=True)
        proc, state = launch_one(n, args.wait)
        if proc is None:
            print(f"    missing (run setup first)")
            continue
        results[n] = (proc, state)
        if state == "ok":
            print(f"    up (PID {proc.pid})")
        elif state == "died":
            print(f"    FAILED - process exited (code {proc.returncode})")
        else:
            print(f"    started but no mod pipe after {args.wait}s (PID {proc.pid})")

    # one retry for the windows that failed to come up
    failed = [n for n, (_, s) in results.items() if s != "ok"]
    for n in failed:
        print(f"  instance {n}: retrying...", flush=True)
        old = results[n][0]
        if old.poll() is None:
            old.kill()  # a window without a pipe is no use, restart it clean
            time.sleep(2.0)
        proc, state = launch_one(n, args.wait)
        if proc is not None:
            results[n] = (proc, state)
            print(f"    {'up (PID %d)' % proc.pid if state == 'ok' else state}")

    up = [n for n, (_, s) in results.items() if s == "ok"]
    bad = {n: s for n, (_, s) in results.items() if s != "ok"}
    print(f"\nRunning: {len(up)}/{count}  ->  {up}")
    if bad:
        print(f"Problems: {bad}")
        return 2
    return 0


# ------------------------------------------------------------------- stop


def cmd_stop(args):
    procs = game_processes()
    if not procs:
        print("No game windows running.")
        return 0
    print("Closing:", ", ".join(f"{name}({pid})" for pid, name in procs))

    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process hollow_knight* -ErrorAction SilentlyContinue "
         "| ForEach-Object { [void]$_.CloseMainWindow() }"],
        capture_output=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not game_processes():
            break
        time.sleep(1.0)

    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process hollow_knight*,UnityCrashHandler64* -ErrorAction SilentlyContinue "
         "| Stop-Process -Force"],
        capture_output=True)
    time.sleep(1.0)
    left = game_processes()
    if left:
        print("Could not close:", left)
        return 1
    print("All game windows closed.")
    return 0


# ----------------------------------------------------------------- status


def cmd_status(args):
    procs = game_processes()
    if procs:
        print("Running windows:")
        for pid, name in procs:
            print(f"  {name} (PID {pid})")
    else:
        print("No game windows running.")
    pipes = sorted(mod_pipes())
    print(f"Mod pipes ({len(pipes)}): {', '.join(pipes) if pipes else 'none'}")
    return 0


# -------------------------------------------------------------------- cli


def main():
    parser = argparse.ArgumentParser(
        prog="hk_multibox",
        description="Run several Hollow Knight windows from one install.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup", help="create instances 1..N")
    p.add_argument("count", type=int, nargs="?", default=DEFAULT_COUNT)
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("launch", help="safely launch N windows")
    p.add_argument("count", type=int, nargs="?", default=DEFAULT_COUNT)
    p.add_argument("--wait", type=int, default=DEFAULT_WAIT,
                   help="seconds to wait for one window to come up")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("stop", help="close every game window")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("list", help="show instances and disk usage")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("status", help="running windows and mod pipes")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
