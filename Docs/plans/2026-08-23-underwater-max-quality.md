# Nirai 海中MAX品質 実装計画

> ARCHIVE。当時の実装計画。現行仕様の正本ではない。現行 World / Visual は `Docs/詳細設計/04_World.md` と tag `m0-pre-stabilization`。

**Goal:** 添付見本とほぼ一致する明るい海中空間をThree.jsで成立させ、Residentが同じ水と光の中で自然に漂い、状態ごとの水中挙動を守る画面にする。
**方針:** 負荷上限は置かず、上面水面、多層体積光、距離散乱、白砂Caustics、高密度粒子、深度を含むResident移動を重ねる。調整値は環境・移動・状態遷移の設定へ集約し、後から画質を保った最適化ができる構造にする。
**触る場所:** `world/src/renderer/src/world/environment/`、`world/src/renderer/src/world/`、`world/src/renderer/src/runtime/`、対応する `world/tests/unit/`
**検証:** `npm test`、`npm run build`、Electron実起動でのIDLE・移動・AFK・SLEEP撮影、および添付見本との目視比較。

## Task 1: MAX品質の海中環境

**目的:** 水面、光束、水の厚み、白砂、Caustics、粒子、照明を一体の空間として作り直す。

**触るファイル:**

- `world/src/renderer/src/world/environment/EnvironmentController.ts`
- `world/src/renderer/src/world/environment/UnderwaterPostProcessing.ts`
- `world/src/renderer/src/runtime/SceneRuntime.ts`
- `world/src/renderer/src/runtime/worldConfig.ts`
- `world/tests/unit/EnvironmentController.test.ts`

**手順:**

- 既存テストへ、実体水面、多層体積光、白砂設定、距離減衰を要求する失敗テストを追加する。
- 頭上へ変位する高密度水面メッシュを追加し、細かな白い反射、Fresnel、中央発光を生成する。
- 前景・中景・遠景へ異なる奥行・幅・傾きの体積光メッシュを配置し、背景側の光も補助として残す。
- 海底形状と霧・背景色を調整し、一直線の境界を消す。砂は `aerial_beach_01` を白・青方向へ補正して利用する。
- Causticsを大きく柔らかな二重ネットワークへ変更し、カメラ距離で減衰させる。
- 高密度粒子と微細な画面揺らぎ、色吸収、BloomをMAX品質向けに強化する。
- 主光源とResident露出を同じ水中照明へ統合する。

**検証コマンド:** `npm test -- EnvironmentController.test.ts`、`npm run build`

## Task 2: Residentの3D遊泳と常時FLOAT

**目的:** 固定2点往復を、画面安全域を守る立体的な遊泳へ変え、顔を見せながら漂わせる。

**触るファイル:**

- `world/src/renderer/src/world/MovementController.ts`
- `world/src/renderer/src/world/ResidentInstance.ts`
- `world/src/renderer/src/runtime/SceneRuntime.ts`
- `world/src/renderer/src/runtime/worldConfig.ts`
- `world/tests/unit/MovementController.test.ts`
- `world/tests/unit/ResidentInstance.test.ts`

**手順:**

- 3D安全域、曲線経路、速度変化、顔向き制限、個体位相を要求する失敗テストを追加する。
- 目的地を安全域内で前後・上下にも散らし、毎回異なる三次ベジェ経路を作る。
- 進行方向の影響を弱くYawへ反映し、停止時は小さな個体差を持つ正面寄りへ戻す。
- Resident本体に低周波と高周波を重ねた上下・前後左右ドリフトと微小傾きを常時付与する。
- カメラ追従ではなく、複数体へ拡張可能な個体別遊泳状態として実装する。

**検証コマンド:** `npm test -- MovementController.test.ts ResidentInstance.test.ts`、`npm run build`

## Task 3: IDLE・AFK・SLEEPの水中状態遷移

**目的:** IDLE/AFKは浮遊、SLEEPのみ接地とし、Moveから各状態へゆったり移る。

**触るファイル:**

- `world/src/renderer/src/world/ResidentInstance.ts`
- `world/src/renderer/src/runtime/SceneRuntime.ts`
- `world/tests/unit/ResidentInstance.test.ts`

**手順:**

- AFK浮遊、SLEEP接地、状態別の遷移時間、見切れ防止を要求する失敗テストを追加する。
- Move終了後に減速、Hover、Settleを挟む遷移制御を入れる。
- IDLE、AFK、SLEEPで別の浮遊高度・姿勢・接地補正を適用する。
- SLEEP要求時は安全な床位置へ移動後、ゆっくり接地してSleep Animationへ接続する。
- 途中で別指示が来た場合は古い遷移を無効化し、新しい状態へ安全に接続する。

**検証コマンド:** `npm test -- ResidentInstance.test.ts`、`npm run build`

## Task 4: 実画面の比較調整と全体確認

**目的:** 見本との差をスクリーンショットで詰め、既存機能を含む最終状態を確認する。

**触るファイル:** Task 1〜3の調整対象のみ。検証専用の一時物は終了時に削除する。

**手順:**

- Electronを起動してIDLE・移動中・AFK・SLEEPを撮影する。
- 添付見本と、水面の占有率、中央発光、青の勾配、光束の層、海底境界、Caustics密度、Resident露出を比較する。
- 色、密度、構図、カメラ、Fog、Bloomを見本優先で反復調整する。
- 全テストとビルドを新しい状態で再実行する。
- 起動した検証プロセスをすべて停止し、一時物を片付ける。

**検証コマンド:** `npm test`、`npm run build`、Electron実起動と画面撮影。
