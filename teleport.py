"""Телепорт к боссам пантеона (Godhome) — выбор босса и запуск обучения.

Требует мод HK_AI_Mod v1.3+ в игре: команды boss/teleport/bosses/warp идут
через именованный пайп ``\\\\.\\pipe\\hk_ai_mod``. Файловый протокол %TEMP%
удалён, фолбэка для старого мода v1.1 больше нет.

Использование:
  python teleport.py                  — интерактивное меню выбора босса;
                                        после телепорта спросит про обучение
  python teleport.py --train          — то же, но обучение запускается сразу
  python teleport.py --list           — показать список боссов и выйти
  python teleport.py --verify         — сверить реестры Python и мода через пайп
  python teleport.py --boss hornet    — телепорт к боссу по имени/номеру/алиасу
  python teleport.py --boss 8         — телепорт к боссу по номеру списка
  python teleport.py --boss nkg --train — телепорт + сразу обучение (train.py)
  python teleport.py --restart        — рестарт текущего боя
  python teleport.py --warp           — вернуть героя к гейту арены

Обучение для каждого босса хранится автоматически:
models/ppo_hk/<сцена>/ — чекпоинты, hk_model_final.zip, vecnormalize.pkl.
Создавать что-то вручную не нужно.

Интерактивные команды в меню:
  <номер/имя/алиас>  телепорт к боссу (например: 5, hornet, nkg, false_knight)
  r                  рестарт текущего боя
  w                  варп к гейту арены (если герой вылетел из боя)
  list               показать список боссов
  q                  выход
"""

import argparse
import os
import subprocess
import sys

from bosses import (
    BOSS_LIST,
    current_scene,
    fetch_boss_list,
    is_connected,
    mod_has_scene_field,
    mod_protocol,
    mod_version,
    request_boss,
    request_restart,
    request_warp,
    resolve_query,
    wait_for_scene,
    wait_hello,
)
from hk_pipe import REQUIRED_PROTOCOL, PIPE_PATH


def print_boss_list():
    print("\n=== Боссы Godhome (пантеоны) ===")
    for i, (scene, label) in enumerate(BOSS_LIST, start=1):
        print(f"  {i:>2}. {label:<28} {scene}")
    print(
        "\nПодсказки: можно вводить номер, имя сцены (gg_hornet_1), "
        "алиас (hornet, nkg, sisters, oro) или часть названия."
    )
    print("Варианты с (Variant) — усложнённые версии боёв для пантеона Халлоунеста.\n")


def check_mod():
    """Проверяет, что пайп мода на связи и его протокол умеет команды пантеона."""
    if not is_connected(timeout=10.0):
        print("[ТЕЛЕПОРТ] Пайп мода не отвечает — игра с модом HK_AI_Mod запущена?")
        print(f"           Мод держит сервер {PIPE_PATH}. Проверь, что игра запущена")
        print("           и HK_AI_Mod.dll лежит в папке Mods.")
        return False

    if wait_hello(timeout=3.0) is None:
        print("[ТЕЛЕПОРТ] Пайп открыт, но hello-сообщение от мода не пришло.")
        print("           Похоже, пайп занят не модом HK_AI_Mod.")
        return False

    proto = mod_protocol()
    if proto < REQUIRED_PROTOCOL:
        print(f"[ТЕЛЕПОРТ] Мод отвечает, но протокол {proto} — команды пантеона "
              f"требуют >= {REQUIRED_PROTOCOL}.")
        print("           Обнови DLL: dotnet build Mod/HK_AI_Mod/HK_AI_Mod.csproj -c Release")
        print("           и скопируй bin/Release/net472/HK_AI_Mod.dll в папку Mods.")
        return False

    if not mod_has_scene_field():
        print("[ТЕЛЕПОРТ] Мод подключён, но поле 'scene' в телеметрии не приходит.")
        return False

    print(f"[ТЕЛЕПОРТ] Мод на связи: v{mod_version()} (протокол {proto}).")
    return True


def verify_registry():
    """Сверяет локальный реестр (bosses.BOSS_LIST) с реестром мода по пайпу."""
    data = fetch_boss_list(timeout=3.0)
    if data is None:
        print("[ПРОВЕРКА] Мод не ответил событием boss_list за 3 секунды.")
        return False

    mod_bosses = data.get("bosses") or []
    mod_scenes = [b.get("scene") for b in mod_bosses]
    local_scenes = [scene for scene, _ in BOSS_LIST]

    print(f"[ПРОВЕРКА] Мод: {data.get('count')} записей, Python: {len(BOSS_LIST)}.")
    print(f"[ПРОВЕРКА] Целевая сцена мода: {data.get('target_scene')}")

    only_local = [s for s in local_scenes if s not in mod_scenes]
    only_mod = [s for s in mod_scenes if s not in local_scenes]
    if only_local:
        print(f"[ПРОВЕРКА] Есть только в Python: {', '.join(only_local)}")
    if only_mod:
        print(f"[ПРОВЕРКА] Есть только в моде: {', '.join(only_mod)}")
    if not only_local and not only_mod:
        print("[ПРОВЕРКА] Реестры совпадают.")
        return True
    return False


def run_train(scene):
    """Запускает обучение (train.py) для выбранного босса в этой же консоли.

    Ctrl+C в train.py корректно сохраняет модель и статистику нормализации
    в models/ppo_hk/<сцена>/.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"\n[ОБУЧЕНИЕ] Старт train.py для {scene}. Ctrl+C — прервать с сохранением.\n")
    try:
        return subprocess.call(
            [sys.executable, os.path.join(script_dir, "train.py"), "--boss", scene],
            cwd=script_dir,
        )
    except KeyboardInterrupt:
        print("\n[ОБУЧЕНИЕ] Прервано (Ctrl+C).")
        return 130


def teleport_to(query, timeout=45.0):
    """Телепорт к боссу по запросу и ожидание начала боя.

    Возвращает (ok, scene) — сцена резолвится локально.
    """
    resolved = resolve_query(query)
    if resolved is None:
        print(f"[ТЕЛЕПОРТ] Босс не распознан: {query!r}. Смотри: python teleport.py --list")
        return False, None

    scene, label = resolved
    before = current_scene()
    print(f"[ТЕЛЕПОРТ] Босс: {label} ({scene})")

    if before == scene:
        # Уже в нужной сцене — мод просто перезагрузит арену (рестарт боя).
        print("[ТЕЛЕПОРТ] Сцена уже активна — рестарт арены.")

    request_boss(scene)

    def progress(current, status):
        if current and current != before:
            print(f"[ТЕЛЕПОРТ] Загрузка: {current} ({status})...")

    ok = wait_for_scene(scene, timeout=timeout, on_progress=progress)
    if ok:
        print(f"[ТЕЛЕПОРТ] Готово: бой на арене {scene} начался!")
    else:
        print("[ТЕЛЕПОРТ] Бой не поднялся за отведённое время.")
        print("           Проверь ModLog (мод пишет активные гейты сцены) — "
              "у некоторых сцен вход отличается от door1, задай его командой")
        print("           set_gate <гейт> или переменной HK_ENTRY_GATE.")
    return ok, scene


# Хабы Godhome без боссов — обучение там не имеет смысла.
NON_TRAIN_SCENES = {"GG_Atrium", "GG_Workshop", "GG_Boss_Door_Entrance"}


def maybe_train(scene, auto_train):
    """Запускает обучение сцены (или спрашивает), кроме хабов без боссов."""
    if not scene:
        return
    if scene in NON_TRAIN_SCENES:
        print(f"[ОБУЧЕНИЕ] {scene} — хаб без босса, обучение не запускаю.")
        print("           Выбери арену с боссом (python teleport.py --list).")
        return
    if auto_train:
        run_train(scene)
        return
    try:
        answer = input("Запустить обучение этого босса? [Y/n]> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer in ("", "y", "yes", "д", "да"):
        run_train(scene)


def interactive_loop(auto_train=False):
    current = current_scene()
    if current:
        print(f"[ТЕЛЕПОРТ] Текущая сцена: {current}")
    print("[ТЕЛЕПОРТ] Введи номер/имя босса, r (рестарт), w (варп к гейту), list, q (выход).\n")

    while True:
        try:
            query = input("boss> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[ТЕЛЕПОРТ] Выход.")
            return

        if not query:
            continue
        low = query.lower()
        if low in ("q", "quit", "exit"):
            print("[ТЕЛЕПОРТ] Выход.")
            return
        if low in ("list", "l", "ls", "--list"):
            print_boss_list()
            continue
        if low == "r":
            print("[ТЕЛЕПОРТ] Рестарт боя...")
            request_restart()
            if wait_for_scene(current_scene(), timeout=30.0):
                print("[ТЕЛЕПОРТ] Бой перезапущен.")
            else:
                print("[ТЕЛЕПОРТ] Бой не перезапустился.")
            continue
        if low == "w":
            print("[ТЕЛЕПОРТ] Варп к гейту арены...")
            request_warp()
            continue

        ok, scene = teleport_to(query)
        if ok:
            maybe_train(scene, auto_train)
        print()


def main():
    parser = argparse.ArgumentParser(description="Телепорт к боссам пантеона Hollow Knight")
    parser.add_argument("--boss", metavar="ИМЯ", help="номер/имя сцены/алиас босса")
    parser.add_argument("--train", action="store_true",
                        help="после телепортации сразу запустить train.py для выбранного босса")
    parser.add_argument("--list", action="store_true", help="показать список боссов")
    parser.add_argument("--verify", action="store_true",
                        help="сверить реестр боссов Python и мода (через пайп)")
    parser.add_argument("--restart", action="store_true", help="рестарт боя в текущей сцене")
    parser.add_argument("--warp", action="store_true", help="варп героя к гейту арены")
    args = parser.parse_args()

    if args.list:
        print_boss_list()
        return

    have_mod = check_mod()

    if args.verify:
        sys.exit(0 if (have_mod and verify_registry()) else 1)

    if args.restart:
        if not have_mod:
            sys.exit(1)
        request_restart()
        print("[ТЕЛЕПОРТ] Команда рестарта отправлена.")
        return

    if args.warp:
        if not have_mod:
            sys.exit(1)
        request_warp()
        print("[ТЕЛЕПОРТ] Команда варпа отправлена.")
        return

    if args.boss:
        if not have_mod:
            sys.exit(1)
        ok, scene = teleport_to(args.boss)
        if ok:
            maybe_train(scene, auto_train=args.train)
        return

    # Интерактивное меню
    print_boss_list()
    if not have_mod:
        print("[ТЕЛЕПОРТ] Интерактивный режим требует запущенной игры с модом.")
        sys.exit(1)
    interactive_loop(auto_train=args.train)


if __name__ == "__main__":
    main()
