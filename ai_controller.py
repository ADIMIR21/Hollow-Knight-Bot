import vgamepad as vg
import time
import pyautogui

class HollowKnightController:
    def __init__(self):
        print("[CONTROLLER] Подключаем геймпад...")
        self.gamepad = vg.VX360Gamepad()
        
        time.sleep(2.0)
        print("[CONTROLLER] Геймпад Xbox 360 подключился)!")
        
        self.buttons = {
            "jump": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
            "attack": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
            "focus": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
            "dash": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "pause": vg.XUSB_BUTTON.XUSB_GAMEPAD_START
        }

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

    def toggle_pause(self):
        print("[CONTROLLER] Пауза через Escape...")
        self.reset_all()
        time.sleep(0.1)
        pyautogui.press('esc')
        time.sleep(0.5)
        print("[CONTROLLER] Escape нажат.")

    def restart_boss_fight(self):
        print("[СИСТЕМА] Запускаем макрос перезапуска боя...")
        
        self.reset_all()
        time.sleep(6.0)

        self.gamepad.press_button(button=self.buttons["jump"])
        self.gamepad.update()
        time.sleep(3.0)
        self.reset_all()
        self.gamepad.release_button(button=self.buttons["jump"])
        self.gamepad.update()
        time.sleep(4.0)
        self.reset_all()
        
        self.gamepad.left_joystick_float(x_value_float=0.0, y_value_float=1.0)
        self.gamepad.update()
        time.sleep(0.2)
        self.reset_all()
        
        time.sleep(2.0)

        self.reset_all()
        self.gamepad.press_button(button=self.buttons["jump"])
        self.gamepad.update()
        
        time.sleep(0.6)

        self.reset_all()
        
        print("[СИСТЕМА] Бой запущен...")

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