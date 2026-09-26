# -*- coding: utf-8 -*-
"""Runs the whole named-pipe harness: the mock mod, the stuck-client selftest and the
integration checks (tests/pipe_sim/test_pipe.py) - the way CI does it, with one command:

    python tests/run_pipe_harness.py

The boring part it takes over: the mock is a .NET program that exits as soon as its stdin
reaches EOF, which is exactly what happens when a harness starts it from a pipeline (that is
why the manual instructions say "keep the terminal open"). Here the mock is started with an
open stdin, waits until it reports its registry, then the checks run against it and the mock
is shut down afterwards. Windows only: the transport is real Win32 named pipes.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SIM_DIR = os.path.join(HERE, "pipe_sim")
CSPROJ = os.path.join(SIM_DIR, "pipe_sim.csproj")
REGISTRY_JSON = os.path.join(SIM_DIR, "bosses.json")
TEST_SCRIPT = os.path.join(SIM_DIR, "test_pipe.py")
EXE_CANDIDATES = [
    os.path.join(SIM_DIR, "bin", "Release", "net8.0", "hkpipesim.exe"),
    os.path.join(SIM_DIR, "bin", "Debug", "net8.0", "hkpipesim.exe"),
]

# The harness listens on its own pipe: with the default name the checks would connect to the
# pipe of a running game and start driving the mod instead of the mock.
PIPE_NAME = "hk_ai_mod_sim"
TEST_TIMEOUT = 900.0


def child_env():
    env = dict(os.environ)
    env["HK_PIPE_NAME"] = PIPE_NAME
    return env


def run(cmd, **kwargs):
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT, env=child_env(), **kwargs)


def find_exe():
    for path in EXE_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def pump(stream, sink):
    """Keeps reading the mock's output (so it can never block on a full pipe) into sink."""
    for line in stream:
        sink.append(line.rstrip())
        if len(sink) > 400:
            del sink[:200]


def start_mock(exe):
    process = subprocess.Popen(
        [exe, REGISTRY_JSON],
        cwd=ROOT,
        env=child_env(),
        stdin=subprocess.PIPE,          # held open: EOF would make the mock exit
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines = []
    threading.Thread(target=pump, args=(process.stdout, lines), daemon=True).start()

    deadline = time.time() + 30.0
    while time.time() < deadline:
        if process.poll() is not None:
            print("\n".join(lines[-20:]))
            raise SystemExit(f"the mock exited right away (code {process.returncode})")
        if any("registry loaded" in line for line in lines):
            time.sleep(0.5)             # the slot threads start right after that line
            return process, lines
        time.sleep(0.1)

    print("\n".join(lines[-20:]))
    process.kill()
    raise SystemExit("the mock did not report its registry within 30 s")


def stop_mock(process, lines, failed):
    if process.poll() is None:
        if process.stdin is not None:
            try:
                process.stdin.close()   # graceful: the mock stops on stdin EOF
            except OSError:
                pass
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
    if failed and lines:
        print("\n---- mock output ----")
        print("\n".join(lines[-40:]))


def main(argv):
    # In CI stdout is a pipe, and the checks below run as a child process writing to the same
    # file descriptor: line buffering keeps this runner's own messages in the right order.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    if os.name != "nt":
        print("SKIP: the pipe harness needs Windows (real Win32 named pipes).", flush=True)
        return 0

    build = "--no-build" not in argv

    if build:
        code = run([sys.executable, os.path.join(SIM_DIR, "gen_boss_list.py")])
        if code != 0:
            print("FAILED: the registry for the mock was not generated")
            return code
        code = run(["dotnet", "build", CSPROJ, "-c", "Release", "--nologo", "-v", "quiet"])
        if code != 0:
            print("FAILED: the mock did not build")
            return code

    exe = find_exe()
    if exe is None:
        print("FAILED: hkpipesim.exe not found - build the harness first (drop --no-build)")
        return 1

    print("\n=== [1/3] stuck-client selftest (a client that stops reading must not eat a slot) ===")
    code = run([exe, "--selftest-stuck"])
    if code != 0:
        print("FAILED: the stuck-client selftest did not pass")
        return code

    print("\n=== [2/3] mock mod (pipe %s, registry %s) ===" % (PIPE_NAME, os.path.basename(REGISTRY_JSON)))
    mock, lines = start_mock(exe)
    code = 1
    try:
        print("\n=== [3/3] integration checks ===")
        test = subprocess.Popen([sys.executable, TEST_SCRIPT], cwd=ROOT, env=child_env())
        try:
            code = test.wait(timeout=TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            test.kill()
            test.wait(timeout=10.0)
            code = 1
            print("FAILED: the integration checks did not finish in %.0f s" % TEST_TIMEOUT)
    finally:
        stop_mock(mock, lines, code != 0)

    print("\n" + ("PIPE HARNESS PASSED" if code == 0 else "PIPE HARNESS FAILED (exit %d)" % code))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
