# Nirai / Serina Memory 監査 2026-09-06

## 目的

Serinaの既存MemoryをNiraiへそのまま移植するのではなく、2026-09-06時点の最新研究・OSSと比較し、以下を分離する。

1. そのまま再利用価値が高い低層部
2. 思想・Patternだけ一般化してNiraiへ採る部分
3. Serina固有でNirai標準へ持ち込まない部分
4. 最新方式で補強すべき不足

詳細な外部Reference調査は `Nirai_Reference-First調査_長期基盤_2026-09-06.md` を参照する。

---

## 1. 総合判定

**Serina Memoryは古くて捨てる対象ではない。2026年夏時点でも低層の安全性・訂正・Forget・Background処理はかなり強い。**

ただし、Niraiの5年利用・複数Resident・World/Public/Private分離という用途では、そのままコピーするには不足がある。

特に不足するのは以下。

- Raw EvidenceとStructured Memoryを両方検索するHybrid Retrieval
- BM25 / FTS + Vectorの明示的Fusion
- Query type別Retrieverの統合
- Temporal Hierarchy / Progressive Consolidation
- Public / Private / Resident scopeを第一級概念にしたMemory Contract
- Memory hallucination / correction / abstention評価
- Cognitive Memory（価値観・暗黙制約）の評価
- 5年相当Scalingの実測

したがって、**SerinaをBaseline Aとして保持し、最新Referenceの強い部分を上乗せしたNirai MemoryをBenchmarkで選ぶ**。

---

## 2. そのまま再利用価値が高い低層Pattern

### A. Shared Ollama BGE-M3 Resource

Serina `core/memory/embedder.py`：

- Ollama `localhost:11434`
- `bge-m3`
- `num_gpu=0`
- `keep_alive=-1`
- CPU固定

NiraiでもMachine-level Embedding Resourceとして同じOllama daemonを共有する方向が有力。

ただしSerina moduleそのものをNiraiからimportして依存するのではなく、Nirai側に小さなEmbedding Adapter Contractを持ち、Serinaと同じMachine Serviceへ接続する。

### B. SQLite WAL / busy_timeout

Serina `MemoryStore` / `SessionStore`の

- SQLite
- WAL
- busy_timeout
- sqlite-vec

はSingle-PC常用基盤として合理的。

Niraiでも候補。ただしPublic / Privateの物理DB配置はNiraiのPrivacy Contractに合わせて分ける。

### C. Background Distillation Job

Serina `core/chores/distillation.py`の以下は強い。

- 会話中に重いMemory生成を完了させない
- Job化
- 冪等content hash
- retry
- failure count
- shelf
- checkpoint
- yield可能

NiraiのStructured Memory / Embedding生成も「Raw即時、派生はBackground」を基本にする価値が高い。

### D. Quote / Provenance Gate

Serina `memory_review.py`はLLM抽出Candidateについて原文quoteをSessionへ機械照合する。

これはHaluMemが問題視するMemory Extraction hallucinationへの有効な防御Pattern。

Niraiではより一般化して、Structured Memoryが必ず1件以上のRaw Entry ID / Event IDへprovenanceを持つContractとする。

### E. Temporal Fact Model

Serina `facts.py`の

- valid_from
- valid_to
- recorded_at
- supersedes
- active / hypothesis / superseded / tombstone
- category
- provenance episode_ids

はNiraiのCorrection semanticsと相性が良い。

Masterの確定判断「訂正はRawを消さず現行Factを差し替える」と整合する。

### F. Directed Forget / Change Log / Backup

Serinaの

- Forget Candidate
- tombstone
- physical delete
- destructive change前backup
- generation store
- change log

はNiraiへ強く参考にする。

ただしNiraiの明示Forgetでは、最終的に対象Raw / Structured / Indexへ一貫した削除を行うため、Serinaの「通常tombstone、物理削除は別指定」仕様をそのまま製品UXへ持ち込むとは限らない。

---

## 3. Patternだけ借り、Serina実装をそのままコピーしない部分

### Recall Activation

Serinaは relevance + importance + recency + grade + spreading + noise のActivationを持つ。

長所：
- 人間の想起らしい重み付け
- 単純Vector top-kよりResidentらしさを出せる

弱点：
- Lexical / Temporal / Entity等の別RetrieverとのFusionが一般化されていない
- noiseは再現性・評価を難しくする
- score weightがNirai全Residentに最適とは限らない

Niraiでは「候補生成」と「最終ranking」を分離し、Serina Activationはranking signal候補の1つとしてBenchmarkする。

### Rolling / Fine Summary

Serinaの会話Context圧縮としては有用だが、Provider native Session / Thread continuationを優先するNiraiでは役割が異なる。

NiraiではProvider Context消失時の再構成、Cross-provider handoff、Long-term Memory consolidationの材料として使う。毎Turnの標準会話継続手段にはしない。

### Preferences / Relationship Summary

Serina固有の2ブロック上限固定はNiraiへそのまま持ち込まない。

ただし「常時必要な継続状態」と「検索で出す詳細」を分ける思想は採用する。

---

## 4. Nirai標準へ持ち込まないSerina固有部

- `master / serina`固定speaker語彙
- Serina固有Persona保護等級
- Serina Day 07:00境界
- Diary固有cascade semantics
- Serina一人称でMemory contentを書き直す規則
- 単一Persona前提のSummary block構造
- SerinaのLegacy import / inherited canonical memory規則
- `G:\SerinaDB Backup`等のSerina運用Path
- Serina用Ollama Brainと同じ生活周期・Chore policy

これらはSerina Memory Adapter内部へ残す。

---

## 5. 最新ReferenceからNiraiへ追加候補

### PropMem

採る候補：
- Raw chunk + Atomic Proposition二本立て
- Entity scope
- BM25 + Vector Hybrid
- Structuredが外してもRawへ戻れる構造

### TiMem

採る候補：
- Temporal Hierarchy
- Query complexityごとの参照Level選択
- Raw -> Atomic -> Episode/Scenario -> Durable Continuityの段階化

### ReMe

採る候補：
- Source / Working(Daily) / Durable(Digest) / Derived Index責務分離
- provenanceを人間が追える構造
- background consolidation / dream

NiraiはDB正本を許容するため「全部Markdown正本」までは採らない。

### Hindsight

採る候補：
- semantic / lexical / temporal / graph(or link)の独立Retriever
- RRF等のFusion
- rerankを独立optional stageにする
- recall（検索）とreflect（深い推論）を分離

### TencentDB Agent Memory

採る候補：
- L0 Conversation -> L1 Atom -> L2 Scenario -> L3 Persona
- Progressive Disclosure
- recall timeout時に会話を止めない
- local fallback
- lower evidence / upper readable abstraction分離

Framework全体はTeam Memory HubとしてNiraiに過剰。

---

## 6. 重大な注意：Fact supersedeの誤爆

Serinaは同一subjectのFactをBGE-M3 similarity閾値で比較し、近ければsupersedeする。

これは2026年時点のMemory hallucination研究を踏まえると、Niraiではそのまま採らない。

Niraiの自動Supersede候補は少なくとも以下を組み合わせる。

- same privacy scope
- same subject/entity
- compatible predicate/category
- provenanceあり
- explicit correction signalまたは高confidence contradiction/update
- time ordering
- semantic similarity

不確実なら旧Factを消さず、新Factをhypothesis / parallel factとして保持する。

---

## 7. Nirai Memory Contractへ必要な抽象

```text
MemoryOwner
  - nirai-world
  - nirai-resident:<id>
  - external-resident:<id>  # Serina等

MemoryScope
  - public
  - private:<resident_id>

RawEntry
  - immutable/lossless except explicit Forget
  - participant / timestamp / conversation / source ids

AtomicMemory
  - proposition/fact/preference/promise/relationship
  - provenance RawEntry IDs
  - temporal validity

EpisodeScenario
  - event/conversation/period grouping
  - provenance

DurableContinuity
  - current preference / relationship / unresolved / stable identity-adjacent state

DerivedIndex
  - FTS/BM25
  - vector
  - optional links/graph
```

Serina Residentは`MemoryOwner=external-resident:serina`としてPrivate Memory Adapterを差し替えられるようにする。

---

## 8. 実装前Evaluation Harness

Serinaを超えたかは同一条件で測る。

### Baseline A
Serina方式相当。

### Baseline B
A + FTS/BM25 + Raw retrieval + Fusion。

### Baseline C
PropMem型Atomic Proposition + Entity/Participant scope。

### Baseline D
C + Temporal Hierarchy / Episode / Durable Continuity。

### 必須評価

- Fact recall
- Exact noun / number / code-like token recall
- Multi-session
- Temporal
- Correction / supersede
- Contradiction
- Abstention
- Preference
- Relationship
- Cognitive latent constraint
- Forget physical purge
- Private leak = 0
- Resident pre-birth World History vs own experience distinction
- latency
- token count
- BGE-M3 CPU queue latency
- failure fallback
- 5-year synthetic scale

外部BenchmarkはLongMemEval / HaluMem / LoCoMo-Plusに加え、MemoryAgentBenchのAccurate Retrieval / Test-Time Learning / Long-Range Understanding / Conflict Resolution・Selective Forgetting系能力を参考にし、日本語Nirai Golden Setを正本評価に加える。

### 第二波Referenceから追加する比較観点

- **agentmemory V4**：6-signal hybrid、HNSW、rerank、temporal grounding、graph。強力なretrieval cheat sheetだが各signalをablationする
- **Chronos**：Raw Turn Calendar + Structured Event Calendar、query-specific temporal/multi-hop retrieval guidance
- **Mastra Observational Memory**：Observer / Reflector background consolidationとstable context。Raw正本はNirai側で保持する
- **CortexDB**：immutable WAL、async extraction、bi-temporal facts、scope、selective forget。Closed/source-available依存は採らずPatternのみ参照
- **MemTxn**：source-supported Memory mutation、conflict resolution、snapshot recovery
- **ScrubJay-MEM**：type-conditioned temporal decay。物理忘却ではなくtransient memory ranking signal候補

---

## 9. 次工程

1. `06_Residentと記憶.md`へ上記Memory ContractのActive Designを反映
2. `Nirai_MemoryEvaluation設計_2026-09-06.md`でData Contract / Golden Case / 採点条件を固定
3. Baseline A〜Dを最小Sliceで比較
4. agentmemory / Chronos / OM等の強い特徴は、A〜Dで不足が残るCaseへだけ追加ablationする
5. Correction / Forget MutationはMemTxn型のsource-supported transaction思想も比較する
6. 最も単純で十分な構成を採用
7. その後にNirai本体へ移行

**性能が証明される前にCross-Encoder、Graph DB、大型Memory Frameworkを追加しない。**
