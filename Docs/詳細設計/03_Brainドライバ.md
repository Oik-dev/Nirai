# Nirai 詳細設計 03：Brainドライバ（頭脳）

Product Goalは [Nirai_基本設計.md](../Nirai_基本設計.md)、設計判断ルールは [Nirai_設計ガバナンス.md](../Nirai_設計ガバナンス.md)、行動コマンド語彙は [01_通信プロトコル.md](01_通信プロトコル.md) を正とする。Whisperの長期Conversation方式に至った旧比較は [Nirai_Reference-First調査_Whisper長期Conversation_2026-09-06.md](../archive/research-and-old-design/Nirai_Reference-First調査_Whisper長期Conversation_2026-09-06.md) をReferenceとし、Current Contractは本書を優先する。

## 概要

Brainドライバとは、住人の思考を外部AI（定額サブスクのCLI等）に問いかける差し替え可能な部品である。

- 前提
  - 全ドライバは共通の窓口を実装する：**コンテキストを渡すと、発言と行動が返る**
  - 全Brain呼び出しは`invocation_id`を持ち、会話中の呼び出しはキャンセル可能にする
  - 呼び出しの同時実行は02のResource / Provider / Conversation Policyに従う。Nirai全体Global 1件を永久Invariantにしない
  - Brain Driverは会話・生活判断用とし、PC上の実作業`work`は11のAgent Runtimeへ分離する。Providerが同じでも会話DriverとAgent Runtime Adapterを1クラスへ統合しない
  - Codex / Claude / Cursorは定額サブスクCLIを優先し、従量課金APIへ自動フォールバックしない。GeminiはMasterが用意したGemini Developer API Keyを利用し、Free Tier運用を想定する。Niraiから有料枠への自動切替は行わない
  - CLIの引数仕様は実装時の実物を正とする。Gemini APIは実際の`models.list` / `generateContent`応答を正とし、差異があれば本書を更新する

## 共通インターフェース

```
think(invocation_id, mode, resident, context) → BrainResponse
cancel(invocation_id) → CancelResult
```

| 引数 | 説明 |
|------|------|
| mode | "talk"（公開会話）/ "whisper"（個別会話）/ "tick"（生活）/ "consult"（タスク相談） |
| resident | 住人情報（persona、config） |
| context | モードごとの入力（下記） |

`cancel(invocation_id)`は対象のBrain呼び出しと、その呼び出しが起動した子プロセス群を可能な範囲で終了させる。会話UIの停止ボタンは、現在のMaster発話から開始された全Residentの会話用invocationをキャンセルする。実行中TaskのAgent Session停止は別Protocolとし、11を正とする。

Windowsでは親CLIだけを終了して子プロセスを残さないよう、Brainプロセス管理層でプロセスツリー単位の停止手段を持つ。具体方式は既存ライブラリまたはWindows標準機構を利用し、独自プロセス管理基盤を作らない。

## Conversation Continuity Contract

Residentの長期会話では、Nirai側のIdentity / MemoryとProvider側のnative Conversation Contextを分離する。

### 正本の分担

- **Resident Identity / Persona / Relationship / Long-term Memory**：Niraiが正本
- **Nirai Conversation identity / lifecycle / participant / turn ordering**：Niraiが正本
- **Provider native Session / Thread / Conversation ID**：そのConversationを効率よく継続するためのTransport Context。Nirai Identityではない
- **Raw / Structured / Retrieval Memory**：06のNirai Memoryが正本。Provider native Contextだけへ閉じ込めない

### Provider native continuation

Providerが正式なSession / Thread / Resume / Load等を持つ場合は、同一Nirai Conversationの後続Turnで積極的に再利用する。

- 過去N件の原文を毎Turn無条件に再送する方式を標準にしない
- Persona / Nirai Skills / 固定会話ルール等、Provider native Contextが既に保持している静的Contextも毎Turn再送しない。Conversation開始・Core再起動後の安全なrefresh・Persona/Skill変更時・Provider compaction検知後だけ再注入する
- Provider native compaction / continuationを利用できるなら利用する。通常Turnは新規会話差分と、そのTurnで新たに必要になったMemory / Observation等の動的Contextを中心に渡す
- Provider native Contextは再生成可能なTransport Cacheとして扱う。ProviderがTurnを処理した後でも、対応するNirai側Response / markerがDurableにcommitされるまでは`pending`であり、Core crash後にそのCacheをそのまま継続してはならない。Current実装は`pending_turn`を保存し、Nirai側のChat / Conversation transcript / Private Memory等の正本commit直後、最初のtransport / World publication `await`より前に同期`mark_seen`してのみ継続可能へ進める。これにより正常Turnがcommit済みなのに次Turnから`pending`へ見える競合窓を作らない。Crash時にpendingならProvider Contextを破棄してNirai正本から再構築する
- native Contextが失われた場合に備え、Nirai側にはlosslessな会話・Memory正本を残す
- native Sessionの再利用でPrivate/Public境界を跨がない。Say / Group ConversationはNirai Chat Session + Residentごと、WhisperはResidentごとのPrivate Channelとして別native Contextを持つ
- 新しい公開Chat Sessionを作成した場合、公開会話のnative Contextは新規になる。過去の別Chat SessionはRaw履歴として直接継承せず、関連するWorld MemoryだけをRetriever経由で必要時に参照する
- Whisperは公開Chat Sessionへ従属させない。ResidentごとのPrivate Channelを継続し、Provider native Working ContextはProvider自身のresume / compactionを優先して利用する。Nirai独自の一定Turnローテーションは行わず、native Context失効・破損・Provider変更等で本当に再構成が必要な時だけNirai MemoryからWorking Contextをrebuildする。5年分Raw Whisperを丸ごとPromptへ載せない
- Provider交換時は新ProviderへNirai Memory / Structured Continuity / 必要なRaw参照からContextを再構成し、旧Provider native Sessionを移植必須データにしない

### Current implementation state / gap

Holo→Cursor / CodexのProvider Conversation、および通常ResidentのCursor / Codex / GeminiはProvider native continuationへ移行済み。通常Turnでは既送会話履歴・Persona・Skills等を毎回再送せず、新規差分中心にする。通常Resident CursorはCursor CLIの`session_id` + `--resume <chatId>`を使用し、Resident選択Model IDをそのまま渡す。実機で`cursor-grok-4.6-xhigh`の非fast Turnを開始し、同一`session_id`へ2 Turn目をresumeして前Turn文脈を再現できることを確認済み。2026-09-08総合監査ではCodexも含むCursor / Codex / Geminiの3 Providerすべてで、1 Turn目のrandom識別語を2 Turn目のnative continuationから再現するLive 2-turnを確認済み。

Gemini Interactionsも`previous_interaction_id`を利用するnative continuationへ実装済みで、2026-09-07に実Provider 2-turn Smokeを通過した。Current CoreはCodex / Cursor / Geminiの全native Resident Conversationで、同一logical Conversation lockをhistory delta選定前に取得し、Provider TurnからNirai側Response / markerのcommit完了まで保持する。Codex / Cursorの`NativeConversationBrainService`はCore-owned lockを再取得せず、Serviceを直接利用する呼び出しだけ従来どおり自己Lockする。Provider保持期限切れ時はNirai側正本からWorking Contextを再構成する。Codexは公式`contextCompaction`通知を検知し、Thread IDを維持したまま次Turnのstatic / Memory contextをrefreshする。通常Resident CursorはCLI native chatを利用し、Nirai独自の推測ローテーションは行わず、`--resume`失敗時だけProvider stateを無効化してNirai正本から再構成する。Holoのread-only Provider Conversation / ReviewとAgent RuntimeはCursor共通staging安全境界を維持しつつ、ACPで選択Modelを正確に表現できる場合はACP、`cursor-grok-4.6-xhigh`等のCLI-only exact ModelはCursor CLIへ分岐する。Claude Codeは現在Provider自体を利用不可としているため、再開時に現行公式continuation能力をReference-Firstで確認して同Contractへ合わせる。

## プロンプト構成

Brainに渡すプロンプトは次の順で連結した1つのテキストとする。

1. **共通ヘッダ**（全モード共通・固定文）
   - `あなたはNiraiという箱庭世界に暮らすResident「{name}」です。`
   - `Niraiは水面から光が届く静かな海中世界です。Masterはこの世界の創造主です。`
   - Resident自身の世界認識へ`3D`、Renderer、画面、アプリ等の実装メタ情報を混ぜない
   - この固定説明は「Niraiがどういう世界か」を示す設定であり、「今この瞬間に何が見えているか」の観測事実ではない
   - 応答は必ず後述のJSON形式1個のみで返すこと（前後に説明文を書かない）
2. **人格**：`residents\<名前>\persona.md`の全文
3. **Nirai Skills**：Task時だけ使う`skills\<name>\SKILL.md`索引＋遅延本文
   - Skillの正本はNirai Root直下の`skills\`とし、Codex / Claude / Cursor等のProvider固有Global Skill Directoryへ複製しない
   - `SKILL.md`はUTF-8、front matterに`name` / `description`を必須とし、`name`はDirectory名と一致させる
   - Coreの共通Skill Registryはfront matterだけから軽量索引を構成し、Core起動後にSkillを追加しても次回Taskから反映できる
   - **talk / whisper / resident_chat等の通常会話へSkill索引・本文を常時注入しない**。Skillが0件なら当然追加Contextは発生しない
   - Task相談時は`name / description`の索引だけをContextへ渡す。実Agent Work開始時にTask文と索引から関連Skillを選び、選ばれた`SKILL.md`本文だけを遅延読込してWork Promptへ追加する
   - Crash後の`rerun`も同じWork Prompt Enricherを通し、通常開始と同じSkill選択規則を使う
   - 1 Skill本文64 KiB、1 Taskへ選択して注入する本文合計128 KiBを安全上限とする。索引自体は全Skill本文の合計容量制限から独立し、後方のSkillが索引から消えない
   - 不正・読取不能・上限超過の本文はそのSkillだけ無視し、Task / Nirai本体を停止しない。参照Fileは自動展開せず、必要なSkillは自己完結を基本とする
4. **記憶**（06の選別規則に従う）
   - Say / resident_chat / tick：M3以降はWorld MemoryのRetriever結果＋必要な直近公開履歴。M1〜M2はRetriever未実装なので現在セッションと蓄積済み公開履歴だけを使う。Private Memoryは渡さない
   - Whisper：宛先Resident本人のPrivate Memory / 必要な直近Whisper / 関連するWorld Memoryを、Provider Working Contextの状態に応じて必要分だけ渡す。現行`context.md`は直近Raw由来の暫定Viewであり、Structured Continuity実装後の長期正本として扱わない
   - talk / whisper / resident_chatでは、履歴とは別に現在有効なResident一覧を渡す。削除済みResidentの過去発言は履歴として残してよいが、その名前を現在の在席情報として扱わせない
   - 各CLI自身のセッション再開・Memory機能はNirai Memoryの正本にはしない。ただし同一Nirai Conversationの効率的なContext継続にはProvider native Session / Threadを積極的に利用する
5. **現在のWorld Observation**（M3以降。01・02参照）
   - `captured_at`と観測可否
   - 自分の現在Locationと行動状態
   - 近くにいるResident、そのおおまかな距離・状態
   - Masterが現在FocusしているResident
   - 現在時刻・時間帯
   - 実際のWorld Presentationから意味化された環境状態（光、水面、視界、Caustics等）
   - 必要なら直近のWorld Event
   - World未接続・Snapshot未取得・Snapshotが古すぎる場合は「現在の観測なし」と明示し、固定世界説明や過去Memoryから現在状態を推測させない
6. **モード別の指示と入力**
   - talk：現在Turnに必要なNirai Context＋「Master（または相手）に返事をする。話すことがなければpass」。Provider native continuationが利用可能な同一Conversationでは過去原文を毎Turn固定件数で再送しない。native continuationが利用不能・再構成が必要な場合だけ、Structured Continuity / Retriever / 必要なrecent rawを組み合わせる。resident_chatでは参加者一覧・直前発言者・直前宛先も渡し、必要なら`to`で次のResidentを指名できる
   - whisper：公開Contextに加えて宛先Resident本人のPrivate MemoryとWhisper履歴を渡し、Masterへ個別に返事をする
   - tick：選べる行動一覧（02参照）＋「今なにをするか1つ選ぶ」。常時の小さな漂い・姿勢変化等はWorld Natural Idleの責務なので、存在感維持だけを目的にBrainへ選ばせない
   - consult：タスク内容＋これまでの相談履歴＋「意見と、立候補するかどうかを返す」

### 固定世界説明と現在観測の区別

Brainは、共通ヘッダに書かれた恒常的な世界設定と、World Observationに入った現在事実を区別する。

- 「Niraiには水面から光が届く」は固定世界説明として言ってよい
- 「今は水面からの光が強い」は現在Observationに`light=bright`等の根拠がある場合だけ現在事実として扱う
- 「Codexが近くにいる」「Masterがこちらを見ている」等もObservationに根拠がある場合だけ断定する
- Observationが無い場合でも会話自体は継続するが、現在見えているものを創作して埋めない
- World ObservationはPrivate Memoryではない。Whisper中にも宛先Residentへ渡してよいが、Whisper本文をObservationへ逆流させない

これによりResidentは設定文を演じるだけでなく、実際のWorld状態に反応して発言・行動できるようにする。

## 応答JSON形式（talk / whisper / tick / consult 共通）

| フィールド | 型 | 必須 | 説明 |
|-----------|----|------|------|
| say | string | — | 発言。無言なら空文字 |
| actions | array | — | 行動コマンドの配列（01の語彙）。例：`[{"command":"move","args":{"location":"light_area"}}]` |
| talk_to | object | — | tickのみ。`{"name":"相手","text":"最初のひとこと"}` |
| to_master | string | — | tickのみ。Masterへのひとこと |
| volunteer | bool | — | consultのみ。立候補するか |
| pass | bool | — | resident_chatでは「今は付け加えることがない」という一時沈黙。会話退出ではなく、後続の新発言後に再参加できる |
| to | string / null | — | resident_chatのみ。次に話を振りたい参加Resident名。Group全体向け・指名なしはnull。無効名や自分自身はCoreが無視する |

- 応答例（tick）：

```json
{"say": "光の筋が今日は長いな", "actions": [{"command":"move","args":{"location":"light_area"}}, {"command":"afk","args":{}}], "pass": false}
```

## 応答のパース規則

1. CLIの出力から最初の`{`〜対応する最後の`}`を取り出してJSONパースする
2. 失敗したら同じプロンプトで1回だけリトライする
3. リトライも失敗したら
   - talk：発言なし（02のエラー方針）
   - tick：何もしない扱い（予算は消費しない）
4. `actions`に未知のコマンドが混ざっていたら、そのコマンドだけ捨てて残りを実行（WARNログ）
5. `say`が200文字を超える場合：talk / resident_chat / tick等の**公開発話**では全文を現在のchat_sessions履歴と会話UIへ残し、Worldの吹き出しには先頭60文字＋「…」を送る。`mode=whisper`の`say`はPrivate Channelの全文表示だけとし、長さに関係なくWorld頭上`bubble`・TTS・公開会話Animationへ派生させない

## Nirai Skill Registry

`skills\<name>\SKILL.md`をProvider中立の共通Skillとして扱う。ただし、Skill本文をResidentの通常会話Contextへ常設しない。現行実装は**索引先行・Task時の本文遅延読込**を正とする。

```text
skills\
  <skill-name>\
    SKILL.md
```

最小形式：

```markdown
---
name: <skill-name>
description: いつ使うSkillかを1行で説明する
---

# Skill本文
...
```

- 索引は各`SKILL.md`のfront matterから`name / description`だけを読み、本文総量制限から独立させる。Skill本文を全件読み込んでから索引を作ってはならない
- Task相談では索引だけをResidentへ渡す。Agent RuntimeはTask文から最大少数件の関連Skillを選択し、選ばれた本文だけを個別ロードする
- 1 Skill本文は64 KiB、1 Taskへ注入する選択本文合計は128 KiBを安全上限とする。これは常用Token Budgetではなく暴走防止用の上限である
- Skill Directoryは辞書順で読む。依存順序を作らない
- Provider側の内蔵Skillや同期RuleはNirai Skillの正本にしない。Nirai側からそれらを削除・改変する責務も持たない
- M4 Agent Runtimeも同じRegistryを入力正本として利用し、Providerごとの別コピーを正本化しない
- Holo AddonのLocal Client `skills`は同じRegistryの索引（`name / description`）だけを返す。本文はHoloへ一括配布せず、実Taskの必要性に応じてCore側で選択する

## Brain固有Memoryの扱い

Claude Code / Codex / Cursor等が独自に持つセッション履歴、設定ファイル、Memory相当機能は、CLIを使いやすくする補助として利用してよい。ただし以下を守る。

- Residentの正式な過去はNirai側のWorld Memory / Private Memoryを正本とする
- Brain交換時に失われる情報をNiraiの人格・関係・会話継続の必須情報にしない
- CLI固有MemoryへWhisper内容を唯一の長期正本として永続保存する前提にしない。Provider native Conversation Contextを利用する場合も、Whisper専用ConversationとしてPrivacy境界を維持する
- 同じ公開会話をResident別・CLI別に重複保存して正本化しない

## Brain Provider Adapter

設定UIはProviderごとの差を直接持たず、Brain Provider Adapterから状態と設定方式を取得する。

各Brain Adapterは少なくとも次を提供する。

- `available`：CLI / Runtimeが利用可能か
- `connected`：Niraiから利用できる状態か
- `configuration_mode`：`connect` / `api` / `local`等
- `connect/configure`：Providerに応じた連携または設定
- `disconnect`：そのResidentとの割当解除。外部CLIのアンインストールや全体ログアウトは行わない
- `think`
- `cancel`
- `health_check`

CoreのProvider RegistryはBrain Adapterと11のAgent Runtime Adapterの有無を集約し、`capabilities`として少なくとも`conversation` / `agent_work`を返す。Approval / Question / Plan / Todo等のAgent Capabilityも11の共通名で返してよい。会話用Brain AdapterへAgent実行機能を埋め込んでCapability判定を兼ねさせない。

初期の設定UIに表示するProviderは **Codex / Claude / Cursor / Gemini / Local LLM** とする。Providerが利用不能でもUI全体を壊さず、そのProviderだけ利用不可表示にする。

ResidentはProviderとは別に任意の`brain_model`を持つ。設定UIはProvider Adapterが返す`models` / `default_model`を候補表示し、空欄ではProvider既定Modelを使う。Codexだけは追加で任意の`brain_reasoning_effort`を持ち、選択ModelのCatalog Metadataに含まれる`reasoning_efforts`から選ぶ。空欄ではCodex既存Configの`model_reasoning_effort`を継承する。Model / Reasoning指定はResident単位で保存し、Brain交換時に旧Providerの値を自動流用しない。CLI/APIへの具体的な指定方法は各Adapter内へ閉じ込める。

Codex / Cursor / GeminiのModel Catalog取得はWebSocket受信ループ内で待たない。Provider一覧要求には現在のCacheを即時返し、外部CLI/API/CacheからのCatalog更新はBackground Taskで取得する。複数Providerの更新Push順序は保証しない。取得完了後は最新一覧をWorldへPushし、失敗・タイムアウト中も会話・履歴取得・停止操作等をブロックしない。Catalogを取得できない間もProvider自体が利用可能ならModel IDの直接指定へ縮退できる。

### 利用枠 / Quota表示（将来拡張）

右Resident設定Sidebarでは、割り当て中Brain Providerの残り利用枠を確認できるようにする。Providerごとに上限の単位や期間が異なるため、UI上の共通概念は「トークン残量」ではなく**利用枠**とする。

Provider Adapterは、取得可能な場合に次の共通Snapshotへ正規化する。

```text
used_percent       # 使用済み割合。取得不能ならnull
remaining_percent  # 残り割合。取得不能ならnull
reset_at           # 次回Reset時刻。取得不能ならnull
period_label       # current session / weekly / all models 等の表示用期間名
updated_at         # 最終取得時刻
stale              # 古いCache値か
```

- 右SidebarではResidentのAI名の近くに利用枠とReset目安を表示する。
- Provider側が複数期間のQuotaを返す場合は、1値へ無理に統合せず期間ごとに表示できる構造を許容する。
- 取得不能・未対応・Provider仕様変更時は推測値を作らず「取得不可」とする。Quota取得失敗で会話やTaskを停止しない。
- Quota取得はBrain呼び出しとは分離し、表示更新のためにAI推論を実行しない。
- 認証情報そのものをNirai独自形式へ複製保存しない。既存CLI / Providerの認証状態を必要最小限の範囲で参照する。
- 実装時はProvider固有のUsage取得をAdapter内へ閉じ込め、World UIへProvider固有API・認証処理を漏らさない。

参考実装として `llmquota`（https://github.com/0xNyk/llmquota）を調査対象にする。Claude / Codex / Cursor等のQuota取得方式、共通表示への正規化、JSON出力の考え方を参考にしてよい。ただしNiraiはWindows専用であり、`llmquota`そのものへの恒久依存は前提にしない。実装着手時に最新Sourceと各Providerの現行仕様を再確認し、必要な方式だけをNiraiのProvider Adapterへ取り込む。

本項は将来拡張の設計メモであり、M1-08以降の現在Taskへ先回り実装しない。

## 各ドライバ

### claude-code

- 2026-08-29に実機`claude.exe --help`で非対話実行仕様を確認済み。
- 会話・WhisperではPromptをstdinから渡し、`-p --safe-mode --tools "" --permission-mode dontAsk --no-session-persistence --output-format json --json-schema <schema>`を使用する。CLI固有Memory・Project設定・ツール実行へ会話を流さない。
- `--json-schema`の構造化出力を利用し、`structured_output`または`result`のJSONをNirai共通応答へ変換する。
- 2026-08-29のLive SmokeはClaude Codeサブスクリプション停止によりHTTP 403。Driverのモック受入は完了しているが、ProviderはLive疎通が復旧するまで利用不可表示とする。従量APIへ自動切替しない。
- タイムアウト：120秒。超えたらプロセスを止めて失敗扱い

### codex

- 2026-08-30の実機Codex CLI 0.147.0で`-m / --model <MODEL>`と`-c key=value`を確認済み。Residentに`brain_model`があれば`--model`へ渡し、未指定ならCodex側既定Modelを使う。
- Model候補は`CODEX_HOME/models_cache.json`（未指定時は`~/.codex/models_cache.json`）の`visibility=list`を正とする。各Modelの`default_reasoning_level`と`supported_reasoning_levels`をProvider Metadataへ載せ、UIは選択Modelが実際に対応する推論強度だけを表示する。
- `brain_reasoning_effort`指定時はResident指定を明示してProviderへ渡す。未指定時はMasterの既存Codex Configの`model_reasoning_effort`を継承する。NiraiのCodex app-serverはCredential隔離Homeを使うため、Global `config.toml`を物理コピーしない場合でも、起動元で解決した既存Model / Reasoning defaultを隔離app-serverへ明示的に伝え、UI表示だけHigh・実行はModel既定Low等の食い違いを起こさない。2026-08-30の実機`gpt-5.6-sol`ではLow / Medium / High / Extra High / Max / Ultraを確認した。
- **Current implementation**：通常Resident会話はCodex app-serverの`thread/start` / `thread/resume`へ移行済み。同一Nirai ConversationではThreadを再利用し、既送Raw履歴を毎Turn再送しない。公式`contextCompaction`を検知した場合もThread IDは維持し、Nirai側のstatic / Memory delivery cacheだけを無効化して次Turnで必要Contextをrefreshする
- タイムアウト：120秒

### cursor

- 2026-08-29に実機Cursor Agent `2026.08.11-e8db854`で非対話実行仕様を確認済み。
- **Current implementation**：通常Resident会話はCursor CLIのJSON出力に含まれる`session_id`をnative Context IDとして保存し、同一Nirai Conversationの後続Turnは`--resume <session_id>`で継続する。Residentの`brain_model`はCLI Model IDへそのまま渡し、`cursor-grok-4.6-xhigh`をHigh/Fast等へ黙ってdowngradeしない。2026-09-07実機でxhigh非fast 2-turn継続を確認済み。`--resume`失敗時だけProvider stateを無効化してNirai Memory / journalから再構成する。通常会話のread-only境界は`--mode ask`とProject Tree外の空Workspaceで構成し、WindowsではCursor CLI Sandbox自体が非対応のため`--sandbox`へ依存しない。
- **Agent Work / Holo ReviewのModel fidelity**：会話からTaskへ移る時もResidentのModelを変えない。ACP config optionで正確に表現できるModelは従来ACP経路を使うが、`cursor-grok-4.6-xhigh`のようなCLI-only exact variantはHigh/FastやAutoへ変換せず、同じModel IDをCursor CLIへ渡すexact-model経路へ分岐する。Workでは実Task workspaceを直接CLIへ渡さず既存staging copyだけをWrite対象とし、Shell / Web / Browser / MCP / 実Workspace・秘密Pathをdenyした上で、終了後は既存の凍結Diff＋Master Approval＋Nirai-owned applyを通す。read-only Holo Reviewは同じexact Modelを`--mode ask`で使い、staging変更があればfail-closedする。2026-09-07実機でxhigh Work、Shell escape拒否、承認後のみ反映、xhigh Review `SAFE`、cleanup成功を確認済み。
- Windows版Cursor AgentはSandbox非対応を実機で確認したため、`--sandbox`へ安全性を依存しない。会話用Workspaceは`%LOCALAPPDATA%\\Nirai\\cursor_brain_workspace`の空Directoryへ固定する。`D:\\Products`配下へ置くとCursorが親Directoryの`.cursor/rules` / `AGENTS.md` / `CLAUDE.md`とGit状態を自動注入するため、Nirai会話WorkspaceはProject Treeの外へ置く。
- Niraiから起動するCursor Agentだけは、Process環境の`USERPROFILE` / `HOME`を`runtime\\cursor_profile`、`CURSOR_CONFIG_DIR`を`runtime\\cursor_profile\\.cursor`へ差し替える。これにより通常Cursor環境のGlobal MCP、User-level Skill互換Directory（`.cursor` / `.agents` / `.claude` / `.codex`）をNirai会話へ持ち込まない。通常のCursorアプリ環境は変更しない。
- Cursorアカウントに同期されるUser Rules / Team RulesはLocal Profile隔離とは別系統であり、CLI側の公開設定に無効化手段が無い場合は残り得る。隔離後の実測でも固定Contextが大きい場合は、Cursor側の同期Rule自体を棚卸し対象とする。Niraiから推測で削除・変更しない。
- JSON出力は`result`内の会話JSONをNirai共通応答へ変換する。`cursor-agent models`で現在アカウントのModel CatalogをBackground取得し、Residentの`brain_model`を`--model`へ渡す。UIはModel表示名をABC順に並べる。Cursor CLIが`cursor-grok-4.6-high`を`Cursor Grok 4.6`、`-high-fast`を`Cursor Grok 4.6 Fast`と表示してHigh表記を省略するため、Nirai側表示だけ`High` / `High Fast`を補う。Model IDは変更しない。
- 2026-08-30の遅延調査では、隔離前の最小構成でも入力約11,230 tokensで、Nirai側Profile隔離後に詳細内訳を取得すると17,358 tokens（Tools 8,732 / Rules 2,558 / Skills 1,498 / System 1,162 / Subagents 798 / MCP 660 / Conversation 1,950）だった。Project Tree外Workspaceへ移して親`D:\Products`由来Rules/Git Contextを切った再測定では約15,231 tokensまで低下し、Live Whisperも成功した。残りの大半はCursor Agent固有Tools・内蔵Skills等の固定Harnessであり、Nirai Promptが主因ではない。NiraiはModelを自動変更せず、これ以上の削減はCursor側公開設定で安全に無効化できる項目が確認できた場合だけ行う。
- タイムアウト：120秒
- Current Phase 1ではCursorもTask担当可能。会話とWorkで同一Resident Modelを維持し、Provider transportの制約を理由に別Modelへ黙って変更しない

### gemini

- GeminiはCLIではなくGemini Developer APIを利用する。API Keyは`world/.env`の`GEMINI_API_KEY`からCoreだけが読み、Rendererへ公開せず、ログやProtocolへ載せない。`.env`はGit管理外とする。
- 会話Transportは2026-08-30からInteractions APIへ統一する。Endpointは`/v1beta/interactions`、`Api-Revision: 2026-05-20`を使用する。通常GeminiとAntigravityを別Driverへ分けない。
- 通常`gemini-*`は`model=<id>`でInteractionを作り、`response_format={type:"text", mime_type:"application/json", schema:<Nirai Talk Schema>}`によるStructured Outputを要求する。実機`gemini-3.5-flash`で約4〜5秒のLive Smoke成功済み。
- `antigravity-*`は`agent=<id>, environment="remote", background=true`で開始し、返されたInteraction IDを`GET /interactions/{id}`で2秒間隔にPollする。AntigravityはStructured Output非対応のためPromptでNirai JSONを要求し、最終`model_output.content[].text`を共通Parserへ渡す。Brain用途ではremote Agentの全Toolを開放せず、`google_search` / `url_context`だけを明示許可する。Promptも最新・外部情報が必要な場合のWeb検索を許可し、ファイル操作・コマンド実行は引き続き禁止する。実作業ToolはM4 Agent Runtimeへ分離する。実機`antigravity-preview-05-2026`は約10.9秒でDriver Live Smoke成功済み。
- Stop時、background Interaction IDが存在すれば`POST /interactions/{id}/cancel`を先に送り、Google側のremote処理を停止させた上でLocal Taskをcancelする。Nirai側の120秒自動Timeoutでも取得済みInteraction IDがあれば同じremote cancelを短時間だけ試してからTimeout Errorを返し、Google側background処理を放置しない。Interaction記録の削除に使う`DELETE /interactions/{id}`とは用途を分離し、停止操作で記録削除を代用しない。
- ProviderのModel候補取得だけは`GET /v1beta/models`をBackgroundの同期HTTPで利用する。会話候補は`gemini-*` / `antigravity-*`を含め、画像/TTS/Transcribe/Robotics/Computer Use/Omni等の専用Modelは除外する。未指定時のNirai既定は`gemini-3.5-flash`。
- Interactions用HTTPは同期HTTPを`to_thread`で包まず、Coreのcancellable async HTTPS transportで実行する。HTTP本文が`Content-Length`またはchunk長に満たないまま切断された場合は`asyncio.IncompleteReadError`を`BrainUnavailableError`へ変換し、通常のBrain失敗WARNとしてWorld UIへ通知する。
- Niraiから有料API枠へ自動切替しない。AntigravityのWork対応は会話用`GeminiDriver`へ追加せず、11の`AntigravityAgentRuntime`として分離する。Work時はコード実行・remote filesystem・Web検索・URL参照等の公式Agent能力を利用可能にし、Nirai側の許可Directory・Approval・安全Policyを外側の境界として適用する。

### local-llm

- Ollama等のローカルRuntimeへ接続するProvider
- 初期UIには選択肢を用意するが、対応Runtimeは実装時に1種類へ絞り、複数Runtimeを先回り実装しない
- APIキーを前提にせず、ローカル接続設定をProvider Adapterが扱う

## 実作業（work）の分離

タスク相談まではBrain Driverの`consult`を使うが、担当決定後のPC上の実作業はBrain Driverで行わない。詳細フローは07、実行基盤とUI契約は11を正とする。

- `work`は`think()`のmodeに追加しない
- 担当ResidentのProviderに対応するAgent Runtime Adapterを`AgentRuntimeManager`が起動する
- 作業ディレクトリ、許可Root、上限時間等の安全枠は07と11で管理する
- Command、File変更、Approval、Question、Plan、Todo等はProvider固有stdoutへ埋め込まず、可能な限りProviderの構造化ProtocolからNirai共通Agent Eventへ変換する
- 実作業のCancelは`invocation_id`ではなく`agent_session_id`単位で扱う
- Providerが会話可能でもAgent Runtime未対応なら、そのResidentは会話・consultには参加できるがTask担当には選ばない

## 失敗と再試行の規定（まとめ）

| 事象 | 扱い |
|------|------|
| タイムアウト | 失敗。リトライしない（時間がもったいない） |
| Masterによる会話キャンセル | 対象invocationを停止し、未実行の同一応答キューも破棄する。失敗扱いにはしない |
| JSONパース失敗 | 同一プロンプトで1回リトライ |
| CLIが存在しない・認証切れ | その住人を「留守」にし、会話UIへ通知（`remove_resident`） |
| タスクの上限超過 | 失敗としてMasterへ報告（07参照） |

※メモ：
- serina は拡張として後日追加する（07の拡張フックで登録）。Holoは通常Brain Driverとして追加せず、12のHolo Addonを正とする。Local LLMは初期Provider一覧に含め、対応Runtimeは1種類から始める
- CLIの応答にかかる実時間（体感）はM1実装時に計測し、タイムアウト値を見直す
