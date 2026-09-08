# Nirai AI Entry

このファイルはNiraiへ入るAI向けの**短いRouter**である。詳細なReview履歴・過去Finding・Provider別の修正経緯はここへ蓄積しない。

## 1. 30秒で分かるNirai

Niraiは、MasterとAI Residentが同じ場所に存在し、長期間関係を継続しながら暮らし・会話・必要な仕事を行う箱庭基盤である。

最重要原則は次の通り。

- **居場所が主、タスクは従**
- ResidentのIdentity・人格・関係・記憶はBrain交換で失わない
- World / Core、人格 / 記憶 / Brain / Avatarを分離する
- Public / Private Memoryを混ぜない
- Brain会話とAgent Workを分離し、File変更等は安全境界・Master Approvalを通す
- 最低5年間の日常利用で根本作り直しを必要としないことを設計目標にする
- 合理性・効率性・保守性を設計品質として扱う
- 大規模実装前はReference-First Gateを通し、Webで公式機能・成熟OSS・Reference Implementationを広く調査して再発明を避ける

## 2. Source of Truth

優先順位は次の通り。

1. **Product Goal / Philosophy**
   - `Docs/Nirai_基本設計.md`
2. **Invariant / Guardrail / Design Governance**
   - `Docs/Nirai_設計ガバナンス.md`
3. **Active Design / Contract**
   - `Docs/詳細設計/00_全体構成.md`
   - `Docs/詳細設計/01_通信プロトコル.md`
   - `Docs/詳細設計/02_Core.md`
   - `Docs/詳細設計/03_Brainドライバ.md`
   - `Docs/詳細設計/04_World.md`
   - `Docs/詳細設計/05_会話パネル.md`
   - `Docs/詳細設計/06_Residentと記憶.md`
   - `Docs/詳細設計/07_タスクと拡張.md`
   - `Docs/詳細設計/09_3DビジュアルとAvatarパイプライン.md`
   - `Docs/詳細設計/11_AgentRuntimeと実行UI.md`
   - `Docs/詳細設計/12_HoloAddonとChatGPTDive.md`
4. **Milestone / Acceptance**
   - `Docs/詳細設計/08_マイルストーンと受入基準.md`
5. **Evidence / History / Reference**
   - `Docs/*_検証結果.md`
   - `Docs/詳細設計/10_AITuberKit分析と実装ブループリント.md`
   - `Docs/詳細設計/13_AIAvatarKit参考カンペと独自実装Slice.md`
   - `Docs/Nirai_Reference-First調査_長期基盤_2026-09-06.md`
   - `Docs/Nirai_Reference-First調査_Whisper長期Conversation_2026-09-06.md`
   - `Docs/Nirai_SerinaMemory監査_2026-09-06.md`
   - `Docs/Nirai_MemoryEvaluation設計_2026-09-06.md`
   - `Docs/Nirai_MemoryEvaluation_初回結果_2026-09-06.md`
   - `Docs/Nirai_MemoryScale_検証結果_2026-09-06.md`
   - `Docs/Nirai_PrivateMemorySemantic_検証結果_2026-09-06.md`
   - `Docs/Nirai_DirectTask_検証結果_2026-09-07.md`
   - `Docs/Nirai_Phase1基盤完成_検証結果_2026-09-07.md`
   - `Docs/Nirai_起動復元耐性とWorldProtocolHardening_検証結果_2026-09-07.md`
   - `Docs/Adversarial_Review_2026-09-08.md`
   - `Docs/Adversarial_Review_Followup_2026-09-08.md`
   - `Docs/Nirai_IncidentRepair_検証結果_2026-09-08.md`
   - `Docs/Nirai_DNA_UE4.27_WorldAddon方針_2026-09-06.md`
   - `Docs/plans/archive/`
   - `Docs/history/`

`10`と`13`は有用なカンニングペーパーだが、現行設計を拘束する正本ではない。

## 3. 現在の設計監査

長期日常利用を基準に、2026-09-06に設計書と実装のユースケース適合性を棚卸ししている。

- 棚卸し：`Docs/Nirai_設計書棚卸_2026-09-06.md`
- ユースケース監査：`Docs/Nirai_ユースケース適合性監査_2026-09-06.md`
- 長期基盤Reference-First調査：`Docs/Nirai_Reference-First調査_長期基盤_2026-09-06.md`

現在の大きな方針変更は次の通り。

- 通常Residentの長期ConversationもProvider native Session / Thread continuationを利用する方向へ統一する
- Public / Private Memoryを`Raw Durable Source + Structured Continuity + Derived Retrieval`へ再設計する。Structured ContinuityはAtomic Memory / Fact、Episode / Scenario、Durable Continuityへ段階化する候補を同一Benchmarkで比較してから確定する
- Global Brain Lockは撤去済み。同一native Conversationだけをlogical conversation単位で直列化し、独立Conversationは並行可能とする
- Task標準入口は、Masterが指名Residentへ自然言語で直接依頼する方式とする
- Councilは必要な時だけ使い、全Taskの必須関所にしない
- Agent Runtime全体1件制約は撤去済み。Task QueueはCrash recovery用に維持し、実行はConcurrency Budget + Workspace read/write Resource Policyで調停する
- World Runtimeは交換可能な層とし、現行Electron + Three.jsを永久Invariantにしない。Core基盤完成後にDNA → UE4.27のFeasibility Spikeを行い、成立する場合はMasterローカル専用Private World Addonとして移植する
- DNA由来Asset / SceneはNirai本体・公開Repository・配布Packageへ含めない。現行Three.js海中WorldはDistribution-safe standard World候補として凍結・退避する

長期構造へ影響する新規実装は、Active Designと監査方針の同期を確認してから行う。

**Phase 1のCore基盤実装・自動回帰、および通常Resident Cursor / Geminiの実Provider Live 2-turn Smokeは2026-09-07にSAFE相当まで到達した。** Cursorは`cursor-grok-4.6-xhigh`非fastのCLI native `session_id` / `--resume`継続、GeminiはInteractions `previous_interaction_id`継続を実機確認済み。Codex LiveはProvider利用枠回復後の再確認だけを外部条件として残す。DNA着手前Hardeningとして、Project-local `.venv` + stdlib-only Startup Preflight / Doctorを導入し、optional Provider欠損をCore起動Failureから分離した。Core / World `hello`はProtocol v1 `runtime_id / capabilities` handshakeを必須化し、非互換Worldを登録前に拒否する。

2026-09-08の敵対レビューで確定したP1/P2 21件は修正・正方向回帰へ変換済み。追加追跡でもCursor runtime ownership / cross-process stale cleanup、外部成果物のjunction脱出、Chat fsync直後crashからMemory outboxへ収束する起動順、World Forget tombstoneと遅延Vector commit、Agent Snapshotの最新500件bounded tailを補強した。独立Cursor再レビューは82秒timeoutで判定未取得のため、外部SAFE証拠には数えない。

同日、フルself-buildを先行実装せず、軽量な**Incident Repair + Dive Health Check**を採用した。Core ERRORは`runtime/incidents.sqlite3`へfingerprint集約し、Memory Outbox等の長期整合性Failureも明示Incident化する。Incident SQLiteはWAL + bounded busy timeoutとし、一時競合でERRORを保存できない場合は単一bounded fallback journalへfsync退避し、Dive時に最大32件ずつSQLiteへ戻す。Holo Diveの`attach / snapshot`は未解決Incident、fallback残留、Memory未同期、Interrupted Agent、Resident設定破損、現在有効なResidentが依存するProvider Runtimeを軽量確認し、`attention`ならHoloが`incidents`で修復Contextを取得できる。修正・Review・回帰後は`incident-resolve`で閉じる。通常Resident / Agent RuntimeのNirai本体write禁止とself-build M5+境界は維持する。Codex Desktop managed Runtime探索もDoctor / Product Runtimeで共通化し、本PCではCursor / Codex / GeminiがすべてOKになった。2026-09-08追加敵対レビューのHolo Conversation stuck、Codex Home並行cleanup、Cursor rollback export、空Gemini key、Codex cleanup event-loop block、Incident SQLite競合の6件も再現後に修正・回帰化した。さらにFollowup Review R01-R06で、Cursor Recoveryの外側cleanup、approved write cancel後のlate write、削除Chatの未同期Memory poison、Codex/Cursor prepare cancel残骸、Incident fallback truncated tailを再現し、全件を正方向回帰へ変換した。

次工程は DNA / UE4.27 World Addon feasibility・移植 → M3暮らし接続 → Core / Protocol v1固定 → M5+。Three.js海中Worldの追加Graphic磨き込みをWorld Replacement確認より先に行わない。

## 4. 現在の実装状態

- **M0**：Stable
- **M1**：Stable
- **M2**：Stable（2026-08-30）
- **M3**：World Memory Retriever先行Slice SAFE。2026-09-06に長期Public World Memory第一製品SliceもSAFE。Private Raw / Local FTS / Gemini Semantic / Whisper Recall第一Sliceと250k ScaleもSAFE。M3全体は未完了
  - Public World Memory：lossless Raw SQLite + Structured Atomic/Current Fact + local FTS5 + Gemini Embedding 2 + abstention-oriented Hybrid Recall + legacy Episode fallback。旧Episodeはappend-only互換Viewへ降格
  - Private Whisper Memory：Resident別SQLite Raw + Local FTS5 + Gemini Embedding 2 768d + legacy JSONL import + old-Whisper semantic retrieval。2026-09-07 Master判断でPrivate WhisperのEmbeddingもGemini Freeを許容
  - Public / Private Gemini Embedding 2は同じrolling 24h quota guardを共有し、Background最大400 / Query最大500 / 合計900をNirai全体で超えない。Nirai RuntimeはローカルBGE-M3を使わず、Serina側のCPU Embedding Runtimeと分離する
  - Scale：Private 250k Whisperでrecent20約4ms / delta約3ms / append約53ms、Public 250k RawでExact Recall約0.6秒 / 12MiB旧Episodeへの1件append約13ms
  - Private Product Golden：Structured Fact Overlayなしの製品Raw + FTS + Gemini Embedding 2 + Temporal ruleで8/8 / false recall 0。Structured ContinuityはCurrent blockerではなく、Golden拡張で不足が出た場合だけ追加するChallenger
  - 未完了：Public Vector/Cloud長時間運用、Private Structured Challenger評価の拡張、World Observation / World Natural Idle Scheduler / Brain生活ティック
- **M4**：SAFE。2026-09-07 Phase 1長期利用更新まで完了
  - Codex Agent Runtime / Cursor ACP Agent Runtime / Antigravity Agent Runtime
  - Focused Residentで`会話 / 仕事`を明示切替し、`仕事`時は自然文をDirect Taskとして送信。`/task`は互換Shortcut
  - 指名ResidentのDirect TaskはCouncilなし、Queue/restartでassignee保持、実作業不能なら自動移管しない
  - resident未指定Taskは明示Council互換のconsult / volunteer経路を維持
  - Global 1-Agent制約を撤去し、既定4 Session Budget + Workspace read/write Resource Policyへ移行
  - Core crash後の`interrupted` Agent Sessionは自動Write resumeしない。`resume`はAdapterが`crash_resume` Capabilityを明示する場合だけ提示し、現行Built-in Adapterは`rerun / abandon`のみを提示する。Recovery choiceはsource→child durable linkでone-shot化し、同じsourceからの逐次・並行二重実行を拒否する
  - named target / Approval / Credential isolation / fail-closed等の既存安全境界を維持
  - Claude Agent Runtimeは追加有料依存を採用しない判断で延期
- **Holo Addon**：Gate 0完了、Avatar統合済み、Conversation Runtime実装済み
  - Holo→Cursor / CodexのProvider ConversationはProvider native Session / Thread継続へ移行済み。通常Resident Cursorは別経路のCursor CLI `session_id` / `--resume`を使い、xhigh非fastを含むResident選択Model IDをそのまま維持する。Conversation transcriptは100件hot tailとは別にappend-only journalを保持し、Provider context喪失・失敗・Cancel・Core crash時はnative contextを無効化してNirai正本から再構築する
  - Holo↔Resident `talk`はTurn開始時のPublic Chat Sessionへ固定し、MasterのChat切替で発言と返答を分裂させない。Cursor Nirai-root read-only review stagingは`.env` / `.env.*`を物理除外する
- **通常Resident Conversation Continuity**：Cursor / Codex / Geminiはnative continuation + unseen delta + 静的Context再送削減まで実装済み。CursorはCLI `session_id` / `--resume`で`cursor-grok-4.6-xhigh`非fast Live 2-turn Smoke SAFE、GeminiもInteractions `previous_interaction_id` Live 2-turn Smoke SAFE。Codex LiveだけProvider利用枠回復後に再確認する
- **Whisper**：公開Chat Sessionへ従属しないResident単位Private Channelへ移行。Provider native Working Contextを優先継続し、Codex compaction検知後は同じThreadのままcontext delivery cacheだけrefreshする。独自の一定Turnローテーションは行わず、失効時だけNirai Memoryからrebuildする。5年分Raw Whisperを全投入しない
- **Memory Evaluation Harness v1**：PublicはGolden 11 CaseでFTS 7/11、BGE-M3 10/11、naive RRF 9/11、Gated Hybrid 10/11、Temporal Fact + Gated Hybrid 11/11。Gemini Embedding 2 follow-upも11/11 / false recall 0、Flash Lite Structured抽出Golden 7/7。PrivateはBGE-M3を比較Evidenceとして残すが、2026-09-07 Current ProductはGemini Embedding 2へ移行し、製品実装そのものをStructured Fact Overlayなしで8/8 / false recall 0。Public / Private Raw+FTSの250k Scaleも実測済み。Public Vector/Cloud長時間Scaleは未完了

## 5. 現在の主要Known Limitation

設計監査上、Phase 1完了後も継続して確認する対象：

1. Conversation Continuity：Cursor xhigh非fast / Gemini通常Resident Live 2-turnは実施済み。Codex Live SmokeはProvider利用枠回復後に再確認。将来Claudeを再採用する場合はその時点のnative continuationを再評価する
2. Private MemoryのStructured Continuity / Atomic FactはCurrent blockerではなくChallenger。Golden拡張でRaw + FTS + Gemini Semantic + Temporal ruleの不足が実測された場合だけProductへ昇格する
3. Public / Private Raw+FTSは250k Syntheticでsteady-state確認済み。Public sqlite-vec / Gemini IndexのCloud quota・長時間運用は実運用Evidenceとして継続観測する
4. Resident自身が作業途中に別Residentへ担当移管・追加Councilを組み立てるDelegation Orchestratorは将来拡張。Direct TaskとMaster明示Councilは現行で成立済み
5. World Observation / Natural Idle / Brain生活ティックはWorld Replacement後のM3工程で接続する
6. DNA → UE4.27 Private World Addonは未着手。次工程でReference-First feasibility調査と最小Spikeを行う

Phase 1で解消済みのGlobal Brain Lock、Agent全体1件制約、自然文Direct Task入口、Core crash後Recovery、Chat UI全履歴scanはKnown Limitationへ戻さない。

## 6. 作業別の読むもの

### Core / Conversation / Brain

1. `Docs/Nirai_基本設計.md`
2. `Docs/Nirai_設計ガバナンス.md`
3. `Docs/詳細設計/02_Core.md`
4. `Docs/詳細設計/03_Brainドライバ.md`
5. Whisper / 長期Conversationなら`Docs/Nirai_Reference-First調査_Whisper長期Conversation_2026-09-06.md`
6. 必要なProtocol / Memory章

### Memory / Retriever

1. `Docs/Nirai_基本設計.md`
2. `Docs/Nirai_設計ガバナンス.md`
3. `Docs/詳細設計/06_Residentと記憶.md`
4. `Docs/Nirai_ユースケース適合性監査_2026-09-06.md`
5. `Docs/Nirai_Reference-First調査_長期基盤_2026-09-06.md`
6. `Docs/Nirai_SerinaMemory監査_2026-09-06.md`
7. `Docs/Nirai_MemoryEvaluation設計_2026-09-06.md`
8. `Docs/Nirai_MemoryEvaluation_初回結果_2026-09-06.md`
9. `Docs/Nirai_MemoryScale_検証結果_2026-09-06.md`
10. `Docs/Nirai_PrivateMemorySemantic_検証結果_2026-09-06.md`
11. `Docs/M3_Retriever_検証結果.md`は現行実装Evidenceとして必要時だけ読む

Memoryの物理Storage・Embedding・Vector / Hybrid Retrieval等を大きく変更する前に、既存Reference-First調査とSerina監査を読み、その後の最新情報もWebで再確認する。2026-09-07時点のPublic World Memory CurrentはSQLite Raw + FTS5 + sqlite-vec + Gemini Embedding 2 + Structured Atomic/Current Fact + abstention-oriented Hybrid。Private CurrentはResident別SQLite Raw + Local FTS5 + sqlite-vec + Gemini Embedding 2で、Public / Privateは同じrolling 24h quota guardを共有する。Nirai RuntimeはローカルBGE-M3を使わず、Serina側Runtimeと分離する。どちらもInvariantではなく、Golden拡張・Scale・Provider条件が変われば再比較する。

### Task / Agent Runtime

1. `Docs/Nirai_基本設計.md`
2. `Docs/Nirai_設計ガバナンス.md`
3. `Docs/詳細設計/07_タスクと拡張.md`
4. `Docs/詳細設計/11_AgentRuntimeと実行UI.md`
5. Provider別検証結果は必要なAdapterを触る時だけ読む

### World / Avatar / UI

1. `Docs/Nirai_基本設計.md`
2. `Docs/Nirai_設計ガバナンス.md`
3. `Docs/詳細設計/08_マイルストーンと受入基準.md`
4. `Docs/詳細設計/04_World.md`
5. `Docs/詳細設計/05_会話パネル.md`
6. `Docs/詳細設計/09_3DビジュアルとAvatarパイプライン.md`
7. DNA / UE4.27 Trackなら`Docs/Nirai_DNA_UE4.27_WorldAddon方針_2026-09-06.md`

現行Three.js Worldは凍結中の配布可能標準World候補である。基盤完成前に追加Graphic工数を大量投入しない。AITuberKit / AIAvatarKitはReferenceとして利用できるが、実装開始時にWebで最新版と他候補も再調査する。

### Holo Addon

1. `Docs/Nirai_基本設計.md`
2. `Docs/Nirai_設計ガバナンス.md`
3. `Docs/詳細設計/12_HoloAddonとChatGPTDive.md`
4. 必要なら`Docs/Holo_Gate0検証結果.md` / `Docs/Holo_ConversationRuntime_検証結果.md`

## 7. AIの判断ルール

- Product Goal / Invariantと衝突する場合は実装を止めてMasterへ確認する
- Current Designより合理的・効率的・保守しやすい方式を見つけたら、旧設計を盲目的に実装しない
- 大規模変更ではReference-First調査 → 比較 → 必要ならgrill-me → 設計更新 → 実装の順に進む
- Product Goalを変えない局所実装詳細は合理的に判断してよい
- Master判断が必要な時は、選択肢・Trade-off・推奨案を示してgrill-me形式で質問する
- Verification / Historyを根拠にCurrent Designを古い方式へ巻き戻さない

## 8. 検証Baseline

2026-09-08 Followup敵対レビュー修正後の直近実測（[修正Evidence](Docs/Adversarial_Review_Followup_2026-09-08.md)）：

- Core pytest：**517 passed**（90秒実行上限のため315 + 202へ分割して全File完走、Failure 0）
- World Vitest：**39 files / 245 tests passed**
- TypeScript typecheck：成功
- Production Build：成功
- Doctor：**fatal=0 / warnings=1**（Claude current acceptanceで無効。Cursor / Codex / GeminiはOK）
- `git diff --check`：成功（既存のLF→CRLF warningのみ）

設計文書だけの変更ではTest件数を推測更新しない。Code変更時は実測結果を最新Evidenceへ記録する。

## 9. History

2026-09-06の設計ガバナンス再編前に肥大化していた旧AI_ENTRYは、履歴参照用に以下へ退避した。

- `Docs/history/AI_ENTRY_2026-09-06_pre-governance.md`

旧AI_ENTRYは現行仕様の正本ではない。
