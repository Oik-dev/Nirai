# Nirai M0海中空間 光学統合修正書

> ARCHIVE。当時の修正書。現行仕様の正本ではない。現行 World / Visual は `Docs/詳細設計/04_World.md` と tag `m0-pre-stabilization`。

## 概要

Nirai M0海中空間の光学統合修正とは、現在別々に配置されている太陽光源、光柱、水中散乱、海底Caustics、Resident照明を、1つの太陽と水の状態から生じる連続した結果へ再構成する修正である。

目的は、現在の「青灰色の空間に発光線を並べた画面」を、透明度の高い浅海で太陽光が海面を通過し、Residentと白砂の海底を自然に照らす画面へ変更することである。

- 前提条件
  - Electron、TypeScript、Three.jsの既存構成を維持する
  - Unity、Unreal Engine、追加Runtime、外部海中Assetを導入しない
  - Resident表示とEnvironment Effectの責務分離を維持する
  - Environment Effectの個別ON / OFFとlow / medium / high品質設定を維持する
  - 海面へCaustics模様を表示しない
    - 海面は波面法線、Fresnel、太陽反射で表現する
    - Causticsは海面波で屈折した光が海底へ到達した結果として表示する
  - 既存のVRM、Animation、Expression、LookAt、移動機能を変更しない

- 非目標
  - 珊瑚、岩、魚、建築物などの装飾物追加
  - FFT Oceanや海上視点の実装
  - 画面全体をBloomさせることで明るく見せる調整
  - 白砂そのものを青く塗る調整
  - M0で利用しない汎用Renderer Frameworkの新設

## 詳細仕様

### 1. 太陽系統の統一

- 共通太陽状態
  - 太陽方向、海面上の太陽中心、海面高度、中央Stage、太陽光色、太陽光量を1つの状態で管理する
  - 既存の`UnderwaterOpticsState`を拡張し、M0に必要な範囲だけを追加する
  - 海面上の太陽中心は固定座標を別途持たず、中央Stageと太陽方向から計算する

- 共通太陽状態を使用する対象
  - 上方の太陽Glow
  - 海面の反射Highlight
  - 光柱の上端
  - 水中の連続散乱
  - 海底Causticsの投影方向
  - Residentと海底を照らすDirectional Light / Spot Light

- 例外処理
  - 太陽方向のY成分が0.15未満の場合
    - 除算と極端な水平光を避けるため、Y成分を0.15として計算する
  - 海面高度がStage以下の場合
    - 光柱を生成せず、太陽GlowとCaustics強度を0にする

### 2. 光源と光柱の接続

> 2026-08-24 訂正：3D Ribbon方式は正面画角で板の輪郭と分割境界が見え、白いポリゴン扇になるため不採用とする。表示用Plane / Cone Meshを置かず、Scene深度までの視線区間を積分する解析的な体積光へ置換する。

- 光柱密度
  - 光柱本数、中心線、扇形配置を手作業で定義しない
  - 各Ray March Sample点を太陽方向へ海面まで投影し、共有海面波から得たCaustics場をその点の散乱密度として使う
  - 低周波集光場の高密度域が連続した光柱として見え、波面変化に応じて分裂・結合・移動する構成とする
  - 海底Causticsは同じ海面波から高周波模様として別に派生させ、細い網目を体積へ押し出さない
  - 中央Stageへの寄せ方は密度場への広いBiasだけで行い、白い線や板を追加しない

- 光柱形状
  - 光柱専用の表示Plane、Sprite、Cone Meshを生成しない
  - Post EffectでCameraからScene深度までをRay Marchし、各Sample点を太陽方向へ海面投影して散乱密度を計算する
  - 深度手前で積分を終了し、Residentと海底による遮蔽を維持する
  - 画素ごとのDitherでSample開始位置をずらし、段差・丸いSample痕を防ぐ
  - Henyey–Greenstein位相関数で太陽方向を向いた時の前方散乱を強める
  - 積分値は`1 - exp(-illumination)`で飽和させ、白飛びする板状領域を作らない
  - 距離減衰と最大密度を持たせ、HDR/Bloomは最も明るい密度域だけへ限定する

- Samplingとノイズ除去
  - 画面上で良好だった広い光束の入り方は、共有Caustics場、太陽方向、Ray March区間の組み合わせとして固定し、ノイズ除去のために密度式を変更しない
  - 体積光は画面の50%解像度の専用Render Targetへ計算する
  - 専用Render TargetのRへ指数飽和後の光量、AへSceneの線形深度を保存する
  - Ray Marchの規則的なSample痕は、低解像度光量に対する5 x 5のintensity-aware bilateral blurで抑える
    - 中央画素との光量差が大きいSampleは重みを下げ、光束の明暗境界を潰さない
    - 深度はblurせず、中央画素の線形深度をAへ維持する
  - Full-resolutionへ戻す際はjoint bilateral upsamplingを使う
    - high / mediumは4 x 4、lowは2 x 2の低解像度近傍を参照する
    - 画面上の距離と、Full-resolution Scene深度に対する相対深度差の両方で重み付けする
    - Residentや海底の深度境界を越えて光が漏れるSampleを除外する
  - 独自の3点平均、ランダムHash置換、光柱密度場自体の多重Sample化は採用しない

- 海面波との連動
  - 光柱上端を共有海面波でわずかに移動させる
  - 光柱の幅、明暗、位置を同じテンポで変化させる
  - 変化量は光柱の存在位置が分からなくなるほど大きくしない

- 光の結果
  - 主要光柱が到達する中央海底をSpot LightとCausticsで明るくする
  - Residentへ太陽と同方向のKey Light / Rim Lightを当てる
  - 光柱内部の水中散乱を周辺より明るくする
  - 光柱そのものだけを発光させ、周辺へ影響しない状態を禁止する

### 3. 透明で瑞々しい水

- 色設計
  - 近景
    - Residentと白砂の局所Contrastを維持する
    - 一様な青色加算を行わない
  - 中景
    - 明るいシアンを主体とする
    - 太陽域とStageの透明度を高くする
  - 遠景
    - 灰色へ落とさず、彩度のある深いコバルトへ遷移する
    - 距離によるContrast低下は維持する

- 吸収と散乱
  - Beer–Lambert吸収を継続使用する
  - 吸収による色減衰と、水中粒子による散乱色を別の設定値として扱う
  - 赤を最も強く減衰させ、青を最も透過させる
  - 散乱色の加算量を抑え、近景を色膜で覆わない

- Fog / Haze
  - FogExp2密度は浅海向けに下げる
  - Distance Hazeは遠景境界を隠す最小量にする
  - FogとDistance Hazeが同じ距離帯を二重に白濁させないようにする

- Tone Mapping
  - ACES Filmic Tone Mappingは維持する
  - Exposureは水の吸収・散乱調整後に設定する
  - Exposureだけで透明感を作らない

### 4. 白砂と青い環境反射

- 砂の地色
  - 既存のAerial Beach PBR素材を継続利用する
  - 地色は暖かい白砂として保持する
  - 一様な青色Albedoと自己発光を使用しない

- 光学特性
  - Roughnessを現在より下げ、青い上方環境光が弱く返る状態にする
  - Normal Scaleを現在より上げ、砂の細かな凹凸へ青い陰影を出す
  - 青い反射はHemisphere Light、Directional Light、Causticsで作る
  - 暗部はシアン～青、集光部は白に近い色とする

- Caustics
  - 海底だけへ表示する
  - 現在より細かな不規則性を増やす
  - 前景全体を白く覆う面積を減らす
  - 青い環境反射の上へ、白～淡いシアンの集光線を重ねる
  - 砂TextureとNormalが読めなくなる強度を禁止する

### 5. Resident表示

- Residentは中央Stageの主役として維持する
  - 顔と衣装の色を水色で塗り潰さない
  - 太陽方向のKey Lightを受ける
  - 反対側には弱い青いFill Lightを受ける
  - 足元は海底Causticsと同じ光域に置く

- Avatar交換時
  - 共通Environmentを再生成しない
  - Light、Caustics、光柱、Fogの状態を維持する

### 6. 品質とEffect分離

- Effect ON / OFF
  - `waterSurface`
  - `lightShafts`
  - `caustics`
  - `lighting`
  - `fog`
  - `suspendedParticles`
  - `luminousParticles`
  - `bubbles`

- 品質差
  - low
    - 光柱数と散乱Stepを減らす
    - 影Mapを小さくする
  - medium
    - M0標準表示とする
  - high
    - 光柱数、粒子数、散乱Step、Shadow品質を最大にする

- EffectをOFFにした場合
  - 光柱OFF
    - Post Effect内の解析的な光束密度と連続散乱を停止する
  - Caustics OFF
    - 海底Caustics MeshとPost Effect内の海底加算を両方停止する
  - Fog OFF
    - Scene Fogだけを停止し、水の距離吸収は維持する

## データ構造

| フィールド名 | データ型 | 説明 |
|---|---|---|
| `time` | `THREE.Uniform<number>` | 海面波、光柱、Causticsの共有時刻 |
| `sunDirection` | `THREE.Uniform<THREE.Vector3>` | Stageから太陽へ向かう正規化方向 |
| `surfaceY` | `THREE.Uniform<number>` | 海面高度 |
| `stageCenter` | `THREE.Uniform<THREE.Vector3>` | Resident中央Stageの基準点 |
| `sunSurfaceAnchor` | `THREE.Uniform<THREE.Vector3>` | 太陽方向と海面が交差する光柱上端の基準点 |
| `sunRadiance` | `THREE.Uniform<THREE.Color>` | 太陽、光柱、海底照明が共有するHDR光色 |
| `deepColor` | `THREE.Uniform<THREE.Color>` | 遠景の深いコバルト色 |
| `absorption` | `THREE.Uniform<THREE.Color>` | RGB別の水中吸収係数 |
| `scatteringColor` | `THREE.Uniform<THREE.Color>` | 水中粒子による散乱色 |
| `scatteringStrength` | `THREE.Uniform<number>` | 散乱色の加算強度 |

### 体積光データ

| フィールド名 | データ型 | 説明 |
|---|---|---|
| `raySteps` | `number` | 視線区間の品質別積分回数 |
| `shaftDensity` | `number` | 波面Caustics場を体積散乱へ変換する密度 |
| `maxDensity` | `number` | 指数飽和後の最大光量 |
| `anisotropy` | `number` | Henyey–Greenstein前方散乱係数 |
| `distanceAttenuation` | `number` | 海面からの深さとCamera距離による減衰 |
| `jitter` | `number` | 画素ごとのSample開始位置のずらし量 |
| `resolutionScale` | `number` | 体積光専用Render TargetのFull-resolution比率 |
| `blurSigma` | `number` | 光量差に対するbilateral blurの許容幅 |
| `upsampleNeighborhood` | `2 x 2 \| 4 x 4` | 深度対応Upsampleで参照する低解像度近傍 |
| `depthSigma` | `number` | 相対線形深度差に対するUpsample重みの許容幅 |

### サンプルデータ

| フィールド名 | 値 | 説明 |
|---|---:|---|
| `raySteps` | `22` | high品質。low / mediumは削減する |
| `shaftDensity` | `0.05` | WaterThreeJSの密度を基準にNiraiの縮尺へ調整する |
| `maxDensity` | `0.45` | 画面を白い膜にしない上限 |
| `anisotropy` | `0.72` | 水中の強い前方散乱 |
| `distanceAttenuation` | `2.0` | 遠距離で自然に弱める |
| `jitter` | `1.0` | 1 Sample幅内で開始位置を分散する |
| `resolutionScale` | `0.5` | 体積光を半解像度で生成する |
| `blurSigma` | `0.10` | 光束境界を守りつつSample痕を平滑化する |
| `upsampleNeighborhood` | `4 x 4` | medium / highの深度対応Upsample |
| `depthSigma` | `0.02` | Residentと海底の深度境界を守る相対深度閾値 |

## フロー説明

### 初期化フロー

1. 共通太陽状態を生成する
   - 太陽方向を正規化する
   - Stageと海面の交点から海面上の太陽中心を計算する
2. 海中背景と海面Materialへ共通太陽状態を渡す
3. Post Effectへ共有太陽、海面波、深度、密度設定を渡す
4. Directional LightとSpot Lightを共通太陽状態へ接続する
5. 海底MaterialとCausticsへ同じ太陽方向と光色を渡す
6. Post Effectへ吸収、散乱、太陽状態を渡す

### 1Frame更新フロー

1. 共有時刻を更新する
2. 海面波を更新する
3. 光柱上端の微小揺らぎと明暗を更新する
4. Causticsを同じ海面波で更新する
5. Resident位置とAnimationから気泡を更新する
6. Scene深度から水中距離を復元し、Full-resolutionでBeer–Lambert吸収と連続散乱を計算する
7. Half-resolutionで光柱の光量と線形Scene深度を生成する
8. Half-resolution光量をintensity-aware bilateral blurで平滑化する
9. Full-resolution Scene深度を使うjoint bilateral upsamplingで光柱を合成する
10. HDR光源だけへBloomを適用する
11. ACES Filmic Tone Mappingで出力する

### Avatar交換フロー

1. 既存Resident Avatarを破棄する
2. 新しいVRMを読み込む
3. Resident Rootを既存Stageへ配置する
4. 共通Environmentと太陽状態は維持する
5. Animation、Expression、LookAt、移動を再接続する

## 計算式

### 海面上の太陽中心

`距離 = (海面Y - StageY) ÷ max(太陽方向Y, 0.15)`

`太陽中心 = Stage中心 + 太陽方向 × 距離`

#### 計算例

- 条件
  - Stage中心: `(0, 0, -0.55)`
  - 海面Y: `4.05`
  - 太陽方向: `(-0.12, 0.72, -0.68)`
- 計算
  1. 距離: `(4.05 - 0) ÷ 0.72 = 5.625`
  2. X: `0 + (-0.12 × 5.625) = -0.675`
  3. Y: `0 + (0.72 × 5.625) = 4.05`
  4. Z: `-0.55 + (-0.68 × 5.625) = -4.375`
  5. 太陽中心: `(-0.675, 4.05, -4.375)`

### Ray Sampleの海面投影

`海面までの距離 = (海面Y - SampleY) ÷ max(太陽方向Y, 0.15)`

`海面投影点 = Sample位置 + 太陽方向 × 海面までの距離`

海面投影点で共有Caustics場を評価し、その値をSample位置の散乱密度に使う。個別光柱の中心線は定義しない。

### 水中透過

`透過率RGB = exp(-吸収係数RGB × 水中距離)`

`最終色 = 元の色 × 透過率 + 散乱色 × (1 - 透過率) × 散乱強度`

#### 計算例

- 条件
  - 赤の吸収係数: `0.075`
  - 水中距離: `10`
  - 赤の散乱色: `0.01`
  - 散乱強度: `0.55`
- 計算
  1. 赤の透過率: `exp(-0.075 × 10) ≒ 0.472`
  2. 元の赤成分が`0.8`の場合: `0.8 × 0.472 = 0.378`
  3. 赤の散乱: `0.01 × (1 - 0.472) × 0.55 ≒ 0.003`
  4. 最終赤成分: `0.378 + 0.003 = 0.381`

## 受け入れ条件

- 太陽と光柱
  - 画面上で太陽Glow中心と主要光柱上端の平均位置が、Viewport幅の3%以内に収まる
  - 光柱密度が共有海面波と太陽方向から計算され、固定`x / z / lean`や個別中心線で決定されていない
  - 高密度域と周囲の低密度散乱が連続して見える
  - 光柱が等間隔のレーザー線に見えない
  - 光柱の明暗と位置が海面波に連動して継続変化する
  - 光柱をOFFにすると白い帯がすべて消え、表示用Plane / Coneの輪郭が残らない

- 水
  - 近景のResidentと砂TextureのContrastが維持される
  - 中景が灰色ではなく明るいシアンに見える
  - 遠景が彩度のある深い青へ遷移する
  - 画面全体へ均一な青灰色膜が見えない

- 海底
  - 白砂の地色が確認できる
  - Caustics以外の領域に青い環境反射が確認できる
  - Causticsが砂Textureを全面的に白飛びさせない
  - Causticsが海面へ表示されない

- Resident
  - 顔と衣装が白飛びまたは青一色にならない
  - Avatar交換後も同じ環境光を受ける
  - stand / walk / afk / sleep、Expression、LookAt、移動が維持される

- 安定性
  - WebGL Errorが0である
  - 起動後の継続表示でEffectとAnimationが停止しない
  - MemoryとObject数が継続増加しない
  - 検証用Electron、Port 5173 / 9223、一時Scriptが終了時に残らない

- Sampling品質
  - 光柱の画面への入り方と主要な明暗領域が、採用基準画像から変わらない
  - 規則正しい格子、点列、丸いSample痕が静止画で認識できない
  - Resident輪郭と海底境界を越える光漏れが認識できない
  - 光柱OFF時はHalf-resolution計算結果が最終画面へ加算されない

## 外部参照

外部実装はコピーまたはForkせず、以下の原理だけをNirai既存Shaderへ再実装する。

| 資料 | 読み取った原理 | Niraiでの採用箇所 | 採用しないもの |
|---|---|---|---|
| WaterThreeJS `Post.js` / `common.js` | Scene深度からWorld座標を復元し、Cameraから深度までRay Marchする。各Sampleを太陽方向へ海面投影し、波面由来Caustics場を密度にする。Beer-Lambert吸収、HG前方散乱、明部だけのBloomを使う。 | `UnderwaterPostProcessing.ts`の体積光・吸収・散乱 | Sourceの定数、Shader本文、Post Chain全体のコピー |
| WaterThreeJS `Ocean.js` | 水中から見た海面は屈折方向、Fresnel、全反射の境界を持つSnell窓として扱う。細部法線は遠距離で減衰させる。 | `OpticalEnvironmentShaders.ts`の海面裏側 | 空・雲・島・泡・SSR等のNirai M0外機能 |
| three-good-godrays `godrays.frag` | 画素ごとにSample開始点をずらす。距離減衰を行い、積分光量を指数関数で飽和させる。 | `UnderwaterPostProcessing.ts`のDither、距離減衰、最大密度 | Shadow Frustum専用Renderer、Library導入、Shader本文のコピー |
| three-good-godrays `index.ts` / `bilateralFilter.frag` / `compositor.frag` | 体積光をHalf-resolutionで生成し、光量差を守るbilateral blurを行う。Rに光量、Aに線形深度を保持し、Full-resolutionでは画面距離と相対深度差によるjoint bilateral upsamplingを行う。 | `UnderwaterPostProcessing.ts`の専用体積光Pass。既存Composer内でillumination、blur、depth-aware compositeを分離する | `postprocessing` Library導入、Shadow専用Renderer、Source Shader本文と定数のコピー |
| Three.js公式 God Rays | 太陽の画面位置とScene遮蔽を一致させる。 | 共通Sun Rigと深度遮蔽の考え方のみ | 画面中心から伸ばすRadial Blurを水中光柱の主方式にしない |

- 参照先
  - https://github.com/achrefelouafi/WaterThreeJS/blob/main/src/Post.js
  - https://github.com/achrefelouafi/WaterThreeJS/blob/main/src/Ocean.js
  - https://github.com/Ameobea/three-good-godrays/blob/main/src/godrays.frag
  - https://github.com/Ameobea/three-good-godrays/blob/main/src/index.ts
  - https://github.com/Ameobea/three-good-godrays/blob/main/src/bilateralFilter.frag
  - https://github.com/Ameobea/three-good-godrays/blob/main/src/compositor.frag
  - https://threejs.org/examples/webgl_postprocessing_godrays.html
- Nirai M0検証結果
  - [M0_検証結果.md](M0_検証結果.md)
- WaterThreeJSライセンス表記
  - [THIRD_PARTY_NOTICES.md](../world/THIRD_PARTY_NOTICES.md)

※メモ：
- Half-resolution専用体積光Passは性能対策だけではなく、良好な光束形状と低Sample由来ノイズを分離して扱うため本修正で採用する
- 本修正では共有太陽状態、既存Depth Texture、既存EffectComposerを使い、新しい外部依存を追加しない
