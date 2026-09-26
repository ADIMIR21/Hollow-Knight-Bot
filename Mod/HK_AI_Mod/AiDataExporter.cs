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

namespace HK_AI_Mod
{
    public class AiDataExporter : Mod
    {
        public override string GetVersion() => "1.3";

        // ---------------- Транспорт: именованный пайп (протокол v3) ----------------
        // Сервер: \\.\pipe\hk_ai_mod (duplex, построчный обмен), поднимается
        // напрямую через kernel32 — см. Win32Pipe.cs и объяснение там же, почему
        // нельзя взять System.IO.Pipes.NamedPipeServerStream (в Mono игры все его
        // конструкторы — заглушки с NotImplementedException).
        //   Мод   -> Python: строки JSON — hello при подключении, затем телеметрия,
        //                     плюс одноразовые события (список боссов, подтверждения).
        //   Python -> Мод: текстовые команды (см. HandleCommand):
        //                     restart [scene] [gate] | teleport | set_boss <scene>
        //                     set_gate <gate> | boss <запрос> | bosses | warp
        // Пайп заменяет прежнюю связку файлов %TEMP%\hk_ai_data.json / hk_ai_cmd.txt
        // / hk_ai_boss.txt / hk_ai_gate.txt / hk_ai_bosses.json: без гонок за файл,
        // без лишних снов на Python-стороне и без ожидания опроса файла модом.
        private const string PIPE_NAME = "hk_ai_mod";
        private const int MAX_PIPE_CLIENTS = 4;
        private const int PIPE_POLL_MS = 25;
        private const int PROTOCOL_VERSION = 3;
        private const string MOD_VERSION = "1.3";
        private const int MAX_OUTBOX = 256;

        // Одноразовые события (список боссов, подтверждение выбора) в отличие от
        // телеметрии не перетираются свежим кадром: их получает каждый подключённый
        // клиент ровно один раз — по монотонному id, который помнит с момента connect.
        private readonly List<KeyValuePair<long, string>> _outbox = new List<KeyValuePair<long, string>>();
        private long _outboxSeq = 0;

        private readonly object _sync = new object();
        private string _latestJson = "{\"status\": \"booting\", \"restart_pending\": 0}";
        private long _seq = 0;
        private volatile bool _shuttingDown = false;
        private readonly ConcurrentQueue<string> _incomingCommands = new ConcurrentQueue<string>();

        private string _targetScene = DEFAULT_BOSS_SCENE;
        private string _targetGate = DEFAULT_ENTRY_GATE;

        private HealthManager? _currentBoss = null;
        private int _lastPlayerHp = 9;
        private long _hitCounter = 0;
        private long _bossDamageTotal = 0;
        private int _lastBossHpKnown = 0;

        private float _lastBossVelX = 0f;
        private float _lastBossVelY = 0f;

        private readonly HashSet<string> _seenFsmStates = new HashSet<string>();

        private int _attackStickyFrames = 0;
        private const int ATTACK_STICKY_MIN_FRAMES = 2;

        private bool _restartPending = false;
        private bool _inMenuScene = true;
        private bool _bossDead = false;
        private BossSceneController _subscribedBsc = null;
        private float _watchdogTimer = 0f;
        private int _forcedEntryAttempts = 0;
        private const string DEFAULT_BOSS_SCENE = "GG_False_Knight";
        private const string DEFAULT_ENTRY_GATE = "door1";

        // ---------------- Реестр боссов Godhome (пантеоны) ----------------
        // Сцены взяты из build settings игры (hollow_knight_Data/globalgamemanagers).
        // Варианты с суффиксом _V — усложнённые версии боёв (Ascended/Radiant),
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
            // --- Пантеон Мастера (ранние боссы) ---
            new BossEntry("GG_Vengefly", "Vengefly King"),
            new BossEntry("GG_Gruz_Mother", "Gruz Mother"),
            new BossEntry("GG_False_Knight", "False Knight"),
            new BossEntry("GG_Mega_Moss_Charger", "Massive Moss Charger"),
            new BossEntry("GG_Hornet_1", "Hornet Protector"),
            new BossEntry("GG_Brooding_Mawlek", "Brooding Mawlek"),
            // --- Пантеон Художника (раньше-середина игры) ---
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
            // --- Пантеон Мудреца (середина-поздняя игра) ---
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
            // --- Пантеон Рыцаря (поздние боссы) ---
            new BossEntry("GG_Hornet_2", "Hornet Sentinel"),
            new BossEntry("GG_Grey_Prince_Zote", "Grey Prince Zote"),
            new BossEntry("GG_White_Defender", "White Defender"),
            new BossEntry("GG_Grimm_Nightmare", "Nightmare King Grimm"),
            new BossEntry("GG_Hollow_Knight", "Pure Vessel"),
            // --- Пантеон Халлоунеста (финал) ---
            new BossEntry("GG_Radiance", "The Radiance"),
            // --- Гвоздемастеры (финалы пантеонов 1-3) ---
            new BossEntry("GG_Nailmasters", "Brothers Oro & Mato"),
            new BossEntry("GG_Painter", "Paintmaster Sheo"),
            new BossEntry("GG_Sly", "Great Nailsage Sly"),
            new BossEntry("GG_Lurker", "Pale Lurker"),
            // --- Усложнённые варианты боёв (Ascended/Radiant) ---
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
            // --- Хаб Godhome (не боссы, но полезно телепортироваться) ---
            new BossEntry("GG_Atrium", "Godhome Atrium (хаб)"),
            new BossEntry("GG_Workshop", "Godhome Workshop (верстак)"),
            new BossEntry("GG_Boss_Door_Entrance", "Двери пантеонов"),
        };

        // Все GG_-сцены из build settings игры — для канонизации имён,
        // введённых пользователем в любом регистре (gg_hornet_1 -> GG_Hornet_1).
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

        // Популярные короткие алиасы, которых нет в самих именах сцен.
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
                    _bossDead = false;
                    _watchdogTimer = 2.5f;
                    _forcedEntryAttempts = 0;
                    SubscribeBossDeath();
                    Publish(StatusJson("loading_scene"));
                }
            };

            var host = new GameObject("HK_AI_Mod_Host");
            UnityEngine.Object.DontDestroyOnLoad(host);
            var ticker = host.AddComponent<AiModTicker>();
            ticker.OnTick += OnTick;

            ModHooks.HeroUpdateHook += OnHeroUpdate;
            Application.quitting += OnGameQuitting;

            var pipeThread = new Thread(PipeListenerLoop) { IsBackground = true, Name = "HK_AI_PipeServer" };
            pipeThread.Start();

            Publish(StatusJson("initialized"));
            Log($"[ИИ] Экспортер {MOD_VERSION} работает! Пайп: \\\\.\\pipe\\{PIPE_NAME}");
            Log("[ИИ] Команды: restart | teleport | set_boss <сцена> | set_gate <гейт> | boss <запрос> | bosses | warp");
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
            Log("[ИИ] Босс мёртв (событие BossSceneController)");
        }

        private void OnTick(float unscaledDelta)
        {
            DrainCommands();
            TransitionWatchdogTick(unscaledDelta);
        }

        private void DrainCommands()
        {
            while (_incomingCommands.TryDequeue(out string line))
            {
                try { HandleCommand(line); }
                catch (Exception e) { Log("[ИИ] Ошибка команды: " + e); }
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
                        Log($"[ИИ] Точка входа: {_targetGate}");
                    }
                    break;

                // "boss <запрос>" — выбрать босса пантеона и телепортироваться к нему.
                case "boss":
                    SelectBoss(rest);
                    break;

                // "bosses" — выгрузить реестр. Работает даже в главном меню.
                case "bosses":
                    PublishEvent(BossListJson());
                    Log($"[ИИ] Список боссов отправлен в пайп ({BossRegistry.Length} записей)");
                    break;

                // "warp" — вернуть героя к гейту арены без перезагрузки сцены.
                case "warp":
                    WarpHeroToGate();
                    break;

                case "teleport":
                case "restart":
                    if (parts.Length >= 2) SetTargetScene(parts[1]);
                    if (parts.Length >= 3) _targetGate = parts[2];
                    TryRestart();
                    break;

                default:
                    Log($"[ИИ] Неизвестная команда: '{line}'");
                    PublishEvent(CommandErrorJson(line, "неизвестная команда"));
                    break;
            }
        }

        // Целевая сцена может прийти алиасом ("hornet", "nkg") или номером
        // реестра — разворачиваем её так же, как команда "boss".
        private void SetTargetScene(string scene)
        {
            string resolved, label;
            if (TryResolveBoss(scene, out resolved, out label))
            {
                _targetScene = resolved;
                Log($"[ИИ] Целевая сцена босса: {_targetScene} ({label})");
            }
            else
            {
                _targetScene = scene;
                Log($"[ИИ] Целевая сцена босса: {_targetScene} (не распознана, передаю как есть)");
            }
        }

        private void SelectBoss(string query)
        {
            string scene, label;
            if (!TryResolveBoss(query, out scene, out label))
            {
                Log($"[ИИ] Босс не распознан: '{query}'. Отправь команду 'bosses' для списка.");
                PublishEvent(CommandErrorJson("boss " + query, "босс не распознан"));
                return;
            }

            _targetScene = scene;
            Log($"[ИИ] Выбран босс: {label} ({scene})");
            PublishEvent("{\"status\": \"boss_selected\", \"scene\": \"" + scene
                + "\", \"label\": \"" + label + "\"}");
            TryRestart();
        }

        private void TryRestart()
        {
            if (_inMenuScene)
            {
                Log("[ИИ] Рестарт проигнорирован: мы в меню");
                return;
            }
            if (_restartPending) return;

            _restartPending = true;
            Log($"[ИИ] Быстрый рестарт: переход в сцену '{_targetScene}' (гейт '{_targetGate}')");

            _watchdogTimer = 0f;

            GameManager.instance.BeginSceneTransition(new GameManager.SceneLoadInfo
            {
                SceneName = _targetScene,
                EntryGateName = _targetGate,
                WaitForSceneTransitionCameraFade = true,
                Visualization = GameManager.SceneLoadVisualizations.Default,
                AlwaysUnloadUnusedAssets = false
            });
        }

        private void TransitionWatchdogTick(float unscaledDelta)
        {
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
                        Log($"[ИИ] Активные гейты сцены: {names}");
                    }
                    catch (Exception) {}
                }
                if (gate == null)
                    gate = FindTransitionGate(hero.transform, gateName);
                Log($"[ИИ] Герой завис в transitioning. Гейт '{gateName}' найден: {gate != null}{(gate != null ? $" ({gate.name})" : "")}, попытка #{_forcedEntryAttempts}");

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
                Log("[ИИ] Герой выставлен у гейта вручную и разблокирован");
            }
            catch (Exception e)
            {
                Log($"[ИИ] Ошибка watchdog перехода: {e}");
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

        // ---------------- Выбор босса Godhome ----------------

        private static string NormalizeQuery(string query)
        {
            if (query == null) return "";
            string norm = query.Trim().ToLowerInvariant();
            norm = norm.Replace(' ', '_').Replace('-', '_');
            while (norm.Contains("__")) norm = norm.Replace("__", "_");
            return norm.Trim('_');
        }

        // Канонизация имени сцены: пользователь может ввести "gg_hornet_1"
        // в любом регистре — подставляем точное имя из build settings.
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

        // Разбирает запрос на босса: номер в списке, имя сцены (любой регистр),
        // короткий алиас, точное или частичное название босса.
        private static bool TryResolveBoss(string query, out string scene, out string label)
        {
            scene = null;
            label = null;
            if (string.IsNullOrWhiteSpace(query)) return false;

            string norm = NormalizeQuery(query);

            // 1. Номер в реестре (1-based)
            int index;
            if (int.TryParse(norm, out index) && index >= 1 && index <= BossRegistry.Length)
            {
                scene = BossRegistry[index - 1].Scene;
                label = BossRegistry[index - 1].Label;
                return true;
            }

            // 2. Точное имя сцены из реестра (без учёта регистра)
            foreach (BossEntry e in BossRegistry)
            {
                if (e.Scene.ToLowerInvariant() == norm)
                {
                    scene = e.Scene;
                    label = e.Label;
                    return true;
                }
            }

            // 3. Короткий алиас (hornet, nkg, sisters, ...)
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

            // 4. Точное название босса
            foreach (BossEntry e in BossRegistry)
            {
                if (NormalizeQuery(e.Label) == norm)
                {
                    scene = e.Scene;
                    label = e.Label;
                    return true;
                }
            }

            // 5. Имя сцены из build settings, не попавшее в реестр
            // (GG_Spa, GG_Wyrm, GG_Engine и т.п.)
            string canonical;
            if (TryCanonicalizeScene(norm, out canonical))
            {
                scene = canonical;
                label = canonical + " (сцена без реестра)";
                return true;
            }

            // 6. Частичное совпадение по сцене/названию; при неоднозначности
            // предпочитаем базовую версию босса (не (Variant) и не *_V)
            string matched = null;
            string matchedLabel = null;
            foreach (BossEntry e in BossRegistry)
            {
                string sceneNorm = NormalizeQuery(e.Scene);
                if (!sceneNorm.Contains(norm) && !NormalizeQuery(e.Label).Contains(norm))
                    continue;
                if (matched != null)
                {
                    // Второй кандидат: сузим до базовой версии, если возможно
                    bool curIsVariant = matched.EndsWith("_V") || matchedLabel.Contains("(Variant)");
                    bool newIsVariant = e.Scene.EndsWith("_V") || e.Label.Contains("(Variant)");
                    if (newIsVariant) continue;          // вариант хуже базовой версии
                    if (curIsVariant) { matched = e.Scene; matchedLabel = e.Label; continue; }
                    return false;                        // две базовые версии — неоднозначно
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

            // 7. Незнакомое имя вида gg_* — передаём как есть, вдруг сцена есть
            if (norm.StartsWith("gg_"))
            {
                scene = query.Trim();
                label = scene + " (неизвестная сцена, попытка загрузки)";
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

        // Реестр уходит в пайп событием, а не файлом %TEMP%/hk_ai_bosses.json.
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

        private static string CommandErrorJson(string command, string reason)
        {
            string safeCommand = (command ?? "").Replace("\"", "'");
            string safeReason = (reason ?? "").Replace("\"", "'");
            return "{\"status\": \"command_error\", \"command\": \"" + safeCommand
                + "\", \"reason\": \"" + safeReason + "\"}";
        }

        // Возвращает героя к входу арены текущей сцены без перезагрузки сцены.
        private void WarpHeroToGate()
        {
            try
            {
                if (_inMenuScene)
                {
                    Log("[ИИ] Warp проигнорирован: мы в меню");
                    return;
                }

                HeroController hero = HeroController.instance;
                if (hero == null)
                {
                    Log("[ИИ] Warp: героя нет на сцене");
                    return;
                }

                TransitionPoint gate = FindTransitionGate(hero.transform, _targetGate);
                if (gate != null)
                    hero.transform.SetPosition2D(gate.transform.position.x, gate.transform.position.y + 1f);
                else
                    Log("[ИИ] Warp: гейт не найден, герой остаётся на месте");

                if (hero.cState != null && hero.cState.transitioning)
                    ReflectionHelper.CallMethod(hero, "FinishedEnteringScene", true, false);

                if (Time.timeScale <= 0f) Time.timeScale = 1f;

                try
                {
                    var heroRenderer = hero.GetComponentInChildren<Renderer>();
                    if (heroRenderer != null) heroRenderer.enabled = true;
                }
                catch (Exception) {}

                Log("[ИИ] Warp: герой возвращён к гейту арены");
            }
            catch (Exception e)
            {
                Log($"[ИИ] Ошибка warp: {e}");
            }
        }

        // ---------------- Пайп-сервер ----------------

        // Слот на клиента. Поток блокируется в ConnectNamedPipe, пока клиент не
        // подключится, а затем сам его обслуживает (PumpClient).
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
                        Log($"[ИИ] Пайп-слот {slot}: CreateNamedPipe не удался (ошибка {Win32Pipe.LastError()})");
                    Thread.Sleep(1000);
                    continue;
                }

                try
                {
                    if (!Win32Pipe.Connect(pipe))
                    {
                        if (!_shuttingDown)
                        {
                            Log($"[ИИ] Пайп-слот {slot}: ConnectNamedPipe не удался (ошибка {Win32Pipe.LastError()})");
                            Thread.Sleep(1000);
                        }
                        continue;
                    }

                    Log($"[ИИ] Пайп-слот {slot}: клиент подключился");
                    byte[] hello = Utf8("{\"status\": \"pipe_hello\", \"protocol\": " + PROTOCOL_VERSION
                        + ", \"mod_version\": \"" + MOD_VERSION + "\"}\n");
                    if (Win32Pipe.Write(pipe, hello, hello.Length))
                        PumpClient(pipe, readBuf, lineBuf, events, payload);
                }
                catch (Exception e)
                {
                    if (!_shuttingDown)
                        Log($"[ИИ] Пайп-слот {slot}: ошибка — {e.Message}");
                }
                finally
                {
                    Win32Pipe.Close(pipe);
                }
            }
        }

        // Обслуживание одного клиента. Всё на одном потоке: сначала пишем
        // накопившееся, затем опрашиваем и читаем команды. На хэндле никогда не
        // висит незавершённая операция, поэтому запись не может повиснуть на
        // незавершённом чтении (именно это убивало прежнюю реализацию).
        private void PumpClient(IntPtr pipe, byte[] readBuf, StringBuilder lineBuf,
            List<string> events, StringBuilder payload)
        {
            long lastSeq = -1;
            long lastOutboxId;
            // События, накопившиеся до подключения, не переигрываем:
            // клиента интересуют только ответы на его собственные команды.
            lock (_sync) { lastOutboxId = _outboxSeq; }

            while (!_shuttingDown)
            {
                events.Clear();
                string toSend = null;

                lock (_sync)
                {
                    // 1) Одноразовые события: каждый клиент получает их ровно раз.
                    if (_outboxSeq != lastOutboxId)
                    {
                        foreach (KeyValuePair<long, string> ev in _outbox)
                            if (ev.Key > lastOutboxId) events.Add(ev.Value);
                        lastOutboxId = _outboxSeq;
                    }

                    // 2) Телеметрия: только самый свежий кадр, старые не копим.
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
                    if (!Win32Pipe.Write(pipe, bytes, bytes.Length))
                        return; // клиент отвалился
                }

                // Неблокирующее вычитывание команд клиента.
                if (!DrainCommands(pipe, readBuf, lineBuf))
                    return;
            }
        }

        // PeekNamedPipe говорит, сколько байт готово, и только после этого читаем —
        // ReadFile не может подвиснуть в ожидании данных.
        private bool DrainCommands(IntPtr pipe, byte[] readBuf, StringBuilder lineBuf)
        {
            while (true)
            {
                uint available;
                if (!Win32Pipe.Peek(pipe, out available))
                    return false; // разрыв: клиент закрылся

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

                if (lineBuf.Length > 65536) // поток мусора без переводов строк — сбрасываем
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

        // Одноразовое событие: в отличие от телеметрии не перетирается свежим
        // кадром, а доставляется каждому подключённому клиенту ровно один раз.
        // Метка "event": 1 говорит клиенту, что это не телеметрия, — иначе
        // событие подменило бы последний кадр наблюдений в Python.
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
            // v1.2: телеметрия каждый HeroUpdate (~60 записей/сек при 60fps) публикуется
            // в пайп \\.\pipe\hk_ai_mod. Python-сторона читает построчно и синхронизирует
            // шаги по факту прихода новой записи — без снов и опроса mtime файла.

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
                            Log($"[ИИ] Босс выбран: {bestCandidate.gameObject.name} (hp={bestHp})");
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
                                foreach (HealthManager hm in bsc.bosses)
                                {
                                    if (hm == null) continue;
                                    if (hm.isDead || hm.hp <= 0)
                                    {
                                        _bossDead = true;
                                        Log("[ИИ] Босс мёртв (HealthManager.isDead)");
                                        break;
                                    }
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
                            Log("[ИИ] Босс мёртв (текущий HealthManager)");
                        }
                    }

                    if (!_bossDead)
                    {
                        try
                        {
                            foreach (HealthManager hm in GameObject.FindObjectsOfType<HealthManager>())
                            {
                                if (hm != null && hm.hp > 20 && (hm.isDead || hm.hp <= 0))
                                {
                                    _bossDead = true;
                                    Log($"[ИИ] Босс мёртв (перебор HM: {hm.gameObject.name})");
                                    break;
                                }
                            }
                        }
                        catch (Exception) {}
                    }

                    if (_bossDead)
                        bossHp = 0;
                    else if (_currentBoss != null)
                        bossHp = _currentBoss.hp;
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
                                    Log($"[FSM] '{fsm.FsmName}' -> состояние '{stateName}' | атака={classifiedAsAttack}");
                                }

                                if (IsAttackFsmState(stateName))
                                {
                                    boss_is_attacking = true;
                                    boss_state = stateName;
                                }

                                if (!_bossDead && stateName == "Death Anim Start")
                                {
                                    _bossDead = true;
                                    Log("[ИИ] Босс мёртв (FSM Death Anim Start)");
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
                        $"\"boss_state\": \"{boss_state}\"" +
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
