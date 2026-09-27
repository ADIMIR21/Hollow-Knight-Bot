# AGENTS.md

How to work in this repository — for human contributors and for AI agents alike. Architecture,
the command protocol and the game-side pitfalls live in `README.md`; the named-pipe harness has
its own `tests/pipe_sim/README.md`. This file holds the rules that are easy to get wrong.

## 0. Keep this file up to date

**If your change makes anything here wrong, update this file in the same change.** A path that
moved, a command that changed, a new test tier, a new hard rule, a knob that appeared or went
away — AGENTS.md is part of that change, not a follow-up task. A stale instruction here is worse
than no instruction: the next agent will follow it and break something.

The same applies to `README.md`: it is the project's documentation of record (architecture,
protocol, parameters, changelog), and every behaviour change belongs there too.

## 1. Tests come with the change

A change is not finished until it is covered by something that runs without the game. There are
two tiers; pick the one that matches what you touched, and add the other only if it fits.

### Tier 1 — pure-Python units (`tests/test_*.py`)

For anything that is plain Python: the boss registry and query resolver, argument parsing,
reward/potential math that can be isolated, protocol constants, parsing of any external source.

* Standard library only — `unittest`, no `pytest`, no `numpy`/`torch`/`vgamepad`/`cv2` imports.
  CI installs nothing for this job, and the modules that need the heavy dependencies
  (`hk_gym.py`, `train.py`, `screen_capture.py`) are only compiled, never imported.
* One file per module or concern, named `test_<thing>.py`; add cases to the existing file when
  the concern already has one.
* Assert behaviour, not implementation: the lookup rules, the guard that must reject something,
  the invariant that must hold.

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

### Tier 2 — the named-pipe harness (`tests/pipe_sim/`)

For anything on the wire or in the transport: a new command, a changed message shape, a new
one-shot event, a reconnect/watchdog behaviour, a change in the mod's answers.

* Extend `test_pipe.py` with checks, and keep the mock (`Program.cs`) faithful to the mod: same
  reason strings (`"unknown command"`, `"boss not recognized"`), same rules (an unknown `gg_*`
  query is passed through as a scene instead of an error), same hello fields. A mock that is
  "nicer" than the mod turns a real divergence into a passing test — that has already happened
  once, see `tests/pipe_sim/README.md`.
* The harness compiles the mod's own `Win32Pipe.cs`, so it exercises production code; keep it
  that way (no copies).
* The mock owns the pipe name `hk_ai_mod_sim` and refuses to shadow a live game — never point
  the tests at the production name `hk_ai_mod`.

```bash
python tests/run_pipe_harness.py     # builds the mock, runs --selftest-stuck, then the checks
```

### Rules that apply to both tiers

* **Never write a test that can pass vacuously.** If a test parses or greps an external source
  (the mod's C#, a log, a registry), first assert that the parse found something — see
  `tests/test_registry_parity.py::test_the_source_is_parsable`. A regex that silently stops
  matching must fail the suite, not skip it.
* **Prove the test fails without your fix.** Perturb the input (or the source you parse) and
  watch the check go red; a test that is green on a broken tree is decoration.
* **Two sides, one commit.** The mod (C#) and the Python framework are a matched pair. Change
  `BossRegistry`/`ExtraAliases`/`DEFAULT_BOSS_SCENE`/`DEFAULT_ENTRY_GATE` in
  `AiDataExporter.cs` and `bosses.py` together — `tests/test_registry_parity.py` fails otherwise.
  The same goes for a telemetry field: the mod, the Python consumer (`ai_environment.py`,
  `hk_gym.py`) and the mock move together.
* **No test may need the game, a running training loop, or a connected mod.** CI has no game.
  If something can only be verified in the game, verify it there before committing and say so in
  the commit message ("verified live: ..."), keeping the automatable part in the harness.
* Keep the suite fast and order-independent — no sleeps longer than the harness protocols need,
  no dependence on a previous test having run.

## 2. Repository map

| Path | What it is |
| --- | --- |
| `Mod/HK_AI_Mod/` | The C# mod: telemetry over the named pipe, command intake, boss registry, deferred restart, fade watchdog, pause |
| `hk_pipe.py` | Named-pipe client the whole Python side runs on (one shared client per process, auto-reconnect) |
| `bosses.py` | Python mirror of the mod's registry + command helpers |
| `hk_features.py` | Pure environment features (action table, aiming rule, boss-state tracker) - imports nothing, so tier 1 tests it directly |
| `hk_gym.py`, `ai_environment.py`, `ai_controller.py` | Gym env, observation/reward pipeline, virtual gamepad |
| `train.py` | PPO training, checkpoint layout, `logs/progress.txt` journal |
| `teleport.py`, `ai_receiver.py` | Boss selection/teleport tooling, live telemetry debugger |
| `deploy_mod.ps1` | Game lookup (Steam registry), build and deploy of the mod DLL |
| `tests/` | Units + registry parity; `tests/pipe_sim/` is the mock mod and its integration checks |
| `.github/workflows/ci.yml` | CI: units on Linux, pipe harness on Windows |

## 3. Hard rules

* **English only in tracked files** — code, comments, docstrings, docs, test names, commit
  messages. Conversation with the maintainer may be in Russian; the repository may not. Check
  with `git grep -n -P "[\x{0400}-\x{04FF}]"` (only the game's shipped DLL ever matched, and it
  is no longer in the tree).
* **Never bump a version unless the maintainer explicitly asks for it.** The mod version is
  frozen at `v1` (`MOD_VERSION`/`GetVersion()` in `AiDataExporter.cs`): it exists only to tell a
  fresh build from an old one. Do not raise it, do not introduce a new version number anywhere
  (mod, protocol, checkpoints, file naming) and do not "align" versions between files — a version
  bump is a decision for the maintainer, never a side effect of a change, and it is not a way to
  mark a fix as important. The history lives in the README changelog, not in version numbers.
* **Protocol 3.** `PROTOCOL_VERSION` (mod) and `REQUIRED_PROTOCOL` (`hk_pipe.py`) move together,
  only for a genuinely incompatible change on the wire, and only when the maintainer asks (see the
  rule above). Then update the mock, the checks and the README in the same commit.
* **Never commit artifacts that come from the game or from a run:** the game's
  `Assembly-CSharp.dll`, `Mod/HK_AI_Mod/libs/`, `models/`, `Save/`, `bin/`, `obj/`,
  `tests/pipe_sim/bosses.json`. They are in `.gitignore` on purpose — do not weaken it, and do
  not "helpfully" add a copy of the game assembly so the build works without the game.
* **The pipe is the only transport.** There is no file-based fallback and no plan to add one: the
  `%TEMP%/hk_ai_*` files were removed deliberately, so do not reintroduce a second channel.
* **The arena entry gate is `door_dreamEnter`** in Godhome arenas (the only `TransitionPoint`
  there). Any other name makes the game search for a gate that does not exist and leaves the
  screen stuck on a white fade — see the README section on the white screen.
* **Do not invent game behaviour.** Anything about the game's internals claims a source: a
  ModLog line, an IL probe in `tests/mono_il/`, or a documented engine fact. If it was not
  verified, say it is unverified instead of writing it down as fact.

## 4. Environment, build, deploy

* Windows. Python 3.11+ with the dependencies from `requirements.txt` (needed for training, not
  for the tests). .NET SDK for the harness and the mod (the projects use
  `RollForward=LatestMajor`, so .NET 6/8/10 all work).
* The mod needs the game's managed `Assembly-CSharp.dll`: it is resolved from the game install,
  with `Mod/HK_AI_Mod/libs/` as an untracked fallback. A clean checkout therefore builds only on
  a machine that has the game — the build fails with an explicit error otherwise.
* Build and deploy with the game **closed** — the DLL is locked while it runs:

  ```powershell
  powershell -ExecutionPolicy Bypass -File deploy_mod.ps1 -Build
  ```

* Python and the mod are deployed in pairs. A stale DLL is not a transport bug: the symptom is
  "the mod did not respond within 20 s" (no pipe at all), so redeploy before debugging.
* Training: `python train.py [--boss <query>] [--entry-gate <gate>]`. Environment knobs:
  `HK_BOSS_SCENE` (scene, alias or index), `HK_ENTRY_GATE`, `HK_VICTORY_FRAMES` (default 3),
  `HK_FRAME_SKIP` (4), `HK_FRAME_STACK` (4), `HK_PIPE_NAME` (default `hk_ai_mod`).
* Quick checks without training: `python ai_receiver.py` (live telemetry, a second client),
  `python teleport.py --verify` (Python and mod registries compared over the pipe).
* CI (`.github/workflows/ci.yml`) runs on every push to `master`/`dev` and on every PR: units and
  `compileall` on Linux, the pipe harness on Windows. The mod itself is not built in CI — it
  needs the game's DLL, which is neither shipped nor downloadable.

## 5. Commits and pushes

Commit only what was asked for, in English, with a message that explains **why** (the subject
line is the what) — the maintainer reads the history as a changelog, not as a diff dump. Do not
commit or push until the maintainer says so: they review changes first. Never rewrite published
history or force-push without an explicit request.

## 6. Definition of done

- [ ] The behaviour changed is covered by a test that fails without the fix (tier 1 or tier 2).
- [ ] `python -m unittest discover -s tests -p "test_*.py" -v` passes.
- [ ] `python tests/run_pipe_harness.py` passes if anything on the pipe, in the mod's answers or
      in the transport changed.
- [ ] Both sides of a paired change (mod + Python, or registry + parity test) are in the same
      commit.
- [ ] `README.md` reflects the change; `AGENTS.md` is updated if this file became wrong.
- [ ] No version was bumped (mod, protocol, checkpoints) — unless the maintainer asked for it.
- [ ] No Cyrillic in tracked files, no game/run artifacts added, `.gitignore` untouched.
- [ ] Anything that could only be checked in the game is named in the commit message.
