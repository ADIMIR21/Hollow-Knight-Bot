import os
import vgamepad as vg
import time

DEFAULT_BOSS_SCENE = "GG_False_Knight"
# In Godhome arenas the entry TransitionPoint is called door_dreamEnter — see
# DEFAULT_ENTRY_GATE in the mod (Mod/AiTrainHK/AiDataExporter.cs).
DEFAULT_ENTRY_GATE = "door_dreamEnter"

class HollowKnightController:
    def __init__(self, pipe=None):
        # Input goes through the mod's pipe by default; HK_INPUT=pad switches back to the
        # legacy emulated gamepad.
        self.use_pipe_input = os.environ.get("HK_INPUT", "pipe").strip().lower() != "pad"
        if not self.use_pipe_input:
            print("[CONTROLLER] Connecting the gamepad...")
        self.gamepad = None if self.use_pipe_input else vg.VX360Gamepad()
        
        time.sleep(2.0)
        if not self.use_pipe_input: print("[CONTROLLER] Xbox 360 gamepad connected!")
        
        # Update 4: commands (restart/scene/gate) go into the mod pipe,
        # not into %TEMP% files. The pipe client is shared with ai_environment.
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
        print(f"[CONTROLLER] Target boss scene: {self.boss_scene}")

    def set_entry_gate(self, gate_name):
        """Sets the arena gate. It is important to keep it in sync with the mod: the
        restart command always carries both the scene and the gate, so a mismatch here
        would override the gate set through bosses.set_gate()."""
        self.entry_gate = (gate_name or DEFAULT_ENTRY_GATE).strip()
        if self.pipe is not None:
            self.pipe.send_command("set_gate " + self.entry_gate)
        print(f"[CONTROLLER] Entry gate: {self.entry_gate}")

    def request_fast_restart(self, scene=None, gate=None):
        """Sends the restart command to the mod pipe. True — the command was sent."""
        scene = (scene or self.boss_scene).strip()
        gate = (gate or self.entry_gate).strip()
        if self.pipe is None or not self.pipe.is_connected:
            return False
        sent = self.pipe.send_command(f"restart {scene} {gate}")
        if sent:
            print(f"[CONTROLLER] Restart command sent: {scene} ({gate})")
        return sent

    def set_paused(self, paused):
        """Freezes or unfreezes the game around the policy update (Update 9).

        The mod sets the game's time scale to zero, so the fight really stops
        while the PPO update runs. True - the command was sent.
        """
        if self.pipe is None or not self.pipe.is_connected:
            return False
        sent = self.pipe.send_command("pause" if paused else "resume")
        if sent:
            print("[CONTROLLER] " + ("Pause" if paused else "Resume") + " command sent")
        return sent

    def set_action(self, action_id):
        if self.use_pipe_input:
            if self.pipe is not None:
                self.pipe.send_command("action %d" % action_id)
            return
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
            self.gamepad.left_joystick_float(x_value_float=0.0, y_value_float=1.0)
            self.gamepad.press_button(button=self.buttons["attack"])
        elif action_id == 15:
            self.gamepad.press_button(button=self.buttons["jump"])
            self.gamepad.press_button(button=self.buttons["dash"])
        elif action_id == 16:
            self.gamepad.left_joystick_float(x_value_float=0.0, y_value_float=-1.0)
            self.gamepad.press_button(button=self.buttons["attack"])
        elif action_id == 17:
            self.gamepad.press_button(button=self.buttons["focus"])
        elif action_id == 18:
            self.gamepad.left_joystick_float(x_value_float=0.0, y_value_float=1.0)
            self.gamepad.press_button(button=self.buttons["focus"])

        self.gamepad.update()

    def reset_all(self):
        if self.use_pipe_input:
            if self.pipe is not None:
                self.pipe.send_command("action 0")
            return
        self.gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        for btn in self.buttons.values():
            self.gamepad.release_button(button=btn)
        self.gamepad.update()

if __name__ == "__main__":
    ctrl = HollowKnightController()
    
    print("\n[TEST] You have 5 seconds to switch to Hollow Knight...")
    time.sleep(5)
    
    print("Moving right...")
    ctrl.set_action(2)
    time.sleep(0.5)
    
    print("Jumping while moving!")
    ctrl.set_action(3)
    time.sleep(0.3)
    
    print("Dash!")
    ctrl.set_action(5)
    time.sleep(0.2)
    
    print("Stopping.")
    ctrl.reset_all()
    print("Test finished!")