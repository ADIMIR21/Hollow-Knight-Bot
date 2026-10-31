# Multi-window training - the plan

One game window per training process, all windows running from the single
Steam install via `tools/multibox/`. This document is the working plan for
the `multi-window-training` branch.

## How the pieces fit

```
instance 1 (window)  --pipe hk_ai_mod   <-- python train.py (HK_PIPE_NAME=hk_ai_mod)
instance 2 (window)  --pipe hk_ai_mod_2 <-- python train.py (HK_PIPE_NAME=hk_ai_mod_2)
instance N (window)  --pipe hk_ai_mod_N <-- python train.py (HK_PIPE_NAME=hk_ai_mod_N)
```

- `tools/multibox/hk_multibox.py` owns the windows: `launch N` starts them
  sequentially (each window's pipe is the "really up" signal), `stop` closes
  everything.
- The mod reads `PIPE_SUFFIX` on start; the launcher sets it per window.
- `hk_pipe.py` keeps one shared client per process, so one `train.py` process
  per window is the correct topology - processes never share a pipe client.
- Hardware envelope: ~600 MB RAM per game window plus the trainer process.
  4 windows is the comfortable baseline, 6-8 the practical ceiling.

## What collides today (why this is a branch, not a one-liner)

1. **Checkpoint folders**: `train.py` writes to `models/ppo_hk/<scene>/`.
   Two processes training the same scene would corrupt each other's
   checkpoints and vecnormalize statistics.
2. **The progress journal**: one shared `logs/progress.txt`; parallel
   `train.py` processes would interleave lines and break `progress_log.py`
   parsing assumptions (one active run per file).
3. **The save folder**: all windows share the one
   `%USERPROFILE%\AppData\LocalLow\Team Cherry\Hollow Knight` (saves live in
   the user profile, not in the install), so they must not boot from the same
   slot. `train.py` auto-loads a slot from the main menu (`load <slot>`,
   `--save-slot` / `HK_SAVE_SLOT`, default 2); an orchestrator has to give
   every window its own slot. The training flow restarts and teleports rather
   than playing through menus, but simultaneous writes from several windows
   are still an unverified hazard.
4. **Bootstrap races**: `setup`/`deploy_mod.ps1` refuse to run while the game
   runs - an orchestrator must respect that ordering.

## Phases

### Phase 1 - integration (this commit)

`tools/multibox/` joins the repository: the pipe-aware launcher, its README,
and this plan. Windows can already be launched and stopped by hand, and a
single `train.py` can already pick a window with `HK_PIPE_NAME`.

### Phase 2 - per-run namespacing in `train.py` (done)

- `--pipe <name>` flag (default `HK_PIPE_NAME` as today) so the training
  process and the launcher agree on the window without env var gymnastics.
- `--tag <name>` flag: when set, training writes to
  `models/ppo_hk/<scene>__<tag>/` and journals to `logs/progress__<tag>.txt`.
  Two runs of the same boss can then proceed in parallel without touching
  each other's checkpoints. No tag = today's behaviour, byte for byte.
- `progress_log.py`/`plot_progress.py` learn to accept the per-tag journal
  path (flag, not a default change).
- Menu bootstrap: the client now polls the pipe instead of leaving a ReadFile
  pending (a pending read on a synchronous pipe handle blocks every later
  command write, and the main menu publishes nothing - the write would hang
  forever), and `train.py` auto-loads the save slot from the menu
  (`bosses.ensure_save_loaded`, `--save-slot` / `HK_SAVE_SLOT`, default 2).
- `hk_run_config.py` fingerprint is untouched - the tag namespacing must not
  change any training value.

### Phase 3 - the orchestrator (done, variant B proven live)

**Decision: every window trains the same boss.** The maintainer wants all
windows on one boss, so the target topology is *one trainer over N windows*:
`tools/multibox/train_multi.py` opens one pipe per window and exposes them to
a single PPO run as N parallel sub-environments (a SubprocVecEnv: one worker
process per window, so the one-shared-client-per-process rule holds). All
N windows share the boss, the timescale and the run's checkpoints/journal;
sample throughput scales with the number of windows. The per-window `--tag`
variant (`--variant a`, N independent `train.py` processes) stays for
splitting the *arena list* across windows.

**Same-hero rule.** Every window must boot from the same hero content:
episodes from a maxed-up hero and from a fresh one are incommensurable, so all
slots must hold identical saves. Windows still use *different slots* (each
reads its own `userN.dat` at boot), but with the same content. Before a
session, sync once (the tool backs up every overwritten slot):

    python tools/multibox/sync_saves.py --list
    python tools/multibox/sync_saves.py --from 2 --to 1,3,4

`tools/multibox/sync_saves.py` refuses to run while the game is open and has
unit tests (`tests/test_save_sync.py`).

Proven live (four windows, False Knight, `--smoke-steps 2000`): all four
windows loaded the synced hero and fought in the same PPO rollout while
unfocused - the mod sets `Application.runInBackground = true`, so a window
does not need the foreground to keep simulating. One command starts it:

    python tools/multibox/train_multi.py start          # default 4-window plan

A smoke run is isolated in `<scene>__smoke` and cannot touch real
checkpoints; `stop`/`status` control the running orchestrator.

Fix in this phase: `hk_gym.reset()` now waits for the fight scene after a
restart (up to ~75 s, covering the mod's deferred restart) instead of
returning an observation mid-transition - previously some windows started
off-boss (the hero was still loading or dead) and the bot acted in the wrong
place.

Still open: a window that dies takes the whole variant B rollout down (a
SubprocVecEnv worker cannot be replaced); the orchestrator restarts pairs
only in variant A. If screen capture is ever enabled, windows must also be
tiled and captured per instance - the telemetry-only observation does not
need it today.

## Known issue (not fixed yet)

**One death serializes all windows.** `hk_gym.reset()` waits for the fight
scene to come up after a death/rebirth, and `reset()` runs inside
`SubprocVecEnv.step()`, so while one window is reborn the other three stop
stepping. **After rebirth one window may not teleport** to the boss - the
observation is returned while the hero is still in the death/dream-return
scenario, so the bot acts in the wrong place.

**Planned fix:** make `reset()` non-blocking - fire the restart command and
return immediately; each worker keeps stepping with `action 0` (buttons
released) until its own fight scene is really up, so one window's death no
longer stops the others, and no window acts before it is in the arena.


The orchestrator reads a small run-plan file (JSON), e.g.
`tools/multibox/plan_4windows.json`:

```json
[{"window": 1},
 {"window": 2},
 {"window": 3},
 {"window": 4}]
```

Pipes and save slots derive from the window number by default
(`hk_ai_mod`, `hk_ai_mod_2`, ...; slots cycle 2-3-4-1). It then: launches the
windows (`hk_multibox`), waits for each pipe, and - variant B (the goal) -
feeds all pipes into one trainer; variant A (interim) starts one `train.py`
per window with the right `--pipe`/`--tag`/`--save-slot`. Variant A watches
the processes and restarts a window+trainer pair that died (a trainer alone
is pointless without its window). Subcommands: `start <plan>`, `stop`,
`status`.

### Phase 4 - the save-file question (partially verified)

The slot numbering of `load <slot>` is settled: the game formats the slot
index straight into the file name (`Platform.GetSaveSlotFileName` ->
`user{0}.dat`, IL probe), and live `load 2` opened the game into the world of
the user2.dat save.

**Mid-run writes are real:** a live smoke rewrote `user2.dat` while training
was running, so the game writes the save folder on its own events, not only
on exit. Until the write points are fully mapped: assign each window its own
slot (via `sync_saves.py` copies of one hero), back up the save folder before
each long parallel session, and never hand-play in parallel with training.

### Phase 5 - scale-out

Prove the ladder: 2 windows (different bosses) -> 4 -> 6. Watch free RAM and
per-window step rate (a slower window is visible in the journals as fewer
steps per second). Stay at `timescale fast` (2x) per window; do not stack
`x4` on several windows on one GPU.

## Non-goals

- No split-screen/UI fusion of windows - they stay independent.
- No checkpoint sharing or policy sync between windows: independent PPO runs
  per boss, compared through their journals.
