# Named-pipe protocol harness that runs without the game

`hkpipesim` is a small C# server that replicates the mod's transport
(`Mod/AiTrainHK/AiDataExporter.cs`): hello, telemetry stream, one-shot
events (`boss_list` / `boss_selected` / `command_error`) and command intake
(`restart`, `teleport`, `set_boss`, `set_gate`, `boss`, `bosses`, `warp`).

It lets you run `hk_pipe.py`, `bosses.py` and `teleport.py` end to end over a
real Windows named pipe — without Hollow Knight.

The project **includes `Mod/AiTrainHK/Win32Pipe.cs` as the very same file**, not a
copy: the harness exercises exactly the code that runs in the game.

## Three bugs found along the way

The first two were found by this harness, the third by a running game with the new
mod. All three looked the same: "the mod is silent, commands never arrive".

1. **`PipeOptions.None` (synchronous handle).** While a blocking `ReadAsync` is
   pending, the next `WriteLine` hangs forever. In .NET this is cured by
   `PipeOptions.Asynchronous`.
2. **`open(path, "r+b")` on the client.** Gives a `BufferedRandom` that hands back
   the first chunk it read and then hangs forever on the next `readline()`.
   Cured by raw `os.read` / `os.write`.
3. **Mono cannot do `NamedPipeServerStream` at all.** In the game's
   `System.Core.dll` (mscorlib 4.6.57) every public constructor of the type
   collapses into two stubs:
   `..ctor(string, PipeDirection, int, PipeTransmissionMode, PipeOptions, int, int)`
   and `..ctor(..., PipeSecurity, HandleInheritability, PipeAccessRights)` — both
   throw `NotImplementedException`. In the game this produced an endless
   `[AI] Pipe slot 0: error — The method or operation is not implemented.`
   (the log line was in Russian at that point; it is quoted here translated).
   The fix is to bring the server up directly through `kernel32` (`Win32Pipe.cs`).
   The diagnosis came from IL analysis, without launching the game:
   `System.Reflection.Metadata` over `System.Core.dll` shows which methods are stubs.

Because of the third item `PipeOptions` is no longer used, but the first bug is
still the reason why `Win32Pipe` works **without** overlapped I/O and without
pending operations: one thread per client strictly writes, peeks
(`PeekNamedPipe`) and reads in turn. No operation on the handle is ever left
incomplete.

## The harness must not reach the game

At first the mock listened on the production name `AiTrainHK`. The mistake surfaced
during a live run: the game had claimed that name, the mock could not create the
pipe (and silently reconnected in a loop), and the test connected **to the mod of
the running game** — and went off teleporting the knight: `boss GG_Hornet_1`, then
`boss GG_No_Such_Boss_Scene`, in response to which the mod dutifully went and loaded
a non-existent scene. The test stopped there and the game survived (the watchdog
pulled the knight out), but this must never be done.

Now it is impossible by construction:

- the mock listens on a **different** name — `hk_ai_mod_sim` (overridable via `HK_PIPE_NAME`);
- `test_pipe.py` sets `HK_PIPE_NAME=hk_ai_mod_sim` before importing `hk_pipe`;
- the mock adds the `"server": "hkpipesim"` field to hello, and the test **stops
  with exit code 2** if it is missing: that means the mod is on the other end, not the mock.

The same run exposed a divergence between the mock and the mod: for an unfamiliar
`gg_*` name the mod does not answer `command_error` but passes it through as a scene
(rule 7 in `TryResolveBoss`) — "maybe the scene exists". The mock answered with an
error, the test "confirmed" that, and the check was a lie. Now the mock replicates
the rule and the test covers both cases: [6] a garbage query → `command_error`,
[6b] an unknown `gg_*` → load attempt.

## A client that stops reading must not eat a slot

A client that connects and then never reads makes `WriteFile` block as soon as the pipe's
out buffer is full (4 KB in the mod) — on a plain synchronous handle there is no timeout to
fall back on. Left alone, that slot thread would never get back to `ConnectNamedPipe` and the
slot would be lost for good: a couple of frozen debuggers, and training has nothing left to
connect to.

The mod guards against this with a per-slot watchdog (`PipeSlotWatchdogLoop` in
`AiDataExporter.cs`): a write that has been in flight longer than `WRITE_STUCK_MS` (3000 ms)
is cancelled with `CancelSynchronousIo` (`Win32Pipe.CancelBlockingWrite`); the write then
fails, the slot closes the handle and creates a fresh instance.

That primitive is covered without the game:

```powershell
dotnet run --project tests/pipe_sim/pipe_sim.csproj -- --selftest-stuck
```

The self-test creates a server instance, connects a `NamedPipeClientStream` that never reads,
writes 64 KB (which blocks), cancels that write from the "watchdog" thread, and requires that
the write really was blocked, that the cancellation was issued, that the write returned and
that it returned failure. Success is `STUCK-CLIENT SELFTEST PASSED` and exit code 0.

## Running

Requires .NET SDK 8+ (the harness projects are set to `RollForward=LatestMajor`, so
they also run on a machine that only has .NET 6/10, without installing the .NET 8
runtime) and Python 3.10+.

One command does all three steps below, plus the stuck-client selftest — this is what CI runs:

```powershell
python tests/run_pipe_harness.py
```

It generates the registry, builds the mock, runs `--selftest-stuck`, then starts the mock with
its stdin held open (the mock exits on stdin EOF, which is why the manual recipe below needs the
mock to keep running in its own terminal), waits for it to report its registry, runs the checks
and shuts the mock down.

Manually, if you want to poke at the mock yourself:

```powershell
# 1. Boss registry from bosses.py in the boss_list event format
python tests/pipe_sim/gen_boss_list.py

# 2. Mock server for the mod (in a separate terminal or in the background)
dotnet run --project tests/pipe_sim/pipe_sim.csproj -- tests/pipe_sim/bosses.json

# 3. Integration test
python tests/pipe_sim/test_pipe.py
```

Success is `ALL CHECKS PASSED` and exit code 0.

The test covers: hello and the protocol version, the telemetry stream and the
monotonicity of `seq`, `wait_for_fresh`, the `bosses` command and registry
comparison (`teleport.py --verify`), `boss <scene>` with waiting for the fight to
start, `command_error` for an unrecognised boss (and separately — passing `gg_*`
through as a scene), restart, `set_boss`/`set_gate`, `teleport_to` by alias and
`warp`.

**Important:** an event must not replace the latest telemetry (otherwise an RL step
would get JSON without `hp`/`x`/`y`). The mod marks events with the `"event": 1`
field and the client does not write such messages into `_latest` — the test checks
this.

The simulator is not the mod: it does not exercise game logic (scene transitions,
the watchdog, knight teleporting). That still has to be run in the game.
