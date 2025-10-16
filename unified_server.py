import asyncio
import json
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from Control import Control
import kachaka_api
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import math

KACHAKA_IP = "10.40.5.97"
app = FastAPI()
kachaka_client: kachaka_api.KachakaApiClient = None

# =================================================================
# Section 1: Kachaka ロボット制御関連のコード
# =================================================================
kachaka_command_queue = deque()
kachaka_clients = set()
kachaka_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=1)

# ★ 状態管理変数を拡張
user_assignments = {}
destination_requests = {}
proposed_plan = {}          # {"first": ..., "second": ...}
plan_confirmations = set()  # {"user_1", "user_2"}


def kachaka_move_sync(location_id, location_name):
    """同期的なKachaka移動処理（別スレッドで実行）"""
    global kachaka_client
    try:
        print(f"🤖 [Kachaka Thread] Starting move to {location_name} ({location_id})")
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
    disconnected_clients = []
    for client in list(kachaka_clients):
        try:
            await client.send_json(status_data)
        except Exception:
            disconnected_clients.append(client)
    for client in disconnected_clients:
        kachaka_clients.discard(client)
    print(f"📤 [Broadcast] Sent to {len(kachaka_clients)} clients: {status_data}")


async def process_destination_requests():
    """2人分のリクエストが揃ったら距離を計算し、プランを提案する"""
    global destination_requests, proposed_plan, plan_confirmations

    if len(destination_requests) < 2:
        return

    print("✅ [Decision] Two requests received. Starting calculation.")
    user1_req = destination_requests["user_1"]
    user2_req = destination_requests["user_2"]
    robot_pose = user1_req["robot_pose"]

    dist1 = math.dist([robot_pose["x"], robot_pose["y"]], [user1_req["location"]["pose"]["x"], user1_req["location"]["pose"]["y"]])
    dist2 = math.dist([robot_pose["x"], robot_pose["y"]], [user2_req["location"]["pose"]["x"], user2_req["location"]["pose"]["y"]])

    if dist1 <= dist2:
        first = user1_req["location"]
        second = user2_req["location"]
    else:
        first = user2_req["location"]
        second = user1_req["location"]

    # ★ 提案を作成して保持し、同意状態をリセット
    proposed_plan["first"] = first
    proposed_plan["second"] = second
    plan_confirmations.clear()
    print(f"🏆 [Decision] Proposal created: 1st='{first['name']}', 2nd='{second['name']}'")

    # ★ 全クライアントにプランを提案
    proposal_message = f"'{first['name']}'の方が距離が近いので、先にそちらへ行きましょう！"
    await send_status_to_all_clients({
        "type": "PROPOSE_PLAN",
        "message": proposal_message,
    })
    print(f"📢 [Proposal] Sent: {proposal_message}")


async def process_kachaka_queue():
    # (この関数に変更はありません)
    global kachaka_client
    current_move_future = None
    idle_start_time = None
    while True:
        try:
            if not kachaka_client:
                await asyncio.sleep(1); continue
            is_busy = kachaka_client.is_command_running()
            if current_move_future and current_move_future.done():
                result = current_move_future.result()
                status = "idle" if result else "error"
                message = "Move failed" if not result else ""
                await send_status_to_all_clients({"type": "kachaka_status", "status": status, "message": message})
                current_move_future = None
                idle_start_time = time.time() if result else None
            wait_period_complete = idle_start_time is None or (time.time() - idle_start_time >= 5.0)
            if not current_move_future and not is_busy and wait_period_complete:
                idle_start_time = None
                with kachaka_lock:
                    if kachaka_command_queue:
                        location_data = kachaka_command_queue.popleft()
                        await send_status_to_all_clients({"type": "kachaka_status", "status": "moving", "destination": location_data["name"]})
                        loop = asyncio.get_event_loop()
                        current_move_future = loop.run_in_executor(executor, kachaka_move_sync, location_data["id"], location_data["name"])
        except Exception as e:
            print(f"🔥 Error in process_kachaka_queue: {e}")
            current_move_future = None; idle_start_time = None
            await asyncio.sleep(5)
        await asyncio.sleep(0.5)


@app.websocket("/ws/kachaka")
async def websocket_kachaka_endpoint(websocket: WebSocket):
    await websocket.accept()
    kachaka_clients.add(websocket)
    user_id = None

    with kachaka_lock:
        if "user_1" not in user_assignments.values(): user_id = "user_1"
        elif "user_2" not in user_assignments.values(): user_id = "user_2"
        else: user_id = "spectator"
        user_assignments[websocket] = user_id
    print(f"✅ [Connect] Client connected as {user_id}. Total: {len(kachaka_clients)}")
    await websocket.send_json({"type": "user_assigned", "user_id": user_id})

    try:
        while True:
            data = await websocket.receive_json()
            print(f"📨 [Receive] From {user_id}: {data}")
            action = data.get("action")

            if action == "REQUEST_DESTINATION":
                if user_id in ["user_1", "user_2"]:
                    destination_requests[user_id] = {"location": data.get("location"), "robot_pose": data.get("robot_pose")}
                    print(f"📝 [Request] Saved for {user_id}: {data['location']['name']}")
                    if len(destination_requests) == 2:
                        await process_destination_requests()
                    else:
                        await send_status_to_all_clients({"type": "WAITING_FOR_OPPONENT", "message": "相手の選択を待っています…"})

            # ★ 同意アクションを処理
            elif action == "CONFIRM_PLAN":
                if user_id in ["user_1", "user_2"]:
                    print(f"👍 [Confirm] Received from {user_id}")
                    plan_confirmations.add(user_id)

                    if len(plan_confirmations) == 2:
                        print("✅ [Confirm] Both users agreed. Starting move.")
                        await send_status_to_all_clients({"type": "STARTING_MOVE", "message": "両者が合意しました。移動を開始します！"})
                        await asyncio.sleep(1)
                        with kachaka_lock:
                            if proposed_plan:
                                kachaka_command_queue.append(proposed_plan["first"])
                                kachaka_command_queue.append(proposed_plan["second"])
                        # 状態をリセット
                        destination_requests.clear()
                        plan_confirmations.clear()
                        proposed_plan.clear()
                    else:
                         await send_status_to_all_clients({"type": "WAITING_FOR_CONFIRMATION", "message": "相手の同意を待っています…"})

    except WebSocketDisconnect:
        disconnected_user_id = user_assignments.pop(websocket, None)
        kachaka_clients.discard(websocket)
        if disconnected_user_id:
            # ★ 切断時にすべての状態をリセット
            destination_requests.clear()
            plan_confirmations.clear()
            proposed_plan.clear()
            print(f"🧹 [Cleanup] All states cleared due to disconnect from {disconnected_user_id}.")
            await send_status_to_all_clients({"type": "user_disconnected", "message": f"ユーザーが切断したため、リセットされました。"})
        print(f"❌ [Disconnect] Client disconnected. Remaining: {len(kachaka_clients)}")

# (Section 2: Servo, Section 3: Server Startup は変更ありません)
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