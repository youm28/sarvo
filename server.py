# server.py (サーボ1台版)

import asyncio
import websockets
import json
from Control import Control

# --- サーボの初期化と設定 ---
# 右サーボ(ID:7)のみを作成します
servoRight = Control(physical_id=7, name="Right Servo")

# アプリIDとサーボの対応付けもapp1のみにします
APP_ID_TO_SERVO_INSTANCE = {
    1: servoRight,
}

# --- 角度や動作の基本設定 ---
MIN_ANGLE = -60
MAX_ANGLE = 60
# 角度の保持もapp1のみ
current_angles = {1: 0} 
STEP = 1.0
UPDATE_INTERVAL = 0.02

# --- サーバーの状態管理 ---
movement_states = {}

def move_servo_by_app_id(app_id, angle):
    """
    アプリIDに基づいて、対応するサーボを動かす関数
    """
    servo_instance = APP_ID_TO_SERVO_INSTANCE.get(app_id)
    if servo_instance:
        # Webからの角度は反転させずにそのまま使うことが多いので、
        # -1倍するかどうかは実際の動きを見て調整してください。
        target_angle = angle 
        target_angle = max(MIN_ANGLE, min(target_angle, MAX_ANGLE))
        
        servo_instance.move(target_angle)
        current_angles[app_id] = angle

async def servo_loop():
    """サーボを連続的に動かすループ"""
    while True:
        for app_id, direction in list(movement_states.items()):
            if direction != "stop":
                angle = current_angles.get(app_id, 0)
                
                if direction == "right":
                    angle += STEP
                elif direction == "left":
                    angle -= STEP
                
                angle = max(-60, min(angle, 60))
                print(f"   -> Servo Loop: App ID {app_id} を {direction}方向に動かします。新しい角度: {angle}")
                move_servo_by_app_id(app_id, angle)

        await asyncio.sleep(UPDATE_INTERVAL)

async def handler(websocket):
    """クライアントからの指示を受け取るハンドラ"""
    client_app_id = None
    print("クライアントが接続しました。")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                print(f"受信データ: {data}") # 受信データをログに出力
                command = data.get("command")
                app_id = data.get("app_id")

                if app_id not in APP_ID_TO_SERVO_INSTANCE:
                    print(f"無効なアプリID: {app_id} (現在app1のみ受付)")
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
    # サーボの初期位置設定もapp1のみ
    move_servo_by_app_id(1, 0)
    
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
