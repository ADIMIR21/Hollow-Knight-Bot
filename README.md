# Hollow Knight AI Bot 🤖

[![CI](https://github.com/ADIMIR21/Hollow-Knight-Bot/actions/workflows/ci.yml/badge.svg)](https://github.com/ADIMIR21/Hollow-Knight-Bot/actions/workflows/ci.yml)

**Hollow Knight AI Bot** is a project on training artificial intelligence (Deep Reinforcement Learning) to fight bosses in the game **Hollow Knight** using the **PPO** (Proximal Policy Optimization) algorithm.

## Architecture

The project consists of two main components:

### 1. C# Mod for Hollow Knight (`Mod/AiTrainHK/`)

The mod exports game telemetry over a **named pipe** `\\.\pipe\hk_ai_mod` (protocol 3, line-delimited JSON, one line per `HeroUpdate`, ~60/s). The old `%TEMP%/hk_ai_data.json` file protocol has been removed entirely - there is no file fallback:

- **Player position** (X, Y) and velocity
- **Player HP**, soul (MP) reserve
- **Boss HP**, boss position and velocity. The boss is taken from `BossSceneController.bosses` (the same boss set the game itself uses to detect the end of the arena); fallback - iterating `HealthManager` entries with HP > 20. The arena is reported as a whole too: `arena_bosses` and `arena_alive` (how many members the set has, how many are up), `arena_hp` (their summed health) and `arena_detail` (`name:hp:dead` per member). The latched boss alone can sit at a health floor while the fight is decided by another member of that set, so the number that actually moves is visible instead of assumed
- **State flags**: grounded, attack, dash, jump, fall, recoil, `boss_is_attacking`, `near_hazard`, `was_hit`, `is_dead`
- **Monotonic counters**: `hit_counter` (hits taken) and `boss_damage_total` (total damage dealt to the boss)
- **Boss death**: the `boss_dead` field - detected via the FSM state `Death Anim Start`, the `BossSceneController.OnBossesDead` event, `isDead`, and HP <= 0; after death `boss_hp` is pinned to 0
- **FSM log**: the mod writes every boss FSM state to the ModLog and classifies them (attack / not attack)

The mod keeps a duplex server on `\\.\pipe\hk_ai_mod` (up to 4 clients, so training and a debugger can run side by side): when a client connects it first receives a hello line `{"status": "pipe_hello", "protocol": 3, "mod_version": "v1"}`. Reading and writing happen on background threads - the game thread only publishes the latest telemetry frame, so a hung Python process cannot freeze the game.

**Fast restart** is supported: Python sends `restart [scene] [gate]` into the pipe, and the mod reloads the fight scene via `BeginSceneTransition`. The target scene and the entry point are stored inside the mod and set with `set_boss` / `set_gate` (defaults `GG_False_Knight` / `door_dreamEnter`). The mod acknowledges the command with `restart_pending: 1` in the telemetry - it stays set while the mod waits for a safe moment to transition.

**Why the screen used to turn white, and why the restart is now deferred.** `GameManager.EnterHero(additiveGateSearch: true)` looks for `EntryGateName` only among the `TransitionPoint`s of the scene being loaded; if the name is not there, the game writes `Searching in next scene for TransitionGate failed.` to Player.log and returns - without `EnterScene`, without `FinishedEnteringScene`, without `FadeSceneIn`. The hero stays in `transitioning`, and the camera fade (white after death / after exiting the arena) stays stuck on screen. Previously the mod always passed `door1`, which does not exist in Godhome arenas (their only gate is `door_dreamEnter`), so every episode was rescued by the watchdog, and the white screen appeared every other time - precisely when the forced transition wedged itself into the game's own white scenario (Dream Return on death, `GG TRANSITION OUT STATUE` on victory). Now the mod: (1) picks a gate that actually exists in the scene (and remembers it in memory per scene), (2) accepts the command immediately, but performs the transition itself only when the game is not busy with its own victory/death scenario, (3) clears a stuck camera fade with the `FADE SCENE IN` event (the stock `CameraController.FadeInFailSafe` does not run in this build of the game), (4) writes every camera fade state to the ModLog - from it you can see what is happening at the end of an episode.

**Choosing a Pantheon boss** - the mod contains a built-in Godhome boss registry (all `GG_*` scenes from the game's build settings) and understands these commands from the pipe:

| Command | Action |
|---------|----------|
| `restart [scene] [gate]` | Fast restart: reload the scene (default - the target set by `set_boss`). The mod picks a gate that exists in the scene; an explicitly set `set_gate` takes priority |
| `teleport` | Same as `restart` - teleport to the target boss's arena |
| `set_boss <scene>` | Set the target scene: alias (`hornet`, `nkg`), scene name or registry index |
| `set_gate <gate>` | Set the arena entry gate (an empty value restores the default `door_dreamEnter`) |
| `boss <query>` | **Select a boss and teleport to it.** Query: index in the registry, scene name (`gg_hornet_1`, case-insensitive), short alias (`hornet`, `nkg`, `sisters`, `oro`) or part of the name. The selected scene becomes the mod's target, so restarts and training keep working with that arena. The mod answers with a `boss_selected` event |
| `bosses` | Send the full registry as a `boss_list` event (works even in the main menu) |
| `warp` | Return the hero to the arena gate without reloading the scene (if thrown out of the fight / stuck) |
| `pause` | **Freeze the fight** (`Time.timeScale = 0`): the boss FSM, the hero's input and every animation stop while the game keeps publishing telemetry. Answered with a `paused` event |
| `resume` | Unfreeze the fight and restore the time scale it had before. Answered with a `resumed` event. Both commands are idempotent, and the mod lifts the pause by itself after 120 s without a `resume` - a trainer that died mid-update must not leave the game frozen |
| `action <id>` | **Press the hero's buttons through the game's own input** (`0`-`18`, the table in `hk_features.py`). The mod commits the state on InControl's `PlayerAction`s and re-commits it on every hero update, so no emulated controller is involved and a killed trainer cannot leave the game bound to a dead pad. The id stays held until the next `action` command, and for at most 2 s after the last one arrives, so a trainer that dies cannot leave the hero pressing a button |

Telemetry contains a `scene` field (the current scene) - so Python and the human can see which boss's arena the fight is taking place in. If a scene transition hangs (the hero stays in `transitioning`), the mod's watchdog finds the entry point after 2.5 seconds via the `TransitionPoint.TransitionPoints` registry and properly triggers `HeroController.EnterScene`; on a repeated hang it teleports the hero to the gate and lifts the freeze directly (the private `FinishedEnteringScene` + re-enabling rendering). The 2.5 s window is only the first line of defence: a hang that starts later is caught by the same repair after 5 s of frozen state, and the fade has a 15 s backstop. The watchdog also raises `Time.timeScale` if the transition zeroed out time. While a `pause` is active the watchdogs and the deferred transition stand down entirely: a frozen game is not a stuck one, and it must not start a scene transition behind the trainer's back.

### 2. Python RL framework

| File | Purpose |
|------|---------|
| `ai_controller.py` | The bot's hands: 19 discrete actions, sent to the mod's pipe as `action <id>` - the default input path. `HK_INPUT=pad` switches to the emulated Xbox 360 gamepad (`vgamepad` + ViGEmBus) instead |
| `screen_capture.py` | Game screen capture via `mss` + auto-focus on the Hollow Knight window |
| `ai_environment.py` | The environment: combines the video stream and telemetry; steps are synced by the pipe message counter (`seq`), so there is no file polling |
| `hk_pipe.py` | **Named-pipe client** for the mod: background reader with auto-reconnect, `get_telemetry()`, `send_command()`, one-shot events (`wait_for_status`), one shared client per process |
| `hk_gym.py` | **Gymnasium environment** - the RL core: observation space, rewards, episode logic, fast restart |
| `hk_features.py` | The pure part of the environment - the action table, the aiming rule, the boss-state tracker. Imports nothing, so the tests exercise it directly instead of parsing the environment |
| `train.py` | **PPO training** via Stable-Baselines3; `--boss` picks the boss, training files are laid out per boss automatically |
| `ai_receiver.py` | Real-time telemetry debugger (connects as a second pipe client, so it does not disturb training) |
| `bosses.py` | Godhome boss registry (mirror of the mod's registry) + command protocol over the pipe |
| `teleport.py` | **Teleport to Pantheon bosses**: interactive boss selection, restart, warp to the arena, `--verify` - compare the Python and mod registries over the pipe, `--train` - teleport and train right away |

The mod must be loaded into the game for the framework to work!   

**Input goes through the pipe by default.** The controller writes `action <id>` into the pipe, and the mod presses the hero's buttons through the game's own input (`InputHandler`), so no emulated controller is involved and a trainer that dies cannot leave the game bound to a dead pad. `HK_INPUT=pad` switches back to the emulated Xbox 360 gamepad (`vgamepad` + ViGEmBus); that path is still supported, but nothing in the repository uses it, so the pipe stays the default.

## Action space (19 actions)

| ID | Action |
|----|----------|
| 0 | Nothing |
| 1 | Left |
| 2 | Right |
| 3 | Jump |
| 4 | Attack (aimed at the boss) |
| 5 | Dash |
| 6 | Jump + Attack |
| 7 | Dash + Attack |
| 8 | Left + Attack |
| 9 | Right + Attack |
| 10 | Left + Jump |
| 11 | Right + Jump |
| 12 | Left + Dash |
| 13 | Right + Dash |
| 14 | Up + Attack |
| 15 | Jump + Dash |
| 16 | Down + Attack |
| 17 | Cast (the spell in the facing direction) |
| 18 | Up + Cast |

Only `4` is aimed: `left`/`right` is chosen from the normalised direction to the boss
(`dx_to_boss`, threshold 0.3) and the other attack actions press exactly the buttons above.
Every attack action used to be rewritten to `8`/`9`, which silently dropped the jump
of `6` and the dash of `7` - while the boss was off centre, which is nearly always, neither
skill could happen or be learned (`hk_features.redirect_action`).

`14`, `16`, `17` and `18` are the actions the knight cannot fight without: the nail swings up and
down (a boss that spends the fight in the air can only be hit by the first) and the soul in the
observation is only worth anything if there is an action that spends it. `14` used to be a second
id for "nothing" - `ai_controller` had no branch for it - so the policy split probability between
two names for one outcome.

## Observation space

- A vector of **31 numeric values**: HP, soul, boss HP, `scene_hp`, player and boss positions, distance and direction to the boss, velocities, state flags (grounded, facing right for the player and the boss, attack, dash, jump, fall, recoil, `boss_is_attacking`, `near_hazard`, `was_hit`), plus what the boss is doing right now
- **Boss state**: `boss_attack_antic` (the attack is winding up), `boss_open` (stunned or recovering - the punish window), `boss_dead`, `boss_state_age` (how long the current state has been running, scaled over 60 frames) and `boss_state_changed`. The mod already sent the state name (`boss_state`, the animation clip or the attacking FSM state) in every single frame; `hk_features.BossStateTracker` turns it into these five numbers. The name as text is useless to the policy, and the frame stack only reaches back ~50 ms at 75 fps, so "the hit lands in a few frames" has to be a feature rather than something to infer
- **`scene_hp`**: the summed health of every `HealthManager` in the scene, next to the mod's monotone damage counter. `boss_hp` alone describes a pool the game repairs during the fight, so on its own it cannot show how far the fight actually got
- **Frame stack**: a stack of the last 4 vectors -> `124` features at the policy's input (set by `HK_FRAME_STACK`)
- **Frame skip**: `HK_FRAME_SKIP` is no longer used - each step waits for a FRESH telemetry frame via `wait_for_fresh_telemetry` in `ai_environment.py` (the pipe message counter `seq` must change), so the step rate follows the game itself (roughly up to ~60 steps/s); if no fresh frame arrives (menu/pause), the step continues after a short wait
- Observations and rewards are normalized via `VecNormalize` (reward normalization is enabled - the reward is clipped within static bounds, victory/death signals are not lost)
- Only the plain attack is aimed toward the boss (`hk_features.redirect_action`)

## Episode loop

1. `reset`: the first run waits for a fight to appear and, if needed, sends an auto-teleport to the arena; afterwards - fast restart via the mod
2. An episode starts only when the telemetry shows a live fight (`status=fight`, `hp>0`, `boss_hp>0`)
3. Victory: `HK_VICTORY_FRAMES` (default 3) consecutive frames with `boss_hp<=0` and `boss_dead=1` -> **+1000**, `terminated`
4. Player death: `hp<=0` -> **-500**, `terminated`
5. Empty episode (no boss in the scene): aborted after 150 frames, so as not to wait 3000 steps outside the arena
6. Episode cap: 3000 steps -> `truncated`, **without reloading the arena**: nobody won and nobody died, so the bot keeps fighting the same boss from the same state with a fresh episode counter. The cap is only a bookkeeping window (it exists so a fight where nobody wins and nobody dies cannot run forever and blind the training metrics); such windows are written to the journal as `WINDOW` and do not count towards the win rate

## Reward function

Potential-based shaping: `r = Φ(s') - Φ(s)`, where `Φ = DAMAGE_REWARD_PER_HP * (damage dealt to the boss) - HEALTH_PENALTY_PER_MASK * (player HP lost)`.

- **+1000** for defeating the boss (terminal)
- **-500** for player death (terminal)
- **-0.05** per step (penalty for hesitation)

Every number lives in one block in `hk_gym.py`, because they only mean anything relative to each other. The decisive one is what a lost mask costs, and it is priced **below** the hit it buys on purpose: at 200 a mask is cheaper than the uncharged hit it buys (32 of the boss's 260, so 480), so standing inside an attack to land one more hit pays better than backing off, and the policy is left free to make that trade. It was raised to 800 once, to force dodging; the run that followed learned no faster than the one before it, so the balance went back. The win still has to be worth more than refusing to engage: a kill pays 3900 of damage plus 1000 of bonus against a death at 500 and a full bar at 1800.

Changing any of these (or `gamma`, or the observation/action sizes) writes a different fingerprint into the boss folder (`hk_run_config.py`). A checkpoint whose fingerprint does not match is not resumed - the value function and the normalization statistics belong to the old reward scale - and the console says which value changed.

Such shaping mathematically does not change the optimal policy and does not let the agent "farm" auxiliary bonuses (spamming jumps/movement) that were present in the old reward function.

## Installation and running

### Requirements

- **Hollow Knight V1.5.78.1183** (Steam version)
- **Modding API** for Hollow Knight (installed)
- **Python 3.10+**
- **.NET Framework 4.7.2** (for building the mod)
- **[Scarab](https://github.com/fifty-six/Scarab)** (mod manager)

### Installing Python dependencies

```bash
pip install -r requirements.txt
```

### Building and installing the mod

```bash
dotnet build Mod/AiTrainHK/AiTrainHK.csproj -c Release
```

The project locates the game by itself (Steam registry or standard paths on drives A: through Z:) and takes the game's `Assembly-CSharp.dll` from that install - the repository does not ship it, because that DLL is Team Cherry's compiled code (a byte-for-byte copy of the installed game). So the mod is built against exactly the assembly it will run against. If automatic detection fails on an unusual install path, copy `hollow_knight_Data\Managed\Assembly-CSharp.dll` into `Mod/AiTrainHK/libs/` (that folder is in `.gitignore`) and the build uses it as a fallback. Copy the built DLL into the mods folder:

```
<path to the game>/hollow_knight_Data/Managed/Mods/AiTrainHK/AiTrainHK.dll
```

The DLL cannot be overwritten while the game is running - close the game before deploying a new build. For convenience there is a script:

```powershell
powershell -ExecutionPolicy Bypass -File deploy_mod.ps1 -Build
```

The build must match the Python side: from the pipe transport on, the mod talks to Python over the named pipe `\\.\pipe\hk_ai_mod`, so with an old DLL deployed the framework will not see the game at all (it reports that the mod did not answer within 20 s).

### Teleporting to Pantheon bosses (choosing a boss to train)

The mod can teleport to any Godhome boss - convenient for choosing which boss to train:

```bash
python teleport.py                # interactive menu (enter a number, hornet, nkg...)
python teleport.py --list         # full list of bosses
python teleport.py --boss hornet  # teleport to Hornet Protector
python teleport.py --boss nkg     # to Nightmare King Grimm
python teleport.py --boss 5       # by list index
python teleport.py --restart      # restart the fight
python teleport.py --warp         # return the hero to the arena gate
```

The game must be running with the mod (not in the main menu - a loaded save is required). The choice is remembered: fast restart and training (`train.py`) keep working with the selected arena. If the teleport into the scene does not fire - check the ModLog: the mod logs which gates exist in the scene; a non-standard entrance can be forced with the `set_gate` command (`bosses.set_gate("<gate>")`, `HK_ENTRY_GATE`).

### Teleport + train right away

After teleporting you can start training immediately - the interactive menu itself asks "Start training? [Y/n]", or pass `--train`:

```bash
python teleport.py --train                # teleport via the menu and train right away
python teleport.py --boss hornet --train  # teleport to the boss and train right away
```

Training starts in the same console: `Ctrl+C` in `train.py` interrupts it with a save, after which you can go back to the menu and pick another boss.

The same choice also works when training directly - `HK_BOSS_SCENE` accepts aliases and indices:

```bash
HK_BOSS_SCENE=hornet python train.py   # Windows PowerShell: $env:HK_BOSS_SCENE="hornet"; python train.py
HK_BOSS_SCENE=nkg python train.py      # Nightmare King Grimm
HK_BOSS_SCENE=7  python train.py       # index from the boss registry
```

The `HK_ENTRY_GATE` variable (default `door_dreamEnter`) sets the arena's entry gate.

### Starting training

```bash
python train.py                 # default boss (GG_False_Knight)
python train.py --boss hornet   # train against a specific boss
python train.py --boss nkg      # aliases work as in teleport.py
```

No environment variable is needed: the controller sends `action <id>` into the pipe by default, and the mod presses the hero's buttons (see the note under "Python RL framework").

Training files are laid out per boss **automatically** - nothing has to be created by hand:

```
models/ppo_hk/
├── GG_False_Knight/        # each boss has its own folder
│   ├── hk_model_final.zip      # final model
│   ├── vecnormalize.pkl        # normalization statistics
│   └── hk_night_run_*_steps.zip # checkpoints every 20000 steps
├── GG_Hornet_1/
└── ...
```

- The model automatically loads the latest save from **its own** boss's folder (`models/ppo_hk/<scene>/hk_model_final.zip`) if it exists - training continues from where it left off
- If a saved `vecnormalize.pkl` belongs to a different observation space - it is discarded and normalization starts from scratch
- A model saved under an older observation space is not loaded either: the vector grew from 25 to 31 numbers per frame (the five boss-state values and `scene_hp`), and a checkpoint whose space does not match is refused - that folder starts from scratch and says so on the console
- Old files from the root of `models/ppo_hk/` (training from before the per-boss layout, the False Knight arena) are automatically moved to `models/ppo_hk/GG_False_Knight/` on the first run
- First episode: training waits for the fight and teleports the bot to the arena itself (the scene is taken from `--boss` / `HK_BOSS_SCENE`)
- From then on the loop is fully autonomous: fight -> victory/death -> fast restart via the mod
- While training you can control it from the console:
  - `r` - enable auto-restart of the fight after death/victory
  - `s` - disable auto-restart
  - `q` - quit

Environment variables:

| Variable | Default | Description |
|------------|--------------|----------|
| `HK_BOSS_SCENE` | `GG_False_Knight` | Scene/boss for restarts and training: a scene name (`GG_Hornet_1`), an alias (`hornet`, `nkg`, `sisters`) or an index from the registry (`python teleport.py --list`) |
| `HK_ENTRY_GATE` | `door_dreamEnter` | Arena entry gate; sent to the mod with `set_gate` at startup (if the gate is not in the scene, the mod still picks an existing one itself) |
| `HK_FRAME_SKIP` | `4` | No longer used - kept for backward compatibility only / ignored; each step now syncs to a fresh telemetry write instead |
| `HK_FRAME_STACK` | `4` | How many recent observations go into the stack |

### The game is paused while the policy updates

`learn()` alternates between collecting a rollout and computing the gradient epochs. The game
runs in real time during the second half, and nothing steps the environment then: the hero used
to stand still for several seconds (an update of 8192 steps with 10 epochs costs roughly 5-10 s
on CPU), the boss kept hitting it, and the next step lumped all of that damage into a single
transition - the damage was never attributed to anything the policy did.

`GamePauseCallback` (in `train.py`) now freezes the fight: `on_rollout_end` fires after the last
step of a rollout and before the metrics table and `train()`, and `on_rollout_start` fires when
the next rollout begins, where the game is unfrozen again. The freeze itself is the mod's
(`pause`/`resume`), so the state is real and confirmed: `hk_gym.pause_game()` releases the
buttons, sends the command and waits for the mod's event. A keyboard interrupt inside the update
still unfreezes the game through the `finally` block, and if the process is killed outright the
mod lifts the pause by itself after 120 s. The resume is sent whenever a pause was requested, not
only when the mod confirmed it (a busy machine can eat the confirmation window), a fresh run asks
for one resume before its first step (a pause left behind by a killed trainer must not outlive
it), and frozen time is not treated as a stuck fight: the transition watchdogs measure real time,
so the paused interval is shifted out of their stamps.

### Training progress log

All metrics that go to TensorBoard and are printed to the console are also mirrored to the text file **`logs/progress.txt`** (UTF-8, appended on every run - if training crashes, the progress already written is not lost):

- a line `EPISODE ...` for each finished episode: the outcome (`outcome=victory` / `death` / `timeout`), reward, length, the victory counter and the win rate over a window of 100 episodes;
- the metrics table after every rollout (`n_steps = 8192` steps) - exactly the block printed to the console: `custom/victories`, `custom/win_rate`, `custom/last100_*`, `reward_breakdown/*`, `rollout/*`, `train/*`. With `verbose=1` it is written by the Stable-Baselines3 logger itself through `HumanOutputFormat`; with `verbose=0` the values are collected by the callback.

Example:

```
# Hollow Knight Bot - training progress log
# Created: 2026-09-24 19:02:11
# EPISODE - outcome of each episode, followed by the metrics table after every rollout
# Values mirror TensorBoard (logs/PPO_*) and the training console

[2026-09-24 19:02:11] EPISODE #21 step=45148 outcome=victory reward=1521.98 len=1735 | wins=4/21 win_rate(100)=0.190 death=0.810 timeout=0.000
------------------------------------
| custom/            |             |
|    episodes        | 21          |
|    victories       | 4           |
|    win_rate        | 0.19        |
|    last100_victory | 0.19        |
|    last100_death   | 0.81        |
| reward_breakdown/  |             |
|    victory         | 47.6        |
| rollout/           |             |
|    ep_len_mean     | 1.73e+03    |
|    ep_rew_mean     | 1.52e+03    |
| train/             |             |
|    loss            | -0.0973     |
------------------------------------
```

It is written by `ProgressFileCallback` in `train.py`. In the `CallbackList` it must come **last** - otherwise the metrics of the other callbacks from the same rollout will not make it into the file.

Watch in real time: `Get-Content logs\progress.txt -Wait -Tail 40` (PowerShell).

Draw the same history instead of reading it:

```bash
python plot_progress.py             # one figure of the newest run -> logs/progress.png
python plot_progress.py --runs 3    # the three newest runs (default 1)
python plot_progress.py --hours 2   # trim to the last two hours
python plot_progress.py --all       # every run in the journal
python plot_progress.py --show      # open a window as well as saving
```

The figure carries the reward per episode and per fight with the cumulative win count, the win rate
against the share of deaths and timeouts, the fight length, and the optimiser's own signals -
entropy loss (is the policy still exploring) and explained variance (does the value function know
the fight). The reward alone is not progress: it rises whenever a reward constant changes, so read
it against the win rate and the fight length.

### Telemetry debugging

```bash
python ai_receiver.py        # live telemetry over the pipe (a second client - training keeps running)
python teleport.py --verify  # compare the Python and mod boss registries over the pipe
```

The pipe is the only channel between the game and Python, so if the framework "does not see" the game: make sure the deployed build is the pipe build (see "Building and installing the mod"), and check the ModLog - on startup the mod prints the pipe name it listens on. The transport can also be exercised without the game: `tests/pipe_sim/` is a mock mod plus an integration test (see `tests/pipe_sim/README.md`).

## Tests and CI

Everything below runs without the game and without the pip dependencies (torch, vgamepad, ...), so it also runs in CI.

```bash
python -m unittest discover -s tests -p "test_*.py" -v   # units: registry, resolver, C# <-> Python parity
python tests/run_pipe_harness.py                         # the whole pipe harness in one command
```

- **Units** (`tests/test_bosses.py`) - the boss registry and `resolve_query`: lookup by number / scene / alias / exact title, the ambiguity rule (a partial match prefers the base fight over the Ascended/Radiant `_V` variant), plus the invariants that keep the menu honest: unique scenes and labels, aliases pointing at real scenes, `DEFAULT_GATE == door_dreamEnter`
- **Registry parity** (`tests/test_registry_parity.py`) - reads `Mod/AiTrainHK/AiDataExporter.cs` and compares it with `bosses.py` entry by entry, in order: the mod's `BossRegistry`, `ExtraAliases`, `DEFAULT_BOSS_SCENE` and `DEFAULT_ENTRY_GATE`. The mod is the source of truth for what the game accepts, so a drift on either side (a boss added, renamed or lost in translation) is a failing test instead of a teleport into a scene the mod does not know
- **Training configuration** (`tests/test_training_config.py`) - parses `train.py` (it cannot be imported: torch, vgamepad and the gym environment are not installed in CI) and checks the invariants that keep a night from being wasted: one update has to cover more than one fight, the discount must not look only ~100 steps ahead, the learning rate must not decay to zero inside a run, and a resumed model must be given the same hyperparameters as a fresh one (`PPO.load` applies its kwargs after the pickled data). `HK_TRAIN_PY` points the checks at another copy of the file, which is how the red case was reproduced
- **Environment features** (`tests/test_features.py`) - `hk_features` is the part of the environment that imports nothing, so it is tested for real instead of being parsed: the action table against `ai_controller.py`'s branches, the aiming rule (only `4` is aimed, `6`/`7`/`8`/`9` keep their buttons, the threshold is not crossed by equality), the boss-state classes over the states observed in `ModLog.txt` during a live session, and the state-age tracker (age grows, a missing frame does not advance it, `reset()` forgets the fight, and the feature order matches `STAT_NAMES`)
- **Game pause** (`tests/test_training_config.py`) - the pause callback is in `train.py`, it calls `pause_game`/`resume_game` through the vectorised env on the right hooks, and the `finally` block unfreezes the game after a crash
- **Input paths** (`tests/test_controller_paths.py`) - `ai_controller` cannot be covered by reading it: the mapper in `set_action` once sat behind the pipe branch's `return` and every text-level check still passed. `vgamepad` is replaced in `sys.modules` before the import, so these units run the real `set_action`/`reset_all` against a stub pad and a stub pipe: the pipe is the default and connects no device, `HK_INPUT=pad` presses the buttons and sends nothing into the pipe, every id from `1` to `18` moves the pad and flushes it, and only the exact value `pad` (any case, surrounding spaces allowed) selects the pad
- **Pipe harness** (`tests/run_pipe_harness.py`) - generates the registry the mock serves, builds it with `dotnet`, runs `--selftest-stuck` (a client that stops reading must not eat a slot: the stuck write is cancelled and the slot is freed) and then the integration checks against the mock over real Win32 named pipes. One command instead of three: the mock exits as soon as its stdin reaches EOF, so the runner keeps that stdin open, waits for the mock to report its registry and shuts it down afterwards. Windows only
- **CI** (`.github/workflows/ci.yml`) - on every push to `master`/`dev` and on every pull request: `ubuntu-latest` compiles every `.py` file (`compileall`, which also catches a broken encoding) and runs the units; `windows-latest` runs the pipe harness. The mod itself is not built in CI: its `.csproj` needs the game's `Assembly-CSharp.dll`, which is neither shipped nor downloadable

## Training parameters (PPO)

| Parameter | Value |
|----------|----------|
| Algorithm | PPO (Stable-Baselines3) |
| Policy | MlpPolicy, net_arch [256, 256] |
| Learning rate | 3e-4, flat (a decaying schedule ends at zero before a run does) |
| n_steps | 8192 |
| batch_size | 256 |
| n_epochs | 10 |
| ent_coef | 0.02 |
| clip_range | 0.2 |
| gae_lambda | 0.95 |
| gamma | 0.9995 (one step is one game frame: this keeps 0.377 of the weight of a win 1950 steps - one fight - away, where 0.995 kept 5.6e-5) |
| max_grad_norm | 0.5 |
| Observation normalization | VecNormalize, clip_obs 10 |
| Reward normalization | enabled |

## License

MIT
