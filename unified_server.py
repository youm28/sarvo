import asyncio
import json
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from Control import Control
import kachaka_api  # 本物のKachaka APIをインポート
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# --- ★★★ ご自身のKachakaロボットのIPアドレスに変更してください ★★★ ---
KACHAKA_IP = "10.40.5.97"

# --- FastAPIアプリケーションのインスタンスを作成 ---
app = FastAPI()
kachaka_client: kachaka_api.KachakaApiClient = None # グローバルなクライアント変数

# =================================================================
# Section 1: Kachaka ロボット制御関連のコード（スレッド化）
# =================================================================
kachaka_command_queue = deque()
kachaka_clients = set()
kachaka_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=1)  # Kachaka用のスレッドプール

def kachaka_move_sync(location_id, location_name):
    """同期的なKachaka移動処理（別スレッドで実行）"""
    global kachaka_client
    try:
        print(f"🤖 [Kachaka Thread] Starting move to {location_name} ({location_id})")
        
        # ★ まず、move_to_location が何を返すかを確認
        move_method = kachaka_client.move_to_location(location_id)
        print(f"🔍 [Kachaka Thread] move_to_location returned: {type(move_method)}")
        print(f"🔍 [Kachaka Thread] move_method value: {move_method}")
        
        # ★ Resultオブジェクトかどうか確認
        if hasattr(move_method, 'success'):
            print(f"🔍 [Kachaka Thread] Result.success: {move_method.success}")
            if hasattr(move_method, 'error'):
                print(f"🔍 [Kachaka Thread] Result.error: {move_method.error}")
        
        # ★ もし既にResultオブジェクトなら、awaitせずにそのまま使用
        if str(type(move_method)).find('Result') != -1:
            print(f"✅ [Kachaka Thread] Move result: {move_method}")
            return hasattr(move_method, 'success') and move_method.success
        
        # ★ コルーチンの場合の処理
        import inspect
        if inspect.iscoroutine(move_method):
            print(f"🔍 [Kachaka Thread] It's a coroutine, running with asyncio.run")
            result = asyncio.run(move_method)
            print(f"✅ [Kachaka Thread] Coroutine result: {result}")
            return True
        
        # ★ 通常の関数の場合
        print(f"🔍 [Kachaka Thread] It's a regular function result")
        result = move_method
        print(f"✅ [Kachaka Thread] Function result: {result}")
        return True
        
    except Exception as e:
        print(f"🔥 [Kachaka Thread] Move failed: {e}")
        print(f"🔥 [Kachaka Thread] Error type: {type(e)}")
        return False

async def send_status_to_all_clients(status_data):
    """全クライアントに状態を送信するヘルパー関数（デバッグ付き）"""
    client_count = len(kachaka_clients)
    print(f"📤 [Status Broadcast] Sending to {client_count} clients: {status_data}")
    
    successful_sends = 0
    failed_sends = 0
    
    for client in list(kachaka_clients):
        try:
            await client.send_json(status_data)
            successful_sends += 1
        except Exception as client_error:
            print(f"🔥 [Status Broadcast] Failed to send to client: {client_error}")
            kachaka_clients.discard(client)
            failed_sends += 1
    
    print(f"📊 [Status Broadcast] Success: {successful_sends}, Failed: {failed_sends}")

async def process_kachaka_queue():
    """キューを監視し、ロボットが待機状態なら次の命令を実行する"""
    global kachaka_client
    current_move_future = None
    
    while True:
        try:
            if not kachaka_client:
                print("🔥 [DEBUG] Kachaka client is None")
                await asyncio.sleep(1)
                continue
            
            try:
                is_busy = kachaka_client.is_command_running()
                queue_length = len(kachaka_command_queue)
                
                print(f"🐛 [DEBUG] is_busy: {is_busy}, queue_length: {queue_length}, move_future: {current_move_future is not None}")
                
                # 現在の移動が完了したかチェック
                if current_move_future and current_move_future.done():
                    result = current_move_future.result()
                    if result:
                        print("✅ [Kachaka] Move completed successfully")
                        # 完了通知を送信
                        print("📤 [Status] Preparing to send 'idle' status")
                        await send_status_to_all_clients({
                            "type": "kachaka_status", 
                            "status": "idle",
                            "timestamp": time.time()
                        })
                    else:
                        print("🔥 [Kachaka] Move failed - sending detailed error")
                        # より詳細なエラー通知を送信
                        await send_status_to_all_clients({
                            "type": "kachaka_status", 
                            "status": "error", 
                            "message": "Robot movement failed - check if location exists and robot is accessible",
                            "error_code": "MOVE_FAILED",
                            "timestamp": time.time()
                        })
                    current_move_future = None
                
                # 新しい移動を開始
                if not current_move_future and not is_busy and kachaka_command_queue:
                    with kachaka_lock:
                        if kachaka_command_queue:  # ダブルチェック
                            location_data = kachaka_command_queue.popleft()
                            location_id = location_data["id"]
                            location_name = location_data["name"]
                            
                            print(f"🤖 [Kachaka] Sending command to robot: Move to {location_name} ({location_id})")
                            
                            # 全クライアントに移動開始を通知
                            print(f"📤 [Status] Preparing to send 'moving' status for {location_name}")
                            await send_status_to_all_clients({
                                "type": "kachaka_status", 
                                "status": "moving", 
                                "destination": location_name,
                                "location_id": location_id,
                                "timestamp": time.time()
                            })
                            
                            # 別スレッドで移動を実行
                            loop = asyncio.get_event_loop()
                            current_move_future = loop.run_in_executor(
                                executor, 
                                kachaka_move_sync, 
                                location_id, 
                                location_name
                            )
                            print(f"🚀 [Kachaka] Move future created for {location_name}")
                
            except Exception as kachaka_error:
                print(f"🔥 [Kachaka] Client operation error: {kachaka_error}")
                kachaka_client = None
                current_move_future = None
                await asyncio.sleep(5)
                continue
                
        except Exception as e:
            print(f"🔥 Error in process_kachaka_queue: {e}")
            current_move_future = None
            await asyncio.sleep(5)
        
        await asyncio.sleep(0.5)  # より短い間隔でチェック

async def broadcast_robot_status():
    """ロボットの状態を定期的に全クライアントにブロードキャストする"""
    global kachaka_client
    last_is_busy = False
    while True:
        try:
            if kachaka_client:
                try:
                    is_busy = kachaka_client.is_command_running()
                    # 状態変化の詳細ログ（デバッグ用）
                    if last_is_busy != is_busy:
                        print(f"🤖 [Kachaka Status Change] busy: {last_is_busy} → {is_busy}")
                        if not is_busy and last_is_busy:
                            print("🎯 [Kachaka Status Change] Robot became idle - but notification will be handled by process_kachaka_queue")
                    last_is_busy = is_busy
                except Exception as kachaka_error:
                    print(f"🔥 [Kachaka] Status check error: {kachaka_error}")
                    kachaka_client = None
                    last_is_busy = False
        except Exception as e:
            print(f"🔥 Error in broadcast_robot_status: {e}")
        
        await asyncio.sleep(1)

@app.websocket("/ws/kachaka")
async def websocket_kachaka_endpoint(websocket: WebSocket):
    await websocket.accept()
    kachaka_clients.add(websocket)
    client_id = id(websocket)
    print(f"✅ [Kachaka] Flutter client connected. Client ID: {client_id}")
    print(f"📊 [Kachaka] Total clients: {len(kachaka_clients)}")
    
    try:
        while True:
            data = await websocket.receive_json()
            print(f"📨 [Kachaka] Received from client {client_id}: {data}")
            
            if data.get("action") == "MOVE":
                location_id = data.get("location_id")
                location_name = data.get("location_name")
                if location_id and location_name:
                    with kachaka_lock:
                        kachaka_command_queue.append({"id": location_id, "name": location_name})
                        queue_length = len(kachaka_command_queue)
                    
                    print(f"📋 [Queue] Added '{location_name}' to queue. New length: {queue_length}")
                    
                    # 全クライアントにキューイング通知
                    print(f"📤 [Status] Preparing to send 'queued' status for {location_name}")
                    await send_status_to_all_clients({
                        "type": "kachaka_status",
                        "status": "queued",
                        "queue_length": queue_length,
                        "added_location": location_name,
                        "timestamp": time.time()
                    })
    except WebSocketDisconnect:
        kachaka_clients.discard(websocket)
        print(f"❌ [Kachaka] Flutter client {client_id} disconnected.")
        print(f"📊 [Kachaka] Remaining clients: {len(kachaka_clients)}")

# =================================================================
# Section 2: Servo Motor Control (スレッド化で独立性を確保)
# =================================================================
servoRight = Control(physical_id=7, name="Right Servo")
servoLeft = Control(physical_id=5, name="Left Servo")
APP_ID_TO_SERVO_INSTANCE = {1: servoRight, 2: servoLeft}
MIN_ANGLE, MAX_ANGLE, STEP, UPDATE_INTERVAL = -60, 60, 1.0, 0.01
current_angles = {1: 0, 2: 0}
movement_states = {}

# スレッドセーフなロック
servo_lock = threading.Lock()

def move_servo_by_app_id(app_id, angle):
    """スレッドセーフなサーボ制御"""
    with servo_lock:
        servo_instance = APP_ID_TO_SERVO_INSTANCE.get(app_id)
        if servo_instance:
            target_angle = max(MIN_ANGLE, min(angle, MAX_ANGLE))
            servo_instance.move(target_angle)
            current_angles[app_id] = target_angle
            print(f"🔩 [Servo] Moved servo {app_id} to {target_angle}°")

def servo_thread_loop():
    """別スレッドで動作するサーボ制御ループ"""
    print("🔩 [Servo] Starting servo control thread...")
    while True:
        try:
            # movement_statesのコピーを作成（スレッドセーフ）
            with servo_lock:
                states_copy = dict(movement_states)
            
            for app_id, direction in states_copy.items():
                if direction != "stop":
                    angle = current_angles.get(app_id, 0)
                    if direction == "right": 
                        angle -= STEP
                    elif direction == "left": 
                        angle += STEP
                    move_servo_by_app_id(app_id, angle)
        except Exception as e:
            print(f"🔥 [Servo] Error in servo_thread_loop: {e}")
        
        time.sleep(UPDATE_INTERVAL)

@app.websocket("/ws/servo")
async def websocket_servo_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_app_id = None
    servo_client_id = id(websocket)
    print(f"✅ [Servo] Web client connected. Client ID: {servo_client_id}")
    
    try:
        while True:
            data = await websocket.receive_json()
            print(f"📨 [Servo] Received from client {servo_client_id}: {data}")
            
            try:
                command = data.get("command")
                app_id = data.get("app_id")
                if app_id not in APP_ID_TO_SERVO_INSTANCE:
                    print(f"⚠️ [Servo] Invalid App ID: {app_id}")
                    continue
                client_app_id = app_id
                
                # スレッドセーフな状態更新
                with servo_lock:
                    if command and command.startswith("start_"):
                        direction = command.split("_")[1]
                        movement_states[app_id] = direction
                        print(f"🔩 [Servo] App {app_id} started moving {direction}")
                    elif command == "stop":
                        movement_states[app_id] = "stop"
                        print(f"🔩 [Servo] App {app_id} stopped")
                        
            except Exception as e:
                print(f"🔥 [Servo] Processing error: {e}")
    except WebSocketDisconnect:
        print(f"❌ [Servo] Web client {servo_client_id} (App ID: {client_app_id}) disconnected.")
    finally:
        if client_app_id:
            with servo_lock:
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
            await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    global kachaka_client
    print("🚀 Starting unified server...")

    # サーボの初期化（完全に独立）
    print("🔩 [Servo] Initializing servos to position 0.")
    try:
        move_servo_by_app_id(1, 0)
        move_servo_by_app_id(2, 0)
        print("✅ [Servo] Servos initialized successfully.")
    except Exception as e:
        print(f"🔥 [Servo] Failed to initialize servos: {e}")

    # サーボスレッドを開始（完全に独立）
    servo_thread = threading.Thread(target=servo_thread_loop, daemon=True)
    servo_thread.start()
    print("✅ [Servo] Servo thread started.")
    
    # Kachaka接続を試行
    print(f"🔌 Connecting to Kachaka robot at {KACHAKA_IP}...")
    try:
        kachaka_client = kachaka_api.KachakaApiClient(f"{KACHAKA_IP}:26400")
        robot_version = await kachaka_client.get_robot_version()
        print(f"✅ Connected to Kachaka robot! Version: {robot_version}")
    except Exception as e:
        print(f"🔥 FAILED to connect to Kachaka robot: {e}")
        kachaka_client = None
        asyncio.create_task(retry_kachaka_connection())

    # Kachakaタスクを開始（非ブロッキング）
    kachaka_queue_task = asyncio.create_task(process_kachaka_queue())
    kachaka_status_task = asyncio.create_task(broadcast_robot_status())
    
    print("✅ [Kachaka] Background tasks started.")
    print("✅ Server is ready.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

