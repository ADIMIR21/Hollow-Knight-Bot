"""The orchestrator's pid liveness probe must probe, never act.

The old probe was os.kill(pid, 0), a POSIX idiom. On Windows os.kill is not a
probe: on the documented Pythons this project trains under (3.11, per
requirements) any signal other than the two CTRL events - 0 included - calls
TerminateProcess, so "python train_multi.py status" stopped the orchestrator
it was reporting on, and _kill() murdered the process before its own taskkill
could run. On newer interpreters (3.14) the call happens to resolve to the
CTRL_C_EVENT branch (CTRL_C_EVENT == 0) and degenerates into the opposite bug:
it never raises for a dead pid, so the probe reports every pid as alive.

These tests run on both CI platforms: the dead-pid expectations fail under the
old code everywhere (killed outright on 3.11, false-positive on 3.14 - verified
on the maintainer's machine), and the Windows branch is exercised for real
there; CI (Linux) covers the guard clauses and the POSIX path.
"""
import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "multibox"))

import train_multi  # noqa: E402


class PidAliveTest(unittest.TestCase):
    def test_probing_our_own_pid_leaves_us_alive(self):
        # The regression case: under the old Windows code this line killed the
        # test process (exit code 0) before the assertion could run.
        self.assertTrue(train_multi._pid_alive(os.getpid()))
        self.assertTrue(train_multi._pid_alive(os.getpid()))

    def test_a_reaped_child_reports_dead(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()  # reaped: the pid cannot come back on either platform
        self.assertFalse(train_multi._pid_alive(child.pid))

    def test_guard_clauses(self):
        self.assertFalse(train_multi._pid_alive(None))
        self.assertFalse(train_multi._pid_alive(0))
        self.assertFalse(train_multi._pid_alive(-1))

    def test_kill_on_a_dead_pid_is_a_quiet_no(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(train_multi._kill(child.pid))

    def test_kill_without_a_pid_is_a_quiet_no(self):
        self.assertFalse(train_multi._kill(None))
        self.assertFalse(train_multi._kill(0))


if __name__ == "__main__":
    unittest.main()
