# 実行制御の横断監査 — 2026-09-15

## 結論

現行コードで再現したR1–R6を修正した。R1–R5の修正後、マスターの明示承認を受け、2026-09-16にTask開始・復旧とWorkflow更新をCoreの同じ受付へ集約した。未完Taskを残したCompleted化を拒否し、失敗の解決記録と再起動後の状態復元を含めて検証した。実Provider・実ChatGPTでの再発確認は未実施であり、検証範囲は末尾に明記する。

開始時のHEADは `b7ba218`。開始前から `core/agents/cursor_workspace.py`、`core/tests/test_codex_agent_runtime.py`、`tools/holo-workflow.mjs`、`tools/holo-local-client.test.mjs` に未コミット変更があった。前2件を変更せず、後2件の既存変更も保持した。commit/push、実Providerの起動、実Conversationへの自動送信は行っていない。

## 修正した問題

### R1 / P1: `resource_exhausted`で途中成果の保存経路を外れる

- 箇所: `core/agents/base.py:classify_provider_limit`、`core/agents/cursor_acp.py` のACP/CLIエラー分岐、`core/agents/manager.py` の終了・Workspace解放。
- 再現: 実Cursor Adapterに、隔離した作業コピーへファイルを書いた後で `resource_exhausted` を返す偽CLIを接続。非ゼロ終了とJSONエラーの両方が一般エラーになり、既存の途中成果保存を通らなかった。既存のquotaエラー2形式は通過した。
- 影響: 途中成果を回収する機会を失い、Taskが一般的なfailedへ進む。
- 修正: 明示的な `resource_exhausted` を `provider_resource_exhausted` として区別し、既存の保存・中断・cleanup完了後のWorkspace解放へ接続。リソース不足だけで課金枠の消費とは断定しない。画面表示も同じ理由に対応した。
- 検証: CLI両形式、Managerでの中断理由と保存先、後続Taskが同Workspaceを使えることを検証。既存quota、取消中の保存完了待ちも回帰対象。

### R2 / P1: 保存済みの途中成果をHoloが状態取得できない

- 箇所: `core/server.py:_agent_snapshot_payload / _task_status`。
- 再現: `session.json`に `interruption_reason / partial_work_path` が存在しても、Holoが利用するTask状態取得結果に両フィールドがない。
- 影響: Auto Resumeが届いても、次の判断に必要な中断理由と途中成果の場所が欠ける。
- 修正: Agent SnapshotとTask状態取得の両方へ保存済みフィールドを渡した。
- 検証: Event履歴の読込を禁止したテストでも、正本の値を返す。

### R3 / P1: 接続切れ・Renderer停止で通常Taskの再開通知を失う

- 箇所: `core/task_runtime.py:_send_active_agent_snapshots` とCore→World通知経路。
- 再現: `interrupted`では通常の終端結果再送リストへ入らず、再接続時のSnapshotからもTask Updateが出ない。さらに `completed / failed / cancelled` ではSocket送信成功の直後、Rendererが永続保存する前に落ちると `result_notified=true` が再送を止める。Core再起動あり/なしで8ケースを再現した。
- 修正: 再接続時、Holoのownerが残る終端Taskを保存済みSnapshotから再送する。回復済みの旧Agentは除外し、既存Hostの保存済み重複キーとowner削除規則を使う。別の通知Queueは追加しない。
- 検証: 4状態×再起動あり/なし、owner消去後の再送停止。偽Providerのresource不足→Manager中断→Task状態・Workspace解放→World再接続通知も通して検証した。
- 限界: 実際のChatGPT DOMでの送信成功や、停電を含む一度だけの配送保証を示すテストではない。

### R4 / P2: 待機の時間切れを作業成功としてLease更新する

- 箇所: `tools/holo-workflow.mjs:isSuccessfulActivityResult`。
- 再現: Coreが返す `{ok:true, timed_out:true}` を活動扱いし、`updated_at` が進んだ。判定側は別表記の `timedOut` しか見ていなかった。
- 影響: 成果のない待機を繰り返すと、停止検知を遅らせる。
- 修正: 両表記を時間切れとして判定する。成功した結果取得の活動更新は維持。

### R5 / P1: Coreが却下した状態をTask/UIへ配信する

- 箇所: `core/agents/manager.py:_record_event`、`core/task_runtime.py:_handle_agent_task_event`、`core/server.py:_task_status`。
- 再現: Providerが `completed` を申告すると、Manager正本は `running` のままなのにTask phaseが `done` になる。承認待ちの後着 `running` も正本は `waiting_for_master` なのにWorldへは `running` が流れる。両ケースで修正前に失敗するテストを確認した。
- 修正: 配信・保存するイベントもCoreが受理した状態へ揃え、元の申告は `provider_reported_state` に残す。Task側は後着の古い状態を採用せず、状態取得はAgent正本を優先する。
- 影響: 早すぎる完了通知、確認待ちUIの消失、実状態とTask表示の不一致を防ぐ。Provider内部の完了申告だけでWorkspace反映・後始末の完了を宣言しない。

## 修正済み R6 / P1: Workflow完了にTaskの実状態の検査がない（2026-09-16）

### 再現と影響

`tools/holo-workflow.mjs:runWorkflowCommand` の `workflow-complete` はexact Workflow IDとWorld buildを確認するが、紐づくTaskを検査しない。

実際のCLIを隔離環境へコピーし、同じWorkflow IDを持つownerとAgent Snapshotを置いた。`running / failed / interrupted / waiting_for_master` の全4状態で `workflow-complete` が成功し、Taskをそのまま残してWorkflowが `completed` になった。

再現スクリプト: `.tools/execution-control-audit-20260915/probe-workflow-completion.mjs`。
出力: `.tools/execution-control-audit-20260915/completion-evidence.json`。
これは実運用Workflowの書換えを行わない隔離再現であり、報告された過去の実例と同一原因であることまでは断定しない。

`world/src/main/holo/HoloWebHost.ts:isObsoleteAfterWorkflowCompletion` は完了WorkflowのTask通知を破棄し、watchdogもcompletedを対象外とする。この抑止規則自体は既存仕様に合っているが、誤った完了を入口で防いでいないため、作業が残っていても再開されなくなる。

### 承認済みの変更と実装

1. **Workflow更新をCoreの同じ受付へ集約する。** CLI/MCP/Worldからの開始・完了・取消をそこへ送る。Task開始予約とWorkflow完了判定を同じ排他区間で処理し、「確認直後に別Taskが始まる」競合を防ぐ。`active / completed`の既存状態は維持する。
2. **Taskの所属Workflow IDをTask正本にも残す。** QueueとAgent Snapshotへ引き継ぎ、通知後に消えるownerファイルを完了証拠に使わない。旧データを現在Workflowへ推測で付け替えない。
3. **完了前に所属Taskを確認する。** 実行中・Queue待ち・承認待ち・中断・未処理の失敗を残したままのCompleteを拒否し、該当Task IDと理由を返す。失敗した試行を後続作業で解消した場合と、明示的に打ち切った場合を記録できるようにする。単に「failedが一つでもあれば永久に完了不能」とはしない。
4. **最終build確認と既存配送を保持する。** 完了判断の直前までTask開始との競合を防ぎ、完了後の古い通知を再送しない。Task中断を成功へ書き換えることや、勝手なAgent再実行で帳尻を合わせることはしない。

主な変更範囲はCoreのWorkflow受付・Task開始/復旧、Queue/Agent保存形式、Local Client/Local MCP呼出し、Worldの取消経路と契約テスト。受入テストは「実行中にComplete」「Task開始とCompleteの同時実行」「failed処理済み/未処理」「中断の復旧」「Core再起動」「古いIDの遅延完了」「取消とbuild判定の競合」。マスターの明示承認後、上記を実装した。主な正本は`core/holo/workflow.py`。CLIの直接JSON更新とプロセス間LockはWorkflow経路から除去し、Taskと同じCoreの受付を使う。Worldの読取形式は維持した。

## 修正済み既知問題との区別

exact Workflow ID必須化、Workflow writer間のLock、古いWorkflowによる置換防止、build入力の前後確認、stalled通知の再試行、送信直前のLease再検査、Review ACK、所有Conversation固定は現行コードに存在する。今回これらを新たな修正件数に含めない。既存テストを回帰確認に使った。

## 先行修正R1–R5の検証（承認前）

- 修正前: 新規の失敗経路11ケースが失敗し、既存quotaケース2件は成功。時間切れによるLease延長も失敗テストで確認。却下状態の誤配信2ケースも修正前に失敗。
- 修正後の狭いCore確認: 13件成功。追加状態通知・Workspace解放確認: 4件成功。
- 途中のCore関連4スイート: 210件成功。この後にR5と追加の横断テストを加えたため、最終根拠は下記の全体検証とする。
- NodeのWorkflow/Lock/build確認: 36件成功。
- World: 42ファイル・348件成功。TypeScript typecheck成功。
- Core全体: **722 passed / 1 skipped**（`python -m pytest -q --tb=short`、148.11秒）。R5と追加の横断テストを含む最終コードで成功。
- 最終build: **成功**。build ID `cce7ff0f-1803-4b48-9a04-43d2e882edb3`、入力fingerprint `28b58fedac0e196329308618b32c6e5db4e4eb0360e6623037b0ac445a0a0554`。開始前後の入力一致を確認する既存build経路で実行。
- `git -c core.safecrlf=false diff --check`: 成功。
- 実Provider、実ChatGPT Conversationへの送信、実プロセス強制終了は未検証。既存稼働プロセスの再起動は行っていない。

## 採用方式と比較

調査対象はPython公式の[asyncio同期プリミティブ](https://docs.python.org/3.12/library/asyncio-sync.html)、SQLite公式の[Atomic Commit](https://www.sqlite.org/atomiccommit.html)、[Isolation](https://www.sqlite.org/isolation.html)、[Transactions](https://www.sqlite.org/lang_transaction.html)。コードの転載や新規外部依存の導入はない。

| 方式 | 判断 |
| --- | --- |
| CLIとCoreがそれぞれJSONを更新し、相互Lockを追加する | Task受付が別責任のまま残り、完了確認の抜け道が増えるため採用しない |
| WorkflowとTaskを新SQLiteへ全面移行 | 複数プロセスのtransactionには適するが、既存Agent/Queue正本の移行または二重管理を伴うため今回採用しない |
| 既存Task実行者のCoreへWorkflow更新を集約 | 採用。単一Coreという既存Runtimeの前提の中で、受付と完了判定を同じLockへ入れ、Taskの状態は既存正本から確認する |

Core内Lockは別Coreプロセス同士の排他を提供しない。既存Agent Runtime同様、同じruntimeを複数Coreで同時駆動する構成は対象外。Agentの実行期間全体はLockしない。build計算は既存Node実装を共有し、通常Activityでbuild計算やEvent履歴読込を追加しない。

## 追加の修正・回帰条件

- Queue→AgentへWorkflow IDを渡し、Recovery childにも残す。ownerが配送後に消えても完了証拠を維持。
- pre-Agentの失敗結果をTask metadataへ確定してからQueueを解放。Crash直後の復元で二重実行せず、残ったownerから通知を再送。
- 完了確認中のTask受付を待たせ、完了が先なら遅れてきたTaskを拒否。build確認後も実行状態を再確認。
- failedを無条件成功にせず、成功Taskによる代替完了または明示打切りの理由を保存。中断の打切りは既存Runtimeの停止処理でWorkspaceを解放。
- 取消は所属Taskを停止してから完了。後片付けが続く場合はactiveを保って再試行を案内。
- malformedな保存データ、保存失敗、古いIDは完了を拒否。Activity保存失敗は元の成功結果を保持し、重複実行を誘発しない。
- Node側にあったLifecycle回帰テストはCore側へ移し、NodeではID伝達・所有Conversation・監視除外・失敗結果保持を確認。

## 承認後の検証（2026-09-16）

- Core共通処理36ケース成功。Task/Review/統合監査の未完了状態、後片付け、代替解決、Crash復元、古いID、同時受付、保存失敗を含む。
- 実Node CLI→認証付きlocalhost WebSocket→Coreの結合テストを含め、関連98ケース成功。ProviderはFake Adapterで隔離。
- Core全体: **758 passed / 1 skipped**、150.60秒。
- World全体: **42ファイル / 348件成功**。TypeScript typecheck成功。
- NodeのWorkflow/MCP/Lock/build: **28件成功**。
- Local MCP登録・意味Tool・隔離SDK接続: **4件成功**。設置済み依存を利用し、ダウンロードなし。MCPのActivity受付は隔離transportで検証し、実Coreへの伝達は上記CLI結合テストで検証。
- 設置済みMCPの`register-tools.mjs`も共通定義のimportへ変更。変更前SHA256 `4041e3743ad2894d11c5d970b8f41830f3326b958ac8a5d98d7c9f154889e5f4`を照合し、`.tools/execution-control-audit-20260915/register-tools.before.mjs`へ退避。変更後 `8608dd52d785f8fc9f4087ad9e7bf8459c61853bfb1b721e54763825765f3c2f`。
- 最終World build成功: build ID `a29eedef-8d45-4dd4-ad47-bba90a9e89e9`、fingerprint `7fbd43dd9fe724a86c3049a0ae5952a5af47d6b196c065e61230d2eaed5de003`。開始前後の入力一致と完了後`current: true`を確認。
- `git diff --check`成功。既存稼働CoreのWorkflowやTaskを変更する実操作はしていない。
- 既存稼働プロセスは再起動していない。新Core/MCPを読み込むには再起動が必要。実Cursor障害の再発、実ChatGPTでの自動送信、実プロセス強制終了によるCrashは未検証。テスト成功を本番動作の証明とは扱わない。
