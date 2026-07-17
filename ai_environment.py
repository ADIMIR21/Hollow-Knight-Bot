import os
import json
import time
import cv2
import numpy as np
from screen_capture import ScreenCaptureAgent, USE_SCREEN_CAPTURE
import tempfile

PATH_TO_TELEMETRY = os.path.join(tempfile.gettempdir(), "hk_ai_data.json") 
AI_VISION_SIZE = (256, 256)

ENABLE_PREVIEW = True 

class HollowKnightEnv:
    def __init__(self):
        self.use_screen_capture = USE_SCREEN_CAPTURE
        if self.use_screen_capture:
            self.camera = ScreenCaptureAgent()
            print("[ENV] Захват экрана ВКЛЮЧЕН")
        else:
            self.camera = None
            print("[ENV] Захват экрана ОТКЛЮЧЕН (используется телеметрия)")
        print("[ENV] хк успешно найден!")

    def get_telemetry(self):
        if not os.path.exists(PATH_TO_TELEMETRY):
            return None
        try:
            with open(PATH_TO_TELEMETRY, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, PermissionError):
            return None

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
        print("[СИСТЕМА] Предпросмотр ВКЛЮЧЕН. Нажми 'q' в окне трансляции для выхода.")
    else:
        print("[СИСТЕМА] Предпросмотр ВЫКЛЮЧЕН. Нажми Ctrl+C в консоли для выхода.")
    
    last_x, last_y = 0.0, 0.0
    last_hp, last_mana, last_boss_hp = 0, 0, 0

    try:
        while True:
            frame, telemetry = env.get_observation()
            
            if telemetry is not None:
                current_x = telemetry.get("x", 0.0)
                current_y = telemetry.get("y", 0.0)
                hp = telemetry.get("hp", 9)
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
                    print(f"=== МОЗГИ ИИ ===")
                    print(f"ИГРОК:    {hp} HP | ДУША: {mana}/99 MP")
                    print(f"БОСС:     {boss_hp} HP | Состояние: {boss_state}")
                    print(f"ПОЗИЦИЯ:  X: {current_x:.2f} | Y: {current_y:.2f}")
                    print(f"СКОРОСТЬ: VX: {vel_x:.2f} | VY: {vel_y:.2f}")
                    print(f"СТАТУС:   Земля={grounded} | Атака={is_attacking} | Рывок={is_dashing}")
                    print(f"          Прыжок={is_jumping} | Падение={is_falling} | Отдача={is_recoiling}")
                    print(f"БОСС АТАКУЕТ: {boss_is_attacking} | Опасность рядом: {near_hazard}")
                    print(f"ПОЛУЧИЛ УРОН: {was_hit}")
                    print(f"ГЛАЗА:    Кадр {AI_VISION_SIZE[0]}x{AI_VISION_SIZE[1]} в памяти")
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
        print("\n[СИСТЕМА] Остановка...")
        
    if ENABLE_PREVIEW:
        cv2.destroyAllWindows()
    print("[СИСТЕМА] Работа завершена.")

if __name__ == "__main__":
    main()