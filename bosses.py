"""Реестр — зеркало BossRegistry в Mod/HK_AI_Mod/AiDataExporter.cs.
Сцены взяты из build settings игры (hollow_knight_Data/globalgamemanagers).
Варианты с суффиксом _V — усложнённые версии боёв (Ascended/Radiant),
GG_Mantis_Lords_V = Sisters of Battle, GG_Nosk_Hornet = Winged Nosk.

Протокол — именованный пайп ``\\\\.\\pipe\\hk_ai_mod`` (см. hk_pipe.py):
  Python -> Мод: restart | teleport | set_boss <сцена> | set_gate <гейт>
                 | boss <запрос> | bosses | warp
  Мод -> Python: телеметрия боя (содержит поле "scene") плюс одноразовые
                 события boss_list / boss_selected / command_error.
Файлы в %TEMP% (hk_ai_cmd.txt, hk_ai_data.json и прочие) больше не используются.
"""

import time

from hk_pipe import REQUIRED_PROTOCOL, get_shared_client

DEFAULT_SCENE = "GG_False_Knight"
# Арены Godhome принимают героя через единственный TransitionPoint сцены —
# door_dreamEnter (мод всё равно подбирает существующий гейт сам).
DEFAULT_GATE = "door_dreamEnter"

# (scene, label) — порядок и подписи совпадают с реестром мода.
BOSS_LIST = [
    # Пантеон Мастера (ранние боссы)
    ("GG_Vengefly", "Vengefly King"),
    ("GG_Gruz_Mother", "Gruz Mother"),
    ("GG_False_Knight", "False Knight"),
    ("GG_Mega_Moss_Charger", "Massive Moss Charger"),
    ("GG_Hornet_1", "Hornet Protector"),
    ("GG_Brooding_Mawlek", "Brooding Mawlek"),
    # Пантеон Художника (раньше-середина игры)
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
    # Пантеон Мудреца (середина-поздняя игра)
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
    # Пантеон Рыцаря (поздние боссы)
    ("GG_Hornet_2", "Hornet Sentinel"),
    ("GG_Grey_Prince_Zote", "Grey Prince Zote"),
    ("GG_White_Defender", "White Defender"),
    ("GG_Grimm_Nightmare", "Nightmare King Grimm"),
    ("GG_Hollow_Knight", "Pure Vessel"),
    # Пантеон Халлоунеста (финал)
    ("GG_Radiance", "The Radiance"),
    # Гвоздемастеры (финалы пантеонов 1-3)
    ("GG_Nailmasters", "Brothers Oro & Mato"),
    ("GG_Painter", "Paintmaster Sheo"),
    ("GG_Sly", "Great Nailsage Sly"),
    ("GG_Lurker", "Pale Lurker"),
    # Усложнённые варианты боёв (Ascended/Radiant)
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
    # Хаб Godhome (не боссы, но полезно телепортироваться)
    ("GG_Atrium", "Godhome Atrium (хаб)"),
    ("GG_Workshop", "Godhome Workshop (верстак)"),
    ("GG_Boss_Door_Entrance", "Двери пантеонов"),
]

_SCENE_TO_LABEL = {scene: label for scene, label in BOSS_LIST}

# Популярные короткие алиасы — зеркало ExtraAliases в моде.
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
    """Приводит запрос к нижнему регистру с подчёркиваниями."""
    if query is None:
        return ""
    norm = str(query).strip().lower()
    for ch in (" ", "-"):
        norm = norm.replace(ch, "_")
    while "__" in norm:
        norm = norm.replace("__", "_")
    return norm.strip("_")


def resolve_query(query):
    """Разбирает запрос на босса локально (для меню/валидации).

    Поддерживает номер в списке, имя сцены (любой регистр), алиас,
    точное название и часть названия. Возвращает (scene, label) или None.
    """
    norm = normalize(query)
    if not norm:
        return None

    # 1. Номер в списке
    if norm.isdigit():
        index = int(norm)
        if 1 <= index <= len(BOSS_LIST):
            scene, label = BOSS_LIST[index - 1]
            return scene, label
        return None

    # 2. Точное имя сцены
    if norm in _SCENE_TO_LABEL:
        for scene, label in BOSS_LIST:
            if scene.lower() == norm:
                return scene, label

    # 3. Алиас
    scene = _EXTRA_ALIASES.get(norm)
    if scene:
        return scene, _SCENE_TO_LABEL.get(scene, scene)

    # 4. Точное название босса
    for scene, label in BOSS_LIST:
        if normalize(label) == norm:
            return scene, label

    # 5. Частичное совпадение — при неоднозначности отдаём предпочтение
    #    базовой версии босса (не (Variant) и не *_V)
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


# ---------------- Протокол обмена с модом (именованный пайп) ----------------

def send_command(cmd):
    """Отправляет команду моду. True — строка ушла в пайп."""
    return get_shared_client().send_command(cmd)


def set_boss_scene(scene_name):
    """Задаёт целевую сцену для рестартов (мод понимает алиасы: hornet, nkg)."""
    return send_command("set_boss " + str(scene_name).strip())


def set_gate(gate_name):
    """Задаёт входной гейт арены (по умолчанию door_dreamEnter — вход Godhome)."""
    return send_command("set_gate " + (str(gate_name).strip() or DEFAULT_GATE))


def request_boss(query):
    """Телепорт к выбранному боссу: мод сам резолвит имя и запоминает сцену."""
    return send_command("boss " + str(query).strip())


def request_restart():
    """Быстрый рестарт боя в целевой сцене."""
    return send_command("restart")


def request_warp():
    """Вернуть героя к гейту арены без перезагрузки сцены."""
    return send_command("warp")


def request_boss_list():
    """Попросить мод прислать полный список боссов событием boss_list."""
    return send_command("bosses")


def read_boss_list_from_mod(timeout=3.0):
    """Ждёт выгрузку реестра от мода (событие boss_list).

    Событие кэшируется клиентом по статусу, поэтому ответ находится даже
    если мод успел ответить раньше, чем мы начали ждать.
    """
    return get_shared_client().wait_for_status("boss_list", timeout=timeout)


def fetch_boss_list(timeout=3.0):
    """Запрашивает реестр у мода и дожидается именно его ответа.

    Возвращает словарь события boss_list или None по таймауту.
    """
    client = get_shared_client()
    before = client.get_status_seq("boss_list")
    if not client.send_command("bosses"):
        return None
    return client.wait_for_status("boss_list", timeout=timeout, after_seq=before)


def read_telemetry():
    """Последняя телеметрия мода (None, если связи нет)."""
    return get_shared_client().get_telemetry()


def is_connected(timeout=5.0):
    """Подключён ли пайп мода (ждём до timeout секунд)."""
    return get_shared_client().wait_connected(timeout)


def wait_hello(timeout=3.0):
    """Ждёт hello-сообщение мода (в нём версия протокола). None по таймауту."""
    return get_shared_client().wait_hello(timeout)


def mod_protocol():
    """Версия протокола мода из hello или None."""
    return get_shared_client().protocol


def mod_version():
    """Версия мода из hello или None."""
    return get_shared_client().mod_version


def current_scene():
    """Текущая сцена из телеметрии или None."""
    data = read_telemetry()
    if data is None:
        return None
    return data.get("scene")


def wait_for_scene(expected_scene=None, timeout=30.0, on_progress=None):
    """Ждёт, пока телеметрия покажет целевую сцену и живой бой.

    Возвращает True, если сцена загрузилась и бой поднялся.
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
        time.sleep(0.05)
    return False


def mod_has_scene_field():
    """Есть ли поле scene в телеметрии."""
    data = read_telemetry()
    return data is not None and "scene" in data
