# Holo / Resident / Provider 共通 Conversation Runtime 検証結果

**Status: 自動検証 SAFE / 実Provider Live Smoke待ち（2026-09-07 adversarial review follow-up済み）**

## 目的

Holo Supervisor Review専用の待機経路だけを増やさず、Holoが同一ChatGPT Turnの中でResidentや外部Providerと複数Turnの会話を継続できる共通基盤を成立させる。

対象用途：

- Holo ↔ Residentの雑談
- Holo ↔ Cursor / Codexの仕様相談
- Holo ↔ Cursor / CodexのBrainstorm
- Holo → Cursor / Codexのread-only Review
- 将来の複数AI会議・Task Workflowが利用できる共通Turn / Wait境界

File変更を伴う実作業は本Sliceへ含めない。既存M4 Task / Agent Runtime + Master Approvalを正本とする。

## 実装契約

### Conversationの正本

`runtime/conversations/<conversation_id>.json`（状態 + 最大100件hot tail）

`runtime/conversations/<conversation_id>.jsonl`（append-only full transcript journal）

Nirai側に以下を保持する。

- `conversation_id`
- participant kind / participant
- mode
- open / closed
- current turn state
- 最大100件の操作用Conversation transcript hot tail
- 全Messageのappend-only recovery journal
- Provider native session / thread ID
- active / last turn ID
- active / last Agent Session ID
- target / model / reasoning effort
- last error
- Review時のstructured verdict

Recordは2 MiB上限とし、temp writeをflush / fsyncしてからreplaceする。Messageはrecord更新前にappend-only journalへflush / fsyncする。Record save前Crashでjournalに孤立したseqが残っても、recordの`next_message_seq`未満だけをcommit済みとして復旧し、同じseqの再試行はsequence mapで一意化する。Core再起動時に`running`だったTurnだけを`interrupted`へ畳む。Resident Conversationは完了済みTranscriptとidentityを維持する。Provider Conversationはin-flight中にremote側がどこまで入力を消費したか不明なため、`interrupted`化と同時にnative Provider IDを無効化し、次Turnをjournalから再構築する。Provider会話の長期正本を100件hot tailだけへ依存させない。

### 共通Holo Local Client操作

```text
conversation-start <resident|provider> <participant> <talk|brainstorm|consult|review> [target] [model] [reasoning]
conversation-send <conversation_id> <text>
conversation-wait <conversation_id> <0..15秒>
conversation-cancel <conversation_id>
conversation-close <conversation_id>
```

旧`review / review-wait / review-cancel`は後方互換用に維持する。新規実装の正本はConversation Runtimeとする。

### Wait

`conversation-wait`は固定Sleepではない。Core内のEventをwake-up hintにするが、Event自体をterminal証拠にはしない。wakeのたびに永続Conversation stateを再読込し、前Turnの遅延Eventが次Turnへ混入しても誤完了しない。

1回のwaitは最大15秒。未完了なら`timed_out=true`で返し、Holoは同一ChatGPT Turnから追加waitできる。

### Resident Turn

Resident Brain Driverを1 Turnずつ呼ぶ。Nirai Conversation transcriptを相手との短期履歴としてBrain Contextへ再投入する。`talk`ではHolo発言とResident返答をWorld公開会話へも反映するが、Conversation Runtime側を処理状態の正本とする。

### Provider Turn

Cursor / CodexのOS ProcessとNirai Agent SessionはConversation lifetime中ずっと保持しない。`conversation-send`ごとにread-only Agent Sessionを1本だけ起動し、Turn終了後にAgent枠を解放する。一方、会話Contextは毎回Nirai transcriptを再送せず、Provider自身のnative Conversationを継続する。

- Cursor: 初回はACP `session/new`。以後は実Capabilityに応じ`session/resume`を優先し、現行Cursorでは`loadSession=true`の`session/load`を使用する。`session/load`が再送する過去`session/update`は復元trafficとして抑止し、新Turnの回答へ混ぜない。read-only stagingはConversation ID由来の安定Pathへ毎Turn再構築し、native Sessionのcwd identityを維持する
- Codex: 初回はapp-server `thread/start`、以後は正式な`thread/resume(threadId)`を使用する。experimentalなrollout `path`復元には依存しない。Conversation単位の隔離`CODEX_HOME`へnative thread stateだけを維持し、`auth.json`等のcredential materialはTurn実行中だけコピーしてProvider停止後に除去する
- Nirai Conversationはidentity / lifecycle /監査用hot tail、append-only full journal、native Provider IDを保持する。通常Turn Promptへ過去20件を再投入しない。native Provider IDが失われたrecovery経路だけjournalの完全Transcriptを明示的に復旧Contextとして利用する。Provider failure / cancel / interrupted / in-flight Core crashではnative IDとConversation cacheを無効化してjournalからfresh Contextを作る。journal導入前に既にtruncateされ原文が物理的に無いlegacy Conversationだけは推測で補わずfail-closedする

これにより長い仕様相談で同じ過去原文を毎Turn課金ContextとしてNirai側から重複投入せず、Provider側のsession/thread履歴・prompt cache・compaction等を利用できる。Niraiは独自の自動要約を通常経路へ挟まないため、要約誤りによる会話前提の書換えも避ける。

Provider Conversation開始をGlobal Task / Queue有無だけでは拒否しない。Agent RuntimeのResource Policyで、同一WorkspaceのWriteとだけ排他し、read-only同士や独立Workspace WorkはConcurrency Budget内で並行可能とする。Provider Turn中に通常Taskが到着した場合も、競合ResourceだけをQueue待機させる。

### read-only境界

Cursor:

- 既存隔離stagingを利用
- Nirai root reviewでは`.env` / `.env.*`をsecret-bearing sourceとしてstaging Snapshot / copy対象から物理除外する
- File変更 / Command等をread-only policyで拒否
- staging改変検知
- Source baseline変化検知
- 実TargetへProvider変更を適用しない

Codex:

- app-server thread sandbox=`read-only`
- turn `sandboxPolicy.type=readOnly`
- `networkAccess=false`
- writableRootsなし
- Approval要求はCoreで`decline`
- tool questionは空回答でskip
- Holo Local ClientへApproval / Decision操作を追加しない

Review:

- final summary先頭非空行の厳密な`SAFE` / `NEEDS FIX`だけをstructured verdictへ変換
- それ以外は`UNKNOWN`

## 自動検証

2026-09-06実測：

- Conversation Store / Holo Conversation / legacy Holo targeted: **29 passed**
- Cursor / Codex / Manager / Task Queueを含む重点回帰: **101 passed**
- Codex read-only app-server wire +既存Codex回帰: **12 passed**
- Holo Local Client実物を使ったResident / Cursor共通Conversation E2E: **passed**
- Core full pytest: **312 passed**
- 実Cursor ACP initialize capability probe: `loadSession=true` / `sessionCapabilities.resume`なしを確認
- `node --check tools/holo-local-client.mjs`: 成功

主要固定回帰：

1. Conversation transcriptがProvider processに依存せず再読込できる
2. Core再起動時、実行中Turnだけ`interrupted`になり完了済みConversationは維持される
3. Holo ↔ Residentを`start → send → wait → 次send`で複数Turn継続できる
4. Holo ↔ Cursor consultをTurnごとに別Agent Sessionで実行しつつ、同じnative Cursor Session IDを継続し、2 Turn目Promptへ前Turn原文を再投入しない
5. 前Turnmonitorの遅延Eventが次Turnのwaitを誤完了させない
6. Review modeが`NEEDS FIX`をstructured verdictとして返す
7. Provider Conversation cancelがProvider cancelへ伝播する
8. Provider Conversationが既存Task / Agent FIFOを追い越さない
9. Codex read-only Turnが`readOnly` sandbox / network disabled / writableRootsなしで起動する
10. Codex read-only時のApproval / QuestionをMaster Decision経路へ持ち上げない
11. 旧Cursor Review Prompt契約と旧`review-*`経路を後方互換で維持する
12. Node Local Client実物からResidentとCursorの双方を同じ`conversation-*`契約で操作できる
13. Cursor ACPをProcess再生成して`session/new → session/load`し、load時に再生された過去Assistant出力を新Turn summaryへ混入させない
14. Cursor Conversation staging cwdはAgent Session IDが変わってもConversation単位で同じPathになる
15. CodexをProcess再生成して`thread/start → thread/resume`し、2 Turn目へ前TurnPromptをNiraiから再投入しない
16. Codex Conversation Homeはnative thread stateをTurn間で維持する一方、`auth.json` / `cap_sid` / `.sandbox-secrets`をTurn終了後に残さない
17. Conversation close時にCodex native context cacheを削除できる
18. 100件hot tailを超えてもappend-only journalから全Message sequenceを再構築できる
19. Provider failure後はnative Provider ID / cacheを無効化し、次TurnのPromptへNirai journalの過去Transcriptを復旧Contextとして投入できる

### 2026-09-07 adversarial review follow-up

初回follow-upでProvider failure / journal recoveryを補強した後、追加の敵対的差分レビューでP1×2 / P2×2を検出し、全件修正した。

- Native Resident Conversationは同一logical Conversation lockを履歴差分の選定前に取得し、Provider Turn → Nirai transcript / Chat / Private Memoryのdurable commit → native `last_seen_entry_id`同期commitまで保持する。最初のtransport/publication `await`より前に`mark_seen`を完了するため、正常Turnが`pending_turn`のまま次Turnへ見える競合と、後続Turnが古いhistory deltaを先読みする競合の双方を閉じた
- Codex / CursorはCoreがNative Conversation Serviceのlogical lockを外側Transactionとして取得し、Service側はCore-owned lockを再取得しない。Service直接利用時は従来どおり自己Lockする。Gemini Interactions continuationも同じCore-owned logical lock境界へ統一した
- Holo ↔ Resident `talk`はTurn開始時のPublic Chat Session IDを固定し、Masterが応答待ち中に別Chatへ切り替えてもHolo発言とResident返答を同じSessionへ保存する。対象Sessionの削除 / World Memory forgetもTurn finalization中は拒否する
- Conversation durable stateが先に`completed`になっても、World publicationを含むTurn Taskが終わるまで`conversation-wait`は完了を返さず、次`conversation-send`も拒否する
- Cursor Nirai-root read-only review stagingは`.env` / `.env.*`をSnapshot / copy対象から物理除外し、Prompt上の禁止だけへ依存しない
- Agent Crash Recoveryはsource interrupted Sessionへchild Session IDを先にdurable予約するone-shot方式へ変更。逐次・並行の二重Recoveryを拒否し、Core restart時はchild存在ならsourceを消費済み、child未生成なら予約解除として復旧する
- Provider native contextのFailure Pathはambiguous failure時にcache破棄 + Nirai journal再構築を維持
- 100件hot tailはUI / Protocol用bounded viewとして維持し、長期復旧正本をappend-only journalへ分離

修正後実測：

- Agent Runtime / Cursor ACP+CLI / Codex / Holo Conversation / Native Conversation / Session / Memoryを含む重点回帰：**238 passed**
- Core full pytest：**417 passed**
- World Vitest：**39 files / 222 tests passed**
- TypeScript typecheck：成功
- Electron production build：成功
- `git diff --check`：成功（既存LF→CRLF warningのみ）
- 通常Resident Gemini実Provider 2-turn：**SAFE**。2 Turn目で前Turn識別語を再現し、`pending_turn=false` / `last_seen_entry_id=cvseq:4`を確認
- 通常Resident Cursor CLI実Provider 2-turn：**SAFE**。`cursor-grok-4.6-xhigh`非fastを維持した同一`session_id`の`--resume`で前Turn識別語を再現し、`pending_turn=false` / `last_seen_entry_id=cvseq:4`を確認
- Private Memory Product Smoke / World Memory Product Smoke：**SAFE**

## 残る実機確認

自動検証上のBlockingはない。

ただし、実Cursor / 実Codexを現在のNirai Coreへ接続したConversation Live Smokeは、Coreを本変更入りで再起動した後に短時間の`conversation-start → conversation-send → bounded conversation-wait`経路で確認する。以前Local MCP切断を起こした「Provider lifetime全体を1本の長時間`run_process`で包むSmoke」は再利用しない。

実機確認では少なくとも次を確認する。

- Cursor `consult`がread-onlyで返答し、Turn終了後Agent枠が空く
- Cursor `review`が`SAFE / NEEDS FIX / UNKNOWN`を返す
- 可能ならCodex `consult`もread-onlyで返答する
- 各waitは15秒以内の短いLocal Client呼び出しだけで継続でき、Local MCP stdioをProvider lifetimeへ拘束しない

## 判定

**自動検証判定: SAFE**

通常Resident Cursor / Geminiの実Provider Live Smokeは完了した。残るのはHoloのProvider Conversationとしての実Cursor consult/reviewと、利用枠回復後のCodex consult再確認であり、通常Resident Conversation ContinuityのBlockingではない。
