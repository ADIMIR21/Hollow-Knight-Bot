using System;
using System.IO;
using System.Globalization;
using UnityEngine;
using Modding;

namespace HK_AI_Mod
{
    public class AiDataExporter : Mod
    {
        public override string GetVersion() => "1.1"; 

        private string _filePath = "";
        private int _frameCounter = 0;
        
        private HealthManager _currentBoss = null;

        public override void Initialize()
        {
            _filePath = Path.Combine(Path.GetTempPath(), "hk_ai_data.json");

            UnityEngine.SceneManagement.SceneManager.activeSceneChanged += (oldScene, newScene) => 
            {
                if (newScene.name != null && newScene.name.Contains("Menu"))
                    WriteSafe("{\"status\": \"main_menu\"}");
                else
                    WriteSafe("{\"status\": \"loading_scene\"}");
            };

            ModHooks.HeroUpdateHook += OnHeroUpdate;
            Application.quitting += OnGameQuitting;

            WriteSafe("{\"status\": \"initialized\"}");
            Log($"ИИ Экспортер 1.1 работает! Файл: {_filePath}");
        }

        private void OnHeroUpdate()
        {
            _frameCounter++;
            if (_frameCounter < 5) return;
            _frameCounter = 0;

            try
            {
                if (HeroController.instance != null && PlayerData.instance != null && !HeroController.instance.cState.transitioning)
                {
                    float x = HeroController.instance.transform.position.x;
                    float y = HeroController.instance.transform.position.y;
                    int hp = PlayerData.instance.health;
                    
                    int mana = PlayerData.instance.MPCharge;

                    if (_currentBoss == null || _currentBoss.hp <= 0)
                    {
                        HealthManager bestCandidate = null;
                        int bestHp = 0;
                        
                        foreach (HealthManager hm in GameObject.FindObjectsOfType<HealthManager>())
                        {
                            int bossCandidateHp = hm.hp;
                            if (bossCandidateHp > 20 && bossCandidateHp > bestHp)
                            {
                                bestCandidate = hm;
                                bestHp = bossCandidateHp;
                            }
                        }
                        
                        _currentBoss = bestCandidate;
                    }

                    int bossHp = _currentBoss != null ? _currentBoss.hp : 0;
                    float bossX = _currentBoss != null ? _currentBoss.transform.position.x : 0f;
                    float bossY = _currentBoss != null ? _currentBoss.transform.position.y : 0f;

                    string data = $"{{\"hp\": {hp}, \"mana\": {mana}, \"boss_hp\": {bossHp}, \"x\": {x.ToString("F2", CultureInfo.InvariantCulture)}, \"y\": {y.ToString("F2", CultureInfo.InvariantCulture)}, \"boss_x\": {bossX.ToString("F2", CultureInfo.InvariantCulture)}, \"boss_y\": {bossY.ToString("F2", CultureInfo.InvariantCulture)}}}";
                    
                    WriteSafe(data);
                }
            }
            catch (Exception) 
            { 
                WriteSafe("{\"status\": \"waiting_for_hero_body\"}");
            }
        }

        private void WriteSafe(string json)
        {
            try
            {
                using (FileStream fs = new FileStream(_filePath, FileMode.Create, FileAccess.Write, FileShare.ReadWrite))
                using (StreamWriter sw = new StreamWriter(fs))
                {
                    sw.Write(json);
                }
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
}