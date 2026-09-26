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
        // ВАЖНО: версия намеренно зафиксирована как "v1" — НЕ меняй её при каждом изменении
        // мода. Она нужна только для того, чтобы в ModLog было видно, какая сборка
        // загружена игрой. Историю изменений ведём в README, а не в этой строке.
        public override string GetVersion() => "v1";

        private string _filePath = "";
        private string _cmdPath = "";
        private string _sceneConfigPath = "";
        private string _gateConfigPath = "";
        // scene -> входной гейт, который РЕАЛЬНО существует в сцене (выучивается при загрузке сцены)
        private string _gateMapPath = "";
        private readonly Dictionary<string, string> _sceneGates = new Dictionary<string, string>();

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
        // Отложенный рестарт: команда принята, но переход сцены выполняется не сразу,
        // а когда игра не занята своим сценарием победы/смерти (иначе белый фейд
        // игры остаётся висеть на экране, см. комментарий у TryPerformPendingTransition).
        private bool _restartRequested = false;
        private float _restartRequestedAt = 0f;
        private string _restartTargetScene = "";
        private string _restartGate = "";
        // Если после запроса сцена уже сменилась — собственный сценарий конца боя
        // (смерть/выход арены) доигран, и ждать больше нечего.
        private bool _sceneChangedSinceRequest = false;
        private string _deferReasonLastLogged = "";
        private const float RESTART_FORCE_TIMEOUT = 10f;
        // Сторож фейда: игра умеет сама гасить залипший фейд (CameraController.FadeInFailSafe),
        // но в этой сборке игры корутина нигде не запускается — делаем это сами.
        private string _fadeStateLastLogged = "";
        private float _fadeNotNormalSince = -1f;
        private int _fadeRescueAttempts = 0;
        private const float FADE_STUCK_TIMEOUT = 1.5f;
        // 'FadingOut' — промежуточное состояние: если игра уже спокойна (сцена загружена,
        // герой не в переходе), а фейд всё ещё в нём, ждать долго нечего.
        private const float FADE_STUCK_FADINGOUT_TIMEOUT = 1.0f;
        private bool _inMenuScene = true;
        private bool _bossDead = false;
        private BossSceneController _subscribedBsc = null;
        private float _watchdogTimer = 0f;
        private int _forcedEntryAttempts = 0;
        private const string DEFAULT_BOSS_SCENE = "GG_False_Knight";
        // Арены Godhome входят в сцену через гейт door_dreamEnter (единственный
        // TransitionPoint в GG_* сценах боссов). Конфиг из hk_ai_gate.txt важнее.
        private const string DEFAULT_ENTRY_GATE = "door_dreamEnter";

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
            _gateMapPath = Path.Combine(Path.GetTempPath(), "hk_ai_gates.txt");
            LoadGateMap();

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
                    _fadeNotNormalSince = -1f;
                    _fadeRescueAttempts = 0;
                    _fadeStateLastLogged = "";
                    if (_restartRequested) _sceneChangedSinceRequest = true;
                    SubscribeBossDeath();
                    LearnSceneGate(newScene.name);
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
            TryPerformPendingTransition();
            TransitionWatchdogTick(unscaledDelta);
            FadeWatchdogTick();
        }

        // ---------------- Гейты сцены ----------------

        // Все TransitionPoint, реально лежащие в сцене (с учётом аддитивной загрузки:
        // в реестре бывают гейты и других сцен).
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

        // GameManager.EnterHero(additiveGateSearch: true) ищет EntryGateName именно
        // среди TransitionPoint ЗАГРУЖАЕМОЙ сцены и при промахе делает
        // `Debug.LogError("Searching in next scene for TransitionGate failed."); return;`
        // — то есть ни EnterScene, ни FinishedEnteringScene, ни FadeSceneIn.
        // Герой навсегда остаётся в transitioning, а фейд (белый после смерти/выхода
        // из арены) остаётся висеть на экране. Поэтому гейт обязан существовать в сцене.
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

                string learned = PickEntryGate(gates, ReadTargetGateName());
                if (string.IsNullOrEmpty(learned)) return;

                string previous;
                if (_sceneGates.TryGetValue(sceneName, out previous) && previous == learned) return;

                _sceneGates[sceneName] = learned;
                SaveGateMap();
                Log($"[ИИ] Гейты сцены '{sceneName}': {string.Join(", ", gates.ToArray())} | вход -> '{learned}'");
            }
            catch (Exception) {}
        }

        private string ResolveEntryGate(string targetScene)
        {
            string configured = ReadTargetGateName();
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

        private void LoadGateMap()
        {
            try
            {
                if (!File.Exists(_gateMapPath)) return;
                foreach (string line in File.ReadAllLines(_gateMapPath))
                {
                    int split = line.IndexOf('=');
                    if (split <= 0) continue;
                    string scene = line.Substring(0, split).Trim();
                    string gate = line.Substring(split + 1).Trim();
                    if (scene.Length > 0 && gate.Length > 0)
                        _sceneGates[scene] = gate;
                }
                Log($"[ИИ] Выученные гейты сцен: {_sceneGates.Count} ({_gateMapPath})");
            }
            catch (Exception) {}
        }

        private void SaveGateMap()
        {
            try
            {
                var sb = new System.Text.StringBuilder();
                foreach (KeyValuePair<string, string> pair in _sceneGates)
                    sb.Append(pair.Key).Append('=').Append(pair.Value).Append('\n');
                File.WriteAllText(_gateMapPath, sb.ToString());
            }
            catch (Exception) {}
        }

        // ---------------- Отложенный рестарт ----------------

        private bool IsHeroDying(HeroController hero)
        {
            try
            {
                if (hero == null || hero.cState == null) return true;
                if (hero.cState.dead || hero.cState.hazardDeath) return true;
                // Сцена уже сменилась после запроса — собственный сценарий конца боя доигран.
                if (_sceneChangedSinceRequest) return false;
                // Хп обнуляется при смерти в Godhome (не выставляя cState.dead), но в зале
                // оно может остаться нулевым и после дрим-возврата, поэтому стоп-фактор
                // считаем только пока герой в целевой арене.
                // controlReqlinquished намеренно НЕ используется: в хабе Godhome он висит
                // и у живого героя с полным хп (в логе hp=9, controlReqlinquished=True),
                // из-за чего рестарт упирался в 10-секундный дедлайн.
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
                return $"герой в сценарии смерти/возврата (hp={hp}, dead={hero.cState.dead}, "
                     + $"сцена {UnityEngine.SceneManagement.SceneManager.GetActiveScene().name})";
            }
            catch (Exception)
            {
                return "герой занят сценарием игры";
            }
        }

        // Белый выход арены из боя (победа над боссом): BossSceneController.EndSceneDelayed()
        // сначала проигрывает "GG TRANSITION OUT STATUE", и только потом уходит из сцены.
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
                if (gm.IsInSceneTransition || gm.IsLoadingSceneTransition) return;

                HeroController hero = HeroController.instance;
                if (hero == null || hero.cState == null) return;
                if (hero.cState.transitioning || hero.cState.hazardDeath || hero.cState.hazardRespawning) return;

                float waited = Time.time - _restartRequestedAt;
                bool forced = waited >= RESTART_FORCE_TIMEOUT;

                string deferReason = null;
                if (!forced && IsHeroDying(hero))
                    deferReason = DescribeHeroState(hero);       // сценарий смерти/дрим-возврата
                else if (!forced && IsArenaExitRunning())
                    deferReason = "арена проигрывает свой белый выход из боя";

                if (deferReason != null)
                {
                    if (_deferReasonLastLogged != deferReason)
                    {
                        _deferReasonLastLogged = deferReason;
                        Log($"[ИИ] Рестарт отложен: {deferReason} — жду, пока игра закончит сценарий");
                    }
                    return;
                }

                _deferReasonLastLogged = "";

                if (forced)
                    Log($"[ИИ] Ждал {waited:F1}с — форсирую переход (состояние игры так и не освободилось)");

                string gate = _restartGate;
                if (string.IsNullOrEmpty(gate)) gate = ResolveEntryGate(_restartTargetScene);

                Log($"[ИИ] Быстрый рестарт: переход в сцену '{_restartTargetScene}' через гейт '{gate}' (ожидание {waited:F2}с)");

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
                _restartRequested = false;
                Log($"[ИИ] Ошибка отложенного перехода: {e}");
            }
        }

        // ---------------- Сторож фейда ----------------

        // Фейды игры (в т.ч. белый RESPAWN FADE/Dream Return) живут на DDOL-объекте
        // GameCameras. Если сцену выдернуть из середины такого сценария, фейд остаётся
        // в непрозрачном состоянии, а штатный FadeInFailSafe в этой сборке игры не
        // запускается нигде. Поэтому гасим залипание сами — тем же событием, что и игра.
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
                        Log($"[ИИ] Фейд камеры -> '{state}' (сцена {CurrentSceneName()})");
                    _fadeStateLastLogged = state;
                }

                if (state == "Normal")
                {
                    _fadeNotNormalSince = -1f;
                    _fadeRescueAttempts = 0;
                    return;
                }

                // Ждём только когда игра реально ничем не занята: во время честного
                // перехода фейд тоже не "Normal", и лезть в него нельзя.
                if (!IsGameSettled(GameManager.instance, HeroController.instance))
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
                Log($"[ИИ] Фейд залип в '{state}' на {stuck:F1}с (попытка {_fadeRescueAttempts}) — отправляю FADE SCENE IN");
                gc.cameraFadeFSM.Fsm.Event("FADE SCENE IN");
            }
            catch (Exception) {}
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
                _restartRequested = true;
                _restartRequestedAt = Time.time;
                _restartTargetScene = targetScene;
                _sceneChangedSinceRequest = false;
                _deferReasonLastLogged = "";

                string gate = ResolveEntryGate(targetScene);
                _restartGate = gate;
                Log($"[ИИ] {(isBossSelect ? "Телепорт к боссу" : "Быстрый рестарт")}: цель '{targetScene}', гейт '{gate}' — команда принята, переход выполню, когда игра освободится");
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