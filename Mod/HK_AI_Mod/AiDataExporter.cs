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
        public override string GetVersion() => "1.0";

        private string _filePath = "";
        private string _cmdPath = "";
        private string _sceneConfigPath = "";
        private string _gateConfigPath = "";
        private int _frameCounter = 0;

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
            Log($"ИИ Экспортер 1.0 работает! Файл: {_filePath}");
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
                if (_inMenuScene) return;

                string cmd = File.ReadAllText(_cmdPath).Trim().ToLower();
                if (cmd != "restart")
                {
                    TryDeleteCmd();
                    return;
                }
                if (_restartPending)
                {
                    TryDeleteCmd();
                    return;
                }
                _restartPending = true;
                string targetScene = ReadTargetScene();
                Log($"[ИИ] Быстрый рестарт: переход в сцену '{targetScene}'");

                _watchdogTimer = 0f;

                GameManager.instance.BeginSceneTransition(new GameManager.SceneLoadInfo
                {
                    SceneName = targetScene,
                    EntryGateName = "door1",
                    WaitForSceneTransitionCameraFade = true,
                    Visualization = GameManager.SceneLoadVisualizations.Default,
                    AlwaysUnloadUnusedAssets = false
                });
                TryDeleteCmd();
            }
            catch (Exception e)
            {
                Log($"[ИИ] Ошибка команды рестарта: {e}");
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
            return "{\"status\": \"" + status + "\", \"restart_pending\": " + (_restartPending ? 1 : 0) + "}";
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
            _frameCounter++;
            if (_frameCounter < 3) return;
            _frameCounter = 0;

            PollCommand();

            try
            {
                if (HeroController.instance != null && PlayerData.instance != null && !HeroController.instance.cState.transitioning)
                {
                    var hero = HeroController.instance;
                    float x = hero.transform.position.x;
                    float y = hero.transform.position.y;
                    int hp = PlayerData.instance.health;
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
                        if (bossObjName.Contains("false knight") || bossObjName.Contains("false_knight") || bossObjName.Contains("falseknight"))
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

                    string data = $"{{\"status\": \"fight\", \"restart_pending\": {(_restartPending ? 1 : 0)}, \"hp\": {hp}, \"mana\": {mana}, \"boss_hp\": {bossHp}, \"boss_dead\": {(_bossDead ? 1 : 0)}, " +
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