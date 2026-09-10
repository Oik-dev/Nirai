# Nirai Memory Evaluation 初回結果 2026-09-06

## 判定

**Evaluation Harness v1 SAFE / Public World Memory Product Slice SAFE / Private Raw+FTS+BGE Semantic Slice SAFE / Raw+FTS 250k Scale SAFE**

Golden Fixture / scorer / Lexical / BGE-M3 Semantic / Hybrid / abstention gate / Temporal Fact overlayの最小Ablationを実装し、同一11 Caseで比較した。

本書前半のAblationは初回Retrieval方式分解のEvidenceである。この時点ではStructured FactにGolden FixtureのOracle Factを使っていた。その後同日中にGemini Structured Extraction / Gemini Embedding / Mutation / Forgetを追加評価し、Public World Memoryの第一製品Sliceまで実装した。さらにPrivateはResident別Raw+FTS+BGE-M3 Semantic第一Slice、Public/Private Raw+FTSは250k Synthetic steady-stateまで検証した。Private Structured ContinuityとPublic Vector/Cloud長時間運用は未確定のため、Nirai Memory全体を最終確定したとは扱わない。

## Reference-First再確認

2026-09-06に既存の長期Memory調査・Serina監査を再確認し、追加で長寿命Conversation / Memoryの現行Referenceを確認した。

- Serina Memory Store / BGE-M3 / Temporal Fact / Recall Planner
- Letta stateful agent / model-independent identity and memory
- Mastra Observational Memory
  - long-running threadのRaw Historyをdense observationへbackground圧縮
  - observation自身のToken肥大化に対するbudget制御
  - as-of observation取得による「その時点で何を知っていたか」の再現
- LangGraph系のthread-scoped working state / cross-thread long-term store分離

NiraiではFramework丸ごと導入ではなく、既存PatternをEvaluationでAblationする方針を維持する。

## 実装したEvaluation専用Slice

製品Memoryとは分離して以下を追加した。

- `core/memory/evaluation.py`
  - Golden Fixture loader
  - machine scorer
  - SQLite FTS5 Lexical baseline
  - Ollama BGE-M3 adapter
  - Semantic baseline
  - naive RRF Hybrid
  - abstention-oriented Gated Hybrid
  - conservative Temporal Fact overlay
  - BGE-M3停止時のFTS fallback
  - 同一Run内Embedding cache
- `core/tests/memory_eval_golden_v1.json`
- `core/tests/test_memory_evaluation.py`
- `.tools/run-memory-eval.py`
- `.tools/run-memory-eval.ps1`

Evaluation codeは製品Private Memory / World Memoryの正本を変更しない。

## Golden v1

11 Case。

- Exact code / 型番
- 真のSemantic paraphrase
- current correction
- historical correction
- promise / temporal
- Private A→B cross-scope leak
- Private→World leak
- public semantic multi-session
- pre-birth public history retrieval
- near-miss false recall
- unknown-memory abstention

特にNear-missは、

> 「海を眺めた日の駐車料金はいくらだった？」

に対し、実Memoryには「海を見ると落ち着く」というPreferenceしか存在しないCaseを入れた。表面語彙の一致だけで無関係な記憶を返す方式を失敗扱いにする。

## Ablation結果

| Baseline | Passed | False recall case | 意味 |
|---|---:|---:|---|
| FTS5 Lexical only | 7 / 11 | 2 | Exactは強いがSemantic paraphraseを落とし、Near-missを誤想起し、Correction currentを解けない |
| Temporal Fact + FTS5 | 8 / 11 | 1 | Correction currentは解けるがSemantic / Near-miss問題は残る |
| BGE-M3 Semantic only | 10 / 11 | 1 | Semantic paraphraseとNear-miss abstentionは解けるがCorrection currentは解けない |
| naive FTS + BGE RRF | 9 / 11 | 2 | FTSの弱い誤HitをFusionが復活させ、Semantic-onlyより悪化 |
| Gated Hybrid | 10 / 11 | 1 | Semantic支持、強いLexical coverage、distinctive exact anchorだけを許しNear-missを抑止。Correctionは未解決 |
| **Temporal Fact + Gated Hybrid** | **11 / 11** | **0** | 現Goldenでは全Case成立 |

### 実機BGE-M3

既存Machine-level Ollama `bge-m3`を使用。

- `num_gpu=0`
- `keep_alive=-1`
- Nirai専用Model Process / Model copyは作成していない

Semantic-only単独実行：

- 10 / 11
- p50 query latency 約225.5 ms
- p95 約248.7 ms

最終候補 `Temporal Fact + Gated Hybrid` 単独実行、similarity floor 0.50：

- **11 / 11**
- false recall 0
- p50 query latency **226.6 ms**
- p95 **242.0 ms**
- mean returned context chars 23.9

FTS-onlyはsub-msだが、BGE-M3をinteractive recallへ使っても現Small Fixtureでは約0.2秒台であり、会話前段として現実的な範囲だった。

## 同日Follow-up：Gemini Free Tier候補の実測

SerinaとNiraiが同じCPU BGE-M3を常時取り合う構成を避けられるか確認するため、`gemini-embedding-2`を同Goldenへ追加した。

- Document / Query task typeを`RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY`へ分離
- output dimensionality：768
- BGE用floor 0.50は流用せず、Gemini用にSweep
- similarity floor 0.70でSemantic-only **10 / 11**
- Temporal Fact + Gated Hybrid **11 / 11 / false recall 0**
- Query latency実測：p50約596 ms / p95約1.82 sec

Gemini側のSimilarity尺度はBGE-M3と異なるため、Embedding Model交換時に閾値を共有しない。

Structured抽出は`gemini-3.5-flash-lite`で別Golden 7 Caseを実測した。初回5/7から、subject誤認・値の勝手な英訳・Promise粒度・曖昧計画分類をScorer / Prompt双方で是正し、最終的に**7 / 7、precision 1.0、recall 1.0**。`store=false`、JSON Schema、minimal thinking、fixed seedを使用する。

この結果により、Public World MemoryではBGE-M3をCurrent Runtimeへ採用せず、Geminiを交換可能なCloud派生Adapterとして使用する方針へ進めた。BGE-M3結果は比較Baselineとして保持し、Serina側の既存Memory Runtimeは変更しない。

## Semantic similarity floor sweep

`Temporal Fact + Gated Hybrid`で実測。

| floor | Passed | False recall | 所見 |
|---:|---:|---:|---|
| 0.45 | 8 / 11 | 1+ | 緩すぎる。存在しない情報への誤想起が増える |
| 0.50 | 11 / 11 | 0 | 現Goldenで成立 |
| 0.55 | 11 / 11 | 0 | 現Goldenで成立 |
| 0.60 | 10 / 11 | 0 | 厳しすぎてSemantic paraphraseを1件落とす |

したがって**0.50〜0.55を次Golden拡張用の候補帯**とする。固定Invariantにはしない。

## 重要Finding

### 1. BGE-M3単独ではCorrectionを解決しない

`赤 → 青`の訂正はSimilarity問題ではなく、Current / Historical stateの問題。

Temporal Fact / Structured Continuityのように、

- valid_from
- valid_to
- status active / superseded
- provenance Raw Entry

を持つ層が必要。

### 2. naive HybridはSemantic-onlyより悪化し得る

「複数Signalを足せば強い」は成立しない。

Weak Lexical hitをRRFへ無条件投入すると、Semanticが正しく棄却したNear-missを再びPromptへ戻す。

Gated Hybrid v1ではLexical-only candidateを次のどれかでのみ許可した。

- Semantic側も同Evidenceを支持
- Queryに対する十分なLexical coverage
- `ZX-7419`等のdistinctive exact anchor一致

このGate自体もEvaluation専用候補であり、製品閾値はGolden拡張後に確定する。

### 3. Abstentionは第一級Metricにすべき

「何かTop-Kを必ず返す」方式は採らない。

現GoldenでもSimilarity floor 0.45では、存在しない犬の名前を聞いた時に色のMemoryを返す誤想起が発生した。

Niraiの長期Memoryは、関係が怪しければ0件を正常系とする。

### 4. BGE-M3停止時のFallbackは成立

Ollama daemon停止状態でSemantic buildが失敗しても、Hybrid評価AdapterはFTS-onlyへ縮退できる。

製品実装でもEmbedding停止・Index lagを会話停止理由にしない方針を維持する。

### 5. Evaluation自体のEmbedding再計算も抑止

複数Baselineを同一Runで比較する時、同じRaw text / queryをBGE-M3へ繰り返し投げないようtext→vector cacheを持たせた。

Benchmark側の無駄なCPU消費を製品Architecture評価へ混ぜない。

## Productization Follow-up 状態

Public World Memoryについては次を製品コードへ実装・回帰した。

- lossless `raw_entries` SQLite Raw Durable Source
- WAL / busy_timeout
- Raw FTS5派生Index
- `sqlite-vec` 768次元Raw vector
- Background `structured_jobs`
- Gemini 3.5 Flash Lite Structured抽出
- exact quote / provenance gate
- Atomic Memory / Current Fact
- explicit correction時だけCurrentを置換し、根拠のない競合はhypothesisへ降格
- Resident Current Factを`resident:<name>`で分離
- Forget時にRaw / Structured / Current Fact / FTS / Vectorを整合削除
- Cloud failure時はRawを保持して指数Backoff
- Strong Exact/Local RecallではGemini Query Embeddingを呼ばない
- Semanticが必要な時だけGemini Query Embeddingを使用
- Current / Historical correctionは、Raw retrieval候補が属するStructured familyに対してだけ発火
- 新Rawが存在するSessionでは旧Episode fallbackを禁止し、abstentionを旧Retrieverが破らない
- Raw未移行Legacy Sessionだけ旧Episode Retrieverをfallback利用

したがってPublic側のStructured extraction / Mutation / Session単位Forgetは「未評価」から外れる。ただしMemory全体のProductization完了条件として次は残る。

1. Golden Set拡張
   - より多いSemantic paraphrase / near-miss / exact symbol
   - multiple entities
   - ambiguous update
   - preference / relationship / cognitive latent constraint
2. Private MemoryのStructured Continuity / Semantic Retrieval / Structuredまで含むForget
3. Public / Private cross-contaminationのResident数増加時Stress
4. 5-year scaleの残り
   - Public / Private Raw+FTS steady-stateは250k Syntheticで確認済み
   - Public sqlite-vec / Gemini Indexのrebuild / maintenance / p95長時間推移は未確認
5. Cloud quota / network障害を含む長時間運用
6. pre-birth World HistoryとPersonal ExperienceのContext表現

## 現時点のPublic World Memory Current

```text
Public utterance
    |
    +--> SQLite Raw Durable Source  <- synchronous / lossless authority
    |       |
    |       +--> Local FTS5
    |       +--> background Gemini Embedding 2 -> sqlite-vec
    |       +--> background Flash Lite extraction
    |                   |
    |                   +--> Atomic Memory / Current Fact + provenance
    |
Query
    |
    +--> Structured correction/history overlay from retrieved Raw family
    +--> strong Exact / Lexical => local only
    +--> weak / semantic => Gemini Query Embedding when budget permits
    +--> abstention gate / floor 0.70
    |
    v
minimal Context Evidence or 0 hits
```

Cloud Semanticは正本ではなく派生Adapter。停止・Quota枯渇時はRaw FTS5 / Structuredへ縮退し、Raw保存と通常会話を止めない。

Graph DB、Cross-Encoder、独自ANN、Agentic retrieval planner、全部入りMemory Frameworkは現時点で不要。Private SemanticはBGE-M3を比較Baselineとして評価した後、2026-09-07 Master判断でGemini Embedding 2へCurrent Productを統一した。Gemini Product Goldenは8/8・false recall 0、Current floor 0.70。今後はGolden拡張 / Private Structured / Public・Private Vector長時間Scaleで不足したCaseだけReference Challengerを追加する。

## Product Slice Verification

2026-09-07の最終実測：

- `core/tests`：**387 passed / 44.89s**（Private Gemini移行、共有Quota、Vector dimension migration、Direct Task Sliceまで含む最終実測）
- 新Hybrid Recall回帰：Exact local-only、Semantic Gemini path、Near-miss abstention、Current correction、Historical correctionを成功
- Public Forget回帰：Raw / Structured / Current Fact / FTS / Vector残存0
- Resident Fact identity回帰：`resident:Lapan` / `resident:Kina`の同一attributeを独立保持し、旧generic `resident`派生行もRaw speaker provenanceから修復
- Quota回帰：Background / Queryが同じEmbedding-2 rolling 24h Budgetを共有し、Background 400 / Query 500 / 合計900の各上限を越えない。Provider RPDのPacific Time日付境界にも依存しない
- Migration回帰：Rawが存在するSessionは旧Episode Retrieverで復活させず、Raw未移行Legacy Sessionだけfallback
- Live Product Smoke：実GeminiでFlash Lite Structured extraction 1件 + Embedding 2 `RETRIEVAL_DOCUMENT` 1件 + `RETRIEVAL_QUERY` 1件を通し、`top_source=gemini-embedding-2`、正しい`CE-LIVE-MEMORY-SMOKE`を1件取得、exit 0。Smoke Runtimeは終了後削除
- Public / Private 250k Scaleの詳細：`Nirai_MemoryScale_検証結果_2026-09-06.md`
- Private Historical BGE-M3：実Ollama 8 Case sweepで0.50 / 0.55が8/8・false recall 0。Resource TestのCPU queue競合も比較Evidenceとして保持するがCurrent Runtimeには採用しない
- Private Current Gemini：Product Golden 8/8 / false recall 0。Live Smokeで2 document embedding / semantic recall / vector込みForgetを通し、`top_source=private-gemini-embedding-2`、対象Entry取得、Forget後0 hits、exit 0
- Public / Private Query / Backgroundは同じEmbedding 2 rolling 24h Budgetを共有し、Nirai RuntimeはローカルBGE-M3を使わない
- Private Semantic Evidence：`Nirai_PrivateMemorySemantic_検証結果_2026-09-06.md`
- `git diff --check`：**exit 0**（LF→CRLF warningのみ）
