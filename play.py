import time
import cv2
import numpy as np
from stable_baselines3 import PPO
from hk_gym import HollowKnightGym

MODELS_DIR = "models/ppo_hk"
LOAD_MODEL_NAME = "hk_model_final"

def main():
    print("Создание среды ХК...")
    env = HollowKnightGym()

    model_path = f"{MODELS_DIR}/{LOAD_MODEL_NAME}.zip"

    print(f"\n[СИСТЕМА] Загружаю модель: {LOAD_MODEL_NAME}...")
    try:
        model = PPO.load(model_path, env=env)
        print("[СИСТЕМА] Модель успешно загружена.")
    except Exception as e:
        print(f"[СИСТЕМА] Ошибка загрузки модели: {e}")
        print("[СИСТЕМА] Убедись, что файл существует и совместим с текущей версией.")
        return

    obs, _ = env.reset()
    total_reward = 0.0
    step = 0

    print("\n[СИСТЕМА] ИИ запущен в режиме демонстрации.")
    print("Нажми 'q' в окне AI Vision для выхода.\n")

    try:
        while True:
            action, _states = model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, _ = env.step(action)
            
            total_reward += reward
            step += 1
            
            if terminated or truncated:
                print(f"[ЭПИЗОД] Шагов: {step} | Награда: {total_reward:.1f}")
                obs, _ = env.reset()
                total_reward = 0.0
                step = 0
                
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
    except KeyboardInterrupt:
        print("\n[СИСТЕМА] Остановка...")
    
    cv2.destroyAllWindows()
    print("[СИСТЕМА] Работа завершена.")

if __name__ == "__main__":
    main()