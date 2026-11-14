"""The pipe client's message bookkeeping, executed against a thread-free client.

hk_pipe.py runs in the unit tier on purpose: its import guards keep it stdlib-only
even on CI's Linux runner. What the tier had never covered is the counters the
whole step loop keys on - and the drift between them is how a real bug looked:
one-shot events (boss_list / command_error replies) consumed the message
counter without carrying any observation, so wait_for_fresh() handed the step
a stale frame while the mod was busy answering a command.

The client here is built without its background thread (_init_state), and the
lines are fed through _handle_line exactly as _read_loop would.
"""
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from hk_pipe import HKPipeClient  # noqa: E402


def make_client():
    """A client with initialized state and no reader thread, pipe, or fd."""
    client = HKPipeClient.__new__(HKPipeClient)
    client._init_state()
    client.verbose = False
    return client


def line(**fields):
    return json.dumps(fields).encode("utf-8")


FRAME = {"status": "fight", "hp": 9, "boss_hp": 260, "x": 1.0, "y": 2.0}


class CounterTest(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def test_a_frame_bumps_both_counters(self):
        self.client._handle_line(line(**FRAME))
        self.assertEqual(self.client.get_seq(), 1)
        self.assertEqual(self.client.get_frame_seq(), 1)
        self.assertEqual(self.client.get_telemetry(), FRAME)

    def test_an_event_bumps_only_the_message_counter(self):
        self.client._handle_line(line(**FRAME))
        self.client._handle_line(line(status="command_error", event=1, command="gg_x"))
        self.assertEqual(self.client.get_seq(), 2)
        self.assertEqual(self.client.get_frame_seq(), 1,
                         "an event consumed the frame counter - a step would act on stale data")
        self.assertEqual(self.client.get_telemetry(), FRAME,
                         "an event replaced the latest observation frame")

    def test_a_service_status_is_a_frame_for_the_counter(self):
        # Service statuses (loading_scene, main_menu) ARE the latest message a
        # menu-bound caller should see; they land in _latest like frames do.
        self.client._handle_line(line(status="loading_scene"))
        self.assertEqual(self.client.get_frame_seq(), 1)
        self.assertEqual(self.client.get_telemetry().get("status"), "loading_scene")

    def test_garbage_is_ignored(self):
        self.client._handle_line(b"not json at all")
        self.client._handle_line(b"")
        self.assertEqual(self.client.get_seq(), 0)
        self.assertEqual(self.client.get_frame_seq(), 0)

    def test_the_hello_is_cached_and_cleared_on_reconnect_helper(self):
        self.client._handle_line(line(status="pipe_hello", protocol=3, mod_version="1"))
        self.assertEqual(self.client.protocol, 3)
        self.assertEqual(self.client.mod_version, "1")


class WaitForFreshTest(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def test_returns_immediately_when_a_frame_already_arrived(self):
        self.client._handle_line(line(**FRAME))
        seq = self.client.get_frame_seq()
        self.assertEqual(self.client.wait_for_fresh(seq, timeout=0.0), seq)
        self.client._handle_line(line(**FRAME))
        self.assertEqual(self.client.wait_for_fresh(seq, timeout=0.0),
                         self.client.get_frame_seq())

    def test_an_event_does_not_freshen_the_wait(self):
        # The red case: under the old counter an event here returned immediately,
        # handing the step a frame it had already consumed.
        self.client._handle_line(line(**FRAME))
        seq = self.client.get_frame_seq()
        self.client._handle_line(line(status="boss_list", event=1, count=0, bosses=[]))
        self.assertEqual(
            self.client.wait_for_fresh(seq, timeout=0.01), seq,
            "an event woke wait_for_fresh - the step would run on a stale frame")

    def test_a_new_frame_ends_the_wait(self):
        self.client._handle_line(line(**FRAME))
        seq = self.client.get_frame_seq()
        # Deliver a frame from another thread after a moment: the wait must end
        # before its timeout expires.
        import threading
        timer = threading.Timer(0.02, self.client._handle_line, args=(line(**FRAME),))
        timer.start()
        try:
            result = self.client.wait_for_fresh(seq, timeout=2.0)
        finally:
            timer.join()
        self.assertEqual(result, self.client.get_frame_seq())
        self.assertGreater(result, seq)


class WaitForStatusTest(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def test_after_seq_only_sees_newer_replies(self):
        self.client._handle_line(line(status="paused", event=1, time_scale=0))
        old = self.client.get_seq()
        # An old reply must not satisfy a wait started after it.
        self.assertIsNone(self.client.wait_for_status("paused", timeout=0.01, after_seq=old))
        self.client._handle_line(line(status="paused", event=1, time_scale=0))
        self.assertIsNotNone(self.client.wait_for_status("paused", timeout=0.01, after_seq=old))

    def test_wait_without_after_seq_sees_any_reply(self):
        self.client._handle_line(line(status="resumed", event=1))
        self.assertIsNotNone(self.client.wait_for_status("resumed", timeout=0.01))


class SharedClientTest(unittest.TestCase):
    """close_shared_client() must really forget the singleton.

    park_windows.py walks several windows in one process and re-points HK_PIPE_NAME
    between them: if the closed client survived (or the next one reused the old
    pipe), windows 2..N would silently talk to window 1. The clients here bind the
    harness pipe name only - never the production name (see AGENTS.md).
    """

    def setUp(self):
        self._old_name = os.environ.get("HK_PIPE_NAME")
        os.environ["HK_PIPE_NAME"] = "hk_ai_mod_sim"
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        import hk_pipe
        hk_pipe.close_shared_client()
        if self._old_name is None:
            os.environ.pop("HK_PIPE_NAME", None)
        else:
            os.environ["HK_PIPE_NAME"] = self._old_name

    def test_a_closed_client_is_stopped_and_not_returned_again(self):
        import hk_pipe
        first = hk_pipe.get_shared_client(verbose=False)
        hk_pipe.close_shared_client()
        self.assertTrue(first._stop,
                        "the closed client kept its reader thread running")
        second = hk_pipe.get_shared_client(verbose=False)
        self.assertIsNot(second, first,
                         "get_shared_client returned the closed singleton again")

    def test_the_next_client_lands_on_the_repointed_pipe(self):
        import hk_pipe
        hk_pipe.get_shared_client(verbose=False)
        hk_pipe.close_shared_client()
        os.environ["HK_PIPE_NAME"] = "hk_ai_mod_sim_2"
        second = hk_pipe.get_shared_client(verbose=False)
        self.assertEqual(second.pipe_path, "\\\\.\\pipe\\hk_ai_mod_sim_2",
                         "the client after close_shared_client() ignored the new HK_PIPE_NAME")

    def test_close_without_a_client_is_a_no_op(self):
        import hk_pipe
        hk_pipe.close_shared_client()
        self.assertIsNotNone(hk_pipe.get_shared_client(verbose=False))


if __name__ == "__main__":
    unittest.main()
