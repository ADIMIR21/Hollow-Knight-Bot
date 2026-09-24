"""Телепорт к боссам пантеона (Godhome) — выбор босса для тренировки/обучения.

Требует мод HK_AI_Mod v1.2+ в игре (права на команды boss/teleport/bosses/warp).

Использование:
  python teleport.py                  — интерактивное меню выбора босса
  python teleport.py --list           — показать список боссов и выйти
  python teleport.py --boss hornet    — телепорт к боссу по имени/номеру/алиасу
  python teleport.py --boss 8         — телепорт к боссу по номеру списка
  python teleport.py --restart        — рестарт боя в текущей сцене
  python teleport.py --warp           — вернуть героя к гейту арены

Интерактивные команды в меню:
  <номер/имя/алиас>  телепорт к боссу (например: 5, hornet, nkg, false_knight)
  r                  рестарт текущего боя
  w                  варп к гейту арены (если герой вылетел из боя)
  list               показать список боссов
  q                  выход
"""

import argparse

from bosses import (
    BOSS_LIST,
    current_scene,
    mod_has_scene_field,
    read_telemetry,
    request_boss,
    request_restart,
    request_warp,
    resolve_query,
    set_boss_scene,
    wait_for_scene,
)


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
    data = read_telemetry()
    if data is None:
        print("[ТЕЛЕПОРТ] Телеметрии нет — игра с модом HK_AI_Mod не запущена?")
        print("           Запусти игру (мод пишет %TEMP%/hk_ai_data.json) и попробуй снова.")
        return False
    if not mod_has_scene_field():
        print("[ТЕЛЕПОРТ] В телеметрии нет поля 'scene' — стоит старый мод (v1.1).")
        print("           Обнови DLL до v1.2: dotnet build Mod/HK_AI_Mod/HK_AI_Mod.csproj -c Release")
        print("           и скопируй bin/Release/net472/HK_AI_Mod.dll в папку Mods.")
        return False
    return True


def teleport_to(query, timeout=45.0):
    """Телепорт к боссу по запросу и ожидание начала боя."""
    resolved = resolve_query(query)
    if resolved is None:
        print(f"[ТЕЛЕПОРТ] Босс не распознан: {query!r}. Смотри: python teleport.py --list")
        return False

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
              "у некоторых сцен вход может отличаться от door1 (задай его: hk_ai_gate.txt).")
    return ok


def legacy_fallback(query):
    """Фолбэк для старого мода v1.1: пишем сцену в конфиг и шлём restart."""
    resolved = resolve_query(query)
    if resolved is None:
        print(f"[ТЕЛЕПОРТ] Босс не распознан: {query!r}.")
        return False
    scene, label = resolved
    print(f"[ТЕЛЕПОРТ] Старый мод v1.1: задаю сцену {scene} и рестарт.")
    set_boss_scene(scene)
    request_restart()
    ok = wait_for_scene(scene, timeout=45.0)
    print("[ТЕЛЕПОРТ] Готово!" if ok else "[ТЕЛЕПОРТ] Бой не поднялся.")
    return ok


def interactive_loop():
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
        if low == "list":
            print_boss_list()
            continue
        if low == "r":
            request_restart()
            if wait_for_scene(current_scene(), timeout=30.0):
                print("[ТЕЛЕПОРТ] Бой перезапущен.")
            else:
                print("[ТЕЛЕПОРТ] Бой не перезапустился.")
            continue
        if low == "w":
            request_warp()
            continue
        if low in ("l", "ls", "--list"):
            print_boss_list()
            continue

        teleport_to(query)
        print()


def main():
    parser = argparse.ArgumentParser(description="Телепорт к боссам пантеона Hollow Knight")
    parser.add_argument("--boss", metavar="ИМЯ", help="номер/имя сцены/алиас босса")
    parser.add_argument("--list", action="store_true", help="показать список боссов")
    parser.add_argument("--restart", action="store_true", help="рестарт боя в текущей сцене")
    parser.add_argument("--warp", action="store_true", help="варп героя к гейту арены")
    args = parser.parse_args()

    if args.list:
        print_boss_list()
        return

    have_mod = check_mod()

    if args.restart:
        request_restart()
        return
    if args.warp:
        request_warp()
        return
    if args.boss:
        if have_mod:
            teleport_to(args.boss)
        else:
            legacy_fallback(args.boss)
        return

    # Интерактивное меню
    print_boss_list()
    if have_mod:
        interactive_loop()
        return

    print("[ТЕЛЕПОРТ] Интерактивный режим требует мод v1.2 и запущенную игру.")
    print("           Можно попробовать фолбэк для старого мода v1.1.")
    try:
        query = input("boss (фолбэк v1.1)> ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if query and query.lower() not in ("q", "quit"):
        legacy_fallback(query)


if __name__ == "__main__":
    main()
