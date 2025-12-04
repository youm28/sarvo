import asyncio
import json
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from Control import Control
import kachaka_api
import threading
import time
from concurrent.futures import ThreadPoolExecutor

KACHAKA_IP = "10.40.5.108"
app = FastAPI()
kachaka_client: kachaka_api.KachakaApiClient = None

# =================================================================
# Section 1: Kachaka ロボット制御関連
# =================================================================
kachaka_command_queue = deque()
kachaka_clients = set()
kachaka_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=1)

user_assignments = {}
current_location_name = "充電ドック" 
current_moving_location = None
current_destination_selector = "user_1" 

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

async def broadcast_connection_status():
    is_user1_present = "user_1" in user_assignments.values()
    is_user2_present = "user_2" in user_assignments.values()
    is_ready = is_user1_present and is_user2_present

    message = {
        "type": "connection_status",
        "ready": is_ready,
        "user1": is_user1_present,
        "user2": is_user2_present,
        "destination_selector": current_destination_selector 
    }
    await send_status_to_all_clients(message)

async def start_move_to_destination(target_location):
    global kachaka_client
    destination_name = target_location["name"]
    print(f"🧐 [Plan] START: '{current_location_name}' -> GOAL: '{destination_name}'")
    try:
        if not kachaka_client: return
        locations = kachaka_client.get_locations()
        location_dict = {loc.name: loc for loc in locations}
        if destination_name in location_dict:
             dest_loc = location_dict[destination_name]
             final_dest_data = {"id": dest_loc.id, "name": dest_loc.name}
        else:
             print(f"🔥 Destination '{destination_name}' not found!")
             return
        message = f"{destination_name} へ向かいます！"
        await send_status_to_all_clients({"type": "STARTING_MOVE", "message": message})
        await asyncio.sleep(1)
        with kachaka_lock:
            kachaka_command_queue.append(final_dest_data)
    except Exception as e:
        print(f"🔥 Process Error: {e}")

def kachaka_move_sync(location_id, location_name):
    global kachaka_client
    try:
        print(f"🤖 [Move] Trying to go to '{location_name}'...")
        timeout = 0
        while kachaka_client.is_command_running():
            time.sleep(0.5)
            timeout += 1
            if timeout > 10: 
                print("⚠️ Force starting new command...")
                break
        kachaka_client.move_to_location(location_id)
        time.sleep(1) 
        while kachaka_client.is_command_running():
            time.sleep(0.5)
        print(f"✅ [Move] Finished command for '{location_name}'.")
        return True 
    except Exception as e:
        print(f"🔥 [Move] Exception: {e}")
        return True 

async def process_kachaka_queue():
    global kachaka_client, current_location_name, current_moving_location, current_destination_selector
    current_move_future = None
    while True:
        try:
            if not kachaka_client:
                await asyncio.sleep(1); continue
            
            if current_move_future and current_move_future.done():
                if current_moving_location:
                    old_loc = current_location_name
                    new_loc = current_moving_location.get("name")
                    current_location_name = new_loc
                    print(f"📍 [Update] Location changed: '{old_loc}' -> '{new_loc}'")
                current_moving_location = None
                
                swap_triggers = ["1", "2", "3", "4", "5", "6"]
                if current_location_name in swap_triggers:
                    current_destination_selector = "user_2" if current_destination_selector == "user_1" else "user_1"
                    print(f"🔄 [Role Swap] Arrived at {current_location_name}. Destination Selector is now: {current_destination_selector}")
                else:
                    print(f"➡️ [Continue] Arrived at {current_location_name}. No role swap.")

                await send_status_to_all_clients({
                    "type": "kachaka_status", 
                    "status": "idle", 
                    "message": "",
                    "current_location": current_location_name,
                    "destination_selector": current_destination_selector
                })
                current_move_future = None

            if not current_move_future and not kachaka_client.is_command_running():
                with kachaka_lock:
                    if kachaka_command_queue:
                        location_data = kachaka_command_queue.popleft()
                        current_moving_location = location_data
                        await send_status_to_all_clients({"type": "kachaka_status", "status": "moving", "destination": location_data["name"]})
                        loop = asyncio.get_event_loop()
                        current_move_future = loop.run_in_executor(executor, kachaka_move_sync, location_data["id"], location_data["name"])

        except Exception as e:
            print(f"🔥 Queue Error: {e}")
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
    
    print(f"✅ [Connect] {user_id}. Sending Location: {current_location_name}")
    init_msg = ""
    if user_id == current_destination_selector:
        init_msg = "どこに行きますか？"
    else:
        init_msg = "パートナーが目的地を選ぶのを待っています..."

    await websocket.send_json({
        "type": "user_assigned", 
        "user_id": user_id,
        "message": init_msg,
        "current_location": current_location_name,
        "destination_selector": current_destination_selector 
    })
    await broadcast_connection_status()

    try:
        while True:
            data = await websocket.receive_json()
            print(f"📨 [{user_id}] Received: {data}")
            action = data.get("action")
            if action == "REQUEST_DESTINATION":
                if user_id != current_destination_selector:
                     await websocket.send_json({"type": "ERROR", "message": "現在あなたのターンではありません。"})
                     continue
                partner_id = "user_2" if user_id == "user_1" else "user_1"
                if partner_id not in user_assignments.values():
                     await websocket.send_json({"type": "ERROR", "message": "パートナーがいません。"})
                     continue
                if current_moving_location:
                    await websocket.send_json({"type": "ERROR", "message": "移動中です。"})
                    continue
                target_loc = data.get("location")
                await start_move_to_destination(target_loc)

    except WebSocketDisconnect:
        u_id = user_assignments.pop(websocket, None)
        kachaka_clients.discard(websocket)
        if u_id:
            print(f"❌ [Disconnect] {u_id}")
            await send_status_to_all_clients({"type": "user_disconnected", "message": "リセットされました"})
            await broadcast_connection_status()

# =================================================================
# Section 2: Servo Motor Control
# =================================================================

# 定義
# Right Set (User 1)
servoHorizontalRight = Control(physical_id=5, name="HRight Servo")
servoVerticalRight = Control(physical_id=7, name="VRight Servo")
# Left Set (User 2)
servoHorizontalLeft = Control(physical_id=13, name="HLeft Servo")
servoVerticalLeft = Control(physical_id=9, name="VLeft Servo")

# ユーザーIDとサーボのマッピング（固定）
USER_SERVO_MAP = {
    "user_1": {
        "horizontal": servoHorizontalRight,
        "vertical": servoVerticalRight
    },
    "user_2": {
        "horizontal": servoHorizontalLeft,
        "vertical": servoVerticalLeft
    }
}

# 物理IDごとの現在の角度
current_angles = {5: 0, 7: 0, 13: 0, 9: 0}
# 物理IDごとの動作状態 ('stop', 'increase', 'decrease')
movement_states = {5: "stop", 7: "stop", 13: "stop", 9: "stop"}

servo_lock = threading.Lock()

def move_servo(physical_id, servo_instance, angle):
    with servo_lock:
        if servo_instance:
            # 角度制限 (-40 ~ 40)
            angle = max(-40, min(angle, 40))
            servo_instance.move(angle)
            current_angles[physical_id] = angle

def servo_thread_loop():
    while True:
        try:
            with servo_lock:
                # 辞書をコピーして反復処理中の変更を防ぐ
                states = dict(movement_states)
            
            # 全サーボの状態を見て動かす
            for physical_id, direction in states.items():
                if direction == "stop":
                    continue
                
                # 対象のサーボインスタンスを探す
                target_servo = None
                # 全探索
                if physical_id == 7: target_servo = servoHorizontalRight
                elif physical_id == 5: target_servo = servoVerticalRight
                elif physical_id == 9: target_servo = servoHorizontalLeft
                elif physical_id == 13: target_servo = servoVerticalLeft
                
                if target_servo:
                    current_angle = current_angles.get(physical_id, 0)
                    step = 0.4  # 移動速度
                    
                    
                    # 垂直方向のサーボ（ID 7 または 9）かどうかを判定
                    is_vertical = (physical_id == 7 or physical_id == 9)

                    if direction == "increase":
                        if is_vertical:
                            current_angle -= step  # 【反転】垂直なら引く
                        else:
                            current_angle += step  # 水平なら足す（通常通り）
                            
                    elif direction == "decrease":
                        if is_vertical:
                            current_angle += step  # 【反転】垂直なら足す
                        else:
                            current_angle -= step  # 水平なら引く（通常通り）
                    
                    move_servo(physical_id, target_servo, current_angle)
                    
        except Exception as e:
            print(f"Servo Loop Error: {e}")
            
        time.sleep(0.01) # 100Hz制御

@app.websocket("/ws/servo")
async def websocket_servo_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("✅ Servo Client Connected")
    try:
        while True:
            data = await websocket.receive_json()
            
            # ★ デバッグ用: コマンド受信ログを追加
            print(f"📨 Servo Command: {data}")

            user_id = data.get("user_id")
            axis = data.get("axis") 
            command = data.get("command") 

            if user_id not in USER_SERVO_MAP:
                print(f"⚠️ Unknown User: {user_id}")
                continue

            target_servos = USER_SERVO_MAP[user_id]
            target_servo = target_servos.get(axis)
            
            if target_servo:
                p_id = target_servo.physical_id
                with servo_lock:
                    movement_states[p_id] = command
            else:
                 print(f"⚠️ Axis not found: {axis}")
            
    except WebSocketDisconnect:
        print("❌ Servo Client Disconnected")
    except Exception as e:
        print(f"Servo WS Error: {e}")

@app.on_event("startup")
async def startup_event():
    global kachaka_client
    print("🚀 Server Starting (Direct Destination Mode)...")

    print("⚙️ Initializing Servos to Origin (0)...")
    try:
        # 定義されている全サーボをリスト化
        initial_servos = [
            (5, servoHorizontalRight),
            (7, servoVerticalRight),
            (13, servoHorizontalLeft),
            (9, servoVerticalLeft)
        ]
        
        # 順番に0度へ移動させる
        for p_id, servo in initial_servos:
            move_servo(p_id, servo, 0)
            
        # 念のため物理的な移動時間を待つ
        time.sleep(0.5)
        
    except Exception as e:
        print(f"⚠️ Servo Init Error: {e}")
        
    threading.Thread(target=servo_thread_loop, daemon=True).start()
    try:
        kachaka_client = kachaka_api.KachakaApiClient(f"{KACHAKA_IP}:26400")
        print(f"✅ Connected to Kachaka! Ver: {kachaka_client.get_robot_version()}")
    except Exception as e:
        print(f"🔥 Kachaka connect failed: {e}")
    
    asyncio.create_task(process_kachaka_queue())
    print("✅ Server Ready")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)