# -*- coding: utf-8 -*-
"""Real-time debugger for the AiTrainHK telemetry over the named pipe.

Connects to \\\\.\\pipe\\hk_ai_mod as a second client (the mod keeps up to 4
instances, so it can run in parallel with training) and prints
every new message from the mod.
"""
import json
import os
import sys
import time

from hk_pipe import HKPipeClient, PIPE_PATH

print(f"Connecting to the pipe: {PIPE_PATH}")
print("Press Ctrl + C to exit\n")

client = HKPipeClient(verbose=False)

if not client.wait_connected(timeout=30.0):
    print("ERROR: the mod did not respond within 30 seconds.")
    print("Check: is the game running? Is AiTrainHK.dll installed in Mods? Has another program taken the pipe?")
    sys.exit(1)

print("Connected to the mod! Reading telemetry...\n")

attempt = 0
last_seq = -1
clear = 0

try:
    while True:
        seq = client.get_seq()
        if seq != last_seq:
            last_seq = seq
            attempt += 1
            data = client.get_last_message() or {}

            status = data.get("status")
            if data.get("event"):
                # A one-shot event (boss_list / boss_selected / command_error).
                # It does not reach the RL observations, so we print it here.
                print(f"[{attempt}] (Event) {status} -> "
                      f"{json.dumps(data, ensure_ascii=False)}")
            elif status == "fight":
                print(f"[{attempt}] HP: {data.get('hp')}/{data.get('max_hp')} | "
                      f"Soul: {data.get('mana')} | Boss: {data.get('boss_hp')} HP | "
                      f"X: {data.get('x')}, Y: {data.get('y')} | "
                      f"Boss attacking: {data.get('boss_is_attacking')} | "
                      f"restart_pending: {data.get('restart_pending')}")
            else:
                print(f"[{attempt}] (Status) -> {status}")

            clear += 1
            if clear == 200:
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"Connected to {PIPE_PATH}. Press Ctrl+C to exit\n")
                clear = 0
                attempt = 0
        else:
            # No new messages (menu/pause/the mod is silent) — a short sleep.
            time.sleep(0.01)

except KeyboardInterrupt:
    print("\nStopping.")
    client.stop()
    sys.exit(0)
