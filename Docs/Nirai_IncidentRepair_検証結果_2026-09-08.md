# Nirai Incident Repair / Dive Health Check 検証結果 — 2026-09-08

## 判定

**SAFE — 日常運用の軽量Repair基盤として採用。フルself-buildはM5+の将来機能として維持する。**

目的は、Nirai運用中の異常をすべてResident自身へ自己改修させることではない。既存Recoveryで自動復旧できるものはCoreが処理し、コード修正が必要な異常だけを次回Holo Diveで自動検出・修復Context化し、HoloがLocal MCPで直す。

Repository複製、自己適用、自己再起動、世代Backupを常時持つself-buildは導入していない。

## 実装した契約

### 1. Incident Ledger

- `core/incidents.py`
- 永続先は`runtime/incidents.sqlite3`の1 File
- Coreの`ERROR`級Logging Recordを`IncidentLogHandler`で自動記録
- SQLiteは`WAL` + bounded busy timeoutを使い、Health読取とERROR書込の競合を減らす
- 一時的なSQLite lock等でERRORを保存できない場合は、`runtime/incidents-fallback.jsonl`という単一bounded journalへfsync退避する
- fallback journalは次回Dive Healthで最大32件ずつSQLiteへ再取込し、残りがあれば`health.status=attention`を維持する
- 同じ`component + code + error_type`に加え、provider / operation / scope等の安定した故障軸はfingerprintへ含める。Session ID / PID等の揮発IDは含めない
- resolve前の同一Incidentは再発しても最高severityを保持し、resolve後の再発は新しいseverityでreopenする
- resolved履歴は最新100件だけ保持
- Incident Store障害やfallback処理障害はCore起動をBlockingしない
- Snapshotには概要だけを出し、stack/detailはHoloの明示`incidents`取得時だけ返す

### 2. 長期整合性Failureの明示昇格

Memory Outbox replay失敗は通常Log上WARNだが、Chat正本とMemory派生の不一致が残るためIncidentへ明示昇格する。

- 同一Incidentへ集約
- 起動時は従来どおりOutbox全収束を試す
- Dive Health Checkでは最大1 batchだけ再試行してInteractive attachを長時間Blockingしない
- Outboxが0へ収束した場合は該当Incidentを自動resolve

### 3. Dive Health Check

既存Holo Local Clientの通常Dive手順を変えず、`attach`と`snapshot`に`health`を追加した。

Healthは次を返す。

- `status = ok | attention`
- Incident Store利用可否
- Incident fallback残留有無
- 未解決Incident数と最新5件の概要
- Memory Outbox pending数
- Interrupted Agent Session数
- enabled Residentの設定破損
- 現在有効なResidentが依存するBrain Runtime状態
- 利用不能なrequired Provider一覧

Provider Checkは軽量なローカル検査だけを行う。

- Cursor: CLI Runtime解決
- Codex: CLI Runtime解決
- Gemini: `world/.env`の`GEMINI_API_KEY`を実Runtimeと同じparserで確認し、引用符を外した後に空ならUnavailable扱い
- Claude: Current acceptanceで無効
- ネットワークProbe、実AI Call、課金Callは行わない
- 未使用Providerの欠損はHealthをattentionにしない

### 4. Holo Repair Context

Local Clientへ追加。

- `incidents [limit]`
  - 未解決Incidentの詳細取得。最大50、通常20
- `incident-resolve <incident_id> [note]`
  - HoloがLocal MCP修正、回帰、必要なReviewer確認を終えた後に明示resolve

標準運用は以下。

```text
自動Recovery
  ↓ 未解決
Incidentへ集約
  ↓
次回Holo Dive attach / snapshotでHealth Check
  ↓ attention
Holoがincidents取得
  ↓
Local MCPで実装修正
  ↓
Cursor等のread-only Review + 回帰
  ↓
incident-resolve
```

これはself-buildではない。通常Resident / Agent RuntimeのNirai `core/` / `world/` write禁止は維持する。

## Codex Desktop Runtime検出修正

Doctorと製品RuntimeのCodex探索を`core/provider_runtime.py`へ共通化した。

探索順：

1. PATH上の`codex.exe / codex.cmd / codex`
2. `%LOCALAPPDATA%/OpenAI/Codex/bin/<managed-runtime>/codex.exe`

npm `.cmd`の場合は従来どおり`node + @openai/codex/bin/codex.js`まで実体解決する。

本PCの実機Doctorでは次を確認した。

- Cursor: OK
- Codex: `C:\Users\weize\AppData\Local\OpenAI\Codex\bin\8e5b6932251c2c1c\codex.exe`を検出してOK
- Gemini: OK
- Claude: Current acceptanceでWARN
- `fatal=0 / warnings=1`

## 回帰Test

新規/更新回帰では以下を固定した。

- Incidentのdedupe / resolve / reopen
- ERROR Logの同一Incident集約
- Dive Healthが未解決Incidentを自動表示
- `incidents`でdetail取得
- `incident-resolve`後にHealthが`ok`へ戻る
- required Provider Runtime欠損をDive Healthが`attention`として検出
- Holo Local Client E2Eでattach Health → incidents → resolve → snapshot healthyを確認
- Codex Desktop managed RuntimeをDoctorが検出
- Dive HealthのMemory replayは1 batch / 32件へbounded
- Incident fallback replayも1 checkpoint最大32件へbounded
- Incident DB lock中のERRORはbounded時間でfallback journalへ退避し、後続replayでSQLiteへ収束
- enabled Residentのconfig破損をHealthが`attention`として検出
- `GEMINI_API_KEY=""`をHealth / Product RuntimeともUnavailable判定

## 追加敵対レビュー修正 — 2026-09-08

Incident Repair導入後の追加敵対レビューで、6件を再現して修正した。いずれも回帰Testを追加し、修正前に欠陥成立を確認してから正方向へ反転させた。

### F01 — Holo Conversationが`running`で永久停止

Resident `talk`で公開Chat Sessionが存在しない場合、従来は`start_turn`後に例外となり、実行Taskなしの`running`が残った。公開Chat Session検証を`start_turn`前へ移し、失敗時はConversation stateを変更しない。

### F02 — Codex並行prepareが別Sessionの認証Homeを削除

CodexにもCursor同等のruntime ownershipを追加し、prepare前からfinal cleanup完了までSession IDをclaimする。stale cleanupはowned Homeを除外し、別Core Process由来の若いHomeも6時間未満なら削除しない。prepare途中Failureでもclaimを必ずreleaseする。

### F03 — Cursor不完全rollbackのRecovery export失敗で原本消失

この時点ではRollback backupを直接`move`せず、原本を保持したまま外部Recoveryへ公開する方式へ変更した。その後のFollowup Review R01で「原本自体がCursor Home配下なら外側cleanupに消される」ことを再現したため、Currentでは**最初の実Workspace writeより前から`runtime/cursor_recovery/.RB-*`へ原本を作る方式**へさらに更新した。Recovery Root作成不能時はWorkspaceを変更せずfail-fastし、publish失敗時も`.RB-*`はHome cleanupから独立して残る。

### F04 — 空の引用付きGemini KeyをRuntimeだけAvailable判定

`GEMINI_API_KEY=""` / `''`は引用符除去後に空なら`None`へ正規化する。Doctor、Dive Health、Product RuntimeのAvailability判定を一致させた。

### F05 — Codex Home cleanupがasyncio event loopをBlocking

Codex isolated Homeのprepare / cleanup / Conversation credential cleanupを`asyncio.to_thread`へ移した。Provider Conversation context破棄もoff-loop化し、同じConversationの次Turnだけは前回cleanup完了をawaitして因果順序を維持する。Core全体のevent loopはFilesystem retry中も継続する。

### F06 — Incident SQLite競合でERRORが消える

SQLiteをWAL化し、短いbusy timeout後も保存できないERRORは単一bounded fallback journalへfsync保存する。次回Diveで最大32件ずつSQLiteへreplayし、未処理が残る間はHealthを`attention`とする。診断機構がCoreを長時間Blockingせず、ERRORもsilent lossしない契約へ変更した。

追加監査では、Memory Outboxの派生`scope / resident / payload`破損も確認した。Outbox値を正本として信用せず、indexed Chat entryから再導出・自己修復する。Private WhisperのOutbox scopeだけを`world`へ改変してもPublic Memoryへ昇格できない。Outbox JSONのみの破損はindexed Chatから修復してreplayし、indexed Chat側まで破損して自動修復不能な場合だけCoreを継続させたままIncidentへ昇格する。

### Followup Reviewによる追加Hardening

`Docs/Adversarial_Review_Followup_2026-09-08.md`のR01-R06で、外側finally / cancellation / deleted-unsynced source / truncated fallbackまで再検証した。発見用8 Probeは修正前8/8 PASSから修正後8/8 FAILへ反転し、Current契約を正方向Test 8件で固定した。

- Cursor Recovery原本はCursor Home外の`runtime/cursor_recovery/.RB-*`へ最初から作成
- approved apply中のcancelはapply / rollback確定までResourceとownershipを保持
- 通常Chat削除時、未同期Memory Outboxが必要とするindexed sourceだけ同期完了まで一時保持
- Codex / Cursorの`to_thread` prepare cancelはworker終了とlate resource cleanupまで待ってからownership release
- Incident fallbackのtruncated / invalid recordは単一bounded quarantineへ隔離し、corruption Incidentを残しながら正常後続をreplay

## 最終実測

2026-09-08 Followup Review修正後：

- Core pytest: **517 passed**
  - 90秒Tool上限回避のため全Test Fileを2分割し、**315 passed + 202 passed / Failure 0**
- World Vitest: **39 files / 245 tests passed**
- TypeScript typecheck: **成功**
- Electron production build: **成功**
- Doctor: **fatal=0 / warnings=1**
  - Cursor: OK
  - Codex: OK
  - Gemini: OK
  - Claude: Current acceptanceで無効のためWARN
- `git diff --check`: **成功**
  - stderrは既存working-copyのLF→CRLF warningのみ

## 残る境界

- フルself-buildは未実装。通常Resident / Agent RuntimeからNirai本体を書き換えない
- Holo不在中は、Coreが自動RecoveryできないコードBugを勝手に修正しない。Incidentを残し、次回DiveでHoloへ渡す
- Incidentは修復Contextであり、ERRORが必ずコードBugであることを意味しない。HoloがFindingを確認してから修正/resolveする
- 実Providerのネットワーク状態やQuotaはDive Healthの軽量検査対象外。実Call失敗がERROR等へ到達した場合はIncidentとして後続修理対象になる
