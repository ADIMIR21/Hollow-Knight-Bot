using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using HKPipeInterop;
using UnityEngine;
using Modding;
using InControl;

namespace AiTrainHK
{
    public class AiDataExporter : Mod
    {
        // IMPORTANT: the version is deliberately pinned as "1" — do NOT bump it on every
        // mod change. It only exists so that ModLog shows which build the game
        // loaded. We keep the changelog in README, not in this string.
        public override string GetVersion() => "1";

        // ---------------- Transport: named pipe (protocol 3) ----------------
        // Server: \\.\pipe\hk_ai_mod (duplex, line-based exchange), created
        // directly through kernel32 — see Win32Pipe.cs and the explanation there why
        // System.IO.Pipes.NamedPipeServerStream cannot be used (in the game's Mono all of
        // its constructors are stubs throwing NotImplementedException).
        //   Mod    -> Python: JSON lines — hello on connect, then telemetry,
        //                     plus one-shot events (boss list, confirmations).
        //   Python -> Mod: text commands (see HandleCommand):
        //                     restart [scene] [gate] | teleport | set_boss <scene>
        //                     set_gate <gate> | boss <query> | bosses | warp
        // The pipe replaces the former set of files %TEMP%\hk_ai_data.json / hk_ai_cmd.txt
        // / hk_ai_boss.txt / hk_ai_gate.txt / hk_ai_bosses.json: no file races,
        // no extra sleeps on the Python side and no file polling by the mod.
        private const string PIPE_NAME = "hk_ai_mod";
        private const int MAX_PIPE_CLIENTS = 4;
        private const int PIPE_POLL_MS = 25;
        private const int PROTOCOL_VERSION = 3;
        private const string MOD_VERSION = "1";
        private const int MAX_OUTBOX = 256;
        // A client that stops reading makes WriteFile block once the pipe's out buffer is
        // full, and the slot thread never gets back to ConnectNamedPipe — the slot would be
        // lost for good. After this many milliseconds the stuck write is cancelled and the
        // slot rebuilds its pipe instance (see PipeSlotWatchdogLoop).
        private const int WRITE_STUCK_MS = 3000;

        // Per-slot bookkeeping for that watchdog: the pipe handle, the id of the thread
        // serving it and the tick at which the current write started (0 = no write in flight).
        private readonly IntPtr[] _slotHandles = new IntPtr[MAX_PIPE_CLIENTS];
        private readonly uint[] _slotThreads = new uint[MAX_PIPE_CLIENTS];
        private readonly int[] _slotWriteSince = new int[MAX_PIPE_CLIENTS];

        // One-shot events (boss list, selection confirmation), unlike telemetry,
        // are not overwritten by the newest frame: every connected client receives
        // them exactly once — by a monotonic id remembered since connect.
        private readonly List<KeyValuePair<long, string>> _outbox = new List<KeyValuePair<long, string>>();
        private long _outboxSeq = 0;

        private readonly object _sync = new object();
        private string _latestJson = "{\"status\": \"booting\", \"restart_pending\": 0}";
        private long _seq = 0;
        private volatile bool _shuttingDown = false;
        private readonly ConcurrentQueue<string> _incomingCommands = new ConcurrentQueue<string>();

        private string _targetScene = DEFAULT_BOSS_SCENE;
        private string _targetGate = DEFAULT_ENTRY_GATE;
        // scene -> entry gate that REALLY exists in the scene (learned when the scene
        // loads, so that a transition into a not-yet-loaded scene also lands in its gate).
        private readonly Dictionary<string, string> _sceneGates = new Dictionary<string, string>();

        private HealthManager? _currentBoss = null;
        private int _lastPlayerHp = 9;
        private long _hitCounter = 0;
        private long _bossDamageTotal = 0;
        private int _lastBossHpKnown = 0;
        // Damage counted over every HealthManager in the scene, not just the one we latched onto.
        // The pool the arena lists is repaired by the game a limited number of times, and the
        // health that actually ends the fight is a different one that never shows up in the arena
        // set. Keeping the last hp seen for each object is what makes the counter monotone: a
        // repair raises a pool, and it must not read as negative damage.
        private long _sceneDamageTotal = 0;
        private readonly Dictionary<int, int> _sceneHealthLast = new Dictionary<int, int>();

        private float _lastBossVelX = 0f;
        private float _lastBossVelY = 0f;

        private readonly HashSet<string> _seenFsmStates = new HashSet<string>();

        private int _attackStickyFrames = 0;
        private const int ATTACK_STICKY_MIN_FRAMES = 2;

        private bool _restartPending = false;
        // Deferred restart: the command is accepted, but the scene transition is not done
        // immediately, only when the game is not busy with its own victory/death scenario
        // (otherwise the game's white fade stays on screen, see TryPerformPendingTransition).
        private bool _restartRequested = false;
        private float _restartRequestedAt = 0f;

        // Update 9: the training loop freezes the fight while the PPO update runs.
        // The time scale is saved so that a game which was already slowed down is
        // restored as it was, and the freeze lifts by itself if the trainer dies
        // without sending "resume" (nobody is left to unfreeze the game otherwise).
        private bool _aiPaused = false;
        private float _savedTimeScale = 1f;
        private float _aiPausedAt = 0f;
        private const float PAUSE_TIMEOUT_SECONDS = 120f;
        private string _restartTargetScene = "";
        private string _restartGate = "";
        // If the scene already changed after the request, the game's own end-of-fight
        // scenario (death / arena exit) has finished and there is nothing left to wait for.
        private bool _sceneChangedSinceRequest = false;
        private string _deferReasonLastLogged = "";
        private const float RESTART_FORCE_TIMEOUT = 10f;
        // Fade watchdog: the game can clear a stuck fade itself (CameraController.FadeInFailSafe),
        // but in this build of the game that coroutine is never started — so we do it ourselves.
        private string _fadeStateLastLogged = "";
        private float _fadeNotNormalSince = -1f;
        private int _fadeRescueAttempts = 0;
        private const float FADE_STUCK_TIMEOUT = 1.5f;
        // 'FadingOut' is a transient state: if the game is already calm (scene loaded,
        // hero not transitioning) while the fade is still in it, there is nothing to wait for.
        private const float FADE_STUCK_FADINGOUT_TIMEOUT = 1.0f;
        private bool _inMenuScene = true;
        private bool _bossDead = false;
        // True when the death came from a signal about THIS fight (the arena reports every boss
        // of the fight dead, the tracked boss itself is dead, its death animation started)
        // rather than from the scene-wide HealthManager scan, which can latch on an enemy that
        // is not the boss being tracked. Only a confirmed death may report boss_hp as zero.
        private bool _bossDeadConfirmed = false;
        private BossSceneController _subscribedBsc = null;
        private float _watchdogTimer = 0f;
        private int _forcedEntryAttempts = 0;
        // The scene-change watchdog is only armed for a couple of seconds after a scene
        // load, so a freeze that starts later has nobody left to repair it: the hero stays
        // in 'transitioning', the white fade stays on screen and a restart is accepted but
        // never performed. This timer re-arms the same repair path independently of the
        // arming window.
        private float _heroFrozenSince = -1f;
        private const float HERO_FROZEN_TIMEOUT = 5f;
        // Independent of IsGameSettled: a fade left opaque while the game merely *claims* to
        // be busy must not stay on screen for hours (see FadeWatchdogTick).
        private float _fadeNonNormalHardSince = -1f;
        private const float FADE_HARD_TIMEOUT = 15f;
        private const string DEFAULT_BOSS_SCENE = "GG_False_Knight";
        // Godhome arenas enter the scene through the door_dreamEnter gate — it is the only
        // TransitionPoint in GG_* boss scenes (verified live in GG_False_Knight and
        // GG_Hornet_1). The mod still matches it against the scene's real TransitionPoints,
        // and an explicitly set set_gate takes priority.
        private const string DEFAULT_ENTRY_GATE = "door_dreamEnter";

        // ---------------- Godhome boss registry (pantheons) ----------------
        // Scenes are taken from the game's build settings (hollow_knight_Data/globalgamemanagers).
        // Entries with the _V suffix are the harder fight versions (Ascended/Radiant),
        // GG_Mantis_Lords_V = Sisters of Battle, GG_Nosk_Hornet = Winged Nosk.
        public struct BossEntry
        {
            public string Scene;
            public string Label;

            public BossEntry(string scene, string label)
            {
                Scene = scene;
                Label = label;
            }
        }

        private static readonly BossEntry[] BossRegistry = new BossEntry[]
        {
            // --- Pantheon of the Master (early bosses) ---
            new BossEntry("GG_Vengefly", "Vengefly King"),
            new BossEntry("GG_Gruz_Mother", "Gruz Mother"),
            new BossEntry("GG_False_Knight", "False Knight"),
            new BossEntry("GG_Mega_Moss_Charger", "Massive Moss Charger"),
            new BossEntry("GG_Hornet_1", "Hornet Protector"),
            new BossEntry("GG_Brooding_Mawlek", "Brooding Mawlek"),
            // --- Pantheon of the Artist (early-to-mid game) ---
            new BossEntry("GG_Soul_Master", "Soul Master"),
            new BossEntry("GG_Crystal_Guardian", "Crystal Guardian"),
            new BossEntry("GG_Crystal_Guardian_2", "Enraged Guardian"),
            new BossEntry("GG_Grimm", "Troupe Master Grimm"),
            new BossEntry("GG_Collector", "The Collector"),
            new BossEntry("GG_Soul_Tyrant", "Soul Tyrant"),
            new BossEntry("GG_Dung_Defender", "Dung Defender"),
            new BossEntry("GG_Mage_Knight", "Soul Warrior"),
            new BossEntry("GG_Watcher_Knights", "Watcher Knights"),
            new BossEntry("GG_Oblobbles", "Oblobbles"),
            new BossEntry("GG_Hive_Knight", "Hive Knight"),
            new BossEntry("GG_Nosk", "Nosk"),
            new BossEntry("GG_Mantis_Lords", "Mantis Lords"),
            new BossEntry("GG_Broken_Vessel", "Broken Vessel"),
            // --- Pantheon of the Sage (mid-to-late game) ---
            new BossEntry("GG_Lost_Kin", "Lost Kin"),
            new BossEntry("GG_Failed_Champion", "Failed Champion"),
            new BossEntry("GG_Traitor_Lord", "Traitor Lord"),
            new BossEntry("GG_Uumuu", "Uumuu"),
            new BossEntry("GG_Flukemarm", "Flukemarm"),
            new BossEntry("GG_God_Tamer", "God Tamer"),
            new BossEntry("GG_Ghost_Xero", "Xero"),
            new BossEntry("GG_Ghost_Gorb", "Gorb"),
            new BossEntry("GG_Ghost_Marmu", "Marmu"),
            new BossEntry("GG_Ghost_No_Eyes", "No Eyes"),
            new BossEntry("GG_Ghost_Markoth", "Markoth"),
            new BossEntry("GG_Ghost_Galien", "Galien"),
            new BossEntry("GG_Ghost_Hu", "Elder Hu"),
            // --- Pantheon of the Knight (late bosses) ---
            new BossEntry("GG_Hornet_2", "Hornet Sentinel"),
            new BossEntry("GG_Grey_Prince_Zote", "Grey Prince Zote"),
            new BossEntry("GG_White_Defender", "White Defender"),
            new BossEntry("GG_Grimm_Nightmare", "Nightmare King Grimm"),
            new BossEntry("GG_Hollow_Knight", "Pure Vessel"),
            // --- Pantheon of Hallownest (finale) ---
            new BossEntry("GG_Radiance", "The Radiance"),
            // --- Nailmasters (finales of pantheons 1-3) ---
            new BossEntry("GG_Nailmasters", "Brothers Oro & Mato"),
            new BossEntry("GG_Painter", "Paintmaster Sheo"),
            new BossEntry("GG_Sly", "Great Nailsage Sly"),
            new BossEntry("GG_Lurker", "Pale Lurker"),
            // --- Harder fight variants (Ascended/Radiant) ---
            new BossEntry("GG_Mantis_Lords_V", "Sisters of Battle"),
            new BossEntry("GG_Nosk_Hornet", "Winged Nosk"),
            new BossEntry("GG_Vengefly_V", "Vengefly King (Variant)"),
            new BossEntry("GG_Gruz_Mother_V", "Gruz Mother (Variant)"),
            new BossEntry("GG_Brooding_Mawlek_V", "Brooding Mawlek (Variant)"),
            new BossEntry("GG_Collector_V", "The Collector (Variant)"),
            new BossEntry("GG_Mage_Knight_V", "Soul Warrior (Variant)"),
            new BossEntry("GG_Nosk_V", "Nosk (Variant)"),
            new BossEntry("GG_Uumuu_V", "Uumuu (Variant)"),
            new BossEntry("GG_Ghost_Gorb_V", "Gorb (Variant)"),
            new BossEntry("GG_Ghost_Marmu_V", "Marmu (Variant)"),
            new BossEntry("GG_Ghost_Markoth_V", "Markoth (Variant)"),
            new BossEntry("GG_Ghost_No_Eyes_V", "No Eyes (Variant)"),
            new BossEntry("GG_Ghost_Xero_V", "Xero (Variant)"),
            // --- Godhome hub (not bosses, but handy to teleport to) ---
            new BossEntry("GG_Atrium", "Godhome Atrium (hub)"),
            new BossEntry("GG_Workshop", "Godhome Workshop (workbench)"),
            new BossEntry("GG_Boss_Door_Entrance", "Pantheon Doors"),
        };

        // All GG_ scenes from the game's build settings — for canonicalizing names
        // entered by the user in any case (gg_hornet_1 -> GG_Hornet_1).
        private static readonly string[] KnownScenes = new string[]
        {
            "GG_Atrium", "GG_Atrium_Roof", "GG_Blue_Room", "GG_Boss_Door_Entrance",
            "GG_Broken_Vessel", "GG_Brooding_Mawlek", "GG_Brooding_Mawlek_V",
            "GG_Collector", "GG_Collector_V", "GG_Crystal_Guardian", "GG_Crystal_Guardian_2",
            "GG_Door_5_Finale", "GG_Dung_Defender", "GG_End_Sequence", "GG_Engine",
            "GG_Engine_Prime", "GG_Engine_Root", "GG_Entrance_Cutscene", "GG_Failed_Champion",
            "GG_False_Knight", "GG_Flukemarm", "GG_Ghost_Galien", "GG_Ghost_Gorb",
            "GG_Ghost_Gorb_V", "GG_Ghost_Hu", "GG_Ghost_Markoth", "GG_Ghost_Markoth_V",
            "GG_Ghost_Marmu", "GG_Ghost_Marmu_V", "GG_Ghost_No_Eyes", "GG_Ghost_No_Eyes_V",
            "GG_Ghost_Xero", "GG_Ghost_Xero_V", "GG_God_Tamer", "GG_Grey_Prince_Zote",
            "GG_Grimm", "GG_Grimm_Nightmare", "GG_Gruz_Mother", "GG_Gruz_Mother_V",
            "GG_Hive_Knight", "GG_Hollow_Knight", "GG_Hornet_1", "GG_Hornet_2",
            "GG_Land_of_Storms", "GG_Lost_Kin", "GG_Lurker", "GG_Mage_Knight",
            "GG_Mage_Knight_V", "GG_Mantis_Lords", "GG_Mantis_Lords_V", "GG_Mega_Moss_Charger",
            "GG_Mighty_Zote", "GG_Nailmasters", "GG_Nosk", "GG_Nosk_Hornet",
            "GG_Nosk_V", "GG_Oblobbles", "GG_Painter", "GG_Pipeway",
            "GG_Radiance", "GG_Shortcut", "GG_Sly", "GG_Soul_Master",
            "GG_Soul_Tyrant", "GG_Spa", "GG_Traitor_Lord", "GG_Unlock",
            "GG_Unlock_Wastes", "GG_Unn", "GG_Uumuu", "GG_Uumuu_V",
            "GG_Vengefly", "GG_Vengefly_V", "GG_Watcher_Knights", "GG_Waterways",
            "GG_White_Defender", "GG_Workshop", "GG_Wyrm"
        };

        // Popular short aliases that are not part of the scene names themselves.
        private static readonly Dictionary<string, string> ExtraAliases = new Dictionary<string, string>
        {
            { "hornet", "GG_Hornet_1" },
            { "hornet2", "GG_Hornet_2" },
            { "hornet_sentinel", "GG_Hornet_2" },
            { "sentinel", "GG_Hornet_2" },
            { "false", "GG_False_Knight" },
            { "falseknight", "GG_False_Knight" },
            { "gruz", "GG_Gruz_Mother" },
            { "gruzmother", "GG_Gruz_Mother" },
            { "vengefly_king", "GG_Vengefly" },
            { "moss_charger", "GG_Mega_Moss_Charger" },
            { "mawlek", "GG_Brooding_Mawlek" },
            { "vessel", "GG_Hollow_Knight" },
            { "pure_vessel", "GG_Hollow_Knight" },
            { "hollow_knight", "GG_Hollow_Knight" },
            { "thk", "GG_Hollow_Knight" },
            { "radiance", "GG_Radiance" },
            { "nkg", "GG_Grimm_Nightmare" },
            { "nightmare_king", "GG_Grimm_Nightmare" },
            { "zote", "GG_Grey_Prince_Zote" },
            { "gpz", "GG_Grey_Prince_Zote" },
            { "sisters", "GG_Mantis_Lords_V" },
            { "sisters_of_battle", "GG_Mantis_Lords_V" },
            { "winged_nosk", "GG_Nosk_Hornet" },
            { "oro", "GG_Nailmasters" },
            { "mato", "GG_Nailmasters" },
            { "oro_mato", "GG_Nailmasters" },
            { "nailmasters", "GG_Nailmasters" },
            { "sheo", "GG_Painter" },
            { "paintmaster", "GG_Painter" },
            { "nailsage", "GG_Sly" },
            { "soul_warrior", "GG_Mage_Knight" },
            { "watcher", "GG_Watcher_Knights" },
            { "umuu", "GG_Uumuu" },
            { "traitor", "GG_Traitor_Lord" },
            { "abs_rad", "GG_Radiance" },
        };

        public override void Initialize()
        {
            _targetScene = DEFAULT_BOSS_SCENE;
            _targetGate = DEFAULT_ENTRY_GATE;

            UnityEngine.SceneManagement.SceneManager.activeSceneChanged += (oldScene, newScene) =>
            {
                bool isMenu = newScene.name != null && newScene.name.Contains("Menu");
                _inMenuScene = isMenu;
                if (isMenu)
                {
                    _restartPending = false;
                    Publish(StatusJson("main_menu"));
                }
                else
                {
                    _currentBoss = null;
                    _restartPending = false;
                    _lastBossHpKnown = 0;
                    // Per-object "last hp" cannot survive a scene change: the objects are gone and
                    // their instance ids can be reused. The damage counter itself is cumulative.
                    _sceneHealthLast.Clear();
                    _bossDead = false;
                    _bossDeadConfirmed = false;
                    // A stamp that was already running before the load would be "expired" in the
                    // new arena and force a fade while the game is fading in on its own.
                    _heroFrozenSince = -1f;
                    _fadeNonNormalHardSince = -1f;
                    _watchdogTimer = 2.5f;
                    _forcedEntryAttempts = 0;
                    _fadeNotNormalSince = -1f;
                    _fadeRescueAttempts = 0;
                    _fadeStateLastLogged = "";
                    if (_restartRequested) _sceneChangedSinceRequest = true;
                    SubscribeBossDeath();
                    LearnSceneGate(newScene.name);
                    Publish(StatusJson("loading_scene"));
                }
            };

            var host = new GameObject("AiTrainHK_Host");
            UnityEngine.Object.DontDestroyOnLoad(host);
            var ticker = host.AddComponent<AiModTicker>();
            ticker.OnTick += OnTick;

            ModHooks.HeroUpdateHook += OnHeroUpdate;
            Application.quitting += OnGameQuitting;

            var pipeThread = new Thread(PipeListenerLoop) { IsBackground = true, Name = "HK_AI_PipeServer" };
            pipeThread.Start();

            Publish(StatusJson("initialized"));
            Log($"[AI] Exporter {MOD_VERSION} is running! Pipe: \\\\.\\pipe\\{PIPE_NAME}");
            Log("[AI] Commands: restart | teleport | set_boss <scene> | set_gate <gate> | boss <query> | bosses | warp | pause | resume");
        }

        private void SubscribeBossDeath()
        {
            try
            {
                if (_subscribedBsc != null)
                    _subscribedBsc.OnBossesDead -= OnBossesDeadHandler;
                _subscribedBsc = null;

                BossSceneController bsc = BossSceneController.Instance;
                if (bsc != null)
                {
                    bsc.OnBossesDead += OnBossesDeadHandler;
                    _subscribedBsc = bsc;
                }
            }
            catch (Exception) {}
        }

        private void OnBossesDeadHandler()
        {
            _bossDead = true;
            _bossDeadConfirmed = true;
            Log("[AI] Boss is dead (BossSceneController event)");
        }

        private void OnTick(float unscaledDelta)
        {
            // DrainCommands() runs even while paused: "resume" arrives through it.
            DrainCommands();

            if (_aiPaused)
            {
                AiPauseTick();
                // A paused game is not stuck, and it must not start a transition:
                // the transition watchdogs measure unscaled time and would try to
                // repair a fight that is merely frozen.
                return;
            }

            TryPerformPendingTransition();
            TransitionWatchdogTick(unscaledDelta);
            FadeWatchdogTick();
            ApplyHeldAction();
        }

        // The bot's buttons used to come from an emulated gamepad built on the Python side, which
        // left the game bound to a dead controller whenever the trainer was killed. The hero is
        // driven through InControl's own actions instead: committing a state on a PlayerAction is
        // the public path InControl uses for merging input (read out of the shipped assembly by
        // the IL probe in tests/mono_il), and it has to be re-committed every frame, because the
        // input manager re-reads its devices each update and a single commit is overwritten.
        // The ids mirror the action table in hk_features.py; tests/test_action_wire.py compares
        // this list against that table so the two cannot drift apart.
        private const double HeldActionTimeoutSeconds = 2.0;
        private int _heldAction;
        private DateTime _heldActionAt = DateTime.MinValue;

        private void ApplyHeldAction()
        {
// A trainer that dies or stalls must not leave the hero holding a button forever -
            // that is the same disease as the dead gamepad, just from the other side. The policy
            // sends an action per step, which is far below this window, so the only thing the
            // timeout can drop is a command source that is gone.
            if ((DateTime.UtcNow - _heldActionAt).TotalSeconds > HeldActionTimeoutSeconds) _heldAction = 0;

            InputHandler handler = InputHandler.Instance;
            if (handler == null || handler.inputActions == null) return;
            HeroActions a = handler.inputActions;
            ulong tick = InputManager.CurrentTick;
            int id = _heldAction;
            if (id == 0) return;

            // The hero's buttons do come from InControl's actions - jump, dash and attack were all
            // measured working in the game. The direction does not: pressing left or right on the
            // same actions moves nothing and does not even turn the hero, while a dash at that very
            // spot carries it to the right, so the spot is walkable and the axis simply never
            // arrives. InputHandler keeps the movement vector in fields of its own, so it is
            // written here too, after the game has already computed its own value for this frame -
            // which is why this runs from the hero update hook rather than an earlier point.
            // Only while a command is actually held: writing zero on every frame, held command or
            // not, also overwrites whatever the player does on the keyboard, and the hero then
            // cannot be moved by hand at all. With no command the axis is left exactly as the
            // game computed it.
            if (id != 0)
            {
                handler.inputX = (id == 1 || id == 8 || id == 10 || id == 12) ? -1f
                               : (id == 2 || id == 9 || id == 11 || id == 13) ? 1f
                               : 0f;
                handler.inputY = (id == 14 || id == 18) ? 1f
                               : (id == 16) ? -1f
                               : 0f;
            }

            Commit(a.left,   id == 1 || id == 8 || id == 10 || id == 12, tick);
            Commit(a.right,  id == 2 || id == 9 || id == 11 || id == 13, tick);
            Commit(a.up,     id == 14 || id == 18, tick);
            Commit(a.down,   id == 16, tick);
            Commit(a.jump,   id == 3 || id == 6 || id == 10 || id == 11 || id == 15, tick);
            Commit(a.attack, id == 4 || id == 6 || id == 7 || id == 8 || id == 9 || id == 14 || id == 16, tick);
            Commit(a.dash,   id == 5 || id == 7 || id == 12 || id == 13 || id == 15, tick);
            Commit(a.focus,  id == 17 || id == 18, tick);
        }

        private static void Commit(PlayerAction action, bool pressed, ulong tick)
        {
            if (action == null) return;
            if (pressed) action.CommitWithState(true, tick, 1f);

        }
        private void DrainCommands()
        {
            while (_incomingCommands.TryDequeue(out string line))
            {
                try { HandleCommand(line); }
                catch (Exception e) { Log("[AI] Command error: " + e); }
            }
        }

        private void HandleCommand(string raw)
        {
            string line = (raw ?? "").Trim();
            if (line.Length == 0) return;

            string[] parts = line.Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries);
            string cmd = parts[0].ToLowerInvariant();
            string rest = line.Substring(parts[0].Length).Trim();

            switch (cmd)
            {
                case "set_boss":
                    if (parts.Length >= 2) SetTargetScene(parts[1]);
                    break;

                case "set_gate":
                    if (parts.Length >= 2)
                    {
                        _targetGate = parts[1];
                        Log($"[AI] Entry gate: {_targetGate}");
                    }
                    break;

                // "boss <query>" — select a pantheon boss and teleport to it.
                case "boss":
                    SelectBoss(rest);
                    break;

                // "bosses" — dump the registry. Works even in the main menu.
                case "bosses":
                    PublishEvent(BossListJson());
                    Log($"[AI] Boss list sent to the pipe ({BossRegistry.Length} entries)");
                    break;

                // "warp" — return the hero to the arena gate without reloading the scene.
                case "warp":
                    WarpHeroToGate();
                    break;

                // "pause" / "resume" - freeze the fight while the policy trains.
                case "pause":
                    SetAiPaused(true, null);
                    break;

                case "resume":
                    SetAiPaused(false, null);
                    break;

                case "teleport":
                case "restart":
                    if (parts.Length >= 2) SetTargetScene(parts[1]);
                    if (parts.Length >= 3) _targetGate = parts[2];
                    TryRestart();
                    break;

                // "action <id>" - press the hero's buttons through the game's own input, so no
                // controller is needed on the Python side. The id indexes the same table the
                // Python action set uses, and the state is held until the next command.
                case "action":
                    if (parts.Length >= 2 &&
                        int.TryParse(parts[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out int heldActionId))
                        _heldAction = heldActionId;
                        _heldActionAt = DateTime.UtcNow;
                    break;
                default:
                    Log($"[AI] Unknown command: '{line}'");
                    PublishEvent(CommandErrorJson(line, "unknown command"));
                    break;
            }
        }

        // ---------------- Pause (Update 9) ----------------

        // The mod owns the pause because the decision has to happen on the main
        // thread, and because Python has no way to tell a real pause from a menu
        // that never opened. Time.timeScale is the engine's own clock: at zero,
        // Update() still runs (telemetry keeps flowing) while the boss's FSM, the
        // hero's input and every animation stop. It is the same lever the game's
        // own pause menu uses.
        private void SetAiPaused(bool paused, string reason)
        {
            if (paused)
            {
                if (!_aiPaused)
                {
                    _savedTimeScale = Time.timeScale > 0f ? Time.timeScale : 1f;
                    _aiPaused = true;
                    _aiPausedAt = Time.unscaledTime;
                    Time.timeScale = 0f;
                    Log("[AI] Paused for the policy update (time scale 0)");
                }
                PublishEvent(PauseStateJson("paused"));
            }
            else
            {
                if (_aiPaused)
                {
                    _aiPaused = false;
                    Time.timeScale = _savedTimeScale;
                    ShiftStampsForPause(Time.unscaledTime - _aiPausedAt);
                    Log("[AI] Resumed" + (reason == null ? "" : " (" + reason + ")")
                        + ": time scale " + _savedTimeScale.ToString("F2", CultureInfo.InvariantCulture));
                }
                PublishEvent(PauseStateJson("resumed"));
            }
        }

        private string PauseStateJson(string status)
        {
            return "{\"status\": \"" + status + "\", \"paused\": " + (_aiPaused ? 1 : 0)
                + ", \"time_scale\": " + Time.timeScale.ToString("F2", CultureInfo.InvariantCulture) + "}";
        }

        // The transition watchdogs measure Time.unscaledTime — the clock that keeps running
        // while the game is frozen (the pause backstop needs exactly that). Without shifting
        // them, the freeze itself would look like a stuck fade or a stuck hero: the first tick
        // after a pause longer than the watchdog timeout would fire a repair in the middle of
        // the game's own fade. Moving the stamps forward keeps a state that was already old
        // before the pause old (it is still repaired), while the paused time no longer counts.
        private void ShiftStampsForPause(float pausedFor)
        {
            if (pausedFor <= 0f) return;
            if (_heroFrozenSince >= 0f) _heroFrozenSince += pausedFor;
            if (_fadeNotNormalSince >= 0f) _fadeNotNormalSince += pausedFor;
            if (_fadeNonNormalHardSince >= 0f) _fadeNonNormalHardSince += pausedFor;
        }

        // Keeps the freeze and gives up on it if the trainer never comes back.
        private void AiPauseTick()
        {
            if (Time.timeScale > 0f)
                Time.timeScale = 0f;

            if (Time.unscaledTime - _aiPausedAt > PAUSE_TIMEOUT_SECONDS)
                SetAiPaused(false, "the trainer did not resume in "
                    + PAUSE_TIMEOUT_SECONDS.ToString("F0", CultureInfo.InvariantCulture) + " s");
        }

        // The target scene may arrive as an alias ("hornet", "nkg") or as a registry
        // index — we resolve it exactly the same way the "boss" command does.
        private void SetTargetScene(string scene)
        {
            string resolved, label;
            if (TryResolveBoss(scene, out resolved, out label))
            {
                _targetScene = resolved;
                Log($"[AI] Target boss scene: {_targetScene} ({label})");
            }
            else
            {
                _targetScene = scene;
                Log($"[AI] Target boss scene: {_targetScene} (not recognized, passing it through as is)");
            }
        }

        private void SelectBoss(string query)
        {
            string scene, label;
            if (!TryResolveBoss(query, out scene, out label))
            {
                Log($"[AI] Boss not recognized: '{query}'. Send the 'bosses' command for the list.");
                PublishEvent(CommandErrorJson("boss " + query, "boss not recognized"));
                return;
            }

            _targetScene = scene;
            Log($"[AI] Boss selected: {label} ({scene})");
            PublishEvent("{\"status\": \"boss_selected\", \"scene\": \"" + scene
                + "\", \"label\": \"" + label + "\"}");
            TryRestart();
        }

        // The restart/teleport command is only accepted. The transition itself is performed
        // by TryPerformPendingTransition once the game frees up: if the scene is yanked
        // in the middle of the death or white arena-exit scenario, the camera fade stays
        // on screen and the hero stays in transitioning (see ResolveEntryGate).
        private void TryRestart()
        {
            if (_inMenuScene)
            {
                Log("[AI] Restart ignored: we are in the menu");
                return;
            }
            if (_restartPending) return;

            _restartPending = true;
            _restartRequested = true;
            _restartRequestedAt = Time.time;
            _restartTargetScene = _targetScene;
            _sceneChangedSinceRequest = false;
            _deferReasonLastLogged = "";

            string gate = ResolveEntryGate(_targetScene);
            _restartGate = gate;
            Log($"[AI] Fast restart: target '{_targetScene}', gate '{gate}' — command accepted, I will perform the transition once the game frees up");
        }

        // Runs on every tick, not only inside the scene-change arming window: if the hero is
        // still in 'transitioning' (or the scene-transition flag is still up) HERO_FROZEN_TIMEOUT
        // after the load, with no scene load running, the repair below is armed again. The
        // escalation is left to the watchdog itself: EnterScene first, manual placement second.
        private void RearmWatchdogForPersistentFreeze()
        {
            try
            {
                GameManager gm = GameManager.instance;
                if (gm == null || _inMenuScene)
                {
                    _heroFrozenSince = -1f;
                    return;
                }
                if (gm.IsLoadingSceneTransition)
                {
                    _heroFrozenSince = -1f;
                    return;
                }

                HeroController hero = HeroController.instance;
                bool heroFrozen = hero != null && hero.cState != null && hero.cState.transitioning;
                if (!heroFrozen && !gm.IsInSceneTransition)
                {
                    _heroFrozenSince = -1f;
                    return;
                }

                if (_heroFrozenSince < 0f || Time.unscaledTime < _heroFrozenSince)
                {
                    _heroFrozenSince = Time.unscaledTime;
                    return;
                }
                if (Time.unscaledTime - _heroFrozenSince < HERO_FROZEN_TIMEOUT) return;

                _heroFrozenSince = Time.unscaledTime;
                _watchdogTimer = 0.01f;
                Log($"[AI] Transition stuck for {HERO_FROZEN_TIMEOUT:F0}s with no scene load "
                    + $"(hero transitioning: {(heroFrozen ? 1 : 0)}, scene-transition flag: "
                    + $"{(gm.IsInSceneTransition ? 1 : 0)}) - repairing");
            }
            catch (Exception) {}
        }

        private void TransitionWatchdogTick(float unscaledDelta)
        {
            RearmWatchdogForPersistentFreeze();
            if (_watchdogTimer <= 0f) return;
            _watchdogTimer -= unscaledDelta;
            if (_watchdogTimer > 0f) return;

            try
            {
                GameManager gm = GameManager.instance;
                if (gm == null || _inMenuScene) return;

                if (gm.IsLoadingSceneTransition)
                {
                    _watchdogTimer = 1.5f;
                    return;
                }

                HeroController hero = HeroController.instance;
                bool heroFrozen = hero != null && hero.cState != null && hero.cState.transitioning;

                if (!heroFrozen)
                {
                    if (gm.IsInSceneTransition)
                        ReflectionHelper.SetField(gm, "<IsInSceneTransition>k__BackingField", false);
                    return;
                }

                if (Time.timeScale <= 0f)
                    Time.timeScale = 1f;

                TransitionPoint gate = null;
                string gateName = _targetGate;
                try
                {
                    var registry = TransitionPoint.TransitionPoints;
                    if (registry != null)
                    {
                        foreach (TransitionPoint tp in registry)
                        {
                            if (tp != null && tp.name == gateName)
                            {
                                gate = tp;
                                break;
                            }
                        }
                    }
                }
                catch (Exception) {}
                if (gate == null && _forcedEntryAttempts == 0)
                {
                    try
                    {
                        var names = new System.Text.StringBuilder();
                        var registry = TransitionPoint.TransitionPoints;
                        if (registry != null)
                            foreach (TransitionPoint tp in registry)
                                names.Append(tp != null ? tp.name : "null").Append("; ");
                        Log($"[AI] Active scene gates: {names}");
                    }
                    catch (Exception) {}
                }
                if (gate == null)
                    gate = FindTransitionGate(hero.transform, gateName);
                Log($"[AI] Hero is stuck in transitioning. Gate '{gateName}' found: {gate != null}{(gate != null ? $" ({gate.name})" : "")}, attempt #{_forcedEntryAttempts}");

                if (gate != null && _forcedEntryAttempts == 0)
                {
                    _forcedEntryAttempts++;
                    hero.StartCoroutine(hero.EnterScene(gate, 0f));
                    _watchdogTimer = 3f;
                    return;
                }

                _forcedEntryAttempts++;
                if (gate != null)
                {
                    Vector2 gp = gate.transform.position;
                    hero.transform.SetPosition2D(gp.x, gp.y + 1f);
                }
                ReflectionHelper.CallMethod(hero, "FinishedEnteringScene", true, false);
                gm.FinishedEnteringScene();
                gm.FadeSceneIn();

                try
                {
                    var heroRenderer = hero.GetComponentInChildren<Renderer>();
                    if (heroRenderer != null) heroRenderer.enabled = true;
                }
                catch (Exception) {}
                Log("[AI] Hero placed at the gate manually and unblocked");
            }
            catch (Exception e)
            {
                Log($"[AI] Transition watchdog error: {e}");
            }
        }

        private static TransitionPoint FindTransitionGate(UnityEngine.Transform heroTransform, string gateName)
        {
            try
            {
                var scene = UnityEngine.SceneManagement.SceneManager.GetActiveScene();
                GameObject[] roots = scene.GetRootGameObjects();
                foreach (GameObject root in roots)
                {
                    if (root.name == gateName)
                    {
                        TransitionPoint tp = root.GetComponent<TransitionPoint>();
                        if (tp != null) return tp;
                    }
                    TransitionPoint[] all = root.GetComponentsInChildren<TransitionPoint>(true);
                    foreach (TransitionPoint tp in all)
                    {
                        if (tp != null && tp.name == gateName)
                            return tp;
                    }
                }
                foreach (var loaded in UnityEngine.SceneManagement.SceneManager.GetAllScenes())
                {
                    if (!loaded.isLoaded || loaded == scene) continue;
                    foreach (GameObject root in loaded.GetRootGameObjects())
                    {
                        TransitionPoint[] all = root.GetComponentsInChildren<TransitionPoint>(true);
                        foreach (TransitionPoint tp in all)
                        {
                            if (tp != null && tp.name == gateName)
                                return tp;
                        }
                    }
                }
            }
            catch (Exception) {}
            return null;
        }

        // ---------------- Godhome boss selection ----------------

        private static string NormalizeQuery(string query)
        {
            if (query == null) return "";
            string norm = query.Trim().ToLowerInvariant();
            norm = norm.Replace(' ', '_').Replace('-', '_');
            while (norm.Contains("__")) norm = norm.Replace("__", "_");
            return norm.Trim('_');
        }

        // Scene name canonicalization: the user may type "gg_hornet_1"
        // in any case — we substitute the exact name from build settings.
        private static bool TryCanonicalizeScene(string normalized, out string scene)
        {
            foreach (string known in KnownScenes)
            {
                if (known.ToLowerInvariant() == normalized)
                {
                    scene = known;
                    return true;
                }
            }
            scene = null;
            return false;
        }

        // Parses a boss query: index in the list, scene name (any case),
        // short alias, exact or partial boss name.
        private static bool TryResolveBoss(string query, out string scene, out string label)
        {
            scene = null;
            label = null;
            if (string.IsNullOrWhiteSpace(query)) return false;

            string norm = NormalizeQuery(query);

            // 1. Index in the registry (1-based)
            int index;
            if (int.TryParse(norm, out index) && index >= 1 && index <= BossRegistry.Length)
            {
                scene = BossRegistry[index - 1].Scene;
                label = BossRegistry[index - 1].Label;
                return true;
            }

            // 2. Exact scene name from the registry (case-insensitive)
            foreach (BossEntry e in BossRegistry)
            {
                if (e.Scene.ToLowerInvariant() == norm)
                {
                    scene = e.Scene;
                    label = e.Label;
                    return true;
                }
            }

            // 3. Short alias (hornet, nkg, sisters, ...)
            string aliasScene;
            if (ExtraAliases.TryGetValue(norm, out aliasScene))
            {
                foreach (BossEntry e in BossRegistry)
                {
                    if (e.Scene == aliasScene)
                    {
                        scene = e.Scene;
                        label = e.Label;
                        return true;
                    }
                }
            }

            // 4. Exact boss name
            foreach (BossEntry e in BossRegistry)
            {
                if (NormalizeQuery(e.Label) == norm)
                {
                    scene = e.Scene;
                    label = e.Label;
                    return true;
                }
            }

            // 5. Scene name from build settings that did not make it into the registry
            // (GG_Spa, GG_Wyrm, GG_Engine and the like)
            string canonical;
            if (TryCanonicalizeScene(norm, out canonical))
            {
                scene = canonical;
                label = canonical + " (scene not in registry)";
                return true;
            }

            // 6. Partial match on scene/label; on ambiguity we
            // prefer the base version of the boss (not (Variant) and not *_V)
            string matched = null;
            string matchedLabel = null;
            foreach (BossEntry e in BossRegistry)
            {
                string sceneNorm = NormalizeQuery(e.Scene);
                if (!sceneNorm.Contains(norm) && !NormalizeQuery(e.Label).Contains(norm))
                    continue;
                if (matched != null)
                {
                    // Second candidate: narrow down to the base version if possible
                    bool curIsVariant = matched.EndsWith("_V") || matchedLabel.Contains("(Variant)");
                    bool newIsVariant = e.Scene.EndsWith("_V") || e.Label.Contains("(Variant)");
                    if (newIsVariant) continue;          // a variant is worse than the base version
                    if (curIsVariant) { matched = e.Scene; matchedLabel = e.Label; continue; }
                    return false;                        // two base versions — ambiguous
                }
                matched = e.Scene;
                matchedLabel = e.Label;
            }
            if (matched != null)
            {
                scene = matched;
                label = matchedLabel;
                return true;
            }

            // 7. Unfamiliar gg_* name — pass it through as is, maybe the scene exists
            if (norm.StartsWith("gg_"))
            {
                scene = query.Trim();
                label = scene + " (unknown scene, attempting to load)";
                return true;
            }

            return false;
        }

        private static string CurrentSceneName()
        {
            try
            {
                return UnityEngine.SceneManagement.SceneManager.GetActiveScene().name ?? "";
            }
            catch (Exception)
            {
                return "";
            }
        }

        // The registry goes to the pipe as an event, not as the %TEMP%/hk_ai_bosses.json file.
        private string BossListJson()
        {
            var sb = new System.Text.StringBuilder();
            sb.Append("{\"status\": \"boss_list\", \"target_scene\": \"").Append(_targetScene)
              .Append("\", \"count\": ").Append(BossRegistry.Length).Append(", \"bosses\": [");
            for (int i = 0; i < BossRegistry.Length; i++)
            {
                if (i > 0) sb.Append(", ");
                sb.Append("{\"index\": ").Append(i + 1)
                  .Append(", \"scene\": \"").Append(BossRegistry[i].Scene)
                  .Append("\", \"label\": \"").Append(BossRegistry[i].Label).Append("\"}");
            }
            sb.Append("]}");
            return sb.ToString();
        }

        // ---------------- Scene gates ----------------

        // All TransitionPoints actually lying in the scene (accounting for additive
        // loading: the registry also contains gates belonging to other scenes).
        private List<string> CollectSceneGates(string sceneName)
        {
            List<string> result = new List<string>();
            try
            {
                UnityEngine.SceneManagement.Scene target;
                if (string.IsNullOrEmpty(sceneName))
                {
                    target = UnityEngine.SceneManagement.SceneManager.GetActiveScene();
                }
                else
                {
                    target = UnityEngine.SceneManagement.SceneManager.GetSceneByName(sceneName);
                    if (!target.IsValid() || !target.isLoaded) return result;
                }

                List<TransitionPoint> registry = TransitionPoint.TransitionPoints;
                if (registry == null) return result;

                for (int i = 0; i < registry.Count; i++)
                {
                    TransitionPoint tp = registry[i];
                    if (tp == null) continue;
                    if (tp.gameObject.scene != target) continue;
                    string name = tp.name;
                    if (!string.IsNullOrEmpty(name) && !result.Contains(name))
                        result.Add(name);
                }
            }
            catch (Exception) {}
            return result;
        }

        // GameManager.EnterHero(additiveGateSearch: true) looks for EntryGateName only
        // among the TransitionPoints of the scene BEING LOADED and on a miss it does
        // `Debug.LogError("Searching in next scene for TransitionGate failed."); return;`
        // — that is, no EnterScene, no FinishedEnteringScene and no FadeSceneIn.
        // The hero stays in transitioning forever, and the fade (white after death / arena
        // exit) stays hanging on screen. That is why the gate must exist in the scene.
        private string PickEntryGate(List<string> gates, string configured)
        {
            if (gates == null || gates.Count == 0) return null;
            if (!string.IsNullOrEmpty(configured) && gates.Contains(configured)) return configured;
            for (int i = 0; i < gates.Count; i++)
            {
                if (gates[i].IndexOf("dream", StringComparison.OrdinalIgnoreCase) >= 0)
                    return gates[i];
            }
            return gates[0];
        }

        private void LearnSceneGate(string sceneName)
        {
            try
            {
                if (string.IsNullOrEmpty(sceneName)) return;
                List<string> gates = CollectSceneGates(sceneName);
                if (gates.Count == 0) return;

                string learned = PickEntryGate(gates, _targetGate);
                if (string.IsNullOrEmpty(learned)) return;

                string previous;
                if (_sceneGates.TryGetValue(sceneName, out previous) && previous == learned) return;

                _sceneGates[sceneName] = learned;
                Log($"[AI] Scene gates '{sceneName}': {string.Join(", ", gates.ToArray())} | entry -> '{learned}'");
            }
            catch (Exception) {}
        }

        private string ResolveEntryGate(string targetScene)
        {
            string configured = _targetGate;
            try
            {
                List<string> loaded = CollectSceneGates(targetScene);
                if (loaded.Count > 0)
                {
                    string gate = PickEntryGate(loaded, configured);
                    if (!string.IsNullOrEmpty(gate))
                        return gate;
                }

                string learned;
                if (!string.IsNullOrEmpty(targetScene)
                    && _sceneGates.TryGetValue(targetScene, out learned)
                    && !string.IsNullOrEmpty(learned))
                    return learned;
            }
            catch (Exception) {}

            if (!string.IsNullOrEmpty(configured)) return configured;
            return DEFAULT_ENTRY_GATE;
        }

        // ---------------- Deferred restart ----------------

        private bool IsHeroDying(HeroController hero)
        {
            try
            {
                if (hero == null || hero.cState == null) return true;
                if (hero.cState.dead || hero.cState.hazardDeath) return true;
                // The scene already changed after the request — the game's own end-of-fight scenario has finished.
                if (_sceneChangedSinceRequest) return false;
                // HP is zeroed on death in Godhome (without setting cState.dead), but in the
                // hall it may stay zero even after a dream return, so we treat it as a stop
                // factor only while the hero is in the target arena.
                // controlReqlinquished is deliberately NOT used: in the Godhome hub it is set
                // even for a live hero with full HP (in the log hp=9, controlReqlinquished=True),
                // which made the restart run into the 10-second deadline.
                if (CurrentSceneName() == _restartTargetScene)
                {
                    PlayerData pd = PlayerData.instance;
                    if (pd != null && pd.health <= 0) return true;
                }
            }
            catch (Exception) {}
            return false;
        }

        private static string DescribeHeroState(HeroController hero)
        {
            try
            {
                PlayerData pd = PlayerData.instance;
                int hp = (pd != null) ? pd.health : -1;
                return $"hero in the death/return scenario (hp={hp}, dead={hero.cState.dead}, "
                     + $"scene {UnityEngine.SceneManagement.SceneManager.GetActiveScene().name})";
            }
            catch (Exception)
            {
                return "hero is busy with the game's scenario";
            }
        }

        // The white arena exit from the fight (boss victory): BossSceneController.EndSceneDelayed()
        // first plays "GG TRANSITION OUT STATUE", and only then leaves the scene.
        private static bool IsArenaExitRunning()
        {
            try
            {
                BossSceneController bsc = BossSceneController.Instance;
                if (bsc == null) return false;
                return ReflectionHelper.GetField<BossSceneController, bool>(bsc, "isTransitioningOut");
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static bool IsGameSettled(GameManager gm, HeroController hero)
        {
            if (gm == null) return false;
            if (gm.IsInSceneTransition || gm.IsLoadingSceneTransition) return false;
            if (hero == null || hero.cState == null) return false;
            if (hero.cState.transitioning || hero.cState.hazardDeath || hero.cState.hazardRespawning) return false;
            return true;
        }

        private void TryPerformPendingTransition()
        {
            if (!_restartRequested) return;

            try
            {
                if (_inMenuScene) return;

                GameManager gm = GameManager.instance;
                if (gm == null) return;
                if (gm.IsLoadingSceneTransition) return;

                HeroController hero = HeroController.instance;
                float waited = Time.time - _restartRequestedAt;
                bool forced = waited >= RESTART_FORCE_TIMEOUT;

                // The game can leave IsInSceneTransition up after the scene has settled. Past
                // the force timeout that flag is stale, and while it stays up this method
                // returns before doing anything - so every restart turns into a silent no-op
                // forever, because nothing else clears it once the scene-change window has
                // passed. The hero being free is the same condition the transition watchdog
                // uses to clear the same flag.
                if (gm.IsInSceneTransition)
                {
                    if (!forced || (hero != null && hero.cState != null && hero.cState.transitioning))
                        return;
                    Log($"[AI] Restart: the scene-transition flag was still up after {waited:F1}s "
                        + "with the hero free - clearing the stale flag");
                    ReflectionHelper.SetField(gm, "<IsInSceneTransition>k__BackingField", false);
                }

                if (hero == null || hero.cState == null) return;
                if (hero.cState.transitioning || hero.cState.hazardDeath || hero.cState.hazardRespawning) return;

                string deferReason = null;
                if (!forced && IsHeroDying(hero))
                    deferReason = DescribeHeroState(hero);       // death / dream-return scenario
                else if (!forced && IsArenaExitRunning())
                    deferReason = "the arena is playing its own white exit from the fight";

                if (deferReason != null)
                {
                    if (_deferReasonLastLogged != deferReason)
                    {
                        _deferReasonLastLogged = deferReason;
                        Log($"[AI] Restart deferred: {deferReason} — waiting for the game to finish its scenario");
                    }
                    return;
                }

                _deferReasonLastLogged = "";

                if (forced)
                    Log($"[AI] Waited {waited:F1}s — forcing the transition (the game state never freed up)");

                string gate = _restartGate;
                if (string.IsNullOrEmpty(gate)) gate = ResolveEntryGate(_restartTargetScene);

                Log($"[AI] Fast restart: transitioning to scene '{_restartTargetScene}' through gate '{gate}' (waited {waited:F2}s)");

                _restartRequested = false;
                _restartGate = "";
                _watchdogTimer = 0f;

                gm.BeginSceneTransition(new GameManager.SceneLoadInfo
                {
                    SceneName = _restartTargetScene,
                    EntryGateName = gate,
                    WaitForSceneTransitionCameraFade = true,
                    Visualization = GameManager.SceneLoadVisualizations.Default,
                    AlwaysUnloadUnusedAssets = false
                });
            }
            catch (Exception e)
            {
                // _restartPending has to fall with _restartRequested: it is cleared only by a
                // scene change, and a failed forced transition never changes the scene. Left
                // standing it makes TryRestart reject every later restart for the rest of the
                // fight (a silent no-op) while the telemetry keeps claiming "restart_pending: 1".
                _restartRequested = false;
                _restartPending = false;
                Log($"[AI] Deferred transition error: {e}");
            }
        }

        // ---------------- Fade watchdog ----------------

        // The game's fades (including the white RESPAWN FADE/Dream Return) live on the DDOL
        // object GameCameras. If the scene is yanked out of the middle of such a scenario, the
        // fade stays in an opaque state, and the stock FadeInFailSafe is never started in this
        // build of the game. So we clear the stuck fade ourselves — with the same event the game uses.
        private void FadeWatchdogTick()
        {
            try
            {
                GameCameras gc = GameCameras.instance;
                if (gc == null || gc.cameraFadeFSM == null || gc.cameraFadeFSM.Fsm == null) return;

                string state = gc.cameraFadeFSM.Fsm.ActiveStateName;
                if (string.IsNullOrEmpty(state)) return;

                if (state != _fadeStateLastLogged)
                {
                    if (state != "Normal")
                        Log($"[AI] Camera fade -> '{state}' (scene {CurrentSceneName()})");
                    _fadeStateLastLogged = state;
                }

                if (state == "Normal")
                {
                    _fadeNotNormalSince = -1f;
                    _fadeRescueAttempts = 0;
                    _fadeNonNormalHardSince = -1f;
                    return;
                }

                // We only wait when the game is genuinely not busy with anything: during an
                // honest transition the fade is also not "Normal", and we must not touch it.
                // A real scene load is exempt: it fades in on its own. But a game that only
                // *looks* busy (a stale transition flag, a hero frozen in 'transitioning') used
                // to keep the white fade on screen for hours with this log silent, so past
                // FADE_HARD_TIMEOUT the fade is forced regardless of IsGameSettled.
                GameManager gm = GameManager.instance;
                if (gm != null && gm.IsLoadingSceneTransition)
                {
                    _fadeNonNormalHardSince = -1f;
                }
                else if (_fadeNonNormalHardSince < 0f || Time.unscaledTime < _fadeNonNormalHardSince)
                {
                    _fadeNonNormalHardSince = Time.unscaledTime;
                }
                else if (Time.unscaledTime - _fadeNonNormalHardSince >= FADE_HARD_TIMEOUT)
                {
                    _fadeNonNormalHardSince = Time.unscaledTime;
                    Log($"[AI] Fade has been '{state}' for over {FADE_HARD_TIMEOUT:F0}s with no "
                        + "scene load running - forcing FADE SCENE IN");
                    gc.cameraFadeFSM.Fsm.Event("FADE SCENE IN");
                }

                if (!IsGameSettled(gm, HeroController.instance))
                {
                    _fadeNotNormalSince = -1f;
                    return;
                }

                float limit = (state == "FadingOut") ? FADE_STUCK_FADINGOUT_TIMEOUT : FADE_STUCK_TIMEOUT;
                if (_fadeNotNormalSince < 0f)
                {
                    _fadeNotNormalSince = Time.unscaledTime;
                    return;
                }

                float stuck = Time.unscaledTime - _fadeNotNormalSince;
                if (stuck < limit) return;
                if (_fadeRescueAttempts >= 5)
                {
                    _fadeNotNormalSince = Time.unscaledTime;
                    return;
                }

                _fadeRescueAttempts++;
                _fadeNotNormalSince = Time.unscaledTime;
                Log($"[AI] Fade stuck in '{state}' for {stuck:F1}s (attempt {_fadeRescueAttempts}) — sending FADE SCENE IN");
                gc.cameraFadeFSM.Fsm.Event("FADE SCENE IN");
            }
            catch (Exception) {}
        }

        private static string CommandErrorJson(string command, string reason)
        {
            string safeCommand = (command ?? "").Replace("\"", "'");
            string safeReason = (reason ?? "").Replace("\"", "'");
            return "{\"status\": \"command_error\", \"command\": \"" + safeCommand
                + "\", \"reason\": \"" + safeReason + "\"}";
        }

        // Returns the hero to the arena entrance of the current scene without reloading the scene.
        private void WarpHeroToGate()
        {
            try
            {
                if (_inMenuScene)
                {
                    Log("[AI] Warp ignored: we are in the menu");
                    return;
                }

                HeroController hero = HeroController.instance;
                if (hero == null)
                {
                    Log("[AI] Warp: no hero in the scene");
                    return;
                }

                TransitionPoint gate = FindTransitionGate(hero.transform, _targetGate);
                if (gate != null)
                    hero.transform.SetPosition2D(gate.transform.position.x, gate.transform.position.y + 1f);
                else
                    Log("[AI] Warp: gate not found, the hero stays in place");

                if (hero.cState != null && hero.cState.transitioning)
                    ReflectionHelper.CallMethod(hero, "FinishedEnteringScene", true, false);

                // Staying frozen is the point of a pause: a warp must not lift it behind the
                // trainer's back (OnTick drains commands before it checks the pause, so without
                // this guard one live frame would run with "paused: 1" still being reported).
                if (!_aiPaused && Time.timeScale <= 0f) Time.timeScale = 1f;

                try
                {
                    var heroRenderer = hero.GetComponentInChildren<Renderer>();
                    if (heroRenderer != null) heroRenderer.enabled = true;
                }
                catch (Exception) {}

                Log("[AI] Warp: hero returned to the arena gate");
            }
            catch (Exception e)
            {
                Log($"[AI] Warp error: {e}");
            }
        }

        // ---------------- Pipe server ----------------

        // One slot per client. The thread blocks in ConnectNamedPipe until a client
        // connects, and then serves that client itself (PumpClient).
        private void PipeListenerLoop()
        {
            for (int slot = 0; slot < MAX_PIPE_CLIENTS; slot++)
            {
                int slotId = slot;
                var thread = new Thread(() => PipeSlotLoop(slotId))
                {
                    IsBackground = true,
                    Name = "HK_AI_PipeSlot" + slotId
                };
                thread.Start();
            }

            var watchdog = new Thread(PipeSlotWatchdogLoop)
            {
                IsBackground = true,
                Name = "HK_AI_PipeWatchdog"
            };
            watchdog.Start();
        }

        // A write to a client that stopped reading blocks forever once the pipe's out buffer
        // is full. The slot is marked as "writing" so that PipeSlotWatchdogLoop can cancel it.
        private bool WriteToClient(int slot, IntPtr pipe, byte[] data)
        {
            _slotWriteSince[slot] = Environment.TickCount;
            try
            {
                return Win32Pipe.Write(pipe, data, data.Length);
            }
            finally
            {
                _slotWriteSince[slot] = 0;
            }
        }

        // Keeps a hung client from eating a slot: if a write has been stuck for WRITE_STUCK_MS,
        // the blocking WriteFile is cancelled (CancelSynchronousIo). The write then fails, the
        // slot loop closes the handle and creates a fresh instance — so a debugger that froze
        // cannot take away a slot that training needs.
        private void PipeSlotWatchdogLoop()
        {
            while (!_shuttingDown)
            {
                Thread.Sleep(500);

                for (int slot = 0; slot < MAX_PIPE_CLIENTS; slot++)
                {
                    int since = _slotWriteSince[slot];
                    if (since == 0) continue;
                    // unchecked: TickCount wraps around roughly every 25 days.
                    if (unchecked(Environment.TickCount - since) < WRITE_STUCK_MS) continue;

                    uint threadId = _slotThreads[slot];
                    Log($"[AI] Pipe slot {slot}: the client is not reading — cancelling the stuck write "
                        + $"({WRITE_STUCK_MS} ms) and freeing the slot");
                    if (!Win32Pipe.CancelBlockingWrite(threadId))
                    {
                        // The cancel could not be issued (the thread is already gone): close the
                        // handle instead, so the slot does not stay stuck on this client.
                        Log($"[AI] Pipe slot {slot}: could not cancel the write (thread {threadId}) — closing the handle");
                        Win32Pipe.Close(_slotHandles[slot]);
                    }
                }
            }
        }

        private void PipeSlotLoop(int slot)
        {
            byte[] readBuf = new byte[4096];
            var lineBuf = new StringBuilder();
            var events = new List<string>();
            var payload = new StringBuilder();

            while (!_shuttingDown)
            {
                IntPtr pipe = Win32Pipe.Create(@"\\.\pipe\" + PIPE_NAME, MAX_PIPE_CLIENTS, 65536, 8192);
                if (pipe == IntPtr.Zero)
                {
                    if (!_shuttingDown)
                        Log($"[AI] Pipe slot {slot}: CreateNamedPipe failed (error {Win32Pipe.LastError()})");
                    Thread.Sleep(1000);
                    continue;
                }

                // The watchdog needs to know which handle and thread belong to this slot.
                _slotHandles[slot] = pipe;
                _slotThreads[slot] = Win32Pipe.CurrentThreadId();

                try
                {
                    if (!Win32Pipe.Connect(pipe))
                    {
                        if (!_shuttingDown)
                        {
                            Log($"[AI] Pipe slot {slot}: ConnectNamedPipe failed (error {Win32Pipe.LastError()})");
                            Thread.Sleep(1000);
                        }
                        continue;
                    }

                    Log($"[AI] Pipe slot {slot}: client connected");
                    byte[] hello = Utf8("{\"status\": \"pipe_hello\", \"protocol\": " + PROTOCOL_VERSION
                        + ", \"mod_version\": \"" + MOD_VERSION + "\"}\n");
                    if (WriteToClient(slot, pipe, hello))
                        PumpClient(slot, pipe, readBuf, lineBuf, events, payload);
                }
                catch (Exception e)
                {
                    if (!_shuttingDown)
                        Log($"[AI] Pipe slot {slot}: error — {e.Message}");
                }
                finally
                {
                    _slotHandles[slot] = IntPtr.Zero;
                    _slotThreads[slot] = 0;
                    _slotWriteSince[slot] = 0;
                    Win32Pipe.Close(pipe);
                }
            }
        }

        // Serving one client. Everything on a single thread: first we write
        // what has accumulated, then we poll for and read commands. There is never an
        // unfinished operation pending on the handle, so a write cannot hang on an
        // unfinished read (that is exactly what killed the previous implementation).
        private void PumpClient(int slot, IntPtr pipe, byte[] readBuf, StringBuilder lineBuf,
            List<string> events, StringBuilder payload)
        {
            long lastSeq = -1;
            long lastOutboxId;
            // We do not replay events accumulated before the connect:
            // the client only cares about replies to its own commands.
            lock (_sync) { lastOutboxId = _outboxSeq; }

            while (!_shuttingDown)
            {
                events.Clear();
                string toSend = null;

                lock (_sync)
                {
                    // 1) One-shot events: every client receives them exactly once.
                    if (_outboxSeq != lastOutboxId)
                    {
                        foreach (KeyValuePair<long, string> ev in _outbox)
                            if (ev.Key > lastOutboxId) events.Add(ev.Value);
                        lastOutboxId = _outboxSeq;
                    }

                    // 2) Telemetry: only the freshest frame, we do not accumulate old ones.
                    if (_seq != lastSeq)
                    {
                        lastSeq = _seq;
                        toSend = _latestJson;
                    }

                    if (events.Count == 0 && toSend == null)
                        Monitor.Wait(_sync, PIPE_POLL_MS);
                }

                if (events.Count > 0 || toSend != null)
                {
                    payload.Length = 0;
                    for (int i = 0; i < events.Count; i++)
                        payload.Append(events[i]).Append('\n');
                    if (toSend != null)
                        payload.Append(toSend).Append('\n');

                    byte[] bytes = Utf8(payload.ToString());
                    if (!WriteToClient(slot, pipe, bytes))
                        return; // the client dropped off
                }

                // Non-blocking draining of the client's commands.
                if (!DrainCommands(pipe, readBuf, lineBuf))
                    return;
            }
        }

        // PeekNamedPipe reports how many bytes are ready, and only after that do we read —
        // ReadFile cannot hang waiting for data.
        private bool DrainCommands(IntPtr pipe, byte[] readBuf, StringBuilder lineBuf)
        {
            while (true)
            {
                uint available;
                if (!Win32Pipe.Peek(pipe, out available))
                    return false; // disconnect: the client closed

                if (available == 0)
                    return true;

                int toRead = (int)Math.Min(available, (uint)readBuf.Length);
                int n = Win32Pipe.Read(pipe, readBuf, toRead);
                if (n <= 0)
                    return false;

                lineBuf.Append(Encoding.UTF8.GetString(readBuf, 0, n));

                while (true)
                {
                    string text = lineBuf.ToString();
                    int idx = text.IndexOf('\n');
                    if (idx < 0) break;
                    string line = text.Substring(0, idx).TrimEnd('\r').Trim();
                    lineBuf.Remove(0, idx + 1);
                    if (line.Length > 0)
                        _incomingCommands.Enqueue(line);
                }

                if (lineBuf.Length > 65536) // a stream of garbage with no line breaks — reset it
                    lineBuf.Remove(0, lineBuf.Length - 1024);
            }
        }

        private static byte[] Utf8(string text)
        {
            return Encoding.UTF8.GetBytes(text);
        }

        private void Publish(string json)
        {
            lock (_sync)
            {
                _latestJson = json;
                _seq++;
                Monitor.PulseAll(_sync);
            }
        }

        // One-shot event: unlike telemetry it is not overwritten by the newest
        // frame, but delivered to every connected client exactly once.
        // The "event": 1 marker tells the client this is not telemetry — otherwise
        // the event would replace the last observation frame in Python.
        private void PublishEvent(string json)
        {
            string marked = (json != null && json.StartsWith("{\"status\""))
                ? "{\"event\": 1, " + json.Substring(1)
                : json;
            lock (_sync)
            {
                _outboxSeq++;
                _outbox.Add(new KeyValuePair<long, string>(_outboxSeq, marked));
                while (_outbox.Count > MAX_OUTBOX)
                    _outbox.RemoveAt(0);
                Monitor.PulseAll(_sync);
            }
        }

        private string StatusJson(string status)
        {
            return "{\"status\": \"" + status + "\", \"restart_pending\": " + (_restartPending ? 1 : 0)
                + ", \"scene\": \"" + CurrentSceneName() + "\"}";
        }

        private bool IsAttackAnimation(string animName)
        {
            if (string.IsNullOrEmpty(animName)) return false;
            string lower = animName.ToLower();
            
            if (lower.Contains("attack")) return true;
            if (lower.Contains("slash")) return true;
            if (lower.Contains("shoot")) return true;
            if (lower.Contains("fire")) return true;
            if (lower.Contains("charge")) return true;
            if (lower.Contains("dash_attack")) return true;
            if (lower.Contains("spit")) return true;
            if (lower.Contains("burst")) return true;
            if (lower.Contains("spin")) return true;
            if (lower.Contains("slam")) return true;
            if (lower.Contains("strike")) return true;
            if (lower.Contains("throw")) return true;
            if (lower.Contains("lunge")) return true;
            if (lower.Contains("pounce")) return true;
            
            if (lower.Contains("idle")) return false;
            if (lower.Contains("walk")) return false;
            if (lower.Contains("run")) return false;
            if (lower.Contains("stun")) return false;
            if (lower.Contains("death")) return false;
            if (lower.Contains("land")) return false;
            if (lower.Contains("turn")) return false;
            if (lower.Contains("appear")) return false;
            if (lower.Contains("intro")) return false;
            if (lower.Contains("roar")) return false;
            if (lower.Contains("taunt")) return false;
            
            return false;
        }

        private bool IsAttackFsmState(string stateName)
        {
            if (string.IsNullOrEmpty(stateName)) return false;
            string lower = stateName.ToLower();

            if (lower.Contains("antic")) return true;
            if (lower.Contains("attack")) return true;
            if (lower.Contains("slam")) return true;
            if (lower.Contains("charge")) return true;
            if (lower.Contains("swipe")) return true;
            if (lower.Contains("stomp")) return true;
            if (lower.Contains("strike")) return true;
            if (lower.Contains("shoot")) return true;
            if (lower.Contains("spit")) return true;
            if (lower.Contains("smash")) return true;
            if (lower.Contains("pound")) return true;

            if (lower.Contains("idle")) return false;
            if (lower.Contains("recover")) return false;
            if (lower.Contains("cooldown")) return false;
            if (lower.Contains("hurt")) return false;
            if (lower.Contains("stun")) return false;
            if (lower.Contains("dazed")) return false;
            if (lower.Contains("death")) return false;
            if (lower.Contains("intro")) return false;
            if (lower.Contains("wake")) return false;
            if (lower.Contains("struggle")) return false;
            if (lower.Contains("turn")) return false;
            if (lower.Contains("pause")) return false;
            if (lower.Contains("init")) return false;
            if (lower.Contains("wait")) return false;

            return false;
        }

        private void OnHeroUpdate()
        {
            // Telemetry on every HeroUpdate (~60 records/sec at 60fps) is published
            // into the \\.\pipe\hk_ai_mod pipe. The Python side reads line by line and syncs
            // its steps to the arrival of a new record — no sleeps and no file mtime polling.

            try
            {
                if (HeroController.instance != null && PlayerData.instance != null && !HeroController.instance.cState.transitioning)
                {
                    var hero = HeroController.instance;
                    float x = hero.transform.position.x;
                    float y = hero.transform.position.y;
                    int hp = PlayerData.instance.health;
                    int max_hp = PlayerData.instance.maxHealth;
                    int mana = PlayerData.instance.MPCharge;
                    
                    float vel_x = hero.current_velocity.x;
                    float vel_y = hero.current_velocity.y;
                    
                    bool grounded = hero.cState.onGround;
                    bool facing_right = hero.cState.facingRight;
                    bool is_attacking = hero.cState.attacking;
                    bool is_dashing = hero.cState.dashing;
                    bool is_jumping = hero.cState.jumping;
                    bool is_falling = hero.cState.falling;
                    bool is_recoiling = hero.cState.recoiling;
                    bool is_dead = hero.cState.dead;
                    
                    bool was_hit = hp < _lastPlayerHp;
                    if (was_hit) _hitCounter++;
                    _lastPlayerHp = hp;
                    
                    if (_currentBoss == null && !_bossDead)
                    {
                        HealthManager? bestCandidate = null;
                        int bestHp = 0;

                        try
                        {
                            BossSceneController bsc = BossSceneController.Instance;
                            if (bsc != null && bsc.bosses != null)
                            {
                                foreach (HealthManager hm in bsc.bosses)
                                {
                                    if (hm == null) continue;
                                    int hpNow = hm.hp;
                                    if (hpNow > 0 && hpNow > bestHp)
                                    {
                                        bestCandidate = hm;
                                        bestHp = hpNow;
                                    }
                                }
                            }
                        }
                        catch (Exception) {}

                        if (bestCandidate == null)
                        {
                            foreach (HealthManager hm in GameObject.FindObjectsOfType<HealthManager>())
                            {
                                int bossCandidateHp = hm.hp;
                                if (bossCandidateHp > 20 && bossCandidateHp > bestHp)
                                {
                                    bestCandidate = hm;
                                    bestHp = bossCandidateHp;
                                }
                            }
                        }

                        if (bestCandidate != null)
                        {
                            _currentBoss = bestCandidate;
                            _lastBossVelX = 0f;
                            _lastBossVelY = 0f;
                            _lastBossHpKnown = bestHp;
                            Log($"[AI] Boss selected: {bestCandidate.gameObject.name} (hp={bestHp})");
                        }
                    }

                    int bossHp = 0;
                    if (!_bossDead)
                    {
                        try
                        {
                            BossSceneController bsc = BossSceneController.Instance;
                            if (bsc != null && bsc.bosses != null)
                            {
                                // The fight is over when EVERY boss of the arena is down, not
                                // when one of them is: Mantis Lords, Watcher Knights and the
                                // phase fights keep the next one alive in the same list, and
                                // latching on the first death ended such fights early.
                                bool anyAlive = false;
                                int arenaBosses = 0;
                                foreach (HealthManager hm in bsc.bosses)
                                {
                                    if (hm == null) continue;
                                    arenaBosses++;
                                    if (!hm.isDead && hm.hp > 0)
                                    {
                                        anyAlive = true;
                                        break;
                                    }
                                }

                                if (arenaBosses > 0 && !anyAlive)
                                {
                                    _bossDead = true;
                                    _bossDeadConfirmed = true;
                                    Log($"[AI] Boss is dead (all {arenaBosses} boss(es) of the arena)");
                                }
                            }
                        }
                        catch (Exception) {}
                    }

                    if (_currentBoss != null && !_bossDead)
                    {
                        if (_currentBoss.hp <= 0 || _currentBoss.isDead)
                        {
                            _bossDead = true;
                            _bossDeadConfirmed = true;
                            Log("[AI] Boss is dead (current HealthManager)");
                        }
                    }

                    // The scene as a whole, filled by the scan below: the arena set does not hold
                    // the health that ends this fight, so the trainer has to be able to see every
                    // pool that is being damaged, and how much damage has been dealt in total.
                    int sceneCount = 0;
                    int sceneHpNow = 0;
                    StringBuilder sceneDetail = new StringBuilder();

                    if (!_bossDead)
                    {
                        try
                        {
                            // Scene-wide, so the dead-object test is only a suspicion: it may fire
                            // for an enemy that has nothing to do with the fight, which is why it
                            // does not count as a confirmed death (see _bossDeadConfirmed). The
                            // same pass is where the damage counter is fed, so it is not wasted.
                            sceneCount = 0;
                            sceneHpNow = 0;
                            sceneDetail.Length = 0;
                            foreach (HealthManager hm in GameObject.FindObjectsOfType<HealthManager>())
                            {
                                if (hm == null) continue;
                                int hmHp = Math.Max(0, hm.hp);
                                int id = hm.GetInstanceID();
                                sceneCount++;
                                sceneHpNow += hmHp;
                                int wasHp;
                                if (_sceneHealthLast.TryGetValue(id, out wasHp) && wasHp > hmHp)
                                    _sceneDamageTotal += wasHp - hmHp;
                                _sceneHealthLast[id] = hmHp;
                                if (sceneDetail.Length < 400)
                                {
                                    if (sceneDetail.Length > 0) sceneDetail.Append('|');
                                    sceneDetail.Append(hm.gameObject.name
                                            .Replace('"', '_').Replace('|', '_').Replace(':', '_'))
                                        .Append(':').Append(hmHp).Append(':')
                                        .Append((hm.isDead || hm.hp <= 0) ? 1 : 0);
                                }

                                if (hm.hp > 20 && (hm.isDead || hm.hp <= 0))
                                {
                                    _bossDead = true;
                                    Log($"[AI] Boss is dead (HM scan: {hm.gameObject.name})");
                                }
                            }
                        }
                        catch (Exception) {}
                    }

                    if (_bossDeadConfirmed || _currentBoss == null)
                        bossHp = 0;
                    else
                        bossHp = Math.Max(0, _currentBoss.hp);

                    // The whole arena, not just the boss we latched onto. A single HealthManager can
                    // sit at a floor (an armour whose last points come off somewhere else, a boss
                    // whose death is the death of a second entity) while the fight is decided by
                    // something this track does not name - the trainer then optimises a number that
                    // stops moving halfway through. These four fields expose the set the game itself
                    // uses to end the arena, so the truth is readable instead of guessed at.
                    int arenaBossCount = 0;
                    int arenaAliveCount = 0;
                    int arenaHpSum = 0;
                    StringBuilder arenaDetail = new StringBuilder();
                    try
                    {
                        BossSceneController bscArena = BossSceneController.Instance;
                        if (bscArena != null && bscArena.bosses != null)
                        {
                            foreach (HealthManager hm in bscArena.bosses)
                            {
                                if (hm == null) continue;
                                arenaBossCount++;
                                bool hmDead = hm.isDead || hm.hp <= 0;
                                int hmHp = Math.Max(0, hm.hp);
                                if (!hmDead) arenaAliveCount++;
                                arenaHpSum += hmHp;
                                if (arenaDetail.Length > 0) arenaDetail.Append('|');
                                arenaDetail.Append(hm.gameObject.name
                                        .Replace('"', '_').Replace('\\', '_')
                                        .Replace('|', '_').Replace(':', '_'))
                                    .Append(':').Append(hmHp).Append(':').Append(hmDead ? 1 : 0);
                            }
                        }
                    }
                    catch (Exception) {}

                    if (_lastBossHpKnown > bossHp)
                        _bossDamageTotal += _lastBossHpKnown - bossHp;
                    _lastBossHpKnown = bossHp;
                    float bossX = _currentBoss != null ? _currentBoss.transform.position.x : 0f;
                    float bossY = _currentBoss != null ? _currentBoss.transform.position.y : 0f;
                    
                    float boss_vel_x = 0f, boss_vel_y = 0f;
                    bool boss_facing_right = true;
                    string boss_state = "idle";
                    bool boss_is_attacking = false;
                    bool near_hazard = false;
                    
                    if (_currentBoss != null)
                    {
                        Rigidbody2D bossRb = _currentBoss.GetComponent<Rigidbody2D>();
                        if (bossRb != null)
                        {
                            boss_vel_x = bossRb.velocity.x;
                            boss_vel_y = bossRb.velocity.y;
                        }
                        
                        boss_facing_right = _currentBoss.transform.localScale.x >= 0f;
                        
                        tk2dSpriteAnimator animator = _currentBoss.GetComponentInChildren<tk2dSpriteAnimator>();
                        if (animator != null && animator.CurrentClip != null)
                        {
                            boss_state = animator.CurrentClip.name;
                        }
                        
                        float accel_x = Mathf.Abs(boss_vel_x - _lastBossVelX);
                        float accel_y = Mathf.Abs(boss_vel_y - _lastBossVelY);
                        if (accel_x > 15f || accel_y > 15f)
                        {
                            boss_is_attacking = true;
                        }
                        
                        _lastBossVelX = boss_vel_x;
                        _lastBossVelY = boss_vel_y;

                        string bossObjName = _currentBoss.gameObject.name.ToLower();
                        {
                            PlayMakerFSM[] fsms = _currentBoss.GetComponentsInChildren<PlayMakerFSM>();
                            foreach (PlayMakerFSM fsm in fsms)
                            {
                                if (fsm == null || fsm.Fsm == null) continue;
                                string stateName = fsm.Fsm.ActiveStateName;
                                if (string.IsNullOrEmpty(stateName)) continue;

                                string logKey = fsm.FsmName + ":" + stateName;
                                if (_seenFsmStates.Add(logKey))
                                {
                                    bool classifiedAsAttack = IsAttackFsmState(stateName);
                                    Log($"[FSM] '{fsm.FsmName}' -> state '{stateName}' | attack={classifiedAsAttack}");
                                }

                                if (IsAttackFsmState(stateName))
                                {
                                    boss_is_attacking = true;
                                    boss_state = stateName;
                                }

                                if (!_bossDead && stateName == "Death Anim Start")
                                {
                                    _bossDead = true;
                                    _bossDeadConfirmed = true;
                                    Log("[AI] Boss is dead (FSM Death Anim Start)");
                                }
                            }
                        }
                    }
                    
                    foreach (DamageHero dh in GameObject.FindObjectsOfType<DamageHero>())
                    {
                        if (dh.damageDealt > 0 && dh.gameObject.activeInHierarchy && dh.enabled)
                        {
                            float dhx = dh.transform.position.x;
                            float dhy = dh.transform.position.y;
                            float dist = Vector2.Distance(new Vector2(x, y), new Vector2(dhx, dhy));

                            if (dist < 4f)
                            {
                                near_hazard = true;

                                bool belongsToBoss = _currentBoss != null &&
                                    dh.transform.IsChildOf(_currentBoss.transform);

                                if (belongsToBoss)
                                {
                                    boss_is_attacking = true;
                                    break;
                                }
                            }
                        }
                    }

                    if (boss_is_attacking)
                    {
                        _attackStickyFrames = ATTACK_STICKY_MIN_FRAMES;
                    }
                    else if (_attackStickyFrames > 0)
                    {
                        _attackStickyFrames--;
                        boss_is_attacking = true;
                    }

                    string data = $"{{\"status\": \"fight\", \"restart_pending\": {(_restartPending ? 1 : 0)}, \"scene\": \"{CurrentSceneName()}\", \"hp\": {hp}, \"max_hp\": {max_hp}, \"mana\": {mana}, \"boss_hp\": {bossHp}, \"boss_dead\": {(_bossDead ? 1 : 0)}, " +
                        $"\"arena_bosses\": {arenaBossCount}, \"arena_alive\": {arenaAliveCount}, \"arena_hp\": {arenaHpSum}, \"arena_detail\": \"{arenaDetail}\", " +
                        $"\"scene_count\": {sceneCount}, \"scene_hp\": {sceneHpNow}, \"scene_damage_total\": {_sceneDamageTotal}, \"scene_detail\": \"{sceneDetail}\", " +
                        $"\"x\": {x.ToString("F2", CultureInfo.InvariantCulture)}, " +
                        $"\"y\": {y.ToString("F2", CultureInfo.InvariantCulture)}, " +
                        $"\"boss_x\": {bossX.ToString("F2", CultureInfo.InvariantCulture)}, " +
                        $"\"boss_y\": {bossY.ToString("F2", CultureInfo.InvariantCulture)}, " +
                        $"\"vel_x\": {vel_x.ToString("F2", CultureInfo.InvariantCulture)}, " +
                        $"\"vel_y\": {vel_y.ToString("F2", CultureInfo.InvariantCulture)}, " +
                        $"\"boss_vel_x\": {boss_vel_x.ToString("F2", CultureInfo.InvariantCulture)}, " +
                        $"\"boss_vel_y\": {boss_vel_y.ToString("F2", CultureInfo.InvariantCulture)}, " +
                        $"\"grounded\": {(grounded ? 1 : 0)}, " +
                        $"\"facing_right\": {(facing_right ? 1 : 0)}, " +
                        $"\"boss_facing_right\": {(boss_facing_right ? 1 : 0)}, " +
                        $"\"is_attacking\": {(is_attacking ? 1 : 0)}, " +
                        $"\"is_dashing\": {(is_dashing ? 1 : 0)}, " +
                        $"\"is_jumping\": {(is_jumping ? 1 : 0)}, " +
                        $"\"is_falling\": {(is_falling ? 1 : 0)}, " +
                        $"\"is_recoiling\": {(is_recoiling ? 1 : 0)}, " +
                        $"\"is_dead\": {(is_dead ? 1 : 0)}, " +
                        $"\"was_hit\": {(was_hit ? 1 : 0)}, " +
                        $"\"hit_counter\": {_hitCounter}, " +
                        $"\"boss_damage_total\": {_bossDamageTotal}, " +
                        $"\"boss_is_attacking\": {(boss_is_attacking ? 1 : 0)}, " +
                        $"\"near_hazard\": {(near_hazard ? 1 : 0)}, " +
                        $"\"boss_state\": \"{boss_state}\", " +
                        $"\"paused\": {(_aiPaused ? 1 : 0)}" +
                        $"}}";

                    Publish(data);
                }
            }
            catch (Exception)
            {
                Publish(StatusJson("waiting_for_hero_body"));
            }
        }

        private void OnGameQuitting()
        {
            _shuttingDown = true;
            try { Publish(StatusJson("quitting")); } catch (Exception) {}
            lock (_sync) { Monitor.PulseAll(_sync); }
        }
    }

    public class AiModTicker : MonoBehaviour
    {
        public event Action<float> OnTick;

        private void Update()
        {
            var handler = OnTick;
            if (handler != null) handler(Time.unscaledDeltaTime);
        }
    }
}
