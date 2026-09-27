# -*- coding: utf-8 -*-
"""Pure pieces of the training environment: the action table, the attack aiming
rule and the boss-state features.

Nothing here imports numpy, gymnasium or vgamepad on purpose: this is the part of
``hk_gym`` that is worth testing without a game, and the unit tier runs with the
standard library only (see AGENTS.md, tier 1).
"""

# --------------------------------------------------------------------------- #
# Action table
# --------------------------------------------------------------------------- #
# An action id travels to the mod as one number; ai_controller maps it to gamepad
# buttons. Keep this table and that mapping in step - README.md carries the same
# table for humans.
ACTION_COUNT = 16

ACTION_NONE = 0
ACTION_LEFT = 1
ACTION_RIGHT = 2
ACTION_JUMP = 3
ACTION_ATTACK = 4           # aimed at the boss, see redirect_action
ACTION_DASH = 5
ACTION_JUMP_ATTACK = 6
ACTION_DASH_ATTACK = 7
ACTION_LEFT_ATTACK = 8
ACTION_RIGHT_ATTACK = 9
ACTION_LEFT_JUMP = 10
ACTION_RIGHT_JUMP = 11
ACTION_LEFT_DASH = 12
ACTION_RIGHT_DASH = 13
ACTION_IDLE = 14
ACTION_JUMP_DASH = 15

ACTION_NAMES = {
    ACTION_NONE: "none",
    ACTION_LEFT: "left",
    ACTION_RIGHT: "right",
    ACTION_JUMP: "jump",
    ACTION_ATTACK: "attack",
    ACTION_DASH: "dash",
    ACTION_JUMP_ATTACK: "jump+attack",
    ACTION_DASH_ATTACK: "dash+attack",
    ACTION_LEFT_ATTACK: "left+attack",
    ACTION_RIGHT_ATTACK: "right+attack",
    ACTION_LEFT_JUMP: "left+jump",
    ACTION_RIGHT_JUMP: "right+jump",
    ACTION_LEFT_DASH: "left+dash",
    ACTION_RIGHT_DASH: "right+dash",
    ACTION_IDLE: "idle",
    ACTION_JUMP_DASH: "jump+dash",
}

# Actions that press the attack button; the aiming rule below only touches one.
ATTACK_ACTIONS = (ACTION_ATTACK, ACTION_JUMP_ATTACK, ACTION_DASH_ATTACK,
                  ACTION_LEFT_ATTACK, ACTION_RIGHT_ATTACK)

# The dx_to_boss threshold (normalised direction) beyond which the boss counts as
# "to the left" or "to the right" of the hero.
AIM_THRESHOLD = 0.3


def redirect_action(action, dx_to_boss, aim_threshold=AIM_THRESHOLD):
    """Aims the plain attack at the boss; every other action is left alone.

    Update 10. Before it, all five attack actions were rewritten to 8 or 9
    whenever the boss was further than ``aim_threshold`` away. That silently threw
    the extra button away: "jump attack" (6) lost the jump and "dash attack" (7)
    lost the dash, so while the boss was off centre - which is nearly always -
    neither skill could ever happen and neither could be learned. Only "attack"
    (4) is aimed now, which is also what the ppo action table in README.md says.
    """
    if action != ACTION_ATTACK:
        return action
    if dx_to_boss > aim_threshold:
        return ACTION_RIGHT_ATTACK
    if dx_to_boss < -aim_threshold:
        return ACTION_LEFT_ATTACK
    return action


# --------------------------------------------------------------------------- #
# Boss state
# --------------------------------------------------------------------------- #
# The mod sends the boss's current animation clip name in "boss_state" and
# replaces it with the attacking FSM state name when its own IsAttackFsmState()
# matches (Mod/HK_AI_Mod/AiDataExporter.cs). The names in the comments below were
# observed in ModLog.txt during a live False Knight session, and the keyword
# families are the ones the mod itself matches on - this file does not invent game
# internals.
#
#   idle     Idle, First Idle, Run, Turn L/R, Ready, JA End, S Fall, Steam
#   windup   JA Antic, Jump Antic, S Antic, S Attack Antic, R Attack Antic,
#            Rage Jump Antic, Run Antic
#   attack   JA Slam, Slam, S Attack
#   open     Opened, Opened 2, Open Uuup, Stun In Air, Stun Land, Stun Fail,
#            Recover, S Attack Recover
#   dead     Death Anim Start, Death Open
BOSS_STATE_IDLE = 0
BOSS_STATE_WINDUP = 1
BOSS_STATE_ATTACK = 2
BOSS_STATE_OPEN = 3
BOSS_STATE_DEAD = 4

# Checked in this order: "S Attack Recover" is both an attack and a recovery and
# the recovery is what the policy can act on, while "Death Open" is a death and
# not a punish window.
_DEAD_KEYWORDS = ("death",)
_WINDUP_KEYWORDS = ("antic",)
_OPEN_KEYWORDS = ("open", "stun", "dazed", "recover")
_ATTACK_KEYWORDS = ("attack", "slam", "charge", "swipe", "stomp", "strike",
                    "shoot", "spit", "smash", "pound")

# Feature names, appended to STAT_NAMES in hk_gym.py in this exact order.
BOSS_STATE_FEATURES = (
    "boss_attack_antic",
    "boss_open",
    "boss_dead",
    "boss_state_age",
    "boss_state_changed",
)

# How many frames a state is "young" for: 60 frames is ~0.8 s at the 75 fps the
# training loop runs at, which is the order of a windup.
BOSS_STATE_PHASE_FRAMES = 60.0


def classify_boss_state(state):
    """Maps the mod's ``boss_state`` string to one of the BOSS_STATE_* classes."""
    if not state:
        return BOSS_STATE_IDLE
    name = str(state).strip().lower()
    if any(key in name for key in _DEAD_KEYWORDS):
        return BOSS_STATE_DEAD
    if any(key in name for key in _WINDUP_KEYWORDS):
        return BOSS_STATE_WINDUP
    if any(key in name for key in _OPEN_KEYWORDS):
        return BOSS_STATE_OPEN
    if any(key in name for key in _ATTACK_KEYWORDS):
        return BOSS_STATE_ATTACK
    return BOSS_STATE_IDLE


class BossStateTracker:
    """Turns the boss_state string into the five features listed above.

    The name itself never reaches the policy. What a fight needs is whether an
    attack is winding up, whether the boss is open for a hit, and how long the
    current state has been running - the last one is the only memory the policy
    has, because the frame stack spans ~50 ms at 75 fps.
    """

    def __init__(self, phase_frames=BOSS_STATE_PHASE_FRAMES):
        self.phase_frames = float(phase_frames)
        self.reset()

    def reset(self):
        """Forgets the fight: call it from the env's reset()."""
        self.state = None
        self.age = 0
        self.kind = BOSS_STATE_IDLE
        self.changed = 0.0

    def update(self, state=None):
        """Feeds one telemetry frame (None when there is no telemetry yet)."""
        self.changed = 0.0
        if state is not None:
            state = str(state)
            if state != self.state:
                if self.state is not None:
                    # A real transition: the new state starts at age zero. The
                    # first frame of an episode is not a change, it is the state
                    # the fight begins in.
                    self.age = 0
                    self.changed = 1.0
                self.state = state
                self.kind = classify_boss_state(state)
            else:
                self.age += 1
        return self.features()

    def features(self):
        """The five observation values, as a list of floats."""
        return [
            1.0 if self.kind == BOSS_STATE_WINDUP else 0.0,
            1.0 if self.kind == BOSS_STATE_OPEN else 0.0,
            1.0 if self.kind == BOSS_STATE_DEAD else 0.0,
            min(1.0, self.age / self.phase_frames),
            self.changed,
        ]
