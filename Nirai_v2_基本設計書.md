# Nirai v2 基本設計書

> 本書はNirai v2の現行仕様を定義する。設計・実装・テスト・文書運用の共通原則は`WORLD_RULES.md`を唯一の正本とし、本書へ重複記載しない。

## 1. 目的

Nirai v2は、AI・長期記憶・Tool・外部サービス・Worldを一つの場所から自然に組み合わせて使うための、Master専用AI Hubである。

PCに例えるなら、Niraiはマザーボードに相当する。

- 長期MemoryはSSD / HDDのような内蔵機能
- GPT、Cursor、Codex等のAIはCPU / GPUのような演算能力
- Web、画像生成、ファイル操作等のToolは周辺機器
- Holoは専用の接続方式を持つ重要な拡張機能
- World / DashboardはMasterがNiraiを使うための表示・操作面

各能力をNirai自身へ抱え込むのではなく、明確な境界で接続し、単体より便利に組み合わせて使えることを価値とする。

この「接続」は論理上の共通Interfaceを意味する。専用Message Brokerや分散Systemを導入すること自体を目的にしない。単一Process内の直接呼び出しで十分な場所は、そのまま直接つなぐ。

---

## 2. ゴール

Niraiから、AIで実現可能なことを一通り扱える状態を目指す。

最低限、以下を自然に組み合わせられること。

- 会話
- 複数Taskの同時実行
- 調査
- 実装・レビュー等のAgent Work
- 文章・画像等の生成
- Web / File / App等のTool利用
- ローカル長期Memory
- Task単位の継続実行
- Holo連携

機能数を増やすこと自体は目的ではない。
便利な機能でも、安定性・保守性・効率性・合理性・設定の明快さを大きく損なう場合は採用しない。

必要な機能が複雑になる場合は、先に単純な代替方法を探す。代替不能で目的達成に必要な場合のみ、その複雑さを責務境界の内側へ閉じ込めて実装する。

---

## 3. 全体構造

Nirai v2は次の6つの論理責務で構成する。

これらを別Process・別Serviceへ分割することを意味しない。初期v2では、分離する必然性がない限りHub Core内の小さなModuleとして直接接続する。

### Hub Core

Niraiの中心。

Capabilityの接続、Task管理、Run管理、安全確認、Resident設定を一つの規則で束ねる。

Hub Core自身は、AI推論・Memory検索・画像生成等の個別能力を実装しない。

### Capability

Niraiから利用できる能力の総称。

例：

- Local Memory
- GPT
- Cursor
- Codex
- Web Search
- Image Generation
- File Tool
- Holo Connector

内蔵機能か外部サービスかに関係なく、Hubから見た接続方法を揃える。

### Task Engine

Masterから依頼された仕事を継続する仕組み。

複数Taskを同時に進行でき、必要なCapabilityを組み合わせる。

### Local Memory

Niraiに備わるローカル長期記憶。

Niraiの正式な内蔵機能だが、Task EngineやAI Providerへ埋め込まず、一つの独立したCapabilityとして扱う。

### Resident

人格・Persona・使用AI・Model・Avatar等を束ねるMaster向けIdentity。

ResidentはCapabilityそのものではない。Residentが会話やTaskを行う際に、設定されたAI Capability等を利用する。

### World / UI

Say、Dashboard、Resident表示等を提供するPresentation層。

状態の正本を持たず、Hub Coreを操作・表示する。

---

## 4. 最重要不変条件

### 4.1 Hubは能力を接続する

Memory、AI、Tool、Holo等の個別機能をTask制御へ直接埋め込まない。

新しいAIやToolを追加するためにTask Engine本体の分岐を増やす設計を避ける。

### 4.2 責務ごとに正本は一つ

同じ状態を複数箇所へ独立して保存しない。

特にTask状態を、Chat、Provider Session、Renderer、Holo、Agent側ファイル等へ複製して正本化してはならない。

### 4.3 ChatとTaskは独立する

以下はTask状態を変更しない。

- Chatを閉じる
- Chat画面をReloadする
- Conversationを切り替える
- ChatGPT等の生成を止める
- Dashboardを閉じる
- DOMや外部UIが変化する

Task状態はHub Coreへの明示Commandだけで変更する。

### 4.4 ProviderはTask状態を持たない

Provider native Session / Threadは推論Contextとして利用できるが、NiraiのTask状態や再開位置の正本にはしない。

Provider Contextを失っても、Taskの目的・会話・完了済みRunの結果から安全に再構成できるようにする。

### 4.5 UIは状態を決めない

Renderer / Dashboard / Worldは表示用Projectionのみ持つ。

DBやTask状態を直接変更しない。

### 4.6 Master環境への操作はHubを通す

Task、Say、Holo等の入口に関係なく、AIがFile変更、Web操作、生成、別AI呼び出し等を行う場合、Niraiが管理する操作はHubのCapability経由で実行する。

Provider固有Toolから安全Policyを迂回してMasterの環境へ直接副作用を出さない。

Provider内部のSandbox等、Master環境へ副作用が出ない閉じた処理はCapability内部へ隠してよい。

---

## 5. Capability

CapabilityはNiraiへ能力を接続する最小単位とする。

Hub CoreはCapabilityごとの内部事情を知らず、共通Envelopeを通して呼び出す。

概念上の最小Interfaceは以下とする。

```text
Capability
  id
  availability
  operations
  invoke(operation, input, context)
  cancel(run_id)        # 対応可能なCapabilityのみ
  usage()               # 取得可能なCapabilityのみ
```

各Capabilityは自身の入力を検証し、自身の外部API・Process・保存方式を内部へ閉じ込める。

Capabilityには複数の`operation`を持たせてよい。

例：

```text
memory.recall
memory.remember
cursor.work
codex.review
image.generate
web.search
holo.respond
```

Hub CoreへProvider固有のRequest形式を漏らさない。

### Capability Registry

利用可能なCapabilityは一つのRegistryから解決する。

初期v2では明示的な登録で十分とし、動的Plugin探索やPlugin Package規格を先回りして作らない。必要性が生じた時だけ拡張する。

Registryは最低限、以下を返す。

- Capability ID
- 利用可否
- 利用可能Operation
- 利用量 / Limit（取得可能な場合のみ）
- 高負荷等の実行特性（既知の場合のみ）

取得できない情報を推測しない。

---

## 6. Task

TaskはMasterが認識する一つの仕事であり、Niraiの継続実行単位である。

TaskはConversationから独立して永続する。

最低限保持する情報：

- Task ID
- タイトル
- 目的 / 最初の依頼
- 担当Resident ID
- 状態
- Resume ON / OFF
- 作成・更新・終了時刻
- 結果要約

### Task状態

Task状態は以下の5種類だけとする。

- `Running`
- `Paused`
- `Completed`
- `Failed`
- `Cancelled`

`NeedsInput`をTask状態にはしない。

確認待ちや質問待ちは後述するMaster Requestとして別管理する。並列処理中の一部が確認待ちでも、他の安全な処理は継続できるためである。

### Task状態とResume設定

Task状態とResume ON / OFFは別の概念として持つ。

- Task状態は「今そのTaskを動かしてよいか」を表す。
- Resume設定は「現在の仕事が一区切りついた後も、Niraiが自動で次の仕事へ進むか」を表す。

したがって、以下はいずれも正しい状態である。

- `Running + Resume ON`：現在の仕事を進め、一区切りついても完遂まで自動継続する。
- `Running + Resume OFF`：現在の仕事は進めるが、一区切りついた後に次の仕事を自動開始しない。
- `Paused + Resume ON`：今は停止中。MasterがTaskを再開した後は完遂まで自動継続する。
- `Paused + Resume OFF`：今は停止中。再開後も一区切りごとに自動継続しない。

Resume ON / OFFをTask状態へ変換したり、`AutoResuming`等の追加状態を作ったりしない。

新規Taskは最初の依頼送信後に`Running`となり、Resumeは初期値`OFF`とする。

---

## 7. Run

RunはTaskがCapabilityを一回利用する実行記録である。

旧Niraiの`Step`と`Attempt`を別々の恒久概念にはしない。

調査、実装、レビュー、画像生成、Memory検索等はすべてRunとして扱える。

最低限保持する情報：

- Run ID
- Task ID
- Capability ID
- Operation
- 状態
- 入力参照
- 結果参照 / 結果要約
- 開始・終了時刻
- 必要なら`retry_of`

### Run状態

- `Pending`
- `Running`
- `Completed`
- `Failed`
- `Cancelled`
- `Interrupted`

Retryは同じRunを書き換えて再利用せず、新しいRunを作成し`retry_of`で元Runを参照する。

Taskは複数Runを同時に持てる。

RunはTask内の継続作業だけに使う。Sayでの通常会話、単発のMemory参照、設定画面の軽い取得等までRunとして永続化しない。同じCapabilityを使っても、Task外の単発利用は軽量な直接呼び出しとして扱う。

同じ資源へ競合するRunだけを直列化し、無関係なTaskやRunを全体Lockで止めない。

---

## 8. Task Engine

Task Engineの責務は一つとする。

> RunningなTaskの現在の仕事を進め、Resume ONの場合は利用可能なCapabilityを使って完遂または安全な停止点まで自動継続する。

Task EngineはHub Core内の小さな実行Moduleとし、独立Serviceや常時監視Supervisorを作らない。

基本動作はイベント駆動とする。

- Task開始 / Pausedからの再開
- Masterからの明示的な続行指示
- Run完了 / 失敗 / 中断
- Master Request解決

等、Taskを進められる状態変化が起きた時だけ次の行動を評価する。`stalled`検出のための定期ポーリング、Lease、Heartbeatを標準構造にしない。

Run完了だけを理由に次の新しいRunへ進めるのは、そのTaskが`Running`かつResume ONの場合に限る。Resume OFFでは、現在のRunを正常に終えて結果を保存した後、自動で次Runを開始しない。Masterの新しい指示や明示操作があれば再び進めてよい。

Task EngineはTaskごとに担当Resident / AIへ現在の目的、MasterとのTask Chat、完了済みRunの要約、利用可能Capabilityを渡し、次に必要な行動を決めさせる。

AIが選んだ行動はHub Coreを通してRunとして実行する。互いに独立した複数行動が返された場合は複数Runを並列開始してよい。

Task全体の工程を固定DAGとして先に永続化しない。完了したRunの結果を受けて次の行動を再評価する。これにより途中の発見や方針変更へ自然に追従し、Step依存管理を別Systemとして持たない。

AIが直接DBを書き換えたり、独自Task Queueを持ったりしてはならない。

### 完了

Taskを`Completed`にするのはHub Coreである。

AIが自然文で「完了」と発言しただけでは完了扱いにしない。

Task Engineが完了結果を構造化してHubへ返し、Hubが未完了Run・未解決Master Request等を確認した上で確定する。

### 失敗

個別Runの失敗だけでTask全体を`Failed`にしない。

別Capability、修正、限定Retry等で安全に続行できる場合はTaskを継続する。

安全な続行方法がなくなった場合のみ`Failed`とする。

同一原因の機械的Retryには内部上限を設け、無限Retryしない。

---

## 9. 複数Taskと並列実行

複数Taskの同時進行は必須要件とする。

Task AのCursor作業中に、Task BでWeb調査、Task Cで画像生成等を同時に行える。

並列数を固定の「Agent全体1件」で制限しない。

制限が必要な場合は、以下の実資源単位で行う。

- 同じ作業FolderへのWrite競合
- 同一Provider側の同時実行制限
- API Limit
- CPU / GPU等の重い資源

一つのTaskやCapabilityの障害を、無関係なTaskへ波及させない。

---

## 10. Auto Resume / Pause / Restart

### Resume ON / OFF

ResumeはTask単位の設定とする。

Resume ONでは、Niraiが起動しておりTaskが`Running`である限り、現在のRunが終わった後もTask Engineが次の仕事を評価し、確認待ち・失敗・完了・Pauseのいずれかへ到達するまで自動で進める。

Resume OFFでは、現在実行中のRunは通常どおり完了させるが、その完了だけを理由に次の新しいRunを自動開始しない。

Resume設定を切り替えてもTask状態は変更しない。

これがNirai v2におけるAuto Resumeであり、専用のWorkflow、Lease、Outbox、Conversation ownership等は作らない。

### Pause

MasterがPauseするとTaskを`Paused`にする。

Paused後はResume ON / OFFに関係なく新しいRunを開始しない。

実行中Runは、安全に中断できるCapabilityなら停止し、中断より完了保存の方が安全な短い処理は現在のRunだけ完了させてよい。

PauseしてもResume設定は変更しない。

### Taskの再開

MasterがPaused Taskを再開するとTaskを`Running`へ戻す。

Hubに残ったTask目的、Task Chat、Run結果から続行する。

Resume ONなら、その後は完遂まで自動継続する。Resume OFFなら、現在の明示された仕事を進めた後は自動で次Runへ進まない。

Chat画面や外部Conversationから再開地点を推測しない。

### Nirai / PC再起動

再起動前に`Running`だった未完了Taskは、起動時に`Paused`として復元する。

Resume ON / OFFの設定値はそのまま保持する。

勝手にTaskを`Running`へ戻さない。

MasterがTaskを再開した後、Resume ONなら再び完遂まで自動進行する。Resume OFFなら自動連鎖しない。

再起動時に生きていないRunは`Interrupted`として確定し、Task再開後にTask Engineが続行方法を判断する。

Provider Processを無理に「途中から生存している」と仮定しない。

---

## 11. Master Request

危険操作の承認や、Masterにしか答えられない質問は、Task状態とは別の`Master Request`として保持する。

確認の仕組みをApprovalとInputへ分けず、一つのRequestに`kind`を持たせる。

- `approval`：実行してよいかを確認する
- `input`：不足している判断・情報を求める

これにより、一つのRunがMaster待ちでも、同じTask内の独立した安全作業や他Taskを止めない。

Master Requestは最低限、以下を持つ。

- Request ID
- Task ID
- kind
- 要求元Run / 提案操作
- 理由 / 質問
- Masterへ見せる内容
- 状態 `Pending / Resolved / Cancelled`
- Masterの回答

`approval`の回答はApprove / Reject、`input`の回答は必要な値または文章とする。

Masterは後で回答してよい。高負荷処理を寝る前まで保留する場合も、特別なScheduling機構を作らずPendingのまま置けばよい。

Master Requestの解決で止まっていた同じRunは、Taskが`Running`であればResume ON / OFFに関係なく続行してよい。これは新しいRunの自動開始ではなく、Masterが回答した現在の仕事の続きだからである。

そのRun完了後に次の新しいRunへ進むかはResume設定に従う。

---

## 12. 安全確認

通常の調査、会話、実装、検証、Memory参照、Agent間の依頼等は自動進行する。

以下は実行前にMaster確認を必須とする。

- 大規模な破壊・大量削除
- 取り返しがつきにくい変更
- 課金が発生する操作
- Unity等の大型Program / Runtimeの導入
- CPU / GPUを長時間ほぼ占有する処理
- PC再起動

権限・公開範囲等の変更で実質的に取り返しがつきにくいものも同じ扱いとする。

確認はCapabilityごとに独自実装せず、Hub Coreの共通Policy Gateを通す。

Capabilityは実行前に既知のRisk / Resource HintをHubへ渡す。

HubがMaster Requestを作成した場合、その回答が必要なCapabilityは開始前に停止する。

---

## 13. Say / Task Chat

### Say

Residentとの通常会話を行う生活空間。

Taskの内部実行ログは流さない。

### Task Chat

特定TaskについてMasterと担当Residentが会話する場所。

Task ChatはTaskの状態正本ではない。

閉じてもReloadしてもTaskは変化しない。

Task Chatには人間が読む価値のある情報だけを表示する。

以下を大量に表示しない。

- Tool Call全文
- 内部Prompt
- 全探索ログ
- テスト全ログ
- Provider内部Event
- Retry内部ログ

Taskの現在地はRunの状態からHubが表示用に要約する。

---

## 14. AI同士の連携

AI同士の連携のために専用の`Collaboration Thread`を恒久概念として作らない。

担当AIが別AIやToolを必要とした場合、Hubを通してそのCapabilityをRunとして呼び出す。

例：

```text
Holo Task
  -> web.search Run
  -> cursor.work Run
  -> codex.review Run
  -> image.generate Run
```

結果はTaskへ戻る。

これにより、「AI同士の会話」と「実際の仕事」を別管理せず、Capability利用という一つの仕組みへ統合する。

必要な説明だけTask Chatへ要約する。

---

## 15. Local Memory

Local MemoryはNiraiに標準搭載する長期記憶機能である。

外部Projectではないが、Hub上ではAIやToolと対等なCapabilityとして扱う。

最低限のOperation：

- `remember`
- `recall`
- `forget`

### 正本

長期Memoryの正本データはローカルに保存する。

検索IndexやEmbedding等は再生成可能な派生データとして扱う。

Memory機能は特定AI Providerが存在しなくても、最低限の保存・取得が成立する構造とする。

将来Semantic Retrieval等で外部AIを補助利用する場合も、Memoryの正本を外部Providerへ移さない。

HoloやSerina等、別Projectが自身で管理する長期MemoryはNirai Memoryへ吸収・統合しない。Niraiは必要ならそれらを外部能力として接続するが、正本の所有権を奪わない。

### 境界

MemoryはTask状態を持たない。

Task Engineは必要な時にMemory Capabilityを呼ぶ。

通常会話からMemoryへ保存する場合も同じCapabilityを利用する。

ArchiveとMemoryを混同しない。

Task履歴があるだけで自動的に長期Memoryとはしない。

---

## 16. Resident / Persona

Residentは不変のResident IDを持つ。

表示名変更でTask、Memory、設定等を書き換えない。

Resident設定は最低限以下を持つ。

- Resident ID
- 表示名
- Role
- Persona
- 使用AI
- Model
- Avatar

旧NiraiのPersona本文は内容を変更せず移行する。

Personaと`WORLD_RULES.md`は別の正本とし、共通RulesをPersonaへ複製しない。

通常Residentは選択されたAI Capabilityを利用する。

Holoは専用Connectorを利用する特殊Residentとして扱う。

---

## 17. Holo

Holo連携はNirai v2の必須機能とする。

ChatGPT Web、Login、Dive、Local Tool接続等のHolo固有事情は`Holo Connector`境界へ閉じ込める。

Holo Connectorが独自に以下を持ってはならない。

- Task状態
- Auto Resume状態
- Workflow
- Retry Queue
- Task ownership
- Task完了判定

HoloがTaskを進める場合も、他AIと同じHub Command / Capability Runを使う。

HoloからCursorやMemory等を使う場合も、Hubを通す。

Holo固有の複雑さをNirai全体へ伝播させないことを最優先する。

---

## 18. Settings

設定は「どこで変えるか」が一意に分かることを重視する。

同じ設定をUI、設定File、Provider別Fileへ重複して持たない。

動的なNirai設定・Resident設定はHub側の一つの設定正本へ集約する。

Persona本文等、人が直接編集することに価値があるものだけ独立Fileを正本としてよい。

Providerの秘密情報・Credentialは設定DBへコピーせず、各Providerの安全なCredential境界を利用する。

---

## 19. 保存構造

動的なHub状態は単一SQLiteを第一候補とする。

概念上は以下を一つのHub Storeで扱う。

- Task
- Run
- Master Request
- Resident Registry
- Settings
- Say / Task Chatの必要なTranscript
- Provider native Session等の再生成可能な接続参照

Local Memoryは責務が異なるため、独立したMemory Storeを持つ。

したがって「DBを一個しか持たない」ことを目的にはしない。

重要なのは、同じ責務の正本を複数作らないことである。

生成Logや一時Cacheは正本にしない。

---

## 20. Control API

UI、Holo、Local Tool等からHubを操作する意味上の入口は一つにする。

最低限のCommand：

- CreateTask
- PauseTask
- ResumeTask
- SetTaskResume
- CancelTask
- SendTaskMessage
- ResolveMasterRequest
- SendSay
- UpdateResident

IPC、WebSocket、Local MCP等のTransportが複数必要でも、意味上のCommandを別々に実装しない。

各Transportは同じHub Commandへ薄く接続する。

---

## 21. Dashboard

DashboardはTask管理の中心UIとする。

見た目・配置・Resident欄・Task / Archive欄・Task Chat等のUI基準は`prototype/`を正とする。

Backend上の恒久概念をUI都合で増やさない。

### 表示する主要情報

- Active Task
- Task状態
- Task単位のResume ON / OFF
- 現在のActivity
- 確認待ち
- Resident / AIのOnline状態
- Provider Limit / Usage（取得可能な場合）
- 最近完了したTask

Task Chatヘッダーでは、Task状態を変えるPause / 再開操作と、Resume ON / OFF切替を別操作として置く。片方を操作してももう片方は変更しない。

旧設計の`Step`表示はBackendの独立Entityを意味しない。

Task配下の行は、現在または最近のRunを人間向けのActivityとして表示する。

### CHECK

Dashboardの`CHECK`は、PendingなMaster Requestを持つTask件数を表す。

CHECKはTask状態ではないため、RUNと重複してよい。

例：別Runを継続中のTaskが一件のMaster Request待ちを持つ場合、RUNとCHECKの両方へ数える。

---

## 22. Task作成

Dashboard上部のResidentを選び、`＋`でTaskを即作成する。

事前ダイアログを挟まない。

最初のMaster指示を送った時点でTaskを`Running`にし、担当Residentが内容からタイトルを生成する。Resume初期値は`OFF`とする。

最初の指示送信前のみ担当Residentを変更できる。

開始後の担当変更が必要になった場合は、自動移管ではなくTask上の明示操作として将来追加できる。必要性が確認されるまで実装しない。

---

## 23. Archive / Retention

Archiveという別状態・別Storeは作らない。

`Completed / Failed / Cancelled`のTerminal TaskをDashboard上でArchiveとして表示するだけとする。

Terminal Taskは終了から90日後にHub Storeから自動削除する。

Task専用の一時Run結果・Log等も同時に削除してよい。

Taskが生成したPJ本体、共有成果物、Local Memoryは削除しない。

---

## 24. 外部能力の追加

新しいAI、API、Tool、生成サービスを追加する時は、原則として新しいCapability Adapterを一つ追加するだけで済む構造にする。

Task Engine、Dashboard、Memory等へProvider名ごとの条件分岐を追加しない。

Capability共通契約で表現できない機能が必要な場合は、まず共通契約を無理に肥大化させず、そのCapability固有Operationで表現できないか検討する。

Hub Coreの恒久概念を増やすのは最後の手段とする。

---

## 25. 旧Niraiからの移行原則（移行期間のみ）

旧Niraiは再利用元であり、v2設計の土台ではない。

再利用判定は、

> 既に動くか

ではなく、

> v2でゼロから作るより単純・安全・保守しやすくなるか

で行う。

### 原則再利用する候補

- Provider接続の安全な低レベル処理
- Process制御
- Credential隔離
- Workspace安全境界
- Cursor等のstaging / diff / apply / rollback
- Usage / Limit取得
- VRM / Animation / LipSync / Environment等の独立Presentation部品
- Task状態を持たないUtility

### 責務を剥がしてから再利用する候補

- AI Adapter
- Memory / Retrievalの検索技術
- Holo Web Surface / Security
- Resident管理
- Provider native Conversation

### 持ち込まない

- 旧Task Queue / Task Runtime
- Workflow
- Workflow Lease
- Auto Resume専用経路
- Holo Auto Resume Outbox
- Conversation ownershipによるTask制御
- Agent SessionをTask状態の正本とする構造
- Step / Attemptを必要以上に恒久化した状態管理
- Chat DOMや生成停止からMaster意思を推測する処理
- 旧履歴・旧Evidence・旧設計互換層

現在のHolo Local連携は、v2 Control APIへ置換されるまで開発経路としてのみ一時保持する。

Persona本文は内容を変更せず移行する。

本章は移行完了後に削除する。

---

## 26. 受け入れ条件

最低限以下を満たすこと。

1. Niraiから会話、Task、生成、Memory、Tool、Holo等の能力を共通Hub経由で利用できる。
2. 新しいAI / Toolは原則Capability Adapter追加だけで接続でき、Task EngineへProvider固有分岐を増やさない。
3. 複数Taskと複数Runを同時進行でき、無関係な仕事同士を全体Lockで止めない。
4. Task状態とTask単位のResume ON / OFFが独立しており、`Running + Resume OFF`と`Paused + Resume ON`を正しく扱える。
5. Nirai / PC再起動後は未完了Taskが勝手に再開せず、Resume設定を保持したまま`Paused`で復元される。MasterがTaskを再開した後、Resume ONなら完遂まで自動進行する。
6. ChatのReload・停止・切替・閉鎖がTask状態へ影響しない。
7. 大規模破壊、不可逆操作、課金、大型Program導入、高負荷処理、PC再起動は実行前にMaster確認される。
8. 一つのMaster Request待ちが、無関係なTaskや同一Task内の独立Runを不必要に停止しない。
9. Local Memoryの正本はローカルにあり、Task / Provider状態と独立する。
10. Holo固有のWorkflow / Auto Resume / ownership状態を持たず、他Capabilityと同じHub Commandへ接続する。
11. Residentは不変IDを持ち、旧Persona本文を変更せず利用できる。
12. Task状態、Run状態、設定の同一正本をRenderer、Provider、Chat等へ複製しない。
13. Terminal Taskは別Archive Storeへ移さず同一Storeの履歴として扱い、90日後に整理できる。
14. DashboardはBackend都合の概念を増やさず、Task・Activity・CHECK・Resident・Usageを簡潔に表示できる。
15. 重要機能を保ったまま、旧NiraiのWorkflow / Step / Attempt / Auto Resume / Conversation ownership中心のControl構造を削除できる。
