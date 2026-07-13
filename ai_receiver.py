import time
import json
import os
import sys
import tempfile

FILE_PATH = os.path.join(tempfile.gettempdir(), "hk_ai_data.json")


print(f"Следим за файлом: {FILE_PATH}")
print("Для выхода нажмите Ctrl + C\n")

attempt = 0
clear = 0 
old_content = None

try:
    while True:
        attempt += 1
        
        if not os.path.exists(FILE_PATH):
            print(f"[{attempt}] ОШИБКА: Файл вообще не создан игрой.")
            clear += 1
            if clear == 20:
                os.system('cls' if os.name == 'nt' else 'clear')
                print("Очистка терминала")
                print(f"Следим за файлом: {FILE_PATH}")
                print("Для выхода нажмите Ctrl + C\n")
                clear = 0 
                attempt = 0
            time.sleep(0.5)
            continue

        try:
            # Читаем файл
            with open(FILE_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
                if old_content == content:
                    time.sleep(0.05)
                    attempt -= 1
                    continue
                old_content = content
            
            # Если пустой файл
            if not content.strip():
                print(f"[{attempt}] Файл пустой, скорее всего (попали в момент перезаписи C#)")
                time.sleep(0.02)
                continue

            try:
                data = json.loads(content)
                if "status" in data:
                    print(f"[{attempt}] (Статус) -> {data['status']}")
                else:
                    print(f"[{attempt}] (Координаты) -> ХП: {data.get('hp')} | X: {data.get('x')}, Y: {data.get('y')}")
            
            except json.JSONDecodeError:
                print(f"[{attempt}] текст успели вытащить: {content}")

        except PermissionError:

            print(f"[{attempt}] Файл занят игрой")
            time.sleep(0.01) 

        time.sleep(0.05)

except KeyboardInterrupt:
    print("\nОстнавливаю.")
    sys.exit(0)