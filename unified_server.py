import asyncio
import json
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# Control.py から Control クラスをインポート
# unified_server.py と Control.py は同じフォルダにある必要があります
from Control import Control

# --- FastAPIアプリケーションのインスタンスを作成 ---
app = FastAPI()

# =================================================================
# Section 1: Kachaka ロボット制御関連のコード
# =================================================================

# --- この部分は実際のKachaka gRPCライブラリに置き換えます ---
# デモ用のモック（偽の）ロボットクラス
class MockKachakaRobot:
    def __init__(self):
        self.is_busy = False

    async def move_to_location(self, location_id):
        self.is_busy = True
        print(f"🤖 [Kachaka] ロボットが {location_id} へ移動開始...")
        await asyncio.sleep(10) # 10秒間の移動をシミュレート
        self.is_busy = False
        print(f"🤖 [Kachaka] ロボットが到着し、待機状態になりました。")

# --- Kachaka関連の状態管理 ---
kachaka_robot = MockKachakaRobot()
kachaka_command_queue = deque() # Kachakaの命令をためるためのキュー
kachaka_clients = set() # Kachakaに接続中のクライアント

async def process_kachaka_queue():
    """Kachakaのキューを継続的に監視し、ロボットが空いていれば命令を処理する"""
    while True:
        if not kachaka_robot.is_busy and kachaka_command_queue:
            location_id = kachaka_command_queue.popleft()
            
            # 全クライアントにロボットが移動中であることを通知
            for client in kachaka_clients:
                await client.send_json({"status": "moving", "destination": location_id})
            
            await kachaka_robot.move_to_location(location_id)
            
            # ロボットが待機状態になったことを全クライアントに通知
            for client in kachaka_clients:
                await client.send_json({"status": "idle"})
        
        await asyncio.sleep(1) # 1秒ごとにチェック

# --- Kachaka用のWebSocketエンドポイント ---
@app.websocket("/ws/kachaka")
async def websocket_kachaka_endpoint(websocket: WebSocket):
    await websocket.accept()
    kachaka_clients.add(websocket)
    print(f"✅ [Kachaka] Flutterクライアントが接続しました。")
    try:
        while True:
            data = await websocket.receive_json()
            # クライアントから移動リクエストを受け取った場合
            if data.get("action") == "MOVE":
                location_id = data.get("location_id")
                if location_id:
                    kachaka_command_queue.append(location_id)
                    # コマンドがキューに追加されたことを全クライアントに通知
                    for client in kachaka_clients:
                       await client.send_json({
                           "status": "queued",
                           "queue_length": len(kachaka_command_queue)
                        })
    except WebSocketDisconnect:
        kachaka_clients.remove(websocket)
        print(f"❌ [Kachaka] Flutterクライアントの接続が切れました。")


# =================================================================
# Section 2: サーボモーター制御関連のコード
# =================================================================

# --- サーボの初期化と設定 ---
servoRight = Control(physical_id=7, name="Right Servo")
servoLeft = Control(physical_id=5, name="Left Servo")

APP_ID_TO_SERVO_INSTANCE = {
    1: servoRight,
    2: servoLeft,
}

# --- 角度や動作の基本設定 ---
MIN_ANGLE = -60
MAX_ANGLE = 60
current_angles = { 1: 0, 2: 0 }
STEP = 1.0
UPDATE_INTERVAL = 0.01

# --- サーボの状態管理 ---
movement_states = {}

def move_servo_by_app_id(app_id, angle):
    servo_instance = APP_ID_TO_SERVO_INSTANCE.get(app_id)
    if servo_instance:
        target_angle = max(MIN_ANGLE, min(angle, MAX_ANGLE))
        servo_instance.move(target_angle)
        current_angles[app_id] = target_angle

async def servo_loop():
    """サーボの角度を継続的に更新するループ"""
    while True:
        for app_id, direction in list(movement_states.items()):
            if direction != "stop":
                angle = current_angles.get(app_id, 0)
                if direction == "right": angle -= STEP
                elif direction == "left": angle += STEP
                move_servo_by_app_id(app_id, angle)
        await asyncio.sleep(UPDATE_INTERVAL)

# --- サーボ制御用のWebSocketエンドポイント ---
@app.websocket("/ws/servo")
async def websocket_servo_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_app_id = None
    print(f"✅ [Servo] Webクライアントが接続しました。")
    try:
        # ★★★ ここからが修正箇所 ★★★
        while True:
            # クライアントからのメッセージをJSON形式で待機
            data = await websocket.receive_json()
        # ★★★ ここまでが修正箇所 ★★★
            
            # 以降の処理は try のインデントを一つ浅くする
            try:
                command = data.get("command")
                app_id = data.get("app_id")

                if app_id not in APP_ID_TO_SERVO_INSTANCE:
                    print(f"⚠️ [Servo] 無効なアプリID: {app_id}")
                    continue
                
                client_app_id = app_id

                if command and command.startswith("start_"):
                    direction = command.split("_")[1]
                    movement_states[app_id] = direction
                elif command == "stop":
                    movement_states[app_id] = "stop"

            except Exception as e:
                print(f"🔥 [Servo] 処理エラー: {e}")

    except WebSocketDisconnect:
        print(f"❌ [Servo] Webクライアント (App ID: {client_app_id}) との接続が切れました。")
    finally:
        # 接続が切れたら、そのクライアントのサーボの動きを止める
        if client_app_id:
            movement_states[client_app_id] = "stop"
            print(f"🛑 [Servo] App ID: {client_app_id} の動作を停止しました。")

# =================================================================
# Section 3: サーバーの起動設定
# =================================================================

@app.on_event("startup")
async def startup_event():
    """サーバー起動時に実行される処理"""
    print("🚀 統合サーバーを起動します...")
    # 1. 両方のサーボを初期位置(0度)に設定
    print("🔩 [Servo] サーボを初期位置に設定します。")
    move_servo_by_app_id(1, 0)
    move_servo_by_app_id(2, 0)
    
    # 2. バックグラウンドタスクを開始
    asyncio.create_task(process_kachaka_queue())
    asyncio.create_task(servo_loop())
    print("🛰️  バックグラウンドタスク（Kachakaキュー, Servoループ）を開始しました。")
    print("✅ サーバーの準備が完了しました。")

# このファイルが直接実行されたときにサーバーを起動するためのコード
if __name__ == "__main__":
    import uvicorn
    # 0.0.0.0 を指定することで、同じネットワーク内の他のPCからアクセス可能になります
    # port=8000 を指定して、8000番ポートで待ち受けます
    uvicorn.run(app, host="0.0.0.0", port=8000)
