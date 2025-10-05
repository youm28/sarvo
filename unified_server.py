import asyncio
import json
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from Control import Control
import kachaka_api  # 本物のKachaka APIをインポート

# --- ★★★ ご自身のKachakaロボットのIPアドレスに変更してください ★★★ ---
KACHAKA_IP = "10.40.5.97"

# --- FastAPIアプリケーションのインスタンスを作成 ---
app = FastAPI()
kachaka_client: kachaka_api.KachakaApiClient = None # グローバルなクライアント変数

# =================================================================
# Section 1: Kachaka ロボット制御関連のコード
# =================================================================
kachaka_command_queue = deque()
kachaka_clients = set()

async def process_kachaka_queue():
    """キューを監視し、ロボットが待機状態なら次の命令を実行する"""
    global kachaka_client
    while True:
        try:
            if not kachaka_client:
                print("🔥 [DEBUG] Kachaka client is None")
                await asyncio.sleep(1)
                continue
            
            # デバッグ情報を追加 - awaitを削除
            is_busy = kachaka_client.is_command_running()
            queue_length = len(kachaka_command_queue)
            
            print(f"🐛 [DEBUG] is_busy: {is_busy}, queue_length: {queue_length}")
            
            if not is_busy and kachaka_command_queue:
                location_data = kachaka_command_queue.popleft()
                location_id = location_data["id"]
                location_name = location_data["name"]
                
                print(f"🤖 [Kachaka] Sending command to robot: Move to {location_name} ({location_id})")
                
                # 全クライアントにロボットが移動中であることを通知
                for client in kachaka_clients:
                    await client.send_json({"type": "kachaka_status", "status": "moving", "destination": location_name})
                
                # ★★★ 本物のロボットに移動命令を送信 ★★★
                try:
                    await kachaka_client.move_to_location(location_id)
                    print(f"✅ [Kachaka] Command sent successfully")
                except Exception as move_error:
                    print(f"🔥 [Kachaka] Failed to send move command: {move_error}")
                    # 失敗した場合、キューに戻すかエラー通知
                    for client in kachaka_clients:
                        await client.send_json({"type": "kachaka_status", "status": "error", "message": str(move_error)})
            elif is_busy:
                print(f"🤖 [DEBUG] Robot is busy, waiting...")
            elif not kachaka_command_queue:
                print(f"🤖 [DEBUG] Queue is empty")
                
        except Exception as e:
            print(f"🔥 Error in process_kachaka_queue: {e}")
            await asyncio.sleep(5)
        
        await asyncio.sleep(1)

async def broadcast_robot_status():
    """ロボットの状態を定期的に全クライアントにブロードキャストする"""
    global kachaka_client
    last_is_busy = False
    while True:
        try:
            if kachaka_client:
                # awaitを削除
                is_busy = kachaka_client.is_command_running()
                # 状態が「実行中」から「待機」に変わった瞬間を検知
                if last_is_busy and not is_busy:
                    print("🤖 [Kachaka] Command finished, robot is now idle.")
                    for client in kachaka_clients:
                        await client.send_json({"type": "kachaka_status", "status": "idle"})
                last_is_busy = is_busy
        except Exception as e:
            print(f"🔥 Error in broadcast_robot_status: {e}")
        await asyncio.sleep(1) # 1秒ごとに状態を確認

@app.websocket("/ws/kachaka")
async def websocket_kachaka_endpoint(websocket: WebSocket):
    await websocket.accept()
    kachaka_clients.add(websocket)
    print(f"✅ [Kachaka] Flutter client connected.")
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "MOVE":
                location_id = data.get("location_id")
                location_name = data.get("location_name")
                if location_id and location_name:
                    kachaka_command_queue.append({"id": location_id, "name": location_name})
                    for client in kachaka_clients:
                       await client.send_json({
                           "type": "kachaka_status",
                           "status": "queued",
                           "queue_length": len(kachaka_command_queue)
                        })
    except WebSocketDisconnect:
        kachaka_clients.remove(websocket)
        print(f"❌ [Kachaka] Flutter client disconnected.")

# =================================================================
# Section 2: Servo Motor Control (変更なし)
# =================================================================
servoRight = Control(physical_id=7, name="Right Servo")
servoLeft = Control(physical_id=5, name="Left Servo")
APP_ID_TO_SERVO_INSTANCE = {1: servoRight, 2: servoLeft}
MIN_ANGLE, MAX_ANGLE, STEP, UPDATE_INTERVAL = -60, 60, 1.0, 0.01
current_angles = {1: 0, 2: 0}
movement_states = {}

def move_servo_by_app_id(app_id, angle):
    servo_instance = APP_ID_TO_SERVO_INSTANCE.get(app_id)
    if servo_instance:
        target_angle = max(MIN_ANGLE, min(angle, MAX_ANGLE))
        servo_instance.move(target_angle)
        current_angles[app_id] = target_angle

async def servo_loop():
    while True:
        for app_id, direction in list(movement_states.items()):
            if direction != "stop":
                angle = current_angles.get(app_id, 0)
                if direction == "right": angle -= STEP
                elif direction == "left": angle += STEP
                move_servo_by_app_id(app_id, angle)
        await asyncio.sleep(UPDATE_INTERVAL)

@app.websocket("/ws/servo")
async def websocket_servo_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_app_id = None
    print(f"✅ [Servo] Web client connected.")
    try:
        while True:
            data = await websocket.receive_json()
            try:
                command = data.get("command")
                app_id = data.get("app_id")
                if app_id not in APP_ID_TO_SERVO_INSTANCE:
                    print(f"⚠️ [Servo] Invalid App ID: {app_id}")
                    continue
                client_app_id = app_id
                if command and command.startswith("start_"):
                    direction = command.split("_")[1]
                    movement_states[app_id] = direction
                elif command == "stop":
                    movement_states[app_id] = "stop"
            except Exception as e:
                print(f"🔥 [Servo] Processing error: {e}")
    except WebSocketDisconnect:
        print(f"❌ [Servo] Web client (App ID: {client_app_id}) disconnected.")
    finally:
        if client_app_id:
            movement_states[client_app_id] = "stop"
            print(f"🛑 [Servo] Stopped movement for App ID: {client_app_id}")

# =================================================================
# Section 3: Server Startup
# =================================================================
async def retry_kachaka_connection():
    """Kachaka接続を定期的に再試行する"""
    global kachaka_client
    while kachaka_client is None:
        try:
            print(f"🔄 Retrying connection to Kachaka robot at {KACHAKA_IP}...")
            kachaka_client = kachaka_api.KachakaApiClient(f"{KACHAKA_IP}:26400")
            robot_version = await kachaka_client.get_robot_version()
            print(f"✅ Reconnected to Kachaka robot! Version: {robot_version}")
            break
        except Exception as e:
            print(f"🔥 Retry failed: {e}")
            await asyncio.sleep(10)  # 10秒待ってから再試行

@app.on_event("startup")
async def startup_event():
    global kachaka_client
    print("🚀 Starting unified server...")

    print(f"🔌 Connecting to Kachaka robot at {KACHAKA_IP}...")
    try:
        kachaka_client = kachaka_api.KachakaApiClient(f"{KACHAKA_IP}:26400")
        robot_version = await kachaka_client.get_robot_version()
        print(f"✅ Connected to Kachaka robot! Version: {robot_version}")
    except Exception as e:
        print(f"🔥 FAILED to connect to Kachaka robot: {e}")
        kachaka_client = None
        # バックグラウンドで再接続を試行
        asyncio.create_task(retry_kachaka_connection())

    print("🔩 [Servo] Initializing servos to position 0.")
    move_servo_by_app_id(1, 0)
    move_servo_by_app_id(2, 0)
    
    # バックグラウンドタスクを開始
    asyncio.create_task(process_kachaka_queue())
    asyncio.create_task(servo_loop())
    asyncio.create_task(broadcast_robot_status())
    
    print("🛰️  Background tasks started.")
    print("✅ Server is ready.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

