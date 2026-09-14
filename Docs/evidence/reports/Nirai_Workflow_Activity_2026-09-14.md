# Workflow / Lease / Auto Resume 制御整理 — 2026-09-14

依頼範囲の実装、複雑性監査、回帰検証を完了した。専用Heartbeatを通常のDive / Auto Resumeの手順から外し、所有者の通常操作が成功した時点でLeaseを更新する。新しい常駐サービス、状態DB、無条件の延命タイマーは追加していない。

## 以前の構造と問題

Holo Local Clientが`workflow.json`を更新し、Worldが30秒以上の更新停止と5秒の画面上の待機を確認すると、同じConversationへ再開通知を送っていた。Task / Review / Agent Sessionの作業はCoreに届くが、その活動はLease更新へ接続されていなかった。専用Heartbeatが実行前に拒否されると、作業の実態とLeaseの時刻が食い違う構造だった。

また、RendererとHostに通知の検証・重複判定が二重実装されていた。監視処理は同じ更新版を2変数で保持していた。Task所有情報は通知の受理時に早く削除していたため、送信待ちの間にWorkflowが完了しても関連を判定しにくかった。

今回の作業開始時点で、attach復元、Workflow ID必須化、同時書込みのLock、最終build判定などの未コミット変更が存在した。それらを維持した。今回の成果に既存変更の全行を含めて数えていない。commit / pushはしていない。

## 最終的な動作

1. `workflow-start`で得たWorkflow IDを、その依頼のIDとして維持する。
2. HoloのTask / Review操作の成功応答には、Coreが保存済み所有情報からWorkflow IDを添える。現在接続しているDiveが所有Diveと一致する場合にだけ、自動判定用の所有情報を返す。
3. Local Clientの共通受付は、この所有情報、または明示されたWorkflow IDを使ってLeaseを更新する。Task / Review / Conversation系の新しい操作も同じ判定を通す。
4. 汎用Local MCPのファイル作業・テスト・Process Job取得などは、通常引数に`workflowId`を添える。登録時の共通ラッパーが成功を観測する。現在表示中のDiveを作業者だと推測しない。
5. 更新は`tools/holo-workflow.mjs`に集約し、既存の同時書込みLockを共有する。受付時と完了時のWorkflow IDを照合し、同じIDがactiveの場合だけ更新する。時刻は並列操作でも前進させる。
6. 失敗・拒否・Timeout、別Workflowの応答、完了・取消後の遅延結果は延命しない。Lease保存だけが失敗した場合は実作業の成功結果を保持して警告を添える。重複実行を誘う「Task失敗」にすり替えない。
7. Activityが停止し、ChatGPTも生成中でなければ、従来の監視がstalledを導出してAuto Resumeする。送信前に更新版を再照合し、既に再開・完了した依頼への古い通知を落とす。

| 操作 | Lease更新 |
| --- | --- |
| 所有Taskの開始・snapshot・wait・応答・取消・復旧 | 成功時に更新。保存済み所有情報または明示IDが必要 |
| 所有Reviewの開始・wait・結果取得・復旧 | 同上 |
| Conversation操作、通常MCPの実作業、所有Jobの取得 | 明示Workflow IDを付けた成功時に更新 |
| `workflow-status`、全体snapshot、全体イベント待機、skills、incidents、task-targets | 更新しない |
| health_check、Process Job一覧、利用可能ディレクトリ一覧 | 更新しない |
| Auto Resume巡回・配送・ACK、設定画面の状態確認 | 更新しない |
| Task / Reviewを監視するだけの読取り | `observeOnly: true`／CLI `--observe-only`で更新しない |
| Workflow完了・取消 | activeを終了。完了記録は古い要求の照合用に残す |

直接CLIを使う場合の共通引数は、コマンドより前の`--workflow-id <id>`／`--observe-only`。Task ownerが旧形式、または終端通知後に削除済みの場合は、通常作業に既知のWorkflow IDを添える。専用Heartbeatで補う運用にはしない。

## 削除・統合したもの

| 対象 | 変更前 → 変更後 |
| --- | --- |
| 通常指示文での専用Heartbeat要求 | Bootstrap + 3種類のResumeの4箇所 → 0箇所 |
| 監視の進捗・再送判断用変数 | idle開始、観測版、通知済み版、通知時刻の4つ → 通知済み版を除いた3つ |
| 通知の検証実装 | Renderer / Hostの2つ → 共有1つ |
| 通知の重複キー生成 | Renderer / Hostの2つ → 共有1つ。保存済みキー形式は維持 |
| HeartbeatのMCP登録定義 | 2つの同型定義 → 共有定義1つ。公開名2つは互換用に維持 |
| Task ownerの早期削除 | Queue受理時の削除を撤去。配送・破棄を保存した後の共通処理へ |
| 完了済み作業の通知抑制 | Reviewだけの時刻推測 → 新形式はTask / Review共通のWorkflow ID照合 |
| Windows Lockの所有者読取り | ファイル読取りだけの再試行 → フォルダー確認を含む一つの観測単位へ |

Workflowの永続状態は`active / completed`の2種類のまま。新しい「生存」「進行中Activity」「Resume所有権」などの永続状態は増やしていない。追加した`task_owner.workflow_id`は依頼との対応を表す固定IDで、別の進行状態ではない。

active Leaseは1件で、終了前に別IDへ置換できない。このため、所有Workflowと現在LeaseのIDが違うことから旧依頼の終了を導出でき、完了Workflow IDの別リストは不要。完了・置換済みの依頼へ後着した失敗通知も再開対象から除外する。

コード行数全体が減ったとは主張しない。共通化のための移動と不具合防止の追加を含む。入口のLocal Clientは698→468行、Renderer outboxは223→203行だが、移動先の共通モジュールも存在する。主要な削減は、明示Heartbeatという運用工程、重複した判定、余分な監視変数、早すぎるcleanupである。

## 制御系の棚卸しと残した理由

| 対象 | 確認・判断 |
| --- | --- |
| WorkflowとLease | 今回も1ファイルを正本にする。stalled専用状態や別期限を追加しない |
| Task ownerと現在Dive | 保存された仕事の宛先と、現在表示している画面は別の事実。統合すると背景Taskが別Conversationへ送られるため分離を維持 |
| Task QueueとAgent Session | Agent起動前の予約と、起動後の実行記録。restart時は既にSession化したTaskをQueueから除外する既存処理を維持 |
| Agentのrun_stateとtask_phase | 実行状態と、依頼の受付・担当決定・結果反映の段階。一部重複はあるがUIと保存形式へ広く影響するため今回は統合しない |
| Agent復旧の親子リンク | 同じ中断Sessionから2回再実行しないための予約。Lock下の再確認とrestart時のリンク修復を維持 |
| result_reportedとresult_notified | 結果をChatへ記録したことと、利用側へ通知したこと。片方を消すと再起動時に結果が欠落または二重記録されるため維持 |
| Renderer outboxとHost Queue | Hostが受理するまでの保存と、ChatGPTへ送れるまでの保存。プロセスをまたぐ受渡しなので両段階を維持し、共通化は検証・キーへ限定 |
| Review ACK | Core→Renderer→Hostの受理確認。送っただけで完了扱いしない既存の再送能力を維持 |
| 取消の記録 | 再起動や遅延通知による再投入を止めるため維持。Workflow取消は共通writer、Task取消は既存Coreの正規経路を使う |
| Task終端通知とstalled通知 | Agent側の結果到着と、Holo側の停止検出という異なる入口。所有Conversationへの同じ配送Queueへ集まり、画面の生成中確認で重なる送信を避ける |
| 復旧・cleanup | 新しい回復workerは追加しない。通知後の古い所有情報整理を共通化し、既存のTask/Agent crash recoveryは維持 |

## 検証結果

| 検証 | 最終結果 |
| --- | --- |
| Core全体 pytest | **695 passed, 1 skipped**、162.72秒 |
| World全体 Vitest | **41 files / 320 passed** |
| Node：Workflow / Lock / 最終build判定 | **35 passed** |
| Local MCP：登録・実受付・権限制御・反映済み入口 | **8 passed** |
| TypeScript型検査 | 成功 |
| World production build | 成功。最終入力と成功stampの一致を再確認 |
| `git -c core.safecrlf=false diff --check` | 成功 |

追加回帰には、専用Heartbeatなしで複数期限をまたぐ活動、監視除外、別Dive・別Workflow拒否、並列更新、完了・取消後の遅延Activity、二重start防止、所有情報のrestart、実Core→CLIでのTask/Review取得、Lease保存失敗時の成功保持を含む。World側では、本当の停止→stalled→Resume、送信失敗後の再通知、最新Activity中の抑制、送信先移動中の再開・完了競合、完了・置換済みTaskの後着通知抑制、送信待ちownerのcleanupを確認した。

初回の制限付き実行では、Provider用の一時認証ファイルのACLや子プロセス停止でCore 8件が失敗した。並行実行したHolo単体でもWindowsの一時ファイル競合・待機Timeoutが発生した。そのまま成功扱いせず、通常ユーザー権限で全体を再実行し、最終的に全695件の成功を確認した。既存`.venv`の起動先が利用できなかったため、同じPython 3.12系列の同梱Pythonと既存site-packagesを使用した。追加ダウンロードはしていない。

最終ログは同名ディレクトリ`Nirai_Workflow_Activity_2026-09-14/`に保存した。実ChatGPTへの自動送信、実Providerでの長時間作業、停電を伴う試験は実施していない。外部画面を使う送信について、完全な一度きり実行保証を新たに主張するものではない。

## 反映範囲

- Core / World / Local Client / 設計書を更新した。Worldは最終build済み。
- Local MCPの設置済み`index.mjs`と`register-tools.mjs`を、変更前ハッシュ照合とバックアップ後に更新した。実行中Jobが0件の状態で既存手順からLocal MCPを再起動し、healthが正常であることを確認した。
- Local MCP側の編集元は既存の`.tools/local-mcp-guard/`。このディレクトリは従来からGit対象外である。共通のActivity処理・テストは`tools/`に置いた。
- 起動中のNirai本体のCore / Worldは再起動していない。新しいCore応答とBootstrap / Resume文面は、Nirai本体の次回起動から使用される。

## 今回は触らなかった削減候補

- `run_state`から導ける`task_phase`部分の整理。ただし、受付前後の段階や保存形式を含めた別の移行検証が必要。
- TaskとReviewの「結果反映・配送ACK」の共通化。現在は通常Chatへの報告とHolo Review通知で用途が異なる。
- Hostの画面切替・retry・watchdogの制御フラグ。画面への割込みと所有Conversation復元を扱うため、実Electronの障害試験を伴わず機械的に削除しない。
- 旧Task ownerの時刻互換、旧Heartbeat API、古い通知形式。過去Conversationや保存済み通知への依存が残るため、今回は通常経路から外す／新形式では使わないところまでとした。

## 設計比較に用いた参照

既存の共通受付を利用する判断に際し、[MCPのTool Schema](https://modelcontextprotocol.io/specification/2025-11-25/schema)、[TemporalのActivity障害検出](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/detecting-activity-failures.mdx)、[etcdのLease API](https://etcd.io/docs/v3.5/learning/api/)を比較した。これはNiraiでの採用判断であり、各製品が今回と同じ方式を保証するという意味ではない。外部Workflowエンジンの導入や別のkeep-alive通信は責務と依存を増やすため採用せず、既存の操作受付・単一Lease・配送処理を利用した。
