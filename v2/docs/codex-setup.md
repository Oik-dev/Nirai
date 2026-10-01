# Codex CLI接続

## 設定

1. Codex CLI 0.159.2以降で`codex login`を実行し、ChatGPTでログインする。NiraiにAPIキーの入力・保存機能はない。古いCLIで安全設定を確認できない場合は生成を開始しない。
2. NiraiのResident設定で「＋ Resident」を開き、名前と「AIの接続：Codex CLI」を選ぶ。
3. 「接続を確認」を押す。これはログイン状態とModel一覧だけを取得し、回答を生成しない。
4. Modelを選んで追加する。未指定の場合はCLIが返す既定Modelを使う。既存Residentも接続・Modelを変更して保存できる。
5. ChatModeではSay / 追加したResidentへのWhisper、TaskModeではそのResidentを選んで新しいTaskへ指示を送る。

通常はPATHまたはCodexデスクトップ同梱の`codex.exe`を検出する。別のインストールを使う場合は、起動前に`NIRAI_V2_CODEX_COMMAND`へローカルの実行ファイルの絶対パスを指定する。CLIを自動ダウンロードしない。

## 実行の境界

通常会話はそのResidentが直接受信したSay / 本人Whisperの限定履歴とPersonaを使う。本人が聞いていないWhisperやTask本文を通常会話へ混ぜない。履歴はHubに保存し、Codex側では一時的な生成用会話を使う。

Taskのファイル操作・検証はNiraiの`nirai_command`を通す。CLIの直接Shell・外部MCP・アプリ・Hook・ローカル実行環境はこの接続内で無効にする。ユーザーのCLI設定ファイルを書き換えない。承認が必要なActionはNiraiの承認待ちへ戻る。ローカル操作にはTaskの作業範囲が必要で、Resident設定で選ぶ作業フォルダーを新規Taskへ固定する。

Codexに自動Resumeはない。未完了の返答後は新しい指示、承認への回答、実行中Actionの完了結果、またはTaskの明示的な再開で続ける。Pause / Cancel / 終了 / Timeout / 接続断後に同じ入力を無断再送せず、停止後の遅い返答をTaskへ保存しない。接続済み表示やModel一覧だけでは、選んだModelでの実応答成功を示さない。

CLI Homeにあるユーザー共通の`AGENTS.md`は引き継ぐ。Taskの作業フォルダーをCLIの実行場所にせず、Niraiが固定した作業範囲の操作だけをHubへ要求する。

## 検証

恒久テストは模擬Providerで本文同文、本人受信履歴、旧Turn拒否、承認、完了予定と本文保存、停止・再起動の境界を検証する。UI smokeはResident追加・接続先・Model保存と狭幅の配置を確認し、外部AIへ生成要求を送らない。

`npm run check:codex`は既存ChatGPTログインとModel一覧だけを確認する。実生成を含める場合は、利用枠の消費を承認したうえでPowerShellで`$env:NIRAI_V2_CODEX_LIVE = '1'`を指定して実行する。Say / Whisper各1回と専用ファイルを読むTask1回を上限とし、失敗しても追加生成しない。Taskだけの再確認は`node scripts/run-codex-smoke.mjs --task-only`で1回までに制限する。

2026-10-02、CLI 0.159.2でChatGPTログインとModel一覧9件、実Say / Whisperの指定本文と保存先を確認済み。Taskは実返答を取得したが、当初はNirai Tool呼び出しに必要な内部hostまで停止していたため完了に達しなかった。Task時だけdynamic Tool transport用hostを有効にし、直接Shell・File操作等を無効のまま保つ修正後、恒久テスト168件・UI smoke・Holo fixture smokeは成功済み。Codex利用枠の都合により、実Taskの`nirai_command` → `local.read` → `CompleteTask`完走だけは未再検証。模擬テストの成功を実Task完了の証拠にしない。
