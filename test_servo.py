import time
from Control import Control

# --- このテスト用の設定 ---
# 実際に動かしたいサーボのIDを指定
SERVO_ID = 7

print(f"--- サーボ単体テストを開始します (ID: {SERVO_ID}) ---")

# Controlクラスのインスタンスを作成
# これによりシリアルポートが開かれるはず
try:
    servo = Control(physical_id=SERVO_ID)
except Exception as e:
    print(f"サーボの初期化中にエラーが発生しました: {e}")
    print("テストを終了します。")
    exit()

print("\nテスト開始！サーボが動くか確認してください。")

try:
    # 1. まずは中央 (0度) へ
    print("1. 0度 (中央) へ移動します...")
    servo.move(0)
    time.sleep(2) # 2秒待機

    # 2. 次に +45度 へ
    print("2. +45度へ移動します...")
    servo.move(45)
    time.sleep(2) # 2秒待機

    # 3. 次に -45度 へ
    print("3. -45度へ移動します...")
    servo.move(-45)
    time.sleep(2) # 2秒待機

    # 4. 最後に中央へ戻る
    print("4. 0度 (中央) へ戻ります...")
    servo.move(0)
    time.sleep(1)

    print("\n--- テストが正常に完了しました ---")

except Exception as e:
    print(f"\nテスト中にエラーが発生しました: {e}")