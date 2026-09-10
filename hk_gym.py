import gymnasium as gym
from gymnasium import spaces
import numpy as np
import time
import cv2
import threading
import sys
from collections import deque 
import math
import os

from ai_environment import HollowKnightEnv
from ai_controller import HollowKnightController
from screen_capture import USE_SCREEN_CAPTURE

SHOW_WINDOWS = False

STAT_NAMES = [
    "hp", "mana", "boss_hp", "x", "y", "boss_x", "boss_y",
    "dist_to_boss", "dx_to_boss", "dy_to_boss",
    "vel_x", "vel_y", "boss_vel_x", "boss_vel_y",
    "grounded", "facing_right", "boss_facing_right",
    "is_attacking", "is_dashing", "is_jumping", "is_falling", "is_recoiling",
    "boss_is_attacking", "near_hazard", "was_hit"
]
IDX = {name: i for i, name in enumerate(STAT_NAMES)}
STATS_SIZE = len(STAT_NAMES)

BOSS_SCENE = os.environ.get("HK_BOSS_SCENE", "GG_False_Knight")

class HollowKnightGym(gym.Env):
    def __init__(self):
        super().__init__()
        
        self.game_env = HollowKnightEnv()
        self.controller = HollowKnightController()
        self.controller.set_boss_scene(BOSS_SCENE)
        
        self.action_space = spaces.Discrete(16)
        
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(STATS_SIZE,), dtype=np.float32
        )
        
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
        
        self._hit_counter = None
        self._damage_total = None
        
        self._boss_attack_active = False
        self._dodged_attack_this_phase = False
        self._consecutive_dodges = 0
        self._times_hit = 0
        self._times_dodged = 0
        self._dodge_check_frames = 0
        
        self.current_action = 0
        self.hold_action_counter = 0
        
        self.auto_restart = True
        self._last_episode_was_victory = False
        self._boss_death_frames = 0
        self._running = True
        self._first_reset = True
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
        
        hp = float(self.last_hp)
        boss_hp = float(self.last_boss_hp)
        mana = 0.0
        x = self.last_x
        y = self.last_y
        boss_x = self.last_boss_x
        boss_y = self.last_boss_y
        
        vel_x = 0.0
        vel_y = 0.0
        boss_vel_x = 0.0
        boss_vel_y = 0.0
        grounded = 0.0
        facing_right = 0.0
        boss_facing_right = 0.0
        is_attacking = 0.0
        is_dashing = 0.0
        is_jumping = 0.0
        is_falling = 0.0
        is_recoiling = 0.0
        boss_is_attacking = 0.0
        near_hazard = 0.0
        was_hit = 0.0
        
        if telemetry is not None and "hp" in telemetry:
            hp = float(telemetry.get("hp", hp))
            mana = float(telemetry.get("mana", mana))
            x = float(telemetry.get("x", x))
            y = float(telemetry.get("y", y))
            boss_x = float(telemetry.get("boss_x", boss_x))
            boss_y = float(telemetry.get("boss_y", boss_y))
            boss_hp = float(telemetry.get("boss_hp", boss_hp))
            
            vel_x = float(telemetry.get("vel_x", 0.0))
            vel_y = float(telemetry.get("vel_y", 0.0))
            boss_vel_x = float(telemetry.get("boss_vel_x", 0.0))
            boss_vel_y = float(telemetry.get("boss_vel_y", 0.0))
            grounded = float(telemetry.get("grounded", 0))
            facing_right = float(telemetry.get("facing_right", 0))
            boss_facing_right = float(telemetry.get("boss_facing_right", 0))
            is_attacking = float(telemetry.get("is_attacking", 0))
            is_dashing = float(telemetry.get("is_dashing", 0))
            is_jumping = float(telemetry.get("is_jumping", 0))
            is_falling = float(telemetry.get("is_falling", 0))
            is_recoiling = float(telemetry.get("is_recoiling", 0))
            boss_is_attacking = float(telemetry.get("boss_is_attacking", 0))
            near_hazard = float(telemetry.get("near_hazard", 0))
            was_hit = float(telemetry.get("was_hit", 0))
        
        dist_to_boss = np.sqrt((x - boss_x)**2 + (y - boss_y)**2)
        angle_to_boss = math.atan2(boss_y - y, boss_x - x)
        
        dx_to_boss = (boss_x - x) / (dist_to_boss + 0.001)
        dy_to_boss = (boss_y - y) / (dist_to_boss + 0.001)
        
        stats = np.array([
            hp, mana, boss_hp, x, y, boss_x, boss_y, dist_to_boss, dx_to_boss, dy_to_boss,
            vel_x, vel_y, boss_vel_x, boss_vel_y,
            grounded, facing_right, boss_facing_right,
            is_attacking, is_dashing, is_jumping, is_falling, is_recoiling,
            boss_is_attacking, near_hazard, was_hit
        ], dtype=np.float32)
        
        return stats

    def _read_counters(self, telemetry):
        new_hits = 0
        new_damage = 0.0
        use_fallback = True
        if telemetry is not None and "hit_counter" in telemetry:
            use_fallback = False
            hc = int(telemetry.get("hit_counter", 0))
            dt = float(telemetry.get("boss_damage_total", 0.0))
            if self._hit_counter is not None:
                new_hits = max(0, hc - self._hit_counter)
                new_damage = max(0.0, dt - self._damage_total)
            self._hit_counter = hc
            self._damage_total = dt
        return new_hits, new_damage, use_fallback

    def _try_fast_restart(self):
        telemetry = self.game_env.get_telemetry()
        if telemetry is None or "restart_pending" not in telemetry:
            print("[RESET] Мод без поддержки быстрого рестарта, будет использован макрос.")
            return False
        
        self.controller.request_fast_restart()
        
        deadline = time.time() + 5.0
        accepted = False
        while time.time() < deadline:
            time.sleep(0.2)
            telemetry = self.game_env.get_telemetry()
            if not self.controller.fast_restart_available():
                accepted = True
                break
            if telemetry is not None and telemetry.get("restart_pending", 0) == 1:
                accepted = True
                break
        
        if not accepted:
            print("[RESET] Мод не подтвердил команду рестарта. Фолбэк на макрос.")
            return False
        
        print("[RESET] Быстрый рестарт принят, жду загрузку сцены боя...")
        deadline = time.time() + 25.0
        while time.time() < deadline:
            time.sleep(0.3)
            telemetry = self.game_env.get_telemetry()
            if (telemetry is not None
                    and telemetry.get("status") == "fight"
                    and telemetry.get("restart_pending", 0) == 0
                    and float(telemetry.get("hp", 0)) > 0
                    and float(telemetry.get("boss_hp", 0)) > 0):
                time.sleep(0.5)
                print("[RESET] Бой перезапущен.")
                return True
        
        print("[RESET] Сцена боя не поднялась за отведённое время. Фолбэк на макрос.")
        return False

    def reset(self, seed=None, options=None):
            super().reset(seed=seed)
            
            self.controller.reset_all()
            
            if self._first_reset:
                self._first_reset = False
                print("[RESET] Первый запуск. Ожидаю загрузку игры...")
                time.sleep(5.0)
                print("[RESET] Прыжок поднятия игрока...")
                self.controller.set_action(3)
                time.sleep(3.0)
                self.controller.reset_all()
                print("[RESET] Запускаю макрос рестарта боя...")
                self.controller.restart_boss_fight()
                
                print("[RESET] Ожидание стабилизации игры после макроса...")
                time.sleep(2.0)
            else:
                want_restart = self.auto_restart or self._last_episode_was_victory
                
                if want_restart and self._try_fast_restart():
                    pass
                else:
                    print("[RESET] Ожидание возрождения игрока... (нажми 'r' для авто-рестарта)")
                    waited_for_restart = False
                    if want_restart:
                        waited_for_restart = True
                    else:
                        print("[RESET] Ожидание выхода из паузы и обновления данных...")
                        time.sleep(1.5)
                        
                        for attempt in range(20):
                            _, telemetry = self.game_env.get_observation()
                            if telemetry is not None:
                                hp = float(telemetry.get("hp", 0))
                                print(f"[RESET] Попытка {attempt+1}: telemetry получена, hp={hp}")
                                if hp > 0:
                                    print("[RESET] Персонаж жив. Начинаем эпизод!")
                                    break
                            else:
                                print(f"[RESET] Попытка {attempt+1}: telemetry = None")
                            
                            if self.auto_restart:
                                print("[RESET] Активирован авто-рестарт во время ожидания")
                                waited_for_restart = True
                                break
                                
                            time.sleep(0.5)
                        else:
                            print("[RESET] Персонаж не появился после 10 секунд. Запускаю макрос рестарта...")
                            waited_for_restart = True
                    
                    if waited_for_restart:
                        print("[RESET] Запускаю макрос рестарта боя...")
                        time.sleep(5.0)
                        
                        self.controller.restart_boss_fight()
                        time.sleep(4.0)
                    else:
                        time.sleep(1.0)
            
            time.sleep(1.0)
            obs = self._get_obs()
            self.last_hp = obs[IDX["hp"]]
            self.last_boss_hp = obs[IDX["boss_hp"]]
            self.last_x = obs[IDX["x"]]
            self.last_y = obs[IDX["y"]]
            self.last_boss_x = obs[IDX["boss_x"]]
            self.last_boss_y = obs[IDX["boss_y"]]
            self.last_dist = obs[IDX["dist_to_boss"]]
            
            _, telemetry = self.game_env.get_observation()
            self._read_counters(telemetry)
            
            self.episode_step = 0
            self.last_time = time.time()
            
            self.current_action = 0
            self.hold_action_counter = 0
            self._boss_death_frames = 0
            self._last_episode_was_victory = False
            self._boss_attack_active = False
            self._dodged_attack_this_phase = False
            self._consecutive_dodges = 0
            self._times_hit = 0
            self._times_dodged = 0
            self._dodge_check_frames = 0
            
            return obs, {}

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
        _, telemetry = self.game_env.get_observation()
        new_hits, new_damage, use_fallback = self._read_counters(telemetry)
        
        current_hp = obs[IDX["hp"]]
        current_mana = obs[IDX["mana"]]
        current_boss_hp = obs[IDX["boss_hp"]]
        current_x = obs[IDX["x"]]
        current_y = obs[IDX["y"]]
        current_boss_x = obs[IDX["boss_x"]]
        current_boss_y = obs[IDX["boss_y"]]
        current_dist = obs[IDX["dist_to_boss"]]
        
        vel_x = obs[IDX["vel_x"]]
        vel_y = obs[IDX["vel_y"]]
        grounded = obs[IDX["grounded"]]
        is_dashing = obs[IDX["is_dashing"]]
        is_jumping = obs[IDX["is_jumping"]]
        is_recoiling = obs[IDX["is_recoiling"]]
        boss_is_attacking = obs[IDX["boss_is_attacking"]]
        near_hazard = obs[IDX["near_hazard"]]
        was_hit = obs[IDX["was_hit"]]
        
        reward = 0.0
        reward_parts = {
            "step_penalty": 0.0,
            "boss_damage": 0.0,
            "was_hit": 0.0,
            "movement": 0.0,
            "melee_attack": 0.0,
            "missed_attack": 0.0,
            "victory": 0.0,
            "death": 0.0,
            "dodge": 0.0,
            "survival": 0.0,
        }
        terminated = False
        truncated = False
        
        self.episode_step += 1
        
        current_time = time.time()
        time_since_last_step = current_time - self.last_time
        fps = 1.0 / (time_since_last_step + 0.0001)
        self.last_time = current_time

        if self.episode_step > 3000:
            truncated = True
            self.controller.reset_all()

        reward -= 0.05
        reward_parts["step_penalty"] -= 0.05

        if use_fallback:
            boss_took_damage = current_boss_hp < self.last_boss_hp and self.last_boss_hp > 0
            got_hit = was_hit > 0.5
        else:
            boss_took_damage = new_damage > 0.0
            got_hit = new_hits > 0

        if boss_took_damage:
            damage_dealt = new_damage if not use_fallback else (self.last_boss_hp - current_boss_hp)
            dmg_reward = damage_dealt * 15.0
            reward += dmg_reward
            reward_parts["boss_damage"] += dmg_reward

        if got_hit:
            reward -= 100.0
            reward_parts["was_hit"] -= 100.0
            self._times_hit += 1

        if action in [1, 2, 5, 10, 11, 12, 13] and not got_hit:
            reward += 0.15
            reward_parts["movement"] += 0.15

        is_attack_action = action in [4, 6, 7, 8, 9]
        if is_attack_action:
            if boss_took_damage and current_dist < 200 and boss_is_attacking < 0.5:
                reward += 2.0
                reward_parts["melee_attack"] += 2.0
            elif not boss_took_damage and current_dist < 200 and boss_is_attacking < 0.5:
                reward -= 0.2
                reward_parts["missed_attack"] -= 0.2
            elif current_dist < 200 and action in [4, 8, 9] and boss_is_attacking > 0.5:
                reward -= 4.0
                reward_parts["melee_attack"] -= 4.0

        if current_boss_hp <= 0:
            self._boss_death_frames += 1
        else:
            self._boss_death_frames = 0
            
        if self._boss_death_frames >= 20 and current_boss_hp <= 0:
            reward += 1000.0
            reward_parts["victory"] += 1000.0
            terminated = True
            self.controller.reset_all()
            self._last_episode_was_victory = True

        if current_hp <= 0 and self.last_hp > 0:
            reward -= 200.0
            reward_parts["death"] -= 200.0
            terminated = True
            self.controller.reset_all()

        is_sprinting = abs(vel_x) > 6.0
        is_dodging_action = is_dashing > 0.5 or is_jumping > 0.5 or is_sprinting
        
        if boss_is_attacking > 0.5 and not self._boss_attack_active:
            self._boss_attack_active = True
            self._dodged_attack_this_phase = False
            self._dodge_check_frames = 0
            reward -= 1.0
            reward_parts["dodge"] -= 1.0
        
        if self._boss_attack_active:
            self._dodge_check_frames += 1
            
            if not self._dodged_attack_this_phase and not got_hit and is_dodging_action:
                reward += 20.0
                reward_parts["dodge"] += 20.0
                self._dodged_attack_this_phase = True
                self._consecutive_dodges += 1
                self._times_dodged += 1
                
                if self._consecutive_dodges >= 3:
                    reward += 15.0
                    reward_parts["dodge"] += 15.0
                elif self._consecutive_dodges >= 2:
                    reward += 8.0
                    reward_parts["dodge"] += 8.0
            
            if not is_dodging_action and not got_hit and grounded > 0.5 and self._dodge_check_frames > 2:
                reward -= 8.0
                reward_parts["dodge"] -= 8.0
            
            if got_hit:
                reward -= 60.0
                reward_parts["dodge"] -= 60.0
                self._consecutive_dodges = 0
        
        if boss_is_attacking < 0.5 and self._boss_attack_active:
            self._boss_attack_active = False
            if not self._dodged_attack_this_phase and not got_hit:
                if near_hazard < 0.5:
                    reward += 5.0
                    reward_parts["dodge"] += 5.0
                    self._times_dodged += 1
                else:
                    reward -= 3.0
                    reward_parts["dodge"] -= 3.0
            self._consecutive_dodges = 0

        if not got_hit and self.episode_step % 50 == 0:
            reward += 1.0
            reward_parts["survival"] += 1.0

        self.last_hp = current_hp
        self.last_boss_hp = current_boss_hp
        self.last_x = current_x
        self.last_y = current_y
        self.last_boss_x = current_boss_x
        self.last_boss_y = current_boss_y
        self.last_dist = current_dist
        self.last_dx_to_boss = obs[IDX["dx_to_boss"]]
        self.last_dy_to_boss = obs[IDX["dy_to_boss"]]
        self.last_angle_to_boss = math.atan2(obs[IDX["dy_to_boss"]], obs[IDX["dx_to_boss"]])
        
        if SHOW_WINDOWS:
            stats_img = np.zeros((520, 500, 3), dtype=np.uint8)
            
            attack_color = (0, 255, 255) if boss_is_attacking > 0.5 else (100, 100, 100)
            dodge_color = (0, 255, 0) if self._dodged_attack_this_phase else (100, 100, 100)
            
            cv2.putText(stats_img, f"HP: {int(current_hp)}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.putText(stats_img, f"Boss HP: {int(current_boss_hp)}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            cv2.putText(stats_img, f"Mana: {int(current_mana)}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
            cv2.putText(stats_img, f"Dist: {current_dist:.1f}", (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(stats_img, f"Vel: ({vel_x:.1f}, {vel_y:.1f})", (20, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 255), 2)
            cv2.putText(stats_img, f"Boss Attack: {'YES' if boss_is_attacking > 0.5 else 'no'}", (20, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.7, attack_color, 2)
            cv2.putText(stats_img, f"Dodged: {'YES' if self._dodged_attack_this_phase else 'no'}", (20, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.7, dodge_color, 2)
            cv2.putText(stats_img, f"Reward: {reward:.1f}", (20, 320), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(stats_img, f"Step: {self.episode_step} / 3000", (20, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
            cv2.putText(stats_img, f"FPS: {fps:.1f}", (20, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
            cv2.putText(stats_img, f"AutoReset: {'ON' if self.auto_restart else 'OFF'}", (20, 440), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255) if self.auto_restart else (100, 100, 100), 2)
            
            cv2.imshow("AI Dashboard", stats_img)
            cv2.waitKey(1) 
        
        return obs, reward, terminated, truncated, {"reward_parts": reward_parts}
