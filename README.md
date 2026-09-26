# Hollow Knight AI Bot 🤖

[![CI](https://github.com/ADIMIR21/Hollow-Knight-Bot/actions/workflows/ci.yml/badge.svg)](https://github.com/ADIMIR21/Hollow-Knight-Bot/actions/workflows/ci.yml)

**Hollow Knight AI Bot** is a project on training artificial intelligence (Deep Reinforcement Learning) to fight bosses in the game **Hollow Knight** using the **PPO** (Proximal Policy Optimization) algorithm.

## Architecture

The project consists of two main components:

### 1. C# Mod for Hollow Knight (`Mod/HK_AI_Mod/`)

The mod exports game telemetry over a **named pipe** `\\.\pipe\hk_ai_mod` (protocol 3, line-delimited JSON, one line per `HeroUpdate`, ~60/s). The old `%TEMP%/hk_ai_data.json` file protocol has been removed entirely - there is no file fallback:

- **Player position** (X, Y) and velocity
- **Player HP**, soul (MP) reserve
- **Boss HP**, boss position and velocity. The boss is taken from `BossSceneController.bosses` (the same boss set the game itself uses to detect the end of the arena); fallback - iterating `HealthManager` entries with HP > 20
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

Telemetry contains a `scene` field (the current scene) - so Python and the human can see which boss's arena the fight is taking place in. If a scene transition hangs (the hero stays in `transitioning`), the mod's watchdog finds the entry point after 2.5 seconds via the `TransitionPoint.TransitionPoints` registry and properly triggers `HeroController.EnterScene`; on a repeated hang it teleports the hero to the gate and lifts the freeze directly (the private `FinishedEnteringScene` + re-enabling rendering). The watchdog also raises `Time.timeScale` if the transition zeroed out time.

### 2. Python RL framework

| File | Purpose |
|------|---------|
| `ai_controller.py` | Xbox 360 gamepad emulation via `vgamepad` (16 discrete actions); sends restart/scene/gate commands into the mod's pipe |
| `screen_capture.py` | Game screen capture via `mss` + auto-focus on the Hollow Knight window |
| `ai_environment.py` | The environment: combines the video stream and telemetry; steps are synced by the pipe message counter (`seq`), so there is no file polling |
| `hk_pipe.py` | **Named-pipe client** for the mod: background reader with auto-reconnect, `get_telemetry()`, `send_command()`, one-shot events (`wait_for_status`), one shared client per process |
| `hk_gym.py` | **Gymnasium environment** - the RL core: observation space, rewards, episode logic, fast restart |
| `train.py` | **PPO training** via Stable-Baselines3; `--boss` picks the boss, training files are laid out per boss automatically |
| `ai_receiver.py` | Real-time telemetry debugger (connects as a second pipe client, so it does not disturb training) |
| `bosses.py` | Godhome boss registry (mirror of the mod's registry) + command protocol over the pipe |
| `teleport.py` | **Teleport to Pantheon bosses**: interactive boss selection, restart, warp to the arena, `--verify` - compare the Python and mod registries over the pipe, `--train` - teleport and train right away |

The mod must be loaded into the game for the framework to work!   

## Action space (16 actions)

| ID | Action |
|----|----------|
| 0 | Nothing |
| 1 | Left |
| 2 | Right |
| 3 | Jump |
| 4 | Attack |
| 5 | Dash |
| 6 | Jump + Attack |
| 7 | Dash + Attack |
| 8 | Left + Attack |
| 9 | Right + Attack |
| 10 | Left + Jump |
| 11 | Right + Jump |
| 12 | Left + Dash |
| 13 | Right + Dash |
| 14 | Pause (nothing) |
| 15 | Jump + Dash |

## Observation space

- A vector of **25 numeric values**: HP, soul, boss HP, player and boss positions, distance and direction to the boss, velocities, state flags (grounded, facing right for the player and the boss, attack, dash, jump, fall, recoil, `boss_is_attacking`, `near_hazard`, `was_hit`)
- **Frame stack**: a stack of the last 4 vectors -> `100` features at the policy's input (set by `HK_FRAME_STACK`)
- **Frame skip**: `HK_FRAME_SKIP` is no longer used - each step waits for a FRESH telemetry frame via `wait_for_fresh_telemetry` in `ai_environment.py` (the pipe message counter `seq` must change), so the step rate follows the game itself (roughly up to ~60 steps/s); if no fresh frame arrives (menu/pause), the step continues after a short wait
- Observations and rewards are normalized via `VecNormalize` (reward normalization is enabled - the reward is clipped within static bounds, victory/death signals are not lost)
- Attacks are aimed toward the boss by default (`_redirect_attack_to_boss`)

## Episode loop

1. `reset`: the first run waits for a fight to appear and, if needed, sends an auto-teleport to the arena; afterwards - fast restart via the mod
2. An episode starts only when the telemetry shows a live fight (`status=fight`, `hp>0`, `boss_hp>0`)
3. Victory: `HK_VICTORY_FRAMES` (default 3) consecutive frames with `boss_hp<=0` and `boss_dead=1` -> **+1000**, `terminated`
4. Player death: `hp<=0` -> **-500**, `terminated`
5. Empty episode (no boss in the scene): aborted after 150 frames, so as not to wait 3000 steps outside the arena
6. Episode cap: 3000 steps -> `truncated`, **without reloading the arena**: nobody won and nobody died, so the bot keeps fighting the same boss from the same state with a fresh episode counter. The cap is only a bookkeeping window (it exists so a fight where nobody wins and nobody dies cannot run forever and blind the training metrics); such windows are written to the journal as `WINDOW` and do not count towards the win rate

## Reward function

Potential-based shaping: `r = Φ(s') - Φ(s)`, where `Φ = 15 * (damage dealt to the boss) - 10 * (player HP lost)`.

- **+1000** for defeating the boss (terminal)
- **-500** for player death (terminal)
- **-0.05** per step (penalty for hesitation)

The death penalty was raised from -200 to -500: the maximum shaping over an episode is ~3000+ (boss damage), so at -200 it was profitable for the policy to trade HP for boss damage; -500 makes dying before the kill strictly bad.

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
dotnet build Mod/HK_AI_Mod/HK_AI_Mod.csproj -c Release
```

The project locates the game by itself (Steam registry or standard paths on drives A: through Z:) and takes the game's `Assembly-CSharp.dll` from that install - the repository does not ship it, because that DLL is Team Cherry's compiled code (a byte-for-byte copy of the installed game). So the mod is built against exactly the assembly it will run against. If automatic detection fails on an unusual install path, copy `hollow_knight_Data\Managed\Assembly-CSharp.dll` into `Mod/HK_AI_Mod/libs/` (that folder is in `.gitignore`) and the build uses it as a fallback. Copy the built DLL into the mods folder:

```
<path to the game>/hollow_knight_Data/Managed/Mods/HK_AI_Mod/HK_AI_Mod.dll
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

### Training progress log

All metrics that go to TensorBoard and are printed to the console are also mirrored to the text file **`logs/progress.txt`** (UTF-8, appended on every run - if training crashes, the progress already written is not lost):

- a line `EPISODE ...` for each finished episode: the outcome (`outcome=victory` / `death` / `timeout`), reward, length, the victory counter and the win rate over a window of 100 episodes;
- the metrics table after every rollout (`n_steps = 1024` steps) - exactly the block printed to the console: `custom/victories`, `custom/win_rate`, `custom/last100_*`, `reward_breakdown/*`, `rollout/*`, `train/*`. With `verbose=1` it is written by the Stable-Baselines3 logger itself through `HumanOutputFormat`; with `verbose=0` the values are collected by the callback.

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
- **Registry parity** (`tests/test_registry_parity.py`) - reads `Mod/HK_AI_Mod/AiDataExporter.cs` and compares it with `bosses.py` entry by entry, in order: the mod's `BossRegistry`, `ExtraAliases`, `DEFAULT_BOSS_SCENE` and `DEFAULT_ENTRY_GATE`. The mod is the source of truth for what the game accepts, so a drift on either side (a boss added, renamed or lost in translation) is a failing test instead of a teleport into a scene the mod does not know
- **Pipe harness** (`tests/run_pipe_harness.py`) - generates the registry the mock serves, builds it with `dotnet`, runs `--selftest-stuck` (a client that stops reading must not eat a slot: the stuck write is cancelled and the slot is freed) and then the 36 integration checks against the mock over real Win32 named pipes. One command instead of three: the mock exits as soon as its stdin reaches EOF, so the runner keeps that stdin open, waits for the mock to report its registry and shuts it down afterwards. Windows only
- **CI** (`.github/workflows/ci.yml`) - on every push to `master`/`dev` and on every pull request: `ubuntu-latest` compiles every `.py` file (`compileall`, which also catches a broken encoding) and runs the units; `windows-latest` runs the pipe harness. The mod itself is not built in CI: its `.csproj` needs the game's `Assembly-CSharp.dll`, which is neither shipped nor downloadable

## Training parameters (PPO)

| Parameter | Value |
|----------|----------|
| Algorithm | PPO (Stable-Baselines3) |
| Policy | MlpPolicy, net_arch [256, 256] |
| Learning rate | 3e-4 with linear decay |
| n_steps | 1024 |
| batch_size | 128 |
| n_epochs | 10 |
| ent_coef | 0.01 |
| clip_range | 0.2 |
| gae_lambda | 0.95 |
| gamma | 0.99 |
| max_grad_norm | 0.5 |
| Observation normalization | VecNormalize, clip_obs 10 |
| Reward normalization | enabled |

## Changelog

### Mod (`Mod/HK_AI_Mod/`)
- Built-in **Godhome boss registry** (60 entries): all combat `GG_*` scenes from the game's build settings, fight variants (`_V` = Ascended/Radiant, `GG_Mantis_Lords_V` = Sisters of Battle, `GG_Nosk_Hornet` = Winged Nosk), plus Godhome hubs
- New commands over the pipe:
  - `boss <query>` - select a boss and teleport to its arena (index, scene name in any register, the alias `hornet`/`nkg`/`sisters`, or part of the name); the choice becomes the mod's target
  - `bosses` - send the registry as a `boss_list` event (works in the main menu too)
  - `set_boss <scene>` / `set_gate <gate>` - set the target scene and the arena entry gate
  - `teleport` - teleport to the target boss's arena
  - `warp` - return the hero to the arena gate without reloading the scene
- `restart`/`teleport` take the entry gate from `set_gate` (previously it was hard-coded `door1`, which does not exist in Godhome arenas); the current version additionally validates it against the scene's real `TransitionPoint`s - see the section on the white screen
- Query resolver: index -> scene name -> alias -> exact title -> partial match (ambiguous queries are rejected with a hint)
- Added the `scene` field to the telemetry - the current fight scene

### Named-pipe transport instead of `%TEMP%` files
- The whole mod <-> Python channel moved to the named pipe `\\.\pipe\hk_ai_mod` (protocol 3, line-delimited JSON): telemetry, commands (`restart`, `teleport`, `set_boss`, `set_gate`, `boss`, `bosses`, `warp`), one-shot events (`boss_list`, `boss_selected`, `command_error`) and the registry dump. The files `%TEMP%/hk_ai_data.json`, `hk_ai_cmd.txt`, `hk_ai_boss.txt`, `hk_ai_gate.txt`, `hk_ai_gates.txt`, `hk_ai_bosses.json` are gone - no file fallback is left
- The mod's server is implemented on raw kernel32 (`Mod/HK_AI_Mod/Win32Pipe.cs`), because in the game's Mono every `NamedPipeServerStream` constructor is a stub that throws `NotImplementedException` (proved by the IL probe in `tests/mono_il/`). One thread per slot, up to 4 clients, synchronous handles without overlapped I/O: a hanging read cannot block a write
- The game thread only publishes the latest frame, so Python can neither slow the game down nor break its own connection; the reader keeps reading the freshest message and never waits for old ones
- Python side: `hk_pipe.py` (background reader thread, auto-reconnect, hello re-read on reconnect), `bosses.py` / `ai_controller.py` / `ai_environment.py` / `teleport.py` / `ai_receiver.py` switched to it
- A client that stops reading cannot eat a slot: a write stuck for more than 3 s is cancelled (`CancelSynchronousIo`), the slot is freed and rebuilt - covered by `--selftest-stuck` in the harness
- `tests/pipe_sim/` - a mock mod plus 36 integration checks; the harness compiles the same `Win32Pipe.cs` and refuses to start if the real mod's pipe exists on the machine (so it cannot accidentally connect to the live game)
- Deployment is paired: the Python side requires the pipe build of the mod. With an older build deployed the pipe is simply absent - the framework reports that the mod did not answer within 20 s

### Python framework
- `bosses.py` (new) - mirror of the mod's registry + command protocol over the pipe (`send_command`, `request_boss`, `request_restart`, `request_warp`, `wait_for_scene`, etc.)
- `hk_pipe.py` (new) - the named-pipe client the whole Python side runs on: one shared client per process, background reader with auto-reconnect, one-shot events (`wait_for_status`)
- `teleport.py` (new) - interactive boss selection and teleport: menu, `--list`, `--boss <query>`, `--restart`, `--warp`, `--train` (teleport and train right away); fallback for mod builds without the `boss`/`warp` commands
- `train.py` - the `--boss` flag; **training files are laid out per boss automatically** (`models/ppo_hk/<scene>/`: checkpoints, `hk_model_final.zip`, `vecnormalize.pkl`); an old save from the root of `models/ppo_hk/` migrates to `GG_False_Knight/` on the first run
- `hk_gym.py` - `HK_BOSS_SCENE` accepts aliases and indices, `HK_ENTRY_GATE` added (the arena gate)
- `deploy_mod.ps1` (new) - game lookup via the Steam registry, build and deployment of the DLL with the game closed

### White screen at the end of an episode and the arena gate
- **Automatic entry gate selection**: the mod takes `EntryGateName` from the scene's real `TransitionPoint` list (priority - the gate explicitly set with `set_gate` if it exists in the scene; otherwise a gate with `dream` in its name; otherwise the first one) and remembers `scene=gate` pairs in memory. Previously `door1` was always sent, which does not exist in Godhome arenas
- **Deferred restart**: the `restart`/`teleport`/`boss` command is accepted immediately (`restart_pending: 1` while the mod is waiting), but `BeginSceneTransition` runs only when the game is not busy with its own scenario - no `IsInSceneTransition`/`IsLoadingSceneTransition`, the hero is not in `transitioning`, is not dying, and the white arena exit is not playing (`BossSceneController.isTransitioningOut`). The death signal is narrow: `cState.dead`/`hazardDeath`, or `health <= 0` **while the scene matches the target arena** (`controlReqlinquished` in Godhome is set even for a live hero in the hall, so it is not used as a stop factor). If the state has not cleared within 10 s, the transition is forced (a warning with the reason for waiting is written to the ModLog)
- **Camera fade watchdog**: every state change of the `CameraFade` FSM is written to the ModLog; if the fade sticks outside `Normal` (1.5 s, 1.0 s for `FadingOut`) while the game is calm (scene loaded, hero not transitioning), the mod sends `FADE SCENE IN` - the same event the game uses. The stock `CameraController.FadeInFailSafe` never runs anywhere in this build of the game (dead code), so the white screen never fixed itself before
- Python: `HK_ENTRY_GATE`/`--entry-gate` defaults to `door_dreamEnter`; the wait for the fight scene after a fast restart was increased to 40 s (the mod may defer the transition); victory is confirmed by the `boss_dead` event over `HK_VICTORY_FRAMES=3` frames (it used to be 20) so that the restart makes it into the `bossesDeadWaitTime` window and does not hit the white arena exit
- The training console prints `[TIMING] reset (<reason>): X.XXs` and a summary every 10 resets - it shows how much an episode restart actually costs

### Verified live
All of the mod's commands were tested on a running game: dumping the list (60 bosses), teleporting from the Atrium to the Vengefly King arena with the fight starting, arena restart, warp to the gate.

The pipe transport was verified on a running game as well: the hello line reports `protocol=3`, telemetry streams continuously, two clients connect at the same time (training + debugger), `bosses` returns all 60 records, `set_boss`/`boss` reload the scene and the fight starts (`GG_False_Knight`, `GG_Hornet_1`, `GG_Vengefly`), `set_gate` + `warp` work, and the ModLog stays clean. The transport itself is covered by the harness in `tests/pipe_sim/` (36 checks against the same `Win32Pipe.cs` the mod uses).

The mod version is deliberately pinned to `v1` and does not change with edits (see the comment on `GetVersion` in `AiDataExporter.cs`) - it exists only to tell a fresh build from old ones; the change history is kept in the "Changelog" section rather than in version numbers.

## License

MIT
