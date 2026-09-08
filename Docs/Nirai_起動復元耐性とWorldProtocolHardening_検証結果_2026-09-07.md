# Nirai 起動復元耐性とWorld Protocol Hardening 検証結果（2026-09-07）

## 判定

**SAFE相当**。

PC復元後にCドライブ側Python PackageとCursor CLIが失われ、Niraiが起動しない実障害を起点として、DNA → UE4.27 World Replacement前に起動基盤とCore / World接続境界をHardeningした。

今回の目的は機能追加ではなく、次を成立させることである。

- Cドライブ側のglobal Python Package状態をNiraiの暗黙前提にしない
- 起動前の必須依存欠損を無言終了ではなく診断可能にする
- Cursor / Codex / Claude / Gemini等のoptional Provider欠損でCore全体を起動不能にしない
- 二つ目のWorld Runtimeを追加する前にCore / World Protocolの互換世代を明示する
- 古い / 非互換Worldを「たぶん動く」として接続しない

## 1. Project-local Python Runtime

Rootへ`.venv`を採用し、Python PackageをNirai Project側へ隔離した。

- `.venv/`はGit管理外
- `Setup Nirai Runtime.cmd`がPython 3.12.xから`.venv`を作成し、`requirements.txt`の固定依存を導入する
- `npm run test:core`も`.venv\\Scripts\\python.exe`を使用する
- 通常起動`Start Nirai.vbs`と開発起動`Start Nirai.cmd`もProject Runtimeを使用する

`.venv`はPython本体そのものを同梱するPortable Runtimeではない。Windows側のPython 3.12本体まで失われた場合はPython 3.12を再導入して`Setup Nirai Runtime.cmd`を実行する必要がある。一方、今回失われた`websockets` / `sqlite-vec`等のPackageはglobal site-packagesへ依存せず、Project Runtimeへ固定される。

## 2. stdlib-only Startup Preflight / Doctor

Rootへ`nirai_bootstrap.py`を追加した。Core Moduleをimportする前にPython標準Libraryだけで次を確認する。

- 実行PythonがRoot `.venv`配下であること
- Python 3.12.x
- `websockets`
- `sqlite-vec`
- `config.toml`の存在・TOML構文・現行Contract（型・範囲・Memory budget整合を含む）
- Electron Runtime
- 通常起動ではProduction World Build
- 開発起動ではelectron-vite Tooling

必須Check失敗時はCoreへ入らず、`runtime/logs/startup-preflight.log`へ理由を記録する。`pythonw`通常起動でも無言終了させず、起動失敗を表示できる構成にした。

`Nirai Doctor.cmd`は同じCheckを起動せず実行する。

Provider Checkは診断専用でありfatalにしない。Cursor / Codex / Claude CLIやGemini credentialが未導入・未認証・利用不能でもCore / World自体は起動可能とする。Provider Adapter側も実使用時まで遅延初期化する設計を維持する。

2026-09-08の追補監査でDoctorのProvider判定も実Provider実装と再照合した。GeminiはCoreと同じ`world/.env`を正として検出し、Cursorは現行CLIを検出、Codexは未検出、Claudeは現行受入上disabledと明示する。復元後PCで必須Check全OK、Gemini / Cursor OK、Codex / Claude WARN、`fatal=0`を確認した。

## 3. Core / World Protocol v1 handshake

Core / World `hello`へ次のProtocol descriptorを必須化した。

```json
{
  "version": 1,
  "runtime_id": "electron-threejs",
  "capabilities": [
    "semantic-actions-v1",
    "conversation-ui-v1",
    "agent-runtime-ui-v1",
    "resident-roster-v1",
    "holo-addon-v1"
  ]
}
```

Core側`hello_ack`も`runtime_id=nirai-core`のdescriptorを返す。

安全順序は次とする。

1. World Secret認証
2. Protocol descriptor検証
3. Version一致確認
4. 正規Worldとして登録
5. `hello_ack` / Snapshot送信

Protocol descriptor欠落・型不正・Version不一致はclose code 4004で登録前に拒否する。Secret不一致は従来どおり4003で拒否し、未認証相手へProtocol判断材料を先に返さない。

標準World側も`hello_ack.protocol.version`がWorld自身のVersionと一致するまで`connected`へ遷移しない。

`runtime_id` / `capabilities`はWorld実装識別と将来Feature negotiationのための情報であり、Resident IdentityやThree.js固有座標等をCore Contractへ持ち込まない。Current v1ではCapability不足だけを理由に接続拒否せず、UE4.27最小Spikeで実装可能範囲を明示するための境界として利用する。

## 4. 回帰Test

追加した主な回帰：

- optional Provider未検出でもStartup Preflightがfatalにならない
- runtime Python Package欠損はPreflightでfail-closedする
- Production Build欠損は通常起動だけfatalになり、明示Dev Toolingがある開発起動とは分離される
- `hello_ack`がProtocol version / runtime metadataを返す
- World helloのProtocol descriptor欠落を拒否する
- 非互換Protocol versionを拒否する
- World側も非互換Core `hello_ack`をconnected扱いしない
- Runtime descriptorの型不正 / duplicate capabilityを拒否する
- 60秒以上安定稼働したWorldの後発Crashは過去の失敗回数を引きずらず、failure streakをリセットする
- Worldが短時間に連続5回失敗した場合はWorldだけを捨ててCoreを残さず、Nirai全体を終了する
- Geminiのclose-delimited HTTP ResponseをEOFまで読み、packet分割でJSONを切断しない
- DoctorのGemini / Claude判定が実Provider availabilityと矛盾しない
- native `codex.exe`だけのInstallもCodex Providerが検出できる

2026-09-08追補監査では、機能Testが通るだけでなくFailure Path / 診断導線の合理性も再確認し、次も修正した。

- `Start Nirai.vbs`の`D:\\Products\\Nirai`固定を廃止し、Shortcut Script自身の親DirectoryからRootを解決
- Runtime再構成を`venv --clear`にし、古い余剰Packageを残す半再構築を廃止
- Production Worldのstdout / stderr破棄をやめ、`runtime/logs/world-process-YYYYMMDD.log`へ起動Crashの診断情報を残す
- Core / World Protocol不一致をStore内部だけでなくWorld UI上にも明示する

最終実測：

- Core pytest：**446 passed**
- World Vitest：**39 files / 243 tests passed**
- TypeScript typecheck：成功
- Production Build：成功
- Project Runtime Doctor：必須Check全OK / fatal 0
- `git diff --check`：成功（既存LF→CRLF warningのみ）

## 5. World Replacementへの意味

DNA → UE4.27 Spikeでは、UE RuntimeをCore本体へ埋め込まず既存`WorldRuntimeLauncher` Contractの別実装として扱う。

最小接続条件は次とする。

- Core起動Secretを受け取る
- Protocol v1 `hello`を送る
- 独自`runtime_id`と実装済みcapabilitiesを広告する
- `hello_ack` v1を確認する
- 1 Residentの意味ActionをWorld表現へ変換する

これによりThree.js標準WorldとUE Private World Addonが並存しても、Core / Resident Identity / Memory / Conversation / TaskをWorld実装ごとForkしない。

## 6. Remaining

- Python本体までDへPortable同梱する方式は採用していない。Python 3.12本体を失った場合は再導入が必要
- optional Providerの個別CLI / credential復旧は、そのProviderを使用する時のAvailability問題でありCore Startup blockerではない
- Codex通常Resident Live continuationの再確認はProvider利用枠回復後の外部条件として残る
- Public / Private Gemini Vectorの長時間quota観測、M3 World Observation / Natural Idle / Brain生活ティックは既存RoadmapどおりWorld Replacement後の工程

今回もcommit / pushは行っていない。
