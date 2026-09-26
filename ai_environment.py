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
            print("[ENV] Захват экрана ВКЛЮЧЕН")
        else:
            self.camera = None
            print("[ENV] Захват экрана ОТКЛЮЧЕН (используется телеметрия)")

        # Обновление 4: телеметрия идёт через именованный пайп \\.\pipe\hk_ai_mod.
        # Фон стримится построчно в отдельном потоке с авто-реконнектом —
        # никаких гонок за файл в %TEMP% и ретраев открытия. Клиент общий на
        # процесс, поэтому команды bosses.py идут по тому же соединению.
        self.pipe = get_shared_client()
        print("[ENV] Жду пайп мода (\\\\.\\pipe\\hk_ai_mod)...")
        if self.pipe.wait_connected(timeout=20.0):
            print("[ENV] Мод на связи!")
        else:
            print("[ENV] ВНИМАНИЕ: мод не ответил за 20с. Игра запущена? Мод HK_AI_Mod.dll установлен?")
            print("[ENV] Продолжаю: клиент продолжит подключаться в фоне.")

        print("[ENV] хк успешно найден!")

    def get_telemetry(self):
        # Последнее сообщение мода (None, если связи ещё нет).
        return self.pipe.get_telemetry()

    def get_telemetry_mtime(self):
        """Штамп последней записи телеметрии (счётчик сообщений пайпа) или None."""
        if not self.pipe.is_connected:
            return None
        return self.pipe.get_seq()

    def wait_for_fresh_telemetry(self, last_seq, timeout=0.15):
        """
        Обновление 4: ждём НОВОЕ сообщение в пайпе (по счётчику seq) вместо
        фиксированных снов. Шаг идёт ровно в темпе игры.

        Возвращает seq новых данных, либо прежний last_seq,
        если за timeout ничего не пришло (меню/пауза — работаем по старым данным).
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
                    print(f"=== МОЗГИ ИИ ===")
                    print(f"ИГРОК:    {hp}/{max_hp} HP | ДУША: {mana}/99 MP")
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
