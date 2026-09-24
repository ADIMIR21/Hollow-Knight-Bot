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


def _enable_precise_sleep():
    """Обновление 3 (хвост): на Windows time.sleep() грубый (~15.6мс такт
    системного таймера). timeBeginPeriod(1) делает сны точными до ~1мс."""
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass


_enable_precise_sleep()

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
# FRAME_SKIP больше не используется: шаг синхронизируется по свежей телеметрии
# (см. wait_for_fresh_telemetry в ai_environment.py). Оставлено для совместимости.
FRAME_SKIP = max(1, int(os.environ.get("HK_FRAME_SKIP", "4")))
FRAME_STACK = max(1, int(os.environ.get("HK_FRAME_STACK", "4")))

class HollowKnightGym(gym.Env):
    def __init__(self):
        super().__init__()
        
        self.game_env = HollowKnightEnv()
        self.controller = HollowKnightController()
        self.controller.set_boss_scene(BOSS_SCENE)
        
        self.action_space = spaces.Discrete(16)
        
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(STATS_SIZE * FRAME_STACK,), dtype=np.float32
        )
        
        self._obs_deque = deque(maxlen=FRAME_STACK)
        
        self.last_hp = 9
        self.max_hp = 9.0
        self.last_boss_hp = 0
        self._last_boss_dead = 0.0
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
        
        self._last_phi = 0.0
        self._boss_hp_start = 0.0
        self.current_action = 0
        self.hold_action_counter = 0
        
        self.auto_restart = True
        self._last_episode_was_victory = False
        self._boss_death_frames = 0
        self._no_boss_frames = 0
        self._running = True
        self._first_reset = True
        self._telemetry_mtime = self.game_env.get_telemetry_mtime()
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
            self._last_boss_dead = float(telemetry.get("boss_dead", self._last_boss_dead))
            hp = float(telemetry.get("hp", hp))
            self.max_hp = max(1.0, float(telemetry.get("max_hp", self.max_hp)))
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

    def _try_fast_restart(self):
        telemetry = self.game_env.get_telemetry()
        for _ in range(5):
            if telemetry is not None and "restart_pending" in telemetry:
                break
            time.sleep(0.2)
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
        saw_loading = False
        while time.time() < deadline:
            time.sleep(0.3)
            telemetry = self.game_env.get_telemetry()
            if (telemetry is not None and telemetry.get("status") == "loading_scene"):
                saw_loading = True
                continue
            if (saw_loading
                    and telemetry is not None
                    and telemetry.get("status") == "fight"
                    and telemetry.get("restart_pending", 0) == 0
                    and float(telemetry.get("hp", 0)) > 0
                    and float(telemetry.get("boss_hp", 0)) > 0):
                time.sleep(0.5)
                print("[RESET] Бой перезапущен.")
                return True
        
        print("[RESET] Сцена боя не поднялась за отведённое время. Фолбэк на макрос.")
        return False

    def _wait_for_fight_scene(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            telemetry = self.game_env.get_telemetry()
            if (telemetry is not None
                    and telemetry.get("status") == "fight"
                    and float(telemetry.get("hp", 0)) > 0
                    and float(telemetry.get("boss_hp", 0)) > 0
                    and int(telemetry.get("restart_pending", 0)) == 0):
                print("[RESET] Сцена боя готова.")
                return True
            time.sleep(0.3)
        print("[RESET] Бой не поднялся. Зайди в арену вручную.")
        return False

    def reset(self, seed=None, options=None):
            super().reset(seed=seed)

            self.controller.reset_all()

            if self._first_reset:
                self._first_reset = False
                if not self._wait_for_fight_scene(10.0):
                    print("[RESET] Автотелепорт на арену...")
                    self._try_fast_restart()
                    self._wait_for_fight_scene(60.0)
            else:
                want_restart = self.auto_restart or self._last_episode_was_victory

                if want_restart and self._try_fast_restart():
                    self._wait_for_fight_scene(10.0)
                else:
                    print("[RESET] Быстрый рестарт не удался, жду появления боя...")
                    self._wait_for_fight_scene(20.0)

            time.sleep(0.5)
            self._telemetry_mtime = self.game_env.get_telemetry_mtime()
            obs = self._get_obs()
            self._obs_deque.clear()
            for _ in range(FRAME_STACK):
                self._obs_deque.append(obs.copy())
            self.last_hp = obs[IDX["hp"]]
            self.last_boss_hp = obs[IDX["boss_hp"]]
            self.last_x = obs[IDX["x"]]
            self.last_y = obs[IDX["y"]]
            self.last_boss_x = obs[IDX["boss_x"]]
            self.last_boss_y = obs[IDX["boss_y"]]
            self.last_dist = obs[IDX["dist_to_boss"]]
            
            self.episode_step = 0
            self.last_time = time.time()
            
            self.current_action = 0
            self.hold_action_counter = 0
            self._boss_death_frames = 0
            self._last_episode_was_victory = False
            self._boss_hp_start = float(obs[IDX["boss_hp"]])
            self._last_phi = self._potential(float(obs[IDX["hp"]]), float(obs[IDX["boss_hp"]]))
            
            stacked_obs = np.concatenate(list(self._obs_deque)).astype(np.float32)
            return stacked_obs, {}

    def _potential(self, hp, boss_hp):
        damage_done = max(0.0, self._boss_hp_start - boss_hp)
        hp_lost = max(0.0, self.max_hp - hp)
        return 15.0 * damage_done - 10.0 * hp_lost

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
        
        # Обновление 2: вместо фиксированных снов (5мс + 3x16мс, а на деле
        # из-за гранулярности таймера Windows ~60-90мс) ждём СВЕЖУЮ запись
        # телеметрии от мода. Шаг идёт ровно в темпе игры.
        # Если новых данных нет (меню/пауза) — короткий фолбэк-сон,
        # чтобы не гонять пустые шаги.
        new_mtime = self.game_env.wait_for_fresh_telemetry(self._telemetry_mtime, timeout=0.15)
        if new_mtime != self._telemetry_mtime:
            self._telemetry_mtime = new_mtime
        else:
            time.sleep(0.016)

        obs = self._get_obs()
        self._obs_deque.append(obs.copy())
        stacked_obs = np.concatenate(list(self._obs_deque)).astype(np.float32)
        
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
        boss_is_attacking = obs[IDX["boss_is_attacking"]]
        
        reward = 0.0
        reward_parts = {
            "step_penalty": 0.0,
            "shaping": 0.0,
            "victory": 0.0,
            "death": 0.0,
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

        phi = self._potential(current_hp, current_boss_hp)
        shaping = phi - self._last_phi
        self._last_phi = phi
        reward += shaping
        reward_parts["shaping"] += shaping

        if current_boss_hp <= 0:
            self._boss_death_frames += 1
        else:
            self._boss_death_frames = 0

        if current_boss_hp <= 0 and self._last_boss_dead < 0.5:
            self._no_boss_frames += 1
        else:
            self._no_boss_frames = 0

        if self._no_boss_frames >= 150:
            truncated = True
            self.controller.reset_all()

        if self._boss_death_frames >= 20 and current_boss_hp <= 0 and self._last_boss_dead >= 0.5:
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
            stats_img = np.zeros((480, 500, 3), dtype=np.uint8)
            
            attack_color = (0, 255, 255) if boss_is_attacking > 0.5 else (100, 100, 100)
            
            cv2.putText(stats_img, f"HP: {int(current_hp)}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.putText(stats_img, f"Boss HP: {int(current_boss_hp)}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            cv2.putText(stats_img, f"Mana: {int(current_mana)}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
            cv2.putText(stats_img, f"Dist: {current_dist:.1f}", (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(stats_img, f"Vel: ({vel_x:.1f}, {vel_y:.1f})", (20, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 255), 2)
            cv2.putText(stats_img, f"Boss Attack: {'YES' if boss_is_attacking > 0.5 else 'no'}", (20, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.7, attack_color, 2)
            cv2.putText(stats_img, f"Reward: {reward:.1f}", (20, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(stats_img, f"Step: {self.episode_step} / 3000", (20, 320), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
            cv2.putText(stats_img, f"FPS: {fps:.1f}", (20, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
            cv2.putText(stats_img, f"AutoReset: {'ON' if self.auto_restart else 'OFF'}", (20, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255) if self.auto_restart else (100, 100, 100), 2)
            
            cv2.imshow("AI Dashboard", stats_img)
            cv2.waitKey(1) 
        
        return stacked_obs, reward, terminated, truncated, {"reward_parts": reward_parts}
