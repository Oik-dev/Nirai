# Nirai 詳細設計 13：AIAvatarKit参考カンペと独自実装Slice

正本は [Nirai_基本設計.md](../Nirai_基本設計.md)。World責務は [04_World.md](04_World.md)、会話UIは [05_会話パネル.md](05_会話パネル.md)、Avatarは [09_3DビジュアルとAvatarパイプライン.md](09_3DビジュアルとAvatarパイプライン.md)、既存AITuberKit分析は [10_AITuberKit分析と実装ブループリント.md](10_AITuberKit分析と実装ブループリント.md)、Agent Runtimeは [11_AgentRuntimeと実行UI.md](11_AgentRuntimeと実行UI.md) を正とする。

本書は2026-09-05時点の `https://github.com/uezo/aiavatarkit` を機能成立例として調査し、Niraiへ次の3領域を**独自実装**するためのカンニングペーパーである。

1. MFCCベース母音LipSync
2. 言い淀みに強いSemantic Turn-End
3. 画像・Slide・Web・Map等のPresentation / Artifact表示

AIAvatarKit本体、AIAvatarKitのClass / Function / Data Structure、uLipSync等のSource CodeはNiraiへコピーしない。Nirai実装担当は本書と公開仕様・一般的アルゴリズムを使ってNirai既存Architecture上に実装する。

---

## 1. 外部調査の扱い

### 参考にしてよいもの

- 「この機能が実用品として成立する」という事実
- 公開README / Documentsに書かれた機能要件、入出力、失敗時挙動
- MFCC、FFT、Mel Filter Bank、DCT等の一般公開された信号処理手法
- Web Audio、Three.js、VRM等の公開API
- VAD → STT → Turn-End判定という一般的な処理分離
- 発話本文と画面表示命令を別の構造化Channelにする設計原則

### 参考にしないもの

- AIAvatarKit SourceのCopy / Paste
- AIAvatarKit固有Class名、Function名、内部Schemaの転記
- AIAvatarKit PackageのRuntime依存
- AIAvatarKitのLLM / Memory / Character / Schedule / Tool Runtime
- AIAvatarKitの`<artifact>`等の独自Control TagをNirai Protocolとして採用すること
- uLipSync等のSourceをNiraiへ移植すること

### 独自実装の確認ルール

実装完了時、変更FileにAIAvatarKit由来Sourceが入っていないことを確認する。AIAvatarKit RepositoryをNirai配下へclone / vendor / submodule配置しない。

本書に書く数値・Class名・処理順は**Nirai側で決めた実装仕様**であり、AIAvatarKit Sourceの転記ではない。

---

# Part A：MFCCベース母音LipSync

## 2. 目的

現行NiraiはAudio波形の最大振幅から`aa`だけを動かす。

```text
Audio waveform
→ max(abs(sample))
→ mouth weight
→ aa
```

これを、再生中音声のスペクトル包絡から母音らしさを推定してVRM標準口形へ分配する方式へ更新する。

```text
Audio PCM
→ voiced / silence判定
→ Window
→ FFT
→ Power Spectrum
→ Mel Filter Bank
→ log energy
→ DCT
→ MFCC feature
→ voice profileとの距離
→ A / I / U / E / O weight
→ aa / ih / ou / ee / oh
```

TTSや会話の正本は変更しない。LipSyncはWorld Presentationのままとする。

## 3. 既存接続点

現行：

```text
SpeechQueue
→ AudioService.play()
→ AnalyserNode
→ Resident LipSyncController
→ ExpressionController.setLipWeight()
```

新規：

```text
SpeechQueue
→ AudioService.play()
→ AnalyserNode / AudioContext.sampleRate
→ VowelLipSyncController
   ├ amplitude fallback
   ├ MfccFeatureExtractor
   ├ VowelClassifier
   └ TemporalSmoother
→ ExpressionController.setLipWeights()
→ aa / ih / ou / ee / oh
```

`SpeechQueue`、TTS生成、Audio再生順序は変更しない。

## 4. Nirai側File案

```text
world/src/renderer/src/world/vrm/
  LipSyncController.ts          # 既存入口。Facadeとして残す
  MfccFeatureExtractor.ts
  VowelClassifier.ts
  LipSyncProfile.ts

world/src/renderer/src/audio/
  LipSyncProfileBuilder.ts

world/tests/unit/
  MfccFeatureExtractor.test.ts
  VowelClassifier.test.ts
  LipSyncController.test.ts
```

実装整理でClass名を増やしすぎない。上記4責務程度で十分。

## 5. MFCC Feature仕様

初期固定値：

```text
frame_size       = 2048 samples
window           = Hamming
fft_size         = 2048
mel_filter_count = 26
mfcc_count       = 12
min_freq_hz      = 80
max_freq_hz      = min(8000, sample_rate / 2)
```

MFCCの`c0`は音量影響が強いため分類Featureから外し、`c1..c12`を使う。音量は別途RMSで扱う。

### 処理式

1. 2048 sampleを取得する。
2. DC成分を軽く除くためframe平均を各sampleから引く。
3. Hamming Windowを掛ける。
4. radix-2 FFTでPower Spectrumを得る。
5. 80Hz〜8kHzへ26本の三角Mel Filterを掛ける。
6. 各Filter Energyへ`log(max(e, epsilon))`を掛ける。
7. DCT-IIでCepstrumへ変換する。
8. `c1..c12`をFeatureとする。

Mel変換は一般式を用いる。

```text
mel(f) = 2595 * log10(1 + f / 700)
f(mel) = 700 * (10^(mel / 2595) - 1)
```

FFT / Mel / DCTはNirai内で小さな純粋関数として実装する。外部MFCC Libraryを追加しない。

## 6. Voice Profile

ResidentごとのAvatarではなく、**TTS Voice設定ごと**に母音中心Featureを持つ。

Profile Key：

```text
provider
speaker_uuid
style_id
speed
pitch
intonation
sample_rate
algorithm_version
```

これらを安定JSON化してHashし、派生Cacheとして保存する。

```text
runtime/lipsync_profiles/<hash>.json
```

Profileは正本データではない。削除しても再生成できる。

### Profile生成

VOICE設定保存または最初の必要時に、同じTTS設定で次の5音を合成する。

```text
A: あー
I: いー
U: うー
E: えー
O: おー
```

各WAVをdecodeし、中央60%を中心にFrame化する。RMSが極端に低いFrameは除外する。

各母音についてMFCC Frame群を集め、全5母音をまとめたFeature次元ごとの`mean / stddev`を求める。そのScalerで正規化した後、各母音のCentroidを保存する。

保存例：

```json
{
  "version": 1,
  "sample_rate": 48000,
  "feature_mean": [0.0],
  "feature_std": [1.0],
  "centroids": {
    "a": [0.0],
    "i": [0.0],
    "u": [0.0],
    "e": [0.0],
    "o": [0.0]
  }
}
```

配列長は実際には12。

Profile生成失敗はTTS失敗や会話失敗へ昇格させない。既存Amplitude LipSyncへfallbackする。

## 7. Vowel分類

Runtime Featureを同じScalerで正規化し、5 Centroidへの二乗Euclidean Distanceを求める。

```text
d_a, d_i, d_u, d_e, d_o
```

WeightはSoftmaxで連続値へする。

```text
score_v = exp(-distance_v / temperature)
weight_v = score_v / sum(score)
```

`temperature`は初期値1.0からQAで調整する。1母音へ張り付きすぎる場合は上げ、口形が混ざりすぎる場合は下げる。

### 音量Gate

RMSからmouth amplitudeを0〜1へする。無音時は全母音0。

初期規則：

```text
rms <= silence_floor → 0
rms > silence_floor  → smoothstepで0..1
```

固定absolute thresholdだけにせず、再生開始後の低RMS区間から小さなnoise floorを更新できる構造にする。ただしTTS再生は比較的cleanなので複雑なNoise Suppressionは作らない。

最終：

```text
final_vowel_weight = vowel_probability * mouth_amplitude
```

全口形の合計が1を大きく超えないようnormalizeする。

## 8. Temporal Smoothing

母音判定をFrameごとに即切替すると口が震えるため、各口形へ指数Smoothを掛ける。

初期値：

```text
attack_tau  = 0.035 sec
release_tau = 0.080 sec
```

```text
alpha = 1 - exp(-delta / tau)
out = out + (target - out) * alpha
```

立ち上がりは速く、閉じ側は少し遅くする。

## 9. ExpressionController変更

現行：

```ts
setLipWeight(weight: number)
```

追加：

```ts
setLipWeights(weights: {
  aa: number
  ih: number
  ou: number
  ee: number
  oh: number
}): void
```

### 規則

- `aa / ih / ou / ee / oh`の存在をAvatarごとに確認する。
- 存在しない口形はno-opにし、利用可能口形へ無理に名前を推測しない。
- 5口形が揃わないAvatarでは、利用可能な口形だけへ再normalizeする。
- `aa`しか無い場合は現行Amplitude方式へfallbackする。
- Emotionの`overrideMouth`解除・復元は現行責務を維持し、各口形の制御中もEmotionと競合させない。
- LipSync終了時は5口形すべて0へ戻す。

## 10. MFCC受入条件

1. 現行Amplitude LipSyncをfallbackとして維持する。
2. A/I/U/E/Oの5口形が揃うVRMで、同一音量でも母音によって口形が変わる。
3. `あー / いー / うー / えー / おー`の単体QAで、対応口形が最上位Weightになる割合が各母音80%以上を目標とする。
4. 通常日本語TTSで1Frameごとの激しい口形振動が見えない。
5. Voice設定変更時は別Profileへ切り替わる。
6. Profile欠損・破損・生成失敗で会話/TTSを止めずAmplitudeへ縮退する。
7. 複数Residentで同じProfileを共有でき、ProfileをResident別コピーしない。
8. 30fps Worldで常時再生しても体感できる描画負荷増加を起こさない。

---

# Part B：Semantic Turn-End

## 11. 目的

音声入力実装時、単なる無音時間だけでMaster発話を確定しない。

```text
「えっと……」
「その……昨日の……」
「たしか、名前が……」
```

等の考えながら話す入力を途中送信しにくくする。

AIAvatarKitではAcoustic VADの後にSemantic Gateを置き、Candidateだけを意味判定する構成が成立している。Niraiでも**この責務分離だけを参考**にし、Gate本体は独自実装する。

## 12. Pipeline

```text
Microphone
→ AcousticVAD
→ partial STT
→ silence candidate
→ TurnEndGateManager
   ├ FillerGate
   ├ TailContinuationGate
   └ optional SemanticClassifierGate
→ END / WAIT
→ final STT text
→ master_say / master_whisper
```

Semantic判定を毎Audio Frameで走らせない。Acoustic VADが「一旦止まった」と判断した時だけ呼ぶ。

## 13. Candidate固定値

初期値：

```text
silence_candidate = 450 ms
hard_max_hold      = 3500 ms
hard_max_utterance = 90 sec
```

450ms無音で終了候補とし、GateがWAITした場合だけ追加待機する。再発話したらWAIT Timerを解除して録音継続する。

Hard limitを超えた場合はGateがWAITでも必ず確定し、録音Bufferを無限に保持しない。

## 14. Gate共通IF

```ts
interface TurnEndDecision {
  shouldEnd: boolean
  confidence: number
  holdMs: number
  reason: string
}

interface TurnEndGate {
  decide(input: {
    text: string | null
    silenceMs: number
    utteranceMs: number
  }): Promise<TurnEndDecision>
}
```

Manager規則：

- 1つでも有効Gateが`WAIT`なら待つ。
- `holdMs`は現在Candidateからの最大追加待機時間として扱う。
- 再発話でDecision状態をresetする。
- Gate Error / Timeoutは「会話入力不能」にせず、他Gateまたは通常終了へfallbackする。
- Hard limitが最優先。

## 15. FillerGate

日本語で明確な言い淀みだけを対象にする。

初期候補：

```text
えっと
えーと
あの
その
うーん
んー
なんていうか
なんだっけ
```

完全一致または文末Fillerなら追加待機する。

```text
exact filler  → hold 2500ms
suffix filler → hold 1800ms
```

短い返事として意味を持つ`はい / うん / そう / いや / 違う`はFiller扱いしない。

Filler一覧を大規模辞書化しない。実会話QAで必要なものだけ追加する。

## 16. TailContinuationGate

文末だけを軽く見る。

追加待機候補例：

```text
〜なんだけど
〜けど
〜だから
〜なので
〜それで
〜あと
〜例えば
〜というか
```

ただし日本語では`けど`等が自然な終止にもなるため、長時間Holdしない。

```text
ambiguous continuation tail → hold 700〜1200ms
```

句点相当や明確な完結表現がある場合は終了を優先してよい。

## 17. SemanticClassifierGate

Ruleだけで不足が実測された場合に追加する二段目。

目的は「返答内容を作る」ことではなく、入力が**まだ続きそうか / 完了しているか**だけを分類すること。

```text
input: partial/finalized STT text
output: END | WAIT
```

### 原則

- Resident Brain ContextやMemoryを渡さない。
-人格判断をさせない。
- Toolを許可しない。
- 1回の判定へ短いTextだけ渡す。
- 500〜800ms程度の短いrequest timeoutを設ける。
- classifierが無い環境でもFiller/Tail Gateだけで動く。
- LLM依存を必須化しない。

将来Nirai専用ローカル分類器を作る場合も同IFの後ろへ差し替える。

## 18. Turn-End受入条件

1. 450ms程度の普通の文末無音で体感遅延が不自然に増えない。
2. `えっと……`で450ms経過直後に送信されず待てる。
3. Filler後に話し直した場合、1つの発話としてSTTへ渡る。
4. `はい`等の短い完結返答を不必要に数秒待たない。
5. Gateが故障しても音声入力自体は通常のSilence終了へ縮退する。
6. 最大Hold / 最大発話時間を超えて録音が無限継続しない。
7. Say / Whisperの送信先判定は現行ChatBarと同じ規則を使い、音声入力だけ別Session規則を作らない。

---

# Part C：Presentation / Artifact

## 19. 目的

ResidentやAgentが話しながら「見せる」情報を、発話本文へHTMLや特殊Tagを混ぜず構造化して扱う。

対象：

- image
- slide / presentation
- web
- map / directions
- 将来：chart / video / document preview

AIAvatarKitの「voice textとscreen artifactを分離する」考え方だけを採用し、AIAvatarKitのControl Tag Schemaは採用しない。

## 20. Nirai共通Presentation

Core内でProvider固有出力を次へ正規化する。

```ts
type PresentationRequest =
  | { id: string; kind: 'image'; action: 'show' | 'clear'; url?: string; title?: string; alt?: string }
  | { id: string; kind: 'web'; action: 'show' | 'clear'; url?: string; title?: string }
  | { id: string; kind: 'slide'; action: 'show' | 'clear'; url?: string; page?: number; title?: string }
  | { id: string; kind: 'map'; action: 'show' | 'clear'; query?: string; origin?: string; destination?: string; travelMode?: 'walking' | 'driving' | 'transit'; title?: string }
```

公開Protocol名は実装時に01へ追加する。Raw HTMLをpayloadに持たせない。

## 21. Brainとの境界

将来BrainResponseへ任意Fieldを追加する。

```py
@dataclass(frozen=True)
class BrainResponse:
    say: str
    actions: tuple[dict[str, Any], ...]
    presentations: tuple[dict[str, Any], ...] = ()
    ...
```

ProviderのStructured OutputからNirai `PresentationRequest`へCoreで検証・正規化する。

発話Textから正規表現で`<artifact>`等を抜かない。

TTSへ渡すのは従来どおり`say`だけ。PresentationのURLやMap Queryを読み上げない。

## 22. Agent Runtimeとの統合

M4には既に共通Agent Event `artifact` がある。

Agent Runtime側はProvider Artifactを受けた場合、Eventの`artifact` payloadをWorld側で同じ`PresentationRequest`へ変換できる形へ寄せる。

```text
Conversation Brain
  └ presentations[]
        ↓
PresentationStore

Agent Runtime
  └ artifact event
        ↓ normalize
PresentationStore
```

会話用とAgent用で別Viewer Frameworkを作らない。

## 23. World構成

```text
world/src/renderer/src/presentation/
  PresentationStore.ts
  PresentationValidator.ts
  PresentationDock.tsx
  renderers/
    ImagePresentation.tsx
    WebPresentation.tsx
    SlidePresentation.tsx
    MapPresentation.tsx
```

初期UIは1つの`PresentationDock`だけを持つ。同時に複数Windowを乱立させない。

新しいPresentationが来た場合は現在表示を置換し、必要なら履歴はChat / Agent Event側の参照から再表示する。

## 24. Security

### URL

Remote sourceは初期仕様で`https:`だけ許可する。

拒否：

```text
javascript:
data:
file:
ftp:
URL username/password
localhost
127.0.0.0/8
::1
private network literal IP
```

Local fileはURL経由で開かず、既存の専用IPC / allowed path境界を使う。

### Web / Slide iframe

- sandbox必須
- `allow-same-origin`は初期では付けない
- top navigation禁止
- download権限を付けない
- RendererへNode APIを公開しない
- 表示できないサイトは失敗扱いにせず「外部Browserで開く」導線へ縮退する

### Map

LLMが任意のEmbed URLを直接指定する方式にしない。

`query / origin / destination / travelMode`を意味情報として受け、Nirai側のMap Resolverが許可済みProvider URLへ変換する。API Keyが必要なProviderはKey未設定時にMap Cardまたは外部Browser導線へ縮退する。

## 25. Presentation受入条件

1. Resident発話本文とPresentation命令が別Fieldで扱われる。
2. Presentation内容がTTSで読み上げられない。
3. imageを表示・clearできる。
4. web / slideはsandboxed Surfaceへ表示でき、埋込み拒否サイトでは外部Browser導線へ縮退する。
5. mapは任意URLではなく意味QueryからResolverを通す。
6. `javascript:` / `file:` / localhost / private literal IP等を拒否する。
7. Agent Runtimeの`artifact`と通常会話Presentationが同一World Viewerを利用する。
8. Presentation描画失敗でChat / Agent Sessionを失敗扱いにしない。

---

# Part D：実装順と境界

## 26. 推奨実装順

現在のM4を優先し、Masterが別途前倒ししない限り次の順で実装する。

### Slice P1：MFCC LipSync

既存Audio / VRM経路だけで閉じるため最初に実装する。

1. `MfccFeatureExtractor`
2. `LipSyncProfileBuilder`
3. `VowelClassifier`
4. `ExpressionController.setLipWeights`
5. `LipSyncController`統合
6. Unit Test
7. 実VOICEVOX 5母音QA
8. 通常発話QA

### Slice P2：Presentation

M4のArtifact表示拡張とまとめて実装する。

1. `PresentationRequest`共通型
2. Validator
3. Store / Dock
4. image
5. web / slide
6. map resolver
7. BrainResponse統合
8. Agent artifact統合
9. Security QA

### Slice P3：Voice Input + Semantic Turn-End

マイク入力自体と同時に実装する。

1. Mic capture
2. Acoustic VAD
3. partial STT
4. `TurnEndGateManager`
5. FillerGate
6. TailContinuationGate
7. ChatBar送信先との統合
8. 実音声QA
9. 必要な場合だけSemanticClassifierGate

## 27. 依存を増やさない原則

この3Sliceのために初期状態では次を追加しない。

- `aiavatar` Package
- AIAvatarKit Source
- uLipSync Source / Package
- 新しいCharacter Framework
- 新しいMemory Framework
- 新しいAgent Framework
- MFCC専用npm Package
- Artifact専用UI Framework

既存Web標準・TypeScript・Nirai Protocolで成立させる。

## 28. 実装AIへの禁止事項

- 「AIAvatarKitと同じにするため」という理由でRepositoryをcloneする。
- Sourceを見ながら行単位で書き換える。
- AIAvatarKitの独自TagをNirai Promptへ導入する。
- MFCCのためにResident人格・BrainをLipSyncへ接続する。
- Semantic Turn-Endのために毎Audio FrameでLLMを呼ぶ。
- Turn-End失敗でText入力まで壊す。
- Presentation失敗でResident発話を失敗扱いにする。
- Arbitrary URLをElectronの強い権限Surfaceへ読み込む。
- Conversation PresentationとM4 Artifact Viewerを別々に作る。

## 29. 調査元

2026-09-05確認：

- AIAvatarKit README
  - `https://github.com/uezo/aiavatarkit`
- Semantic Turn-End document
  - `https://github.com/uezo/aiavatarkit/blob/main/documents/vad-turn-end.md`
- Artifact document
  - `https://github.com/uezo/aiavatarkit/blob/main/documents/artifacts.md`
- MFCC方式の一般的な成立例としてuLipSyncの公開説明を参考対象に含めるが、Sourceは利用しない
  - `https://github.com/hecomi/uLipSync`

外部実装の更新で本書の前提が変わっても、Niraiへ自動追従しない。新しい良い手法が見つかった場合は、本書へ要件として取り込むかをMasterが判断してからNirai独自実装を更新する。
