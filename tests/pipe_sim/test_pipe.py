# -*- coding: utf-8 -*-
"""Integration test for the Python named-pipe client against the mod's mock server.

Covers the transport (hk_pipe), the command protocol (bosses) and the teleport
logic (teleport) without a running game. Run from the repository root.
"""
import os
import sys
import time

# The harness listens on its own pipe name: without this the test would connect to
# the pipe of a running game and start driving the mod instead of the mock (that is
# exactly what happened once — the test went off to teleport the knight into
# GG_No_Such_Boss_Scene).
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

print("\n[1] Connection and hello")
check("wait_connected", client.wait_connected(timeout=10.0))
hello = client.wait_hello(timeout=5.0)
check("hello received", hello is not None, hello)
check("protocol == 3", client.protocol == 3, client.protocol)
check("mod_version == 1", client.mod_version == "1", client.mod_version)
check("hello event available", (client.hello or {}).get("status") == "pipe_hello")

# Guard against the harness's most expensive mistake: connecting to the pipe of a
# live game (for example, if the mock failed to claim the name) and starting to
# teleport the knight. The mod does not send such a field, so we must not go on.
if (hello or {}).get("server") != "hkpipesim":
    print("\n[STOP] The pipe is not the mock but something else — looks like a running mod.")
    print("       The test would be driving a real game, so I am stopping here.")
    print("       Check HK_PIPE_NAME: the mock listens on hk_ai_mod_sim.")
    sys.exit(2)

print("\n[2] Telemetry stream")
deadline = time.time() + 5.0
while time.time() < deadline and not (client.get_telemetry() or {}).get("scene"):
    time.sleep(0.05)
t = client.get_telemetry() or {}
check("status == fight", t.get("status") == "fight", t.get("status"))
check("scene field present", "scene" in t, t.get("scene"))
check("hp == 9 and boss_hp == 40", t.get("hp") == 9 and t.get("boss_hp") == 40)
check("arena reported as a whole",
      t.get("arena_bosses") == 1 and t.get("arena_alive") == 1
      and t.get("arena_hp") == 40 and t.get("arena_detail") == "MockBoss:40:0",
      f"{t.get('arena_hp')} HP, {t.get('arena_detail')}")
check("the scene's pools and the damage counter are reported",
      t.get("scene_count") == 1 and t.get("scene_hp") == 40 and t.get("scene_damage_total") == 0,
      f"{t.get('scene_hp')} HP over {t.get('scene_count')}, damage {t.get('scene_damage_total')}")

seq0 = client.get_seq()
time.sleep(0.3)
check("seq grows", client.get_seq() > seq0, f"{seq0} -> {client.get_seq()}")
s1 = client.get_seq()
s2 = client.wait_for_fresh(s1, timeout=1.0)
check("wait_for_fresh returns a new seq", s2 != s1, f"{s1} -> {s2}")

print("\n[3] Command bosses -> boss_list event")
data = bosses.fetch_boss_list(timeout=5.0)
check("boss_list received", data is not None and data.get("status") == "boss_list")
if data:
    scenes_mod = [b["scene"] for b in data["bosses"]]
    scenes_py = [s for s, _ in BOSS_LIST]
    check("count matches", data.get("count") == len(BOSS_LIST),
          f"{data.get('count')} vs {len(BOSS_LIST)}")
    check("scene lists are identical", scenes_mod == scenes_py)

print("\n[4] teleport.check_mod / verify_registry")
check("check_mod()", teleport.check_mod() is True)
check("verify_registry()", teleport.verify_registry() is True)

print("\n[5] Command boss <scene> -> boss_selected + fight")
before = client.get_status_seq("boss_selected")
check("request_boss sent", bosses.request_boss("GG_Hornet_1"))
ev = client.wait_for_status("boss_selected", timeout=5.0, after_seq=before)
check("boss_selected received", ev is not None, ev)
check("scene in event", (ev or {}).get("scene") == "GG_Hornet_1")
check("wait_for_scene(GG_Hornet_1)", bosses.wait_for_scene("GG_Hornet_1", timeout=5.0))
check("current_scene()", bosses.current_scene() == "GG_Hornet_1", bosses.current_scene())

print("\n[6] Unknown boss -> command_error")
before = client.get_status_seq("command_error")
bosses.request_boss("No_Such_Boss_Query")
ev = client.wait_for_status("command_error", timeout=5.0, after_seq=before)
check("command_error received", ev is not None, ev)
check("event did not replace telemetry",
      (client.get_telemetry() or {}).get("event") is None
      and (client.get_telemetry() or {}).get("status") == "fight",
      client.get_telemetry())
check("get_last_message() is not empty", client.get_last_message() is not None)
check("scene unchanged after the error", bosses.current_scene() == "GG_Hornet_1",
      bosses.current_scene())

print("\n[6b] Unfamiliar gg_* scene -> passed through as-is (mod rule 7)")
before = client.get_status_seq("boss_selected")
check("request_boss(gg_*) sent", bosses.request_boss("GG_No_Such_Boss_Scene"))
ev = client.wait_for_status("boss_selected", timeout=5.0, after_seq=before)
check("mod attempted to load the scene instead of answering with an error",
      (ev or {}).get("scene") == "GG_No_Such_Boss_Scene", ev)
bosses.request_boss("GG_Hornet_1")   # return the harness to a known state
time.sleep(0.3)

print("\n[7] Fight restart")
check("request_restart sent", bosses.request_restart())
check("wait_for_scene after restart", bosses.wait_for_scene("GG_Hornet_1", timeout=5.0))
check("restart_pending is back to 0",
      int((client.get_telemetry() or {}).get("restart_pending", 0)) == 0)

print("\n[8] set_boss / set_gate")
check("set_boss_scene", bosses.set_boss_scene("GG_Mantis_Lords"))
check("set_gate", bosses.set_gate("door2"))

print("\n[9] teleport.teleport_to by alias")
ok, scene = teleport.teleport_to("nkg", timeout=8.0)
check("teleport_to('nkg') -> GG_Grimm_Nightmare",
      ok is True and scene == "GG_Grimm_Nightmare", (ok, scene))

print("\n[10] Warp")
check("request_warp sent", bosses.request_warp())

print("\n[11] bosses helpers")
check("mod_has_scene_field()", bosses.mod_has_scene_field() is True)
check("mod_protocol() == 3", bosses.mod_protocol() == 3)
check("is_connected()", bosses.is_connected(timeout=2.0) is True)

print("\n[12] pause / resume (Update 9)")
before = client.get_status_seq("paused")
check("pause sent", bosses.send_command("pause"))
ev = client.wait_for_status("paused", timeout=5.0, after_seq=before)
check("paused event received", ev is not None, ev)
check("paused event reports paused=1", (ev or {}).get("paused") == 1, ev)
check("paused event reports time_scale 0", (ev or {}).get("time_scale") == 0.0, ev)
check("the event did not replace telemetry",
      (client.get_telemetry() or {}).get("event") is None, client.get_telemetry())
time.sleep(0.3)
check("telemetry reports paused=1",
      int((client.get_telemetry() or {}).get("paused", 0)) == 1,
      (client.get_telemetry() or {}).get("paused"))

# A second pause is not an error: the command is idempotent.
before = client.get_status_seq("paused")
check("second pause sent", bosses.send_command("pause"))
ev = client.wait_for_status("paused", timeout=5.0, after_seq=before)
check("still paused after a second pause", (ev or {}).get("paused") == 1, ev)

before = client.get_status_seq("resumed")
check("resume sent", bosses.send_command("resume"))
ev = client.wait_for_status("resumed", timeout=5.0, after_seq=before)
check("resumed event received", ev is not None, ev)
check("resumed event reports paused=0", (ev or {}).get("paused") == 0, ev)
check("resumed event reports time_scale 1", (ev or {}).get("time_scale") == 1.0, ev)
time.sleep(0.3)
check("telemetry reports paused=0",
      int((client.get_telemetry() or {}).get("paused", 1)) == 0,
      (client.get_telemetry() or {}).get("paused"))

# resume without a pause: a fresh trainer may clear a pause left by a dead one.
before = client.get_status_seq("resumed")
check("resume without a pause sent", bosses.send_command("resume"))
ev = client.wait_for_status("resumed", timeout=5.0, after_seq=before)
check("an idle resume still reports paused=0", (ev or {}).get("paused") == 0, ev)
check("telemetry still reports paused=0",
      int((client.get_telemetry() or {}).get("paused", 1)) == 0)

print()
if FAILS:
    print(f"CHECKS FAILED: {len(FAILS)}")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
