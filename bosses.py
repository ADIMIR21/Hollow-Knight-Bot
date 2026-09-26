"""Godhome boss registry and low-level command protocol for the HK_AI_Mod.

The registry mirrors BossRegistry in Mod/HK_AI_Mod/AiDataExporter.cs.
Scenes are taken from the game's build settings (hollow_knight_Data/globalgamemanagers).
Variants with the _V suffix are harder versions of the fights (Ascended/Radiant),
GG_Mantis_Lords_V = Sisters of Battle, GG_Nosk_Hornet = Winged Nosk.

Protocol (files in %TEMP%, same as the mod):
  hk_ai_cmd.txt   — command: restart | teleport | boss <query> | bosses | warp
  hk_ai_boss.txt  — target boss scene (the mod overwrites it itself on "boss <x>")
  hk_ai_gate.txt  — arena entrance gate name (defaults to door_dreamEnter)
  hk_ai_data.json — telemetry
"""

import os
import tempfile
import time
import json

CMD_FILE = os.path.join(tempfile.gettempdir(), "hk_ai_cmd.txt")
BOSS_SCENE_FILE = os.path.join(tempfile.gettempdir(), "hk_ai_boss.txt")
GATE_FILE = os.path.join(tempfile.gettempdir(), "hk_ai_gate.txt")
BOSS_LIST_FILE = os.path.join(tempfile.gettempdir(), "hk_ai_bosses.json")
TELEMETRY_FILE = os.path.join(tempfile.gettempdir(), "hk_ai_data.json")

DEFAULT_SCENE = "GG_False_Knight"
# Godhome arenas accept the hero through the scene's single TransitionPoint —
# door_dreamEnter (mod v1 finds an existing gate itself anyway).
DEFAULT_GATE = "door_dreamEnter"

# (scene, label) — order and labels match the mod's registry.
BOSS_LIST = [
    # Pantheon of the Master (early bosses)
    ("GG_Vengefly", "Vengefly King"),
    ("GG_Gruz_Mother", "Gruz Mother"),
    ("GG_False_Knight", "False Knight"),
    ("GG_Mega_Moss_Charger", "Massive Moss Charger"),
    ("GG_Hornet_1", "Hornet Protector"),
    ("GG_Brooding_Mawlek", "Brooding Mawlek"),
    # Pantheon of the Artist (early-mid game)
    ("GG_Soul_Master", "Soul Master"),
    ("GG_Crystal_Guardian", "Crystal Guardian"),
    ("GG_Crystal_Guardian_2", "Enraged Guardian"),
    ("GG_Grimm", "Troupe Master Grimm"),
    ("GG_Collector", "The Collector"),
    ("GG_Soul_Tyrant", "Soul Tyrant"),
    ("GG_Dung_Defender", "Dung Defender"),
    ("GG_Mage_Knight", "Soul Warrior"),
    ("GG_Watcher_Knights", "Watcher Knights"),
    ("GG_Oblobbles", "Oblobbles"),
    ("GG_Hive_Knight", "Hive Knight"),
    ("GG_Nosk", "Nosk"),
    ("GG_Mantis_Lords", "Mantis Lords"),
    ("GG_Broken_Vessel", "Broken Vessel"),
    # Pantheon of the Sage (mid-late game)
    ("GG_Lost_Kin", "Lost Kin"),
    ("GG_Failed_Champion", "Failed Champion"),
    ("GG_Traitor_Lord", "Traitor Lord"),
    ("GG_Uumuu", "Uumuu"),
    ("GG_Flukemarm", "Flukemarm"),
    ("GG_God_Tamer", "God Tamer"),
    ("GG_Ghost_Xero", "Xero"),
    ("GG_Ghost_Gorb", "Gorb"),
    ("GG_Ghost_Marmu", "Marmu"),
    ("GG_Ghost_No_Eyes", "No Eyes"),
    ("GG_Ghost_Markoth", "Markoth"),
    ("GG_Ghost_Galien", "Galien"),
    ("GG_Ghost_Hu", "Elder Hu"),
    # Pantheon of the Knight (late bosses)
    ("GG_Hornet_2", "Hornet Sentinel"),
    ("GG_Grey_Prince_Zote", "Grey Prince Zote"),
    ("GG_White_Defender", "White Defender"),
    ("GG_Grimm_Nightmare", "Nightmare King Grimm"),
    ("GG_Hollow_Knight", "Pure Vessel"),
    # Pantheon of Hallownest (finale)
    ("GG_Radiance", "The Radiance"),
    # Nailmasters (Pantheon 1-3 finales)
    ("GG_Nailmasters", "Brothers Oro & Mato"),
    ("GG_Painter", "Paintmaster Sheo"),
    ("GG_Sly", "Great Nailsage Sly"),
    ("GG_Lurker", "Pale Lurker"),
    # Harder fight variants (Ascended/Radiant)
    ("GG_Mantis_Lords_V", "Sisters of Battle"),
    ("GG_Nosk_Hornet", "Winged Nosk"),
    ("GG_Vengefly_V", "Vengefly King (Variant)"),
    ("GG_Gruz_Mother_V", "Gruz Mother (Variant)"),
    ("GG_Brooding_Mawlek_V", "Brooding Mawlek (Variant)"),
    ("GG_Collector_V", "The Collector (Variant)"),
    ("GG_Mage_Knight_V", "Soul Warrior (Variant)"),
    ("GG_Nosk_V", "Nosk (Variant)"),
    ("GG_Uumuu_V", "Uumuu (Variant)"),
    ("GG_Ghost_Gorb_V", "Gorb (Variant)"),
    ("GG_Ghost_Marmu_V", "Marmu (Variant)"),
    ("GG_Ghost_Markoth_V", "Markoth (Variant)"),
    ("GG_Ghost_No_Eyes_V", "No Eyes (Variant)"),
    ("GG_Ghost_Xero_V", "Xero (Variant)"),
    # Godhome hubs (not bosses, but useful to warp to)
    ("GG_Atrium", "Godhome Atrium (hub)"),
    ("GG_Workshop", "Godhome Workshop (bench)"),
    ("GG_Boss_Door_Entrance", "Pantheon Doors"),
]

_SCENE_TO_LABEL = {scene: label for scene, label in BOSS_LIST}

# Popular short aliases — mirror of ExtraAliases in the mod.
_EXTRA_ALIASES = {
    "hornet": "GG_Hornet_1",
    "hornet2": "GG_Hornet_2",
    "hornet_sentinel": "GG_Hornet_2",
    "sentinel": "GG_Hornet_2",
    "false": "GG_False_Knight",
    "falseknight": "GG_False_Knight",
    "gruz": "GG_Gruz_Mother",
    "gruzmother": "GG_Gruz_Mother",
    "vengefly_king": "GG_Vengefly",
    "moss_charger": "GG_Mega_Moss_Charger",
    "mawlek": "GG_Brooding_Mawlek",
    "vessel": "GG_Hollow_Knight",
    "pure_vessel": "GG_Hollow_Knight",
    "hollow_knight": "GG_Hollow_Knight",
    "thk": "GG_Hollow_Knight",
    "radiance": "GG_Radiance",
    "nkg": "GG_Grimm_Nightmare",
    "nightmare_king": "GG_Grimm_Nightmare",
    "zote": "GG_Grey_Prince_Zote",
    "gpz": "GG_Grey_Prince_Zote",
    "sisters": "GG_Mantis_Lords_V",
    "sisters_of_battle": "GG_Mantis_Lords_V",
    "winged_nosk": "GG_Nosk_Hornet",
    "oro": "GG_Nailmasters",
    "mato": "GG_Nailmasters",
    "oro_mato": "GG_Nailmasters",
    "nailmasters": "GG_Nailmasters",
    "sheo": "GG_Painter",
    "paintmaster": "GG_Painter",
    "nailsage": "GG_Sly",
    "soul_warrior": "GG_Mage_Knight",
    "watcher": "GG_Watcher_Knights",
    "umuu": "GG_Uumuu",
    "traitor": "GG_Traitor_Lord",
    "abs_rad": "GG_Radiance",
}


def normalize(query):
    """Normalizes the query to lowercase with underscores."""
    if query is None:
        return ""
    norm = str(query).strip().lower()
    for ch in (" ", "-"):
        norm = norm.replace(ch, "_")
    while "__" in norm:
        norm = norm.replace("__", "_")
    return norm.strip("_")


def resolve_query(query):
    """Resolves a boss query locally (for menus/validation).

    Supports a list number, scene name (any case), an alias,
    an exact label and a partial label. Returns (scene, label) or None.
    """
    norm = normalize(query)
    if not norm:
        return None

    # 1. List number
    if norm.isdigit():
        index = int(norm)
        if 1 <= index <= len(BOSS_LIST):
            scene, label = BOSS_LIST[index - 1]
            return scene, label
        return None

    # 2. Exact scene name
    if norm in _SCENE_TO_LABEL:
        for scene, label in BOSS_LIST:
            if scene.lower() == norm:
                return scene, label

    # 3. Alias
    scene = _EXTRA_ALIASES.get(norm)
    if scene:
        return scene, _SCENE_TO_LABEL.get(scene, scene)

    # 4. Exact boss label
    for scene, label in BOSS_LIST:
        if normalize(label) == norm:
            return scene, label

    # 5. Partial match — on ambiguity prefer the
    #    base version of the boss (not (Variant) and not *_V)
    matches = []
    for scene, label in BOSS_LIST:
        if norm in normalize(scene) or norm in normalize(label):
            matches.append((scene, label))
    if len(matches) > 1:
        base = [m for m in matches if not m[0].endswith("_V") and "(Variant)" not in m[1]]
        if len(base) == 1:
            matches = base
    if len(matches) == 1:
        return matches[0]

    return None


# ---------------- Exchange protocol with the mod ----------------

def write_file(path, text):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except OSError as e:
        print(f"[BOSS] Failed to write {path}: {e}")
        return False


def send_command(cmd):
    """Writes a command to hk_ai_cmd.txt (the mod polls the file every tick)."""
    return write_file(CMD_FILE, cmd)


def set_boss_scene(scene_name):
    """Sets the target scene for restarts (hk_ai_boss.txt)."""
    return write_file(BOSS_SCENE_FILE, str(scene_name).strip())


def set_gate(gate_name):
    """Sets the arena entrance gate (hk_ai_gate.txt); empty string → DEFAULT_GATE."""
    write_file(GATE_FILE, str(gate_name).strip() or DEFAULT_GATE)


def request_boss(query):
    """Teleport to the selected boss: the mod resolves the name itself and remembers the scene."""
    return send_command(f"boss {query}")


def request_restart():
    """Quick restart of the fight in the target scene."""
    return send_command("restart")


def request_warp():
    """Return the hero to the arena gate without reloading the scene."""
    return send_command("warp")


def request_boss_list():
    """Ask the mod to dump the full boss list to hk_ai_bosses.json."""
    return send_command("bosses")


def read_boss_list_from_mod():
    """Reads the mod's dump of hk_ai_bosses.json (command 'bosses')."""
    try:
        with open(BOSS_LIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def read_telemetry():
    try:
        with open(TELEMETRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def current_scene():
    """Current scene from telemetry, or None."""
    data = read_telemetry()
    if data is None:
        return None
    return data.get("scene")


def wait_for_scene(expected_scene=None, timeout=30.0, on_progress=None):
    """Waits until telemetry shows the target scene and a live fight.

    Returns True if the scene loaded and the fight came up.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = read_telemetry()
        if data is not None:
            scene = data.get("scene", "")
            status = data.get("status", "")
            if on_progress:
                on_progress(scene, status)
            if (expected_scene is None or scene == expected_scene) \
                    and status == "fight" \
                    and float(data.get("hp", 0)) > 0 \
                    and float(data.get("boss_hp", 0)) > 0 \
                    and int(data.get("restart_pending", 0)) == 0:
                return True
        time.sleep(0.3)
    return False


def mod_has_scene_field():
    """Whether the telemetry has a scene field."""
    data = read_telemetry()
    return data is not None and "scene" in data
