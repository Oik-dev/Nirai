# Nirai Memory Evaluation 設計 2026-09-06

## 0. 目的

本書は、Niraiの長期Memory方式を「新しそう」「高機能そう」「既存設計だから」という理由で決めず、**同一条件の実測で最も単純かつ十分な方式を選ぶためのEvaluation Contract**を定義する。

Reference：

- `Nirai_Reference-First調査_長期基盤_2026-09-06.md`
- `Nirai_SerinaMemory監査_2026-09-06.md`
- `詳細設計/06_Residentと記憶.md`

本書は実装Algorithmの正本ではない。比較条件と合否条件を固定する。

### 2026-09-06 実行状況

Evaluation Harness v1とRetrieval中心の初回Ablationは実施済み。結果は`Nirai_MemoryEvaluation_初回結果_2026-09-06.md`をEvidenceとする。

- FTS5：7/11
- Temporal Fact + FTS5：8/11
- BGE-M3 Semantic：10/11
- naive FTS + BGE RRF：9/11
- abstention-oriented Gated Hybrid：10/11
- Temporal Fact + Gated Hybrid：11/11

この11 Case結果は方式分解用であり、単独で製品方式の採用判定にはしない。同日中にPublic Structured extraction / Mutation integrity / Explicit Forget、Private Resident別Raw+FTS+BGE-M3 Semantic、Public/Private Raw+FTS 250k Syntheticまで後続評価・製品化した。現在の次GateはPrivate Structured Continuity / temporal Current Fact、Public Vector/Cloud長時間maintenance、Golden拡張である。Similarity threshold等の数値も現GoldenでのCurrent候補値に過ぎず、本書のInvariantにはしない。

---

## 1. 比較原則

比較するBaseline間で、Memory方式以外の差を可能な限り固定する。

固定するもの：

- 同一Input conversation / event data
- 同一Question / expected answer
- 同一Brain Modelまたは同一Judge条件
- Semantic Retrievalを使うBaselineでは同一Embedding Model
- 同一Top-K / Context token budgetを基本とし、方式固有差が必要なら記録する
- 同一Hardware
- Cold / Warm条件
- Public / Private scope

各Baselineについて必ず記録する。

- build時間
- background処理時間
- storage size
- query latency p50 / p95
- context token量
- BGE-M3 request数 / queue待ち
- LLM追加call数
- correctness
- false recall / abstention
- update / correction / forget整合性

「正答率が少し高いが、Memory buildに桁違いのTokenや常駐Modelを要求する」方式を自動的に勝者にしない。

---

## 2. Baseline

### Baseline A：Serina方式相当

目的：既にPC上で成立している設計を基準線にする。

- Structured Memory / Fact
- BGE-M3 Vector Retrieval
- importance / recency等のranking signal
- temporal / entity fact query
- supersede / hypothesis

NiraiへSerina codeをそのままcopyすることを意味しない。評価上の挙動Baselineとする。

### Baseline B：Raw + Structured Hybrid

Aへ以下を追加する。

- Raw conversation / eventの検索
- SQLite FTS5 / BM25
- Structured Memory / FactのFTS
- Vector + LexicalのFusion
- Structured抽出に失敗してもRaw Evidenceへ戻れる

Fusionは最初はRRF等、単純で説明可能な方式を比較起点にする。ただし初回Ablationではnaive RRFがSemantic-onlyよりNear-miss false recallを増やした。したがってFusion自体を価値とみなさず、weak lexical hitを棄却できるabstention / confidence gateを含めて比較する。

### Baseline C：Atomic Proposition + Scope

Bへ以下を追加する。

- RawからAtomic Memory / Propositionを抽出
- entity / participant / privacy scope
- provenance Raw Entry IDs
- fact / promise / preference / relationship等のtype
- temporal validity

PropMem等のReference思想を比較対象にする。

### Baseline D：Temporal Hierarchy

Cへ以下を追加する。

- Episode / Scenario
- Durable Continuity
- Raw -> Atomic -> Episode/Scenario -> Durable Continuityの階層
- Query type / complexityに応じたLevel選択
- Background consolidation

TiMem / ReMe / Tencent系のProgressive Memory思想を比較対象にする。

### Reference Challenger Ablation

A〜Dで不足が残るCaseだけ、以下を一要素ずつ追加して効果を測る。

- agentmemory型：graph signal / Cross-Encoder rerank / HNSW候補生成
- Chronos型：Structured Event Calendar / query-specific iterative temporal retrieval
- Mastra OM型：Observer / Reflectorによるdense Durable Continuity
- MemTxn型：source-supported mutation validation / conflict resolver / recovery journal
- ScrubJay型：transient memoryのtype-conditioned temporal ranking decay

これらを最初から全部載せた「全部入りBaseline」は作らない。どの要素が効いたか分からなくなるためである。

---

## 3. Fixture Data Contract

各Raw Entryは最低限以下を持つ。

```json
{
  "entry_id": "raw-0001",
  "scope": "public | private:<resident_id>",
  "conversation_id": "conv-...",
  "speaker_id": "master | resident:<id>",
  "participants": ["master", "resident:a"],
  "occurred_at": "ISO8601",
  "text": "原文",
  "event_type": "utterance | world_event | task_result",
  "resident_experience": ["resident:a"]
}
```

`resident_experience`は「そのResidentが実際にその場へ参加・知覚したか」を表す。新規Residentが誕生前World Historyを参照できても、自分の経験と誤認させないTestに利用する。

Question Fixture：

```json
{
  "case_id": "correction-001",
  "query_scope": "private:resident-a",
  "query_time": "ISO8601",
  "question": "...",
  "expected": {
    "answer": "...",
    "must_include_evidence": ["raw-0012"],
    "must_not_use": ["raw-0900"],
    "should_abstain": false
  },
  "tags": ["correction", "temporal"]
}
```

文字列完全一致だけで採点できないCaseはJudgeを使ってよいが、Privacy / Forget / Evidence ID等、機械判定可能な項目は機械判定を優先する。

---

## 4. 必須Case群

Case tagはLongMemEvalだけへ依存せず、MemoryAgentBenchの能力分解も併用する。

- `accurate_retrieval`
- `test_time_learning`
- `long_range_understanding`
- `conflict_resolution`
- `selective_forgetting`

さらにHaluMem由来で`extraction_integrity / update_integrity`、LoCoMo-Plus由来で`cognitive_memory`を付与できるようにする。


### 4.1 Exact Recall

- 人名
- Project名
- 型番
- 数値
- 日付
- code symbol
- URL断片等

Semantic検索だけでは落としやすいものを含む。

### 4.2 Semantic Recall

語彙が一致しなくても同じ意味の過去を引けるか。

### 4.3 Multi-session

数週間〜数か月離れた複数Sessionを跨ぐ情報統合。

### 4.4 Temporal

- 昨日 / 先月 / 当時
- Xより前 / 後
- 現在値と過去値
- 予定の有効期間

### 4.5 Correction / Supersede

例：

1. 「好きな色は赤」
2. 数週間後「前に赤って言ったけど今は青」

期待：

- Raw 1は残る
- current answerは青
- historical queryなら赤も返せる
- 無関係な同一subject Factをsupersedeしない

### 4.6 Contradiction / Uncertain Update

曖昧な発言を勝手に確定Factへ上書きしない。

例：

- 「転職するかも」
- 「会社辞めた」

前者だけで「退職済み」にしない。

### 4.7 Abstention / False Recall

Memoryに存在しない情報を問う。

期待：

- 推測でMemoryを捏造しない
- 「覚えていない / 記録がない」を許容

### 4.8 Preference / Relationship Continuity

直接Fact語彙を繰り返さないSituationでも過去の好み・関係性を適切に反映できるか。

### 4.9 Cognitive Latent Constraint

LoCoMo-Plus型。

過去に明示された価値観・目標・制約を、後の別文脈で適用できるか。

### 4.10 Forget

明示Forget後に以下すべてから再出現しないこと。

- Raw
- Atomic / Fact
- Episode / Scenario
- Durable Continuity
- FTS
- Vector
- Cache
- Prompt Context

Forget対象と無関係なMemoryは残る。

### 4.11 Privacy

Private Aを以下から絶対に取得しない。

- Resident B Whisper
- Say
- World Retriever
- Resident同士公開Conversation
- BのBackground life tick

**Cross-contamination許容件数 = 0。**

### 4.12 Pre-birth World History

Resident B入居前にWorldで起きたEventをBが世界史として答えられる。

ただし、

- 「わたしもその場にいた」
- 「わたしがMasterとその話をした」

とは答えない。

### 4.13 Extraction Hallucination / Update Integrity

Rawに無いFactをStructured Memory生成時に捏造しない。

Atomic / Factは必ずprovenanceを持ち、引用・source entryと矛盾しない。

Updateでも以下を機械確認する。

- source-supportedか
- old/newの対象Entity / Predicate / Scopeが一致するか
- supersedeすべきでないFactを隠していないか
- crash / retry後もdeclared active stateが一意に復元できるか

MemTxn型のMutation validationをChallengerとして比較する。

### 4.14 Failure / Degradation

- Ollama停止
- BGE-M3 timeout
- Vector index破損 / 未生成
- Background consolidation失敗
- Partial index lag

期待：

- Raw保存は失わない
- 会話が不要に停止しない
- FTS / Structured state等へ縮退できる
- 後から派生Indexを再生成できる

---

## 5. 5年Synthetic Scale

### 目的

少数Memoryで速いだけの方式を採用しない。

最低3段階を用意する。

- Small：実開発中の軽量Fixture
- Medium：数か月相当
- Five-Year：5年日常利用想定

件数は実際のNirai利用率の計測後に補正する。初期Syntheticでは少なくとも、Conversation / Event / Atomic / Episodeが数十万件規模まで増える場合を想定したStressを行う。

計測：

- startup time
- append latency
- query p50 / p95
- DB size
- index size
- rebuild time
- backup time
- Forget cascade time
- background backlog
- CPU / RAM

全件scan、全体rewrite、全Embedding再生成が通常経路へ入っていないことを確認する。

---

## 6. BGE-M3 Resource Test

SerinaとNiraiが同じMachine-level Ollama `bge-m3`を共有する想定を検証する。

Test：

1. Serina相当Embedding request連続
2. Nirai recall query同時投入
3. Nirai background indexing同時投入
4. 会話Brain / World描画中

確認：

- `num_gpu=0`でVRAMを奪わない
- Queue待ち時間
- p95 query latency
- Background jobがinteractive recallを飢餓させない
- timeout時にNirai conversationが継続する

必要ならApplication側でPriority Queueを設けるが、実測前に独自Schedulerを作らない。

---

## 7. Benchmark解釈ルール

外部Leaderboardの順位は採用判定に直接使わない。

2026年のLongMemEval公開値は、Generator、Judge、oracle access、retrieval-only / end-to-end、ensemble、context budgetがSystemごとに異なる。例えばagentmemory、Chronos、Mastra OM、CortexDB、Supermemory等は非常に有力なReferenceだが、公開score同士をそのまま順位表として扱えない。

外部scoreは「何が効きそうか」を探すSignalとしてのみ利用し、Nirai採用判断は本Harnessの統一条件で行う。

---

## 8. 採用判定

方式を採用する優先順位：

1. Privacy / Forget / Correction等のInvariantを満たす
2. 必須Caseの正確性
3. false recall / hallucinationの低さ
4. 5年Scaling
5. conversation latency
6. token / Brain call / CPU / RAM cost
7. 実装・保守Complexity

複雑なBaselineが単純Baselineを僅差でしか上回らない場合、**単純な方を採用する**。

Cross-Encoder reranker、Graph DB、追加常駐Model、大型Memory Frameworkは、明確なBenchmark改善がある場合だけ採用候補へ昇格する。

---

## 9. 実装順

1. Golden Fixture / scorer
2. Baseline A Adapter
3. Baseline B
4. Bが明確に不足するCaseだけCへ進む
5. Cが長期時間・関係Continuityで不足する場合Dへ進む
6. 最小十分Baselineを選定
7. Active Designを確定
8. Nirai本体へ実装・Migration

A〜Dすべてを最初から製品実装することは禁止する。Evaluation Sliceだけを作り、勝者だけを製品化する。
