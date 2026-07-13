import gymnasium as gym
from gymnasium import spaces
import numpy as np
import time
import cv2
from collections import deque 

from ai_environment import HollowKnightEnv
from ai_controller import HollowKnightController

SHOW_WINDOWS = True  

class HollowKnightGym(gym.Env):
    def __init__(self):
        super().__init__()
        
        self.game_env = HollowKnightEnv()
        self.controller = HollowKnightController()
        
        self.action_space = spaces.Discrete(6)
        
        self.observation_space = spaces.Dict({
            "image": spaces.Box(low=0, high=255, shape=(84, 84, 4), dtype=np.uint8),
            "stats": spaces.Box(low=0, high=2000, shape=(3,), dtype=np.float32) 
        })
        
        self.last_hp = 9
        self.last_boss_hp = 0
        self.episode_step = 0
        self.last_time = time.time()
        
        self.frames = deque([np.zeros((84, 84), dtype=np.uint8) for _ in range(4)], maxlen=4)
        
    def _get_obs(self):
        frame, telemetry = self.game_env.get_observation()
        
        frame_84 = cv2.resize(frame, (84, 84), interpolation=cv2.INTER_AREA)
        
        if len(frame_84.shape) == 3:
            frame_84 = cv2.cvtColor(frame_84, cv2.COLOR_BGR2GRAY)
            
        self.frames.append(frame_84)
        
        stacked_image = np.stack(self.frames, axis=-1)
        
        hp = self.last_hp
        boss_hp = self.last_boss_hp
        mana = 0.0
        
        if telemetry is not None and "hp" in telemetry:
            hp = float(telemetry.get("hp", hp))
            mana = float(telemetry.get("mana", mana))
            
            new_boss_hp = float(telemetry.get("boss_hp", boss_hp))
            if new_boss_hp > 0 or (new_boss_hp == 0 and self.last_boss_hp < 50): 
                boss_hp = new_boss_hp

        stats = np.array([hp, mana, boss_hp], dtype=np.float32)
        return {"image": stacked_image, "stats": stats}

    def reset(self, seed=None, options=None):
            super().reset(seed=seed)
            
            self.controller.reset_all()
            
            print("[RESET] Проверка арены...")
            while True:
                _, telemetry = self.game_env.get_observation()
                
                check_boss_hp = 0.0
                if telemetry is not None:
                    check_boss_hp = float(telemetry.get("boss_hp", 0))
                    
                if check_boss_hp <= 0:
                    print("[RESET] Пуста арена. Начинаем рестарт!")
                    break 

                print(f"[RESET] Босс еще жив (HP: {check_boss_hp}). Ждем...")
                time.sleep(1.0) 

            time.sleep(5.0) 
            
            self.controller.restart_boss_fight()
            time.sleep(4.0) 
            
            frame, _ = self.game_env.get_observation()
            frame_84 = cv2.resize(frame, (84, 84), interpolation=cv2.INTER_AREA)
            if len(frame_84.shape) == 3:
                frame_84 = cv2.cvtColor(frame_84, cv2.COLOR_BGR2GRAY)      
            for _ in range(4):
                self.frames.append(frame_84) 
            obs = self._get_obs()
            self.last_hp = obs["stats"][0]
            self.last_boss_hp = obs["stats"][2]
            
            self.episode_step = 0
            self.last_time = time.time()
            
            return obs, {}

    def step(self, action):
        self.controller.set_action(action)
        time.sleep(0.005)
        
        obs = self._get_obs()
        
        current_hp = obs["stats"][0]
        current_boss_hp = obs["stats"][2]
        current_mana = obs["stats"][1]
        
        reward = 0.0
        terminated = False
        truncated = False
        
        self.episode_step += 1
        
        current_time = time.time()
        time_since_last_step = current_time - self.last_time
        fps = 1.0 / (time_since_last_step + 0.0001)
        self.last_time = current_time


        if self.episode_step > 2500:
            truncated = True
            self.controller.reset_all()

        reward -= 0.1 
        
        
        #ИИ ударил босса 
        if current_boss_hp < self.last_boss_hp:
            damage_dealt = self.last_boss_hp - current_boss_hp
            reward += (damage_dealt * 3.0)
            

        if current_hp < self.last_hp:
            reward -= 250.0
            
            
        # Босс убит 
        if current_boss_hp <= 0 and self.last_boss_hp > 0:
            reward += 1000.0
            terminated = True
            self.controller.reset_all()
            
        #ИИ умер
        if current_hp <= 0 and self.last_hp > 0:
            reward -= 700.0
            terminated = True
            self.controller.reset_all()
            
        self.last_hp = current_hp
        self.last_boss_hp = current_boss_hp
        
        if SHOW_WINDOWS:      
            latest_frame = obs["image"][:, :, -1]
            vision_img_display = cv2.resize(latest_frame, (256, 256), interpolation=cv2.INTER_NEAREST)
            cv2.imshow("AI Vision", vision_img_display)  
            stats_img = np.zeros((300, 400, 3), dtype=np.uint8)
            
            cv2.putText(stats_img, f"HP: {int(current_hp)}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.putText(stats_img, f"Boss HP: {int(current_boss_hp)}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            cv2.putText(stats_img, f"Mana: {int(current_mana)}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
            cv2.putText(stats_img, f"Reward: {reward:.1f}", (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(stats_img, f"Step: {self.episode_step} / 2500", (20, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
            cv2.putText(stats_img, f"FPS: {fps:.1f}", (20, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
            
            cv2.imshow("AI Dashboard", stats_img)
            cv2.waitKey(1) 
        
        return obs, reward, terminated, truncated, {}