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
3. **The save file**: all windows share `user1.dat` (and the settings
   registry key). The training flow restarts and teleports rather than
   playing through menus, but simultaneous writes from several windows are
   still an unverified hazard.
4. **Bootstrap races**: `setup`/`deploy_mod.ps1` refuse to run while the game
   runs - an orchestrator must respect that ordering.

## Phases

### Phase 1 - integration (this commit)

`tools/multibox/` joins the repository: the pipe-aware launcher, its README,
and this plan. Windows can already be launched and stopped by hand, and a
single `train.py` can already pick a window with `HK_PIPE_NAME`.

### Phase 2 - per-run namespacing in `train.py`

- `--pipe <name>` flag (default `HK_PIPE_NAME` as today) so the training
  process and the launcher agree on the window without env var gymnastics.
- `--tag <name>` flag: when set, training writes to
  `models/ppo_hk/<scene>__<tag>/` and journals to `logs/progress__<tag>.txt`.
  Two runs of the same boss can then proceed in parallel without touching
  each other's checkpoints. No tag = today's behaviour, byte for byte.
- `progress_log.py`/`plot_progress.py` learn to accept the per-tag journal
  path (flag, not a default change).
- `hk_run_config.py` fingerprint is untouched - the tag namespacing must not
  change any training value.

### Phase 3 - the orchestrator

`tools/multibox/train_multi.py`: a small run-plan file (JSON) mapping
instance -> boss/scene, entry gate and timescale, e.g.:

```json
[{"window": 1, "boss": "False Knight", "timescale": "fast"},
 {"window": 2, "boss": "hornet",       "timescale": "fast"},
 {"window": 3, "boss": "gruz_mother",  "timescale": "fast"}]
```

It then: launches the windows (`hk_multibox`), starts one `train.py` per
window with the right `HK_PIPE_NAME`/`--tag`, watches both processes, and
restarts a window+trainer pair that died (a trainer alone is pointless
without its window). Subcommands: `start <plan>`, `stop`, `status`.

### Phase 4 - the save-file question (verify before long parallel runs)

Determine what the training flow actually writes to `user1.dat` (the
teleport/restart path may never save mid-fight). Until verified: back up the
save folder before each long parallel session, and never hand-play in
parallel with training.

### Phase 5 - scale-out

Prove the ladder: 2 windows (different bosses) -> 4 -> 6. Watch free RAM and
per-window step rate (a slower window is visible in the journals as fewer
steps per second). Stay at `timescale fast` (2x) per window; do not stack
`x4` on several windows on one GPU.

## Non-goals

- No split-screen/UI fusion of windows - they stay independent.
- No checkpoint sharing or policy sync between windows: independent PPO runs
  per boss, compared through their journals.
