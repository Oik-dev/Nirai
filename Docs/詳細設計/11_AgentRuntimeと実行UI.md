# Nirai 詳細設計 11：Agent Runtimeと実行UI

正本は [Nirai_基本設計.md](../Nirai_基本設計.md)。タスク全体の業務フローは [07_タスクと拡張.md](07_タスクと拡張.md)、Core⇔World通信は [01_通信プロトコル.md](01_通信プロトコル.md)、通常会話のBrainは [03_Brainドライバ.md](03_Brainドライバ.md)、表示UIは [05_会話パネル.md](05_会話パネル.md) を正とする。

## 目的

NiraiからCodex / Cursor / Claude等へ実作業を依頼した時に、各Providerの専用クライアントを開かなくても、Nirai内で次を完結できる実行基盤を作る。

- AIの作業状況を見る
- 実行しようとしているコマンドや変更内容を見る
- 危険な操作をMasterが承認・拒否する
- AIからの質問へ答える
- Planを確認して承認・差し戻す
- Todo / SubAgent / Test / File変更等の進捗を見る
- 実行を停止する
- 完了結果と成果物へ辿る

ただし、NiraiをコードEditorやTerminal Emulatorそのものへ変えることは目的にしない。Niraiは**Agentを操作する統合クライアント**であり、詳細編集が必要な時は既存EditorやFileを開けばよい。

## 最重要の責務分離

Residentの「会話する頭脳」と「PC上で仕事をするAgent」は別の実行面として扱う。

```text
Resident
  ├ Brain Driver
  │   └ talk / whisper / tick / consult
  │      人格・記憶・会話・生活判断
  │
  └ Agent Runtime Adapter
      └ work
         File操作・Command・Tool・Plan・Approval・Question等の実作業
```

### Brain Driver

- Residentとして自然に会話・相談・生活判断するための薄い呼び出し口
- Nirai側の人格・Memory・World Contextを正本とする
- 原則として最終的な発言・意味的行動だけを返す
- 会話時にProvider固有のTool実行UI、承認UI、Project Memory等へ依存しない
- 現在のCodex / Claude Code / Cursor / Gemini会話Driverをこの役割として維持する

### Agent Runtime

- Masterが仕事を依頼した時だけ起動する
- Providerが持つAgent Protocol / App Server / CLI Integrationを利用して、作業中イベントを構造化して受け取る
- Provider固有イベントをNirai共通Agent Eventへ正規化してWorldへ送る
- Providerの専用クライアントと同等の「確認・承認・質問・進捗確認」に必要な機能をNirai UIへ提供する
- Residentの人格・長期Memoryの正本にはしない

同じCodexを使う場合でも、会話用`CodexDriver`と作業用`CodexAgentRuntime`は別Adapterとする。1つの巨大Driverへ統合しない。

## 全体構成

```text
World / React
  ├ Chat UI
  └ Agent UI
       │ Core⇔World WebSocket
       ▼
Core
  ├ Conversation / Memory
  ├ Task Manager
  └ AgentRuntimeManager
       ├ CodexAgentRuntime
       ├ CursorAgentRuntime
       ├ ClaudeAgentRuntime
       ├ AntigravityAgentRuntime
       └ future adapters...
              │ Provider固有Protocol
              ▼
         Agent Provider
```

`AgentRuntimeManager`はProvider固有ProtocolをWorldへ漏らさない。WorldはCodexの`item/started`やCursorの`cursor/create_plan`等を直接知らず、Nirai共通Eventだけを描画する。

## Provider方針

### Codex

最初の基準実装とする。CodexのRich UI / IDE統合向けapp-server Protocolを優先し、Command実行、File変更、Approval、User Input等を構造化イベントとして受け取る。M4着手時に現行公式Schemaを再確認する。

- stdout文字列を正規表現で解析して擬似UIを作らない
- Command / cwd / File Diff / Approval等はProviderから構造化されて届く値を使う
- Codex固有のEvent名・Decision名はAdapter内でNirai共通形式へ変換する

### Cursor

Cursor ACPをAgent Runtimeとして利用する。ACPはJSON-RPCでCustom Clientを構築でき、Session、Permission Request、Plan、Question、Todo、SubAgent Task等のイベントを扱えるため、Nirai共通Eventへの対応付けを行う。

会話用Cursor DriverのAsk Modeとは別経路とし、work時だけAgent Runtimeを起動する。

2026-09-04の実機確認で、CursorはAgent workspace内のFile Editを必ずしもACP Permission Requestへ送らず、通常workspace EditをApprovalなしで即時保存し得ることを確認した。このProvider特性に対して、Niraiの上位設計を変更せず「Providerの承認機構だけに安全を丸投げしない」という既存安全原則をCursor Adapterで具体化する。Cursorへ実Task workspaceを直接渡さずSession専用staging workspaceをcwdとし、turn終了後にProvider Process treeを停止して、変更FileをProviderから分離したSession専用の凍結review bundleへコピーする。Niraiはその凍結bundleから初期Snapshotとの差分を生成してMasterへFile Change Approvalを提示し、承認後もstaging / review bundleのHash一致を再確認したうえで、凍結bundleからだけ実Task workspaceへ反映する。reject / cancel、またはreview後のstaging / bundle変化では実Workspaceを変更しない。詳細な実装制約・Live Smokeは`../M4_CursorACP基準Slice_検証結果.md`を正とする。

Cursorの現行Capabilityは`approval / question / plan / todo / subagent / file_diff / command_result`。Cursorの画像生成・Artifact通知はstaging workspace上のPathをそのまま成果物として公開しない安全境界のため現Sliceでは意図的に抑止し、`artifact=false`とする。安全なArtifact export / review経路を実装するまではCapabilityをtrueへ戻さない。

### Claude

Claude CodeのProvider固有Adapterは将来候補として維持するが、**2026-09-05にMaster判断で実装を延期した**。現行のClaude Code認証はClaude Pro / Max契約またはAPI Key利用を要求し、Masterはこの追加有料依存を現時点では採用しない。したがってClaude Agent Runtimeは現在のM4完走を阻害する必須項目にせず、契約方針が変わった時だけ再開する。

途中まで検証したAgent SDK Adapterとprobeは`../plans/archive/2026-09-05-claude-agent-runtime-deferred/`へ退避しており、現行Coreへは配線しない。再開時はPermission、Plan、Question、Tool実行等をその時点の現行公式仕様で再確認し、Nirai共通Eventへ変換する。十分な構造化Integrationが無い場合は、取得できる機能だけをCapabilityとして公開する。

### Antigravity

Antigravityは会話用`GeminiDriver`とWork用`AntigravityAgentAdapter`を分離する。Resident上のProvider IDは`gemini`を維持し、**`antigravity-*` Modelを選んだResidentだけ**`agent_work`有資格とする。通常の`gemini-*` Modelは会話可能でもTask担当にはしない。

Work AdapterはGemini Interactions APIのManaged Agent `antigravity-preview-05-2026`を利用する。Google remote environmentとローカルTask workspaceを同一Filesystemとして扱わず、remote側はscratch環境としてのみ利用する。

- remote environmentはInteractionと同時に暗黙作成せず、Environments APIで`network=disabled`として先に作成する。作成前のEnvironment一覧とSession固有inline markerを保持し、CreateEnvironment応答喪失時はList / Getでmarker一致する新規Environment IDを復旧する。Google公式のAPI ReferenceはEnvironment IDを`id`、Managed Agentsガイドは`environment_id`として例示しているため、responseは両方を受理し、`next_page_token` paginationを処理する。実Smokeで成立済みのREST request形は維持する。Work時のToolはremote `code_execution`とNirai所有Custom Functionだけを明示し、`google_search` / `url_context` / MCPは基準Sliceで渡さない
- remote `code_execution`はGoogle sandbox内だけで実行し、ローカルTask workspaceへ直接書けない。Command Eventは`execution_scope=remote_sandbox`としてNiraiへ正規化する。Core権限のlocal shellはこのAdapterから起動しない
- ローカルProject操作は`nirai_list_files` / `nirai_read_text_file` / `nirai_write_text_file` / `nirai_edit_text_file` / `nirai_delete_file`へ限定する。Read/Listは`working_dir`内だけ、Write/Edit/DeleteはPath・`task.md`・Diff・HashをCoreで再検証してMasterの`approve_once`後だけNirai自身が反映する
- Approval待ち中に対象File内容またはcanonical Path topologyが変わった場合は適用しない。既存Fileの全旧Textを安全にレビューできない場合（非UTF-8または512 KiB超）は上書きをfail-closedする
- Questionは`nirai_ask_master`、Planは`nirai_submit_plan`から既存Nirai Master UIへ接続する。PlanのCancelはAgent Session停止として扱う
- Stateful継続では`previous_interaction_id`と同一`environment_id`を使い、Interactionごとの`tools` / `system_instruction`を毎Turn再指定してNirai安全境界を維持する。`incomplete`は同一環境で最大8回まで継続する
- background実行に必要な`store=true`を使用する。IDを取得できたInteraction recordはSession終了時に明示DELETEし、事前作成してIDを保持または復旧したremote environmentも明示DELETEする。cleanup失敗を成功扱いにしない
- 2026-09-05時点の公式Interactions APIにはCreateの冪等キーとInteraction一覧取得APIがなく、`store=false`は`background=true`および`previous_interaction_id`と両立しない。このため`POST /interactions`がProvider側で成立した直後にCreate応答自体を喪失した場合、そのInteraction IDをNiraiから100%復元してrecordを即時DELETEすることは現行API契約では保証できない。NiraiはInteraction作成前にEnvironment IDを確定し、Create応答喪失時もそのEnvironmentの削除を試行してSessionを失敗扱いにする。Gemini API実機では`labels`もEnterprise Agent Platform限定としてHTTP 400で拒否されるため、未知InteractionへNirai固有labelを付ける方式も採用しない。未知IDのstored Interaction recordはProvider保持期限へ委ねる残余制約とし、これを「完全cleanup済み」とは扱わない
- Provider内部のthoughtはNirai Eventへ保存・表示しない
- 現SliceのCapabilityは`approval / question / plan / file_diff / command_result`。Todo / SubAgent / Artifactは未対応として`false`を公開する

実装・自動回帰・実Antigravity Positive Smoke、修正後再レビューは2026-09-05に確認済み。Nirai側で修正可能なFindingは解消され、未知Interaction IDのstored record即時完全回収だけは上記Provider制約として残る。Masterが当該残余制約を正式受容したためAntigravity基準SliceはSAFE確定とし、検証記録は`../M4_Antigravity基準Slice_検証結果.md`を正とする。

### Gemini / その他

通常Gemini等は、会話ProviderであることとAgent Runtime対応Providerであることを別Capabilityとして扱う。会話できるからといってwork可能とはみなさない。

## Capability

Provider Registryは少なくとも次を共通Capabilityとして扱う。

```text
conversation      # Brain Driverとして会話可能
agent_work        # Agent Runtimeとして実作業可能
approval          # 実行前承認をNiraiへ転送可能
question          # AgentからMasterへ質問可能
plan              # Plan提示・承認が可能
todo              # Todo進捗を取得可能
subagent          # SubAgent状態を取得可能
file_diff         # File変更差分を取得可能
command_result    # Command実行状態・結果を取得可能
artifact          # 成果物参照を構造化して取得可能
```

未対応Capabilityを推測で`true`にしない。各Agent Runtime Adapterが対応Capabilityを明示宣言し、宣言が無いAdapterはManagerで空集合としてfail-closedする。UIはCapabilityに応じて機能を出し分ける。CapabilityがModel依存するProviderではProvider全体の`agent_work`をAdapter存在だけで`true`にせず、Provider-levelは既定Modelの実効Capabilityを表し、Model Catalog側にもModelごとの実効Capabilityを付与できる。現行Geminiは通常`gemini-*`を`agent_work=false`、`antigravity-*`だけを`true`とし、Task相談・担当決定時もResidentの実ModelをCore側で再検証する。

## Agent Session

Task 1件の中で、実際にAgent Providerと接続して作業する単位を`Agent Session`と呼ぶ。

- `task_id`：Nirai上の仕事そのもの
- `agent_session_id`：Providerとの実行Session
- `provider_session_id`：Provider固有Session ID。Core内部だけで保持してよく、Worldへ必須公開しない
- `event_id`：Agent Event 1件の一意ID

通常はTask 1件につきAgent Session 1件だが、失敗後の再実行やProvider交代で複数Sessionを持てる構造にする。

保存先：

```text
runtime\agent_sessions\<agent_session_id>\
  session.json
  events.jsonl
```

Task依頼本文のmetadata正本は設定順に依存せず`runtime\workspace\<task_id>\task.md`へ固定する。`/task @対象フォルダ名 ...`で実作業cwdを別の許可Projectへ切り替えた場合も、Nirai管理用`task.md`を実Projectへ混入させず、Agent Sessionの`working_dir`だけをnamed targetへ向ける。Manager直接呼び出しでも別metadata directoryは受け付けない。

`events.jsonl`は実行UIを再構築するための正本とする。会話履歴`runtime\chat_sessions\S-*.jsonl`へCommand全文や大量Diffを複製保存しない。Command / File Changeのstreaming deltaはcompleted Eventとの重複と無制限増加を避けるため永続化せず、現Sliceでは文字列12,000文字、1 Event payload約32,000文字、Session payload約2,000,000文字を通常詳細上限とする。Approval / Question / Plan / Run State / Error等の安全上重要なBlocking / State Eventに加え、MasterのApproval判断へ直接対応する`operation_id`付き`pending_approval` File Change Contextは、通常Session詳細budgetを使い切っても欠落させず保持する。ただし1 Event payload上限は維持し、全変更Pathを安全に表示できない場合はAdapter側でApproval自体をfail-closedする。Final Summaryも8,000文字を上限とする。

`session.json`には実行状態に加えて、少なくとも元Chat Session ID、Task phase、Task結果をChat / World Memoryへ保存済みかを示す`result_reported`、WorldへTerminal Snapshot / Task Updateを通知済みかを示す`result_notified`を別々に保持する。Core再起動後も`agent_session_id → 元Chat Session`の対応を復元できること。

Event永続化はCrashを考慮する。起動時に`events.jsonl`の最終正常seqを走査して`session.json.last_event_seq`と照合し、Event側が先行していればSnapshotを前進させる。書込み途中の末尾1行だけが不完全な場合はその末尾を切り詰め、途中までの正常Eventは失わない。同一Session内でseq / `event_id`を再利用しない。

Chat Session側には、Task開始・担当・完了・失敗等の人間が読む高水準Entryだけを残し、必要なら`task_id` / `agent_session_id`から詳細Agent UIを開く。Terminal結果の`task_phase`と`result_reported`は同じSnapshot更新で保存し、Crashで`run_state`だけterminal・phaseだけ旧値になった場合も再起動時にterminal stateから補正する。World送信成功後にだけ`result_notified=true`を保存し、それ以前にCrashした場合は次回World接続へTerminal Snapshot / Chat Entry / Task Updateを冪等再通知する。

## Nirai共通Agent Event

共通Envelope：

```json
{
  "event_id": "AE-...",
  "agent_session_id": "AS-...",
  "task_id": "T-...",
  "provider": "codex",
  "type": "command_execution",
  "ts": "2026-08-30T12:00:00+09:00",
  "payload": {}
}
```

Provider固有の生JSONはCore内部の診断用に限定し、World Protocolの正本にはしない。

### P0 Event Types

| type | 用途 | 主なpayload |
|---|---|---|
| `assistant_message` | AgentからMasterへの通常メッセージ | `text`, `format=markdown` |
| `status_message` | 短い作業状況 | `text` |
| `tool_call` | MCP等を含むTool実行 | `tool_name`, `summary`, `arguments?`, `status` |
| `command_execution` | Command実行 | `command`, `cwd`, `status`, `exit_code?`, `stdout_excerpt?`, `stderr_excerpt?` |
| `file_change` | File作成・編集・削除 | `path`, `change_type`, `diff?`, `status` |
| `approval_request` | Master承認待ち | `request_id`, `kind`, `title`, `description`, `risk?`, `options` |
| `question_request` | Agentからの質問 | `request_id`, `title?`, `questions` |
| `plan` | 実装Plan提示 | `request_id?`, `title?`, `markdown`, `todos?`, `approval_required` |
| `todo_update` | Todo進捗 | `todos` |
| `subagent_update` | SubAgent / Child Task状態 | `name?`, `summary`, `status` |
| `artifact` | 成果物 | `name`, `path?`, `mime_type?`, `kind` |
| `run_state` | Session全体状態 | `state`, `message?` |
| `error` | 回復不能または要確認エラー | `message`, `code?`, `recoverable` |

`run_state.state`は少なくとも`queued / starting / running / waiting_for_master / cancelling / completed / failed / cancelled`を持つ。

### 思考過程の扱い

NiraiはProvider内部の非公開なChain of Thoughtを取得・保存・表示することを要件にしない。

- 表示するのはProviderがUI向けに公開した通常メッセージ、Plan、Todo、Tool状態、要約等だけ
- 内部Reasoning Tokenや隠れた思考全文を`events.jsonl`へ保存しない
- Providerが公開用のReasoning Summary等を正式に返す場合は`status_message`等へ正規化してよい

## UI表示

Agent Eventは会話欄の流れを壊さず、Task単位の実行領域として表示する。

### 基本表示

```text
[Cursor が作業中]

Plan
  ✓ 既存実装を確認
  ● Core Protocolを変更
  ○ World UIを追加

> npm test
  D:\Products\Nirai\world
  ✓ exit 0  65 tests passed

File change  core/server.py
  +12 -3
  [差分を開く]

[作業完了]
```

大量のCommand出力やDiffを常時展開しない。既定は文字数・行数を示す折り畳みSummaryとし、Masterが明示的に開いた場合だけ本文を展開する。Core側でもEvent payloadを有限化し、Worldの折り畳みだけを負荷対策にしない。

### Markdown / Link

`assistant_message`と`plan`はMarkdown表示をP0とする。

- 見出し
- 箇条書き
- Code Block
- Inline Code
- Table
- URL Link
- File Path / `path:line`参照

外部URLは`http / https`だけをOS既定Browserへ渡し、`javascript:`等のSchemeはLink化しない。raw HTMLはMarkdownから実行せずTextとして描画する。File PathはRendererから直接OS操作せず専用Preload IPC → Electron Mainを通し、MainでNirai `runtime/workspace`配下かつ対象Agent Sessionのworking directory内の既存Fileか再検証してから既定アプリへ渡す。

### Command Card

P0で表示する。

- Command
- cwd
- pending / running / success / failed / cancelled
- Exit Code
- stdout / stderrの短い抜粋
- 詳細展開

ANSI Terminal Emulatorは作らない。Interactive Shellの完全再現はP0に含めない。

### File Change / Diff Card

P0で表示する。

- File Path
- create / modify / delete
- 追加・削除行数が取得できる場合は表示
- Unified Diffを折り畳み表示
- Fileを開く導線

Nirai独自の本格Editorは作らない。

### Approval UI

Agentが承認待ちになった場合、Toastだけで済ませず、該当Agent Session内へBlocking Cardを表示する。

```text
実行の承認が必要です

npm install <package>
D:\Products\Foo

理由: Dependency追加が必要

[今回だけ許可] [このAgent Sessionでは許可] [拒否]
```

Nirai共通Decision：

- `approve_once`
- `approve_session`
- `reject`
- `cancel`

Providerが対応しないDecisionはUIへ出さない。`approve_session`は現在のAgent Sessionを越えて永続化しない。永続Allowlistは別の明示設定機能として将来設計し、P0の承認Dialogから勝手に作らない。

Codex File Change ApprovalではProvider Schemaの`itemId`をNirai共通`operation_id`へ保持し、`grantRoot`がある場合はMasterへCardを出す前にCoreでTask working directory内か検証する。範囲外はfail-closedで拒否する。Approval Cardへ添えるFile Change / Diffは同じ`operation_id`のEventだけとし、対応Eventがまだ無い・関連付け不能な場合はApprove系Buttonを無効化する。別の直前File Changeを代用しない。

### Question UI

Agentから質問された場合はBlocking Card / Dialogを表示する。

P0で扱う質問形式：

- 単一選択
- 複数選択
- 自由入力

Providerが対応する入力形式だけをUIへ出す。Cursor ACPの`cursor/ask_question`は選択式契約として扱い、`allow_free_text=false`、単一選択はRadio、複数選択だけCheckboxとする。Codex等で自由入力が有効な質問は従来どおり自由入力を表示する。複数の質問が1要求に含まれる場合も1つのDialog内で回答できる。回答するまでAgent Sessionは`waiting_for_master`とする。

### Plan UI

PlanをMarkdownとして表示し、Providerが承認を要求している場合は次を出す。

- `このPlanで進める`
- `修正してほしい` + 任意理由
- `キャンセル`

PlanのTodoが構造化されている場合はTodo表示と同期する。Plan FileそのものをNirai独自Editorで編集することはP0要件にしない。

### Todo

Taskの進み具合を一目で分かる形にする。

- pending
- in_progress
- completed
- cancelled

同一Todo IDの更新は新しいCardを増やさず、同じ表示を更新する。

### SubAgent

ProviderがChild Agent / SubAgent状態を返す場合、親Task配下の折り畳みCardで表示する。

- 名前または役割
- 何をしているかの要約
- running / completed / failed
- 最終結果要約

SubAgentの内部会話全文を親Chatへ大量展開しない。

## MasterからAgent Runtimeへの応答

World → Coreに次の意味的応答を送る。

```text
agent_approval_response
agent_question_response
agent_plan_response
agent_session_cancel
agent_session_snapshot_request
```

具体payloadは01を正とする。WorldはProvider固有Decision名やJSON-RPC Method名を送らない。

## 再接続・復元

World再起動やWebSocket再接続で実行状況を失わない。

- CoreはAgent Eventを`events.jsonl`へ保存してからWorldへPushする
- World接続時、実行中Agent SessionがあればCoreがSnapshotを再通知できる
- Worldは`agent_session_snapshot_request`で現在状態と必要な直近Eventを再取得できる
- Approval / Question / Plan承認待ちなら、再接続後も同じ`request_id`で入力UIを復元する
- Snapshotとlive Eventが競合した場合は`last_event_seq`を順序規則とし、Worldが既に適用したlive Eventより古いSnapshotで状態を巻き戻さない。古いEventの再送も適用しない
- 同じ応答を二重送信してProviderへ二重適用しないよう、Coreはrequestごとの解決済み状態を保持する
- Core再起動時は未完了Agent Sessionを`interrupted`へ確定し、永続化した元Chat Session IDを使って高水準の失敗結果をChatとWorld Memoryへ冪等に1件だけ保存する。Terminal結果について`result_reported=true / result_notified=false`なら、結果自体は増やさず次のWorld接続へSnapshot / Chat Entry / Task Updateを再通知し、送信成功後に`result_notified=true`へ進める。以後の再起動では再送しない
- Agent Session開始前のTask Queueは`runtime\task_queue.json`を正本とし、World再接続時はpending Taskを`task_update phase=queued`として再通知する。Core再起動時にconsult中だった`active` pre-Agent TaskはFIFO先頭へ戻すが、同じ`task_id`のAgent Sessionが既にDurable化されていれば再実行しない

## 同時実行とQueue

Agent Sessionの同時実行は**1件だけ**を維持し、2件目以降の`task_request`はbusy拒否せず永続FIFO Queueへ積む。Queue正本は`runtime\task_queue.json`で、`active` pre-Agent Task 1件と`pending`をtemp write + replaceで原子的に保存する。pending上限は32件、Task本文上限は32,000文字、persisted Queue File上限は8 MiBとし、request入口とStore双方で再検証する。

- 相談開始前からTaskを`active`として永続化し、最初のawaitより前のTask Flow予約とCrash recoveryを両立する
- 先行TaskがAgent作業へ昇格した後も、Agent Sessionがterminalになるまで次のQueue Itemを開始しない
- terminal到達後にpending先頭だけを`active`へ昇格し、FIFOで相談を開始する
- Core停止開始後はQueue dispatcherを停止し、新規Task受付も拒否する
- Core再起動時に残った`active` pre-Agent Taskはpending先頭へ戻す。ただし同一`task_id`のDurable Agent Sessionが存在すれば昇格済みTaskとしてQueueから除外し、二重相談・二重実行を防ぐ
- Queue Fileの破損、Path / target / origin Chat Session不整合、永続化失敗では推測実行せずfail-closedする
- Queue待機中も元Chat Session削除 / ForgetとResident削除 / Brain変更を拒否する
- Queue待機表示は`task_update phase=queued`と`queue_position`を使い、Provider固有のQueue概念はWorldへ漏らさない

## Cancel

会話の`cancel_response`とAgent Session停止は別物とする。

- 会話停止：Master発話1回に対するResident応答を止める
- Agent停止：実作業中のAgent Sessionを止める

`agent_session_cancel`を受けたCoreはProviderの正式なCancel / interrupt機構を短い上限時間付きで先に試す。Providerがinterruptへ応答しなくても停止操作自体を無期限待ちにせず、Manager側Taskのcancelへ進み、Provider app-serverとその子Process treeの終了まで行う。MasterからのApproval / Question / Plan応答はSessionが`waiting_for_master`の間だけ受理し、`cancelling`へ入った後の遅延応答は同じ`request_id`がpendingに残っていても拒否する。Cancel後にProviderから遅延Eventが届いても、完了扱いへ戻さず`cancelled`を最終状態として維持する。

Codex app-serverの停止は`taskkill`、`terminate`、`kill`、各`wait`を含む停止全体に有限上限を設ける。各OS操作の`OSError / PermissionError / timeout`を吸収して次の停止手段へ進み、Process停止が失敗してもSession専用Credential Homeの削除・不存在確認を別の後始末として必ず実行する。Process残留またはCredential Home残留は成功扱いにせず明示エラーへする。

Agent Sessionの上限Timeoutでも同じ停止Sequenceを使う。表示状態だけを`failed`にして実Processを残すことは禁止し、interrupt試行後にProvider Taskをcancelし、Adapter終了処理でapp-server / 子Process treeを停止する。

## 安全境界

Agent RuntimeはProviderの承認機構だけに安全を丸投げしない。Nirai側にも外側の境界を持つ。

1. `tasks.allowed_dirs`で作業可能Rootを制限する
2. Providerへ渡すcwd / Workspaceを許可範囲内へ固定する
3. Git変更、外部送信、System設定、秘密情報等、Master承認が必要な操作は07と00の安全規則を適用する
4. ProviderからApproval Requestが来た場合はNiraiへ転送し、MasterのDecisionを待つ
5. ProviderがApprovalを要求しない危険操作でも、Nirai側で明確に検出できるものは外側のPolicyで止めてよい
6. Approval Cardには「何を」「どこで」「なぜ」を可能な範囲で表示する。File Changeでは`itemId / operation_id`と`grantRoot`を失わず、書込み範囲をMasterへ隠したままProviderへacceptを返さない
7. Nirai自身の本体更新はself-build手順完成まで通常Agent Runtimeから直接適用しない
8. Codex等で認証情報を一時Homeへ複製する場合、Agent working directory配下へ置かない。Session専用Homeへ必要最小Fileだけを複製し、可能な範囲で現在ユーザーだけのACLへ絞る
9. Agent起動時に前回の一時Credential Home残留を棚卸しし、削除を再試行して不存在を確認する。削除できない場合は新しいAgentを開始せず明示エラーにする
10. Codex app-server stderr等のCLI異常出力はCore共通ログ規約どおり、小さなchunkで読み、1行の先頭最大500文字だけを改行escapeした診断抜粋として記録する。長い1行全体をbuffer / logしない
11. Cursorのようにworkspace内File EditをProvider Permissionへ必ず出さないProviderでは、実Task workspaceを直接Providerへ渡さず、staging / 凍結review bundle / diff / Master Approval / Nirai-owned apply等の外側境界で「承認前に実Workspaceを変更しない」「Masterが見た内容と適用byteを一致させる」を成立させる。Provider固有の無承認EditをNirai共通Approval済みと見なさない
12. Cursor staging applyは実Workspace・staging・凍結review bundleの再Hashで競合やreview後変化を検出し、複数File反映途中の失敗は事前Backupからrollbackする。Approval Event上限で一部Pathが隠れる場合はDiffを落としても全変更Pathを優先し、それでもManifestが収まらなければ適用を拒否する。ACP Permission拒否ではProvider optionの`kind`を意味として判定し、reject semanticが存在しない場合に任意のallow optionへfallbackしてはならない

ProviderのSandboxは追加防御として利用してよいが、Niraiの許可範囲や承認UIの代替にはしない。

## P0 / P1 / 将来候補

### P0：専用クライアントを常用しなくても仕事を監督できる最低条件

- Markdown
- URL Link
- File Path Link
- Command Card
- Tool Call Card
- File Change / Diff
- Approval
- Question
- Plan
- Todo
- Run State
- Cancel
- Error
- Agent Event永続化と再接続復元

### P1：使用感を大きく上げる

- SubAgent詳細
- Artifact / Image Preview
- Test Result専用表示
- 変更File一覧Summary
- Provider / Model / Reasoning / Agent Mode表示
- Agent Session履歴検索
- 外部EditorとのFile / Line連携強化

### 将来候補：長時間Agent SessionのContext維持

- Context圧縮 / Summary操作

バイブコーディングを主用途とし、Session Fork、Checkpoint / Undo / Revert、Cloud HandoffはNirai標準機能にしない。長時間作業でContext上限が実害になった場合だけ、Providerの公開機能を利用したContext圧縮 / Summaryを追加する。Provider専用クライアントの高度なSession管理を再現すること自体は目的にしない。

## やらないこと

- Nirai内蔵の本格コードEditor
- Nirai内蔵Terminal Emulator
- Providerの画面をPixel単位で複製すること
- Provider固有EventをWorld UIへ直接流すこと
- stdout文字列を大量の正規表現で解析して疑似Protocolを発明すること
- 非公開Chain of Thoughtの取得・保存・表示
- 承認要求の自動許可
- Providerの全機能を最初から共通化すること

## M4受入の最低条件

1. Codex Agent Runtime 1本で、小さなFile変更TaskをNiraiから開始できる
2. `running → waiting_for_master → running → completed`等の状態遷移をUIへ反映できる
3. Command、cwd、結果をCard表示できる
4. File変更とDiffをCard表示できる
5. ProviderからApproval Requestが来た場合、Niraiで内容を確認して許可・拒否できる
6. ProviderからQuestionが来た場合、Niraiで回答して作業を継続できる
7. ProviderがPlan承認を要求する場合、Planを確認し、承認・差し戻しできる
8. Todo更新は同じTodoを追記スパムせず状態更新として表示できる
9. Agent Sessionを停止でき、Provider interruptが無応答でも停止操作がhangせず、app-server / 子Process treeを残さない。Session Timeoutも同じ停止Sequenceを通る
10. Worldを再接続しても実行中Sessionと未回答Approval / Questionを復元でき、古いSnapshotで新しいlive Eventを巻き戻さない。Core再起動時は`interrupted`結果を元Chat / World Memoryへ冪等復旧できる
11. 詳細Eventは`runtime\agent_sessions`へ残り、Chat Sessionへ大量複製されない
12. Provider固有ProtocolをWorldが知らず、Adapter Testで共通Agent Eventへ変換できる
13. 許可外Directoryへの作業はProviderへ渡す前にCoreが拒否できる。Codex File Change Approvalの`grantRoot`もMaster承認前に同じ境界で検証する
14. Agent Sessionを1件だけに制限したまま、2件目以降のTaskを永続FIFO Queueへ積み、Core再起動時のactive復旧・Agent Session昇格済みtask_idのdedupe・Queue永続化失敗時のfail-closedを確認できる
15. Event payload / Final Summaryに有限上限があり、大量stdout / DiffはCoreでbounded、Worldで既定折り畳みになる
16. Markdownのraw HTMLと非http(s) URLを実行せず、Table / Link / File Pathを安全なRenderer / IPC境界で扱える
17. Process停止故障時も有限時間で終了処理を抜け、Credential Home cleanupを必ず試行し、残留を明示エラーにできる
18. `result_reported`と`result_notified`を分離し、結果保存後・World通知前CrashからTerminal通知を復旧できる
19. Codex stderrの長い1行は先頭500文字だけが改行escapeされてDEBUGログへ残り、501文字目以降を保存しない

## 実装時の主要参照

2026-08-30時点で、最初に確認する一次資料：

- Codex app-server: `https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md`
- Cursor ACP: `https://cursor.com/docs/cli/acp`
- Antigravity Agent: `https://ai.google.dev/gemini-api/docs/antigravity-agent`

Codex app-serverはCommand実行やApprovalをUI Clientへ構造化して渡す前提を持ち、Cursor ACPはJSON-RPCのCustom ClientとしてPermission Request、Plan、Question、Todo等を扱える。Antigravity AgentはGoogle管理のremote Linux sandbox上で`code_execution`、filesystem、`google_search`、`url_context`を利用でき、remote MCP ServerやCustom Functionによる外部Tool接続も可能。これらをNirai共通Agent Event設計の参考にするが、Provider固有Protocol自体をNiraiの共通仕様にはしない。

Claude / Antigravity等を含め、Provider固有仕様は更新され得る。Adapter実装開始日に公式資料と実機を再確認し、Method名・Decision名・Capabilityを確定する。

## 実装順

M4着手時は次の順で行う。

1. Nirai共通Agent Event型とAgentRuntime Interface
2. `AgentRuntimeManager`とEvent永続化
3. Core⇔World Agent Protocol
4. World Agent Event Store
5. Markdown / Command / Diff / Run State Card
6. Approval / Question / PlanのBlocking UI
7. Codex Agent Runtime Adapter
8. Cancel / Reconnect / Snapshot復元
9. Codex実機受入
10. Cursor ACP Adapter
11. Claude Adapter
12. Antigravity Agent Runtime Adapter
13. Antigravityのremote sandboxとNirai許可Directoryを安全につなぐBridge方式の実機受入
14. P1は必要性を確認してから追加

Codex / Cursor / Claude / AntigravityのProvider固有Protocolは変化し得るため、各Adapter実装開始時に公式仕様と実機を再確認する。設計書に書かれた過去のMethod名を盲信しない。
