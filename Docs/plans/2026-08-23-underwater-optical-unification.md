# Nirai M0海中光学統合 実装計画

**Goal:** 太陽、光柱、水中散乱、海底Caustics、Resident照明を1つの光学状態へ統合し、透明で瑞々しい浅海表現を成立させる。
**方針:** [Nirai_M0海中空間_光学統合修正書.md](../Nirai_M0海中空間_光学統合修正書.md)を正本として、共有Sun Rigから全光学Effectを派生させる。固定Radiance Planeと3D Ribbonを撤去し、既存Depth Texture内をRay Marchして共有海面波由来の低周波集光密度を積分する。水の吸収と散乱、白砂と青い環境光を分離して調整する。
**触る場所:** `world/src/renderer/src/world/environment/`、`world/src/renderer/src/runtime/SceneRuntime.ts`、`world/tests/unit/`、`Docs/M0_検証結果.md`
**検証:** 対象Unit TestをRED→GREENで実行し、全Vitest、TypeScript、Build、Electron実画面比較、WebGL Error、短時間継続表示、Portと一時物の棚卸しを通す。

## Task 1: 共有Sun Rigと座標契約

**目的:** 太陽Glow、光柱、Caustics、照明が同じ海面上の太陽中心を参照する状態を作る。

**触るファイル:**

- `world/src/renderer/src/world/environment/UnderwaterOptics.ts`
- `world/src/renderer/src/world/environment/EnvironmentController.ts`
- `world/src/renderer/src/world/environment/OpticalEnvironmentShaders.ts`
- `world/src/renderer/src/world/environment/UnderwaterPostProcessing.ts`
- `world/tests/unit/UnderwaterOptics.test.ts`
- `world/tests/unit/EnvironmentController.test.ts`

**手順:**

- 海面上の太陽中心をStage、海面高度、太陽方向から計算する失敗テストを追加する。
- `UnderwaterOpticsState`へ`sunSurfaceAnchor`、`sunRadiance`、吸収・散乱設定を追加する。
- 光柱、Lighting、Caustics、背景、Post Effectへ同じUniform参照を渡す。
- 太陽方向Yが小さい場合と海面がStage以下の場合の安全処理を実装する。
- 共有時刻とEffect ON / OFFの既存契約を維持する。

**検証コマンド:**

- `rtk npm test -- --run tests/unit/UnderwaterOptics.test.ts tests/unit/EnvironmentController.test.ts`
- `rtk npx tsc --noEmit`

## Task 2: 光柱を深度対応の解析的体積光へ再構築

**目的:** 等間隔の発光線と白いポリゴン扇を廃止し、太陽中心から海底へ広がる連続した水中散乱を作る。

**触るファイル:**

- `world/src/renderer/src/world/environment/EnvironmentController.ts`
- `world/src/renderer/src/world/environment/UnderwaterPostProcessing.ts`
- `world/tests/unit/EnvironmentController.test.ts`
- `world/tests/unit/UnderwaterPostProcessing.test.ts`

**手順:**

- 表示用光柱Meshが0件で、Post Effectが海面投影・Dither・HG散乱・指数飽和を持つ失敗テストを追加する。
- 既存の固定`x / z / lean`Radiance Planeと3D Ribbonを削除する。
- 表示用Plane / Coneを使わず、Ray Sampleを太陽方向へ海面投影し、共有海面波の低周波集光場を散乱密度にする。
- 画素ごとのDither、HG前方散乱、距離減衰、指数飽和を実装する。
- 波面密度の高低で光束が自然に分裂・結合し、個別の本数や中心線を持たない構成にする。
- Post Effectの連続散乱を主要光域へ寄せ、離散サンプル痕を再発させない。

**検証コマンド:**

- `rtk npm test -- --run tests/unit/EnvironmentController.test.ts tests/unit/UnderwaterPostProcessing.test.ts`
- `rtk npx tsc --noEmit`

## Task 3: 透明な水と青い白砂反射

**目的:** 青灰色の膜を減らし、近景の透明度、明るいシアンの中景、深いコバルトの遠景を作る。

**触るファイル:**

- `world/src/renderer/src/world/environment/UnderwaterOptics.ts`
- `world/src/renderer/src/world/environment/OpticalEnvironmentShaders.ts`
- `world/src/renderer/src/world/environment/UnderwaterPostProcessing.ts`
- `world/src/renderer/src/world/environment/EnvironmentController.ts`
- `world/src/renderer/src/runtime/SceneRuntime.ts`
- `world/tests/unit/EnvironmentController.test.ts`
- `world/tests/unit/UnderwaterPostProcessing.test.ts`

**手順:**

- Fog、砂Material、吸収・散乱の受入値を要求する失敗テストを追加する。
- Fog密度を浅海向けへ下げ、Distance Hazeを遠景境界だけへ限定する。
- 背景をシアンからコバルトへ遷移する高彩度Gradientへ調整する。
- Post Effectで吸収と散乱を別Uniformにし、散乱加算を抑える。
- Tone Mapping Exposureは光学調整後に0.95～1.05の範囲で確定する。
- 砂の一様Emissiveを撤去し、暖かい白砂、適度なRoughness、強めたNormalへ変更する。
- Hemisphere / Directional / Causticsを白～シアンへ再配色し、青い環境反射を照明結果として作る。
- Causticsの白飛び面積を抑え、砂Textureが読める強度へ調整する。

**検証コマンド:**

- `rtk npm test -- --run tests/unit/EnvironmentController.test.ts tests/unit/UnderwaterPostProcessing.test.ts`
- `rtk npx tsc --noEmit`
- `rtk npm run build`

## Task 4: Electron実機比較とM0回帰確認

**目的:** 修正書の見た目と既存M0機能を実画面で確認し、証跡と後片付けまで完了する。

**触るファイル:**

- `Docs/M0_検証結果.md`
- `Docs/evidence/underwater-optical-unification/`
- 実装Taskで変更したSource / Testのみ

**手順:**

- Electron Production BuildをPort 9223付きで一時起動する。
- 太陽と光柱の上端、光柱の幅、海の彩度、砂Texture、青い反射、Causticsを参考画像と比較する。
- Effectを個別にOFFにして、光柱、Caustics、粒子、気泡の責務が混ざっていないことを確認する。
- VRM表示、Avatar交換、stand / walk / afk / sleep、Expression、LookAt、移動の回帰を確認する。
- 短時間の継続表示でWebGL Error、Heap、Process Memory、DOM / Listener増殖を確認する。
- 全Vitest、TypeScript、Production Buildを最終状態で再実行する。
- 最終画像、診断JSON、継続表示結果を証跡へ保存し、`M0_検証結果.md`へ追記する。
- 検証用Electron、Port 5173 / 9223、一時Scriptを停止・削除する。
- この実装計画を削除し、恒久修正書は残す。

**検証コマンド:**

- `rtk npm test`
- `rtk npx tsc --noEmit`
- `rtk npm run build`
- Electron実機のCDP診断で`webglError = 0`
- `Get-NetTCPConnection -LocalPort 5173,9223`で残置なし
