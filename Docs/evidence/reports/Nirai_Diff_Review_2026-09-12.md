Niraiの未コミット差分をHEAD `9167663`と比較し、DNA関連を除いてレビューした。開始時点は追跡済み52ファイルと未追跡5ファイル。設計意図を維持する局所修正として、再現できた11件を修正した。

確認対象はAgent実行・途中成果保存、Residentの役割、利用枠の取得と割当、統合監査の入口、Holoの所有Conversationと継続通知、Worldの通信・状態表示、および対応する設計差分。変更前のファイルとSHA-256は `.tools/diff_review_20260912/before/` と `baseline.json` に保存した。

| ID | 重要度 | 再現条件と影響 | 修正と現在の位置 |
|---|---|---|---|
| R01 | P1 | 利用制限で止まったCursorの途中成果保存を一時停止し、その間にキャンセルする。保存を続ける処理より先に作業用コピーが削除され、途中成果が失われる。停止を繰り返しても再現した。 | 保存処理が終了するまでキャンセルの確定と後片付けを待つ共通処理をCursor/Codexへ適用。保存ファイルも既存のディスク書き出し確認付きコピーで扱う。[cursor_workspace.py:622](D:/Products/Nirai/core/agents/cursor_workspace.py:622) |
| R02 | P2 | Cursorの終了コードが0でも、JSONが `is_error=true` と利用制限エラーを返す場合、通常失敗扱いになり途中成果が残らない。 | 終了コード経由とJSON経由の両方を同じ利用制限の終了処理へ接続。[cursor_acp.py:840](D:/Products/Nirai/core/agents/cursor_acp.py:840) |
| R03 | P2 | Codexの通信エラーが利用制限を示す `code` と一般的な `message` を返す場合、文字列化で分類情報を失い、途中成果を保存せず通常失敗になる。 | 通信層で構造化されたエラーを分類し、通常のTurn失敗と同じ保存・終了処理へ接続。[codex_app_server.py:51](D:/Products/Nirai/core/agents/codex_app_server.py:51) |
| R04 | P2 | Codexの利用枠応答が空、または両方の枠がnullの場合にも `available` と表示・判断する。 | 利用率も明示的な制限情報も無い応答は取得失敗として扱い、既存のUNKNOWN／古い値の保持へ接続。[usage_budget.py:187](D:/Products/Nirai/core/usage_budget.py:187) |
| R05 | P2 | Codexがprimaryの制限到達を明示しても、その枠の詳細が欠け、secondaryだけ返る場合、実行可能と判定する。 | Provider全体の明示的な制限状態を、残っている別枠の情報で打ち消さない。[usage_budget.py:273](D:/Products/Nirai/core/usage_budget.py:273) |
| R06 | P2 | Cursor Models枠のみ100%と返り、Other Models枠が欠けた場合、Other ModelsやAutoまで利用不可にする。 | 別枠の値を代用しない。月次の共通枠だけを互換経路として扱い、対象モデルの枠が不明ならResident表示もUNKNOWNにする。[usage_budget.py:261](D:/Products/Nirai/core/usage_budget.py:261)、[server.py:4516](D:/Products/Nirai/core/server.py:4516) |
| R07 | P2 | Worldから利用枠の手動更新を送り、取得を一時停止すると、その後の一覧取得など同じ接続の操作を処理できない。 | 既存のバックグラウンド更新処理へ渡し、Worldのメッセージ受付を継続する。[server.py:3944](D:/Products/Nirai/core/server.py:3944) |
| R08 | P2 | 利用枠Providerが返答しないと取得が待ち続ける。別の再現ではCore停止後も取得Taskが残った。 | 取得待ちに15秒の上限を設定し、タイムアウトはUNKNOWNへ縮退。Core停止では取得処理と定期更新の終了を待ち、新たな更新予約も止める。[usage_budget.py:133](D:/Products/Nirai/core/usage_budget.py:133)、[server.py:2989](D:/Products/Nirai/core/server.py:2989) |
| R09 | P2 | 指揮者がいる状態で新しい指揮者を作成すると、保存上は旧指揮者が降格するが、画面には新Residentの情報しか届かず、旧役割が残る。 | 指揮者作成時にはResident一覧全体を通知し、新旧両方を更新する。[server.py:4292](D:/Products/Nirai/core/server.py:4292) |
| R10 | P2 | 旧Diveの完了通知を整理する際、処理済み記録の保存が失敗しても `accepted=true` を返す。Core／Rendererが再送を止めてしまう。 | 保存に成功したときだけ受理を返す。失敗時はメモリ上の処理済み扱いも戻し、再送可能にする。[HoloWebHost.ts:363](D:/Products/Nirai/world/src/main/holo/HoloWebHost.ts:363) |
| R11 | P2 | 統合監査の開始前に利用枠取得が10.2秒かかると、Local Clientの10秒制限でTask IDの受領前に失敗する。 | 監査開始の返答待ちを60秒にし、利用枠の取得上限と終了処理の時間を確保する。[holo-local-client.mjs:355](D:/Products/Nirai/tools/holo-local-client.mjs:355) |

リファクタリングでは、Cursor/Codexに共通する途中成果の保存待ちを1か所へまとめ、Codexの2種類の利用制限エラー処理、およびCursor CLIの2種類の失敗通知の重複を整理した。通常成功、キャンセル、書き込み確定後のキャンセルの既存の意味は維持している。

修正前の再現結果は、Coreの最初の検査で8 failed / 1 passed、利用枠の終了処理で2 failed、Codex通信エラーで1 failed / 1 passed、監査開始のLocal Client試験で1 failed。Holo保存失敗でも `accepted=true` の誤った応答を確認した。これらは不具合を確認するための修正前の結果である。

再発防止テストは各機能の既存テストファイルへ追加した。保存中の停止は実際の別スレッドを同期して検査し、World操作と指揮者通知はローカルWebSocketを使用した。Codexは偽のローカルapp-server、Cursorは偽のCLI結果を使用して、実ファイルの保存・後片付けまで確認している。監査開始は既存Local Clientの一連の試験に遅い利用枠応答を追加した。

検証結果：

- Core全体：最終状態で669 passed / 1 skipped。最終テスト入力として保存した57ファイルのSHA-256と、引継ぎ後の現行ファイルが57/57一致することを再確認した。
- World全体：引継ぎ後の現行状態で39ファイル、278 passed。
- `npm run typecheck`：引継ぎ後の現行状態で成功。
- `npm run build`：引継ぎ後の現行状態で成功。
- `git diff --check`：引継ぎ後の現行状態で成功。

実アカウントの利用枠Smokeは明示実行用テストのためskip。ChatGPT実画面のDOM操作、実Providerでの長時間作業、電源断は今回の検証範囲に含めていない。

既存のユーザー差分を保持して作業ツリーに修正を重ねた。DNAファイルへの編集、commit、pushは実施していない。引継ぎ後はAstraが開始時点から変更した14ファイルを変更前スナップショットと突き合わせてFresh Reviewし、周辺実装も確認した。追加修正を要する確定不具合は見つからず、今回のDNA除外レビューはSAFEと判定する。
