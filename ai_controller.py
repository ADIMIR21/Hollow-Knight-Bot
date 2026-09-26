import vgamepad as vg
import time

DEFAULT_BOSS_SCENE = "GG_False_Knight"
# В аренах Godhome входной TransitionPoint называется door_dreamEnter — см.
# DEFAULT_ENTRY_GATE в моде (Mod/HK_AI_Mod/AiDataExporter.cs).
DEFAULT_ENTRY_GATE = "door_dreamEnter"

class HollowKnightController:
    def __init__(self, pipe=None):
        print("[CONTROLLER] Подключаем геймпад...")
        self.gamepad = vg.VX360Gamepad()
        
        time.sleep(2.0)
        print("[CONTROLLER] Геймпад Xbox 360 подключился)!")
        
        # Обновление 4: команды (рестарт/сцена/гейт) уходят в пайп мода,
        # а не в файлы %TEMP%. Клиент пайпа общий с ai_environment.
        self.pipe = pipe
        self.boss_scene = DEFAULT_BOSS_SCENE
        self.entry_gate = DEFAULT_ENTRY_GATE

        self.buttons = {
            "jump": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
            "attack": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
            "focus": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
            "dash": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "pause": vg.XUSB_BUTTON.XUSB_GAMEPAD_START
        }

    def set_boss_scene(self, scene_name):
        self.boss_scene = (scene_name or DEFAULT_BOSS_SCENE).strip()
        if self.pipe is not None:
            self.pipe.send_command("set_boss " + self.boss_scene)
        print(f"[CONTROLLER] Целевая сцена босса: {self.boss_scene}")

    def set_entry_gate(self, gate_name):
        """Задаёт гейт арены. Важно держать его в синхроне с модом: команда
        рестарта всегда несёт и сцену, и гейт, поэтому расхождение здесь
        перебило бы гейт, заданный через bosses.set_gate()."""
        self.entry_gate = (gate_name or DEFAULT_ENTRY_GATE).strip()
        if self.pipe is not None:
            self.pipe.send_command("set_gate " + self.entry_gate)
        print(f"[CONTROLLER] Точка входа: {self.entry_gate}")

    def request_fast_restart(self, scene=None, gate=None):
        """Отправляет команду рестарта в пайп мода. True — команда ушла."""
        scene = (scene or self.boss_scene).strip()
        gate = (gate or self.entry_gate).strip()
        if self.pipe is None or not self.pipe.is_connected:
            return False
        sent = self.pipe.send_command(f"restart {scene} {gate}")
        if sent:
            print(f"[CONTROLLER] Команда рестарта отправлена: {scene} ({gate})")
        return sent

    def set_action(self, action_id):
            self.gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
            for btn in self.buttons.values():
                self.gamepad.release_button(button=btn)

            if action_id == 1:   self.gamepad.left_joystick_float(x_value_float=-1.0, y_value_float=0.0)
            elif action_id == 2: self.gamepad.left_joystick_float(x_value_float=1.0, y_value_float=0.0)
            elif action_id == 3: self.gamepad.press_button(button=self.buttons["jump"])
            elif action_id == 4: self.gamepad.press_button(button=self.buttons["attack"])
            elif action_id == 5: self.gamepad.press_button(button=self.buttons["dash"])
            elif action_id == 6:
                self.gamepad.press_button(button=self.buttons["jump"])
                self.gamepad.press_button(button=self.buttons["attack"])
            elif action_id == 7:
                self.gamepad.press_button(button=self.buttons["dash"])
                self.gamepad.press_button(button=self.buttons["attack"])
            elif action_id == 8:
                self.gamepad.left_joystick_float(x_value_float=-1.0, y_value_float=0.0)
                self.gamepad.press_button(button=self.buttons["attack"])
            elif action_id == 9:
                self.gamepad.left_joystick_float(x_value_float=1.0, y_value_float=0.0)
                self.gamepad.press_button(button=self.buttons["attack"])
            elif action_id == 10:
                self.gamepad.left_joystick_float(x_value_float=-1.0, y_value_float=0.0)
                self.gamepad.press_button(button=self.buttons["jump"])
            elif action_id == 11:
                self.gamepad.left_joystick_float(x_value_float=1.0, y_value_float=0.0)
                self.gamepad.press_button(button=self.buttons["jump"])
            elif action_id == 12:
                self.gamepad.left_joystick_float(x_value_float=-1.0, y_value_float=0.0)
                self.gamepad.press_button(button=self.buttons["dash"])
            elif action_id == 13:
                self.gamepad.left_joystick_float(x_value_float=1.0, y_value_float=0.0)
                self.gamepad.press_button(button=self.buttons["dash"])
            elif action_id == 14:
                pass
            elif action_id == 15:
                self.gamepad.press_button(button=self.buttons["jump"])
                self.gamepad.press_button(button=self.buttons["dash"])

            self.gamepad.update()

    def reset_all(self):
        self.gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        for btn in self.buttons.values():
            self.gamepad.release_button(button=btn)
        self.gamepad.update()

if __name__ == "__main__":
    ctrl = HollowKnightController()
    
    print("\n[ТЕСТ] У тебя есть 5 секунд, чтобы развернуть хк...")
    time.sleep(5)
    
    print("Идем вправо...")
    ctrl.set_action(2)
    time.sleep(0.5)
    
    print("Прыгаем в движении!")
    ctrl.set_action(3)
    time.sleep(0.3)
    
    print("Рывок!")
    ctrl.set_action(5)
    time.sleep(0.2)
    
    print("Остановка.")
    ctrl.reset_all()
    print("Тест завершен!")