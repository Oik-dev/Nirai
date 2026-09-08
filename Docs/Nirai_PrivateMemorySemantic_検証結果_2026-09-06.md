# Nirai Private Memory Semantic 検証結果 2026-09-06 / 2026-09-07更新

## 判定

**Private Raw + Local FTS + Gemini Embedding 2 Semantic Retrieval 第一製品Slice：SAFE**

**Private Structured Continuity：Current blockerではなくChallenger**

2026-09-06にはshared CPU Ollama BGE-M3で第一Sliceを成立させたが、2026-09-07のMaster判断でNiraiのPublic / Private Memory SemanticをGemini Freeへ統一した。Holo / SerinaはNirai標準Resident Memoryとは別軸のAddon / external systemとして扱い、Nirai RuntimeはローカルBGE-M3を使用しない。

Private Raw正本は引き続きResident別SQLiteに保持する。Geminiへ送るのはSemantic Embedding対象のWhisper text / queryであり、Private DBや他Resident Memoryを混ぜて送らない。

---

## 1. Current Architecture

### Storage

Residentごとの`private_memory.sqlite3`をPrivate Raw正本とする。

- lossless Raw Whisper
- Local FTS5
- `embedding_jobs`
- `raw_vec` (`sqlite-vec`, **768 dimensions**, cosine)
- Legacy `whispers.jsonl`はimport / compatibility mirror

Residentごとに物理DBを分け、Lapan queryがKina DBを開かない。

### Semantic Provider

Current：`gemini-embedding-2`

- Document：`RETRIEVAL_DOCUMENT`
- Query：`RETRIEVAL_QUERY`
- dimensions：768
- similarity floor：0.70
- Strong lexical / distinctive exactはGemini Queryを使わずLocal return
- Gemini停止 / quota枯渇 / vector lag時はTemporal Queryを含めconservative Local fallback
- Current / Historical queryでも、Strong / distinctive exactまたは`coverage >= 0.22`を満たす候補が1件だけならLocalで返す。曖昧な複数候補は0件へ倒し、古い候補を推測採用しない

Embedding Model / dimension変更時、`raw_vec`は派生Indexとして破棄し、`embedding_jobs`をpendingへ戻してResident-local Rawから再構築する。同一次元でもModelが変われば異なるEmbedding空間としてrebuildし、1024d BGE indexと768d Gemini indexだけでなく、同一次元の異Model Vectorも混在させない。

---

## 2. Gemini Quota Policy

Public / Privateは同じ`gemini-embedding-2` quota authorityを共有する。

Current rolling 24h guard：

- Background Document Embedding：Public + Private合算 **最大400**
- Query Embedding：Public + Private合算 **最大500**
- Embedding 2合計：**最大900**

したがってPrivateをGeminiへ移しても、Public 900 + Private 900のように二重計上しない。

Embedding 1等の別Model枠は、異なるVector空間を同一Indexへ混ぜられないためCurrentの容量へ足し算しない。

Cloud Semanticが利用できなくても、Raw保存・Local FTS・通常会話は継続する。

---

## 3. Private Product Golden

Runner：

- `.tools/run-private-memory-product-golden.py`
- `.tools/run-private-memory-product-golden.ps1`

2026-09-07に、Evaluation AdapterではなくProduct `PrivateMemoryService / PrivateVectorStore / PrivateMemoryHybridRetriever`を実Gemini Embedding 2へ接続し、既存Private 8 Caseを再実行した。

結果：

- **8 / 8 passed**
- false recall 0
- 6 document embedding / 0 failed
- Lapan vector 5 / Kina vector 1
- Exact code recall：成功
- Semantic paraphrase recall：成功
- Correction Current：新しいRawだけ返す
- Correction History：旧 / 新Rawを返す
- Promise recall：成功
- Cross Resident Scope：0件
- Near-miss：0件
- Unknown：0件
- exit 0

返却sourceは`private-gemini-embedding-2`。

この結果、Private Structured Fact Overlayを現在の8 Case成立のために追加する必要はない。Structured層はGolden拡張でRaw + FTS + Gemini + Temporal ruleが実際に不足した場合だけChallengerからProductへ昇格する。

---

## 4. Live Product Smoke

Runner：

- `.tools/run-private-memory-product-smoke.py`
- `.tools/run-private-memory-product-smoke.ps1`

2026-09-07実測：

- Gemini Document Embedding：2 processed / 0 failed
- vector count before Forget：2
- Semantic query：`CE-PRIVATE-LIVE-SEA`を取得
- `top_source=private-gemini-embedding-2`
- similarity：**0.7284**
- Forget成功
- vector count after Forget：1
- Forget対象の再Recall：0 hits
- exit 0
- Smoke Runtimeは終了後削除

---

## 5. Privacy / Scope / Forget

`private_memories`はWhisper Contextへだけ入れる。

- Public Say / resident_chatへ入れない
- Resident AからResident BのPrivate DBを開かない
- recent tailに既にあるEntryはRetrieverから除外する
- Provider native Working Contextへ一度注入済みの同一Evidenceは、同Contextが健全な間は再送しない
- Core restart / Provider reset / detected compaction後は再注入可能

Private Entry Forgetは次を整合削除する。

- `raw_vec`
- `raw_fts`
- Raw Entry
- `embedding_jobs`
- compatibility JSONL
- derived `context.md`

`sqlite-vec`を開けずVector purgeを確認できない場合はfail-closedする。

---

## 6. Historical BGE-M3 Baseline

2026-09-06の比較Evidenceとして、shared CPU Ollama `bge-m3`も評価した。

- 1024d
- `size_vram=0`
- short query p95：約263 ms
- 4-way short p95：約478 ms
- 長文Background競合時のNirai query p95：約6.13秒
- Private Goldenではfloor 0.50 / 0.55が8/8 / false recall 0

この結果は方式比較として有効だが、**2026-09-07 Current Nirai RuntimeにはBGE-M3を採用しない**。Serina側のMemory Runtimeは変更しない。

Historical Runner：

- `.tools/run-private-bge-golden.py`
- `.tools/run-private-bge-golden.ps1`
- `.tools/run-bge-resource-test.mjs`

---

## 7. Verification

2026-09-07 Gemini移行追加回帰：

- Private Gemini Product Golden 8/8 / false recall 0 / exit 0
- Private Gemini Live Product Smoke exit 0
- Public / Private shared Gemini background budget
- Public / Private shared Gemini query budget
- Embedding ModelまたはVector dimension変更時にderived indexを破棄しRawからrebuild。同一次元のModel変更も対象
- Resident物理Scopeを跨がないSemantic Recall
- Gemini failure / quota exhaustion時のLocal fallback
- Current / History Temporal rule
- Forget後vector / job残存0
- Private Semantic ContextはWhisper only
- config未指定環境は`disabled`で外部API依存にしない

Core全体の最終件数は`AI_ENTRY.md`の最新Baselineを正とする。

---

## 8. Current / 残件

### SAFE

- Private lossless Raw SQLite
- Private Local FTS5
- Private Gemini Embedding 2 semantic vector
- Whisper実Contextへのold semantic memory injection
- Resident別物理Scope
- Public / Private共有rolling 24h quota guard
- embedding model / dimension変更時のderived vector rebuild
- vector込みEntry Forget
- Raw / FTS steady-state 250k Scale

### 未完了 / Challenger

1. Private Structured Continuity / Atomic FactはCurrent blockerではない。Golden拡張で不足が証明された時だけ追加
2. Public / Private Gemini Vector Indexの年単位maintenance / rebuild / quota運用Scale
3. Serina `external-resident` Memory Adapter
4. pre-birth World History / Personal Experience context semantics

Private Structured生成も、必要性が証明されるまでは追加LLM callを増やさない。将来必要になった場合はMasterが許容したGemini FreeをProcessor候補に含めてEvaluationする。
