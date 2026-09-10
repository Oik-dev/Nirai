# Nirai 総合監査 Current Evidence — 2026-09-09

本書は2026-09-09総合監査のCurrent working treeについて、Active Designから参照する**実測Evidenceの正置先**である。

過去のReview履歴・Findingの詳細は`Docs/archive/research-and-old-design/Nirai_総合監査手順_2026-09-08.md`へ残し、本書ではCurrent受入判断に必要な測定結果だけをまとめる。

本書単独で総合Gate判定は行わない。

## Current自動回帰

- Core pytest：**617 passed / 0 failed**（`core/tests`全File）
- World Vitest：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- Electron production build：**PASS**
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：**PASS**。whitespace error 0。Windows working treeのLF→CRLF warningのみ

### World reconnect / Chat history regression

Fresh Memory / Session Reviewで、World reconnect中にfresh `history_response`がlive `chat_append`を後から上書きし、公開ChatまたはPrivate Whisperの最新EntryをUI stateから消すraceを検出した。

修正前に`world/tests/unit/SessionStore.test.ts`へ現行APIだけでREDを作り、`resident_whisper`のlive Entryが`setHistory()`後に実際に欠落することを確認した。

Currentではfresh history request開始時の`entryKeys`をbaselineとして保持し、history payloadに含まれずbaseline後に到着したlive Entryだけをhistory responseへ合流する。

- 修正前 focused：**1 failed / 7 passed**。`resident_whisper` live Entry欠落を直接再現
- 修正後 focused：**8 passed**
- 修正後 World全回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**

### Cursor ACP staging / history replay regression

Fresh Core Reviewで、Cursor ACPのNirai safety configだけ`Shell(*)` denyが欠落し、任意Shell command文字列からstaging / `tasks.allowed_dirs`境界を迂回できる余地を検出した。またnative Conversationの`session/load`中に、historical `cursor/update_todos` / `cursor/task`等がCurrent Agent Eventへ再放出され得る経路も確認した。

CurrentではACP / exact CLI双方で`Shell(*)`をdenyし、Providerはstaging内の構造化File操作だけを行い、実WorkspaceへのwriteはNirai-owned frozen review/apply経路へ限定する。`session/load`中はnotificationをCurrent Eventへemitせず、request形extensionもProviderを詰まらせないresponseだけ返してMasterへhistorical requestを再提示しない。

- focused Regression：**3 passed**
- Cursor Agent Runtime全体：**39 passed**
- Current Core全回帰：**594 passed / 0 failed**

### Active Contract follow-up

Fresh Active Contract Reviewで、`詳細設計/05_会話パネル.md`がCurrent Worldの`会話 / 仕事`切替を落としてFocus中入力をWhisper-onlyとしていた点と、Private DNA v0.5が変化中のCurrent Niraiコードを固定行番号で参照していた点を確認した。

Currentでは05を実装済み`ChatBar`へ同期し、Focus中の`会話`はWhisper、`仕事`はFocused ResidentへのDirect Task / Councilなしと明記した。Private DNA v0.5のCurrent Nirai根拠は、固定行番号から`_world_connection` / `_broadcast_agent_event()` / `_request_world_action()` / `PROTOCOL_VERSION` / `WorldRuntimeLauncher` / `ChatBar`等のsymbol・message contract・component名へ置き換えた。

- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：**PASS**。whitespace error 0。Windows working treeのLF→CRLF warningのみ

### Master Gate / Direct Task contract follow-up

Current fingerprint `c4245cf1d93b46256267890b767e9ba5d0b25ca0682a75b94959a5c2e502b3ba`へのDocs Fresh Reviewで、最上位`Nirai_基本設計.md`にFocus入力のWhisper-only / 文面Task化残渣、Private DNA v0.5にCurrent `会話 / 仕事` + Direct Task意味境界の欠落を検出した。Currentでは通常Resident Focusを`会話`=Whisper、`仕事`=Focused Resident Direct Taskへ統一し、通常会話の文面だけではTaskへ自動昇格しない。Holo Focusは通常Resident Direct Task assigneeへ変換せず、既存専用Whisper Surfaceの例外を維持する。

同fingerprintのCore Fresh Reviewは新規P0/P1なしだったが、別Reviewで得た候補をCurrentコードへ独立Failure Injectionしたところ、`waiting_for_master`中にProviderのlate `run_state=running`が届くとpending Approvalを残したままLifecycleだけ`running`へ戻り、Master responseを拒否する状態を再現した。CurrentではProviderのLifecycle eventをEvidenceとして保存しつつ、Nirai-owned `waiting_for_master` / `cancelling` Gateを上書きさせない。

- 修正前恒久Regression：**1 failed**。`running != waiting_for_master`を直接再現
- 修正後恒久Regression：**1 passed**
- 修正後実Probe：`waiting_for_master`保持、pending id/kind保持、provider turn metadata更新、Master response **accepted=true**、その後completedへ収束
- Agent Runtime Manager全体：**42 passed**
- Agent Runtime Manager + Agent Server Protocol：**83 passed**
- Current Core全回帰：**594 passed / 0 failed**
- Active Design監査：**17 files / broken links 0 / stale markers 0**

### Provider Conversation completed-before-cancel regression

Fresh Core Reviewで、Provider Agent Sessionがすでに`completed`へ到達した後、Nirai Conversationの`finish_turn()`前にCancelすると、正常返答とnative continuationを破棄するraceを検出した。

修正前に`core/tests/test_holo_conversation_runtime.py::test_holo_provider_cancel_after_agent_completed_preserves_completed_turn`を追加し、Agentだけ`completed`、Conversationは`running`の窓を固定してREDを実測した。

Currentでは、Provider AgentがCancel受付時点ですでに`completed`なら既存monitorのConversation commitを優先し、`cancellation_requested=false`で正常完了を保持する。Agentがまだnon-terminalなら従来どおりCancelを先にcommitし、cancel中のlate successを復活させない。

- 修正前Regression：**1 failed**。`cancellation_requested=true`を直接再現
- 修正後Cancel境界focused：**3 passed**
- Holo Conversation Runtime：**17 passed**
- Current Core全回帰：**594 passed / 0 failed**

## Doctor / Runtime

Current Doctor実測：

- Python runtime：project-local `.venv` **OK**
- Python：**3.12.10**
- `websockets`：**OK**
- `sqlite-vec`：**OK**
- config：**OK**
- Electron runtime：**OK**
- World production build：**OK**
- Cursor runtime：**OK**
- Codex runtime：**OK**
- Gemini runtime / credential source：**OK**
- Claude：Current acceptanceで無効化しているため**WARN**
- **fatal=0 / warnings=1**

## 実Provider Conversation Continuity

2026-09-08総合監査中のLive再確認で、通常Resident ConversationのCodex / Cursor / Gemini 3 Providerすべてについて、1 Turn目だけで与えたrandom識別語を2 Turn目のprovider-native continuationで再現した。

- Codex：native Thread continuation
- Cursor：Current `CursorCliConversationAdapter` + native `session_id / --resume`
- Gemini：Interactions `previous_interaction_id`

これにより、Active Design / Acceptanceでいう**通常Resident 3 Provider Live 2-turn確認済み**のCurrent根拠を本Evidence階層へ正置する。

2026-09-07の`Nirai_Phase1基盤完成_検証結果_2026-09-07.md`はPhase 1実装Baselineの時点Evidenceとして保持し、後続Live再確認を遡及追記しない。

## Live M4 / Product smoke

総合監査中にCurrent製品経路で次を実測した。

### M4 Core E2E / Codex

- 実Codex Providerを使用
- `file_change`に対するMaster approvalを1回実施
- `NIRAI_M4_CORE_E2E_OK`を実Fileへ作成
- Agent `done`
- Chat Task結果 1件
- Agent event 15件
- approval 1件
- isolated `CODEX_HOME` cleanup確認

### Public Memory

- Gemini Structured extraction：1件
- Document / Query embedding実行
- Semantic Queryで対象Entryを1位取得
- `processed=1 / failed=0`

### Private Memory

- 2件をembedding
- Semantic Queryで対象Private Entryを1位取得
- 対象Entry Forget後、vector count **2 → 1**
- Forget後の再検索Hit **0**
- `processed=2 / failed=0`

## Scale Evidence

### Public

- Raw：**250,000件**
- 対象検索：約**555ms**
- 12.02MiB compatibility Episodeへの1発話追記：約**115ms**
- 対象Session Forget：約**3.98s**
- Forget後Hit：**0**
- DB：約**193.6MiB**

### Private

- Raw：**100,000件**
- 通常検索：約**55ms**
- recent20：約**1.5ms**
- delta20：約**1.3ms**
- context作成：約**7.2ms**
- 追記：約**69ms**
- Forget：約**374ms**
- Forget後Hit：**0**

初回Legacy JSONL→SQLite importは約10.6s。通常再起動ではderived generation markerにより全rewriteをskipする。

## Production health smoke

Production World modeでNiraiを実起動し、Holo local `snapshot` / `skills` / `incidents`をCurrent製品経路で確認した時点では次を満たした。

- World：connected
- health：`ok`
- unresolved incident：0
- Memory outbox pending / quarantined：0
- pending World Forget：0
- interrupted Agent Session：0
- Codex / Cursor / Gemini runtime：`ok`
- missing required provider：0
- Resident config error：0
- Current公開Skill：0件として正常応答
- incidents：available / 0件

### R7-F46 — Cursor ACP read-only / workの外部Browser・Computer Tool境界がbaseline denyから漏れる

R7-F43〜F45反映後のfingerprint `3e4702781d83a75a21d24e908e9a674dc8207f6bec5a0ee6d79ff7e5c129b235`へのCore Fresh Reviewで、Cursor ACPだけexact CLIより外部Tool denyが弱い候補を検出した。Currentコードへ恒久Regressionを先に追加したところ、修正前は次を直接再現した。

- ACP用`cli-config.json`に`WebSearch(*)`が存在せず、Focused Regressionが失敗
- read-only Holo Reviewの`kind=browser` permission requestが`reject_once`ではなく`allow_once`を選択し、Focused Regressionが失敗

原因は、ACP homeを作る`_cursor_permission_denies()`が`Shell(*) / WebFetch(*) / Mcp(*:*)`までしかdenyせず、runtime側`CURSOR_EXTERNAL_TOOL_KINDS`も`browser / computer`系を外部Toolとして分類していなかったことにある。exact CLI側の`_harden_cursor_cli_home()`にはすでに`WebSearch(*) / Browser(*) / Computer(*)`があり、同じCursor Provider内で防御強度が分岐していた。

CurrentではACP homeにも`WebSearch(*) / Browser(*) / Computer(*)`をbaseline denyとして追加し、`browser / browser_use / computer / computer_use`と現行表記揺れの`websearch / webfetch`を外部Tool分類へ加えた。read-only Reviewと通常Agent Workの双方で、これらをMaster承認へ上げる前にbaseline拒否する。

- 修正前Focused Regression：**2 failed**
- 修正後Focused Regression：**3 passed**
- Cursor Agent Runtime全体：**39 passed**
- Agent Runtime Manager + Agent Server Protocol：**83 passed**
- Current Core全回帰：**594 passed / 0 failed**
- World全回帰：初回は`HoloAddonHost`のDive永続化待ち1件だけtimeout相当で **250 passed / 1 failed**。直後の同File Focusedは **10 passed**、続くWorld全回帰は **39 files / 251 tests passed**で再現せず
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：**fatal 0 / warning 1**（Claude disabled）
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：whitespace error **0**。LF→CRLF warningのみ

同Core Fresh Reviewが挙げた`update_task_metadata()`とCancelのLost Update候補はFindingへ昇格しなかった。`update_task_metadata()`は同一event-loop上で同期的にsnapshot取得→更新→保存まで実行し、その途中にyield pointがない。Fresh Reviewが想定したrunning event broadcastを意図的に停止し、その間にCancelを通してからbroadcastを再開する独立probeでも、最終`run_state=cancelled`は非terminalへ復活しなかった。Cancelは待機中Provider broadcast自体をcancelし得るため、このprobeでは`task_phase=assigned`のまま終わったが、LifecycleのLost Updateは再現しなかった。

Docs Fresh Reviewが挙げたVOICEVOX段階契約の文言差は、Master方針としてVOICEVOX経路自体を撤去するため今回の修正対象から外す。撤去時に関連Active Contractを同時に整理する。

### R7-F47 — Conversation-owned Cursor stagingがClose / Crash後の回収境界から漏れる

R7-F46反映後のfingerprint `c71b31101a3ae99ca1468c804d0a50beee2088f215397d53b1b85be437db04fb`へのCore Fresh Reviewで、read-only Cursor Conversationが使う安定staging `.cursor-conversation-<digest>`だけが明示Discardとstale cleanupの双方から漏れる候補を検出した。

Currentコードへ恒久Regressionを先に追加し、修正前に次を直接再現した。

- `discard_conversation_context(conversation_id)`後も対応するConversation stagingが残る：**failed**
- 6時間TTLを超えたabandoned `.cursor-conversation-*`をstale cleanupが回収しない：**failed**

原因は、通常turn完了時の`finally`ではstagingを削除する一方、`discard_conversation_context()`が`runtime/cursor_conversation_homes/CV-*`だけを対象にし、`_cleanup_stale_staging_workspaces()`も`.cursor-stage-*`しか走査していなかったことにある。Crash / 強制終了など通常`finally`を通らない場合、read-only対象Projectのコピーが`runtime/workspace`へ残留し続け得た。

Currentでは、Conversation staging名自体をlive runtime ownershipとして保持し、実行中はTTL超過でもstale cleanupから保護する。通常cleanup時にそのownershipを解放し、明示Conversation Discardではcredential homeと対応stagingの両方をbest-effortではなく失敗集約付きで削除する。Crash残骸の`.cursor-conversation-*`は通常`.cursor-stage-*`と同じ保守的TTL後に回収する。

- 修正前恒久Regression：**2 failed**
- 修正後Focused Regression：**3 passed**
- Cursor Agent Runtime全体：**42 passed**
- Agent Runtime Manager + Agent Server Protocol：**83 passed**
- R7-F47反映後のCurrent Core全回帰：**597 passed / 0 failed**

### R7-F48 — Private DNA ArchitectureだけWhisper由来の音声・表情・身振りをWorld roleへ許可していた

同fingerprintへのDocs Fresh Reviewで、`Docs/Nirai_DNA_UE427_Architecture_2026-09-08.md` §6.2が「Whisperの既存意味論を維持する」としながら、World roleへWhisper由来の発話中／聴取中状態、身振り・表情、Master向け音声再生指示を渡す契約になっていることを検出した。

これはMasterが撤去を決めたVOICEVOX transportの段階差とは別で、上位Active Contractが一貫して定める「Private Whisper / `resident_whisper`をWorld頭上吹き出し・TTS・公開会話Animationへ派生させない」というPrivacy Invariantとの直接矛盾だった。

Current DNA Architectureでは、Private Whisper固有の本文・発話状態・会話身振り・表情・音声再生指示をWorld roleへ渡さず、権限付きPrivate UI roleだけで表示・応答状態を扱うよう統一した。`NiraiVoicePlayback`もPublic World presentation対象だけに限定した。将来Private専用音声を導入する場合はPublic presentationを流用せず、上位Privacy Contractと専用の非World経路を先に設計する。

- Active Design監査：**17 files / broken links 0 / stale markers 0**

R7-F47 / R7-F48反映後の同一Current treeで最終自動回帰を再取得した。

- Core全自動回帰：**597 passed / 0 failed**
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：project-local `.venv`で **fatal 0 / warning 1**（Claude disabled）
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：whitespace error **0**。LF→CRLF warningのみ

### R7-F49 — Direct Task標準入口の旧文言が通常Whisper / `@Resident`からの暗黙Task化を許していた

R7-F47 / R7-F48反映後のfingerprint `8e29f0801250d2746a76e16f1b87287557255f55ca32845deab148449f29c207`へのDocs Fresh Reviewで、`Docs/詳細設計/07_タスクと拡張.md`の冒頭「標準入口：Direct / Delegated」だけが、Focus / Whisper等で相手と実作業意図が明確ならDirect Taskとして扱える旧契約を残していることを検出した。

同じ文書のCurrent Direct Task節と上位契約は、通常Whisperの文字列heuristicではTask化せず、MasterがUIで`仕事`を明示した入力、または既存の明示Task Shortcut / APIだけをTask入口とする。したがって旧標準入口は、`会話`modeの`@Resident`や仕事らしい文面を実作業Taskへ自動昇格し得る直接矛盾だった。

Currentでは標準入口をCurrent契約へ同期し、Resident Focus中の`仕事`明示入力または既存の明示Task入口だけをDirect Task化する。`会話`modeのWhisper / 通常Sayは、相手が明確、`@Resident`指定、または実作業依頼らしい文面であってもTaskへ昇格しない。Councilも通常会話を暗黙Task化する入口には使わない。

同Docs Fresh Reviewが指摘したCurrent Evidence冒頭のCore全回帰`594 passed`は、R7-F47追加後のCurrent実測`597 passed`へ同期した。後段に残る`594 passed`は各Finding時点の履歴Evidenceなので遡及変更しない。

### R7-F50 — Holo-owned Agent SessionがWorldの汎用Agent面へ露出し、Worldから操作できる

R7-F49反映後のfingerprint `06cc5b68eef5590d606d7ca3d7b0a3f04614fb63885e68054191c2ea162eb2ab`へのCore Fresh Reviewで、Holo Review / Holo Conversationが内部利用するAgent SessionもWorld hello時の`agent_session_snapshot`へ含まれ、Worldの汎用`agent_session_cancel / recover`等から所有境界を迂回できる候補を検出した。

CurrentコードへProtocol Regressionを先に追加し、修正前にWorld hello直後へHolo-owned `HR-*` Sessionの`agent_session_snapshot`が実際に送られることを直接再現した。さらにコード横断では、同じSessionの`agent_event` broadcast、Approval / Question / Plan response、snapshot request、cancel / recoverもWorld汎用面からSession IDだけで到達できた。

上位Active Contractでは、Holoの通常Assistant出力はMaster向けHolo Whisperであり、Nirai Worldへの公開はLocal MCP経由の明示Actionだけとする。原因はAgent Runtimeのdurable ownershipをWorld transport境界で判定せず、全Agent Sessionを同じWorld UIへ公開していたことにある。

Currentでは`origin_chat_session_id`を持つ公開Task由来SessionだけをWorld-managed Agent Sessionと定義した。Holo Review / Holo Conversation provider child等のHolo-owned SessionはWorld hello snapshot・`agent_event`・Approval / Question / Plan response・snapshot request・cancel・recoverから隔離し、Holo専用の認可済みLocal経路だけを使用する。内部Lifecycle / Queue解放等は従来どおりAgent Runtimeで処理する。

- 修正前Protocol Regression：**1 failed**。World helloでprivate Agent snapshot露出を直接再現
- 修正後Protocol Regression：**1 passed**。hello snapshot非公開、private `agent_event`非公開、World snapshot request / cancel拒否を確認
- Agent Server Protocol + Holo：**66 passed**
- R7-F50反映後のCurrent Core全回帰：**598 passed / 0 failed**

### R7-F51 — R7-F48後もDNA Voice汎用節だけPrivate Whisper音声をWorld経路へ通せる旧契約が残っていた

同fingerprintへのDocs Fresh Reviewで、R7-F48により§6.2と`NiraiVoicePlayback`をPublic-onlyへ直した後も、§4.5が`audience`付き発話をVoice ProviderからPrivate UEへ渡す記述を残し、§9.2の`speech_request / speech_status`も`audience`を維持すればPrivate用途へ使える形になっていることを検出した。

これはR7-F48と同じPrivacy Invariantの横展開漏れであり、特定Voice Engineの撤去方針とは独立する。CurrentではVoice Provider / `NiraiVoicePlayback` / `speech_request`をすべて**Public World presentation専用**へ統一した。Private Whisper本文・発話状態・音声再生指示はWorld roleへ送らず、将来Private専用音声を導入する場合も権限付きPrivate UI向けの非World経路を別設計する。

- Active Design監査：**17 files / broken links 0 / stale markers 0**
- DNA Architecture内の§4.5 / §6.2 / §9.2はPublic-only Voice境界で一致

### R7-F52 — 長期M4受入条件だけ旧Direct Task文字列推測契約が残っていた

同Docs Fresh Reviewで、`詳細設計/08_マイルストーンと受入基準.md`の長期M4受入条件に「相手と実作業意図が明確ならCouncilを経由しない」「実作業意図が曖昧なら確認」という旧契約が残り、R7-F49で同期したCurrent Direct Task入口と矛盾していることを検出した。

Currentでは、Focused Residentの`仕事`modeまたは既存の明示Task Shortcut / APIをMasterが選んだ時だけDirect Taskとする。指名済みの明示TaskはCouncilを経由しない一方、通常Say / Whisper / `@Resident`は仕事らしい文面でも文字列heuristicだけでTaskへ昇格しない。Task入口を明示した後に作業内容や変更対象が曖昧な場合だけ、File変更前にMasterへ確認する。

- 旧句「相手と実作業意図が明確なら」のActive Docs横断検索：**0件**
- Active Design監査：**17 files / broken links 0 / stale markers 0**

R7-F50〜F52反映後の同一Current treeで受入自動Evidenceを再取得した。

- Core全自動回帰：**598 passed / 0 failed**
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：project-local `.venv`で **fatal 0 / warning 1**（Claude disabled）
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：whitespace error **0**。LF→CRLF warningのみ

### R7-F53 — R7-F50のHolo-owned Agent World隔離がActive Protocol / Agent Contractへ貫通していなかった

R7-F50〜F52反映後のCurrent treeをFresh Reviewへ投入した際、Review harness側の固定fingerprintが旧`06cc5b68eef5590d606d7ca3d7b0a3f04614fb63885e68054191c2ea162eb2ab`のままだったため、そのReview結果自体はCurrent Gate Evidenceとして破棄した。ただしDocs側が提示した「R7-F50の所有境界が01 / 11へ契約化されていない」という候補はCurrent Active Docsを独立照合し、実在を確認した。

R7-F50のCore実装はすでに、durable `origin_chat_session_id`を持つ公開Task由来SessionだけをWorld-managedとし、Holo Supervisor ReviewとHolo Provider Conversation childをWorld汎用Agent面から隔離していた。一方、`01_通信プロトコル.md`は`agent_event / agent_session_snapshot / Approval・Question・Plan応答 / cancel / recover / snapshot request`をAgent Session一般のWorld契約として記述し、`11_AgentRuntimeと実行UI.md`も「Provider EventをWorldへ送る」「実行中Agent Sessionを再接続時に復元する」と無条件に記述していた。将来実装が文書を正としてR7-F50を巻き戻す危険がある契約不整合だった。

Currentでは01 / 11 / 12へ同一ownership定義を明示した。

- **World-managed Agent Session**：durable `origin_chat_session_id`を持つ公開Task由来Session
- **Holo-owned Agent Session**：`HR-*`かつorigin ChatなしのSupervisor Review、またはdurable `conversation_id`を持ちorigin Chatを持たないHolo Provider Conversation child
- Holo-owned SessionはWorld helloのAgent Snapshot、World向け`agent_event`、World汎用Approval / Question / Plan応答、cancel / recover / snapshot requestへ出さない
- Holo-owned Sessionの観測・停止・結果取得は認可済みHolo Local / Conversation Runtime経路だけを使う
- Agent Runtime内部のLifecycle / Resource管理は共通のまま維持する

### R7-F54 — AI_ENTRYのCurrent Core回帰Baselineだけ597のまま残っていた

同じ旧fingerprint Docs Reviewが提示した回帰件数差もCurrentで独立照合した。Current Evidenceと実測はR7-F50追加後の**598 passed / 0 failed**だが、`AI_ENTRY.md` §8だけ**597 passed / 0 failed**のまま残っていた。

これは実装欠陥ではなくCurrent Evidence同期漏れである。`AI_ENTRY.md`のCurrent Baselineだけを**598 passed / 0 failed**へ同期し、過去Finding時点の597 / 594等の履歴Evidenceは遡及変更しない。

### R7-F55 — Final Gate直前のCurrent Core全回帰が599件で、Current Baselineだけ598のままだった

R7-F50〜F54反映後のCurrent treeを最終Gate用に再実測したところ、`core/tests`全件は**599 passed / 0 failed**で完走し、`--collect-only`でも**599 tests collected**を確認した。一方、`AI_ENTRY.md` §8と本Evidence冒頭のCurrent Baselineだけ598のままだった。

製品コード・Test sourceの追加変更はなく、再実測前後のreview snapshotは同じCurrent treeを指しているため、これは新しい製品欠陥ではなくCurrent計測値の同期漏れと判断する。過去Finding各時点で記録した598 / 597 / 594等の履歴Evidenceは遡及変更しない。

Current Baselineは**599 passed / 0 failed**へ同期した。最終Fresh ReviewはこのEvidence同期後に新しいreview-target fingerprintを取得して実行する。

### R7-F56 — World-managed ownership契約が受信拒否まで明示されず、Protocol上fail-openに読めた

R7-F55反映後のfinal review-target fingerprint `5fda6a28d8335bab...56297`へのProtocol Fresh Reviewで、R7-F50 / F53のownership定義自体は存在するものの、`01_通信プロトコル.md`のHolo-owned境界が「World汎用面へ出さない」という表現中心で、World→CoreのApproval / Question / Plan応答、cancel / recover / snapshot requestをCoreが副作用前に必ず拒否する契約が十分に明示されていないP1候補を検出した。

Current実装はすでにdurable `origin_chat_session_id`を唯一のWorld-managed判定として6入口を共通guardで拒否しており、外部Protocol ProbeでもApproval / Question / Plan / cancel / recover / snapshot requestの6操作すべてHolo-owned Sessionに対して拒否されることを確認済みである。したがって実装バイパスではなく、将来実装が文書を正としてfail-openへ巻き戻り得る契約欠落と判断した。

Currentでは01 / 11 / 12を同じallowlist契約へ同期した。

- World-managedの唯一のallowlist条件はdurable `origin_chat_session_id`を持つ公開Task由来Session
- World→Coreの6操作はownershipを状態判定・Provider呼び出し・Recovery副作用より前に検証し、不一致なら受理しない
- 拒否時はAgent状態・pending request・Recovery消費状態を変更せず、Snapshot / Event / Recovery Result / `pending_input`も返さない
- Core→Worldの`agent_event / agent_session_snapshot / agent_session_recovery_result`もWorld-managedだけへ限定する
- Recovery childはsource自体がWorld-managedである場合だけownershipをdurableに引き継いで公開する
- Holo-owned Sessionは引き続き認可済みHolo Local / Conversation Runtime経路だけで観測・停止・結果取得する

### R7-F57 — Cursor Nirai-root read-only Reviewが実read set外の`runtime/workspace`まで排他していた

R7-F56反映後のfinal review-target fingerprint `ec3101cad93206757ac7157cb56065eb56c2ab42cfb2de610757ff7d8029586d`へのAgent Fresh Reviewで、Holo SupervisorのCursor Nirai-root ReviewがResource Policy上Repository Root全体のreaderとして予約されるため、World Taskの通常workspace `runtime/workspace/<task_id>`へのWriteまで同一tree競合として拒否するP1を検出した。

Current Cursor read-only root ReviewはProviderへ実Repositoryを直接渡さずstaging Snapshotを使い、そのSnapshotから`runtime/`を物理除外する。したがって`runtime/workspace/<task_id>`は実際のReview read setに含まれず、Path ancestryだけを理由に直列化する必要がない。一方、Codex root read-onlyは実Rootをread-only cwdとして扱い`runtime/`を同様に除外しないため、この例外をProvider一般へ拡張すると安全境界を弱める。

CurrentではAgent Runtimeのread reservationへ「Cursor root read-onlyで`runtime/`をread setから除外する」effective scopeを保持し、active snapshot / start reservationの双方で同じscope-aware conflict判定を使う。Cursor root Reviewと`runtime/workspace` WriteはBudget内で並行可能にし、Codex root read-onlyと同Writeは従来どおり競合として拒否する。

- 修正前恒久Regression：**1 failed**。active `runtime/workspace` Writeに対するCursor root Reviewが`same workspace`で拒否されることを直接再現
- 修正後恒久Regression：**1 passed**。Cursor root Reviewは並行開始し、同時にCodex root read-onlyは同Writeへ引き続き拒否されることを確認

### R7-F58 — Holo `review`互換入口だけResource Policyを迂回して全AgentをGlobal FIFO化していた

同Agent Fresh Reviewで、`CoreServer.holo_start_cursor_review_authorized()`がResource-based Concurrency導入後も`_task_work_pending() or agent_runtime.has_active_session()`を全体Gateとして残し、独立Resource上のAgent Sessionが1件でも存在するとHolo Reviewを拒否するP1を検出した。Reviewerが参照した旧Queue symbolはCurrentと異なっていたが、Currentコードを独立確認するとより広いGlobal Gateが実在した。

Active Designは全Provider / 全Projectの永久1-Agent直列化をInvariantにせず、Concurrency Budget + 実際に競合するResource単位で調停する。Holo Reviewも同じResource Policyへ従う契約であるため、このcompatibility入口だけGlobal FIFOを維持する合理性はない。

CurrentではServer側のGlobal FIFO Gateを撤去し、Holo Review開始時のBudget / workspace競合判定を`AgentRuntimeManager.start_session()`へ一本化した。Queue中のWorkは開始時に同じResource Policyで再判定され、競合時は既存のQueue待機へ戻るため、安全なResource調停は維持する。

- 修正前恒久Regression：**1 failed**。独立`runtime/workspace` AgentがrunningなだけでHolo Reviewが`FIFO boundary`拒否されることを直接再現
- 修正後恒久Regression：**1 passed**。World Task AgentをrunningのままCursor root Reviewが並行開始することを確認
- Agent Runtime Manager + Holo周辺回帰：**44 passed**
- R7-F57 / F58反映後のCore全自動回帰：**600 passed / 0 failed**
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：project-local `.venv`で **fatal 0 / warning 1**（Claude disabled）

### R7-F59 — Cursor Nirai-root read-only Reviewのstagingが実Repository配下にあり、real root denyも外れていた

R7-F57 / F58反映後のfinal review-target fingerprint `9c7412ed23bc3d861d81277fcc37c9c6e53e60fb6320546524611b9cf6998f78`へのAgent Fresh Reviewで、Cursor Nirai-root read-only ReviewのProvider隔離が不完全なP1を検出した。

Currentのroot Reviewは`runtime/`等をstaging Snapshotから物理除外していた一方、staging自体はNirai Repository内の`runtime/workspace`配下に作られていた。このためProvider cwdから親Directoryを辿ると実Repositoryの`.git`や実Sourceへ到達できる位置関係だった。また、staging自身までroot denyへ巻き込むことを避けるためNirai root Reviewだけ`extra_denied_paths`から実Rootを外しており、ACP側のmodeもread-onlyであっても`agent`固定だった。終了時のSource Hash確認もstagingと同じignore setを使うため、Providerが実Root側の`.env` / `runtime`等へ脱線した場合は検証対象外となり得た。

CurrentではNirai-root Cursor read-onlyだけstaging rootをOS temp配下のRepository外へ移し、実Nirai Root全体をRead / Write denyへ戻した。通常Task / 外部Projectのstaging場所は従来どおりとし、F57で確立した「Cursor root stagingの実read setに`runtime/`を含めない」Resource Policyは維持する。ACP read-onlyもmodeを`ask`へ固定し、exact CLIの`--mode ask`と同じくProvider自体のwrite/tool自由度を下げる。秘密・生成領域のSnapshot物理除外、staging改変検出、Source変化検出も従来どおり併用する。

- 修正前恒久Regression：root Review stagingが実Root配下であることを直接再現して **1 failed**
- 修正前恒久Regression：ACP read-onlyに`read_only` mode契約自体がなく **1 failed**
- 修正後Focused Regression：root Review repo外staging + real root deny + exact CLI askを **1 passed**
- 修正後Focused Regression：ACP read-only ask modeを **1 passed**
- Cursor Agent Runtime全体：**44 passed**
- Agent Runtime Manager全体：**44 passed**

### R7-F60 — Cursor apply / read-only検証成功後のcleanup失敗がSession全体をfailedへ巻き戻していた

同Agent Fresh Reviewで、Cursor ACP / exact CLI双方の`finally`が、Provider処理・read-only検証またはMaster承認済みapplyがすでに正常終了した後でもCredential Home / staging cleanup失敗を`AgentRuntimeError`として再送出するP1を検出した。

Writable Workでは実Workspaceへのapplyがすでに完了していてもManagerがSessionを`failed`へ確定し得るため、Masterや上位Workflowが「未適用の失敗」と解釈してrerunすると同じ作業を重複適用する危険がある。read-only Reviewでも有効なReview結果と後始末異常が同じfailedへ畳まれ、結果の意味とResource cleanup状態が混線する。

CurrentではProvider処理・read-only検証・Master承認済みapplyの成功確定と後始末を別状態として扱う。成功確定前のcleanup失敗は従来どおり`AgentRuntimeError`でfail-closedする。一方、成功確定後にcleanupだけが失敗した場合は`provider_cleanup_failed` Error Eventを残し、すでに成立したWork / Review結果自体はfailedへ巻き戻さない。Error Eventの送信自体が失敗した場合もLogへ診断を残し、成功済みapplyをrerunnable failureへ変換しない。

- 修正前恒久Regression：実Workspace apply成功後の最終staging cleanup失敗が`AgentRuntimeError`となり **1 failed**
- 修正後Focused Regression：apply結果を維持し`provider_cleanup_failed`を通知して正常returnし **1 passed**
- Cursor Agent Runtime全体：**44 passed**
- Agent Runtime Manager全体：**44 passed**

R7-F59 / F60反映後の同一Current treeで受入自動Evidenceを再取得した。

- Core全自動回帰：**602 passed / 0 failed**
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：project-local `.venv`で **fatal 0 / warning 1**（Claude disabled）
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：whitespace error **0**。LF→CRLF warningのみ

### R7-F61 — Codex正常Turn後のCredential cleanup失敗が成功済み結果をfailedへ巻き戻し得た

R7-F59 / F60反映後のfinal review-target fingerprint `10ff4794e826e3ce9f547a018cc1c616e9a1a99ed4faa1bce49eda8b0a9a4ff2`へのAgent Fresh ReviewはCodex cleanup境界へP1候補を2件提示した。

1件目の「app-server spawn失敗時にCredential Homeとruntime ownershipが残る」はCurrentコードと独立Failure Injectionで不成立を確認した。Current `CodexAppServerAdapter.run()`はHome prepare後の`_spawn()`を明示tryで囲み、spawn失敗 / cancel時にCredential HomeまたはConversation secret materialを削除してからouter ownershipをreleaseする。独立probeでも`SPAWN_FAILURE_CLEANUP=PASS`、Home不存在、ownership空を確認したため、このFindingは**false positiveとして棄却**する。

2件目の「Credential cleanup failureが正常終了と混線する」は、Reviewer記述の「黙殺」という機序自体はCurrentと異なった。Current `_remove_isolated_home()`はpersistent削除失敗をraiseし、`_finalize_run_resources()`も明示エラー化していた。しかし独立probeすると、Codex Turnが正常`completed`へ到達しapp-server停止も成功した後、最後のCredential cleanupだけが失敗した場合にもAdapter全体がraiseし、Managerが成功済みWorkを`failed`へ畳み得ることを確認した。Cursor R7-F60と同じく、上位が未実行失敗と解釈してrerunすると既に成立した作業を重複実行する危険がある。

CurrentではCredential cleanup失敗を専用内部例外へ分離し、**Codex Turnがcompleted済みかつapp-server shutdown成功済み**の場合に限り`provider_cleanup_failed` Error Eventへ変換して、成立済み結果をrerunnable `failed`へ戻さない。Provider Turn未成功、cancel / interrupted、またはapp-server shutdown失敗は従来どおりfail-closedする。shutdown failureとcleanup failureが同時に起きた場合もshutdown failureを優先し、Provider processがquiescedでない状態をcleanup warningへ降格しない。

- 独立spawn-failure probe：**PASS**。Credential Home不存在 / runtime ownership解放を確認
- 修正前恒久Regression：正常Turn後のCredential cleanup失敗が`AgentRuntimeUnavailableError`として再送出され **1 failed**
- 修正後恒久Regression：正常結果を維持し`provider_cleanup_failed`を通知して **1 passed**
- Codex Agent Runtime全体：**15 passed**
- Agent Runtime Manager全体：**44 passed**
- Core全自動回帰：**603 passed / 0 failed**
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：project-local `.venv`で **fatal 0 / warning 1**（Claude disabled）
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：whitespace error **0**。LF→CRLF warningのみ

### R7-F62 — Codex spawn / post-success cleanupのcancel raceを閉鎖

R7-F61反映後のfinal Codex delta review-target fingerprint `d2b4b2386e820da96b87fef6679629b4c643a4b6354284e470739e302693ff34`へのAgent Fresh Reviewで、Codex Resource lifecycleに2件のBlocking raceを検出した。

1. Codex TurnがProvider側で正常`completed`へ到達した後、app-server / Credential Homeのbounded cleanup中にMaster cancelまたはCore stopが入ると、Manager側Task cancelがcleanup自体を中断し、成立済み結果を`cancelled / failed`へ巻き戻し得た。Process停止も途中で切られるため、上位のrerunによる二重Workだけでなく子Process残留の可能性があった。
2. isolated Home準備とspawn成功後、`_active_lock`登録待ちへ入るまでの区間が共通Resource `finally`の外にあり、そのawait中cancelでapp-server Process / Credential Home / runtime ownershipが残り得た。またspawn待機中cancelではProcess生成が遅れて成立した場合のhandleを呼出側が失う可能性があった。

CurrentではCodex spawnをcancellation-safeな専用境界へ寄せ、cancel後も有限時間だけspawn settleを待ってlate-created Process handleを回収し、実Processが成立していればbounded process-tree stopを完了してからHomeを削除する。spawn自体がsettleしない場合も無期限待ちせずspawn taskをcancelして終了する。spawn成功後は次のawaitより前にclient / Home / runtime ownershipを共通cleanup `finally`の管理下へ置き、active registration待ちcancelでもResourceを回収する。

Turn成功後のcleanupもshieldされたbounded finalizationとしてResource収束まで完了する。成功確定を「cancel後にAdapterが普通にreturnした」という挙動から推測すると通常Holo Review cancelまで`completed`へ誤分類する回帰が実際に出たため、その推測は採用しなかった。代わりにAgent Runtime内部戻り値`AgentRunResult.work_committed`でAdapterがProvider側Work成立済みを明示した場合だけManagerが先行`cancelling`を`completed`へ収束させる。通常Adapterのplain return、Provider未成功cancel、Process shutdown失敗、cleanup failureは従来どおりcancel / fail-closed契約を維持する。World Protocolや新しいLifecycle stateは追加していない。

- 修正前恒久Regression：spawn中cancelでlate Processが残り **RED**
- 修正前恒久Regression：spawn成功後active登録待ちcancelでProcess / Home / ownershipが残り **RED**
- 修正前恒久Regression：Turn成功後process cleanup中cancelが`CancelledError`へ巻き戻り **RED**
- 修正前恒久Regression：ManagerがProvider成功済みcleanup結果を`cancelled`へ巻き戻し **RED**
- 修正途中で既存Holo Review cancelが`completed`へ誤分類される回帰を検出し、暗黙推測を廃止してexplicit `AgentRunResult`へ限定
- Codex cancel境界 + Holo既存cancel focused：**5 passed**
- `test_adversarial_followup_regressions.py`：**8 passed**。spawnが永続待機するFault Injectionでもcancelは有限時間で収束
- Codex Agent Runtime全体：**18 passed**（explicit result導入前の同Slice。最終Core全回帰で再包含）
- Agent Runtime Manager全体：**45 passed**（explicit result導入前の同Slice。最終Core全回帰で再包含）
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：project-local `.venv`で **fatal 0 / warning 1**（Claude disabled）
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：whitespace error **0**。LF→CRLF warningのみ
- Core全自動回帰：**607 passed / 0 failed**（117.45s）

### R7-F63 — Provider成功とformal interruptの競合で成功済みWorkが`cancelled`へ落ちるraceを閉鎖

R7-F62の最終fingerprint凍結後、同一Codex deltaを手動敵対レビュー中に、Managerがdurable stateを`cancelling`へ変更してからProviderのformal interrupt RPCを待ち、その後にProvider Taskをcancelするまでの短い区間を追加検証した。Provider側Workが既に成立済みで、その正常returnがformal interrupt待ち中に先着すると、Managerの通常完了分岐が`cancelling`だけを根拠にTerminal `cancelled`へ畳み、`final_summary='done'`を保持したまま再実行可能に見える状態を作れた。

ignored独立probeでは`CANCEL_RETURN=True / FINAL_STATE=cancelled / FINAL_SUMMARY='done'`を再現し、同じ条件を恒久Regressionへ移して修正前 **1 failed**を確認した。単純にCodexの全正常Turnを`AgentRunResult`へ変える案は、全Core回帰で既存Read-Only / Native Conversationの直接Adapter契約2本を壊し、**606 passed / 2 failed**となったため採用しなかった。

Currentでは通常Codex Turnの戻り値は従来どおりplain summary文字列を維持する。Codex Active Runだけに`cancel_requested`を保持し、`cancel()`はProvider `turn/interrupt` RPCをawaitする**前**にこのintentを記録する。Providerが`turn/completed`へ到達済みで、cleanup中cancelまたはformal cancel intentとの競合が存在する場合に限って`AgentRunResult(work_committed=True)`を返す。Managerはこの明示結果だけを`cancelling / timeout`表示より優先して`completed`へ収束させ、plain returnは引き続きcancel overrideの根拠にしない。これにより二重rerun防止と既存Conversation Adapter互換性を両立する。

- 独立probe：**RED**。`FINAL_STATE=cancelled / FINAL_SUMMARY='done'`
- 恒久Manager Regression修正前：**1 failed**
- 途中案の全Core：**606 passed / 2 failed**。Read-Only / Native Codex Conversationのsummary互換性回帰を検出して棄却
- Conversation互換性 + Manager race + cleanup cancel + Holo cancel focused：**5 passed**
- Codex cancel intent + Manager interrupt-race focused：**2 passed**
- Codex Agent Runtime + Agent Runtime Manager：**65 passed**
- `test_adversarial_followup_regressions.py`：**8 passed**
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：project-local `.venv`で **fatal 0 / warning 1**（Claude disabled）
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：whitespace error **0**。LF→CRLF warningのみ
- Core全自動回帰：**609 passed / 0 failed**（114.48s）

### R7-F64 — Cancel intent先行後のlate Provider completionを成功済みWorkへ誤分類するraceを閉鎖

R7-F63反映後のfingerprint `07a71a0913cbcbf5427614344aaf514456c595abb90887f0b8830822572252f4`へのFinal Fresh Adversarial Reviewで、Codex Adapterの`cancel_requested` booleanだけでは「Provider成功が先」か「Cancel intentが先」かを区別できないP1を検出した。`cancel()`はformal `turn/interrupt` RPCをawaitする前に`cancel_requested=True`を記録するが、その待機中にProviderの`turn/completed`が後着すると、修正前は`provider_work_succeeded=True`と`cancel_requested=True`の組だけで`AgentRunResult(work_committed=True)`を返せた。Managerはこの明示結果を`cancelling`より優先するため、Master Cancelが先行しているのにSessionを`completed`へ戻し得た。

恒久Regressionでは、fake Codex app-serverが`turn/interrupt`要求を受けた後にlate `turn/completed`を送り、その後でinterrupt RPCへ応答する順序を固定した。修正前はAdapter単体が`AgentRunResult(summary=None, work_committed=True)`を返し、Manager統合では最終`run_state=completed`となり、**2 failed**で欠陥を直接再現した。timeout-firstも同じ順序契約として追加し、late completionでtimeout結果を復活させないことを固定した。

Currentでは新しいLifecycle stateやGlobal lockを増やさず、Active Codex Runへ`provider_success_observed_before_cancel`という順序情報1bitだけを追加した。`turn/completed(status=completed)`を観測した時点でCancel intentがまだ無い場合だけこのbitを立てる。`AgentRunResult(work_committed=True)`は、Provider成功済みであることに加えて**成功通知をCancelより先に観測済み**の場合だけ返す。したがってF63の`success-first → completed維持`とF64の`cancel-first → late completionはcancelled維持`を同時に区別し、通常Codex Turnのplain summary契約は変更しない。

- 修正前恒久Regression：**2 failed**。Adapterの誤`work_committed=True`とManagerの誤`completed`を直接再現
- 修正後F64 + timeout-first focused：**3 passed**
- R7-F63 success-first + 通常cancel focused：**4 passed**
- Codex Agent Runtime + Codex Read-only Conversation + Native Conversation：**34 passed**
- Agent Runtime Manager + `test_adversarial_followup_regressions.py`：**54 passed**
- Core全自動回帰：**612 passed / 0 failed**（112.49s）
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：project-local `.venv`で **fatal 0 / warning 1**（Claude disabled）
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：whitespace error **0**。LF→CRLF warningのみ

この時点でも総合Gate判定は保留する。R7-F64反映・Evidence同期後の新fingerprintを凍結し、そのCurrent treeへのFinal Fresh Reviewを継続する。

### R7-F65：Manager cancel / timeout確定後、Adapterへのintent通知前にProvider成功が割り込むraceを閉鎖

R7-F64反映後のfingerprint `c435e4eec690bb90a01fe89be64665683d9f179a03550b55f57b2da65f736584`へのFinal Fresh Adversarial Reviewで、Manager側のCancel順序境界にBlocking raceを検出した。Master CancelではManagerがSessionを`cancelling`へdurable確定した後、Worldへのrun-state broadcastをawaitしてからAdapterのformal cancelへ進んでいた。timeoutも同様に`cancelling` Eventの記録・broadcastがProvider interruptより先だった。このため、Nirai側ではCancel / timeoutが先行済みでもCodex Adapterへintentが届く前のawait区間にProvider成功が割り込み、その成功をpre-cancelとして`AgentRunResult(work_committed=True)`へ昇格し得た。

恒久Regressionでは`cancelling` broadcastを意図的に停止し、その間にProvider workを完了させる順序をCancel / timeout双方で固定した。修正前は両ケースとも最終`run_state=completed`へ戻り、**2 failed**で契約違反を直接再現した。

Currentでは新しいLifecycle stateやGlobal lockを追加せず、ManagerがCancel / timeout境界へ入った時点でAdapterへ同期的な`mark_cancel_intent`を通知する任意hookだけを追加した。Master Cancelではdurable `cancelling`確定後、最初のbroadcast awaitより前にintentを通知する。timeoutでは`cancelling` Event記録より前にintentを通知する。Codexはこの同期intentを正式な`turn/interrupt` RPC待ちとは分離して保持するため、broadcastやRPC応答待ちの長さでProvider成功との順序が反転しない。hook非対応Adapterは従来契約のままである。

- 修正前恒久Regression：Cancel / timeout双方が最終`completed`へ戻り **2 failed**
- F63 / F64 / F65 focused：**7 passed**
- Codex Agent Runtime + Codex Read-only Conversation + Native Conversation + Manager + adversarial follow-up：**90 passed**
- F65時点Core全自動回帰：**614 passed / 0 failed**（110.83s）

### R7-F66：Codex active登録前のCancel intent取りこぼしを閉鎖

R7-F65修正後のFresh Reviewで、同期`mark_cancel_intent`が`_active`登録済みRunにしか届かないpre-active gapを検出した。CodexはCredential Home準備、Process spawn、Active object生成、`_active_lock`取得、active登録の順に進むため、ManagerがCancelへ入った時点でProcessは存在していても`_active`へ未登録ならintentを保持できなかった。その後Provider Turnが成功すると、実際にはCancel先行であるにもかかわらずpre-cancel successとして`AgentRunResult(work_committed=True)`を返し得た。

専用の即時成功fake app-serverを用いた恒久Regressionで、active登録前にCancel intentを記録した後Provider成功させる順序を固定し、修正前に`AgentRunResult(summary='done', work_committed=True)`を直接再現した。さらに初回修正後、Active object生成時のpending intent確認と`_active`登録の間で`_active_lock`待ちが発生すると、そこで到着したCancel intentを再び取りこぼす狭いraceを同じRegressionで再現した。

Currentでは既存のruntime ownership lockを再利用し、pre-active Cancel intentのSession IDだけを一時集合へ保持する。Active object生成時にpending intentを継承し、さらに`_active_lock`取得後の登録直前でも再確認する。登録後のintentはActive objectへ直接反映するため、Manager境界からpre-active保持、登録直前確認、active中更新までawait gapなく順序を引き継ぐ。run終了時のruntime ownership解放とManager task終了時の両方でpending intentを破棄し、Session終了後に集合を残さない。新しいGlobal lockやLifecycle stateは追加していない。

- pre-active修正前恒久Regression：Cancel先行後も`work_committed=True`となり **1 failed**
- Active object生成後 / active登録前の厳密化Regression：初回修正では同じ誤`work_committed=True`を再現し **1 failed**
- F63〜F66 focused：**8 passed**
- Codex Agent Runtime + Codex Read-only Conversation + Native Conversation + Manager + adversarial follow-up：**91 passed**
- Core全自動回帰：**615 passed / 0 failed**（113.19s）
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：project-local `.venv`で **fatal 0 / warning 1**（Claude disabled）
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：whitespace error **0**。LF→CRLF warningのみ

この時点でも総合Gate判定は保留する。R7-F65 / F66とCurrent Evidence同期後のtreeを新fingerprintへ凍結し、その同一treeへのFinal Fresh Adversarial Reviewを継続する。

### R7-F67：Provider正常終了後、Managerが結果を消費する直前のCancelで成功済みWorkが`cancelled`へ落ちるraceを閉鎖

R7-F65 / F66反映後のfingerprint `f8137bff1562ccdb77578cd9116b76ed2f36dd740eb67b1bb5123ae0554c88f8`へのFinal Fresh Adversarial Reviewで、success-first側に残るManager境界のP1を検出した。Adapter / Provider coroutineが正常WorkとAdapter-owned cleanupをすべて終えplain summaryを返した後でも、Manager wrapperがcompleted Provider Taskの結果をまだ消費していない短い区間がある。この間にMaster Cancelが入ると、Adapterはすでにreturn済みなので`AgentRunResult(work_committed=True)`へ昇格できず、修正前ManagerはSnapshotを`cancelling`へ変更した後、plain resultを`cancelled`へ畳み得た。

恒久Regressionでは、fake Adapterが`work_completed`を通知してplain `"done"`をreturnし、event loopを1回yieldしてProvider Taskの正常終了を確定させた後、Manager wrapperが結果を消費する前にCancelを入れた。修正前はProvider成功がCancelより先であるにもかかわらず最終`run_state=cancelled`となり、**1 failed**で契約違反を直接再現した。

CurrentではAdapter成功判定をManagerへ再実装せず、Managerが各SessionのProvider Task handleだけを短命に保持する。`cancel()`のNirai lifecycle境界へ入った時点でProvider Taskが既に`done()`なら、Provider実行とAdapter-owned cleanupはCancelより前に終了済みなのでCancelを`False`で棄却し、既存Manager wrapperへ正常結果または例外の消費を任せる。Provider Taskが未完了なら従来どおりCancelへ進む。Provider Task handleはManager wrapper終了時に破棄し、新しいLifecycle stateやProvider固有成功推測は追加していない。

- 修正前恒久Regression：Provider正常return後のCancelで最終`cancelled`となり **1 failed**
- R7-F63〜F67 focused：**9 passed**
- Codex Agent Runtime + Codex Read-only Conversation + Native Conversation + Manager + adversarial follow-up：**92 passed**
- R7-F67時点Core全自動回帰：**616 passed / 0 failed**（113.15s）

### R7-F68：Provider未起動のstarting中Cancelでpre-active intent集合だけが残るP2を閉鎖

R7-F67修正後のFresh Reviewで、R7-F66が追加したpre-active Cancel intentの寿命にP2を検出した。Managerが`start_session()`のstarting Event broadcast中で、まだProvider wrapper Taskを作成していない段階でも`cancel()`はAdapterへ`mark_cancel_intent`を呼んでいた。Codex側はこのSession IDをpre-active集合へ保持するが、Provider runが一度も始まらないため`_release_runtime_id()`は通らず、Manager wrapperも存在しないため`_task_done()`によるclearも通らない。実Work自体は正しく開始阻止されるものの、Cancelを繰り返すと短命管理情報が残留し続け得た。

恒久Regressionではstarting broadcastを停止し、Managerにdurable Snapshotはあるが`_tasks`にProvider wrapperがまだ存在しない状態を固定した。その状態でCancelし、startを再開してProviderが一度も起動しないことを確認した上で、修正前はtracking Adapterのpending intent集合にSession IDが残ることを**1 failed**で直接再現した。

CurrentではManagerがAdapterへ同期Cancel intentを渡す条件を、対応するManager provider wrapper Taskが既に存在する場合だけへ限定した。Taskが存在しないstarting段階では、その後のstart-blocked判定とManager自身の`cancelled`確定だけでProvider起動を防げるためAdapter intentは不要である。一方、wrapper Taskは存在するがCodex `_active`未登録というR7-F66のpre-active区間では従来どおりintentを保持する。これによりR7-F66の順序保証を維持したまま未起動Sessionの残留だけを除いた。

- 修正前恒久Regression：Provider未起動Cancel後もpending intent Session IDが残り **1 failed**
- R7-F63〜F68 focused：**10 passed**
- Codex Agent Runtime + Codex Read-only Conversation + Native Conversation + Manager + adversarial follow-up：**93 passed**
- Core全自動回帰：**617 passed / 0 failed**（112.66s）
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：project-local `.venv`で **fatal 0 / warning 1**（Claude disabled）
- Active Design監査：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：whitespace error **0**。LF→CRLF warningのみ

この時点でも総合Gate判定は保留する。R7-F67 / F68とCurrent Evidence同期後のtreeを新fingerprintへ凍結し、その同一treeへのFinal Fresh Adversarial Reviewを継続する。

## Fresh Reviewの扱い

Fresh Reviewは必ず同一fingerprintへ当てる。Review中にworking treeが変わった場合は`Review target changed while Cursor was reviewing`としてstale結果を破棄し、Current Evidenceへ数えない。

2026-09-09の途中凍結tree `8a27f974...de075`ではCore Fresh Reviewが新規P0/P1なしを返したが、その後World reconnect race修正とActive Docs修正を行ったため、最終Fresh Review証拠には数えない。

その後のfingerprint `c4245cf1d93b46256267890b767e9ba5d0b25ca0682a75b94959a5c2e502b3ba`では、Memory Fresh Reviewは新規P0/P1なし、Docs Fresh ReviewはR7-F43 / R7-F44の契約欠落を検出した。Core Fresh Reviewは新規P0/P1なしだったが、別Review候補から独立Failure InjectionしたR7-F45が実在したため、このfingerprintも修正前のDiscovery Evidenceとして扱う。

R7-F43〜F45反映後のfingerprint `3e4702781d83a75a21d24e908e9a674dc8207f6bec5a0ee6d79ff7e5c129b235`では、Memory Fresh Reviewは新規Findingなし、Core Fresh ReviewはR7-F46と`update_task_metadata()`競合候補を提示した。R7-F46は独立REDで実在を確認して修正し、metadata競合候補は実行順序確認と独立probeでLifecycle巻き戻しを再現できず棄却した。Docs Fresh ReviewのVOICEVOX文言差は、Masterが同経路の撤去を選択したため撤去作業へ統合する。

R7-F46反映後のfingerprint `c71b31101a3ae99ca1468c804d0a50beee2088f215397d53b1b85be437db04fb`では、Memory Fresh Reviewは新規Findingなし。Core Fresh ReviewはR7-F47を検出し、恒久RED 2本で実在を確認した。Docs Fresh Reviewは既知VOICEVOX差を除外した上でR7-F48のPrivate Whisper Presentation矛盾を検出した。両件ともCurrentへ反映済みで、R7-F47はFocused 3 passed、Cursor Runtime 42 passed、Manager + Protocol 83 passedまで確認した。

R7-F47 / R7-F48反映後のfingerprint `8e29f0801250d2746a76e16f1b87287557255f55ca32845deab148449f29c207`では、Core / Memory Fresh Reviewは新規P0/P1なし。Docs Fresh ReviewはR7-F49とCurrent Evidence冒頭の回帰件数同期漏れを検出し、Current契約へ反映した。

R7-F49反映後のfingerprint `06cc5b68eef5590d606d7ca3d7b0a3f04614fb63885e68054191c2ea162eb2ab`では、Memory Fresh Reviewは新規P0/P1なし。Core Fresh ReviewはR7-F50のHolo-owned Agent ownership境界を検出し、Docs Fresh ReviewはR7-F51 / R7-F52の契約残渣を検出した。3件ともCurrentへ反映した。

R7-F57 / F58反映後のfingerprint `9c7412ed23bc3d861d81277fcc37c9c6e53e60fb6320546524611b9cf6998f78`へのAgent Fresh ReviewではR7-F59 / F60を検出し、Currentコードへ独立REDを作って実在を確認した。F59はNirai-root Cursor read-only stagingをRepository外へ隔離しreal root deny + ACP ask modeへ統一、F60は成功済みWork / Reviewと後処理cleanup失敗を分離した。Focused / Adapter全体 / Manager全体をGREENへ反転し、Core 602 / World 251 / typecheck / build / Doctor / Active Design / diff-checkまで再取得した。総合Gate判定は、この反映後treeを新fingerprintへ再凍結し、その同一fingerprintへのFresh Reviewと他Reviewer結果を突合して行う。

## Final Fresh Adversarial Review / 総合Gate終了判定 — 2026-09-10

### 最終Review対象

R7-F67 / F68反映とCurrent Evidence同期後の製品treeをfingerprint `0c2f3427b3e63ec11cee57fb0c6776e03f1ea3f20df3c1fe8586fed463958616`へ凍結し、この同一fingerprintを動かさずFinal Fresh Adversarial Reviewを実施した。

修正理由や過去Findingの答え合わせを前提にせず、少なくとも次の境界を再確認した。

- Agent Lifecycle：starting / pre-active / active / Provider正常終了後のCancel、timeout、success-first / cancel-first、Manager task / Provider task / cancel intentの寿命
- Provider：Codex native replay、late completion、cleanup、persistent context、Provider task収束
- Safety：`allowed_dirs`、Cursor staging、`Shell(*)` deny、Approval / Question / Planのlate response、World-managed / Holo-owned ownership
- Public / Private：Whisper storage、Brain context、Session title、World bubble / TTS / Animation、Forget / deleteの意味境界
- Memory：Public Raw authority、Episode / Structured / Derivedの役割、Private Scope、5年利用時の常用経路
- Active Contract：基本設計、Protocol 01、Core 02、UI 05、Memory 06、Milestone 08、Private DNA v0.5の相互整合

このFinal Freshでは新規P0 / P1を検出しなかった。新規P2も検出しなかった。補助的なCursor xhigh micro reviewはCore WebSocket不通のため開始できず、Fresh Evidenceには数えていない。特定Reviewerの完走は終了条件ではなく、同一frozen treeへのFresh攻撃と各Gate成立を終了根拠とする。

### P2 Disposition

総合監査で残したP2は、最終終了条件に従い次のいずれかへ明示的に収束させる。

- **Fixed**：Finding本文に`Fix` / `Current change` / Regressionが記録されたP2。R7-F68を含めCurrentへ反映済み。
- **Accept**：R7-F01の低頻度Chat delete / Forget時のConversation scanは、正しさを優先し本監査では索引を追加しない。R5-F17のDirect Task結果はPrivate Whisperへ混ぜずAgent panelを詳細正本とする現契約を維持する。
- **Deferred — Architecture / World replacement**：R2-F03〜F10およびR6-F06 / F09 / F11等のGod Object、Provider lifecycle共通化、設定責務重複、互換層退役は、DNA / UE World replacementまたはReference-Firstを伴う設計変更と同時に扱う。本監査のP1修正へ混ぜない。
- **Deferred — Provider / Recovery operations**：R4-F04の`.RB-*`自動rollbackはMaster判断なしの自動復元を避け、workspace排他 + durable recovery evidenceをCurrent境界とする。R4-F06のCredential Home TTL、R6-F01の無効Claude Catalog、Round 5 CandidateのNative Codex talk / Agent Runtime slot分離は、Provider制約・実害Evidenceと合わせて再評価する。
- **Deferred — Compatibility / Product UX**：R3-F05のclosed Conversation retention、R6-F02の未実装`local-llm`予約行、R6-F04のLegacy Episode / Private JSONL互換、R6-F10のCouncil native continuation、Round 5 CandidateのRecall trigger高度化は、現行正本・Privacy・通常利用を壊すBlocking条件を確認していないため、それぞれStorage / migration / Council / Memory UXの後工程へ送る。

R5-F15で扱ったWorld ForgetとPrivate Whisperの関係は、未解決P1として残さない。Current Product Contractは「World Memory ForgetはPublic系を忘れ、Private Memoryは別系統として残す」と明示しており、基本設計・Protocol・UI・Memory設計も同じ意味へ同期済みである。Private側を明示Forgetする追加UIは別Product判断であり、現在のPublic ForgetをPrivate削除へ拡張しない。

### 最終Gate

- P0：**0 unresolved**
- P1：**0 unresolved**
- P2：**Fixed / Accept / reasoned Deferredへ全件Disposition済み**
- Failure Injection Regression：主要Lifecycle / Forget / Recovery / Cancel / Approval / reconnectに恒久Regressionあり
- Core全自動回帰：**617 passed / 0 failed**
- World全自動回帰：**39 files / 251 tests passed**
- TypeScript typecheck：**PASS**
- production build：**PASS**
- Doctor：**fatal 0 / warning 1**。warningはCurrent acceptanceで無効のClaudeのみ
- Active Design：**17 files / broken links 0 / stale markers 0**
- `git diff --check`：**PASS**。whitespace error 0
- 主要E2E：実Codex M4、Codex / Cursor / Gemini native 2-turn、Public / Private Memory smoke、production World接続を確認済み
- Public / Private / Credential / Approval / `allowed_dirs`：**Blockingなし**
- 5年利用：常用経路に明白な全件scan / O(N^2) / 無制限保持のBlockingなし。既知の低頻度・移行時コストはAccept / Deferredへ分類済み
- Round 6削除監査：**完了**。削除済 / 削除可能 / Current互換のため維持へ分類済み
- Final Fresh Adversarial Review：**新規Blockingなし**

**総合判定：PASS / SAFE。**

この判定はNirai Phase 1基盤と、次工程であるDNA / UE4.27 Feasibilityへ進むための総合監査Gateを閉じるものである。M3のWorld Observation / Natural Idle / Brain生活ティックやDNA / UE実装そのものの完了を意味しない。
