# -*- coding: utf-8 -*-
"""Named pipe client for the AiTrainHK mod (protocol v3).

The mod (C#) hosts the pipe server ``\\\\.\\pipe\\hk_ai_mod`` and broadcasts telemetry
as JSON lines (newline-delimited). Python opens the pipe through a RAW handle
(``os.open`` + ``os.read``/``os.write``): it reads telemetry in a background thread and
writes text commands to the same handle.

Why not ``open(path, "r+b")``: that gives a ``BufferedRandom``, which on a
Windows named pipe yields the first chunk read and then hangs forever on the
next ``readline()``. Raw ``os.read``/``os.write`` work correctly and give you
your own line-based parsing of the stream.

Mod message format (one line = one JSON object):
    {"status": "pipe_hello", "protocol": 3, "mod_version": "1"}   — on connect
    {"status": "fight", "restart_pending": 0, "scene": "GG_False_Knight", "hp": 9, ...}
    {"status": "main_menu" | "loading_scene" | "initialized" | ...}  — service statuses
    {"status": "boss_list", "event": 1, "count": 60, "bosses": [...]}   — reply to "bosses"
    {"status": "boss_selected", "event": 1, "scene": "...", ...}        — boss choice accepted
    {"status": "command_error", "event": 1, "command": "...", ...}      — command rejected

Telemetry arrives as a stream (~60/sec), so only the latest frame is kept.
One-shot events (boss_list/boss_selected/command_error) are marked with the
``"event": 1`` field, are cached by status and do NOT replace the latest telemetry —
they can be awaited via :meth:`HKPipeClient.wait_for_status`.

Commands Python -> mod (plain text, one line = one command):
    restart [scene] [gate]   — fast fight restart ( BeginSceneTransition )
    teleport                 — same as restart
    set_boss <scene>         — target boss scene (understands aliases)
    set_gate <gate>          — arena entry point
    boss <query>             — pick a pantheon boss and teleport to it
    bosses                   — send the boss registry as a boss_list event
    warp                     — return the knight to the arena gate without reloading the scene
"""

import json
import os
import threading
import time

# Pipe name. HK_PIPE_NAME overrides it for the test rig (tests/pipe_sim):
# the mock listens on hk_ai_mod_sim so it does not occupy the pipe of a running game.
# The constant is baked at import; the shared client (get_shared_client) resolves
# HK_PIPE_NAME lazily instead, so train.py can set it from --pipe inside main().
_DEFAULT_PIPE_NAME = "hk_ai_mod"
PIPE_PATH = "\\\\.\\pipe\\" + os.environ.get("HK_PIPE_NAME", _DEFAULT_PIPE_NAME)

# Minimum protocol version that has the pantheon commands.
REQUIRED_PROTOCOL = 3

_RETRY_OPEN_DELAY = 0.5
_READ_CHUNK = 65536
# Reader poll interval: the reader never leaves a ReadFile pending on the
# handle (see _read_loop), so it wakes up to check the queue at this rate.
_READ_POLL_INTERVAL = 0.005
# Guard against a flood of garbage with no newlines.
_MAX_LINE_BUFFER = 1 << 20

_O_BINARY = getattr(os, "O_BINARY", 0)

try:
    import ctypes as _ctypes
    import msvcrt as _msvcrt

    _PeekNamedPipe = _ctypes.windll.kernel32.PeekNamedPipe
    _PeekNamedPipe.argtypes = [_ctypes.c_void_p, _ctypes.c_void_p, _ctypes.c_uint,
                               _ctypes.POINTER(_ctypes.c_uint),
                               _ctypes.POINTER(_ctypes.c_uint),
                               _ctypes.POINTER(_ctypes.c_uint)]
    _PeekNamedPipe.restype = _ctypes.c_int
except (ImportError, AttributeError):   # non-Windows: tier-1 unit imports on CI
    _PeekNamedPipe = None


def _pipe_available(fd):
    """Bytes waiting in the pipe's inbound queue.

    None means the pipe is gone (the server closed its end) or the platform
    has no PeekNamedPipe (plain POSIX import on CI). The fd itself stays
    untouched, so no operation is ever left pending on the handle.
    """
    if _PeekNamedPipe is None:
        return None
    try:
        handle = _msvcrt.get_osfhandle(fd)
    except OSError:
        return None
    avail = _ctypes.c_uint(0)
    if not _PeekNamedPipe(handle, None, 0, None, _ctypes.byref(avail), None):
        return None
    return avail.value


class HKPipeClient:
    """Persistent connection to the mod pipe with auto-reconnect.

    Thread-safe: a background reader thread holds the latest parsed
    JSON (``_latest``), a monotonic message counter (``_seq``) and a cache
    of one-shot events keyed by the ``status`` field.
    """

    def __init__(self, pipe_path=PIPE_PATH, reconnect_delay=_RETRY_OPEN_DELAY, verbose=True):
        self.pipe_path = pipe_path
        self.reconnect_delay = reconnect_delay
        self.verbose = verbose

        self._cond = threading.Condition()
        self._latest = None          # latest telemetry frame (events excluded)
        self._last_any = None        # last message of any type (for debugging)
        self._seq = 0                # total number of messages received
        self._by_status = {}         # status -> last message with that status
        self._status_seq = {}        # status -> seq it arrived on
        self._hello = None           # mod hello message
        self._connected = False
        self._stop = False

        self._send_lock = threading.Lock()
        self._fd = None              # raw pipe handle (r+w)

        self._thread = threading.Thread(target=self._run, daemon=True, name="HKPipeClient")
        self._thread.start()

    # ------------------------------------------------------------------ API

    @property
    def is_connected(self):
        with self._cond:
            return self._connected

    @property
    def hello(self):
        """Mod hello message (None until it arrives)."""
        with self._cond:
            return self._hello

    @property
    def protocol(self):
        """Mod protocol version or None."""
        with self._cond:
            hello = self._hello
            return hello.get("protocol") if hello else None

    @property
    def mod_version(self):
        """Mod version from hello or None."""
        with self._cond:
            hello = self._hello
            return hello.get("mod_version") if hello else None

    def wait_connected(self, timeout=15.0):
        """Blocks until the pipe is open (or the timeout expires). True = handle is open."""
        deadline = time.perf_counter() + timeout
        with self._cond:
            while not self._connected:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def wait_hello(self, timeout=3.0):
        """Waits for the mod hello message (it carries the protocol version). None on timeout."""
        deadline = time.perf_counter() + timeout
        with self._cond:
            while self._hello is None:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            return self._hello

    def get_telemetry(self):
        """Latest parsed telemetry JSON or None (no link).

        One-shot events do NOT land here — only observation frames (and
        mod service statuses such as loading_scene/main_menu).
        """
        with self._cond:
            return self._latest

    def get_last_message(self):
        """Last mod message of any type, events included (for debugging)."""
        with self._cond:
            return self._last_any

    def get_seq(self):
        """Monotonic counter of received messages (replaces the old file mtime)."""
        with self._cond:
            return self._seq

    def get_status(self, status):
        """Last message with the given ``status`` or None."""
        with self._cond:
            return self._by_status.get(status)

    def get_status_seq(self, status):
        """Seq the last message with the given ``status`` arrived on (None — never seen).

        Companion to :meth:`wait_for_status` with ``after_seq``: remember the seq
        before sending a command so you wait for that command's reply, not an old one.
        """
        with self._cond:
            return self._status_seq.get(status)

    def wait_for_status(self, status, timeout=5.0, after_seq=None):
        """Waits for a message with the given ``status``. None on timeout.

        ``after_seq`` — return only a message newer than the given seq.
        """
        deadline = time.perf_counter() + timeout
        with self._cond:
            while True:
                seq = self._status_seq.get(status)
                if seq is not None and (after_seq is None or seq > after_seq):
                    return self._by_status.get(status)
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def wait_for_fresh(self, last_seq, timeout=0.15):
        """Waits for a NEW message from the mod. Returns the current seq.

        If nothing arrives within the timeout (menu/pause/no link) it returns
        the previous last_seq, like the old mtime-based wait_for_fresh_telemetry.
        """
        with self._cond:
            if self._seq != last_seq:
                return self._seq
            self._cond.wait(timeout)
            return self._seq

    def send_command(self, text):
        """Send a command to the mod ('restart GG_False_Knight door_dreamEnter' etc.).

        True — the line went into the pipe (the mod picks it up within a frame or two).
        """
        payload = (text.rstrip("\n") + "\n").encode("utf-8")
        with self._send_lock:
            fd = self._fd
            if fd is None:
                return False
            try:
                while payload:
                    written = os.write(fd, payload)
                    if written <= 0:
                        return False
                    payload = payload[written:]
                return True
            except OSError:
                # the pipe dropped mid-write — the reader thread will reconnect
                return False

    def stop(self):
        """Stops the client and unblocks the reader thread."""
        self._stop = True
        with self._send_lock:
            fd = self._fd
            self._fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        with self._cond:
            self._cond.notify_all()

    # ------------------------------------------------------------ internal

    def _log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    def _open_pipe(self):
        """Opens the pipe through a raw handle. None if the server is absent or not responding."""
        while not self._stop:
            try:
                # O_RDWR = GENERIC_READ|GENERIC_WRITE: the server is duplex for us.
                fd = os.open(self.pipe_path, os.O_RDWR | _O_BINARY)
                return fd
            except FileNotFoundError:
                pass          # the mod has not created the pipe yet (game not running)
            except OSError:
                pass          # ERROR_PIPE_BUSY and the like — keep waiting too
            time.sleep(self.reconnect_delay)
        return None

    def _run(self):
        while not self._stop:
            fd = self._open_pipe()
            if fd is None:
                break
            with self._send_lock:
                self._fd = fd
            self._set_connected(True)
            self._log(f"[PIPE] Connected: {self.pipe_path}")
            try:
                self._read_loop(fd)
            except OSError:
                pass
            finally:
                with self._send_lock:
                    self._fd = None
                try:
                    os.close(fd)
                except OSError:
                    pass
                # hello belongs to one specific connection — clear it so that
                # protocol/mod_version do not survive a reconnect.
                with self._cond:
                    self._hello = None
                self._set_connected(False)
            if self._stop:
                break
            self._log("[PIPE] Connection lost, reconnecting...")
            time.sleep(self.reconnect_delay)

    def _read_loop(self, fd):
        buffer = b""
        while True:
            # A ReadFile left pending on a synchronous pipe handle serializes
            # the handle: a command written by another thread then waits for
            # the read to complete. In a fight that delay is one telemetry
            # frame (~50 ms), but the main menu publishes nothing until a save
            # is loaded, so the write would hang forever. The mod's server
            # side avoids the same trap by never leaving an operation pending
            # on the handle (see Win32Pipe.cs) - the client now does too:
            # Peek first, and only Read when data is actually waiting.
            avail = _pipe_available(fd)
            if avail is None:
                if _PeekNamedPipe is None:
                    # POSIX import path (tier-1 units on CI): plain blocking
                    # read, the pending-read serialization is a Windows trap.
                    chunk = os.read(fd, _READ_CHUNK)
                else:
                    raise OSError("pipe is gone")
            elif avail == 0:
                if self._stop:
                    return
                time.sleep(_READ_POLL_INTERVAL)
                continue
            else:
                chunk = os.read(fd, min(avail, _READ_CHUNK))
            if not chunk:
                raise OSError("pipe eof")

            if buffer:
                buffer += chunk
            else:
                buffer = chunk

            while True:
                idx = buffer.find(b"\n")
                if idx < 0:
                    break
                line = buffer[:idx]
                buffer = buffer[idx + 1:]
                self._handle_line(line)

            if len(buffer) > _MAX_LINE_BUFFER:
                buffer = buffer[-1024:]

    def _handle_line(self, raw):
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            return
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return  # truncated line — skip it, the mod sends line by line
        with self._cond:
            self._seq += 1
            self._last_any = data
            status = data.get("status")
            if status:
                self._by_status[status] = data
                self._status_seq[status] = self._seq
                if status == "pipe_hello":
                    self._hello = data
            # One-shot events (marked "event": 1) do not replace the latest
            # telemetry: otherwise an RL step would get JSON without hp/x/y.
            if not data.get("event"):
                self._latest = data
            self._cond.notify_all()

    def _set_connected(self, value):
        with self._cond:
            self._connected = value
            self._cond.notify_all()


# ---------------- Per-process shared client ----------------
# The mod supports up to 4 clients, but one shared client is simpler and more
# reliable: the training loop reads telemetry while commands (restart/boss/warp)
# are sent by anyone through bosses.py — all over a single connection.

_shared_client = None
_shared_lock = threading.Lock()


def get_shared_client(verbose=True):
    """Per-process singleton of the pipe client.

    The pipe name is resolved from HK_PIPE_NAME at first use, not at import:
    train.py sets the variable from --pipe inside main(), long after the
    module-level imports have run.
    """
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            pipe_name = os.environ.get("HK_PIPE_NAME", _DEFAULT_PIPE_NAME)
            _shared_client = HKPipeClient(pipe_path="\\\\.\\pipe\\" + pipe_name,
                                          verbose=verbose)
        return _shared_client


def send_command(text):
    """Send a command to the mod through the shared client."""
    return get_shared_client().send_command(text)


def get_telemetry():
    """Latest telemetry through the shared client."""
    return get_shared_client().get_telemetry()


def is_connected():
    return get_shared_client().is_connected


if __name__ == "__main__":
    # Quick check: python hk_pipe.py — print live telemetry for 5 seconds.
    client = HKPipeClient()
    print("Waiting for the mod (\\\\.\\pipe\\hk_ai_mod), up to 10 seconds...")
    if not client.wait_connected(10.0):
        print("The mod did not respond. Is the game running? Is the AiTrainHK.dll mod installed?")
        raise SystemExit(1)

    hello = client.wait_hello(3.0) or {}
    print(f"Hello: protocol={hello.get('protocol')} mod_version={hello.get('mod_version')}")
    if (hello.get("protocol") or 0) < REQUIRED_PROTOCOL:
        print(f"WARNING: pantheon commands require protocol >= {REQUIRED_PROTOCOL}. "
              f"Update the mod DLL.")

    end = time.time() + 5.0
    last = -1
    while time.time() < end:
        seq = client.get_seq()
        if seq != last:
            last = seq
            print(seq, client.get_telemetry())
        time.sleep(0.05)
