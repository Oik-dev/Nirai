# Nirai 詳細設計 09：3DビジュアルとAvatarパイプライン

正本は [Nirai_基本設計.md](../Nirai_基本設計.md)。World実装は [04_World.md](04_World.md)。Resident設定は [06_Residentと記憶.md](06_Residentと記憶.md)。

## 1. 目的

本章は、Residentの身体となるVRM Avatarと海中Worldの3D表現に関する共通規格を定める。

最優先は、Masterや実装AIが3Dモデルごとの専用作業を増やさず、既存規格と既存OSSでAvatarを交換できること。

## 2. Avatar標準形式

Niraiの標準Avatar形式はVRMとする。

初期対象：

- VRM 0.x
- VRM 1.0

VRMのロード・Humanoid・Expression・LookAt・SpringBone等は`@pixiv/three-vrm`を中心に扱う。

Animationはpixiv公式`@pixiv/three-vrm-animation`を利用してVRMAを読み込む。AITuberKit内の独自`src/lib/VRMAnimation` Sourceはコピーせず、公式Packageで同じ問題を解く。

### 対象外

- UnityPackage / FBXをAvatar入力として自動変換すること
- PMX / PMDの直接読込
- VRC固有Prefab / PhysBone / Contact / Expression Menuの完全再現
- Nirai独自Avatar Runtime形式

World Runtimeと設定UIのAvatar入力はVRMだけに固定する。UnityPackage自動変換はMaterial・Shader・Humanoid差の再現コストがM2の目的に見合わないため対象外とする。必要なAvatarは事前にVRM化されたものを利用する。

## 3. AITuberKitとの関係

AITuberKitはVRM Avatarの実運用例として主要ベンチマークにする。

参考対象：

- VRMロード
- Modelのライフサイクル
- Animation / Pose
- Expression
- LookAt
- LipSync
- TTSとの同期
- Three.js Rendererとの統合

ただしAITuberKitをForkしない。AITuberKit固有コードをコピー・改変して利用しない。

Niraiは採用OSSの公開APIと仕様を直接利用し、Nirai固有のResidentManagerや海中Worldへ最適化した独自コードとして実装する。

## 4. 責務分離

外部VRMが提供するもの：

- Mesh
- Material
- Humanoid Skeleton
- Expression
- LookAt情報
- SpringBone等、VRMに含まれるAvatar情報
- Avatar固有の外見

Niraiが提供するもの：

- 共通Animation
- 移動
- 行動状態
- 意味的なExpression指示
- 発話テキスト
- TTS / LipSync制御
- 吹き出し
- World内Location

同じResidentへ別Avatarを割り当てても、persona、World Memory、Private Memory、Brainへ影響しない。

## 5. Avatar登録

`D:\Products\Nirai\avatars\`をNiraiのAvatar管理Rootとする。サブフォルダ利用は許可する。

設定UIの`Avatar読込 / Avatar変更`からWindows File Pickerを開く。初期表示は`D:\\Products\\Nirai\\avatars\\`とし、`.vrm`だけを1つ選択できる。Resident設定には`avatars\\`からの**VRM相対パスだけ**を保存する。

```text
avatars\
  Holo.vrm
  Alice\
    Alice.vrm
    avatar.toml   # 補正が必要な場合だけ
```

`avatar.toml`は任意とし、VRMに含まれないNirai固有補正が必要な場合だけ、選択VRMと同じフォルダへ置く。

例：

```toml
height_scale = 1.0
ground_offset = 0.0
bubble_anchor = [0.0, 1.7, 0.0]
look_anchor = [0.0, 1.55, 0.0]
```

VRMファイル自身から取得できる情報をTOMLへ二重保存しない。Residentを削除しても`avatars\`配下のVRM本体は削除しない。

## 6. 読込フロー

Rendererから任意の`file://` URLを開かず、Electron MainでVRMを選択し、`avatars\\`配下Pathと拡張子を検証する。UnityPackage / FBX変換経路は持たない。

通常VRM Load：

```text
Resident configのavatars相対Path
  ↓
Main: Root/拡張子検証 + fs.readFile
  ↓ Uint8Array
Renderer VrmLoader
  ↓
GLTFLoader + VRMLoaderPlugin + parseAsync
  ↓
@pixiv/three-vrm
  ↓
Nirai VRM Runtime
  ↓
ResidentInstance
```

ロード時に確認する：

- VRMとして正常に読める
- 規格バージョンを識別できる
- Humanoidが取得できる
- 表示が破綻しない
- 接地補正後に海底へ自然に立つ
- Expressionが利用可能なら取得する
- LookAtが利用可能なら使う

一部機能が無いAvatarでも、身体として表示可能なら即エラーにしない。機能ごとに縮退する。

VRM 0.xは`VRMUtils.rotateVRM0()`で向きを補正する。Avatar変更時はLoad Generationを比較し、古い非同期Load結果を採用しない。Unload時はSceneからremoveした後に`VRMUtils.deepDispose()`を行い、VRM交換を繰り返してもGPU Resourceを残さない。

## 7. Animation

基本AnimationはNirai側の共通資産とする。

- stand（LOCOMOTIONのIdle）
- walk（旧Move B内部の基礎Clip）
- afk（IDLE / AFK）
- sleep

通常移動の見た目は2026-08-26変更前の旧Move Bそのものを基準とする。Move A/Bは目的地以外の挙動を分けず、旧Move Bで成立していた`walk`基礎Clip、低いBank、経路・速度、水中骨格Overlay、追加脚振りの組み合わせを維持する。`walk`は製品上の独立した「歩行モード」を意味しない。Avatar専用Animationを前提にしない。

`gesture / talk`は対応Asset追加後の将来拡張とする。`sit / stretch / think`は実装予定に含めない。

### 原則

1. 共通Animation AssetはVRMAを優先する
2. `VRMAnimationLoaderPlugin`でVRMAをLoadする
3. `gltf.userData.vrmAnimations[0]`を取得する
4. `createVRMAnimationClip(vrmAnimation, vrm)`で対象VRM用Clipを作る
5. `THREE.AnimationMixer(vrm.scene).clipAction(clip)`で再生する
6. 独自Retargeterは最後の手段とする

確認項目：

- Move A/Bが2026-08-26変更前の旧Move Bと同じ見た目・テンポであること
- A/Bで差があるのは目的地だけで、経路・速度・姿勢・Animation・Overlayが分岐しないこと
- 腕や髪の極端な貫通
- move / stand遷移
- afk / sleepが意味として読める
- Avatar交換後も同じAnimation Setが利用できる

## 8. Expression

Niraiは表情を意味名で扱う。

代表例：

- neutral
- happy
- angry
- sad
- relaxed
- surprised
- awkward
- doubt
- blink
- 発話口形

VRM標準Expressionを優先する。

VRM 0.x / 1.0の内部差はVRM Runtime側で吸収し、Core Protocolへバージョン差を露出させない。VRM0の`joy / sorrow / fun`は`happy / sad / relaxed`として扱う。Avatar固有の同義Expressionが複数ある場合もCoreは意味名だけを指定し、World側で候補へ解決する。

Avatar固有差を補う設定が本当に必要な場合だけ`avatar.toml`等へ最小限追加する。最初から独自BlendShape Mapping表を全Avatarへ要求しない。

## 9. LookAt / SpringBone

- LookAtはVRM Runtimeの標準機能を優先する
- `face(master)`、`face(Resident名)`はNiraiの意味コマンドとしてWorldが対象座標へ変換する
- 髪・服等の揺れはVRMに含まれるSpringBone情報を利用する
- VRC PhysBoneの完全互換をNirai側で実装しない

## 10. LipSync

LipSyncはTTS音声再生に付随するWorld表現である。

- テキスト発話はLipSyncが無くても成立する
- Mute中は音声LipSyncを行わない
- 現行Amplitude → `aa`方式はfallbackとして維持する
- 後続SliceではMFCC / FFT / Mel Filter Bank / DCT等の一般的な信号処理をNirai独自コードで実装し、TTS Voice ProfileからA/I/U/E/Oを推定してVRM `aa / ih / ou / ee / oh`へ反映する
- AIAvatarKit / uLipSync等のSource Codeはコピーせず、成立している機能要件と一般公開アルゴリズムだけを参考にする
- 詳細な独自実装仕様・Profile生成・fallback・受入は [13_AIAvatarKit参考カンペと独自実装Slice.md](13_AIAvatarKit参考カンペと独自実装Slice.md) を正とする
- VRMの口形Expressionを利用する

## 11. BOOTH等のAvatar利用方針

BOOTH等で配布されるAvatarには、UnityPackage / FBX / blend / VRM等が同梱される場合がある。

NiraiではVRMが用意されているAvatarだけを直接利用対象とする。VRMが無いAssetをNirai内部で自動変換することはしない。必要なら外部の既存変換手段で事前にVRM化し、その完成VRMを`avatars\\`へ置く。

これにより共通Animation / Expression / LookAt / LipSync等のWorld側パイプラインをVRM一本に保つ。元Assetの利用条件・再配布条件は形式変換によって変更されない。

## 12. 海中Environment

主要要素：

- WaterSurface
- Caustics
- LightShafts
- Fog
- Bubbles
- SuspendedParticles
- Seabed

原則：

- Residentの存在感を環境の情報量より優先する
- 演出は緩やかに変化する
- 物理的な正確さより自然な見た目と低負荷を優先する
- 既存Three.js向けShader、Effect、OSS、実装例を優先する
- Nirai専用の流体シミュレーションを作らない
- Effectごとに品質を下げられる

## 13. 品質確認

Avatar：

- VRM読込成功
- Material表示
- 接地
- 共通Animation
- Expression
- LookAt
- SpringBone
- LipSync
- Avatar交換

World：

- 水面
- Caustics
- 光条
- Fog
- 気泡
- 粒子
- 海底
- Residentへの光と影
- 長時間表示時の負荷

M0では複数のMaster所有VRMで確認する。特定有償AvatarをNirai本体へ同梱しない。

## 14. 実装停止条件

次の状況では新規独自実装へ進む前に停止する。

- VRMロードで詰まった
- Expression名差で詰まった
- Animation Retargetで詰まった
- LipSyncで詰まった
- Three.js海中Effectで詰まった

まずAITuberKit、VRM公式仕様、`@pixiv/three-vrm`公式例、既存OSSを確認する。

既存解があるのにNirai専用実装を作ることを避ける。
