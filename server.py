# server.py

import asyncio
import websockets
import json
from Control import Control  # Control.pyからControlクラスをインポート

# --- サーボの初期化と設定 ---
# Controlクラスを使って、それぞれのサーボのインスタンス（実体）を作成
servoRight = Control(physical_id=7, name="Right Servo")
servoLeft = Control(physical_id=5, name="Left Servo")

# アプリIDと、操作するサーボのインスタンスを対応付ける
APP_ID_TO_SERVO_INSTANCE = {
    1: servoRight,
    2: servoLeft,
}

# --- 角度や動作の基本設定 ---
MIN_ANGLE = -60
MAX_ANGLE = 60
# アプリIDをキーとして現在の角度を保持
current_angles = {1: 0, 2: 0} 
STEP = 1.0
UPDATE_INTERVAL = 0.02

# --- サーバーの状態管理 ---
movement_states = {}  # 例: {1: "up", 2: "stop"}

def move_servo_by_app_id(app_id, angle):
    """
    アプリIDに基づいて、対応するサーボを動かす関数
    """
    servo_instance = APP_ID_TO_SERVO_INSTANCE.get(app_id)
    if servo_instance:
        # 実際のサーボの目標角度を計算（-1をかけるなど、ハードウェアに合わせる）
        target_angle = -1 * angle
        target_angle = max(MIN_ANGLE, min(target_angle, MAX_ANGLE))
        
        servo_instance.move(target_angle)
        current_angles[app_id] = angle

async def servo_loop():
    """サーボを連続的に動かすループ"""
    while True:
        for app_id, direction in list(movement_states.items()):
            if direction != "stop":
                angle = current_angles.get(app_id, 0)
                
                if direction == "up":
                    angle += STEP
                elif direction == "down":
                    angle -= STEP
                
                angle = max(-60, min(angle, 60))
                move_servo_by_app_id(app_id, angle)

        await asyncio.sleep(UPDATE_INTERVAL)

async def handler(websocket, path):
    """クライアントからの指示を受け取るハンドラ"""
    client_app_id = None
    print("クライアントが接続しました。")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
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
    """サーバーを起動するメイン関数"""
    print("サーバーを起動します...")
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