import mss
import cv2
import numpy as np
import time
import pygetwindow as gw  

USE_SCREEN_CAPTURE = False
AI_VISION_SIZE = (256, 256)

class ScreenCaptureAgent:
    def __init__(self):
        self.sct = mss.mss()
        self.window_title = "Hollow Knight"
        self.monitor = {'top': 0, 'left': 0, 'width': 1280, 'height': 720}
        
        self.game_was_found = False 
        
        self.update_window_position()

    def update_window_position(self):
        try:
            windows = gw.getWindowsWithTitle(self.window_title)
            
            if windows:
                hk_window = windows[0] 
                if hk_window.width > 100 and hk_window.height > 100:
                    self.monitor['left'] = hk_window.left + 8
                    self.monitor['top'] = hk_window.top + 31
                    self.monitor['width'] = hk_window.width - 16
                    self.monitor['height'] = hk_window.height - 39
                    
                    if not self.game_was_found:
                        print("[VISION] Игра Hollow Knight успешно зафиксирована!")
                        self.game_was_found = True
                    return True
            else:
                if self.game_was_found:
                    print("[VISION] Окно игры потеряно. Ожидание запуска...")
                    self.game_was_found = False
                    
        except Exception as e:
            pass
        return False

    def get_state_frame(self):
        self.update_window_position()
        
        try:
            if not self.game_was_found:
                return np.zeros(AI_VISION_SIZE, dtype=np.uint8)
                
            img = self.sct.grab(self.monitor)
            frame = np.array(img)
            
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            ai_frame = cv2.resize(frame_gray, AI_VISION_SIZE)
            return ai_frame
            
        except Exception as e:
            return np.zeros(AI_VISION_SIZE, dtype=np.uint8)

def main():
    agent = ScreenCaptureAgent()
    
    window_name = "AI Brain View"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, AI_VISION_SIZE[0], AI_VISION_SIZE[1])

    print("[СИСТЕМА] Автонаведение запущено! Ожидаем Hollow Knight...")
    print("Нажми 'q' в окошке ИИ для выхода.")
    
    last_time = time.time()
    
    while True:
        ai_frame = agent.get_state_frame()
        
        current_time = time.time()
        fps = 1 / (current_time - last_time) if (current_time - last_time) > 0 else 0
        last_time = current_time
        
        cv2.putText(ai_frame, f"FPS: {int(fps)}", (10, 20), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,), 1)

        cv2.imshow(window_name, ai_frame)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    cv2.destroyAllWindows()
    print("[СИСТЕМА] Зрение отключено.")

if __name__ == "__main__":
    main()