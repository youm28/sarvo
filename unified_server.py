import asyncio
import json
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from Control import Control
import kachaka_api
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import math  # ★ 距離計算のためにmathライブラリをインポート

# --- ★★★ ご自身のKachakaロボットのIPアドレスに変更してください ★★★ ---
KACHAKA_IP = "10.40.5.97"

# --- FastAPIアプリケーションのインスタンスを作成 ---
app = FastAPI()
kachaka_client: kachaka_api.KachakaApiClient = None

# =================================================================
# Section 1: Kachaka ロボット制御関連のコード
# =================================================================
kachaka_command_queue = deque()
kachaka_clients = set()
kachaka_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=1)

# ★ 2ユーザー協調のための状態管理変数
user_assignments = {}  # {websocket: "user_1", ...}
destination_requests = {}  # {"user_1": {"name": "A", "pose": {...}}, ...}


def kachaka_move_sync(location_id, location_name):
    """同期的なKachaka移動処理（別スレッドで実行）"""
    global kachaka_client
    try:
        print(f"🤖 [Kachaka Thread] Starting move to {location_name} ({location_id})")
        # .get() で同期的に結果を待つ
        result = kachaka_client.move_to_location(location_id).get()
        print(f"✅ [Kachaka Thread] Move result: {result}")
        return result.success
    except Exception as e:
        print(f"🔥 [Kachaka Thread] Move failed: {e}")
        return False


async def send_status_to_all_clients(status_data):
    """全クライアントに状態を送信するヘルパー関数"""
    if not kachaka_clients:
        return
    # list()でコピーを作成してからイテレートすることで、送信中のクライアント切断エラーを防ぐ
    disconnected_clients = []
    for client in list(kachaka_clients):
        try:
            await client.send_json(status_data)
        except Exception:
            disconnected_clients.append(client)
    # 送信に失敗したクライアントをセットから削除
    for client in disconnected_clients:
        kachaka_clients.discard(client)
    print(f"📤 [Broadcast] Sent to {len(kachaka_clients)} clients: {status_data}")


async def process_destination_requests():
    """2人分のリクエストが揃ったら距離を計算し、キューに追加する"""
    global destination_requests
    print("🧠 [Decision] Checking requests...")

    if len(destination_requests) < 2:
        return

    print("✅ [Decision] Two requests received. Starting calculation.")

    # リクエスト情報を取得
    user1_req = destination_requests["user_1"]
    user2_req = destination_requests["user_2"]
    # どちらのリクエストにも同じロボット座標が入っているはず
    robot_pose = user1_req["robot_pose"]

    # ユーザー1の目的地までの距離
    dist1 = math.dist(
        [robot_pose["x"], robot_pose["y"]],
        [user1_req["location"]["pose"]["x"], user1_req["location"]["pose"]["y"]]
    )
    # ユーザー2の目的地までの距離
    dist2 = math.dist(
        [robot_pose["x"], robot_pose["y"]],
        [user2_req["location"]["pose"]["x"], user2_req["location"]["pose"]["y"]]
    )

    print(f"📏 [Decision] Distance to '{user1_req['location']['name']}': {dist1:.2f}")
    print(f"📏 [Decision] Distance to '{user2_req['location']['name']}': {dist2:.2f}")

    # 距離が近い方を先にキューに追加
    if dist1 <= dist2:
        first_destination = user1_req
        second_destination = user2_req
    else:
        first_destination = user2_req
        second_destination = user1_req

    print(f"🏆 [Decision] First: '{first_destination['location']['name']}', Second: '{second_destination['location']['name']}'")

    # 全クライアントに決定を通知
    await send_status_to_all_clients({
        "type": "decision_made",
        "message": f"'{first_destination['location']['name']}'が近いため、先に向かいます。",
        "first_destination": first_destination['location']['name'],
        "second_destination": second_destination['location']['name'],
    })
    await asyncio.sleep(1)  # 通知を見せるための短い待機

    # 実際の移動コマンドをキューに追加
    with kachaka_lock:
        kachaka_command_queue.append(first_destination["location"])
        kachaka_command_queue.append(second_destination["location"])
        queue_length = len(kachaka_command_queue)
        print(f"📋 [Queue] Added 2 destinations. New length: {queue_length}")

    # 処理が終わったのでリクエストをクリア
    destination_requests.clear()
    print("🧹 [Decision] Requests cleared for the next round.")


async def process_kachaka_queue():
    """キューを監視し、ロボットが待機状態なら次の命令を実行する"""
    global kachaka_client
    current_move_future = None
    idle_start_time = None

    while True:
        try:
            if not kachaka_client:
                await asyncio.sleep(1)
                continue

            is_busy = kachaka_client.is_command_running()

            if current_move_future and current_move_future.done():
                result = current_move_future.result()
                if result:
                    print("✅ [Kachaka] Move completed successfully")
                    idle_start_time = time.time()
                    await send_status_to_all_clients({"type": "kachaka_status", "status": "idle"})
                else:
                    print("🔥 [Kachaka] Move failed")
                    idle_start_time = None
                    await send_status_to_all_clients({"type": "kachaka_status", "status": "error", "message": "Move failed"})
                current_move_future = None

            wait_period_complete = True
            if idle_start_time is not None:
                if time.time() - idle_start_time < 5.0:  # 待機時間を5秒に設定
                    wait_period_complete = False
                else:
                    idle_start_time = None

            if not current_move_future and not is_busy and wait_period_complete:
                with kachaka_lock:
                    if kachaka_command_queue:
                        location_data = kachaka_command_queue.popleft()
                        location_id = location_data["id"]
                        location_name = location_data["name"]

                        print(f"🤖 [Kachaka] Sending command: Move to {location_name}")
                        await send_status_to_all_clients({
                            "type": "kachaka_status",
                            "status": "moving",
                            "destination": location_name,
                        })

                        loop = asyncio.get_event_loop()
                        current_move_future = loop.run_in_executor(
                            executor, kachaka_move_sync, location_id, location_name
                        )

        except Exception as e:
            print(f"🔥 Error in process_kachaka_queue: {e}")
            current_move_future = None
            idle_start_time = None
            await asyncio.sleep(5)

        await asyncio.sleep(0.5)


@app.websocket("/ws/kachaka")
async def websocket_kachaka_endpoint(websocket: WebSocket):
    await websocket.accept()
    kachaka_clients.add(websocket)
    client_id = id(websocket)
    user_id = None

    # ユーザー割り当て処理
    with kachaka_lock:
        if "user_1" not in user_assignments.values():
            user_id = "user_1"
        elif "user_2" not in user_assignments.values():
            user_id = "user_2"
        else:
            user_id = "spectator"  # 3人目以降は観戦者
        user_assignments[websocket] = user_id

    print(f"✅ [Connect] Client {client_id} connected as {user_id}.")
    print(f"📊 Total clients: {len(kachaka_clients)}")

    # 接続したクライアントにユーザーIDを通知
    await websocket.send_json({"type": "user_assigned", "user_id": user_id})

    try:
        while True:
            data = await websocket.receive_json()
            print(f"📨 [Receive] From {user_id} ({client_id}): {data}")

            if data.get("action") == "REQUEST_DESTINATION":
                if user_id in ["user_1", "user_2"]:
                    destination_requests[user_id] = {
                        "location": data.get("location"),
                        "robot_pose": data.get("robot_pose")
                    }
                    print(f"📝 [Request] Saved destination for {user_id}: {data['location']['name']}")

                    other_user = "user_2" if user_id == "user_1" else "user_1"
                    if other_user in destination_requests:
                        message = "両者の目的地が決定しました。ルートを計算します..."
                    else:
                        message = f"ユーザー「{user_id.split('_')[1]}」が目的地を決定。相手の選択を待っています..."

                    await send_status_to_all_clients({
                        "type": "waiting_for_opponent",
                        "message": message,
                        "user_1_done": "user_1" in destination_requests,
                        "user_2_done": "user_2" in destination_requests,
                    })

                    await process_destination_requests()

    except WebSocketDisconnect:
        # 切断時のクリーンアップ処理
        disconnected_user_id = user_assignments.pop(websocket, None)
        kachaka_clients.discard(websocket)
        if disconnected_user_id and disconnected_user_id in destination_requests:
            destination_requests.pop(disconnected_user_id)
            print(f"🧹 [Cleanup] Cleared request for disconnected user {disconnected_user_id}.")
            await send_status_to_all_clients({
                "type": "user_disconnected",
                "message": f"ユーザー「{disconnected_user_id.split('_')[1]}」の接続が切れました。リクエストはリセットされます。"
            })
            # 片方が切れたらもう片方のリクエストもクリア
            destination_requests.clear()


        print(f"❌ [Disconnect] Client {client_id} ({disconnected_user_id}) disconnected.")
        print(f"📊 Remaining clients: {len(kachaka_clients)}")


# =================================================================
# Section 2: Servo Motor Control (変更なし)
# =================================================================
servoRight = Control(physical_id=7, name="Right Servo")
servoLeft = Control(physical_id=5, name="Left Servo")
APP_ID_TO_SERVO_INSTANCE = {1: servoRight, 2: servoLeft}
MIN_ANGLE, MAX_ANGLE, STEP, UPDATE_INTERVAL = -60, 60, 1.0, 0.01
current_angles = {1: 0, 2: 0}
movement_states = {}
servo_lock = threading.Lock()

def move_servo_by_app_id(app_id, angle):
    with servo_lock:
        servo_instance = APP_ID_TO_SERVO_INSTANCE.get(app_id)
        if servo_instance:
            target_angle = max(MIN_ANGLE, min(angle, MAX_ANGLE))
            servo_instance.move(target_angle)
            current_angles[app_id] = target_angle

def servo_thread_loop():
    print("🔩 [Servo] Starting servo control thread...")
    while True:
        try:
            with servo_lock:
                states_copy = dict(movement_states)
            for app_id, direction in states_copy.items():
                if direction != "stop":
                    angle = current_angles.get(app_id, 0)
                    if direction == "right": angle -= STEP
                    elif direction == "left": angle += STEP
                    move_servo_by_app_id(app_id, angle)
        except Exception as e:
            print(f"🔥 [Servo] Error in servo_thread_loop: {e}")
        time.sleep(UPDATE_INTERVAL)

@app.websocket("/ws/servo")
async def websocket_servo_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_app_id = None
    try:
        while True:
            data = await websocket.receive_json()
            command = data.get("command")
            app_id = data.get("app_id")
            if app_id not in APP_ID_TO_SERVO_INSTANCE: continue
            client_app_id = app_id
            with servo_lock:
                if command and command.startswith("start_"):
                    movement_states[app_id] = command.split("_")[1]
                elif command == "stop":
                    movement_states[app_id] = "stop"
    except WebSocketDisconnect:
        print(f"❌ [Servo] Web client disconnected (App ID: {client_app_id}).")
    finally:
        if client_app_id:
            with servo_lock:
                movement_states[client_app_id] = "stop"

# =================================================================
# Section 3: Server Startup
# =================================================================
async def retry_kachaka_connection():
    global kachaka_client
    while kachaka_client is None:
        try:
            print(f"🔄 Retrying connection to Kachaka robot at {KACHAKA_IP}...")
            kachaka_client = kachaka_api.KachakaApiClient(f"{KACHAKA_IP}:26400")
            await kachaka_client.get_robot_version()
            print("✅ Reconnected to Kachaka robot!")
            break
        except Exception as e:
            print(f"🔥 Retry failed: {e}")
            await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    global kachaka_client
    print("🚀 Starting unified server...")

    # サーボ初期化
    try:
        move_servo_by_app_id(1, 0)
        move_servo_by_app_id(2, 0)
    except Exception as e:
        print(f"🔥 [Servo] Failed to initialize servos: {e}")
    servo_thread = threading.Thread(target=servo_thread_loop, daemon=True)
    servo_thread.start()

    # Kachaka接続
    try:
        kachaka_client = kachaka_api.KachakaApiClient(f"{KACHAKA_IP}:26400")
        robot_version = await kachaka_client.get_robot_version()
        print(f"✅ Connected to Kachaka robot! Version: {robot_version}")
    except Exception as e:
        print(f"🔥 FAILED to connect to Kachaka robot: {e}")
        kachaka_client = None
        asyncio.create_task(retry_kachaka_connection())

    # Kachakaタスクを開始
    asyncio.create_task(process_kachaka_queue())
    print("✅ Server is ready.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)