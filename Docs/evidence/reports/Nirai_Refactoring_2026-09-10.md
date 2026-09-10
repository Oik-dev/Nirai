# Nirai 挙動互換リファクタリング結果 — 2026-09-10

既存のRound監査修正を含む作業ツリーを起点に、Agent Runtimeを中心としてCore・Conversation・Worldの責務分離と重複除去を実施した。機能、通信内容、保存形式、Provider設定、承認・隔離・キャンセルの安全境界は維持した。

## 変更範囲

既存ソース11ファイルを編集し、責務分離先として14ファイルを追加した。既存の未コミット変更は保持し、テストファイル・依存パッケージ・製品設定は変更していない。サブエージェントは使用していない。

| 対象 | 整理内容 |
| --- | --- |
| Cursor Adapter | 実行手順から作業コピー・承認済み適用とrollback、認証情報、モデル・承認値の変換、安全上限を分離。ACPとexact CLIに重複していた作業場所の検証、実行依頼の複製、完了前検証、後始末エラーの報告を共通化 |
| Codex Adapter | 認証用Homeの作成、環境変数、ACL、古いHomeの回収を分離。プロセス開始・停止、cancel intent、成功確定後のcleanupは元の所有者に維持 |
| Antigravity Adapter | ローカルファイル操作と承認境界を専用ファイルへ分離。write/editの承認後の適用と完了通知を共通化 |
| AgentRuntimeManager | 起動時とQueue問い合わせ時の作業場所競合判定を共通化。承認待ち3項目の解除を一括更新に集約。イベント本文の上限制御を分離し、実行状態一覧を型定義から導出 |
| CoreServer | Queue復元、担当決定、Task実行、結果報告をtask_runtime.pyへ分離。WebSocket送信・snapshot境界と停止処理はCoreServer側に維持 |
| Conversation | データ型・保存形式の検証をtypes.py、永続化とturn操作をruntime.pyへ分離。終了時の実行ハンドル3項目の解除を共通化 |
| World | AgentTaskPanelからMarkdown表示・イベント表示・承認/質問/Plan入力・表示用判定を分離。通信解釈、Store、UIの実行状態一覧を共通化 |

新しい実装部品は、元のAdapter/CoreServerが持つ状態を利用する。別のQueue、所有権台帳、実行状態の写しは追加していない。既存の呼出先や異常注入用メソッドは保持した。

主要ファイルの行数は次のとおり。行数の減少は主に責務を別ファイルへ移した結果であり、同じ量の処理を削除したという意味ではない。

| ファイル | 変更前 | 変更後 |
| --- | ---: | ---: |
| core/agents/cursor_acp.py | 2,844 | 1,469 |
| core/agents/codex_app_server.py | 1,254 | 1,066 |
| core/agents/antigravity_agent.py | 1,343 | 993 |
| core/agents/manager.py | 1,517 | 1,404 |
| core/server.py | 6,358 | 5,271 |
| core/conversation/runtime.py | 784 | 577 |
| world/src/renderer/src/ui/AgentTaskPanel.tsx | 738 | 125 |

## 互換性の確認

- 移動したPython関数・メソッド125個は、変更前と構文木が一致。制御の順序や条件を変更せず移動したことを確認。
- 全テストファイルのbyte列が作業開始時と一致。
- 変更箇所の未定義グローバル参照なし。
- 安全上限、除外対象、権限判定、Providerコマンド、承認内容と適用内容の照合、rollback、キャンセル後の書き込み完了待ちを維持。
- 成功済み処理のcleanup異常と、成功確定前の失敗を分ける既存条件を維持。異なる安全上の意味を持つ分岐は、共通化のために統合していない。
- 外部Providerの実呼出しは行わず、既存の自動テストで検証。

## 最終検証

| コマンド | 結果 |
| --- | --- |
| npm run test:core | **617 passed / 0 failed**、111.30秒 |
| npm test | **39 files / 251 tests passed** |
| npm run typecheck | **成功** |
| npm run build | **成功** |
| git diff --check | **成功** |

変更前もCore 617件、World 251件が成功しており、最終の全件実行で回帰は検出されなかった。最終テスト開始後のコード変更は、ファイル末尾の余分な空行の除去のみ。

## 個別検証で見つかった既存の実行順依存

次の4ファイルだけをこの順序で実行すると、診断ログ取得テスト `test_codex_stderr_debug_log_is_bounded_and_drops_long_line_tail` が失敗する。

1. core/tests/test_agent_server_protocol.py
2. core/tests/test_server.py
3. core/tests/test_codex_agent_runtime.py
4. core/tests/test_cursor_agent_runtime.py

変更前コピーと変更後の両方で同じ **167 passed / 1 failed** を再現。該当テストの単独実行は成功し、標準の全Core実行は変更前後とも617件成功した。この実行順依存を今回の変更で隠すためのテスト修正や製品ログ処理の変更は行っていない。

## 設計判断の参照

[Python 3.12 asyncio](https://docs.python.org/3.12/library/asyncio-task.html)、[CPython task実装](https://github.com/python/cpython/blob/main/Lib/asyncio/tasks.py)、[dataclasses.replace](https://docs.python.org/3.12/library/dataclasses.html#dataclasses.replace)を確認。既存のtask/shieldによる所有権とキャンセル方式を維持し、値の複製に標準のdataclass機能を利用した。TaskGroup、新たな状態管理ライブラリ、新規retry機構への置換は、今回求められた挙動互換の範囲を超えるため採用していない。外部実装のコードはコピーしていない。

作業開始時のコピー、構造比較の詳細、コマンド出力はローカルの `.tools/refactor-20260910/` に保存。`verification.json`、`structure-result.json`、`exact-moves.json` を参照。
