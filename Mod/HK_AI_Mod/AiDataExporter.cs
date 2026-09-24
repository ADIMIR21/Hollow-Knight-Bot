using System;
using System.IO;
using System.Globalization;
using System.Collections.Generic;
using UnityEngine;
using Modding;

namespace HK_AI_Mod
{
    public class AiDataExporter : Mod
    {
        public override string GetVersion() => "1.2";

        private string _filePath = "";
        private string _cmdPath = "";
        private string _sceneConfigPath = "";
        private string _gateConfigPath = "";

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
            _filePath = Path.Combine(Path.GetTempPath(), "hk_ai_data.json");
            _cmdPath = Path.Combine(Path.GetTempPath(), "hk_ai_cmd.txt");
            _sceneConfigPath = Path.Combine(Path.GetTempPath(), "hk_ai_boss.txt");
            _gateConfigPath = Path.Combine(Path.GetTempPath(), "hk_ai_gate.txt");

            UnityEngine.SceneManagement.SceneManager.activeSceneChanged += (oldScene, newScene) =>
            {
                bool isMenu = newScene.name != null && newScene.name.Contains("Menu");
                _inMenuScene = isMenu;
                if (isMenu)
                    WriteSafe(StatusJson("main_menu"));
                else
                {
                    _currentBoss = null;
                    _restartPending = false;
                    _lastBossHpKnown = 0;
                    _bossDead = false;
                    _watchdogTimer = 2.5f;
                    _forcedEntryAttempts = 0;
                    SubscribeBossDeath();
                    WriteSafe(StatusJson("loading_scene"));
                }
            };

            try { if (File.Exists(_cmdPath)) File.Delete(_cmdPath); } catch (Exception) {}

            var host = new GameObject("HK_AI_Mod_Host");
            UnityEngine.Object.DontDestroyOnLoad(host);
            var ticker = host.AddComponent<AiModTicker>();
            ticker.OnTick += OnTick;

            ModHooks.HeroUpdateHook += OnHeroUpdate;
            Application.quitting += OnGameQuitting;

            WriteSafe(StatusJson("initialized"));
            Log($"ИИ Экспортер {GetVersion()} работает! Файл: {_filePath}");
            Log("[ИИ] Команды: restart | teleport | boss <имя/номер> | bosses | warp");
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

        private string ReadTargetScene()
        {
            try
            {
                if (File.Exists(_sceneConfigPath))
                {
                    string scene = File.ReadAllText(_sceneConfigPath).Trim();
                    if (!string.IsNullOrEmpty(scene))
                        return scene;
                }
            }
            catch (Exception) {}
            return DEFAULT_BOSS_SCENE;
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

        private void WriteBossList()
        {
            try
            {
                var sb = new System.Text.StringBuilder();
                sb.Append("{\"target_scene\": \"").Append(ReadTargetScene()).Append("\", \"count\": ").Append(BossRegistry.Length).Append(", \"bosses\": [");
                for (int i = 0; i < BossRegistry.Length; i++)
                {
                    if (i > 0) sb.Append(", ");
                    sb.Append("{\"index\": ").Append(i + 1)
                      .Append(", \"scene\": \"").Append(BossRegistry[i].Scene)
                      .Append("\", \"label\": \"").Append(BossRegistry[i].Label).Append("\"}");
                }
                sb.Append("]}");
                string listPath = Path.Combine(Path.GetTempPath(), "hk_ai_bosses.json");
                File.WriteAllText(listPath, sb.ToString());
                Log($"[ИИ] Список боссов выгружен: {listPath}");
            }
            catch (Exception e)
            {
                Log($"[ИИ] Не удалось выгрузить список боссов: {e.Message}");
            }
        }

        private string CurrentSceneName()
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

        private void OnTick(float unscaledDelta)
        {
            PollCommand();
            TransitionWatchdogTick(unscaledDelta);
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
                string gateName = ReadTargetGateName();
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

        private string ReadTargetGateName()
        {
            try
            {
                if (File.Exists(_gateConfigPath))
                {
                    string gate = File.ReadAllText(_gateConfigPath).Trim();
                    if (!string.IsNullOrEmpty(gate))
                        return gate;
                }
            }
            catch (Exception) {}
            return DEFAULT_ENTRY_GATE;
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

        private void PollCommand()
        {
            try
            {
                if (!File.Exists(_cmdPath))
                {
                    _restartPending = false;
                    return;
                }

                string raw = File.ReadAllText(_cmdPath).Trim();
                if (raw.Length == 0) return;
                string cmd = raw.ToLowerInvariant();

                // Справка по списку боссов — работает даже в главном меню.
                if (cmd == "bosses")
                {
                    WriteBossList();
                    TryDeleteCmd();
                    return;
                }

                if (_inMenuScene) return;

                if (cmd == "warp")
                {
                    WarpHeroToGate();
                    TryDeleteCmd();
                    return;
                }

                bool isRestart = cmd == "restart";
                bool isTeleport = cmd == "teleport";
                bool isBossSelect = cmd == "boss" || cmd.StartsWith("boss ");

                if (!isRestart && !isTeleport && !isBossSelect)
                {
                    TryDeleteCmd();
                    return;
                }

                if (_restartPending)
                {
                    TryDeleteCmd();
                    return;
                }

                // Целевая сцена: из аргумента "boss <x>" или из конфиг-файла.
                string targetScene = ReadTargetScene();
                if (isBossSelect)
                {
                    string query = cmd.Length > 5 ? cmd.Substring(5).Trim() : "";
                    string resolvedScene, resolvedLabel;
                    if (!TryResolveBoss(query, out resolvedScene, out resolvedLabel))
                    {
                        Log($"[ИИ] Босс не распознан: '{query}'. Отправь команду 'bosses' для списка.");
                        TryDeleteCmd();
                        return;
                    }
                    targetScene = resolvedScene;
                    // Запоминаем выбранного босса, чтобы рестарты и Python
                    // продолжали работать с этой же ареной.
                    WriteBossConfig(targetScene);
                    Log($"[ИИ] Выбран босс: {resolvedLabel} ({resolvedScene})");
                }
                else
                {
                    // restart/teleport тоже понимают алиасы (HK_BOSS_SCENE="hornet")
                    string resolvedScene, resolvedLabel;
                    if (TryResolveBoss(targetScene, out resolvedScene, out resolvedLabel))
                        targetScene = resolvedScene;
                }

                _restartPending = true;
                _watchdogTimer = 0f;

                string gate = ReadTargetGateName();
                Log($"[ИИ] {(isBossSelect ? "Телепорт к боссу" : "Быстрый рестарт")}: переход в сцену '{targetScene}' через гейт '{gate}'");

                GameManager.instance.BeginSceneTransition(new GameManager.SceneLoadInfo
                {
                    SceneName = targetScene,
                    EntryGateName = gate,
                    WaitForSceneTransitionCameraFade = true,
                    Visualization = GameManager.SceneLoadVisualizations.Default,
                    AlwaysUnloadUnusedAssets = false
                });
                TryDeleteCmd();
            }
            catch (Exception e)
            {
                Log($"[ИИ] Ошибка команды: {e}");
            }
        }

        private void WriteBossConfig(string scene)
        {
            try
            {
                File.WriteAllText(_sceneConfigPath, scene);
            }
            catch (Exception) {}
        }

        // Возвращает героя к входу арены текущей сцены без перезагрузки сцены.
        private void WarpHeroToGate()
        {
            try
            {
                HeroController hero = HeroController.instance;
                if (hero == null)
                {
                    Log("[ИИ] Warp: героя нет на сцене");
                    return;
                }

                TransitionPoint gate = FindTransitionGate(hero.transform, ReadTargetGateName());
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

        private void TryDeleteCmd()
        {
            for (int attempt = 0; attempt < 3; attempt++)
            {
                try
                {
                    File.Delete(_cmdPath);
                    return;
                }
                catch (IOException)
                {
                    System.Threading.Thread.Sleep(30);
                }
                catch (Exception)
                {
                    return;
                }
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
            // v1.1: телеметрия каждый HeroUpdate (~60 записей/сек при 60fps).
            // Python-сторона синхронизируется по mtime файла и успевает за игрой,
            // что поднимает потолок скорости обучения с ~20 до ~60 шагов/сек.

            PollCommand();

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
                    
                    WriteSafe(data);
                }
            }
            catch (Exception)
            {
                WriteSafe(StatusJson("waiting_for_hero_body"));
            }
        }

        private void WriteSafe(string json)
        {
            try
            {
                string tmpPath = _filePath + ".tmp";
                using (FileStream fs = new FileStream(tmpPath, FileMode.Create, FileAccess.Write, FileShare.ReadWrite))
                using (StreamWriter sw = new StreamWriter(fs))
                {
                    sw.Write(json);
                }
                if (File.Exists(_filePath))
                    File.Replace(tmpPath, _filePath, null);
                else
                    File.Move(tmpPath, _filePath);
            }
            catch (Exception) {}
        }

        private void OnGameQuitting()
        {
            try
            {
                if (!string.IsNullOrEmpty(_filePath) && File.Exists(_filePath))
                {
                    File.Delete(_filePath);
                }
            }
            catch (Exception){}
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