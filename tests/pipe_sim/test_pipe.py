# -*- coding: utf-8 -*-
"""Интеграционный тест Python-клиента пайпа против mock-сервера мода.

Проверяет транспорт (hk_pipe), протокол команд (bosses) и логику телепорта
(teleport) без запущенной игры. Запускать из корня репозитория.
"""
import os
import sys
import time

# Стенд слушает своё имя пайпа: без этого тест подключился бы к пайпу
# запущенной игры и начал бы управлять модом вместо макета (так и случилось
# однажды — тест ушёл телепортировать героя в GG_No_Such_Boss_Scene).
os.environ.setdefault("HK_PIPE_NAME", "hk_ai_mod_sim")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import bosses  # noqa: E402
import teleport  # noqa: E402
from bosses import BOSS_LIST  # noqa: E402
from hk_pipe import get_shared_client  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    mark = "  ok  " if cond else "  FAIL"
    print(f"{mark} {name}" + (f"  | {extra}" if extra != "" else ""), flush=True)
    if not cond:
        FAILS.append(name)


client = get_shared_client()

print("\n[1] Подключение и hello")
check("wait_connected", client.wait_connected(timeout=10.0))
hello = client.wait_hello(timeout=5.0)
check("hello получен", hello is not None, hello)
check("protocol == 3", client.protocol == 3, client.protocol)
check("mod_version == 1.3", client.mod_version == "1.3", client.mod_version)
check("hello-событие доступно", (client.hello or {}).get("status") == "pipe_hello")

# Страховка от самого дорогого промаха стенда: подключиться к пайпу живой игры
# (например, если макет не смог занять имя) и начать телепортировать героя.
# Мод такого поля не шлёт, поэтому дальше идти нельзя.
if (hello or {}).get("server") != "hkpipesim":
    print("\n[СТОП] На пайпе не макет, а что-то другое — похоже, запущенный мод.")
    print("       Тест управлял бы настоящей игрой, поэтому останавливаюсь.")
    print("       Проверь HK_PIPE_NAME: макет слушает hk_ai_mod_sim.")
    sys.exit(2)

print("\n[2] Поток телеметрии")
deadline = time.time() + 5.0
while time.time() < deadline and not (client.get_telemetry() or {}).get("scene"):
    time.sleep(0.05)
t = client.get_telemetry() or {}
check("status == fight", t.get("status") == "fight", t.get("status"))
check("поле scene есть", "scene" in t, t.get("scene"))
check("hp == 9 и boss_hp == 40", t.get("hp") == 9 and t.get("boss_hp") == 40)

seq0 = client.get_seq()
time.sleep(0.3)
check("seq растёт", client.get_seq() > seq0, f"{seq0} -> {client.get_seq()}")
s1 = client.get_seq()
s2 = client.wait_for_fresh(s1, timeout=1.0)
check("wait_for_fresh отдаёт новый seq", s2 != s1, f"{s1} -> {s2}")

print("\n[3] Команда bosses -> событие boss_list")
data = bosses.fetch_boss_list(timeout=5.0)
check("boss_list получен", data is not None and data.get("status") == "boss_list")
if data:
    scenes_mod = [b["scene"] for b in data["bosses"]]
    scenes_py = [s for s, _ in BOSS_LIST]
    check("count совпадает", data.get("count") == len(BOSS_LIST),
          f"{data.get('count')} vs {len(BOSS_LIST)}")
    check("списки сцен идентичны", scenes_mod == scenes_py)

print("\n[4] teleport.check_mod / verify_registry")
check("check_mod()", teleport.check_mod() is True)
check("verify_registry()", teleport.verify_registry() is True)

print("\n[5] Команда boss <сцена> -> boss_selected + бой")
before = client.get_status_seq("boss_selected")
check("request_boss отправлен", bosses.request_boss("GG_Hornet_1"))
ev = client.wait_for_status("boss_selected", timeout=5.0, after_seq=before)
check("boss_selected пришёл", ev is not None, ev)
check("сцена в событии", (ev or {}).get("scene") == "GG_Hornet_1")
check("wait_for_scene(GG_Hornet_1)", bosses.wait_for_scene("GG_Hornet_1", timeout=5.0))
check("current_scene()", bosses.current_scene() == "GG_Hornet_1", bosses.current_scene())

print("\n[6] Неизвестный босс -> command_error")
before = client.get_status_seq("command_error")
bosses.request_boss("No_Such_Boss_Query")
ev = client.wait_for_status("command_error", timeout=5.0, after_seq=before)
check("command_error пришёл", ev is not None, ev)
check("событие не подменило телеметрию",
      (client.get_telemetry() or {}).get("event") is None
      and (client.get_telemetry() or {}).get("status") == "fight",
      client.get_telemetry())
check("get_last_message() не пуст", client.get_last_message() is not None)
check("после ошибки сцена не сменилась", bosses.current_scene() == "GG_Hornet_1",
      bosses.current_scene())

print("\n[6b] Незнакомая сцена gg_* -> передаётся как есть (правило 7 мода)")
before = client.get_status_seq("boss_selected")
check("request_boss(gg_*) отправлен", bosses.request_boss("GG_No_Such_Boss_Scene"))
ev = client.wait_for_status("boss_selected", timeout=5.0, after_seq=before)
check("мод попытался загрузить сцену, а не ответил ошибкой",
      (ev or {}).get("scene") == "GG_No_Such_Boss_Scene", ev)
bosses.request_boss("GG_Hornet_1")   # вернуть стенд в известное состояние
time.sleep(0.3)

print("\n[7] Рестарт боя")
check("request_restart отправлен", bosses.request_restart())
check("wait_for_scene после рестарта", bosses.wait_for_scene("GG_Hornet_1", timeout=5.0))
check("restart_pending вернулся в 0",
      int((client.get_telemetry() or {}).get("restart_pending", 0)) == 0)

print("\n[8] set_boss / set_gate")
check("set_boss_scene", bosses.set_boss_scene("GG_Mantis_Lords"))
check("set_gate", bosses.set_gate("door2"))

print("\n[9] teleport.teleport_to по алиасу")
ok, scene = teleport.teleport_to("nkg", timeout=8.0)
check("teleport_to('nkg') -> GG_Grimm_Nightmare",
      ok is True and scene == "GG_Grimm_Nightmare", (ok, scene))

print("\n[10] Варп")
check("request_warp отправлен", bosses.request_warp())

print("\n[11] Хелперы bosses")
check("mod_has_scene_field()", bosses.mod_has_scene_field() is True)
check("mod_protocol() == 3", bosses.mod_protocol() == 3)
check("is_connected()", bosses.is_connected(timeout=2.0) is True)

print()
if FAILS:
    print(f"ПРОВАЛЕНО ПРОВЕРОК: {len(FAILS)}")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("ВСЕ ПРОВЕРКИ ПРОШЛИ")
