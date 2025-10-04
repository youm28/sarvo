import serial
import time

# --- 通信設定 (この2つを色々試す) ---
SERIAL_PORT = 'COM8'
BAUDRATE = 1250000  # 最後のテスト。ダメなら 1250000 でも試す

# --- サーボ設定 ---
SERVO_ID = 7

# --- 直接送信するコマンドを作成 ---
# 中央位置 (7500) に動かすコマンドを直接作る
POSITION = 7500
COMMAND = 0x80 | SERVO_ID
POS_HIGH = (POSITION >> 7) & 0x7F
POS_LOW = POSITION & 0x7F

# 送信するバイト列
data_to_send = bytearray([COMMAND, POS_HIGH, POS_LOW])

print("--- 最小構成での直接コマンド送信テスト ---")
print(f"ポート: {SERIAL_PORT}, ボーレート: {BAUDRATE}, ストップビット: 2")
print(f"送信データ: {data_to_send.hex()}")

ser = None
try:
    # シリアルポートを開く (ストップビットは2で固定)
    ser = serial.Serial(
        port=SERIAL_PORT,
        baudrate=BAUDRATE,
        parity=serial.PARITY_EVEN,
        timeout=1
    )
    
    print("ポートを開きました。1秒後にコマンドを送信します...")
    time.sleep(1)
    
    # コマンドを書き込む
    ser.write(data_to_send)
    
    print("コマンドを送信しました。サーボが中央に動くか確認してください。")
    time.sleep(1)

except Exception as e:
    print(f"\nエラーが発生しました: {e}")

finally:
    if ser and ser.is_open:
        ser.close()
        print("ポートを閉じました。")

print("--- テスト終了 ---")