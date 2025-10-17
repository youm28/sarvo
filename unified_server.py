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

# 状態管理変数
user_assignments = {}
destination_requests = {}
proposed_plan = {}
plan_confirmations = set()


def kachaka_move_sync(location_id, location_name):
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
    if not kachaka_clients: return
    disconnected_clients = []
    for client in list(kachaka_clients):
        try:
            await client.send_json(status_data)
        except Exception:
            disconnected_clients.append(client)
    for client in disconnected_clients:
        kachaka_clients.discard(client)
    print(f"📤 [Broadcast] Sent to {len(kachaka_clients)} clients: {status_data}")


# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
# ★ START: この関数を全面的に書き換え               ★
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
async def process_destination_requests():
    """2つの目的地を巡る最短ルートを計算し、プランを提案する"""
    global destination_requests, proposed_plan, plan_confirmations

    if len(destination_requests) < 2:
        return

    print("✅ [Decision] Two requests received. Calculating shortest overall route.")
    
    # --- 必要な情報を抽出 ---
    user1_req = destination_requests["user_1"]
    user2_req = destination_requests["user_2"]
    
    loc_A_data = user1_req["location"]
    loc_B_data = user2_req["location"]
    
    pose_robot = user1_req["robot_pose"]
    pose_A = loc_A_data["pose"]
    pose_B = loc_B_data["pose"]
    
    # --- 3点間の距離を計算 ---
    # math.distは2点間のユークリッド距離を計算する
    dist_robot_to_A = math.dist([pose_robot["x"], pose_robot["y"]], [pose_A["x"], pose_A["y"]])
    dist_robot_to_B = math.dist([pose_robot["x"], pose_robot["y"]], [pose_B["x"], pose_B["y"]])
    dist_A_to_B = math.dist([pose_A["x"], pose_A["y"]], [pose_B["x"], pose_B["y"]])
    
    # --- 2つのルートの総移動距離を計算 ---
    # ルート1: 現在地 → A → B
    total_dist_route1 = dist_robot_to_A + dist_A_to_B
    
    # ルート2: 現在地 → B → A
    total_dist_route2 = dist_robot_to_B + dist_A_to_B # dist_A_to_B と dist_B_to_A は同じ
    
    print(f"📏 [RouteCalc] Route 1 (-> {loc_A_data['name']} -> {loc_B_data['name']}): {total_dist_route1:.2f}m")
    print(f"📏 [RouteCalc] Route 2 (-> {loc_B_data['name']} -> {loc_A_data['name']}): {total_dist_route2:.2f}m")
    
    # --- 総移動距離が短いルートを選択 ---
    if total_dist_route1 <= total_dist_route2:
        first = loc_A_data
        second = loc_B_data
        print(f"🏆 [Decision] Route 1 is shorter.")
    else:
        first = loc_B_data
        second = loc_A_data
        print(f"🏆 [Decision] Route 2 is shorter.")

    # 提案を作成して保持し、同意状態をリセット
    proposed_plan["first"] = first
    proposed_plan["second"] = second
    plan_confirmations.clear()
    
    # 全クライアントにプランを提案
    proposal_message = f"先に「{first['name']}」へ向かうのが最短ルートです。この順番で行きましょう！"
    await send_status_to_all_clients({
        "type": "PROPOSE_PLAN",
        "message": proposal_message,
    })
    print(f"📢 [Proposal] Sent: {proposal_message}")
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
# ★ END: この関数を全面的に書き換え                 ★
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★


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
                        destination_requests.clear()
                        plan_confirmations.clear()
                        proposed_plan.clear()
                    else:
                         await send_status_to_all_clients({"type": "WAITING_FOR_CONFIRMATION", "message": "相手の同意を待っています…"})

    except WebSocketDisconnect:
        disconnected_user_id = user_assignments.pop(websocket, None)
        kachaka_clients.discard(websocket)
        if disconnected_user_id:
            destination_requests.clear()
            plan_confirmations.clear()
            proposed_plan.clear()
            print(f"🧹 [Cleanup] All states cleared due to disconnect from {disconnected_user_id}.")
            await send_status_to_all_clients({"type": "user_disconnected", "message": f"ユーザーが切断したため、リセットされました。"})
        print(f"❌ [Disconnect] Client disconnected. Remaining: {len(kachaka_clients)}")

# (Section 2: Servo と Section 3: Server Startup は変更ありません)
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