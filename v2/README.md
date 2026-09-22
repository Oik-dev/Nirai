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

**M1・M2は、計画で定めた代替Capabilityによる出口を確認済み。次はM3。実Holoによる作業実行はまだ未接続。** 次表は現状案内であり、仕様・合格条件は基本設計と初期計画を参照する。変更時は表を置換更新し、過去の件数や作業履歴を蓄積しない。

| 段階 | 実装・確認できる範囲 | 出口までの残作業 |
|---|---|---|
| M1 | 実HubへRegistry / Engineを接続。共通handler、開始予約・再評価、応答別Context範囲、子Run、固定した承認対象、資源と停止確認、構造化した完了条件と実検証・内容指紋の照合、応答自身と結果Messageの同時完了、成果物所有区分、設定の版とRunへの固定を確認。SQLite Schema 5、Migration前Backup、排他、破損DB拒否、Paused復元、Main通信切断時の停止保存 | Hub内のAC01・AC04・AC05・AC07・AC08を代替Capabilityで確認済み。外部認証・実配送・実ファイル変更とProcess照合はM3以降 |
| M2 | 実Electron画面から質問回答→具体的承認→子Run結果→Task完了と結果Messageまで確認。新規Task、指示、Pause / 再開、Resume設定、早すぎる完了の拒否、取消、Archive、縦横配置、二重クリック、Reload、CHECK中Chat、切断後の受付照合・状態再取得 | AC01・AC09のM2範囲を確認済み。通常起動は未接続表示。実ChatGPTのWeb操作はM4以降 |
| M3〜M8 | 未実装 | 初期計画の順でv2 Control API・Policy / Tool・Holo実接続・Auto Resume・統合確認・実行版切替を実装する |
| 初期自走化後 | 未実装 | 基本設計§27の依存順と出口に従う |

応答の入口は`HubService.handleResponseCommand`。M1では登録Adapterへ渡したRun限定の関数から呼び、M3・M4で認証した外部呼出元も同じhandlerへ接続する。保存済みCommandは同じ結果を返し、新規操作は現在のTask・応答Run・世代・期限・実際に渡した指示範囲を検査する。Storeの内部メソッドを外部APIとして公開しない。

ローカルTool、Holo Web表示、Local MCP橋渡しは未実装。通常の`npm start`では外部Capabilityを登録しないため、指示の保存はできてもHolo応答は始まらない。代替CapabilityはUI smokeの隔離構成だけで動き、画面に「検証構成」「検証用Capability」と表示する。ResumeのHub内判定が通ることも、M6の実Holo Auto Resumeの成立を意味しない。

## 開発と検証

Windows、開発用Node 24系とnpmを使う。製品HubはElectron同梱Nodeで動かす。リポジトリ直下ではなく`v2/`で次を実行する。

```powershell
Set-Location D:\Products\Nirai\v2  # 実際のcheckout先に合わせる
npm ci
npm test
npm run smoke
npm run smoke:ui
npm start
```

依存はこのFolderのpackage / lockfileを使用する。`world/node_modules`やグローバルのTypeScriptに依存させない。Electron本体が未配置なら`npm ci`は大容量ダウンロードを伴うため、Masterの承認条件を確認してから行う。検証環境ではlockfileと一致する既存Electron本体をローカルで再利用してよいが、実行経路にv1へのfallbackを追加しない。

`start`とsmokeは最初にbuildする。smokeは専用の一時Data RootとElectron userDataを使い、`NIRAI_V2_DATA_ROOT`に製品領域が指定されていても利用しない。終了後に一時領域を片付ける。通常起動の保存先は基本設計§3.3。任意の検証版を製品Data Rootで試さない。

| 検証 | 確認範囲 |
|---|---|
| `npm test` | build＋23件のHub契約・保存・起動・実行フロー。応答権限、指示範囲、実結果と版の照合、停止資源、保存済み呼び起こし、設定固定、再送と再起動を含む |
| `npm run smoke` | 固定版ElectronでMain→Hub→SQLiteのTask作成、Snapshot、終了 |
| `npm run smoke:ui` | 実Dashboardの画像読込、二重クリック、質問と承認、子Run・Task結果、CHECK中Chat、Reload、Paused中回答、Pause / 再開、Resume設定、完了拒否、取消、Archive、縦画面、実MessagePort切断→Hub停止保存・再起動→受付照合 |

上記3コマンドは現作業版で通過確認済み（Schema 5 / Electron 41.10.6）。検証資料は`.tools/m1-m2-evidence/verification.json`のSource指紋と、同Folderの縦横画面を参照する。`.tools`はGit対象外なので、資料がないcheckoutでは上記コマンドを実行して現在の版を確認する。

画面保存が必要なら`NIRAI_V2_UI_CAPTURE_DIR`へ検証資料用Folderを指定して`npm run smoke:ui`を実行する。この場合だけ撮影のため検証Windowを表示する。制限環境ではElectronのGPU子Processが起動できない場合があるため、実Windows環境で確認する。

実Holo / ChatGPT、ローカル書込、外部Processと子孫の停止、Main強制終了・PC再起動時の外部副作用照合、候補版切替は未検証。Main通信切断の確認と強制終了を同一視せず、AC06・AC10〜AC12やM3以降の合格に読み替えない。

## 次の着手点と引き継ぎ

1. M3: `runtime.ts`の既存排他pipeを認証Transportへ発展させ、認証・応答Token検査後に`service.ts`へ渡す。第二のStoreや別handlerを作らない。
2. M3: `capability.ts` / `engine.ts`へ最小File・Process能力と共通Policyを登録する。実ファイルの内容照合、変更退避、PID以外も含むProcess識別、停止と復旧の実証を追加する。M4では実Holoからv2用Toolに到達することを先に証明する。
3. 各出口は変更対象の版、実行コマンド、代替環境か実環境か、未検証範囲を対応させて報告する。通過した部分だけを更新し、次に必要なファイルと具体的な未成立条件を残す。

M8完了後も§27の必須要件が残る。初期計画の退役は全参照を更新してから行う。
