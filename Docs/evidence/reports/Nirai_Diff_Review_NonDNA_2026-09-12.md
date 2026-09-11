# Nirai Diffレビュー（DNA除外）— 2026-09-12

基準はHEAD `4efde30` と作業ツリーの差分。Holo、Core、Agent Runtime、起動コマンド、テスト整理を対象とした。DNA設計書・DNA向けallowed_dirs設定・Private側実装は本レビューの対象外で、今回の追加修正でも触れていない。生成物 `world/runtime/` も意図的には変更していない。commit / pushは実施していない。

Masterの「通常のTask・Plan・安全に検証できるファイル操作は自動続行し、大規模破壊・commit・push等だけ直接確認する」という運用意図を維持したまま、初回レビューで残ったR1/P1・R2/P2まで閉じた。

## 最終判定

**SAFE。今回のレビュー範囲で既知のP1/P2残件はない。**

初回レビューで残った2件は、文字列パターン追加だけの局所対処ではなく、失敗時・再起動時・任意スクリプト実行を含めても境界が成立する方式へ変更した。

### R1 / P1 — Codex重大破壊の判定をコマンド文字列から最終効果へ移行

旧方式では `_codex_approval_requires_master()` が既知の文字列パターンで危険コマンドを検出していたため、`python -c "import shutil; shutil.rmtree(...)"` 等へ包むと大規模削除を事前判定できなかった。またProvider通知単位の削除数判定では、小分け通知による閾値回避の余地があった。

修正後の書き込みCodexは、実Task WorkspaceではなくNirai管理の隔離staging copyで実行する。Providerの任意コマンド・ファイル操作はstaging内に閉じ、Provider停止後にTurn全体の最終差分を凍結してから実Workspaceへ反映する。大規模削除のMaster判定はこの集約済み差分に対して1回行う。

これにより、破壊操作をPython・PowerShell・別ツール等へ包んでも実Workspaceを直接破壊できず、複数通知へ分割しても最終差分では合算される。commit / pushはファイル差分トランザクション外へ影響できるため、従来どおり実行前の直接Master境界として残した。

stagingでは `.venv` / `venv` / `node_modules` 等の巨大依存木を複製せず、既存インストール済み依存を読み取り用途で再利用できる環境変数を設定する。通常のtest/buildを自動続行しつつ、ソース書き込み先はstagingに限定する。

さらにstagingには `.git` を複製しないため、Gitが親ディレクトリを探索して実Niraiリポジトリを発見しないよう、Codex子プロセスへ `GIT_CEILING_DIRECTORIES=<staging>` を設定した。回帰で実際のspawn cwdが実Workspaceではなくstagingであり、同環境変数もそのstaging自身を指すことを確認した。

回帰では、30ファイルを持つ実Workspaceに対してProvider側が `python -c ... shutil.rmtree(...)` 相当の任意スクリプト削除をstaging内で行うケースを再現した。Providerの個別file-change通知に依存せず、最終凍結差分が重大削除としてMaster確認になり、reject時は実Workspaceの30ファイルがすべて残ることを確認した。

また共有staging基盤について、実Workspaceへの反映開始後にCancelが届いた場合は、反映済み作業を通常cancelとして再実行可能にしないよう `work_committed=True` として扱う境界をCodex/Cursor双方で明示した。反映前Cancelは従来どおり未反映のcancelとなる。

### R2 / P2 — Auto Resumeを送信元耐久Outbox + Host耐久ACKへ変更

旧方式ではHostの未送信Queueが32件で満杯になると `accepted=false` を返したが、Rendererが結果を保持せず33件目以降を失う可能性があった。

Rendererへ `HoloAutoResumeOutbox` を追加し、Auto Resume TriggerをまずlocalStorageへ永続化してからHostへ送る方式へ変更した。Hostは所有Conversationを確定し、自身の永続Queueへ保存し終えた場合だけ `accepted=true` を返す。Rendererは `accepted` または `duplicate` を受けるまでOutbox項目を削除しない。

したがってHost Queue満杯、状態ファイル保存失敗、一時的なIPC失敗、World再起動を挟んでも未配送TriggerはRenderer側に残って再送される。Host側のTrigger Key重複排除とTask所有Dive固定は維持しているため、再送による二重継続や別Conversationへの誤配送も防ぐ。

回帰では、Host保存失敗時にacceptedを返さないこと、Renderer再生成後も拒否済みTriggerが残って再送されること、duplicate/accepted時だけOutboxから消えることを確認した。

## その他の修正

| 箇所 | 修正と確認 |
|---|---|
| Codexファイル変更 | Provider通知のdelete形式、変更先・move先のWorkspace境界を検証。書き込みTurnではProvider通知そのものを最終安全判定に使わず、凍結staging差分を正本化。 |
| Antigravity承認 | `approve_once` が後続削除全体へ拡大される状態を修正し、閾値到達後は独立した決裁が必要になることを確認。 |
| Agent起動時調査 | 不要な全ファイル調査を削減し、必要な調査はCore応答を止めない形へ移動。失敗・Cancel時の後片付け経路も統合。 |
| Holo Task状態 | 通常状態確認で最大500イベントとtask.mdを毎回読む処理を省略。古い承認待ち復元だけ互換維持。 |
| 実行前失敗Task | World送信後も直近結果を別記録へ保持し、状態取得から即消失しないよう修正。 |
| Holo初期化 | 保存状態読込Promiseを共有し、読込途中にホーム画面へ進む競合を修正。 |
| Holo背景送信 | busy / draft / 未準備 / JS失敗でも元Conversationへ復帰。送信とwatchdogの画面移動を直列化し、送信直前にもURL・draftを再確認。 |
| 古いAuto Resume | 完了・更新済みWorkflowの古いResumeと壊れた永続Queue項目を破棄。所有不明TriggerはCurrent Diveへ推測配送せずfail closed。 |
| Holo待機 | NaN / Infinity timeoutを拒否し、Cancel時にTask待ち・接続終了待ちの双方を終了。 |
| Local Client | Core確定済みTask ownershipの二重書込みを削除し、成功後の余分な保存失敗でTask開始自体を失敗扱いしないよう整理。 |
| 説明文 | Approval/Plan決裁とAuto Resume所有Conversationの説明を実装へ一致させた。 |

## 最終検証

- Codex focused：`core/tests/test_codex_agent_runtime.py` — **25 passed**。
- Core全体：`.venv\Scripts\python.exe -m pytest -q --tb=short` — **630 passed / 113.79秒**。
- World全体：`npm --prefix world test` — **39 files / 262 passed**。
- 型チェック：`npm --prefix world run typecheck` — **成功**。
- ビルド：`npm --prefix world run build` — **成功**。
- 差分整合：`git diff --check` — **成功**。改行コードのLF→CRLF警告のみで、空白エラー・競合マーカーはなし。
- Core全件実行中の作業ツリーfingerprintは開始時・終了時とも `80cc024c45b2f4f7645344f91340ca5bdb6a6b996c14ec9a0207e8318aab771e` で一致し、別セッションによる途中ソース変更がないことを確認した。

実Providerへの新規有料呼び出し、実ChatGPT画面での大量Auto Resume配送、Electron終了と実通信の実機競合は行っていない。Worldテストは疑似画面・疑似DOMを用いるため、実機での長時間継続動作そのものまで保証するものではない。

既存Diffにはテストの削除・統合も含まれる。件数・カバレッジ維持自体を目的にせず、「削除すると現実的に何を見逃すか」で整理した現行テスト群を基準とする。
