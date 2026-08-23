# Nirai 詳細設計 10：AITuberKit分析と実装ブループリント

正本は [Nirai_基本設計.md](../Nirai_基本設計.md)。全体構成は [00_全体構成.md](00_全体構成.md)、通信は [01_通信プロトコル.md](01_通信プロトコル.md)、Worldは [04_World.md](04_World.md)、UIは [05_会話パネル.md](05_会話パネル.md)、Residentと記憶は [06_Residentと記憶.md](06_Residentと記憶.md)、受入条件は [08_マイルストーンと受入基準.md](08_マイルストーンと受入基準.md)、Avatarは [09_3DビジュアルとAvatarパイプライン.md](09_3DビジュアルとAvatarパイプライン.md) を正とする。

本書は**実装担当AIが設計判断を追加せず、上から順に作業すればNiraiを組み立てられること**を目的とする。

---

## 1. 調査対象と利用方針

調査対象：`https://github.com/tegnike/aituber-kit` の2026-08-22時点の`main`ブランチ。

AITuberKitは、Niraiで必要となる以下の領域が実際に成立している先行実装として利用する。

- Three.js + VRM表示
- VRM 0.x / 1.0差の吸収
- AnimationMixerによるAnimation再生
- Expression / Blink / LookAt
- Web AudioによるLipSync
- 音声合成結果の再生と停止
- 発話Queue
- WebSocket外部連携
- React + ZustandによるUI状態管理
- Electronデスクトップ化

### 絶対ルール

1. AITuberKitをForkしない。
2. AITuberKit固有Sourceをコピーしない。
3. AITuberKitのClass名・関数名をそのままNiraiへ移植しない。
4. AITuberKitが利用しているOSSの公開APIと、成立している責務分割を参考にする。
5. 同等機能をNirai独自方式で再発明しない。
6. AITuberKitの単一Character前提をNiraiへ持ち込まない。
7. AITuberKitの`webSecurity: false`は採用しない。
8. AITuberKitのNext.js/API Route構成は採用しない。Niraiはデスクトップ専用なのでElectron + electron-vite + Reactで構成する。

---

## 2. AITuberKitの重要実装とNiraiへの対応

| AITuberKit | 成立していること | Niraiでの扱い |
|---|---|---|
| `src/features/vrmViewer/model.ts` | VRM Load、Mixer、Expression、LipSync、VRM Update | `ResidentInstance`へ分解して利用する考え方を採用 |
| `src/features/vrmViewer/viewer.ts` | Scene、Renderer、Camera、Render Loop、VRM非同期Load | `SceneRuntime`へ置換。単一`model`前提は採用しない |
| `src/features/emoteController/*` | Emotion、Blink、LipSyncの競合制御 | `ExpressionController`の責務として採用 |
| `src/features/lipSync/lipSync.ts` | AudioContext + AnalyserNodeから口形Weight算出 | M1のLipSync方式として採用。最初は`aa`のみ |
| `src/features/messages/speakQueue.ts` | 音声Queue、Session切替、Stop Token | `SpeechQueue`のQueue/Generation方式として採用 |
| `src/features/messages/characterRenderer.ts` | Character描画実装の共通IF | Niraiでは`ResidentInstance`の発話表現APIとGlobal `SpeechQueue`へ責務分割 |
| `src/features/messages/synthesizeVoiceVoicevox.ts` | TTS ProviderをCharacter処理から分離 | `VoicevoxClient` + `TtsService`として採用 |
| `src/features/externalLinkage/*` | requestId、cancel、接続Lifecycle | Nirai Protocolへ`request_id`の考え方を採用 |
| `src/features/stores/externalLinkageWebSocketStore.ts` | 接続状態、再接続、Request状態 | `connectionStore` + `CoreConnection`へ採用 |
| `src/features/stores/*` | ZustandによるUI状態管理 | Nirai RendererのUI Stateへ採用 |
| `electron.mjs` | nodeIntegration=false、contextIsolation=true | 採用。ただし`webSecurity:false`は不採用 |

### AITuberKitから採用しないもの

- Next.js
- 多数のLLM API Provider
- Live2D / PNGTuber
- ブラウザ版対応
- AITuberKit内Memory
- AITuberKit内Chat Agent
- Legacy WebSocket互換
- PCM16 Streaming TTS
- Cloudflare等のWeb Deploy基盤
- 単一Character Store
- Character位置をCamera中心に固定する設計

---

# Part A：World実装

## 3. Worldの固定技術

Worldの技術は次で固定する。

- Language：TypeScript
- Desktop Shell：Electron
- Main / Preload / Renderer Build：electron-vite
- UI：React
- UI State：Zustand
- 3D：Three.js
- VRM：`@pixiv/three-vrm`
- VRMA：`@pixiv/three-vrm-animation`
- Package Manager：npm

### Bootstrap規則

`world`はelectron-viteの`react-ts`相当構成を基準にし、Main / Preload / Rendererを1つのBuild設定で扱う。独自に複数Vite設定を継ぎ合わせない。

最低限のPackage：

```text
runtime / renderer:
  react
  react-dom
  three
  @pixiv/three-vrm
  @pixiv/three-vrm-animation
  zustand

development:
  electron
  electron-vite
  vite
  @vitejs/plugin-react
  typescript
  @types/react
  @types/react-dom
  @types/three
  vitest
```

`package.json`の基本Script：

```json
{
  "main": "./out/main/index.js",
  "scripts": {
    "dev": "electron-vite dev",
    "build": "electron-vite build",
    "preview": "electron-vite preview",
    "test": "vitest run"
  }
}
```

M0 bootstrap時に、以下を実際にinstallしてSmoke Testを通した後、その組み合わせを`package-lock.json`で固定する。

AITuberKitで確認済みの出発点：

- `three` 0.167系
- `@pixiv/three-vrm` 3.4系
- React 18系
- Zustand 4系

`@pixiv/three-vrm-animation`はAITuberKit内の独自VRMAnimation Sourceを利用せず、pixiv公式Packageを直接利用する。

実装AIは「新しい方が良さそう」という理由だけでDependencyを更新してはならない。

---

## 4. Worldのファイル構成

M0開始時点で以下を作る。**名前を勝手に変えない。**

```text
world/
  package.json
  package-lock.json
  tsconfig.json
  electron.vite.config.ts

  src/
    main/
      main.ts
      paths.ts
      ipc/
        avatarIpc.ts
        personaIpc.ts
        voicevoxIpc.ts

    preload/
      index.ts
      api.ts

    renderer/
      index.html
      src/
        main.tsx
        App.tsx

        runtime/
          SceneRuntime.ts
          RenderLoop.ts
          CoreConnection.ts

        world/
          ResidentManager.ts
          ResidentInstance.ts
          LocationRegistry.ts

          vrm/
            VrmLoader.ts
            AnimationController.ts
            ExpressionController.ts
            LookAtController.ts
            LipSyncController.ts

          environment/
            EnvironmentController.ts
            WaterSurface.ts
            Seabed.ts
            UnderwaterFog.ts
            Caustics.ts
            LightShafts.ts
            BubbleSystem.ts
            SuspendedParticles.ts

        audio/
          AudioService.ts
          SpeechQueue.ts
          TtsService.ts
          types.ts

        stores/
          connectionStore.ts
          sessionStore.ts
          residentStore.ts
          audioStore.ts
          uiStore.ts

        ui/
          ChatBar.tsx
          ChatHistory.tsx
          SessionSidebar.tsx
          ResidentSidebar.tsx
          ResidentEditor.tsx
          BrainSelector.tsx
          VoiceSettingsPanel.tsx
          VolumeControl.tsx
          DeleteResidentDialog.tsx
          NoticeToast.tsx

        protocol/
          types.ts
          parser.ts

        styles/
          global.css

  tests/
    unit/
```

### 禁止

- `App.tsx`にThree.js初期化を全部書かない。
- React Component内に`GLTFLoader`を直接書かない。
- React Stateへ`THREE.Scene`や`VRM`本体を格納しない。
- ComponentからVOICEVOXへ直接`fetch()`しない。
- ComponentからFile Systemへ直接アクセスしない。

ReactはUIを担当し、3D Runtimeは通常のTypeScript ClassとしてReact外に置く。

---

## 5. Electron Main / Preload

### BrowserWindow必須設定

`src/main/main.ts`でBrowserWindowを1つ作る。

必須：

```text
nodeIntegration = false
contextIsolation = true
webSecurity = true
```

`sandbox`は利用Libraryとの互換を確認し、問題がなければtrueにする。

AITuberKitの`webSecurity:false`はCORS回避目的だが、Niraiでは採用しない。VOICEVOX通信はMain Processへ寄せる。

### NIRAI_ROOT

CoreがWorldを起動する際、環境変数`NIRAI_ROOT`へNirai Root Pathを渡す。

例：

```text
NIRAI_ROOT=D:\Products\Nirai
```

Main Processは全ローカルPathをこのRootから解決する。Core未実装のM0単体起動時のみ、`NIRAI_ROOT`が無ければ`world`の親Directoryを候補Rootとし、`avatars`と`Docs`が存在することを確認して採用する。候補確認に失敗した場合は推測せず起動Errorにする。

`paths.ts`で次の関数だけを公開する。

```ts
getNiraiRoot(): string
getAvatarsRoot(): string
getResidentsRoot(): string
resolveAvatarPath(relativePath: string): string
resolvePersonaPath(residentName: string): string
```

`resolveAvatarPath`は必ず次を検証する。

1. 正規化後Pathが`<NIRAI_ROOT>\avatars\`配下である。
2. 拡張子が`.vrm`である。
3. `..`によるRoot脱出を拒否する。

### PreloadでRendererへ公開するAPI

`window.nirai`だけを公開する。

```ts
window.nirai.avatar.pick(): Promise<string | null>
window.nirai.avatar.read(relativePath: string): Promise<Uint8Array>
window.nirai.persona.open(residentName: string): Promise<void>
window.nirai.voicevox.health(): Promise<boolean>
window.nirai.voicevox.speakers(): Promise<VoicevoxSpeaker[]>
window.nirai.voicevox.synthesize(request: VoicevoxSynthesisRequest): Promise<Uint8Array>
```

Rendererへ`ipcRenderer`そのものを渡さない。

### VRM File Picker

`avatar.pick()`：

1. `dialog.showOpenDialog()`を使う。
2. `defaultPath = <NIRAI_ROOT>\avatars`。
3. Filterは`VRM (*.vrm)`だけ。
4. 選択結果がavatars Root外なら拒否する。
5. RendererへAbsolute Pathではなくavatars RootからのRelative Pathを返す。

### VRM Bytes取得

Rendererから`file://`を直接Loadしない。

`avatar.read(relativePath)`：

1. MainでPath検証。
2. `fs.readFile()`。
3. `Uint8Array`でRendererへ返す。
4. Renderer側`VrmLoader`が`GLTFLoader.parseAsync()`で読み込む。

VRMはGLB系Binaryとして扱い、任意Local File URLをRendererへ開放しない。

---

## 6. SceneRuntime

`SceneRuntime`はWorld 3D全体の唯一のRoot Classとする。

```ts
class SceneRuntime {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  clock: THREE.Clock
  residents: ResidentManager
  environment: EnvironmentController
  locations: LocationRegistry

  start(canvas): void
  resize(width, height): void
  update(delta): void
  dispose(): void
}
```

### start順序

1. Renderer作成。
2. Scene作成。
3. Camera作成。
4. Light作成。
5. `LocationRegistry`作成。
6. `EnvironmentController`作成。
7. `ResidentManager`作成。
8. Window resize監視。
9. Render Loop開始。

### Render Loop順序

1Frameごとに必ず次の順で呼ぶ。

```text
clock.getDelta()
↓
environment.update(delta)
↓
residents.update(delta)
↓
renderer.render(scene, camera)
```

Resident内部では：

```text
MovementController.update
AnimationController.update
ExpressionController.update
LookAtController.update
LipSyncController.update
vrm.update
```

同一処理をReact render cycleから呼ばない。

---

## 7. ResidentManager

AITuberKitのViewerは`model`を1つだけ持つ。Niraiはこの構造をそのまま採用してはならない。

`ResidentManager`はMapでResidentを管理する。

```ts
class ResidentManager {
  private residents: Map<string, ResidentInstance>

  spawn(def: ResidentViewDefinition): Promise<void>
  remove(residentName: string): void
  changeAvatar(residentName: string, avatarPath: string): Promise<void>
  get(residentName: string): ResidentInstance | undefined
  update(delta: number): void
  dispose(): void
}
```

### spawn規則

1. 同名Residentが存在する場合は二重生成しない。
2. `avatar == null`なら身体を生成しない。
3. `ResidentInstance`を作る。
4. `loadAvatar()`完了後にSceneへ追加する。
5. Load中に別Avatarへ変更された場合、古いLoad結果を破棄する。

### 非同期Avatar競合防止

AITuberKitの`_loadVrmRequestId`方式を一般化する。

各ResidentInstanceに`loadGeneration`を持つ。

```text
changeAvatar開始 → generation++
↓
非同期Load
↓
完了時generation確認
↓
開始時と違う → そのVRMをdisposeして捨てる
一致 → 採用
```

これを省略すると、短時間にVRM変更した際に古いLoadが後から到着して巻き戻るため、必須。

---

## 8. ResidentInstance

1 Residentの3D身体は以下だけを持つ。

```ts
class ResidentInstance {
  readonly name: string
  vrm: VRM | null
  root: THREE.Group

  animation: AnimationController
  expression: ExpressionController
  lookAt: LookAtController
  movement: MovementController
  lipSync: LipSyncController

  loadAvatar(path: string): Promise<void>
  playAction(command): void
  speakVisual(expression?): void
  stopSpeakVisual(): void
  update(delta): void
  dispose(): void
}
```

ResidentInstanceへ置いてはいけないもの：

- persona
- World Memory
- Private Memory
- Brain Driver
- Chat Session全文
- VOICEVOX HTTP通信

---

## 9. VrmLoader

### Load手順

AITuberKit `model.ts`とpixiv公式Exampleで成立している手順をNirai向けに固定する。

1. Main IPCから`.vrm` Binaryを取得。
2. `GLTFLoader`を作る。
3. `VRMLoaderPlugin`をregisterする。
4. `parseAsync()`する。
5. `gltf.userData.vrm`を取得する。
6. `VRMUtils.rotateVRM0(vrm)`を呼ぶ。
7. 不要頂点・Joint等の最適化は公式`VRMUtils`で安全に利用可能なものだけ適用する。
8. `vrm.scene.traverse()`で必要ならfrustumCulledをfalseにする。
9. `THREE.AnimationMixer(vrm.scene)`を作る。
10. stand Animation（LOCOMOTIONのIdle）をLoadする。
11. Sceneへ追加する。
12. `vrm.scene.visible = true`にする。

### unload手順

1. Animationをstop。
2. Audio/LipSyncをstop。
3. SceneからVRM rootをremove。
4. `VRMUtils.deepDispose(vrm.scene)`。
5. Controller参照を解除。
6. `vrm = null`。

`scene.remove()`だけで終えない。VRM交換を繰り返すとGPU Resource Leakになるため、deepDispose必須。

---

## 10. Animation

### Format

Nirai共通AnimationはVRMAを優先する。

Asset Root：

```text
world/public/animations/
  stand.vrma
  walk.vrma
  afk.vrma
  sleep.vrma
```

`gesture / talk`は対応Assetが追加された将来Milestoneで配置する。`sit / stretch / think`用Assetは予定しない。

### Load

`@pixiv/three-vrm-animation`の公式方式を使う。

概念手順：

```text
GLTFLoader
+ VRMAnimationLoaderPlugin
↓
VRMA Load
↓
gltf.userData.vrmAnimations[0]
↓
createVRMAnimationClip(vrmAnimation, vrm)
↓
AnimationMixer.clipAction(clip)
```

AITuberKit内`src/lib/VRMAnimation`は参照調査のみとし、Sourceを利用しない。

### AnimationController

```ts
play(name: AnimationName, options?): void
stop(name?): void
crossFade(next, durationSec): void
update(delta): void
```

状態は最低限：

```text
stand
locomotion
afk
sleep
```

M0では複雑なAnimation State Machineを作らない。

将来`gesture / talk`を追加した時だけ、`oneShot / speaking`状態と次の優先度を追加する。

```text
oneShot gesture > movement walk > talk posture > afk / sleep / stand
```

発話中でも歩ける等の複合Animationが必要になった場合のみLayer化を検討する。

---

## 11. Expression / Blink / LookAt

AITuberKitではEmotion、AutoBlink、LipSyncを別状態として扱い、互いにExpression値を潰し合わないよう制御している。この考え方を採用する。

### ExpressionControllerが持つ状態

```ts
emotion: 'neutral' | 'happy' | 'angry' | 'sad' | 'relaxed'
lipWeight: number
blinkEnabled: boolean
```

### Emotion変更

1. 前のEmotion Weightを0へ戻す。
2. 新Emotionを0〜1で設定する。
3. neutralではAutoBlink有効。
4. 目を強く使うEmotionではBlinkがExpressionを壊す場合のみ一時停止する。

### LipSync

LipSyncはEmotionとは別に適用する。

M1では`aa` Expressionだけでよい。AITuberKitで成立している初期係数をBenchmark値として使い、まず次で実装する。

```text
raw = 2048 sampleのTime Domain波形の max(abs(sample))
weight = 1 / (1 + exp(-45 * raw + 5))
weight < 0.1 の場合は 0
```

Expressionへ渡す最終Weightは、AITuberKitの競合回避を参考に初期値を次とする。

```text
emotion == neutral : aa = weight * 0.5
emotion != neutral : aa = weight * 0.25
```

実Avatarで口開きが弱すぎる/強すぎる場合だけ定数を調整する。Avatarごとに別LipSync Algorithmを作らない。

ai/ue/oh等の音素推定はM1で実装しない。

### LookAt

- Master：Camera付近のLookAt Target Object
- Resident：対象Residentのhead World Position

`face(master)`等を受けた時、CoreへThree.js座標を返さない。World内でTargetを解決する。

---

## 12. AudioService / LipSync

AITuberKitのLipSyncはWeb AudioのAnalyserNodeでTime Domain波形を取得し、振幅から口開きを算出している。Niraiも同方式で開始する。

### Audio Graph

```text
AudioBufferSourceNode
        ↓
   AnalyserNode
        ↓
     GainNode  ← global volume 0.0〜1.0
        ↓
 AudioContext.destination
```

`GainNode`はNirai全体音量に使う。VOICEVOXの`volumeScale`を全体音量として使わない。

### AudioContext開始

Browser Autoplay制約に備え、最初のMaster操作時に`audioContext.resume()`する。

候補操作：

- Chat入力クリック
- Sendボタン
- Volume操作
- VOICE試聴

TTS到着時までAudioContextを未開始のまま放置しない。

### LipSyncController

1FrameごとにAnalyserから波形取得。

M1初期計算は§11の固定式を利用する。AnalyserのTime Domain buffer長は2048から開始する。まずAITuberKitで成立している値を利用し、実機で問題が確認された場合だけ調整する。

`weight`を`ExpressionController.setLipWeight(weight)`へ渡す。

---

## 13. SpeechQueue

AITuberKitのSpeakQueueから次を採用する。

- Queueは1つにする。
- Session/Request単位で古いTaskを捨てられる。
- Stop時に現在再生＋待機Queueを止める。
- Stop Generation Tokenを持ち、Stop後に古いasync処理が復活しないようにする。

### Task型

```ts
interface SpeechTask {
  requestId: string
  residentName: string
  text: string
  audio: Uint8Array
}
```

### M1/M2の規則

- Text表示はSpeechQueueを待たない。
- SpeechQueueは音声Presentationだけを直列化する。
- Residentが複数いても初期は音声を同時再生しない。
- `cancel(requestId)`で、そのRequestの再生中Audioと待機Taskを捨てる。
- Global Volumeが0ならTTS生成自体をSkipし、Queueへ追加しない。

### Stop Generation

```text
generation = 12
Task処理開始時に12を保存
↓
Stop → generation = 13
↓
古いTaskのawait完了
↓
12 != 13なので後続処理を捨てる
```

AbortControllerが利用できる処理には併用する。

---

## 14. VOICEVOX

VOICEVOX接続先既定値：

```text
http://127.0.0.1:50021
```

`localhost`ではなく127.0.0.1を既定とする。

### Speaker取得

Main Processから：

```text
GET /speakers
```

UIでは：

```text
Speaker名
  ├ Style A (id)
  ├ Style B (id)
  └ Style C (id)
```

VOICEVOX APIの`speaker`引数は実質Style IDである。

### 保存値

Resident configは曖昧な`Speaker名`ではなく次を保存する。

```toml
[tts]
enabled = true
provider = "voicevox"
speaker_uuid = "..."
style_id = 3
speed = 1.0
pitch = 0.0
intonation = 1.0
```

Speaker名、Style表示名はEngineから都度取得し、正本にしない。

### 合成手順

Main Process `VoicevoxClient.synthesize()`：

1. `POST /audio_query?text=<encoded>&speaker=<style_id>`。
2. JSONを取得。
3. `speedScale = request.speed`。
4. `pitchScale = request.pitch`。
5. `intonationScale = request.intonation`。
6. `volumeScale`はProvider既定のまま。
7. `POST /synthesis?speaker=<style_id>`へ変更済みAudioQuery JSONを送る。
8. WAV Binaryを取得。
9. Rendererへ`Uint8Array`で返す。

### Timeout

- health / speakers：5秒
- audio_query：10秒
- synthesis：30秒

Timeout時はTTSだけ失敗扱い。Chat本文を失敗させない。

### Cancellable Synthesis

VOICEVOXには実験的なcancellable synthesisがあるが、M1では前提にしない。

停止ボタンでは：

- Rendererの再生を即停止。
- 未開始TTSをQueueから削除。
- Main側fetchへAbortSignalを使える範囲でcancel。

Engine内部計算が残る場合でもChat停止自体は完了扱いとする。

---

# Part B：Core接続

## 15. CoreConnection

AITuberKit External Linkageの「Session IDとRequest IDを分ける」設計を採用する。

Niraiでは：

- `message.id`：WebSocket Message 1件の相関ID
- `session_id`：UI Chat Session
- `request_id`：Master発話1回と、それに対する全Resident応答のID
- `invocation_id`：Core内のBrain 1呼び出しのID

同一概念として混ぜない。

### request_id生成

RendererがMaster発話送信時に`crypto.randomUUID()`で生成する。

```json
{
  "type": "master_say",
  "payload": {
    "text": "おはよう",
    "request_id": "..."
  }
}
```

Coreは同じ`request_id`をResponse Lifecycleへ引き継ぐ。

### Stop

```json
{
  "type": "cancel_response",
  "payload": {
    "request_id": "..."
  }
}
```

Session IDで停止対象を決めてはならない。同一Sessionで過去Requestと現在Requestを区別できなくなるため。

---

## 16. WebSocket状態管理

`CoreConnection`はSocketそのものを管理し、`connectionStore`はUI表示用Stateだけを持つ。

### State

```ts
status: 'disconnected' | 'connecting' | 'connected' | 'reconnecting'
lastError: string | null
reconnectCount: number
reconnectDelayMs: number
activeRequestId: string | null
```

### 再接続

AITuberKitの指数Backoffを参考に次で固定する。

```text
1秒 → 2秒 → 4秒 → 8秒 → 16秒 → 30秒 → 30秒…
```

接続成功で1秒へReset。

M1では独自Heartbeatを追加しない。WebSocket close/errorを使う。

### 接続成功後の同期順

1. `hello`
2. `hello_ack`
3. Resident一覧反映
4. Active Chat Session反映
5. Session一覧Request
6. 選択Session History Request
7. Audio Volume反映
8. UI操作Enable

---

## 17. Protocol Parser

WebSocket受信JSONをComponent内で直接分岐しない。

`protocol/parser.ts`で：

1. JSON parse。
2. object確認。
3. `type` string確認。
4. `payload` object確認。
5. Typeごとに最低限のField確認。
6. 不正ならWARNし捨てる。

未知TypeでWorldを落とさない。

Zod等を使う場合も全Protocolを巨大Schema 1個にしない。Message Type単位に小さくする。

---

# Part C：UI

## 18. Zustand Store分割

AITuberKitのStore構成を参考にするが、巨大Store 1個に集約しない。

### connectionStore

```text
status
lastError
activeRequestId
```

### sessionStore

```text
sessions[]
activeSessionId
entries[]
hasOlder
historyLoading
```

### residentStore

```text
residents[]
expandedResidentName
providerStatuses[]
```

### audioStore

```text
volume 0..100
speakingResidentName | null
voicevoxAvailable
```

### uiStore

```text
chatActive
historyOpaque
leftSidebarOpen
rightSidebarOpen
voicePanelResidentName
```

3D ObjectはStoreへ入れない。

---

## 19. ChatBar

State：

```text
text
isComposing
```

入力規則：

1. IME composition中のEnterでは送信しない。
2. `Shift+Enter`なら改行。
3. Enterのみなら送信。
4. 空白だけなら何もしない。
5. `connectionStore.activeRequestId != null`なら送信ボタンはStop表示。

日本語IME対策として`compositionstart` / `compositionend`または`event.isComposing`を必ず見る。

### send順序

1. 入力Textを確定。
2. request_id生成。
3. activeRequestIdへSet。
4. UIへMaster発話をOptimistic表示してもよいが、Coreから同一Entryが返る設計なら二重表示しないようIDでdedupeする。
5. `master_say` / `master_whisper`送信。
6. 入力欄clear。

---

## 20. ChatHistory

- `entries`を下から上へ表示。
- 新規Entry追加時、ユーザーがBottom付近なら自動Scroll。
- ユーザーが古い履歴を読んでいる時は勝手にBottomへ飛ばさない。
- Scroll Top到達時、`history_request`。
- 取得した古いEntryを先頭へprependした後、見ていた位置がJumpしないようScroll Height差分を補正する。
- Window clickで`historyOpaque=true`。
- World clickで`historyOpaque=false`へ戻せる。

---

## 21. SessionSidebar

最低Component：

```text
NewChatButton
SessionList
SessionRow
SessionMenu
```

SessionRow操作：

- 選択
- 履歴削除
- World Memoryから忘れさせる

削除と忘却は同じMenu項目にしない。

### 履歴削除確認

通常Confirmでよい。`Delete`入力までは要求しない。

### World Memory忘却確認

「会話履歴は残るが、ResidentがこのSession由来の公開記憶を思い出さなくなる」ことをDialogへ明示する。

---

## 22. ResidentSidebar

新規作成は名前のみ。

作成後すぐ設定一覧へ追加し、未設定項目を表示する。

例：

```text
Holo
  AI       未設定
  VRM      未設定
  VOICE    未設定
  Prompt   開く
  Delete
```

### AI連携

UIはProvider固有認証ロジックを持たない。

Coreから：

```json
{
  "name": "claude-code",
  "display_name": "Claude",
  "available": true,
  "connected": true,
  "configuration_mode": "connect"
}
```

等を受けて描画する。

### VRM

1. `window.nirai.avatar.pick()`。
2. relative path取得。
3. Coreへ`resident_set_avatar`。
4. Core保存成功後、WorldのResidentManagerへAvatar変更が通知される。
5. Load失敗なら旧Avatarを維持する。

### Prompt

`window.nirai.persona.open(name)`だけを呼ぶ。

### Delete

1. Dialog表示。
2. Inputが`Delete`完全一致するまでOK disabled。
3. Coreへ`resident_delete`。
4. 成功後ResidentManager remove。
5. avatars Rootには触らない。

---

## 23. VoiceSettingsPanel

Open時：

1. `voicevox.health()`。
2. falseなら「VOICEVOXに接続できません」。
3. trueなら`speakers()`。
4. Speaker一覧をGroup表示。
5. Resident設定済み`style_id`を選択。

Field：

```text
Speaker
Style
Speed
Pitch
Intonation
Preview
Save
```

### Preview

固定文を利用する。例：「こんにちは。Niraiでこの声を使います。」

PreviewはResident設定を保存しない。

Preview開始前に現在PreviewをStopする。

### Save

Coreへ保存してからUI Stateを確定する。Core保存失敗時は画面値をResident正本へ反映した扱いにしない。

---

## 24. VolumeControl

- Range：0〜100。
- Wheel Step：5。
- Slider領域Hover中のみWheelを横取りする。
- 値は`Math.min(100, Math.max(0, value))`。
- Wheel event時は`preventDefault()`を適切に使い、背景Chat HistoryをScrollさせない。
- `AudioService.setVolume(volume / 100)`を即時呼ぶ。
- Coreへ`audio_volume_changed`を送って永続化する。
- 0になった瞬間、現在SpeechをStopする。

---

# Part D：Core M1実装

## 25. Coreファイル構成

```text
core/
  __main__.py
  config.py
  server.py
  protocol.py
  registry.py

  sessions/
    manager.py
    chat_store.py

  brains/
    base.py
    registry.py
    process_manager.py
    claude_code.py
    codex.py
    cursor.py
    gemini.py
    local_llm.py

  memory/
    world.py
    private.py
    episodes.py

  residents/
    service.py

  tasks/
    service.py

  tests/
```

M1で未使用ModuleはStubだけ先に作らない。必要になるMilestoneで追加する。

---

## 26. Brain共通IF

Python側：

```py
class BrainDriver(Protocol):
    async def think(self, invocation_id, mode, resident, context) -> BrainResponse: ...
    async def cancel(self, invocation_id) -> None: ...
    async def health_check(self) -> BrainHealth: ...
```

### process_manager

全CLI subprocess起動をここへ集約する。

Driverごとに`asyncio.create_subprocess_exec()`を直接乱立させない。

ProcessManagerが持つ：

```text
invocation_id -> process handle / process group metadata
```

必要API：

```py
run(invocation_id, argv, cwd, timeout) -> CompletedInvocation
cancel(invocation_id)
cancel_many(invocation_ids)
```

WindowsでProcess Treeを残さない方式を採用する。実装時はWindows標準Job Objectまたは既存の十分成熟したLibraryを評価し、親PID killだけで済ませない。

---

## 27. Master発話1回の処理

Say：

```text
master_say(text, request_id)
↓
ChatSessionへMaster発話保存
↓
response_state(active=true, request_id)
↓
対象Resident列挙
↓
Resident A invocation生成 → Brain呼出
↓
結果を保存 / bubble / TTS対象としてWorldへ
↓
Resident B...
↓
全員終了
↓
response_state(active=false, request_id)
```

### cancel_response

```text
cancel_response(request_id)
↓
request_idに紐づく実行中invocation取得
↓
ProcessManager.cancel()
↓
未開始Resident Queue破棄
↓
response_state(false)
↓
World SpeechQueue.cancel(request_id)
```

Task work invocationはrequest_idの会話Cancel Mapへ入れない。

---

## 28. Chat Session Store

`runtime/chat_sessions/index.json`を一覧正本、`S-*.jsonl`を本文正本とする。

### index.json Entry

```json
{
  "id": "S-20260822-001",
  "title": "NiraiのUIについて",
  "created_at": "2026-08-22T03:00:00+09:00",
  "updated_at": "2026-08-22T03:05:00+09:00"
}
```

Write時は一時Fileへ書いてからReplaceし、途中Crashでindex.jsonを壊しにくくする。

### Session Title

最初のMaster発話を：

1. 改行を空白へ。
2. Trim。
3. 最大30文字程度でCut。
4. 空なら`新しいチャット`。

Brainを呼ばない。

---

# Part E：実装順

## 29. M0実装Task順

**番号順に実施する。前をPassしていない状態で後ろへ進まない。**

### M0-01 World bootstrap

作る：Electron + electron-vite + React + TypeScriptの空Application。

完了条件：

- `npm run dev`でWindow表示。
- Rendererに文字列`Nirai`表示。
- Console Error 0。

### M0-02 Electron境界

作る：Main / Preload / Renderer分離、`window.nirai`空API。

完了条件：

- nodeIntegration=false。
- contextIsolation=true。
- Rendererから`require`不可。

### M0-03 Three.js Scene

作る：`SceneRuntime`。

完了条件：

- Canvas表示。
- Camera / Light / Plane 1枚が見える。
- Resizeして崩れない。

### M0-04 Avatar IPC

作る：`avatar.pick`、`avatar.read`。

完了条件：

- Pickerが`avatars\`から開く。
- `.vrm`以外を選べない。
- Root外PathをAPIへ渡すUnit Testが拒否される。

### M0-05 VrmLoader

完了条件：

- Master所有VRM 1体が表示。
- Error時Worldが落ちない。
- unload→loadを5回繰り返して古いVRMがSceneに残らない。

### M0-06 ResidentInstance / Manager

完了条件：

- Manager経由で1体Spawn。
- removeできる。
- 短時間にAvatar変更して最後に選んだVRMだけ残る。

### M0-07 Stand VRMA

完了条件：

- LOCOMOTIONのIdleを使ったstandがLoopする。
- Avatar交換後も同じstandが動く。

### M0-08 Animation Controller

順番：walk → afk → sleep。

1本ずつ追加し、全部まとめて追加しない。

`gesture / talk`は対応Asset追加後の将来Taskとする。`sit / stretch / think`は実装しない。

### M0-09 Expression

完了条件：happy / angry / sad / neutral / blink。

### M0-10 LookAt

完了条件：Master方向へ顔/目が自然に向く。

### M0-11 Movement

完了条件：Location A→Bをwalkし、到着でstand。

NavMeshは禁止。

### M0-12 Environment最低版

順番：

1. Seabed
2. Fog
3. WaterSurface
4. Caustics
5. SuspendedParticles
6. Bubbles
7. LightShafts

各Effect追加後にFPS/見た目確認。全部同時に作らない。

### M0-13 M0回帰

複数VRMでAnimation / Expression / LookAt /接地確認。

---

## 30. M1実装Task順

### M1-01 Core bootstrap

Python Core起動、config読込、127.0.0.1 WebSocket起動。

### M1-02 CoreConnection

World接続、hello/ack、指数Backoff。

### M1-03 Chat Session保存

Session create/select/history/listのみ。まだBrainを呼ばない。

### M1-04 Chat UI Skeleton

中央ChatBar、History、左Sidebar、右Sidebar、VolumeControlをDummy Dataで描画。

### M1-05 Master Say Echo

Master送信→Core保存→同じTextをWorldへ返すだけ。

この段階でrequest_idを通す。

### M1-06 Claude Driver

会話1回だけ成立させる。

### M1-07 Cancellation

Fake Slow BrainでStop Testを先に通す。その後CLI実Processで確認。

### M1-08 Resident create/config

名前だけ作成、一覧反映、Brain未設定許容。

### M1-09 Avatar設定UI

設定Sidebarから既存ResidentのVRM変更。

### M1-10 Persona open

Windows既定Editor起動。

### M1-11 VOICEVOX IPC

health / speakers / synthesize。

### M1-12 AudioService + SpeechQueue

まず固定WAV再生 → Queue → Stop → Volumeの順。

### M1-13 LipSync

Analyser amplitude → aa Expression。

### M1-14 Voice Settings UI

Speaker/Style → Preview → Speed/Pitch/Intonation → Save。

### M1-15 Whisper

Private保存と公開Context除外Testを先に書く。

### M1-16 Resident Delete

`Delete`完全一致、VRM/World Memory非削除をTest。

### M1-17 History Delete / Forget分離

両者が互いのDataへ触らないTestを実施。

---

## 31. M2実装Task順

1. Resident 2体表示。
2. Resident 3体表示。
3. Codex Driver。
4. Cursor Driver。
5. Gemini Driver。
6. Say時のResident逐次応答。
7. Global SpeechQueueで音声重複防止。
8. resident_chat。
9. 相手Locationへ移動。
10. face(other Resident)。
11. 公開Episode 1個保存。
12. Whisper漏洩Regression Test。

---

# Part F：テストと失敗時の判断

## 32. World Unit Test対象

自動Test対象：

- Path Root脱出拒否
- Protocol parse
- Zustand Store action
- SpeechQueue順序
- SpeechQueue cancel
- Stop generation
- Volume clamp / wheel step
- Resident load generation
- Session list reducer

3D見た目そのものをSnapshot Testしない。

---

## 33. Core pytest対象

M1で必須：

- request_idとinvocation_id紐付け
- cancel_responseで会話だけ停止
- Taskを停止しない
- Chat Session create/select/delete
- 履歴削除してWorld Memoryが残る
- World Memory忘却してUI履歴が残る
- WhisperがWorld Memoryへ入らない
- Whisperが他Resident Promptへ入らない
- Resident DeleteでVRMを消さない
- Invalid Resident name拒否

---

## 34. 手動Smoke Test固定シナリオ

### VRM

1. App起動。
2. Resident AへVRM A。
3. stand確認。
4. VRM Bへ変更。
5. stand確認。
6. VRM Aへ戻す。
7. Memory/GPU異常増加がないこと確認。

### Chat

1. 新規Chat。
2. Say。
3. 応答途中Stop。
4. もう一度Say。
5. 正常に次の応答が開始する。

Stop後に永久停止状態になる実装は不合格。

### TTS

1. VOICEVOX起動。
2. Preview。
3. Sayして読み上げ。
4. 再生中にVolume 0。
5. 再生停止。
6. Volume 50。
7. 次のSayだけ読み上げ。
8. 過去発話を再生しない。

### Session/Memory

1. Session AでSay。
2. New Chat B。
3. Aを再選択。
4. 履歴が戻る。
5. Aの履歴だけ削除。
6. Episodeが残ること確認。
7. 別Session CでSay。
8. Cを「世界の記憶から忘れさせる」。
9. CのUI履歴は残ること確認。

---

## 35. 失敗時の判断表

| 症状 | 最初に確認 | やってはいけないこと |
|---|---|---|
| VRMが出ない | Binary取得、GLTFLoader、VRMLoaderPlugin、Console Error | 独自VRM Parserを書く |
| VRM0だけ向きが逆 | `VRMUtils.rotateVRM0` | Avatarごとに手書きRotation表を増やす |
| Animationが崩れる | normalized Humanoid、VRMA、公式three-vrm-animation Example | 独自Retargeterを即作る |
| Avatar変更で古いモデルが戻る | loadGeneration | setTimeoutで順番をごまかす |
| Avatar変更でMemory増加 | Scene remove + `VRMUtils.deepDispose` | Page reloadで逃げる |
| LipSyncしない | AudioContext state、Analyser接続、aa Expression存在 | 音素AIを追加する |
| TTSだけ失敗 | VOICEVOX health、style_id、audio_query、synthesis | Chat全体をErrorにする |
| ElectronでCORS | VOICEVOXをMain IPCへ寄せたか | `webSecurity:false`にする |
| Stop後に音声が復活 | generation/requestId検査 | Queue全体を作り直し続ける |
| StopでTaskまで死ぬ | request_idとwork invocationのMap分離 | 全Process一括kill |
| 複数Residentで状態が混ざる | Map key / ResidentInstance固有Controller | Viewer.modelの単一Globalを復活させる |
| UIが複雑化 | Store責務とComponent分割 | App.tsxに全部書く |

---

## 36. 実装AIが勝手にしてはいけないこと

- AITuberKitのSourceをProjectへDownloadして流用する。
- AITuberKitをSubmoduleにする。
- AITuberKitのLicenseをNiraiのLicenseとして扱う。
- Next.jsを導入する。
- Live2DやPNGTuberを追加する。
- React ComponentへThree.js Runtimeを埋め込む。
- CoreへTTS再生責務を移す。
- RendererからVOICEVOXへ直接接続するためElectron Securityを無効化する。
- VRM以外のImporterを追加する。
- M0でRAGを作る。
- M1でVector DBを作る。
- M1で複数TTS Providerを実装する。
- M1でPCM Streamingを実装する。
- StopボタンをProcess全停止ボタンとして実装する。
- Character削除で`avatars\`のVRMを消す。
- Session削除でWorld Memoryまで消す。
- Whisperを公開Episode要約へ渡す。

---

## 37. AITuberKitを再確認する場合の見る順序

VRM問題：

1. `src/features/vrmViewer/model.ts`
2. `src/features/vrmViewer/viewer.ts`
3. pixiv `three-vrm`公式Example
4. pixiv `three-vrm-animation`公式Example

Expression / LipSync：

1. `src/features/emoteController/expressionController.ts`
2. `src/features/emoteController/emoteController.ts`
3. `src/features/lipSync/lipSync.ts`

音声Queue：

1. `src/features/messages/speakQueue.ts`
2. `src/features/messages/characterRenderer.ts`
3. `src/features/messages/speakCharacter.ts`

VOICEVOX：

1. `src/features/messages/synthesizeVoiceVoicevox.ts`
2. VOICEVOX Engine公式README / API

WebSocket / Stop：

1. `src/features/externalLinkage/externalLinkageProtocol.ts`
2. `src/features/stores/externalLinkageWebSocketStore.ts`

Electron：

1. `electron.mjs`
2. Electron公式Security Guidance

AITuberKitで独自実装されている部分を見つけても、そのSourceを持ってくるのではなく、**何の公開APIを組み合わせて成立させているか**を確認してNiraiで再構成する。

---

## 38. 完了の定義

実装担当AIは「Codeを書いた」だけで完了報告してはならない。

各Task完了報告には最低限次を含める。

```text
実装したTask ID:
変更File:
実行したTest:
Test結果:
手動確認:
未確認事項:
設計との差異:
```

`設計との差異`がある場合、勝手に仕様化せずMasterへ報告する。

M0/M1の最終完了判定は08の受入条件を全て満たした場合のみとする。
