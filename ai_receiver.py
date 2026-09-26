import time
import json
import os
import sys
import tempfile

FILE_PATH = os.path.join(tempfile.gettempdir(), "hk_ai_data.json")

print(f"Watching file: {FILE_PATH}")
print("Press Ctrl + C to exit\n")

attempt = 0
clear = 0 
old_content = None

try:
    while True:
        attempt += 1
        
        if not os.path.exists(FILE_PATH):
            print(f"[{attempt}] ERROR: The file was never created by the game.")
            clear += 1
            if clear == 20:
                os.system('cls' if os.name == 'nt' else 'clear')
                print("Clearing the terminal")
                print(f"Watching file: {FILE_PATH}")
                print("Press Ctrl + C to exit\n")
                clear = 0 
                attempt = 0
            time.sleep(0.5)
            continue

        try:
            with open(FILE_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
                if old_content == content:
                    time.sleep(0.05)
                    attempt -= 1
                    continue
                old_content = content
            
            if not content.strip():
                print(f"[{attempt}] File is empty, most likely (caught it during the C# rewrite)")
                time.sleep(0.02)
                continue

            try:
                data = json.loads(content)
                if "status" in data:
                    print(f"[{attempt}] (Status) -> {data['status']}")
                else:
                    print(f"[{attempt}] (Position) -> HP: {data.get('hp')} | X: {data.get('x')}, Y: {data.get('y')}")
            
            except json.JSONDecodeError:
                print(f"[{attempt}] managed to pull the text out: {content}")

        except PermissionError:
            print(f"[{attempt}] File is locked by the game")
            time.sleep(0.01) 

        time.sleep(0.05)

except KeyboardInterrupt:
    print("\nStopping.")
    sys.exit(0)