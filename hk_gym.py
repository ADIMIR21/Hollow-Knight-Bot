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
from bosses import resolve_query
from screen_capture import USE_SCREEN_CAPTURE
from hk_features import damage_weight, victory_bonus, ACTION_COUNT, BossStateTracker, redirect_action


def _enable_precise_sleep():
    """Update 3 (tail): on Windows time.sleep() is coarse (~15.6 ms system
    timer tick). timeBeginPeriod(1) makes sleeps accurate to ~1 ms."""
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass


_enable_precise_sleep()

SHOW_WINDOWS = False

STAT_NAMES = [
    "hp", "mana", "boss_hp", "scene_hp", "x", "y", "boss_x", "boss_y",
    "dist_to_boss", "dx_to_boss", "dy_to_boss",
    "vel_x", "vel_y", "boss_vel_x", "boss_vel_y",
    "grounded", "facing_right", "boss_facing_right",
    "is_attacking", "is_dashing", "is_jumping", "is_falling", "is_recoiling",
    "boss_is_attacking", "near_hazard", "was_hit",
    # Update 10: what the boss is doing right now. The mod already sends the
    # state name every frame; the tracker turns it into features (hk_features.py).
    "boss_attack_antic", "boss_open", "boss_dead",
    "boss_state_age", "boss_state_changed"
]
IDX = {name: i for i, name in enumerate(STAT_NAMES)}
STATS_SIZE = len(STAT_NAMES)

# HK_BOSS_SCENE accepts a scene name (GG_False_Knight), an alias (hornet, nkg)
# or a number from the boss registry (see bosses.py / python teleport.py --list).
_RAW_BOSS_SCENE = os.environ.get("HK_BOSS_SCENE", "GG_False_Knight")
_RESOLVED_BOSS = resolve_query(_RAW_BOSS_SCENE)
BOSS_SCENE, BOSS_SCENE_LABEL = _RESOLVED_BOSS if _RESOLVED_BOSS else (_RAW_BOSS_SCENE, _RAW_BOSS_SCENE)
ENTRY_GATE = os.environ.get("HK_ENTRY_GATE", "door_dreamEnter")
# FRAME_SKIP is no longer used: the step is synchronized by fresh telemetry
# (see wait_for_fresh_telemetry in ai_environment.py). Kept for compatibility.
FRAME_SKIP = max(1, int(os.environ.get("HK_FRAME_SKIP", "4")))
FRAME_STACK = max(1, int(os.environ.get("HK_FRAME_STACK", "4")))

# Victory is confirmed by boss_dead — that is already a game event (HealthManager.isDead /
# OnBossesDead / FSM "Death Anim Start"), not a guess from hp, so a couple of frames is enough.
# It used to be 20 frames: the restart lagged and landed in the white arena exit, which forced
# the mod to wait for the full dream return to the hall (+6-10 seconds per episode).
VICTORY_CONFIRM_FRAMES = max(1, int(os.environ.get("HK_VICTORY_FRAMES", "3")))

# --------------------------------------------------------------------------- #
# Reward economics
# --------------------------------------------------------------------------- #
# Every number the reward is built from lives here, because they only mean anything relative to
# each other: a fight is 1500-2600 steps long (13-26 s at one step per fresh frame), so what the
# policy learns depends on the balance between them, not on any one value.
#
#   * damage dealt pays DAMAGE_REWARD_PER_HP per hit point, counted from the mod's monotone
#     counter over every HealthManager in the scene rather than from the boss field. That field
#     describes a pool the game repairs during the fight (the armour drains 260 -> 4, the boss
#     falls, the pool is back at 260, three times over), so "started at minus now" collapsed to
#     zero on every repair - one -3840 step at 15 per hit point, seven times the death penalty,
#     charged for the very hit that opens the stunned punish window where the fight is won;
#   * every mask the knight loses costs HEALTH_PENALTY_PER_MASK. This is the number that decides
#     whether the policy dodges or tanks: at 10 the whole health bar was cheaper than 1% of the
#     boss's, so standing inside an attack to land a hit was always the better trade. At 200 a
#     mask still cost less than the nail hit it buys: an uncharged hit takes 32 of the boss's
#     260, so it pays 480, and tanking went on paying. At 1000 a mask outweighs any single hit
#     landed outside the stunned window and stays below what the same hit pays inside it;
#   * the outcomes outweigh the dense part on purpose: the boss dying pays VICTORY_REWARD, the
#     knight dying costs DEATH_PENALTY (and, through the mask term, the whole health bar). The win has to outbid the mask term, or holding the bar beats killing the boss: with a mask at 800 the whole bar is 7200, so the kill is paid 3900 of damage plus 4000 of bonus;
#   * every step costs STEP_PENALTY, so a fight that drags on is never free.
#
# GAMMA in train.py has to reach the end of such a fight (see test_reward_economics.py), and the
# whole set is fingerprinted into the checkpoint folder (hk_run_config.py): changing one of them
# makes an old model and its normalization statistics meaningless rather than resumable.
DAMAGE_REWARD_PER_HP = 15.0
HEALTH_PENALTY_PER_MASK = 800.0
# Damage that lands while the boss is open is worth this much more than the same damage outside
# the window. The stunned window is the only place this fight can be finished, and paying the same
# for a hit anywhere made the policy hover instead of committing (see hk_features.damage_weight).
OPEN_WINDOW_DAMAGE_MULTIPLIER = 3.0
# What a full set of masks is worth: a win is scaled by the health it was carried out with, so the
# base reward is paid at zero masks and twice that at full health (hk_features.victory_bonus). The
# payout itself stays VICTORY_REWARD - this is the hero's capacity, not a reward knob.
HERO_MAX_MASKS = 9.0
VICTORY_REWARD = 4000.0
DEATH_PENALTY = 500.0
STEP_PENALTY = 0.05
EPISODE_STEP_LIMIT = 3000

class HollowKnightGym(gym.Env):
    def __init__(self):
        super().__init__()
        
        self.game_env = HollowKnightEnv()
        self.controller = HollowKnightController(self.game_env.pipe)
        self.controller.set_boss_scene(BOSS_SCENE)
        # The gate is kept in the controller as well: the restart command carries both scene and gate.
        self.controller.set_entry_gate(ENTRY_GATE)
        
        self.action_space = spaces.Discrete(ACTION_COUNT)
        
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
        self.last_boss_open = False
        self.last_dy_to_boss = 0.0
        self.last_angle_to_boss = 0.0
        self.episode_step = 0
        self.last_time = time.time()
        
        self._last_phi = 0.0
        self._scene_damage = 0.0
        self._last_scene_damage = 0.0
        self._weighted_damage = 0.0
        self._weighted_damage_start = 0.0
        self.current_action = 0
        self.hold_action_counter = 0
        self._boss_state = BossStateTracker()
        
        self.auto_restart = True
        self._last_episode_was_victory = False
        self._boss_death_frames = 0
        self._no_boss_frames = 0
        # Reset timing: episode end reason + accumulated reset time statistics
        self._episode_reason = "start"
        self._reset_stats = {}
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
                    print("\n[CONSOLE] AUTO-RESTART ENABLED.")
                elif cmd == 's':
                    self.auto_restart = False
                    print("\n[CONSOLE] AUTO-RESTART DISABLED.")
                elif cmd == 'q':
                    print("\n[CONSOLE] Exiting on request...")
                    self._running = False
                    import os
                    os._exit(0)
            except (EOFError, ValueError):
                time.sleep(0.1)
                
    def close(self):
        self._running = False
        super().close()

    def set_paused(self, paused, timeout=2.0):
        """Freezes or unfreezes the fight through the mod.

        Update 9: the training loop calls this around the PPO update. The game
        runs in real time while the gradients are computed and nothing is
        stepping the environment, so the hero used to stand still for seconds
        while the boss kept hitting it. True - the mod confirmed the new state.
        """
        pipe = getattr(self.game_env, "pipe", None)
        if pipe is None:
            print("[PAUSE] No pipe client, the game keeps running.")
            return False
        # Taken BEFORE the command: the mod can answer within the same millisecond,
        # and a sequence number captured afterwards would hide that answer.
        before = pipe.get_seq()
        status = "paused" if paused else "resumed"
        if not self.controller.set_paused(paused):
            print("[PAUSE] The mod is not connected, the game keeps running.")
            return False
        reply = pipe.wait_for_status(status, timeout=timeout, after_seq=before)
        if reply is None:
            print(f"[PAUSE] No '{status}' confirmation from the mod within {timeout} s.")
            return False
        print(f"[PAUSE] The game is {status} (time_scale={reply.get('time_scale')}).")
        return True

    def pause_game(self, timeout=2.0):
        # Release the buttons as well: a held attack would fire on the frame the
        # game unfreezes, and the hero must not act while the policy is not looking.
        self.controller.reset_all()
        return self.set_paused(True, timeout=timeout)

    def resume_game(self, timeout=2.0):
        return self.set_paused(False, timeout=timeout)
        
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

        # The summed health of every HealthManager in the scene, and the mod's monotone damage
        # counter over them. The boss field alone describes a pool the game repairs during the
        # fight, so the policy cannot see how far the fight actually got from it.
        scene_hp = 0.0
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
            scene_hp = float(telemetry.get("scene_hp", scene_hp))
            self._scene_damage = float(telemetry.get("scene_damage_total", self._scene_damage))
            
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
        
        # The boss state string never reaches the policy as text: the tracker turns it
        # into "an attack is winding up", "he is open for a hit" and the state's age.
        frame_state = telemetry.get("boss_state") if (telemetry is not None and "hp" in telemetry) else None
        boss_state_features = self._boss_state.update(frame_state)

        dist_to_boss = np.sqrt((x - boss_x)**2 + (y - boss_y)**2)
        angle_to_boss = math.atan2(boss_y - y, boss_x - x)
        
        dx_to_boss = (boss_x - x) / (dist_to_boss + 0.001)
        dy_to_boss = (boss_y - y) / (dist_to_boss + 0.001)
        
        stats = np.array([
            hp, mana, boss_hp, scene_hp, x, y, boss_x, boss_y, dist_to_boss, dx_to_boss, dy_to_boss,
            vel_x, vel_y, boss_vel_x, boss_vel_y,
            grounded, facing_right, boss_facing_right,
            is_attacking, is_dashing, is_jumping, is_falling, is_recoiling,
            boss_is_attacking, near_hazard, was_hit,
            # Same order as the tail of STAT_NAMES (see hk_features.py).
            *boss_state_features
        ], dtype=np.float32)
        
        return stats

    def _try_fast_restart(self):
        # The restart command goes into the mod pipe; confirmation is
        # restart_pending=1 in the telemetry (the mod has picked the command up).
        sent = self.controller.request_fast_restart()
        if not sent:
            print("[RESET] Pipe is not connected, restart through the mod is unavailable.")
            return False
        
        deadline = time.time() + 5.0
        accepted = False
        while time.time() < deadline:
            time.sleep(0.2)
            telemetry = self.game_env.get_telemetry()
            if telemetry is not None and telemetry.get("restart_pending", 0) == 1:
                accepted = True
                break
        
        if not accepted:
            print("[RESET] The mod did not confirm the restart command. Falling back to the macro.")
            return False
        
        print("[RESET] Fast restart accepted, waiting for the fight scene to load...")
        # The mod performs the transition not instantly, but when the game becomes free:
        # after a death the arena first returns to the Hall of Gods as usual (white dream
        # return), and only then does the mod move the knight back into the fight. Hence the slack.
        deadline = time.time() + 40.0
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
                print("[RESET] Fight restarted.")
                return True
        
        print("[RESET] The fight scene did not come up within the allotted time. Falling back to the macro.")
        return False

    def _fight_in_progress(self):
        """
        Is a live fight already running right now? Then there is nothing to reload: both the
        boss and the knight are alive and no restart is pending. Used after a step-limit
        truncation, where reloading the arena would only cut the fight in half.
        """
        telemetry = self.game_env.get_telemetry()
        return (telemetry is not None
                and telemetry.get("status") == "fight"
                and int(telemetry.get("restart_pending", 0)) == 0
                and float(telemetry.get("hp", 0)) > 0
                and float(telemetry.get("boss_hp", 0)) > 0)

    def _wait_for_fight_scene(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            telemetry = self.game_env.get_telemetry()
            if (telemetry is not None
                    and telemetry.get("status") == "fight"
                    and float(telemetry.get("hp", 0)) > 0
                    and float(telemetry.get("boss_hp", 0)) > 0
                    and int(telemetry.get("restart_pending", 0)) == 0):
                print("[RESET] Fight scene is ready.")
                return True
            time.sleep(0.3)
        print("[RESET] The fight did not start. Enter the arena manually.")
        return False

    def _log_reset_timing(self, reason, seconds):
        """What a reset actually costs: shows whether the scene transition optimizations pay off."""
        entry = self._reset_stats.setdefault(reason, [0, 0.0, 0.0])
        entry[0] += 1
        entry[1] += seconds
        entry[2] = max(entry[2], seconds)
        print(f"[TIMING] reset ({reason}): {seconds:.2f}s | {reason}: n={entry[0]}, "
              f"mean {entry[1] / entry[0]:.2f}s, max {entry[2]:.2f}s")

        total_n = sum(v[0] for v in self._reset_stats.values())
        if total_n % 10 != 0:
            return
        total_t = sum(v[1] for v in self._reset_stats.values())
        parts = ", ".join(f"{k}: n={v[0]} avg {v[1] / v[0]:.1f}s"
                          for k, v in sorted(self._reset_stats.items(), key=lambda kv: -kv[1][1]))
        print(f"[TIMING] total {total_n} resets = {total_t:.0f}s ({total_t / total_n:.2f}s on average) | {parts}")

    def reset(self, seed=None, options=None):
            super().reset(seed=seed)

            reset_reason = self._episode_reason
            reset_started = time.time()

            self.controller.reset_all()
            # The state of the previous fight says nothing about this one, and the
            # tracker is read by _get_obs() below.
            self._boss_state.reset()

            if reset_reason == "step limit" and self._fight_in_progress():
                # The step cap is only a bookkeeping boundary of the episode, not a fight
                # outcome: nobody won and nobody died. Reloading the arena here used to cut a
                # live fight in half, so a truncated episode simply continues in place - the
                # same boss, the same HP, a fresh episode counter.
                print("[RESET] Step limit reached: continuing the same fight without a restart.")
            elif self._first_reset:
                self._first_reset = False
                if not self._wait_for_fight_scene(10.0):
                    print("[RESET] Auto-teleporting to the arena...")
                    self._try_fast_restart()
                    self._wait_for_fight_scene(60.0)
            else:
                want_restart = self.auto_restart or self._last_episode_was_victory

                if want_restart and self._try_fast_restart():
                    self._wait_for_fight_scene(10.0)
                else:
                    print("[RESET] Fast restart failed, waiting for the fight to appear...")
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
            self._last_scene_damage = self._scene_damage
            self._weighted_damage_start = self._weighted_damage
            self._last_phi = self._potential(float(obs[IDX["hp"]]), self._weighted_damage)
            
            stacked_obs = np.concatenate(list(self._obs_deque)).astype(np.float32)
            self._log_reset_timing(reset_reason, time.time() - reset_started)
            self._episode_reason = "unknown"
            return stacked_obs, {}

    def _potential(self, hp, weighted_damage):
        # Damage comes from the mod's monotone counter over every HealthManager in the scene, not
        # from the boss field. The pool that field describes is repaired by the game on the way to
        # the punished window (260 -> 4 -> 260), so reading damage as "started at, minus now"
        # turned every repair into a large negative step and charged the policy for opening the
        # only route to the kill. A counter that only grows keeps this term monotone whatever the
        # game does to a pool.
        damage_done = max(0.0, weighted_damage - self._weighted_damage_start)
        hp_lost = max(0.0, self.max_hp - hp)
        return DAMAGE_REWARD_PER_HP * damage_done - HEALTH_PENALTY_PER_MASK * hp_lost

    def step(self, action):
        # Update 10: only "attack" is aimed at the boss, see hk_features.redirect_action.
        action = redirect_action(action, self.last_dx_to_boss, self.last_boss_open)
        
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
        
        # Update 2: instead of fixed sleeps (5 ms + 3x16 ms, in practice
        # ~60-90 ms because of Windows timer granularity) we wait for a FRESH
        # telemetry record from the mod. The step runs exactly at the pace of the game.
        # If there is no new data (menu/pause) — a short fallback sleep,
        # so that we do not spin through empty steps.
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
        boss_is_open = obs[IDX["boss_open"]] > 0.5
        
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

        step_limit = False
        if self.episode_step > EPISODE_STEP_LIMIT:
            truncated = True
            step_limit = True
            self.controller.reset_all()
            self._episode_reason = "step limit"

        reward -= STEP_PENALTY
        reward_parts["step_penalty"] -= STEP_PENALTY

        # The counter only ever grows, so the damage of this step is its increment, and what that
        # damage is worth depends on whether the boss was open when it landed.
        damage_now = max(0.0, self._scene_damage - self._last_scene_damage)
        self._last_scene_damage = self._scene_damage
        self._weighted_damage += damage_weight(
            damage_now, boss_is_open, OPEN_WINDOW_DAMAGE_MULTIPLIER)

        phi = self._potential(current_hp, self._weighted_damage)
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
            self._episode_reason = "boss missing"

        if self._boss_death_frames >= VICTORY_CONFIRM_FRAMES and current_boss_hp <= 0 and self._last_boss_dead >= 0.5:
            won = victory_bonus(VICTORY_REWARD, current_hp, HERO_MAX_MASKS)
            reward += won
            reward_parts["victory"] += won
            terminated = True
            self.controller.reset_all()
            self._last_episode_was_victory = True
            self._episode_reason = "victory"

        if current_hp <= 0 and self.last_hp > 0:
            # The mask term has already charged the health that was spent (see the reward block
            # at the top of this file); this is the price of ending the fight unfinished, and it
            # has to stay smaller than a full kill is worth - otherwise the safest way to protect
            # the knight's health is not to engage at all.
            reward -= DEATH_PENALTY
            reward_parts["death"] -= DEATH_PENALTY
            terminated = True
            self.controller.reset_all()
            self._episode_reason = "death"

        self.last_hp = current_hp
        self.last_boss_hp = current_boss_hp
        self.last_x = current_x
        self.last_y = current_y
        self.last_boss_x = current_boss_x
        self.last_boss_y = current_boss_y
        self.last_dist = current_dist
        self.last_dx_to_boss = obs[IDX["dx_to_boss"]]
        self.last_boss_open = obs[IDX["boss_open"]] > 0.5
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
            cv2.putText(stats_img, f"Step: {self.episode_step} / {EPISODE_STEP_LIMIT}", (20, 320), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
            cv2.putText(stats_img, f"FPS: {fps:.1f}", (20, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
            cv2.putText(stats_img, f"AutoReset: {'ON' if self.auto_restart else 'OFF'}", (20, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255) if self.auto_restart else (100, 100, 100), 2)
            
            cv2.imshow("AI Dashboard", stats_img)
            cv2.waitKey(1) 
        
        # "step_limit" tells the training callbacks that this episode boundary is only the
        # bookkeeping cap of a fight that is still going on (nobody won, nobody died): such a
        # window must not be counted as an episode outcome.
        return stacked_obs, reward, terminated, truncated, {
            "reward_parts": reward_parts,
            "step_limit": step_limit,
        }
