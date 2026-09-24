# Nirai v2

v2の製品実装。現行仕様はリポジトリ直下の`Nirai_v2_基本設計書.md`、初期自走化中の実装順序は`Nirai_v2_初期自走化計画.md`を正本とする。

## 構成

- `src/hub/` — SQLite Store、共通Command、Capability Registry、Task Engine、utilityProcess入口
- `src/main/` — Electron Main、限定preload、Hub起動・終了、Dashboard IPC
- `src/renderer/` — prototypeを基準にした実Dashboard。Task状態は持たずHub Snapshotを表示
- `src/shared/` — 共通型と入力指紋
- `resources/` — v2同梱の画像・アイコン。v1の純粋資産から採用し、実行時にv1を参照しない
- `tests/` — Hubの恒久契約テスト
- `scripts/run-electron-smoke.mjs` — Main → Hubの実経路確認
- `scripts/run-ui-smoke.mjs` — Dashboard → IPC → Hub → SQLite → Dashboardの実経路確認
- `src/verification/` — 検証構成だけで登録する代替Capability。製品起動では登録しない

v1のCore / Workflow / Outboxをv2の実行正本として利用しない。

## 現在の到達範囲

Holo経路は **Task / Holo Turn / Action Run** で実装済み。

`Nirai入力 → Turn参照だけをChatGPTへ送信 → HoloがGetTaskContextを取得 → Nirai-MCPを使用 → 完了したassistant MessageをそのままNiraiへ反映 → 未完了ならResume`

Task完了は`CompleteTask`、Master待ちは`RequestMasterInput`を正本とする。Resume ONでは未完了Taskを次Turnへ進め、通常発言終了・25分・Timeout・Session Error・接続復旧を同じ継続判定で扱う。

Tool実行と副作用管理はAction Runに集約する。Conversation ID / URLはChatGPTの送信先ヒントとして扱う。

Hub Schemaは13。Unit 31件、Main smoke、UI smoke、Holo Web Adapter fixture smokeが通過済み。実ChatGPTとの一巡だけが未検証。

## 開発と検証

Windows上で`v2/`から実行する。

```powershell
Set-Location D:\Products\Nirai\v2
npm ci
npm test
npm run smoke
npm run smoke:ui
npm run smoke:holo
npm start
```

恒久テストは、Turn権限、assistant Message同文反映、Resume、Master待ち、Action副作用、Pause / Cancel / 再起動、MCP認証とCommand idempotencyを検証する。

`smoke:holo`はWeb Adapterのfixture検証。実ChatGPT接続は`docs/holo-setup.md`で確認する。

## M3の利用と制約

Holo用Local MCPは専用接続を使い、公開Toolは`nirai_command`一つに集約する。接続Secretは橋渡し内部だけで保持し、ChatGPTへ渡さない。

Holoへ公開する意味上の操作は`GetTaskContext / GetRunResult / InvokeCapability / RequestMasterInput / CompleteTask`だけ。

ローカルCapabilityはread / search / apply_patch / inspect / run_command / cancelを提供する。File変更とProcess実行はAction RunとしてScope、入力指紋、副作用、停止・cleanupをHubで管理する。

任意Shell、Scope外書込、依存導入、公開、破壊的Git等はPolicy Gateを迂回させない。結果不明のActionを自動再実行しない。

## 次の着手点と引き継ぎ

実ChatGPTで次を一巡確認する。

`Nirai送信 → Holo → Nirai-MCP Tool → GPT発言 → Niraiへ同文反映 → Resume → CompleteTask`

確認対象は、通常発言の同文反映、複数Turn継続、Master入力待ち、25分 / Timeout / Session Error後のResume。Provider側の一時的な表示や待機はWeb Adapterで観測し、Taskは未完了判定から継続する。
