# -*- coding: utf-8 -*-
"""Отладчик телеметрии мода HK_AI_Mod через именованный пайп.

Подключается к \\\\.\\pipe\\hk_ai_mod как второй клиент (мод держит до 4
инстансов, поэтому может работать параллельно с тренировкой) и печатает
каждое новое сообщение мода.
"""
import json
import os
import sys
import time

from hk_pipe import HKPipeClient

PIPE_PATH = r"\\.\pipe\hk_ai_mod"

print(f"Подключаюсь к пайпу: {PIPE_PATH}")
print("Для выхода нажмите Ctrl + C\n")

client = HKPipeClient(verbose=False)

if not client.wait_connected(timeout=30.0):
    print("ОШИБКА: мод не ответил за 30 секунд.")
    print("Проверь: игра запущена? HK_AI_Mod.dll установлен в Mods? Другая программа не заняла пайп?")
    sys.exit(1)

print("Подключено к моду! Читаю телеметрию...\n")

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
                # Одноразовое событие (boss_list / boss_selected / command_error).
                # В наблюдения RL оно не попадает, поэтому печатаем его здесь.
                print(f"[{attempt}] (Событие) {status} -> "
                      f"{json.dumps(data, ensure_ascii=False)}")
            elif status == "fight":
                print(f"[{attempt}] HP: {data.get('hp')}/{data.get('max_hp')} | "
                      f"Душа: {data.get('mana')} | Босс: {data.get('boss_hp')} HP | "
                      f"X: {data.get('x')}, Y: {data.get('y')} | "
                      f"Босс атакует: {data.get('boss_is_attacking')} | "
                      f"restart_pending: {data.get('restart_pending')}")
            else:
                print(f"[{attempt}] (Статус) -> {status}")

            clear += 1
            if clear == 200:
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"Подключено к {PIPE_PATH}. Для выхода Ctrl+C\n")
                clear = 0
                attempt = 0
        else:
            # Новых сообщений нет (меню/пауза/мод молчит) — короткий сон.
            time.sleep(0.01)

except KeyboardInterrupt:
    print("\nОстанавливаю.")
    client.stop()
    sys.exit(0)
