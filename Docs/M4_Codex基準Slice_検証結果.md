# M4 Codex基準Slice 検証結果

検証日: 2026-09-04

## 判定

**SAFE（2026-09-04 DiffレビューSAFE + 現行実Codex Approval E2E通過）**

Codex Agent Runtime基準Sliceは、Core・World実装、自動回帰、実Codex app-server接続・File Change Approval・Task完了記録まで成立している。最新DiffレビューではTimeout cleanup競合P1とAI_ENTRYのレビュー世代矛盾P2の解消が確認され、新しいBlocking／回帰なしでSAFE。続けて`grantRoot / itemId`変更後の現行コードから実Codex File Change Approval E2Eを再実施し、File Change Approval 1回だけを`approve_once`して完走、Session=`completed`、Task Update=`done`、Chat結果1件、World Memory結果1件、一時Codex Home削除を確認したため、本Codex基準Sliceを**SAFE**とする。M4全体は未完了。

## 実装済み

- Brain DriverとAgent Runtimeを分離
- Codex app-server 0.147.0を基準Protocolとして利用
- Provider固有EventをNirai共通Agent Eventへ正規化
- Command / Tool / File Change / Diff / Approval / Question / Plan / Todo / Run State / Error / Cancel
- 非公開ReasoningをEventへ保存・表示しない
- Provider固有thread / turn / session IDをWorld Eventへ露出しない
- Agent Sessionを`runtime/agent_sessions/<agent_session_id>/session.json + events.jsonl`へ永続化
- Core再起動時の未完了Sessionを`interrupted`へ遷移し、永続化した元Chat Session IDから失敗結果をChat / World Memoryへ冪等復旧
- World再接続時のSnapshot復元と未回答Approval / Question / Plan復元。`last_event_seq`より古いSnapshot / Eventでlive状態を巻き戻さない
- Agent Event永続化は起動時に`events.jsonl`の最終正常seqとSnapshotを照合し、Snapshot遅延を復旧。不完全なJSONL末尾1行は切り詰めて正常Eventを保持
- 60分上限。超過時もTimeout確定・永続化 → Provider interrupt → Provider Task cancel / cleanup → Adapter終了の停止Sequenceを通し、`failed`へ確定
- 通常CancelのProvider interruptには短い上限時間を設け、無応答でも停止処理を継続。Codex app-server停止全体にも有限上限を設け、`taskkill / terminate / kill / wait`のOSError・PermissionError・timeoutを処理する。Process停止失敗時もCredential Home cleanupを別処理として必ず実行し、ProcessまたはHome残留を明示エラーにする
- Cancel / Timeout後にProvider Taskを終了した際は終了結果を検査し、ProcessまたはCredential Home cleanup失敗が返った場合は`provider_cleanup_failed` Error Eventを永続化してSessionを`failed`へ確定する。単なるProvider interrupt無応答と、実資源cleanup失敗を混同しない
- Timeoutを検出した時点でSessionを`cancelling`へ遷移し、`session_timeout` Error Eventを永続化してからProvider interrupt / cleanupへ入る。したがってcleanup中の通常Cancelは再受付されず、Timeoutの事実とcleanupの完走を保護する
- Sessionがすでに`cancelling`なら2回目以降のManager `cancel()`は`False`を返し、Provider interruptや管理Taskへのcancelを再実行しない。Core終了処理からの再停止も同じ冪等境界を通る
- Agent Runtimeは終了開始を`_stopping`で固定し、それ以降の新規`start_session()`を拒否する。開始予約はProvider管理Task登録または安全中断まで保持し、開始処理完了Eventを`stop()`が待つため、起動途中のSessionを終了処理が追い越さない
- 開始Event通知中に終了が割り込んだ場合はProvider管理Taskを作らずSessionを`cancelled`へ確定する。`stop()`は開始処理待機後、`_tasks`だけでなく全Non-Terminal Snapshotを停止対象として再走査する
- CoreServerは終了処理の冒頭でAgent Runtimeを停止開始状態へ切り替えるため、既存World WebSocketがまだ開いている間に届いた新規`task_request`もProvider起動前に拒否する
- `tasks.allowed_dirs`外のworking directoryをProvider起動前に拒否
- Nirai `core/` / `world/`を通常Agent Runtimeから直接書換え不可
- Task working directoryへ`task.md`を保存
- 通常Task開始時および後続Task Updateへ`working_dir`を含め、World Agent Storeが再接続前からFile Path IPCの基準Directoryを保持する
- Task終了結果を元Chat Sessionへ高水準Entry 1件だけ保存
- 同結果をWorld Memory Episodeへ1件だけ保存
- Command全文・大量DiffはChat Session / World Memoryへ複製しない
- Agent作業中の元Chat Session削除・World Memory忘却を拒否
- Approval Policy=`untrusted`
- Approval reviewer=`user`
- workspace-write sandbox + writableRoots=Task working directory + networkAccess=false
- Codex File Change Approvalの`itemId`をNirai `operation_id`として保持し、Schemaの`grantRoot`をMaster提示前にTask working directory内か検証。範囲外はfail-closedでProviderへ`decline`を返す
- Approval Cardは同じ`operation_id`のFile Changeだけを承認対象として表示し、関連Eventが未取得・不明な場合は許可ボタンを無効化する。別File Changeを「直前だから」という理由で承認対象へ流用しない
- Secret Questionをpassword入力として表示
- Agent作業状態を通常Brain応答状態と分離
- Queue完成まではAgent Session同時実行数を1件に固定し、実行中または起動予約中の2件目Taskはbusyとして拒否する。Credential Home予約前後の並行起動を許さない
- Terminal Task結果は`task_phase`と`result_reported`を同じSnapshot更新で保存し、`run_state`がterminalなのに旧phaseが残るCrash中間状態はCore再起動時に補正する。さらにWorld通知済み状態を`result_notified`として別永続化し、`result_reported=true / result_notified=false`なら次回World接続へTerminal Snapshot / Chat Entry / Task Updateを再通知し、送信成功後だけ`result_notified=true`へ進める
- World切断中にTerminalへ到達した場合も、同一Core内の再通知対象として保持する。Core再起動を挟まなくても次回World接続でTerminal Snapshot / Chat Entry / Task Updateを復旧し、通知成功後だけ`result_notified=true`へ確定する
- Command / File Changeのstreaming deltaは永続化せず、completed Eventだけを保存する。Event文字列12,000文字、1 payload約32,000文字、Session payload約2,000,000文字、Final Summary 8,000文字を上限とし、安全上重要なApproval / Question / Plan / Run State / ErrorはSession payload枯渇時も保持する
- World Agent UIはCommand output / Diffを`details`で既定折り畳みとし、文字数・行数Summaryを先に表示する
- World Agent UIは`cancelling`中の停止ボタンを表示せず、Approval / Question / PlanのMaster入力カードも`waiting_for_master`時だけ操作可能にして停止処理中の追加操作を抑止する
- Agent Markdownはraw HTMLを解釈せずReact Textとして扱い、見出し・箇条書き・Code Block・Inline Code・Table・http/https Markdown Link・File Pathを表示する。`javascript:`等はLink化しない
- File Pathを開く場合は専用Preload IPCを通し、Electron Mainで`runtime/workspace`配下かつ当該working directory内の既存Fileか再検証してからOS既定アプリへ渡す
- Core起動ごとのWorld専用SecretでWorld WebSocketを認証し、認証前はWorld置換・Snapshot取得・Approval / Plan / Agent Cancel等を拒否
- Codex app-server stderrは512-byte単位で読み、1行最大2048 bytesだけを一時bufferし、DEBUGログへは改行escapeした先頭500文字だけを記録する。501文字目以降は同じ長行の続きとして破棄する

## Codex Home / Secret隔離

実Codex Smokeで、通常のCodex Homeをそのまま継承した場合に`C:\Users\...\.codex\skills\...`を自動読取する経路を確認したため、以下へ変更した。

- Agent Sessionごとに一時`runtime/codex_agent_homes/<agent_session_id>`を作成
- 元Codex Homeから`auth.json`だけを複製
- `config.toml` / `AGENTS.md` / `skills` / `memories`等は持ち込まない
- `CODEX_HOME` / `USERPROFILE` / `HOME`を一時Homeへ固定
- 親Coreの環境変数を丸ごと継承せず、OS実行に必要な最小環境だけを渡す
- `GEMINI_API_KEY`等の無関係な秘密環境変数はCodex Agent子Processへ渡さない
- Agent終了後に一時Codex Homeを削除し、不存在を確認する。Process停止処理の成否とは分離して必ずcleanupを試行し、削除失敗時は再試行、それでも残れば明示エラーにする
- Agent起動前に`runtime/codex_agent_homes`の残留Homeと旧実装の`runtime/workspace/m4-codex-agent-home`を棚卸しして削除する。削除できない場合は新しいAgentを開始しない
- Windowsでは一時`auth.json`のACL継承を外し、現在ユーザーだけへFull Controlを付与する
- 2026-09-04中間レビューで確認された実残留`runtime/workspace/m4-codex-agent-home`は、内容を再表示せずディレクトリごと削除済み
- Developer Instructionでもworking directory外の読取を禁止し、必要時はMasterへ確認するよう明示

隔離後の実Codex Core E2EではGlobal Skill / User Homeを読むCommandは発生せず、File Changeの直前でApprovalへ到達した。

## 現行Codex / WindowsのRead境界制約

Codex 0.147.0のapp-server `workspaceWrite`はwritable rootを制限できる一方、任意のfilesystem read rootを同じProtocolから厳密に限定する契約にはなっていない。新しいPermission Profile系は存在するが、0.147.0の`thread/start` / `turn/start` Schemaには直接`permissionProfile`指定が無く、Windowsのrestricted-read permission profileにも2026-09時点で既知の不具合報告がある。

したがって現時点の保証は次のように切る。

- **書込み境界**: Nirai `allowed_dirs` + working directory + Codex workspace-write sandboxで強制
- **Network**: Codex sandboxでdisabled
- **自動Global Skill / User Config / Memory読取**: Session専用Codex Homeで遮断
- **親Coreの秘密環境変数**: allowlist環境で遮断
- **任意の外部filesystem readをOSレベルで完全禁止**: 現行Windows Codex側の制約により保証しない。Nirai instructionとHome/Env隔離で縮小する

この制約は隠さず、Codex側でWindows restricted-readが実用可能になった時点でPermission Profileへ移行候補とする。

## 自動検証

- Core pytest: **202 passed**（Codex基準Slice SAFE確定時183件 + 後続Task調停第1巡Slice初期回帰8件 + 独立レビュー修正回帰11件）
- World Vitest: **39 files / 210 tests passed**
- World TypeScript typecheck: 成功
- World Production Build: 成功
- `git diff --check`: 成功

Agent Runtime追加受入では少なくとも以下を自動固定した。

- Approval → Question → Plan → completed
- Cancel → cancelled
- Provider cleanup進行中の2回目Cancel → `False`で冪等拒否し、管理Taskを維持したまま最終cancelled
- World UIは`cancelling`中に停止操作を提示しない
- 開始Event通知中にManager `stop()`を固定割り込み → stopは開始処理を待機し、Provider未起動・Session=`cancelled`・管理Task残置なし
- Agent Runtime停止開始後の新規`start_session()` → 拒否
- CoreServer終了開始後、既存World WebSocketから新規`task_request` → Noticeで拒否しProvider未起動
- Provider interrupt無応答でもCancelがhangせず → cleanup成功ならcancelled
- Cancel時のProvider cleanup失敗 → `provider_cleanup_failed` Error Eventを保持してfailed
- Timeout時のProvider cleanup失敗 → `session_timeout`に加えて`provider_cleanup_failed`を保持してfailed
- Timeout cleanup中の通常Cancel → `False`で冪等拒否、Provider interrupt 1回、cleanup完了、`session_timeout`保持、最終failed
- Timeout → Provider interruptを試行してfailed
- Core再起動 → interrupted + 元Chat / World Memoryへ失敗結果を1件だけ冪等復旧 + Worldへ一度通知
- World切断中Approval待ち → 再接続Snapshot復元
- World切断中にTask完了 → Core再起動なしの再接続でTerminal Snapshot / Chat Entry / Task Updateを復旧し、通知済み状態へ進む
- 通常Task開始直後のTask Updateで`working_dir`をWorld Storeへ反映し、再接続前でもFile Path基準Directoryを保持
- live Event先着後に古いSnapshotが届いても状態を巻き戻さない
- Snapshotより`events.jsonl`が1seq先行したCrash状態から次seqを重複なく復旧
- JSONL末尾の不完全1行だけを除去し、正常Eventを維持
- Provider固有Protocol非露出
- Private reasoning非露出
- Task結果1件保存と詳細Command非混入
- working directory境界
- Session Codex Homeへauthだけ複製
- 旧Credential Home残留の起動前削除・削除retry・不存在確認
- 無関係な秘密環境変数非継承
- 未認証Worldによる正規World置換・Snapshot取得を拒否
- File Change Approvalの`grantRoot`をworking directory外へ向けるとMaster UI前に拒否し、`itemId`を`operation_id`として保持する
- 複数File Changeが存在してもApprovalは同一`operation_id`のEventだけを関連付け、未関連付け時は許可不可
- 1件目Agent Session実行中の2件目開始をbusy拒否
- `completed + task_phase=running + result_reported=true`のCrash状態を再起動時に`done`へ補正し、Chat結果を重複させず通知を復旧
- `completed + done + result_reported=true + result_notified=false`の通知直前Crash状態を次回World接続でSnapshot / Chat Entry / Task Updateへ復旧し、送信成功後は`result_notified=true`となって次回再起動では再送しない
- 100k文字級のEvent payloadを上限内へ切り詰め、Session budget枯渇後の非Blocking詳細Eventを要約へ縮退
- Command/File Change streaming deltaを永続化しない
- raw HTML無効化、`javascript:`拒否、Table / Markdown Link / File Path parse、File Open workspace境界、Command/Diff既定折り畳み
- `taskkill失敗 → terminate失敗 → kill失敗`と`wait timeout`を故障注入し、Process停止処理がhangせず失敗を明示する。`client.close()`失敗時もCredential Home cleanupが実行される
- DEBUG stderrへ500文字超の長文を流し、501文字目以降のsentinelがログへ残らない

## 実Codex検証

### Adapter直Smoke

実`codex app-server` + `gpt-5.6-sol`で、`runtime/workspace/M4-SMOKE-*`内の小さなFile作成が成功した実績あり。ただし当時はApproval Policy強化前。

### Core E2E / 現行安全設定

隔離Nirai rootからCoreServer → WebSocket `task_request` → 実Codex app-serverまで通し、次を確認した。

1. Codex起動成功
2. Global Skill / User Home Commandなし
3. File Change Eventに対象FileとDiffが出る
4. 実File Change Approval Requestが発生
5. Approval未許可時はHolo側でDecisionを生成せず`cancel`でき、Sessionが`cancelled`で終了する
6. Master自身が隔離Smokeの`M4_CORE_SMOKE.txt`作成を「今回だけ許可」し、最初のFile Change Approvalへ`approve_once`を返した
7. 追加の検証Command Approvalが発生した試行では、許可範囲外として承認せず安全停止した
8. 検証Command自体を不要にした最終SmokeでApprovalはFile Change 1回だけ発生し、その1回だけを`approve_once`して完走した
9. `waiting_for_master → running → completed`を確認
10. `M4_CORE_SMOKE.txt`はTask working directory内だけに生成され、内容は`NIRAI_M4_CORE_E2E_OK\n`
11. Final Summary=`NIRAI_M4_CORE_E2E_OK`
12. Task Update=`done`
13. Chat Task結果=1件
14. World Memory Episode=1件
15. Agent Event詳細は`runtime/agent_sessions`へ保存
16. Session専用一時`CODEX_HOME`は終了後に削除
17. Global Skill / User Home外読取Commandは最終Smokeで発生しなかった

上記の既存Live E2Eに加え、2026-09-04の最新DiffレビューSAFE後に、`grantRoot / itemId` Approval境界変更後の現行コードから専用Smoke Harnessを再実行した。実CodexのFile Change Approvalは1回だけ発生し、その`file_change`を`approve_once`して完走。`NIRAI_M4_CORE_E2E_OK`の実File生成、Final Summary一致、Task Update=`done`、Chat Task結果1件、World Memory Episode 1件、Agent Event保存、一時Codex Home削除を確認した。したがってCodex基準Sliceは**SAFE**。Smoke生成物は成功後に専用接頭辞`M4-CORE-SMOKE-*` / `M4-HOME-SMOKE-*` / `M4-SMOKE-*`を掃除し、`runtime/workspace`内に残骸がないことも確認した。

## M4全体として後続に残るもの

Codex基準Slice SAFE後のM4後続状態は以下。

- Resident相談 / volunteer / 担当決定 / 立候補ゼロ停止: 後続`M4 Task調停第1巡Slice`で実装済み。独立レビュー6件を修正し、Holo + Cursor ExHigh二重レビューともSAFEで第1巡Slice確定SAFE
- Task Queue（現時点はTask Flow / Agent Sessionを同時1件に固定し、2件目busy拒否）
- `/task @対象フォルダ名 ...`対象指定
- Cursor Agent Runtime
- Claude Agent Runtime
- Antigravity Agent Runtime

M3 Natural Idle / Brain生活ティック / World Observationは別途M3後続であり、本Sliceには含めない。
