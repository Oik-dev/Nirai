# Nirai Reference-First調査：長期Memory / Conversation / Concurrency 2026-09-06

## 0. 目的

Niraiの長期日常利用基盤を再設計する前に、既に世の中で成立しているMemory、Conversation継続、Durable Execution、Concurrencyの設計・実装を横断調査し、無駄な再発明を避ける。

本書はReference / Evidenceであり、単独でActive Designを上書きしない。

調査対象は主に次の3領域。

1. 長期Memory：Raw、構造化記憶、時間変化、検索、訂正、Forget
2. Conversation Continuity：Provider native Session / Thread / server-side state
3. Concurrency / Recovery：Conversation順序、Resource Lock、長時間WorkのCrash復旧

Master判断として次は確定済み。

- Resident IdentityはBrain交換を越えて継続する
- Public / Private Memoryは数年規模を前提とする
- 新規Residentは入居前のWorld Memoryを世界史として知れるが、自分の体験とは扱わない
- 訂正は過去Rawを残してStructured Memoryで現行値を更新する
- 明示ForgetはRaw / Structured / Derivedを本当に削除する
- Residentは安全な範囲で並行生活する
- TaskはDirect / Delegatedを標準とする
- 最低5年間の日常利用を設計目標とする

---

## 1. 調査した主要Reference

### 1.1 Zep / Graphiti

- Zep Facts: https://help.getzep.com/facts
- Graphiti Overview: https://help.getzep.com/graphiti/getting-started/overview
- Graphiti Quick Start: https://help.getzep.com/graphiti/getting-started/quick-start
- GitHub: https://github.com/getzep/graphiti
- License: Apache-2.0

重要な考え方：

- Raw episodeと、Entity / Relationship / Factを分離する
- Factへ`valid_at / invalid_at`を持たせ、古い事実を消さず「いつまで正しかったか」を保持する
- temporal + full-text + semantic + graphを組み合わせる
- 新情報で旧Factをinvalidateし、歴史を保持する

Niraiとの適合：非常に高い考え方がある。

特に「Masterが以前Xと言ったが、後でYへ訂正した」を、Raw履歴を改竄せずFactの有効期間で表現する方式はNiraiの確定方針と一致する。

ただしGraphiti本体をそのまま採用するのは現時点では推奨しない。

理由：

- ローカル利用でもNeo4j / FalkorDB等のGraph DBが基本要件となり、Nirai単体アプリとして運用部品が増える
- LLMによる自動Entity / Fact抽出を中心にするため、Memory更新のBrain callコストが増える
- 2026年には無関係なFactまで誤ってinvalidateし得るIssue報告があり、自動Fact invalidationを正本へ丸投げするのは危険
- Niraiは1 Master + 少数Residentのローカル箱庭であり、GraphitiのGraph機能すべてを必要とする根拠はまだない

**採用候補：temporal Fact model、provenance、episode/rawとfactの分離。**

**不採用候補：Graphiti全体依存、Neo4j/FalkorDB導入、自動invalidateを無条件正本化。**

---

### 1.2 Mem0

- GitHub: https://github.com/mem0ai/mem0
- Architecture: https://github.com/mem0ai/mem0/blob/main/integrations/mem0-plugin/skills/mem0/references/architecture.md
- How it works: https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx
- License: Apache-2.0

重要な考え方：

- MemoryをApplication本体のConversation stateと分離する
- user / agent / run等のscopeをMetadataで明示する
- SQLをFact/Metadataの正本、Vector StoreをSemantic Search用派生Storeとして分離する構成
- Semantic / BM25 / Entity等の複数Signalを融合する方向
- Memory update / delete / historyというLifecycle APIを持つ

2026年の新Memory AlgorithmではADD-only extraction、entity linking、multi-signal retrieval、temporal reasoningを強化している。

Niraiへの示唆：

- Retrieval Indexを正本にしない
- scopeを明示し、Public / Private / Conversation等をQuery時に必ず絞る
- SemanticだけにせずLexical + Entity + Temporalを組み合わせる
- Memory操作を`add / correct / forget / search`等の明示Lifecycleにする

ただしMem0 OSSを丸ごと組み込むのは現時点では推奨しない。

理由：

- LLM / Embedder / Vector Store等の外部構成がNiraiの既存方針と重複する
- OSS側のStorageやHistory周辺で近年も設定・削除履歴のIssueが出ている
- NiraiはRaw原文をlossless正本にする要件が強く、Mem0のMemory抽出層を正本にする必要はない

**採用候補：scope、multi-signal retrieval、SQL source-of-truth + derived search index、Memory Lifecycle API。**

---

### 1.3 Letta / Letta Code

- Docs: https://docs.letta.com/
- Letta Code: https://github.com/letta-ai/letta-code
- License: Apache-2.0

重要な考え方：

- agent identity / memoryをModelから独立させる
- recent Conversationと、古いConversationのsummary / recallを分離する
- in-context Memory Blockと、外部長期Memoryを分ける
- Local modeではAgent、Conversation、Memory等をローカル保存できる
- Memory filesystemをGitで履歴管理する方向もある

Niraiの「Brainを交換しても同じResident」という思想と非常に近い。

ただしLettaをNiraiのResident Runtimeそのものへ採用すると、Nirai Coreと責務が大幅に重複する。

**採用候補：IdentityとModel分離、Working ContextとLong-term Memory分離、古いContextのrecall発想。**

**不採用候補：Nirai CoreをLetta Runtimeへ置換。**

---

### 1.4 LangGraph

- Memory: https://docs.langchain.com/oss/python/langgraph/add-memory
- Persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- Checkpointer integrations: https://docs.langchain.com/oss/python/integrations/checkpointers
- License: MIT

重要な考え方：

- Short-term Memory = thread-scoped state
- Long-term Memory = threadを跨ぐStore
- namespaceでMemory scopeを分離する
- checkpointerでConversation / Workflow stateを永続化しresumeする
- SQLite checkpointerも公式Integrationとして存在する
- Memory formationをhot pathではなくbackgroundで行う設計を明示的に紹介している

Niraiへの示唆：

- Conversation ContinuityとLong-term Memoryを別Subsystemとして扱う現在方針は妥当
- Memory抽出をMaster応答のhot pathへ全部載せる必要はない
- Rawは即時永続化し、Structured Memory生成やIndex更新を後段処理に分離できる
- Task / Agent Recoveryも「step boundaryでcheckpoint」という考え方が有用

ただしLangGraph全体をNirai Coreへ導入するのは現時点では推奨しない。既存CoreのConversation / Task / Agent Runtimeと大幅に責務が重なり、移行コストが高い。

**採用候補：thread vs long-term store分離、namespace、checkpoint、background memory formation。**

---

### 1.5 SQLite / FTS5 / sqlite-vec

- SQLite WAL: https://www.sqlite.org/wal.html
- SQLite file format: https://www.sqlite.org/fileformat.html
- sqlite-vec: https://github.com/asg017/sqlite-vec
- sqlite-vec License: MIT / Apache-2.0

SQLite WALは、Writerがcommitしている間もReaderが旧Snapshotを読める構造を持つ。Niraiの「1PC / Local / 数年利用 / 少数同時Writer」に非常に適している。

Niraiへの適合：高い。

候補構成：

- Raw / Structured MemoryのTransaction正本：SQLite
- Lexical Retrieval：SQLite FTS5
- Semantic Retrieval：必要性をBenchmarkで確認した後、`sqlite-vec`等をDerived Indexとして追加可能

`sqlite-vec`は非常に軽く、Python / Node等から利用できるが、2026年時点でも0.x系である。Raw正本やMemory Lifecycleを依存させず、**壊れたら再生成できるoptional derived index**としてのみ扱うのが安全。

---

### 1.6 BGE-M3

- FlagEmbedding: https://github.com/FlagOpen/FlagEmbedding
- Model: https://huggingface.co/BAAI/bge-m3
- License: MIT

特徴：

- 100+言語対応
- Dense / Sparse / Multi-vectorを1 Modelで扱える
- 8192 tokenまで
- 1024次元Dense embedding
- 約569M params / 約2.27GB

日本語を含むNiraiの長期Memory Retrieval候補として有力。

ただし、**最初から採用確定しない**。

現行FTS5との比較Benchmarkを先に行い、次を実測する。

- 日本語の言い換え想起
- 数か月前設定を模した検索
- 名前・固有語のExact recall
- Token / VRAM / latency
- Hybrid化した時の改善幅

FTS5で十分な領域へEmbeddingを入れない。

---

## 2. Conversation ContinuityのReference

### 2.1 Codex app-server

- https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md

公式app-serverは`Thread → Turn → Item`をConversation primitiveとして持ち、既存Threadを`thread/resume`で継続する。

重要：

- `thread/resume`は後続`turn/start`を同じThreadへappendする正式機能
- 最新仕様にはHistory paginationと`excludeTurns: true`があり、resume時に巨大なTurn History全体をClientへ再構築しなくてよい経路が存在する
- bounded queue / overload error / retry with backoffの考え方も公式に入っている

2026年の実Issueでは、巨大Threadのfull resumeが非常に重くなるケース、version間のresume回帰、active writer競合等も報告されている。

したがってNiraiは、

- Provider ThreadをConversation Contextに利用する
- ただしNirai側にFallback可能なRaw / Structured Memoryを保持する
- Version / Capabilityを実機検出する
- 可能ならhistoryをClientへ丸ごと復元しないresume方式を使う
- Provider Threadだけを5年Memoryの正本にしない

とするのが合理的。

### 2.2 Cursor ACP

- https://cursor.com/docs/cli/acp

公式ACPは`session/new`と`session/load`を明示し、既存Conversation resumeを正式にサポートする。

NiraiがCursor Residentへ毎Turn履歴全文を再送する理由はない。同一Nirai Conversation IDにCursor native Session IDを紐づける設計が妥当。

### 2.3 Gemini Interactions API

- https://ai.google.dev/gemini-api/docs/interactions-overview

2026年6月以降GAで、新規Project推奨API。`previous_interaction_id`により履歴を再送せずServer-side Conversation stateを継続でき、cache効率・Token cost改善が公式に説明されている。

ただし保持期限がある。

- Free tier：1日
- Paid tier：55日

したがってGemini native stateは短中期Conversation transportとして利用可能だが、長期Identity / Memory正本にはできない。

---

## 3. Concurrency / RecoveryのReference

### 3.1 Python asyncio

- Queue: https://docs.python.org/3.14/library/asyncio-queue.html
- Semaphore: https://docs.python.org/3/library/asyncio-sync.html

Niraiの規模では、全体Workflow Engineを導入せずとも、

- ConversationごとのFIFO Queue
- ProviderごとのSemaphore / capacity
- Workspace Write Lock
- Presentation Queue

を組み合わせれば、多くのConcurrency要件をPython標準機能で満たせる。

### 3.2 Inngest

- Steps: https://www.inngest.com/docs/learn/inngest-steps
- Concurrency: https://www.inngest.com/docs/guides/concurrency

有用な考え方：

- 長時間処理をcheckpoint可能なstepへ分ける
- 成功stepは再実行せず、失敗stepだけretryする
- side effectはidempotentにする
- concurrencyをglobal 1件ではなくResource keyで制限する
- sleep / wait状態はactive concurrency slotを消費しない

これはNiraiのAgent Runtime改善へ非常に参考になる。

ただしCloud/外部Runtime依存を増やす必要はなく、**設計パターンだけ採用**するのが現状は合理的。

### 3.3 Temporal / LangGraph durable execution

TemporalはEvent HistoryからCrash後もWorkflowを再開するDurable Executionを中心にする。LangGraphもcheckpointerでThread / Workflow stateを永続化し、resumeできる。

Niraiへの示唆：

- 「Agent Processを再起動できるか」だけではなく、Nirai自身がどこまでTaskの進行を完了済みと知っているかをcheckpointする
- Side effect境界を越えた操作を無条件retryしない
- File apply / Git / 外部送信等はoperation ID + durable stateでidempotencyを担保する

ただしTemporal / Inngest / LangGraphを丸ごと導入するのはNirai単体アプリには現状Overkill。既存Agent Session event storeへcheckpoint思想を取り込む方を第一候補とする。

---

## 4. 現時点の推奨アーキテクチャ方向

これはReference調査後の**候補**であり、Active Design確定前のたたき台。

### 4.1 Memory Storage

第一候補：SQLite中心。

```text
World Memory DB
  raw_events / raw_messages       lossless
  facts / continuity              structured + temporal
  episode / source mapping        provenance
  FTS5                            lexical derived index
  vector index                    optional derived index

Resident Private Memory DB
  raw_messages                    lossless private
  facts / continuity              private structured
  FTS5                            private only
  vector index                    optional private only
```

Public / Privateを物理DBレベルでも分ける案を有力候補とする。理由はPrivacy BoundaryをQuery filterだけへ依存させずfail-closedしやすいため。

ただしResident数が大きくなった場合のConnection管理等をBenchmarkしてから確定する。

### 4.2 Raw Entry

Rawは原則immutable。

最低候補Metadata：

```text
entry_id
scope: public | private
conversation_id
occurred_at
recorded_at
speaker_id
participants[]
text / event payload
source_kind
```

新規Residentの「自分の体験か」を判断するため、Resident作成時刻とparticipants / witness metadataを利用する。

入居前World Memoryを取得した場合はContext Composerが`historical / shared knowledge`として扱い、`personal experience`へ昇格させない。

### 4.3 Structured Fact

Graphiti/Zepを参考に、Factは時間とprovenanceを持つ。

候補：

```text
fact_id
scope
subject
predicate
value
valid_from
valid_to
learned_at
invalidated_at
source_entry_ids[]
status: active | superseded
```

訂正時はRawを変更せず、旧Factを`superseded`にして新Factをactiveにする。

重要：自動LLM extractionが「似たFactだから旧Factを消す」と勝手に判断しない。Masterの明示Correctionは強いSignalとして扱えるが、自動invalidationは高い確度が無い場合はadditive側へ倒す。

### 4.4 Forget

Forgetは1 Transaction / operationとして扱う。

対象を確定 → Raw削除 → dependent Fact / Summary / Chunk削除・再生成 → FTS / Vector更新 → Verification。

Forget operationのAuditを残す場合も、削除した本文そのものはAudit logへ再保存しない。`operation_id / timestamp / deleted IDs / status`程度にする。

### 4.5 Retrieval

初期候補：

1. FTS5 lexical candidate
2. temporal / scope / participant filtering
3. Structured Fact match
4. 必要性が実測された場合だけBGE-M3 dense candidate
5. score fusion
6. 上位だけRaw provenanceへ戻してContext化

Graph DBを最初から導入しない。

### 4.6 Memory Formation

Raw保存は会話hot pathで即時・Transaction内に完了させる。

Structured Fact / continuity抽出、Episode生成、Embeddingは後段処理へ分離できる。

Conversation応答をMemory要約生成で待たせない。

ただしCrashしても後段処理対象を失わないよう、`memory_jobs`等のdurable pending stateを持つ方向を検討する。

### 4.7 Conversation

Nirai Conversation IDを上位正本にする。

```text
Nirai Conversation
  ├ provider=codex  -> native thread_id
  ├ provider=cursor -> native session_id
  └ provider=gemini -> previous interaction id
```

native Contextが使える間は履歴全文を再送しない。

native Contextが失われた場合だけNirai Memory / Raw historyからrebuildする。

Provider固有TTL / Version bug / resume failureはAdapter境界で吸収し、Identityを失わない。

### 4.8 Concurrency

Global Lockを次へ分解する方向を第一候補とする。

```text
ConversationQueue[conversation_id]  = 1 turn at a time
ProviderSemaphore[provider/account] = provider capability / subscription budget
WorkspaceWriteLock[canonical_root]  = 1 writer
AgentSessionLock[session]           = own state sequencing
PresentationQueue                   = TTS / visible speech only
```

待機中のApproval / Question等はProvider compute slotを可能な限り占有しない設計を目指す。

---

## 5. 丸ごと採用しない方がよい候補

### Graphiti全体

良いカンニングペーパーだが、Graph DB + LLM extractionまで依存するとNiraiには過剰な可能性が高い。temporal fact / provenance / hybrid retrieval思想を借りる。

### Mem0全体

Memory layerの思想は有用だが、NiraiのRaw Memory正本と二重管理になりやすい。scope / lifecycle / retrieval設計を借りる。

### Letta Runtime

NiraiのResident Identity思想に近いが、Coreを二重化する。Memory-first設計を借りる。

### LangGraph / Temporal / Inngest Runtime

Durabilityの設計は非常に有用。ただし既存Nirai Agent Runtimeへ追加Frameworkを丸ごと載せる価値はまだ証明されていない。checkpoint / idempotent step / keyed concurrencyの思想を借りる。

---

## 6. 次の設計前に確認するgrill-me論点

Reference調査で、技術ではなくMasterの好みで決める必要がある論点が2つ残った。

### Q1. Private Memoryの物理分離

A. **ResidentごとにPrivate Memory DBを物理的に分ける**
- Privacyがfail-closedにしやすい
- Forget / Resident削除が単純
- Residentが少数なら運用Costは小さい

B. World / Privateを1 SQLite DBに入れ、scope列とQuery policyで分離する
- DB管理は単純
- Cross-memory transactionは楽
- Query bug時のPrivate leak影響が大きい

推奨：**A**。

### Q2. Structured Memory生成のタイミング

A. **Rawだけ即時保存し、Structured Memory / Embeddingは後段Jobで増分生成**
- 会話latencyを増やさない
- 複数発言をまとめて処理できる
- durable job管理が必要

B. 毎Turn返答前にStructured Memoryまで必ず更新
- 常に最新
- Brain call / latency / Tokenが増える

推奨：**A**。

---

## 7. 現時点の結論

今回のReference-First調査で、Niraiが独自にゼロから発明する必要はかなり減った。

最も有用だった組み合わせは次。

- **Storage基盤：SQLite / WAL / FTS5**
- **時間付きFact思想：Zep / Graphiti**
- **scope / lifecycle / multi-signal retrieval：Mem0**
- **IdentityとModel分離：Letta**
- **thread state / long-term store / checkpoint：LangGraph**
- **native Conversation continuation：Codex app-server / Cursor ACP / Gemini Interactions**
- **keyed concurrency / step checkpoint / idempotency：asyncio + Inngest / Temporalの設計思想**
- **semantic retrieval候補：BGE-M3 + optional sqlite-vec**

現時点では大型FrameworkやGraph DBを追加せず、Niraiの既存Coreへ上記の成熟した設計パターンを薄く取り込む方が、合理性・効率性・保守性のバランスがよい。

---

## 8. Serina既存Memory実装の再評価

2026-09-06に`D:\Products\dev\serina`を確認した結果、Nirai MemoryのReference-First対象として、外部OSSだけでなく**Serina自身が非常に重要な先行実装**であることを確認した。

### 既に存在するもの

- `core/memory/embedder.py`
  - Ollama `http://localhost:11434`
  - model=`bge-m3`
  - `num_gpu=0`でCPU固定
  - `keep_alive=-1`で常駐
  - コメント上、RAM約1GB・短文Embedding 1秒未満の実機確認あり
- `data/serina_memory.db`
- `sqlite-vec`
- SQLite WAL / `busy_timeout`
- 1024次元BGE-M3 Vector Index
- `facts`台帳
  - `valid_from / valid_to`
  - `active / hypothesis / superseded / tombstone`
  - `supersedes`
- Fact Vectorを別保存し、既存Fact全件再Embeddingを避ける仕組み
- Directed Forget / Tombstone / Physical Delete
- Vector Index再構築
- Recall Planner
- Temporal / Entity Query
- Fact supersede / correction Test

### 重要な含意

Nirai側でBGE-M3 Runtimeを別個に立てる必要は薄い。Serinaは既にPC上の共通Ollama daemonへBGE-M3をCPU固定で常駐させる構造を採っている。

NiraiがSemantic Retrievalを採用する場合も、原則として同じOllama daemon / 同じ`bge-m3`をMachine-level Embedding Resourceとして共有する方向を優先する。Nirai専用の別BGE-M3 Processや別Model copyを常駐させない。

Ollama公式仕様では同一Modelへの並行Requestを処理でき、Memory条件により並列またはQueueされる。既定`OLLAMA_NUM_PARALLEL=1`では同一Model Requestは実質1本ずつ処理されるため、SerinaとNiraiが同時にEmbedding Requestを出しても、基本リスクは**データ衝突ではなくCPU待ち時間 / Queue競合**である。

Nirai側もBGE-M3を使う場合は、Serinaと同じ`num_gpu=0`方針を守り、GPUへ誤配置して会話BrainとVRAM競合させない。

### Serina入居時のMemory Ownership

Serinaは既に独自の長期Memory / Fact / Forget / Recall機構を持つため、Niraiへ入居させる際に「通常Residentと同じPrivate Memoryをもう1セットNirai側へ作る」と、次の二重化が発生し得る。

- 同じWhisperをSerina DBとNirai Private DBの双方へ記録
- Serina RecallとNirai Recallの二重注入
- 同じ発話を双方がBGE-M3でEmbedding
- 訂正 / Forgetの二重処理と不整合

したがってSerina統合時は、**ResidentごとのMemory Ownershipを差し替え可能なContract**として設計する価値が高い。

候補：

- 通常Resident：Nirai-owned Private Memory
- Serina Resident：Serina-owned Private Memory Adapter
- World Memory：Nirai-owned Public Historyを全Residentへ共通供給

この場合、Serinaの既存`serina_memory.db`を維持しつつ、NiraiはWorld Historyだけを外部Contextとして供給できる。Serina自身のIdentity / Personal MemoryをNiraiへ無理にコピーしない。

### 再発明防止上の結論

Nirai長期Memoryの具体設計前に、Serinaの`MemoryStore / FactStore / directed_forget / recall_planner / distillation`をReference Implementationとして正式監査する。

丸ごと共通化するとはまだ決めない。まず「そのまま再利用できる低層部」「Niraiへ一般化できるPattern」「Serina固有で残すべき部分」を分ける。

---

## 9. 2026-09-06 再調査：Serinaを上回る候補の有無

Serina Memoryは2026-07〜08頃の設計であるため、2026-09-06時点の新しい研究・OSSを再調査した。

### 結論

**Serinaをそのまま捨てて置換すべき単一Frameworkは見つからなかった。**

一方、Serinaより強い要素を持つ最新実装・研究は複数あり、これらを選択的に取り込めば、Nirai向けにはSerina基準を明確に上回る構造を作れる。

現時点の有力Referenceは以下。

1. **PropMem / ProsusAI MemEval**
   - Raw chunkとAtomic Propositionを両方保持
   - BM25 + Vector Hybrid Retrieval
   - Entity scopeで別人Memory混入を抑制
   - Controlled benchmarkでLoCoMo / LongMemEvalとも強いquality-to-cost
2. **TiMem (ACL Findings 2026)**
   - Temporal Memory Treeによる時間階層Memory
   - Raw observationから段階的にPersona級抽象へconsolidate
   - Query complexityに応じて参照Levelを変える
   - LoCoMo 75.30%、LongMemEval-S 76.88%、LoCoMo recalled token 52.20%削減を報告
3. **ReMe / AgentScope**
   - Source -> Daily -> Digest -> Derived Indexの階層
   - Raw sourceと長期抽象を同時保持
   - 人間が直接読めるMemory as File
   - Auto DreamによるBackground consolidation
   - Personal / Procedure / Wiki等、長期Memory種類を分離
4. **Hindsight**
   - Semantic / BM25 / Graph / Temporalの4検索を並列
   - Reciprocal Rank Fusion + Cross-Encoder rerank
   - Retain / Recall / Reflectを分離
   - Memory retrievalとMemory上の深いreasoningを分ける
5. **TencentDB Agent Memory (2026)**
   - 完全ローカル運用を重視
   - L0 Conversation -> L1 Atom -> L2 Scenario -> L3 Persona
   - 下位層はDB、上位層は人間可読MarkdownというProgressive Disclosure
   - Recall timeout時は会話を止めずskip
   - BM25 / Embedding / Hybridを選択可能
   - MIT License
6. **LongMemEval / LongMemEval-V2 / HaluMem / LoCoMo-Plus**
   - 単なるfact recallだけでは不十分
   - Knowledge update / temporal reasoning / abstention / contradiction / cognitive latent constraint / memory hallucinationを別能力として評価すべき

### Serinaが現在も強い点

Serinaには既に次があり、2026年最新方式と比べても捨てる理由はない。

- Raw Session History
- Background Distillation Job
- Job checkpoint / retry / shelve
- SQLite WAL / busy_timeout
- sqlite-vec
- BGE-M3 1024次元
- CPU固定 / Ollama共有Runtime
- Fact valid_from / valid_to
- active / hypothesis / superseded / tombstone
- Fact provenance (`episode_ids`)
- Directed Forget
- Physical Delete
- Backup before destructive operation
- Change Log
- Preference / Relationship structured state
- Temporal / Entity Recall Planner
- Importance / Recency / semantic relevance / spreading activation
- Vector Index rebuild

特に、Fact supersede・Forget・Background Distillation・CPU BGE-M3はNiraiへ流用価値が高い。

### Serinaが最新Referenceに対して弱い点

#### 1. Raw EvidenceとStructured MemoryのHybrid Retrievalが弱い

SerinaはRaw Sessionを保存するが、長期Recallの中心は蒸留済みMemory / Factである。

2026年6月のRedis AI ResearchによるLongMemEval実験では、**Raw conversation retrieval + extracted memoryのHybrid**が単独方式より強く、86.1% task-averaged accuracyを報告している。

したがってNiraiでは、Structured Memoryが誤抽出・欠落した場合でもRaw Evidenceへ戻れるretrieval pathを持つ。

#### 2. Lexical + Semantic Hybridが不足

Serinaの主RecallはBGE-M3 Vector中心であり、BM25/FTSとの明示的Fusionがない。

PropMem、Hindsight、TencentDB Agent Memory等ではBM25 + Vectorを併用している。

固有名詞、型番、具体数値、コードSymbol等ではLexical検索がSemanticのみより有利なため、NiraiではSQLite FTS5/BM25を維持し、BGE-M3は追加信号として使う方向が有力。

#### 3. Retrieval Fusion / Rerankが弱い

Serinaは独自Activation Scoreを持つが、Semantic / Lexical / Temporal / Entityの独立retrieverを統合する一般的なFusion層は弱い。

HindsightのRRF + optional rerankはReference価値が高い。ただしCross-Encoder Modelを常駐追加するかは、Nirai実測Benchmarkで価値が確認できた場合だけ採用する。

#### 4. Memoryの時間階層が浅い

SerinaはRaw Session / Memory / Fact / Summaryを持つが、TiMemやTencent方式のような明示的な

`Raw -> Atomic -> Episode/Scenario -> Durable Continuity/Persona`

というProgressive階層は弱い。

Niraiの5年利用では、全Atomic Memoryを同じ粒度で検索し続けるより、Queryに応じて抽象Levelを切り替えられる方がToken効率と長期一貫性に有利な可能性が高い。

#### 5. Memory hallucination評価が不足

HaluMemは、Memory systemがExtraction / Update段階で誤情報を生成し、それが後段へ累積する問題を指摘している。

Serinaにはquote照合やhypothesisがあり防御力は高いが、Fact supersedeを同一subject + embedding similarityで判定する部分は誤置換余地がある。

NiraiではCorrection / Supersedeを自動判定だけへ任せず、provenance・predicate/category一致・明示訂正表現・confidence等を複数条件で判定する設計を検討する。

#### 6. Cognitive Memory評価が不足

LoCoMo-Plusは、後から直接同じ語を使わずに「以前の価値観・制約・目標」を適用するCognitive Memoryを別能力として評価している。

NiraiのResident Relationshipでは、単純Fact検索よりこの能力が重要である。

### Nirai向け推奨Architecture候補

現時点では、Frameworkを丸ごと導入するより以下のHybridが最有力。

```text
Raw Durable Source
  - World conversation/event raw
  - Resident Private raw
        |
        v
Atomic Memory / Fact
  - proposition / fact / promise / preference / relationship
  - valid_from / valid_to / supersedes / provenance
        |
        v
Episode / Scenario
  - ある出来事・会話・期間の意味まとまり
        |
        v
Durable Continuity
  - preference / relationship / unresolved / persona-adjacent state

Derived Retrieval
  - SQLite FTS5 / BM25
  - BGE-M3 vector via shared Ollama CPU resource
  - Temporal filters
  - Entity / Participant filters
  - optional graph/link expansion
        |
        v
Fusion
  - RRF等の単純で説明可能な統合から開始
  - rerankerはBenchmarkで効果が出る場合のみ追加
```

### BGE-M3 Resource方針

SerinaとNiraiでBGE-M3 Runtimeを別々に立ち上げない。

- Machine-levelの同一Ollama daemonを共有
- model=`bge-m3`
- `num_gpu=0`
- CPU常駐を基本
- RequestはQueue可能
- Embedding遅延が会話をBlockingしないBackground処理を基本
- Recall Query embeddingがtimeoutした場合はFTS / Structured Context等へ縮退可能にする

### Memory Ownership候補

```text
World Memory                 Nirai-owned
Normal Resident Private      Nirai-owned per-Resident DB
Serina Private               Serina-owned via Memory Adapter
Derived Embedding Runtime    Machine-shared Ollama bge-m3
```

Serina入居時にPrivate Memoryを二重記録しない。

### 実装前Benchmark Gate

最新Frameworkの宣伝値とSerinaの既存Golden Testは直接比較できない。したがって「Serinaより上」を証明せずArchitectureを決め打ちしない。

最低限、次を同一条件で比較する。

#### Baseline A: Serina方式
- BGE-M3 Vector + current Fact / Activation

#### Baseline B: Serina + FTS Hybrid
- Raw / Structured双方へFTS5
- Vector + Lexical Fusion

#### Baseline C: PropMem型
- Raw chunk + Atomic Proposition + Entity filter + Hybrid Search

#### Baseline D: Temporal Hierarchy型
- B/C + Episode / Scenario / Durable Continuity階層

評価軸：

- factual recall
- multi-session reasoning
- temporal reasoning
- knowledge update / correction
- abstention / false recall
- preference / relationship continuity
- cognitive latent constraint
- Forget後の再想起漏れ
- Public / Private cross-contamination 0件
- retrieval latency
- context token量
- BGE-M3 CPU queue待ち
- 5年相当件数へのScaling

LongMemEval、HaluMem、LoCoMo-Plusの考え方を利用しつつ、Nirai用の日本語Golden Setも作る。

### 現段階の採否

- **Serina Memoryの低層をReferenceとして採用：YES**
- **SerinaをそのままNirai標準Memoryとしてコピー：NO**
- **PropMemのRaw + Atomic + Entity + Hybrid思想：採用候補 HIGH**
- **TiMemのTemporal Hierarchy：採用候補 HIGH**
- **ReMeのSource / Daily / Digest / Derived分離：採用候補 HIGH**
- **Hindsightの4-way retrieval + RRF：採用候補 MEDIUM-HIGH**
- **Hindsight/PostgreSQL一式導入：NO（現時点では過剰）**
- **TencentDB Agent Memory一式導入：NO（Team Memory HubとしてはNiraiに過剰）**
- **TencentのL0-L3 / local fallback / timeout思想：採用候補 HIGH**
- **Graphiti / Mem0 / MemOS等のFramework丸ごと導入：現時点NO**

次工程は、上記4 Baselineを比較できるNirai Memory Contract / Evaluation Harnessを先に設計し、その後に物理実装を確定する。

---

## 10. 第二波調査：2026年上位Benchmark系と8月の新研究

第一波の候補に加え、LongMemEval上位主張・ICLR/ACL/EMNLP 2026・2026年7〜8月の新研究を再確認した。

### agentmemory V4（Jordan McCann）

MITの公開Repositoryで、LongMemEval-S 481/500 = 96.20%を自己報告し、result JSON / full log / legitimacy noteをRepositoryへ含めている。

主な構造：

- SQLite
- Semantic + BM25 + Activation + Graph + Importance + Temporalの6 signal
- HNSW
- Cross-Encoder rerank
- temporal grounding
- knowledge graph
- consolidation / contradiction detection
- abstention calibration

Niraiへの評価：

- **非常に有力なReference Implementation**
- Serinaよりretrieval fusionは明確に広い
- ただし公開履歴が浅く、BenchmarkはClaude Opus 4.6 + GPT-4o Judgeを含むため、NiraiのローカルBGE-M3条件と同一ではない
- Cross-Encoder / custom HNSW / graphを丸ごと採用せず、各signalのablationをNirai側で取る

### Chronos

2026-03の研究で、Raw Dialogueを時間付きSVO Event tupleへ分解し、Full Turn Calendarも並行保持する。

- Structured Event Calendar
- Raw Turn Calendar
- resolved datetime range
- entity aliases
- queryごとにretrieval guidanceを生成
- iterative tool retrieval

LongMemEval-SでChronos Low 92.60%、High 95.60%を報告。

Niraiへの評価：

- Raw + Structured dual pathがRedis Hybrid結果とも整合
- Temporal query / multi-hop時だけagentic retrievalを起動する設計は有力
- 全QueryへLLM retrieval plannerを入れるとLatency / call costが増えるため、Rule / simple retrievalで足りないCaseだけに限定候補

### Mastra Observational Memory

Observer / ReflectorのBackground AgentがRaw Messageをdense observationへ段階圧縮し、stable / cacheable contextを維持する。

- background Observer
- background Reflector
- observation date / referenced date / relative date
- token threshold based consolidation
- dynamic retrievalを毎Turn必須にしない

LongMemEvalでgpt-4o 84.23%、gpt-5-mini 94.87%を報告。

Niraiへの評価：

- Niraiの`Durable Continuity` / Background consolidationのReferenceとして強い
- ただしRaw正本を捨てる設計はNiraiでは採らない
- Provider native continuationがあるNiraiでは、OMをConversation Transportの代替ではなく再構成・長期Continuity層として検討する

### CortexDB v1

LongMemEval-S 93.8%、LoCoMo cats 1-4 86.9%を自己報告。ArchitectureはNiraiの狙いと近い。

- immutable WAL
- async extraction
- typed / bi-temporal facts
- BM25 + Vector + RRF
- graph
- selective forget / audit
- hierarchical scope

ただし：

- source-availableでOpen Sourceではない
- 現時点でWindows self-host binaryなし
- Benchmark構成はOpenAI Embedding / Cohere reranker / Claude等を利用
- Niraiの完全ローカル・既存Subscription中心方針にはそのまま合わない

よって**Architecture / ablationのカンペとして利用し、製品依存にはしない**。

### MemTxn（2026-07）

Memory更新そのものへTransaction Boundaryを置く研究。

- source-supported update verification
- conflict時のvisible version resolution
- durable snapshot journal
- fault後のcomplete-state recovery

Niraiへの評価：**HIGH**。

Memoryは検索精度だけでなく、「間違った更新を永続化しない」ことが長期利用で極めて重要。Correction / Supersede / Forgetを単なるCRUDではなく、検証可能なMemory Mutationとして扱うReferenceにする。

### ScrubJay-MEM（2026-08）

Memory typeごとにperishability / utility horizonを持たせ、時間経過によるranking劣化を変える研究。

Niraiへの評価：**限定採用候補**。

- transient schedule / temporary stateには有用
- relationship / promise / identity等へ一律Decayを適用しない
- decayは物理Forgetではなくretrieval ranking signalとしてのみ検討する

### MemoryAgentBench（ICLR 2026）

LongMemEvalだけでなくMemory Agentを次の能力へ分解して評価する。

- Accurate Retrieval
- Test-Time Learning
- Long-Range Understanding
- Conflict Resolution / Selective Forgetting系能力

Niraiへの評価：**Evaluation taxonomyとしてHIGH**。

LongMemEval単一scoreへ過適合しないため、Nirai Golden Setをこれらの能力軸でも分類する。

### HaluMem / LoCoMo-Plus

HaluMemはExtraction / Update / QAそれぞれのhallucinationを分離評価する。LoCoMo-Plusは後から直接同じ語を使わず、過去の価値観・目標・制約を適用するCognitive Memoryを評価する。

Niraiでは「正しいFactを検索できる」だけでは不足するため、両方をEvaluationへ取り込む。

### Private Semantic Product判断（2026-09-06追補）

Private WhisperのSemantic Retrievalについては、Public World Memoryで採用したGemini Freeをそのまま横展開しなかった。

Gemini API Additional TermsのUnpaid Services条項では、unpaid quotaへ送信したcontent / generated responsesがGoogle製品・ML技術改善に利用され得てhuman reviewerが処理する場合があり、confidential / sensitive / personal informationを送らないよう明記されている。したがってMasterとResidentのWhisperをUnpaid Servicesへ送る設計はCurrent Privacy Boundaryと適合しない。Paid Servicesはdata-use条件が異なるが、Private向け採用は将来必要になった時に別Gateで再確認する。

- https://ai.google.dev/gemini-api/terms
- https://ai.google.dev/gemini-api/docs/pricing

代替としてSerinaが既に利用しているMachine-level Ollama `bge-m3`を実機評価した。

- `num_gpu=0`で`size_vram=0`を確認
- short query p95：約263ms
- 4-way short parallel p95：約478ms
- 長文Serina相当Background embeddingとの競合時、Nirai short query p95：約6.13秒

Ollama公式FAQ上も同一Model requestはmemory / parallelism条件によりqueueされ得るため、観測したRiskはGPU競合ではなくshared CPU queue待ちと整理できる。

- https://docs.ollama.com/faq

独自Priority Schedulerは作らず、Current Productは次の最小防御に留めた。

1. Resident別Private SQLite DBへBGE-M3 vectorを保存する
2. exact / strong lexicalならEmbeddingを呼ばない
3. interactive BGE query timeout 1.2秒でLocal FTSへfail-soft
4. query failure後30秒はcircuit-breaker cooldownとして、busy queueへ連続requestを積まない
5. Background Embeddingは全Resident合計1件/5秒
6. similarity floor 0.55。実BGE Private Golden 8 Caseで8/8 / false recall 0
7. `raw_vec`はderived indexで、Explicit ForgetではRaw / FTS / Vector / Jobを整合削除する

詳細Evidence：`Nirai_PrivateMemorySemantic_検証結果_2026-09-06.md`

### 第二波後の結論

**2026-09-06時点でも、Nirai要件に対してSerinaを丸ごと置換すべき単一Systemは確定しない。**

しかし、Serinaを超えるために必要な要素はより明確になった。

1. Serinaの安全な低層・BGE-M3・Fact temporal model・Background Jobを基準線にする
2. PropMem / RedisのRaw + Structured Hybridを加える
3. BM25 + Vector + Temporal / Entity等を独立signalとしてFusionする
4. Chronos / TiMemのTemporal structuringを比較する
5. Mastra OM / ReMe / TencentのBackground consolidation・階層化を比較する
6. MemTxn型のsource-supported mutation / recoveryを加える
7. HaluMem / MemoryAgentBench / LoCoMo-Plusで「検索以外」の失敗も測る
8. Graph / Cross-Encoder / agentic retrievalはBenchmarkで利益が証明された時だけ足す

Benchmark上位Systemの数字はModel・Judge・ensemble・retrieval条件が揃っていないことが多いため、順位をArchitecture採用理由にはしない。Niraiでは同一Input / Model / Embedding / Token Budget / Hardwareで比較する。
