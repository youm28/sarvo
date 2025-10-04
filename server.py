# server.py (サーボ2台対応版)

import asyncio
import websockets
import json
from Control import Control

# --- サーボの初期化と設定 ---
# 1. 右サーボと左サーボの両方を準備
servoRight = Control(physical_id=7, name="Right Servo")
servoLeft = Control(physical_id=5, name="Left Servo") # ID:5の左サーボを追加

# 2. アプリIDとサーボの対応付けを拡張
APP_ID_TO_SERVO_INSTANCE = {
    1: servoRight, # App 1 は 右サーボ
    2: servoLeft,  # App 2 は 左サーボ
}

# --- 角度や動作の基本設定 ---
MIN_ANGLE = -60
MAX_ANGLE = 60
# 3. 角度の保持をIDごとに分ける
current_angles = {
    1: 0, # App 1 の現在の角度
    2: 0, # App 2 の現在の角度
}
STEP = 1.0  # 1回の更新で動かす角度
UPDATE_INTERVAL = 0.01

# --- サーバーの状態管理 ---
movement_states = {}

def move_servo_by_app_id(app_id, angle):
    servo_instance = APP_ID_TO_SERVO_INSTANCE.get(app_id)
    if servo_instance:
        target_angle = angle
        target_angle = max(MIN_ANGLE, min(target_angle, MAX_ANGLE))
        
        servo_instance.move(target_angle)
        current_angles[app_id] = angle

async def servo_loop():
    while True:
        # movement_statesをコピーして処理することで、ループ中の変更に対応
        for app_id, direction in list(movement_states.items()):
            if direction != "stop":
                angle = current_angles.get(app_id, 0)
                
                if direction == "right":
                    angle -= STEP
                elif direction == "left":
                    angle += STEP
                
                angle = max(-60, min(angle, 60))
                move_servo_by_app_id(app_id, angle)

        await asyncio.sleep(UPDATE_INTERVAL)

async def handler(websocket):
    client_app_id = None
    print("クライアントが接続しました。")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                print(f"受信データ: {data}")
                command = data.get("command")
                app_id = data.get("app_id")

                if app_id not in APP_ID_TO_SERVO_INSTANCE:
                    print(f"無効なアプリID: {app_id}")
                    continue
                
                client_app_id = app_id

                if command and command.startswith("start_"):
                    direction = command.split("_")[1]
                    movement_states[app_id] = direction
                elif command == "stop":
                    movement_states[app_id] = "stop"

            except Exception as e:
                print(f"処理エラー: {e}")

    except websockets.exceptions.ConnectionClosed:
        print(f"クライアント (App ID: {client_app_id}) との接続が切れました。")
    finally:
        if client_app_id:
            movement_states[client_app_id] = "stop"
            print(f"App ID: {client_app_id} の動作を停止しました。")

async def main():
    print("サーバーを起動します...")
    # 4. 両方のサーボを初期位置(0度)に設定
    move_servo_by_app_id(1, 0)
    move_servo_by_app_id(2, 0)
    
    loop = asyncio.get_running_loop()
    loop.create_task(servo_loop())
    
    async with websockets.serve(handler, "0.0.0.0", 5000):
        print("WebSocketサーバーが起動しました (ws://<あなたのIPアドレス>:5000)")
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("サーバーを停止します。")