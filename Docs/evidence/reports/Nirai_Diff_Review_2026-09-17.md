# Nirai Diffレビュー — 2026-09-17

対象はHEAD `b7ba218`に対する作業ツリー差分（未追跡のWorkflow共通処理も含む）。目的は、日常作業を重くする冗長な処理の削減と、簡略化に伴う不具合の修正。開始前からある変更を保持し、以下の局所修正を追加した。新しい管理層・常駐処理・外部依存は追加していない。

## 修正済み

| 優先度 | 問題と再現条件 | 修正 |
| --- | --- | --- |
| P2 | Workflow完了後、同じ会話の単発Task開始も完了済みWorkflowへの追加として拒否される | `core/holo/workflow.py:admit`と`tools/holo-workflow.mjs:workflowForOwner`で、自動参加先をactiveに限定。古いIDを明示した遅延開始は拒否する |
| P2 | `core/task_runtime.py`が通常の状態通知や完了復元でもイベント履歴を全件読み、使わず捨てる | 状態だけで処理し、失敗理由が必要な場合だけ履歴を読む。ログ読込を禁止した実行中・完了・取消・中断の4ケースで確認 |
| P2 | Auto ResumeがTask作成時刻から現在のWorkflow所属を推測し、旧Taskを別の依頼へ紐づける | `HoloWebHost.ts:resolveAutoResumeOwner`の時刻推測を削除。保存済みowner/TriggerのIDのみ使用 |
| P2 | 配送用ownerファイルが消えると、TriggerにWorkflow IDが残っていても完了済み通知を再送する | `isObsoleteAfterWorkflowCompletion`が保存済みTriggerのIDも使う |
| P2 | 旧配送レコードへIDを補完した際の保存に失敗しても、メモリ上にはIDが残り、次回は保存を飛ばして送信する | 保存失敗時に該当レコードを戻す。2回続けて保存が失敗しても送信しないことを確認 |
| P2 | ブラウザー側の配送記録の保存失敗を握りつぶし、送信後の再試行を判別できない。また、再読込後に画面内の同文件数が減ると送信済み通知を再送する | Masterの回答に従い、短い再開IDによる照合へ置換。件数記録とlocalStorageの読書き一式を削除 |
| P2 | Workflowのない単発Task/Reviewの再開通知が`Workflow ID: -`となり、対象を特定できない | Workflow所属時の短い文面は保持。単発時だけTask ID、Reviewでは取得用Agent Session IDを渡す |

設計文書に残っていた「Auto Resumeへ委譲方針や復旧規則を毎回載せる」記述も現在の簡略化に合わせた。長いポリシー文は復活させていない。

## 承認された送信確認の簡略化

修正前の`world/src/main/holo/holoWeb.ts:buildHoloAutoResumeSubmissionScript`は、保存した送信前件数より画面内の同文件数が増えたことを送信確認に使っていた。送信後・確認保存前の再読込などで過去メッセージの表示件数が減ると、送信済みのコピーが画面に残っていても再度クリックする。

隔離再現: `.tools/diff-review-20260917/probe-delivery-count.cjs`。送信前件数2、再開時の画面には送信済みコピー1件という条件で、修正前は`duplicateClicks: 1`かつ`status: not_ready`を確認。修正後は再開IDを持つコピー1件だけで`duplicateClicks: 0`、`status: submitted / duplicate: true`を確認した。これは生成スクリプトを偽DOMで実行した結果であり、実ChatGPTの実機検証ではない。

Masterは「短い再開IDを付け、件数記録を削る」を選択。文末に`再開ID: <delivery_id>`を1行だけ付け、ユーザーメッセージ内の完全一致で確認する。同じ配送の再試行は同じID、新しい続行要求は別IDという既存Host Queueの規則を再利用する。長いポリシー文は戻さず、別の保存先・移行管理・再試行機構も追加していない。

ブラウザー保存が使えなくても送信を確認できること、同じ先頭部分を持つ別IDやAssistantの引用を送信確認に使わないこともテストした。なお、送信済みメッセージ自体がDOMに存在しない場合や、ChatGPT側の画面構造変更まで保証するものではない。

## 検証

- 修正前の追加再現で、単発Task拒否、誤った所属推測、owner削除後の再送、配送ID保存の再試行、不要な履歴読込、ブラウザー保存失敗時の送信を確認。
- Core全体: **763 passed / 1 skipped**、151.96秒。
- World全体: **42ファイル / 355 passed**。
- Node（Local Client / MCP / file-lock / build状態）: **28 passed**。
- World typecheck、`git diff --check`: 成功。
- World最終build: 成功。build ID `8be20ce3-9842-4aad-b3ff-ead1aa01fcf2`、fingerprint `4f811182b1b0fd70a32fef2e0a04db90e3c6ff9f521053eeef4916985fba32d3`。完了後のstatusでも`current: true`。
- 実Provider起動、実ChatGPTへの送信、稼働プロセスの再起動は未実施。テスト成功を本番配送の証明とは扱わない。commit/pushなし。
