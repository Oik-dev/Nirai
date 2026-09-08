# Nirai Phase 1 基盤完成 検証結果 — 2026-09-07

## 判定

**SAFE — Phase 1 Core基盤・自動回帰・必須実Provider受入完了**

2026-09-06のユースケース適合性監査でPhase 1に置いた「World差し替え前に固めるCore基盤」は、2026-09-07時点のCurrent Design・実装・自動回帰・必須実Provider Smokeまで第一完成相当へ到達した。通常Resident Gemini Interactions continuationは実Provider 2-turnで`previous_interaction_id`継続を確認し、Cursorも`cursor-grok-4.6-xhigh`非fastのCLI native `session_id` / `--resume` 2-turnを実機確認したため、Phase 1最終実機受入を`SAFE`とする。

この判定はDNA / UE4.27 World自体の成立を意味しない。Codex Live再確認は当日のProvider利用枠上限が解除された後の継続観測事項であり、Phase 1受入のBlockingには戻さない。

---

## Phase 1で閉じた主要Gap

### 1. Conversation Continuity

- Cursor / Codexの通常Resident会話はProvider native Session / Thread continuationを利用する。CursorはCLI `session_id` / `--resume`、Codexはapp-server `thread/resume`を使う
- 同一Conversationの後続Turnで固定件数のRaw Transcriptを毎回無条件再送しない
- Persona / Skills / 固定Contextの重複送信を抑制する
- Gemini Interactions `previous_interaction_id` continuationは実装・自動回帰・実Provider Live 2-turn Smokeまで通過済み
- Whisperは公開Chat Sessionから独立したResident単位Private Channelとし、Provider native Working Contextを優先する
- Global Brain Lockを撤去し、同一native Conversationだけをlogical conversation単位Lockで順序保証する

主な実装：

- `core/brains/native_conversation.py`
- `core/brains/talk_common.py`
- `core/brains/gemini.py`
- `core/conversation/runtime.py`
- `core/server.py`

関連Evidence：

- `ConversationContinuity_検証結果_2026-09-06.md`
- `Nirai_Reference-First調査_Whisper長期Conversation_2026-09-06.md`

### 2. Long-term Memory / Scaling

- Public World Memoryをlossless Raw SQLite + FTS5 + sqlite-vec + Gemini Embedding 2 + Structured Atomic/Current Factへ更新
- Private Whisper MemoryをResident別SQLite Raw + FTS5 + sqlite-vec + Gemini Embedding 2へ更新
- Public / Private ScopeをStorage / Retrievalで分離
- False Recallを優先的に避けるabstention-oriented Hybrid Recallを採用
- Public Golden 11 Case、Private Product Golden 8 Case、Structured Extraction Golden 7 Caseを成立
- Public / Private Raw+FTS 250k Synthetic steady-stateを確認
- Chat UI履歴の読み出しをJSONL全scan依存からSQLite派生Indexへ移行
- Embedding Model / Dimension変更時のvector-only rebuild queue / batch rebuildを追加
- Cloud停止・Quota枯渇時もRaw + Local FTSへ縮退し、会話の正本を失わない

関連Evidence：

- `Nirai_MemoryEvaluation_初回結果_2026-09-06.md`
- `Nirai_MemoryScale_検証結果_2026-09-06.md`
- `Nirai_PrivateMemorySemantic_検証結果_2026-09-06.md`

Public / Private Gemini Cloud quotaの年単位運用は今後も観測するが、Raw正本・Fallback・Rebuild経路が存在するためPhase 1 blockerにはしない。

### 3. Direct Task UX

- Focused Residentに対して`会話 / 仕事`をMasterが明示切替できる
- `仕事`時は自然文をそのまま指名ResidentへのDirect Taskとして送る
- `/task`は互換Shortcutとして残すが、日常利用の必須文法ではない
- 指名ResidentがあるTaskは全Resident Councilを通さない
- 指名Residentが実作業不能でも別Residentへ勝手に移管しない
- assigneeはQueue / Core restartを跨いで維持する
- resident無しTaskはMaster明示Council互換経路として維持する
- 通常Whisperを文字列heuristicだけで勝手にFile変更Taskへ昇格しない

関連Evidence：

- `Nirai_DirectTask_検証結果_2026-09-07.md`

### 4. Agent Resource-based Concurrency

旧M4の「Agent Session全体1件」Temporary制約を撤去した。

Current `AgentRuntimeManager`：

- 既定最大4 SessionのConcurrency Budget
- 独立WorkspaceはBudget内で並行可能
- 同一Workspaceはread-only同士のみ並行可能
- Writeは同一WorkspaceのReader / Writerと排他
- Session起動途中の予約枠とMaterialize済みSnapshotを二重カウントしない
- Resource競合はTask失敗ではなくQueue待機として扱う
- Queue先頭が競合中でも、後続の独立Workspace Taskは先行可能
- Approval / Question / Planは`agent_session_id + request_id`へ一意にroutingする
- Cursor staging / Codex Credential Home等のSession固有隔離は維持する

主な実装：

- `core/agents/manager.py`
- `core/task_queue.py`
- `core/server.py`

### 5. Agent Crash Recovery

Core restart等で未完了Agent Sessionが`interrupted`になった場合、Write Workを自動再開しない。

Snapshotの`recovery_options`からMasterが明示的に選択する。

- `resume`：Provider native Session / Thread IDが保存され、かつAdapterが`crash_resume` Capabilityを明示する場合だけ提示
- `rerun`：Nirai正本の`runtime/workspace/<task_id>/task.md`からfresh Sessionを開始
- `abandon`：元Sessionを`cancelled`へ確定し、run-state / status historyを残す

2026-09-07 CurrentではBuilt-in Adapterは`crash_resume`を宣言していないため、実ProviderのCore-crash Recovery UIは`rerun / abandon`を提示する。Provider IDがあるだけで`resume`を出す旧挙動は廃止した。

Recovery choiceはone-shotである。`resume / rerun`選択時はsource interrupted Sessionへchild Agent Session IDを先にdurable予約し、同じsourceからの逐次・並行二重Recoveryを拒否する。予約後にCore restartした場合はchild Snapshotが存在すればsourceを消費済み、child未生成なら予約を解除して再選択可能として復旧する。

World Protocol / Store / UIはProvider固有Decision名を知らず、Nirai共通`agent_session_recover` / `agent_session_recovery_result`で扱う。

主な実装：

- `core/agents/manager.py`
- `core/server.py`
- `world/src/renderer/src/protocol/types.ts`
- `world/src/renderer/src/protocol/parser.ts`
- `world/src/renderer/src/stores/agentStore.ts`
- `world/src/renderer/src/ui/AgentTaskPanel.tsx`
- `world/src/renderer/src/App.tsx`

### 6. Core / World Boundary

Phase 1最後の境界監査で、Core lifecycleから標準Electron Worldの起動詳細を分離した。

- `core/world_runtime.py`はRuntime-neutralな`WorldRuntimeProcess` / `WorldRuntimeLauncher` Protocolだけを持つ
- Electron / Three.js固有のProcess path、dev/prod起動、Windows process tree停止は`world/launcher.py::StandardWorldLauncher`へ隔離
- `core/__main__.py`はLauncher Contractの`launch(root, world_secret)` / `stop(process)`だけでlifecycleを調停する
- Core product codeから`Electron` / `Three.js`固有文字列を除去
- Resident Avatar参照はCoreでは`avatars/`配下のPresentation asset referenceとして検証し、`.vrm`拡張子をCore Contractへ固定しない
- Resident初期asset自動選択も「Resident名と同Stemの一意なasset」という形式非依存規則へ変更
- Coreが保持するWorld情報は意味Location / Action / Observation / Resident設定であり、Three.js座標、Camera、Shader値を正本にしない

これにより、新World RuntimeはCore / Memory / Conversation / TaskをForkせず、最小Launcher Adapterと既存WebSocket Protocol接続でFeasibility Spikeへ入れる。

---

## 2026-09-07 adversarial review follow-up

Phase 1完成判定後の敵対的レビューで、Failure Pathと長期運用境界に残っていた次を修正した。

1. Agent crash recoveryはProvider Session IDの存在だけで`resume`を提示せず、Adapterの明示`crash_resume` Capabilityを必須化した
2. Native ConversationはProvider応答後を`pending_turn`として記録し、Nirai側Response/markerの永続commit後だけ継続可能とする。Core crashでProvider cacheだけ先行した場合はcacheを破棄してNirai正本から再構築する
3. Private MemoryのTemporal QueryもGemini停止・Quota枯渇時にLocal FTSへ縮退し、十分強い候補が1件だけなら返す。曖昧な複数候補は0件へ倒す
4. Private Vector IndexもEmbedding Model / Dimensionをmetadata管理し、同一次元でもModelが変わればRawからvector-only rebuildする
5. Holo Provider Conversationは100件hot tailとは別にappend-only journalを保持する。Provider失敗・Cancel・interrupted・Core crashではnative contextを無効化し、次TurnをNirai journalから再構築する
6. Public Structured Recallは全Atomic Memoryを毎回走査せず、Raw Retriever候補に関係するFact familyだけをSQL取得する
7. Native Resident Conversationは同一logical Conversation lockをhistory delta選定前に取得し、Provider Turn → Nirai側Transcript / Chat / Private Memory commit → native marker commitまで保持する。最初のtransport/publication `await`より前にmarkerを確定し、Codex / Cursor / Geminiを同じcommit境界へ揃える
8. CursorのNirai-root read-only review stagingは`.env` / `.env.*`をSnapshot / copy対象から物理除外し、ProviderへのSecret露出をPrompt禁止だけへ依存させない
9. Holo ↔ Resident `talk`はTurn開始時Public Chat Sessionを固定し、MasterのChat切替でHolo発言とResident返答が別Sessionへ分裂しない。Turn中の対象Session削除 / World Memory forgetも拒否する
10. Agent Crash Recoveryはsource→child durable linkを用いたone-shot choiceへ変更し、逐次・並行二重実行とrestart境界を回帰化する

これらは通常経路の機能追加ではなく、「Provider/Cloud/Processが壊れた時にもNirai正本が上位である」という既存Invariantを実装へ揃えた修正である。

---

## 回帰検証

2026-09-07 adversarial review修正後の実測：

- Core pytest：**417 passed**
- World Vitest：**39 files / 222 tests passed**
- TypeScript typecheck：**成功**
- Electron production build：**成功**
- `git diff --check`：**成功**
  - stderrに既存working-copyのLF→CRLF warningは出るが、whitespace errorは0

追加で境界確認：

- `core/`内の`Electron`検索：product / test含め0件
- `core/`内の`Three.js`検索：product / test含め0件
- `.vrm`参照は既存標準World互換を確認するCore test fixtureには残るが、Core product logicのAvatar形式検証からは除去済み

---

## 残る受入保留 / 非Blocking事項

### 最終実機受入

- **Gemini通常Resident Live 2-turn Smoke：SAFE。** 1 Turn目の識別語を2 Turn目で再掲せず再現し、保存stateで`provider=gemini` / `pending_turn=false` / `last_seen_entry_id=cvseq:4`を確認した
- **Cursor通常Resident Live 2-turn Smoke：SAFE。** Cursor CLI `cursor-grok-4.6-xhigh`非fastで同一`session_id`を`--resume`し、2 Turn目で前Turn識別語を再現。`pending_turn=false` / `last_seen_entry_id=cvseq:4`も確認した

### Provider Constraint / 継続観測

- Codex Live Smoke：Provider利用枠回復後に再確認
- Claude Agent Runtime / native continuation：追加有料依存を再採用すると決めた場合のみ再評価
- Private Structured Continuity：Golden拡張でRaw Hybrid不足が確認された場合だけChallengerからProductへ昇格
- Gemini Cloud Vector quota / 長時間運用：実運用Evidenceを継続取得
- Resident自身が途中で追加Council / 担当移管するDelegation Orchestrator：将来拡張
- World Observation / Natural Idle / Brain生活ティック：Phase 2 World Replacement後のM3工程

---

## 次工程

**Phase 2：DNA → UE4.27 Private World Addon feasibility / migration**

順序：

1. Reference-FirstでDNA asset / scene抽出、UE4.27持込、Resident runtime、Core接続、利用条件を再調査
2. 最小Spike：`1 Scene + 1 Resident + Core接続`
3. FeasibleならMasterローカル専用Private World Addonとして移行
4. Not Feasible / Cost過大ならEvidenceを残して標準Three.js Worldまたは別候補へ戻る

DNA由来Asset / SceneはNirai本体・公開Repository・配布Packageへ含めない。
