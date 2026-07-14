import gymnasium as gym
from gymnasium import spaces
import numpy as np
import time
import cv2
import threading
import sys
from collections import deque 
import math

from ai_environment import HollowKnightEnv
from ai_controller import HollowKnightController

SHOW_WINDOWS = True  

IDEAL_DIST_MIN = 150
IDEAL_DIST_MAX = 350

class HollowKnightGym(gym.Env):
    def __init__(self):
        super().__init__()
        
        self.game_env = HollowKnightEnv()
        self.controller = HollowKnightController()
        
        self.action_space = spaces.Discrete(16)
        
        self.observation_space = spaces.Dict({
            "image": spaces.Box(low=0, high=255, shape=(84, 84, 4), dtype=np.uint8),
            "stats": spaces.Box(low=-5000, high=5000, shape=(10,), dtype=np.float32)
        })
        
        self.last_hp = 9
        self.last_boss_hp = 0
        self.last_x = 0.0
        self.last_y = 0.0
        self.last_boss_x = 0.0
        self.last_boss_y = 0.0
        self.last_dist = 0.0
        self.last_dx_to_boss = 0.0
        self.last_dy_to_boss = 0.0
        self.last_angle_to_boss = 0.0
        self.episode_step = 0
        self.last_time = time.time()
        
        self.frames = deque([np.zeros((84, 84), dtype=np.uint8) for _ in range(4)], maxlen=4)
        
        self.current_action = 0
        self.hold_action_counter = 0
        
        self.auto_restart = False
        self._last_episode_was_victory = False
        self._boss_death_frames = 0
        self._running = True
        self._console_thread = threading.Thread(target=self._console_listener, daemon=True)
        self._console_thread.start()
        
    def _console_listener(self):
        while self._running:
            try:
                cmd = sys.stdin.readline().strip().lower()
                if cmd == 'r':
                    self.auto_restart = True
                    print("\n[КОНСОЛЬ] АВТО-РЕСТАРТ ВКЛЮЧЁН.")
                elif cmd == 's':
                    self.auto_restart = False
                    print("\n[КОНСОЛЬ] АВТО-РЕСТАРТ ВЫКЛЮЧЕН.")
                elif cmd == 'q':
                    print("\n[КОНСОЛЬ] Выход по запросу...")
                    self._running = False
                    import os
                    os._exit(0)
            except (EOFError, ValueError):
                time.sleep(0.1)
                
    def close(self):
        self._running = False
        super().close()
        
    def _get_obs(self):
        frame, telemetry = self.game_env.get_observation()
        
        frame_84 = cv2.resize(frame, (84, 84), interpolation=cv2.INTER_AREA)
        
        if len(frame_84.shape) == 3:
            frame_84 = cv2.cvtColor(frame_84, cv2.COLOR_BGR2GRAY)
            
        self.frames.append(frame_84)
        
        stacked_image = np.stack(self.frames, axis=-1)
        
        hp = float(self.last_hp)
        boss_hp = float(self.last_boss_hp)
        mana = 0.0
        x = self.last_x
        y = self.last_y
        boss_x = self.last_boss_x
        boss_y = self.last_boss_y
        
        if telemetry is not None and "hp" in telemetry:
            hp = float(telemetry.get("hp", hp))
            mana = float(telemetry.get("mana", mana))
            x = float(telemetry.get("x", x))
            y = float(telemetry.get("y", y))
            boss_x = float(telemetry.get("boss_x", boss_x))
            boss_y = float(telemetry.get("boss_y", boss_y))
            
            new_boss_hp = float(telemetry.get("boss_hp", boss_hp))
            if new_boss_hp > 0:
                boss_hp = new_boss_hp
        
        dist_to_boss = np.sqrt((x - boss_x)**2 + (y - boss_y)**2)
        angle_to_boss = math.atan2(boss_y - y, boss_x - x)
        
        dx_to_boss = (boss_x - x) / (dist_to_boss + 0.001)
        dy_to_boss = (boss_y - y) / (dist_to_boss + 0.001)
        
        stats = np.array([hp, mana, boss_hp, x, y, boss_x, boss_y, dist_to_boss, dx_to_boss, dy_to_boss], dtype=np.float32)
        return {"image": stacked_image, "stats": stats}

    def reset(self, seed=None, options=None):
            super().reset(seed=seed)
            
            self.controller.reset_all()
            
            want_restart = self.auto_restart or self._last_episode_was_victory
            
            print("[RESET] Ожидание возрождения игрока... (нажми 'r' для авто-рестарта)")
            waited_for_restart = False
            if want_restart:
                waited_for_restart = True
            else:
                while True:
                    _, telemetry = self.game_env.get_observation()
                    if telemetry is not None:
                        hp = float(telemetry.get("hp", 0))
                        if hp > 0:
                            print("[RESET] Персонаж жив. Начинаем эпизод!")
                            break
                    
                    if self.auto_restart:
                        waited_for_restart = True
                        break
                        
                    time.sleep(0.5)
            
            if waited_for_restart:
                print("[RESET] Запускаю макрос рестарта боя...")
                time.sleep(2.0)
                
                self.controller.restart_boss_fight()
                time.sleep(4.0)
            else:
                time.sleep(1.0)
            
            self._init_frames()
            time.sleep(1.0)
            obs = self._get_obs()
            self.last_hp = obs["stats"][0]
            self.last_boss_hp = max(obs["stats"][2], 99999)
            self.last_x = obs["stats"][3]
            self.last_y = obs["stats"][4]
            self.last_boss_x = obs["stats"][5]
            self.last_boss_y = obs["stats"][6]
            self.last_dist = obs["stats"][7]
            
            frame, telemetry = self.game_env.get_observation()
            if telemetry is not None:
                boss_hp_val = float(telemetry.get("boss_hp", 0))
                if boss_hp_val > 0:
                    self.last_boss_hp = boss_hp_val
            
            self.episode_step = 0
            self.last_time = time.time()
            
            self.current_action = 0
            self.hold_action_counter = 0
            self._boss_death_frames = 0
            self._last_episode_was_victory = False
            
            return obs, {}
            
    def _init_frames(self):
        frame, _ = self.game_env.get_observation()
        frame_84 = cv2.resize(frame, (84, 84), interpolation=cv2.INTER_AREA)
        if len(frame_84.shape) == 3:
            frame_84 = cv2.cvtColor(frame_84, cv2.COLOR_BGR2GRAY)
        for _ in range(4):
            self.frames.append(frame_84)

    def _redirect_attack_to_boss(self, action):
        attack_actions = {4, 6, 7, 8, 9}
        if action not in attack_actions:
            return action
        
        if self.last_dx_to_boss > 0.3:
            return 9
        elif self.last_dx_to_boss < -0.3:
            return 8
        else:
            return action

    def step(self, action):
        action = self._redirect_attack_to_boss(action)
        
        if action == self.current_action:
            self.hold_action_counter += 1
        else:
            self.hold_action_counter = 0
            self.current_action = action
        
        if self.hold_action_counter < 3:
            self.controller.set_action(action)
        elif self.hold_action_counter % 4 == 0:
            self.controller.reset_all()
            time.sleep(0.003)
            self.controller.set_action(action)
        
        time.sleep(0.005)
        
        if action in [1, 2, 8, 9, 10, 11, 12, 13] and self.hold_action_counter < 5:
            time.sleep(0.01)
        
        obs = self._get_obs()
        
        current_hp = obs["stats"][0]
        current_mana = obs["stats"][1]
        current_boss_hp = obs["stats"][2]
        current_x = obs["stats"][3]
        current_y = obs["stats"][4]
        current_boss_x = obs["stats"][5]
        current_boss_y = obs["stats"][6]
        current_dist = obs["stats"][7]
        
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

        reward -= 0.05
        
        if current_boss_hp < self.last_boss_hp:
            damage_dealt = self.last_boss_hp - current_boss_hp
            reward += (damage_dealt * 15.0)
            
            if action in [4, 6, 7, 8, 9]:
                reward += 5.0
                
        if current_hp < self.last_hp:
            reward -= 50.0
            
        if action in [1, 2, 5, 10, 11, 12, 13] and current_hp >= self.last_hp:
            reward += 0.5
            
        if current_dist < 200 and action in [4, 6, 7, 8, 9]:
            reward += 2.0
            
        if current_boss_hp <= 0:
            self._boss_death_frames += 1
        else:
            self._boss_death_frames = 0
            
        if self._boss_death_frames >= 20 and current_boss_hp <= 0:
            reward += 1000.0
            terminated = True
            self.controller.reset_all()
            self._last_episode_was_victory = True
            
        if current_hp <= 0 and self.last_hp > 0:
            reward -= 200.0
            terminated = True
            self.controller.reset_all()
        
        dist_delta = self.last_dist - current_dist
        
        if IDEAL_DIST_MIN <= current_dist <= IDEAL_DIST_MAX:
            reward += 1.5
            if action in [4, 6, 7, 8, 9]:
                reward += 3.0
        elif current_dist < IDEAL_DIST_MIN:
            too_close = IDEAL_DIST_MIN - current_dist
            reward -= 0.02 * too_close
            if dist_delta > 0:
                reward += 1.0
            elif dist_delta < -0.001:
                reward -= 1.0
        elif current_dist > IDEAL_DIST_MAX:
            too_far = current_dist - IDEAL_DIST_MAX
            reward -= 0.005 * too_far
            if dist_delta < -0.001:
                reward += 0.5
            elif dist_delta > 0:
                reward -= 0.2
        
        if current_dist < self.last_dist and current_dist > 100:
            reward += 0.2
            
        self.last_hp = current_hp
        self.last_boss_hp = current_boss_hp
        self.last_x = current_x
        self.last_y = current_y
        self.last_boss_x = current_boss_x
        self.last_boss_y = current_boss_y
        self.last_dist = current_dist
        self.last_dx_to_boss = obs["stats"][8]
        self.last_dy_to_boss = obs["stats"][9]
        self.last_angle_to_boss = math.atan2(obs["stats"][9], obs["stats"][8])
        
        if SHOW_WINDOWS:      
            latest_frame = obs["image"][:, :, -1]
            vision_img_display = cv2.resize(latest_frame, (256, 256), interpolation=cv2.INTER_NEAREST)
            cv2.imshow("AI Vision", vision_img_display)  
            stats_img = np.zeros((440, 450, 3), dtype=np.uint8)
            
            dx_to_boss = obs["stats"][8]
            dy_to_boss = obs["stats"][9]
            
            dist_color = (0, 255, 0) if IDEAL_DIST_MIN <= current_dist <= IDEAL_DIST_MAX else (0, 0, 255)
            
            cv2.putText(stats_img, f"HP: {int(current_hp)}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.putText(stats_img, f"Boss HP: {int(current_boss_hp)}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            cv2.putText(stats_img, f"Mana: {int(current_mana)}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
            cv2.putText(stats_img, f"Dist: {current_dist:.1f}", (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.8, dist_color, 2)
            cv2.putText(stats_img, f"Zone: {IDEAL_DIST_MIN}-{IDEAL_DIST_MAX}", (20, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(stats_img, f"Dir: ({dx_to_boss:.2f}, {dy_to_boss:.2f})", (20, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 255), 2)
            cv2.putText(stats_img, f"Reward: {reward:.1f}", (20, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(stats_img, f"Step: {self.episode_step} / 2500", (20, 320), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
            cv2.putText(stats_img, f"FPS: {fps:.1f}", (20, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
            cv2.putText(stats_img, f"AutoReset: {'ON' if self.auto_restart else 'OFF'}", (20, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255) if self.auto_restart else (100, 100, 100), 2)
            
            cv2.imshow("AI Dashboard", stats_img)
            cv2.waitKey(1) 
        
        return obs, reward, terminated, truncated, {}