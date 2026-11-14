# The experiment sweep bench

Reward tweaks used to be judged by watching a noisy training statistic and hoping: the
mask penalty was raised once to force dodging, the following run learned no faster, and
the value went back - a decision made with no instrument behind it. The bench exists so
every change to the training problem is a **bounded, isolated, recorded** experiment, and
comparisons are made across runs that differ in exactly one thing.

The division of labour: the tooling does the mechanics (run, isolate, collect, compare);
the human makes the decisions (what to try next, what "better" means). No auto-tuner
until the noise floor is known - an optimizer pointed at noise confidently finds garbage.

## Layout

* `tools/sweep/run_experiment.py <config.json>` - runs one experiment: a JSON config
  becomes a run tagged `exp-<name>`, isolated in `models/ppo_hk/<scene>__exp-<name>/`,
  `logs/progress__exp-<name>.txt` and `logs/train__exp-<name>.log`. The working files
  (the reward-override JSON, the multi-window plan) land in `logs/sweep/<name>/`.
  `--dry-run` prints what would run; an existing tag is refused without `--force`.
* `tools/sweep/report.py --boss <scene>` - one table, one line per experiment: the
  reward values it really trained under (read from the checkpoint fingerprint, not from
  the config), the exploration bonus, the training win rate, the **deterministic probe's**
  `eval_win_rate`, the step of the first win, the mean fight length. `--all` adds the
  untagged baseline folder. An experiment without a journal is reported as an error row,
  never silently dropped.
* The trainer knobs the bench uses: `--steps` (a bounded budget; the default stays the
  open-ended 5M night run), `HK_REWARD_OVERRIDES` (a JSON file replacing hk_gym's named
  reward constants - captured by the checkpoint fingerprint, so an experiment's
  checkpoints are never resumed by a default run), `HK_PPO_ENT_COEF`.

Example config (`experiments/mask400.json`):

```json
{
    "name": "mask400",
    "boss": "GG_False_Knight",
    "timescale": "fast",
    "steps": 200000,
    "windows": 4,
    "reward": {"HEALTH_PENALTY_PER_MASK": 400}
}
```

```
python tools/sweep/run_experiment.py experiments/mask400.json
python tools/sweep/report.py --boss GG_False_Knight
```

## The machine

4 windows at 2x is the proven stable setup on the current CPU; more windows stalls it,
and pushing the timescale past 2x is deliberately off the table while frame-tied physics
is a live risk. So a budget of 200k steps costs roughly the same wall-clock per window
batch regardless of the experiment count: run experiments in batches of four-window
variant-b sessions, one after another.

## Continuing an interrupted experiment

The trainer resumes only from `hk_model_final.zip`; the periodic
`hk_night_run_*_steps.zip` checkpoints are history, and a power cut (or a hard kill)
loses everything after the last of them. To continue where a dead run stopped,
promote its newest checkpoint and restart the same experiment - the fingerprint
guard makes sure the folder is only resumed by a run with the same reward and
time scale:

```
cp models/ppo_hk/<scene>__exp-<name>/hk_night_run_<N>_steps.zip    models/ppo_hk/<scene>__exp-<name>/hk_model_final.zip
python tools/sweep/run_experiment.py experiments/<name>.json --no-launch
```

The checkpoint and the normalization statistics are written at the same
boundaries, so a promoted pair is always consistent.

## Before a batch: park the windows

A window that boots from a save parked mid-world has to cross into Godhome while the
trainer's restart loop is already firing, and that combination can churn for tens of
minutes per window (observed live: 143-467 s "the game state never freed up", cycled,
zero fights detected). `tools/sweep/park_windows.py` walks each window in one at a
time - load the save, wait for the teleport to land in a live fight, freeze it - and
leaves every window paused in the arena; the trainer lifts the pauses on the way in:

```
python tools/sweep/park_windows.py --windows 4 --boss GG_False_Knight
python tools/sweep/run_experiment.py experiments/baseline-1.json --no-launch
```

Save slots default to the launcher's cycling (2, 3, 4, 1). A window that never parked
is reported; the trainer still tries to restart it itself, but expect a slow start.

## The order of experiments

1. **Noise floor (before anything else).** Run the identical baseline config three
   times (`baseline-1`, `baseline-2`, `baseline-3`). The spread of `eval_win_rate` and
   `train_win_rate` between them is the smallest difference any future experiment must
   show to mean anything. Until this is measured, every comparison - manual or automatic
   - is astrology.
2. **Exploration bonus.** `ent_coef` 0.01 / 0.02 / 0.05, everything else at baseline.
   Too low and the policy collapses onto one behaviour; too high and it never commits.
3. **The open-window multiplier.** `OPEN_WINDOW_DAMAGE_MULTIPLIER` 1 / 3 / 6. This is
   the knob that decides whether the policy commits to the stunned window or hovers.
4. **The mask price** (only if the replay shows tanking): `HEALTH_PENALTY_PER_MASK`
   200 / 400 - the raised value was tried once and reverted, but that verdict had no
   probe behind it; it is worth one clean re-trial with `eval_win_rate` recorded.

Each step compares against the baseline row of the same budget; a knob that beats the
noise floor twice in a row becomes the new baseline and the next step starts from it.
Only after the grid is exhausted is it worth bolting an optimizer (e.g. Optuna) onto the
same runner - by then the report tells you whether the metrics are clean enough for one.
