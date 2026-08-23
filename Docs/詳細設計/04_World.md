# Nirai 詳細設計 04：World

正本は [Nirai_基本設計.md](../Nirai_基本設計.md)。行動コマンド語彙の正は [01_通信プロトコル.md](01_通信プロトコル.md)。Avatar規格の正は [09_3DビジュアルとAvatarパイプライン.md](09_3DビジュアルとAvatarパイプライン.md)。

## 1. 責務

Worldは、Electron + Three.jsで動くNiraiの3Dクライアントである。Coreから届く意味的な行動と発話テキストを、Residentの身体・Animation・表情・音声・UI・環境演出へ変換する。

担当するもの：

- 海中3D Worldの描画
- VRM Residentの読込・表示
- Animation
- Expression
- LookAt
- LipSync
- TTS
- 移動
- カメラ
- 会話UI
- 環境演出

担当しないもの：

- AIの判断
- 人格
- World Memory / Private Memoryの正本
- Brain呼び出し
- 会話の調停
- タスク判断

前提：

- Windows上の通常のデスクトップアプリとして動作する
- 通常FPS上限はconfig.tomlの`world.fps`（既定30）
- Coreと切断中も風景・アイドル演出は動き続ける
- 解像度はメインモニタを基準とする（マルチモニタは初期対象外）

## 2. AITuberKitをベンチマークにする範囲

AITuberKitは、Three.js上でのVRM表示・Animation・Expression・LookAt・LipSync・TTS・WebSocket外部連携・Electronデスクトップ化が成立している先行実装として扱う。

実装時は以下を優先する。

1. AITuberKitが採用しているOSSや一般的なThree.js / VRMの実装方法を確認する
2. Nirai要件にそのまま適用できる既存OSSがあれば利用する
3. Nirai固有要件だけを新規実装する

AITuberKit自体はForkしない。AITuberKit固有コードをコピー・改変してNiraiへ持ち込まない。Niraiは`three`、`@pixiv/three-vrm`等の公開APIを直接利用した独自コードとする。

Nirai固有として主に新規実装する領域は、海中World、複数Residentを管理するResidentManager、CoreとのNirai Protocol、Resident同士の位置・会話表現である。

## 3. World構成

概念構成：

```text
Electron App
├ Main Process
│ ├ Window lifecycle
│ ├ Windows file dialog / 既定アプリ起動
│ ├ avatars配下VRMの安全な読込
│ └ VOICEVOX HTTP Client
├ Preload
│ └ Rendererへ`window.nirai`の必要最小限IPC APIだけ公開
└ Renderer (Vite + React + TypeScript)
  ├ SceneRuntime
  │ ├ Three.js Scene / Camera / Renderer / RenderLoop
  │ ├ EnvironmentController
  │ │ ├ WaterSurface
  │ │ ├ Seabed
  │ │ ├ Caustics
  │ │ ├ LightShafts
  │ │ ├ UnderwaterFog
  │ │ ├ BubbleSystem
  │ │ └ SuspendedParticles
  │ ├ LocationRegistry
  │ └ ResidentManager
  │   └ ResidentInstance × N
  ├ CoreConnection
  ├ AudioService / SpeechQueue / TtsService
  ├ Zustand Stores
  └ React UI
```

Three.jsのScene GraphとUI DOMは分離する。会話UIを3D Meshとして再実装しない。

RendererからNode.js / File System / Process操作を直接公開しない。VRM選択、`persona.md`をWindows既定エディタで開く操作、VOICEVOX HTTP通信はPreload IPCを介してMain Processへ依頼する。BrowserWindowは`nodeIntegration=false`、`contextIsolation=true`、`webSecurity=true`を必須とする。

RendererはReact + ZustandをUIに利用するが、`THREE.Scene`、`VRM`、`AudioContext`等のRuntime ObjectをReact Stateへ格納しない。3D Runtimeは通常のTypeScript ClassとしてReact外で保持する。

実装File構成とClass APIは10を正とする。

## 4. 海中Environment

水面から届く光をWorldの主要な視覚要素とする。

- 上方に水面を置く
- Causticsを緩やかに時間変化させる
- 光条を低速で揺らす
- 気泡を複数の発生源から上昇させる
- 微細な浮遊粒子を低速で漂わせる
- Fogで遠景を青へ溶かす
- Residentの足元に海底を置く
- Residentと海底へ自然な光と影を落とす

物理シミュレーションの正確さより、存在感・静けさ・常用時の負荷の低さを優先する。

Caustics、水面、Fog、Particle等は既存Three.js向け実装・Shader例・OSSを先に調査し、Nirai専用の水シミュレーションを作らない。

時間帯はCoreから`time_of_day`を受けて緩やかに補間する。

- morning：柔らかい光
- day：光条とCausticsが最も明瞭
- evening：暖色がわずかに混じる
- night：全体を暗くし、残る光を細くする

## 5. ResidentManager

WorldはResidentを1体専用の構造にしない。M0では1体だけ表示するが、内部の入口はResidentManagerとする。

```text
ResidentManager
├ ResidentInstance A
├ ResidentInstance B
└ ResidentInstance C
```

ResidentInstanceが持つ責務：

- VRM Model
- AnimationController
- ExpressionController
- LookAtController
- MovementController
- LipSyncController
- BubbleAnchor
- ResidentPresenter

TTSエンジンへの接続はWorld共通のTtsServiceが持ち、ResidentInstanceは自分のSpeaker設定と再生状態だけを持つ。

人格・記憶・Brainへの参照は持たない。Coreから受け取る`name` / `avatar` / `location` / `command` / 発話テキストだけを演じる。

## 6. VRM Runtime

標準Avatar形式はVRMとする。

- VRM Runtimeは`@pixiv/three-vrm`を中心に構成する
- VRM 0.x / 1.0を対象とする
- Humanoid、Expression、LookAt、SpringBone等はVRM Runtimeが提供する機能を優先する
- VRM規格で吸収できる差をNirai独自Adapterで再実装しない
- FBX / Unitypackage / PMX等をWorldが直接読み込むことは初期要件にしない

詳細は09を正とする。

## 7. Animation

Nirai共通Animation：

- stand（LOCOMOTIONのIdle）
- walk
- afk（IDLE / AFK）
- sleep

AvatarごとにAnimationを制作しない。VRM Humanoidへ共通Animationを適用する。

`gesture / talk`は対応Assetが追加された将来Milestoneで共通Animationへ加える。`sit / stretch / think`は実装予定に含めない。

共通AnimationはVRMAを優先し、pixiv公式`@pixiv/three-vrm-animation`の`VRMAnimationLoaderPlugin`と`createVRMAnimationClip()`を利用する。独自Retargeterはこの方式で成立しないことを確認した場合のみ検討する。

Animation状態はNirai側の意味名で管理し、ライブラリ固有型をCore Protocolへ露出させない。

## 8. Expression / LookAt

Nirai上の意味表現：

- happy
- angry
- sad
- relaxed
- blink
- 発話用口形

VRM標準Expressionを優先して利用する。Avatar固有差が残る場合だけ補正設定を持つ。

LookAtはVRM Runtimeの仕組みを優先し、`face(master)`や`face(Resident名)`の意味コマンドをWorld側で対象座標へ変換する。

瞬きやごく小さな視線移動はWorld側のアイドルゆらぎとして自動実行してよい。

## 9. TTS / LipSync

### 原則

Residentの発話の正本は常にテキストである。TTSはテキストに付随する任意の表現であり、音声が無くても会話機能は完全に成立する。

初期TTS ProviderはVOICEVOXとする。

発話処理：

```text
Coreから発話テキスト受信
├ 吹き出し表示
├ 会話UIへ全文表示
├ talk Animation
└ TTSが有効かつ全体音量>0
   └ Renderer TtsService
      ↓ IPC
      Main VoicevoxClient
      ├ POST /audio_query
      ├ speedScale / pitchScale / intonationScaleを設定
      └ POST /synthesis
         ↓ WAV bytes
      Renderer SpeechQueue
         ↓ AudioService
         ├ GainNodeで全体音量
         └ AnalyserNode → LipSync
```

規則：

- 全体音量は0〜100。0をMuteとして扱う
- 音量0でも吹き出し、会話UI、会話ログ、talk Animationは通常通り動く
- 音量を0へ変更した時点で再生中の音声は停止できる
- 音量0では新規TTS生成を行わない
- 音量を戻した時、過去の発話を遡って読み上げない
- LipSyncは音声再生中のみ音声解析へ連動させる。Mute中はtalk Animationだけでよい
- ResidentごとにTTSの有効/無効、VOICEVOX Speaker / Style、話速、音高、抑揚を設定できる
- VOICE設定UIはVOICEVOXのSpeaker / Style一覧を取得し、試聴して保存できるようにする。VOICEVOX本体の設定画面へ依存しない

TTS Providerを大量に先回り実装しない。Provider差し替え用の小さなIFだけ持ち、必要になった時にAITuberKit等の既存実装を参考に追加する。

VOICEVOXへのRenderer直fetchやCORS回避目的の`webSecurity=false`は禁止する。初期接続先は`http://127.0.0.1:50021`とする。

音声再生はGlobal `SpeechQueue`で直列化し、Taskを`request_id`とResident名に紐づける。停止時は現在再生と同じ`request_id`の待機音声を破棄する。AITuberKitのStop Tokenと同様にGeneration値を持ち、Stop前に開始したasync処理が後から再生を復活させないようにする。

## 10. 移動とLocation

Coreと共有するのは意味的なLocation IDだけ。実座標はWorldが持つ。

初期Location：

| id | name_ja | 役割 |
|---|---|---|
| center | 光の柱 | 世界の中心 |
| light_area | 光の差し込み | Causticsが強い場所 |
| quiet_area | 静かな陰 | 光が弱い寄り所 |
| open_floor | 開けた海底 | 広く見える場所 |
| rest | 休み場 | AFK・睡眠に使う場所 |

M0〜M2のWorldは障害物の少ない小規模空間を前提とするため、最初からNavMeshや独自経路探索を作らない。Location間の単純移動とResident同士の重なり回避で成立させる。

複雑な経路探索が必要になった時だけ、既存のThree.js向けナビゲーション実装を評価する。

## 11. 行動コマンドの演技

| command | 演技 |
|---|---|
| move | walk Animationで目標Locationへ移動。到着でstandへ戻る |
| wander | 現Location付近を短く歩く |
| stand | LOCOMOTIONのIdleで立ち待機へ戻る |
| afk | IDLE / AFK Animationで休憩する |
| expression | VRM Expressionを適用 |
| face | LookAt対象を変更 |
| work | 作業状態へ。具体的な視覚表現はWorldが決める |
| sleep | restへ移動してSLEEP Animationを再生する |

Worldは未知のcommandを受けても落ちず、WARNログを残して`stand`扱いにする。

※メモ：`gesture / talk`の演技は対応Asset追加後にこの表へ追加する。

## 12. 吹き出しと会話UI

- `bubble`受信でResident頭上へ表示
- `text_short`を吹き出しへ使う
- `text_full`はTTS入力と全文表示の元として利用できる
- 同一Residentに連続発話が来た場合は古い吹き出しを差し替える
- 会話UIの詳細は05を正とする

## 13. 省エネ

- `pause`受信：描画FPSとEnvironment Effect更新頻度を下げる。WebSocket接続は維持する
- `resume`受信：通常品質に戻す
- Electron Windowの表示・フォーカス・最小化状態を`display_state`でCoreへ通知する
- 全体音量状態は省エネ状態と独立する

## 14. 異常時

1. 未知のtype / commandはWARNログを出して無視する
2. 存在しないResidentへのactionは無視してWARNログ
3. 同じResidentで新しいactionが来た場合、意味的に排他的な動作は新しい指示を優先する
4. VRMロード失敗はそのResidentだけを非表示にし、World全体は継続する
5. VOICEVOXへ接続できない場合はTTSだけを無効化し、テキスト会話は継続する
