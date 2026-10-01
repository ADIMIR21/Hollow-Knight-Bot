"""Both input paths of `ai_controller`: the pipe (the default) and the emulated pad.

The mapper inside `set_action` cannot be guarded by reading the file. While its body sat
behind the pipe branch's `return`, every branch was still in the source and every text-level
check still passed - only running the code shows that nothing presses anything. These units
drive the real `set_action` and `reset_all` against a stub pad and a stub pipe, so a branch
that is never reached fails instead of hiding.

`vgamepad` is replaced in `sys.modules` before `ai_controller` is imported: connecting a real
pad needs the ViGEmBus driver, and CI installs neither the package nor the driver.
"""

import contextlib
import io
import os
import sys
import types
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import hk_features  # noqa: E402
from hk_features import ACTION_NAMES, ACTION_NONE  # noqa: E402


class FakePad:
    """What the controller asks the gamepad to do, in the order it asks."""

    def __init__(self):
        self.calls = []

    def left_joystick_float(self, x_value_float=0.0, y_value_float=0.0):
        self.calls.append(("stick", x_value_float, y_value_float))

    def press_button(self, button=None):
        self.calls.append(("press", button))

    def release_button(self, button=None):
        self.calls.append(("release", button))

    def update(self):
        self.calls.append(("update",))

    def pressed(self):
        return [call[1] for call in self.calls if call[0] == "press"]

    def stick_moves(self):
        """Stick calls that actually move the hero, i.e. not the neutralising one."""
        return [call[1:] for call in self.calls if call[0] == "stick" and call[1:] != (0.0, 0.0)]


class FakePipe:
    """The mod pipe: only the commands the controller sends are of interest here."""

    is_connected = True

    def __init__(self):
        self.sent = []

    def send_command(self, command):
        self.sent.append(command)
        return True


PADS = []


def _new_pad():
    pad = FakePad()
    PADS.append(pad)
    return pad


def _install_fake_vgamepad():
    module = types.ModuleType("vgamepad")
    module.XUSB_BUTTON = types.SimpleNamespace(
        XUSB_GAMEPAD_A="A",
        XUSB_GAMEPAD_X="X",
        XUSB_GAMEPAD_B="B",
        XUSB_GAMEPAD_RIGHT_SHOULDER="RB",
        XUSB_GAMEPAD_START="START",
    )
    module.VX360Gamepad = _new_pad
    sys.modules["vgamepad"] = module


_install_fake_vgamepad()
# Belt and braces: if anything imported the controller earlier, that copy holds the real
# package and the assertions below would be made against a driver that is not there.
sys.modules.pop("ai_controller", None)
import ai_controller  # noqa: E402


class InputPath(unittest.TestCase):
    """`HK_INPUT` picks the transport, and both transports have to reach the game."""

    def setUp(self):
        # The constructor waits 2 s for the game to notice a new pad; nothing here needs that.
        quiet = mock.patch("ai_controller.time.sleep")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.saved_input = os.environ.get("HK_INPUT")
        self.addCleanup(self.restore_env)

    def restore_env(self):
        if self.saved_input is None:
            os.environ.pop("HK_INPUT", None)
        else:
            os.environ["HK_INPUT"] = self.saved_input

    def build(self, mode=None, pipe=None):
        """A controller in the requested mode, with the pad recorder emptied first."""
        del PADS[:]
        os.environ.pop("HK_INPUT", None)
        if mode is not None:
            os.environ["HK_INPUT"] = mode
        with contextlib.redirect_stdout(io.StringIO()):  # the constructor announces the pad
            controller = ai_controller.HollowKnightController(pipe)
        return controller, (PADS[0] if PADS else None)

    def test_the_pipe_is_the_default(self):
        pipe = FakePipe()
        controller, _ = self.build(pipe=pipe)
        self.assertTrue(controller.use_pipe_input, "training without HK_INPUT must use the pipe")
        self.assertEqual(PADS, [], "a gamepad was connected even though the pipe is the default")
        self.assertIsNone(controller.gamepad)
        controller.set_action(7)
        controller.reset_all()
        self.assertEqual(pipe.sent, ["action 7", "action 0"])

    def test_the_emulated_pad_presses_buttons(self):
        pipe = FakePipe()
        controller, pad = self.build("pad", pipe=pipe)
        self.assertFalse(controller.use_pipe_input)
        self.assertIsNotNone(pad, "HK_INPUT=pad did not connect a gamepad")

        controller.set_action(4)
        self.assertEqual(pad.pressed(), [controller.buttons["attack"]])
        self.assertEqual(pipe.sent, [], "pad mode must not also send the action into the pipe")
        # Everything is released first, so an action cannot inherit the previous one.
        released = [call for call in pad.calls if call[0] == "release"]
        self.assertEqual(len(released), len(controller.buttons), pad.calls)
        self.assertEqual(pad.calls[-1], ("update",), "the pad was not flushed")

        controller.reset_all()
        self.assertEqual(pad.pressed(), [controller.buttons["attack"]], "reset_all pressed something")
        self.assertEqual(pad.stick_moves(), [], "reset_all left the stick off centre")
        self.assertEqual(pipe.sent, [])

    def test_every_action_moves_the_pad(self):
        # The check the text-level test in test_features.py cannot make: a branch that exists
        # in the source but never runs passes it. Action 0 is the idle one - it releases and
        # flushes, and that is all it is supposed to do.
        controller, pad = self.build("pad")
        checked = 0
        for action_id in sorted(ACTION_NAMES):
            if action_id == ACTION_NONE:
                continue
            checked += 1
            pad.calls.clear()
            controller.set_action(action_id)
            self.assertTrue(
                pad.pressed() or pad.stick_moves(),
                "action %d (%s) does not touch the pad: %r"
                % (action_id, ACTION_NAMES[action_id], pad.calls),
            )
            self.assertEqual(
                pad.calls[-1], ("update",), "action %d did not flush the pad" % action_id
            )
        # An empty or shrunken table would make the loop above pass over nothing.
        self.assertEqual(checked, hk_features.ACTION_COUNT - 1)

    def test_only_the_exact_value_selects_the_pad(self):
        # Anything else keeps the pipe. A typo must not drop the input onto a device that is
        # not connected, which is exactly how the hero ends up standing still for a night.
        for value in ("pad", "PAD", " pad "):
            _, pad = self.build(value)
            self.assertIsNotNone(pad, "HK_INPUT=%r did not select the pad" % value)
        for value in ("pipe", "", "gamepad", "padd"):
            controller, pad = self.build(value)
            self.assertTrue(controller.use_pipe_input, "HK_INPUT=%r did not keep the pipe" % value)
            self.assertIsNone(pad)

    def test_the_units_never_touch_the_real_driver(self):
        # The stub has to be what `ai_controller` imported: CI has no `vgamepad` at all, and a
        # machine with ViGEmBus would otherwise run different code than the one tested here.
        self.assertIs(ai_controller.vg, sys.modules["vgamepad"])
        self.assertIs(ai_controller.vg.VX360Gamepad, _new_pad)

    def test_a_missing_pipe_is_not_an_error(self):
        # Training may start before the mod is loaded; the action is lost, not fatal.
        controller, _ = self.build()
        self.assertIsNone(controller.pipe)
        controller.set_action(3)
        controller.reset_all()


if __name__ == "__main__":
    unittest.main()
