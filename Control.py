# Control.py

import serial
import time

# --- シリアルポートの基本設定 ---
SERIAL_PORT = 'COM8'  # Windowsの場合。'COM4', 'COM5'など環境に合わせて変更

BAUDRATE = 1250000  # ICSサーボのボーレート

# グローバルなシリアルポートのインスタンス
# 複数のサーボで1つのポートを共有します
ser = None

def init_serial():
    """シリアルポートを初期化して開く関数"""
    global ser
    if ser is None or not ser.is_open:
        try:
            ser = serial.Serial(
                port=SERIAL_PORT,
                baudrate=BAUDRATE,      # 1250000 のままにする
                bytesize=8,             # データビット長 (通常8)
                parity=serial.PARITY_EVEN, # パリティ (通常None)
                timeout=0.1
            )
            print(f"シリアルポート {SERIAL_PORT} を開きました。")
        except serial.SerialException as e:
            print(f"エラー: シリアルポート {SERIAL_PORT} を開けませんでした。")
            print(f"詳細: {e}")
            ser = None # エラー発生時はNoneに戻す
            
def angle_to_position(angle):
    """
    サーバーから受け取った角度(-60 ~ +60)を
    ICSサーボの位置(3500 ~ 11500)に変換します。
    中央値を7500とします。
    """
    # -60から+60度の範囲を、サーボ位置5500から9500の範囲にマッピング
    # 7500 (中央) ± 2000 の範囲で動かす計算
    position = 7500 + (angle / 60.0) * 2000
    
    # 念のため、ICSの限界値内に収める
    return int(max(3500, min(position, 11500)))


class Control:
    """
    1つのICSサーボモーターをpyserial経由で制御するためのクラス。
    """
    def __init__(self, physical_id, name="Servo"):
        """
        サーボを初期化します。
        """
        self.physical_id = physical_id
        self.name = name
        init_serial() # クラスのインスタンス作成時にシリアルポートを初期化
        print(f"{self.name} (ID: {self.physical_id}) を準備しました。")

    def move(self, angle):
        """
        指定された角度にサーボを動かすためのICSコマンドを送信します。
        
        Args:
            angle (float): サーバーから受け取る目標の角度
        """
        global ser
        if ser is None or not ser.is_open:
            # print(f"エラー: {self.name}を動かせません。シリアルポートが開いていません。")
            return

        # 角度をICSの位置コマンドに変換
        position = angle_to_position(angle)
        
        # ICSコマンドを作成 (3バイト)
        # 1バイト目: コマンド (0x80) + サーボID
        # 2バイト目: 位置データの上位7bit
        # 3バイト目: 位置データの下位7bit
        command_byte = 0x80 | self.physical_id
        pos_high = (position >> 7) & 0x7F
        pos_low = position & 0x7F
        
        data_to_send = bytearray([command_byte, pos_high, pos_low])
        
        try:
            ser.write(data_to_send)
            # ログ表示（任意）
            # print(f"Sent to {self.name} (ID: {self.physical_id}): Position {position}")
        except Exception as e:
            print(f"エラー: {self.name}へのコマンド送信に失敗しました: {e}")