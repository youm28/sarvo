import kachaka_api
import sys
import os

# IPアドレスとポート
KACHAKA_IP = "10.40.42.28"
KACHAKA_PORT = 26400

# 保存するファイル名
OUTPUT_FILENAME = "map_image_default.png"

def main():
    try:
        client = kachaka_api.KachakaApiClient(f"{KACHAKA_IP}:{KACHAKA_PORT}")
    except Exception as e:
        print(f"🔥 クライアントの初期化に失敗しました: {repr(e)}")
        sys.exit(1)
    
    # -----------------------------------------------------------------
    # ステップ1: 接続テスト
    # -----------------------------------------------------------------
    print(f"🤖 {KACHAKA_IP} に接続中...")
    try:
        version = client.get_robot_version() 
        print(f"✅ 接続成功！ (ロボットバージョン: {version})")
    except Exception as e:
        error_str = repr(e)
        if "Not ready" in error_str or "UNAVAILABLE" in error_str:
            print(f"🔥 接続失敗: ロボットがビジー状態です (Not ready)。")
        else:
            print(f"🔥 接続失敗: {error_str}")
        sys.exit(1)

    # -----------------------------------------------------------------
    # ステップ2: 目的地（ロケーション）一覧を取得 (★ 新規追加 ★)
    # -----------------------------------------------------------------
    print(f"\n📍 目的地（ロケーション）一覧を取得します...")
    try:
        # .get() なしで呼び出す
        locations = client.get_locations() 
        
        if not locations:
            print("  > 登録されているロケーションがありません。")
        else:
            print(f"  > 合計 {len(locations)} 箇所のロケーションが見つかりました：")
            for loc in locations:
                # ロケーション名、ID、座標を表示
                print(f"    - {loc.name} (ID: {loc.id})")
                print(f"      > Pose: (x={loc.pose.x:.2f}, y={loc.pose.y:.2f})")
    
    except Exception as e:
        print(f"🔥 ロケーション情報の取得に失敗しました: {repr(e)}")
        # マップ取得は続行するため、ここでは exit しない

    # -----------------------------------------------------------------
    # ステップ3: PNGマップ応答を取得する (旧ステップ2)
    # -----------------------------------------------------------------
    print(f"\n🖼️  現在アクティブなマップの描画情報を取得します...")
    map_image_pb = None
    
    try:
        map_image_pb = client.get_png_map() 
        print(f"✅ 描画情報を取得しました。")
            
    except Exception as e:
        print(f"🔥 描画情報の取得に失敗しました: {repr(e)}")
        sys.exit(1)

    # -----------------------------------------------------------------
    # ステップ4: 取得したマップ情報を表示 (旧ステップ3)
    # -----------------------------------------------------------------
    if map_image_pb:
        print("\n--- 取得したマップ描画情報 ---")
        try:
            print("  [メタデータ]")
            print(f"  解像度 (m/pixel): {map_image_pb.resolution:.4f}")
            print(f"  幅 (pixels):      {map_image_pb.width}")
            print(f"  高さ (pixels):      {map_image_pb.height}")
            print("  [原点座標 (m)]")
            print(f"  Origin X: {map_image_pb.origin.x:.4f}")
            print(f"  Origin Y: {map_image_pb.origin.y:.4f}")
            print("-" * 20)
            print("  [PNGデータ]")
            print(f"  データ長 (bytes):   {len(map_image_pb.data)}")
            
            with open(OUTPUT_FILENAME, "wb") as f:
                f.write(map_image_pb.data)
            print(f"\n✅ PNGデータを '{OUTPUT_FILENAME}' として保存しました。")
            
        except Exception as e:
            print(f"🔥 取得したオブジェクトの解析または保存に失敗しました: {repr(e)}")

if __name__ == "__main__":
    main()