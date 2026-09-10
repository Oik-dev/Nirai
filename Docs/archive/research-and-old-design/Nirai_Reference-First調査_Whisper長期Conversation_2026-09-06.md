# Nirai Reference-First調査：Whisper長期Conversation 2026-09-06

## 0. 目的

Whisperを5年以上継続する際に、Provider native ContextへRaw履歴を無制限に積み上げる、またはNirai独自のSessionローテーション機構を先に発明することを避ける。

調査対象は以下。

1. 長寿命Conversation IdentityとWorking Contextの分離
2. Provider native continuation / compaction
3. compaction後の静的Context再注入
4. 長期Memoryと会話履歴の責務分離
5. 同一Memory Evidenceの重複注入抑制

本書はReference / Evidenceであり、Active Designは`詳細設計/03_Brainドライバ.md`と`06_Residentと記憶.md`を正とする。

---

## 1. 主要Reference

### 1.1 OpenAI Codex app-server

- https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md
- https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/src/protocol/common.rs
- https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/src/protocol/v2/thread.rs

確認事項：

- Conversation primitiveはThreadであり、`thread/resume`で同じThreadを継続する。
- `thread/compact/start`が正式に存在する。
- compactionは同じThread ID上で行われ、`item/started` / `item/completed`の`type=contextCompaction`として通知される。
- 自動compactionも発生し得る。
- `thread/resume`は`baseInstructions` / `developerInstructions`等のoverrideを受けられる。
- 過去にはresume直後のdeveloperInstructions更新が最初のTurnへ反映されないIssueも報告されており、Provider固有挙動をNirai Identityの正本へしない方針は妥当。

Niraiへの採用：

- WhisperのCodex Thread IDをcompactionのたびに捨てない。
- `contextCompaction`を検知したら、同じThreadを維持したまま「何が残ったか分からないProvider Working Context」とみなし、次TurnでNirai-owned static context / 必要Memoryを再注入可能にする。
- 独自のTurn数閾値によるThreadローテーションは現時点で導入しない。

### 1.2 Cursor ACP / CLI

- https://cursor.com/docs/cli/acp

確認事項：

- ACPは`session/new` / `session/load`でConversation continuationを提供する。
- ACP公開仕様上、Niraiが利用できる明示compaction notification / APIは確認できない。
- Cursor CLI自体にはConversation要約機能があるが、ACP統合から同じ制御を安全に呼べるとは限らない。

Niraiへの採用：

- Whisperは同一Cursor Sessionを利用できる間は継続する。
- Provider内部compactionをNirai側で推測して独自同期しない。
- `session/load`失敗等、native Contextが実際に利用不能になった場合だけNirai Memoryからrebuildする。

### 1.3 Gemini Interactions

- https://ai.google.dev/gemini-api/docs/interactions-overview

確認事項：

- `previous_interaction_id`でServer-side Conversation stateを継続できる。
- 保持期間が有限であり、長期Memory正本にはできない。

Niraiへの採用：

- 有効期間内はnative continuationを使う。
- Interaction失効時はPrivate Channel Identityを維持し、Nirai MemoryからWorking Contextだけ再構成する。

### 1.4 OpenAI Agents SDK Session / Compaction

- https://openai.github.io/openai-agents-js/guides/sessions/
- https://openai.github.io/openai-agents-js/openai/agents/interfaces/session/

確認事項：

- Session identityとStored Historyは分離される。
- `OpenAIResponsesCompactionSession`は長くなったhistoryを短い等価履歴へ置換する。
- `replaceHistoryWithCompaction()`はSession identityをresetせず、論理Sessionの履歴だけをcompaction resultへ置換できる。
- Server-managed stateを既に使う場合、同じConversation historyを別Session mechanismで二重管理する必要はない。

Niraiへの採用：

- 「同じWhisper Private Channelを保ったままWorking Contextだけ圧縮・再構成する」方針を支持するReferenceとして利用する。
- Provider native continuationとNirai Raw履歴を二重に毎Turn再送しない。

### 1.5 Letta

- https://docs.letta.com/

確認事項：

- Agent identity / memoryをModelから分離する。
- Active contextと外部長期Memoryを分離する。
- 長寿命AgentはModel Context WindowそのものをIdentityやMemory正本にしない。

Niraiへの採用：

- Resident Identity / Private Channel IdentityをProvider Sessionから独立させる。

### 1.6 Mastra Observational Memory

- https://mastra.ai/research/observational-memory

確認事項：

- active message historyが増えると、Raw messageをdated observationへ段階的に置換する。
- Memory block + active message historyを分離し、Context量を安定化する。
- Raw Conversationを永久にmodel contextへ載せ続けない。

Niraiへの採用：

- 将来のStructured Private Continuity / Background consolidationのReferenceとする。
- ただしNiraiではRaw Durable Sourceを削除せず、Observation / Summaryは派生扱いとする。

---

## 2. 比較結果

### 案A：Whisperごとに公開Chat Sessionへ従属

不採用。

- UX上は同じResidentとのDMなのに、公開Chat Session切替でPrivate Contextまで分断される。
- Private Memoryの長期Identityと表示上の体験が一致しない。

### 案B：Residentごとに永続Private Channelを持ち、一定TurnごとにNirai独自Sessionをローテーション

現時点では不採用。

- Provider側にnative continuation / compactionがあるのに、Nirai独自閾値を先に作る再発明になる。
- ProviderごとのContext Window・compaction方式・保持期限をNiraiが二重管理することになる。

### 案C：Residentごとに永続Private Channel、Provider native Working Contextを利用し、compaction /失効時のみrefresh・rebuild

**採用。**

```text
Whisper Private Channel     # Nirai-owned long-lived identity
  Master <-> Resident
        |
        +-- Private Raw Durable Source
        +-- Structured Continuity / Retrieval
        |
        +-- Provider Working Context
              Cursor Session
              Codex Thread
              Gemini Interaction chain
```

Provider Working Contextは消耗品だが、Niraiが勝手に一定Turnで捨てる対象ではない。

---

## 3. Active Contractへ反映する原則

1. Whisper Private ChannelはResident単位で1本。
2. 公開Chat Session切替でWhisper identityを切らない。
3. 通常TurnはProvider native continuation + 新規Whisper差分を使う。
4. 既送Raw履歴・Persona・Skills・同一Memory Evidenceを毎Turn再送しない。
5. Provider native compactionを利用できる場合はProviderへ任せる。
6. compactionを検知できるProviderでは、同じnative IDを維持しつつNirai側delivery cacheを無効化し、次Turnで必要Contextをrefreshする。
7. compactionを検知できないProviderでは推測ローテーションせず、resume/load/interaction失効等の実Failure時にrebuildする。
8. rebuild時も5年分Rawを全投入しない。Structured Continuity + Retrieval + bounded recent rawを利用する。
9. Provider Working ContextはPrivate Memoryの正本ではない。
10. 同一World/Private Memory Evidenceを一度native contextへ注入した後は、同じ内容を毎Turn再注入しない。内容変更・Core再起動・context reset/compaction後は再注入可能にする。

---

## 4. 現行実装への適用

2026-09-06 Sliceで以下を実装する。

- Whisper logical id：`private:whisper:<resident>`
- Public Chat Sessionを跨ぐWhisper delta取得
- Cursor / Codex / Gemini native continuation
- 静的Contextの通常Turn再送抑制
- World Memory Contextの同一Evidence再注入抑制
- Codex `contextCompaction`通知検知
- Codex compaction後もThread IDを維持し、static/memory delivery cacheだけ無効化
- Cursor / GeminiはProvider-native Context失効時にrebuild

現行`context.md`は直近Rawから作った暫定ViewでStructured Continuityではないため、「重要な長期状態」の正本として信頼しない。

2026-09-06後続SliceでPrivate RawをResident別SQLiteへ移行し、当初はLocal FTS5 + shared CPU Ollama BGE-M3で実Whisper Semantic Recallを成立させた。これは比較Evidenceとして保持する。その後2026-09-07にMasterが「Holo / Serinaは別軸Addonであり、Nirai Private WhisperもGemini Free利用可」と判断したため、Current ProductはLocal FTS5 + Gemini Embedding 2へ移行した。Product Golden 8/8 / false recall 0、Live Smoke exit 0。Public / Privateは同じEmbedding 2 rolling 24h quota guardを共有し、Nirai RuntimeはローカルBGE-M3を使わない。詳細は`Nirai_PrivateMemorySemantic_検証結果_2026-09-06.md`。

---

## 5. 未実装 / 後続

- Structured Private Continuity / temporal Current Fact
- Private Structured層まで含むCorrection / Forget
- Public Vector/Cloudを含む年単位Index maintenance
- Provider別Context使用量の可観測性
- Cursor ACPで将来compaction signal/APIが追加された場合のAdapter対応
- Codex manual compactionをNiraiから明示的に起動する必要性の実測。Provider auto-compactionで十分なら追加しない

---

## 6. 結論

WhisperはNirai独自のSessionローテーション機構を主役にしない。

**永続Private Channel + Provider native Working Context + Provider-native compaction + Nirai long-term Memory**という、既存のStateful Agent / Session / Compaction設計に沿う。

Niraiが独自に持つのは、Public/Private境界、Resident Identity、Memory正本、Provider差異を吸収する薄いAdapter層だけとする。
