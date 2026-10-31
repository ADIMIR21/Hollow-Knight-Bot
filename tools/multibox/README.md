# tools/multibox - multi-window launcher

Launches several Hollow Knight windows from the ONE Steam install, using
hardlinks and NTFS junctions instead of folder copies (~0 bytes per window):

```
HK_Instance<N>/
  hollow_knight_<N>.exe    hardlink to the game exe (a different exe name is
                           what lets windows run side by side and bypasses
                           the Steam DRM wrapper)
  hollow_knight_<N>_Data   junction to hollow_knight_Data
  MonoBleedingEdge         junction
  UnityPlayer.dll          hardlink
  steam_appid.txt          "367520" - launches without the Steam client
```

The instance folders live on the game's volume (hardlinks cannot cross
volumes), by default in `D:\HKMultiInstance`; override with `HKMB_INSTANCES`.

## Commands

```
python tools/multibox/hk_multibox.py setup 4     create instances 1..4
python tools/multibox/hk_multibox.py launch 4    safe sequential launch
python tools/multibox/hk_multibox.py stop        close every window
python tools/multibox/hk_multibox.py list        instances + real disk usage
python tools/multibox/hk_multibox.py status      running windows + mod pipes
```

`launch` starts one window at a time and waits until the AiTrainHK mod of
that window has created its named pipe (`hk_ai_mod`, `hk_ai_mod_2`, ...) -
the pipe is the proof the window is really up - and retries a window once if
it fails. `stop` closes windows gracefully first and force-kills survivors
(including stray Unity crash handlers).

## Pipes

The mod reads `PIPE_SUFFIX` on start, so window N serves pipe
`hk_ai_mod` (N=1) / `hk_ai_mod_<N>`. `launch` sets the variable itself.
A training process picks its window with `HK_PIPE_NAME` - see
`docs/multi_window_training.md` for the full parallel-training plan.

## Training over several windows

`train_multi.py` is the phase 3 orchestrator: from one JSON plan it launches
the windows, waits for every pipe and starts the training.

```
python tools/multibox/train_multi.py start plan.json --boss hornet --timescale fast
python tools/multibox/train_multi.py stop
python tools/multibox/train_multi.py status
```

With no plan argument it uses the default 4-window plan
(`tools/multibox/plan_4windows.json`), so `python tools/multibox/train_multi.py
start` is a complete one-command start.

The plan is a list of windows (`[{"window": 1}, {"window": 2}]`); the pipe and
the save slot are derived by default (pipes follow the launcher naming, save
slots cycle 2-3-4-1). Two variants:

- `--variant b` (default, the goal): ONE trainer over all windows - a
  SubprocVecEnv with one worker per window, all windows on the same boss,
  one set of checkpoints/journal. `--smoke-steps N` isolates a short live
  check in `<scene>__smoke`.
- `--variant a` (interim): one independent `train.py` per window, namespaced
  by a tag; the orchestrator watches the pairs and restarts a dead pair.

**Same-hero rule:** every window must boot from the same hero, otherwise
episodes from different heroes are incommensurable. Windows use different
slots but identical content - sync once before a session, with automatic
backups of the overwritten slots:

```
python tools/multibox/sync_saves.py --list
python tools/multibox/sync_saves.py --from 2 --to 1,3,4
```

`sync_saves.py` refuses to run while the game is open.

The windows must run the CURRENT mod: it sets `Application.runInBackground`,
so a window keeps simulating while another one has the focus, and its
`reset()` waits for the fight scene after a restart (the mod defers restarts
while the game is busy after a load).

## Notes

- Saves (`AppData\LocalLow\Team Cherry\Hollow Knight`) and the settings
  registry key are shared by all windows.
- A public, mod-free edition of this tool lives at
  https://github.com/ADIMIR21/hk-multibox; this copy adds the pipe-aware
  launch/status integration the training loop relies on.
