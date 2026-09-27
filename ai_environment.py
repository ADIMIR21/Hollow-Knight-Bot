import os
import time
import cv2
import numpy as np
from screen_capture import ScreenCaptureAgent, USE_SCREEN_CAPTURE
from hk_pipe import get_shared_client

AI_VISION_SIZE = (256, 256)

ENABLE_PREVIEW = False

class HollowKnightEnv:
    def __init__(self):
        self.use_screen_capture = USE_SCREEN_CAPTURE
        if self.use_screen_capture:
            self.camera = ScreenCaptureAgent()
            print("[ENV] Screen capture ENABLED")
        else:
            self.camera = None
            print("[ENV] Screen capture DISABLED (telemetry is used)")

        # Update 4: telemetry comes through the named pipe \\.\pipe\hk_ai_mod.
        # The background streams line by line in a separate thread with auto-reconnect —
        # no races over a file in %TEMP% and no open retries. The client is shared per
        # process, so commands from bosses.py go over the same connection.
        self.pipe = get_shared_client()
        print("[ENV] Waiting for the mod pipe (\\\\.\\pipe\\hk_ai_mod)...")
        if self.pipe.wait_connected(timeout=20.0):
            print("[ENV] The mod is connected!")
        else:
            print("[ENV] WARNING: the mod did not respond within 20s. Is the game running? Is the AiTrainHK.dll mod installed?")
            print("[ENV] Note: an older mod build is not enough — the pipe transport needs the current DLL "
                  "(powershell -ExecutionPolicy Bypass -File deploy_mod.ps1 -Build, with the game closed).")
            print("[ENV] Continuing: the client will keep connecting in the background.")

        print("[ENV] Hollow Knight found successfully!")

    def get_telemetry(self):
        # The last message from the mod (None if there is no connection yet).
        return self.pipe.get_telemetry()

    def get_telemetry_mtime(self):
        """Stamp of the last telemetry record (pipe message counter) or None."""
        if not self.pipe.is_connected:
            return None
        return self.pipe.get_seq()

    def wait_for_fresh_telemetry(self, last_seq, timeout=0.15):
        """
        Update 4: we wait for a NEW pipe message (by the seq counter) instead of
        fixed sleeps. The step runs exactly at the pace of the game.

        Returns the seq of the new data, or the previous last_seq
        if nothing arrived within the timeout (menu/pause — we keep working on old data).
        """
        return self.pipe.wait_for_fresh(last_seq, timeout)

    def get_observation(self):
        frame = None
        if self.use_screen_capture and self.camera is not None:
            frame = self.camera.get_state_frame()
        else:
            frame = np.zeros(AI_VISION_SIZE, dtype=np.uint8)
        telemetry = self.get_telemetry()
        return frame, telemetry

def main():
    env = HollowKnightEnv()
    window_name = "AI Observation Center"

    if ENABLE_PREVIEW:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, AI_VISION_SIZE[0], AI_VISION_SIZE[1])
        print("[SYSTEM] Preview ENABLED. Press 'q' in the stream window to exit.")
    else:
        print("[SYSTEM] Preview DISABLED. Press Ctrl+C in the console to exit.")
    
    last_x, last_y = 0.0, 0.0
    last_hp, last_mana, last_boss_hp = 0, 0, 0

    try:
        while True:
            frame, telemetry = env.get_observation()
            
            if telemetry is not None:
                current_x = telemetry.get("x", 0.0)
                current_y = telemetry.get("y", 0.0)
                hp = telemetry.get("hp", 9)
                max_hp = telemetry.get("max_hp", 9)
                mana = telemetry.get("mana", 0)
                boss_hp = telemetry.get("boss_hp", 0)
                
                vel_x = telemetry.get("vel_x", 0.0)
                vel_y = telemetry.get("vel_y", 0.0)
                grounded = telemetry.get("grounded", 0)
                is_attacking = telemetry.get("is_attacking", 0)
                is_dashing = telemetry.get("is_dashing", 0)
                is_jumping = telemetry.get("is_jumping", 0)
                is_falling = telemetry.get("is_falling", 0)
                is_recoiling = telemetry.get("is_recoiling", 0)
                boss_is_attacking = telemetry.get("boss_is_attacking", 0)
                near_hazard = telemetry.get("near_hazard", 0)
                was_hit = telemetry.get("was_hit", 0)
                boss_state = telemetry.get("boss_state", "idle")
                
                if (current_x != last_x or current_y != last_y or 
                    hp != last_hp or mana != last_mana or boss_hp != last_boss_hp):
                    
                    os.system('cls' if os.name == 'nt' else 'clear')
                    print(f"=== AI BRAIN ===")
                    print(f"PLAYER:   {hp}/{max_hp} HP | SOUL: {mana}/99 MP")
                    print(f"BOSS:     {boss_hp} HP | State: {boss_state}")
                    print(f"POSITION: X: {current_x:.2f} | Y: {current_y:.2f}")
                    print(f"VELOCITY: VX: {vel_x:.2f} | VY: {vel_y:.2f}")
                    print(f"STATUS:   Grounded={grounded} | Attack={is_attacking} | Dash={is_dashing}")
                    print(f"          Jump={is_jumping} | Fall={is_falling} | Recoil={is_recoiling}")
                    print(f"BOSS ATTACKING: {boss_is_attacking} | Hazard near: {near_hazard}")
                    print(f"TOOK DAMAGE: {was_hit}")
                    print(f"VISION:   Frame {AI_VISION_SIZE[0]}x{AI_VISION_SIZE[1]} in memory")
                    print(f"=============================")
                    
                    last_x, last_y = current_x, current_y
                    last_hp, last_mana, last_boss_hp = hp, mana, boss_hp
            
            if ENABLE_PREVIEW:
                cv2.imshow(window_name, frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            else:
                time.sleep(0.01)
                
    except KeyboardInterrupt:
        print("\n[SYSTEM] Stopping...")
        
    if ENABLE_PREVIEW:
        cv2.destroyAllWindows()
    print("[SYSTEM] Shutdown complete.")

if __name__ == "__main__":
    main()
