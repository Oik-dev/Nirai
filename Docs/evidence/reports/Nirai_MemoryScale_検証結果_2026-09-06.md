# Nirai Memory Scale 検証結果 2026-09-06

## 判定

**Public Raw / FTS steady-state Scale SAFE**  
**Private Raw / FTS / Whisper Recall Scale SAFE**  
**Private Gemini Embedding 2 Semantic 第一製品Slice SAFE**  
**Private Structured Continuity と Public Vector / Cloud長時間運用を含む5年Memory全体は未完了**

目的は、最低5年間の日常利用でRaw履歴量が増えた時に、通常の1 Turnが全履歴scan / 全体rewriteへ退行しないことを実測することである。

## Private Memory

### Current

Residentごとに独立した `residents/<name>/private/private_memory.sqlite3` をRaw正本とする。

- SQLite WAL + busy_timeout
- lossless Raw Entry
- Residentごとに物理DB分離
- Local FTS5
- `recent_whispers()` はSQL tail
- `whispers_after()` はstable Entry marker以降だけSQL取得
- Private RetrieverはFTS + Gemini Embedding 2 / abstention-oriented gate
- `whispers.jsonl` はLegacy import / compatibility mirror。通常read経路では全scanしない
- Private Retriever結果はWhisper Contextだけへ入り、Public Say / resident_chatへ渡さない
- Provider native Working Contextでは同じPrivate Memory Evidenceを重複注入しない
- 2026-09-07 Master判断でPrivate WhisperのSemantic EmbeddingもGemini Freeを利用する

### 100,000 Whisper

約5年間で1日55件相当。

| 項目 | 実測 |
|---|---:|
| Legacy JSONL生成 | 780.74 ms |
| 初回JSONL→SQLite import + recent20 | 8,359.98 ms |
| raw count | 7.58 ms |
| recent20 | 3.84 ms |
| late marker以降20件取得 | 3.18 ms |
| Exact FTS recall | 64.90 ms |
| context_for_brain | 2.34 ms |
| 新規Whisper 1件追記 | 61.46 ms |
| Entry単位Forget | 363.44 ms |
| DB | 83.45 MiB |
| compatibility JSONL | 18.50 MiB |

### 250,000 Whisper

約5年間で1日137件相当。

| 項目 | 実測 |
|---|---:|
| Legacy JSONL生成 | 1,731.38 ms |
| 初回JSONL→SQLite import + recent20 | 19,741.32 ms |
| raw count | 9.30 ms |
| recent20 | 3.97 ms |
| late marker以降20件取得 | 2.71 ms |
| Exact FTS recall | 185.69 ms |
| context_for_brain | 3.77 ms |
| 新規Whisper 1件追記 | 52.55 ms |
| Entry単位Forget | 1,025.24 ms |
| DB | 210.48 MiB |
| compatibility JSONL | 46.25 MiB |

### Private結論

件数が100k→250kへ2.5倍になっても、`recent20`、late-marker delta、Context生成、新規appendはほぼ横ばいであり、旧`whispers.jsonl`全scan問題は通常経路から除去できた。

Exact FTSは250kで約186 ms。その後、Private Semantic paraphraseは2026-09-07にGemini Embedding 2へ製品移行し、Product Golden 8 Caseで8/8・false recall 0、Current similarity floor 0.70を確認した。詳細は`Nirai_PrivateMemorySemantic_検証結果_2026-09-06.md`を参照する。Private Structured ContinuityはCurrent blockerではなくChallengerとして残す。

Explicit Forgetはcompatibility JSONLから対象情報を物理除去するため、250kで約1.0秒。通常会話PathではないためCurrentでは許容し、必要性が実測された時だけcompatibility mirrorの退役・別方式を検討する。

## Public World Memory

### Episode全体rewriteの除去

旧compatibility EpisodeはRaw正本ではないが、従来は新発言ごとに既存Episode全文をread + rewriteしていた。

2026-09-06に次へ変更した。

- Raw SQLite insertが新規Entryだった時だけcompatibility Episodeへ追記
- 既存Episodeをreadしない
- file modeはappend-only
- duplicate判定はRaw stable IDへ委譲

自動回帰で、2発言目追加時に既存Episodeへの`Path.read_text()`を禁止しても成功することを確認した。

### 250,000 Public Raw

既存5年Dataはbulk Syntheticで構築し、その巨大状態に対するsteady-state operationを測定した。bulk fixture構築時間は製品の1件ずつの会話write throughputを示す値ではない。

| 項目 | 実測 |
|---|---:|
| 250k Raw + FTS fixture構築 | 12,864.94 ms |
| Exact FTS recall | 597.11 ms |
| compatibility Episode size | 12.02 MiB |
| そのEpisodeへ新規公開発話1件append | 13.06 ms |
| Episode増分 | 104 bytes |
| 50 Entryを含むSession Forget | 4,729.38 ms |
| Forget後同Query | 634.01 ms / 0 hits |
| Raw DB | 193.59 MiB |

### Scaleで発見したFalse Recall

初回250k Scaleでは、対象MemoryをForgetした後、Gemini Semanticが無いfallbackで弱いFTS候補を4件返した。

これは「怪しい記憶を出すくらいなら0件」のProduct原則へ反するため修正した。

Current fallback：

1. Strong / distinctive Exact evidenceは従来通りLocalで返す
2. Semanticが利用不能な場合、弱いLexical候補を無条件に返さない
3. `coverage >= 0.22`を満たす候補が**1件だけ**の時だけ非常用Local fallbackを許可
4. 複数候補ならabstain

既存の「青い貝殻」「珊瑚の洞窟」Recallは維持し、250k ScaleのForget後Queryは0 hitsへ改善した。

Public Exact Recall約0.6秒、Session Forget約4.7秒は5年Synthetic上でも実用範囲と判断する。ただし大量Session Forgetは通常Turnではないため、将来UX要求が出た場合の最適化候補とする。

## Historical Private BGE-M3 Resource補足

Serinaと同じMachine-level Ollama `bge-m3`を`num_gpu=0`で共有する実機Resource Testを追加した。

- `size_vram=0`
- short query p95：約263 ms
- 4-way short parallel p95：約478 ms
- 長いSerina相当Background embedding実行中のNirai query p95：約6.13秒

このCPU queue競合が実測されたことも、2026-09-07にNirai Private SemanticをGemini Freeへ移す判断材料になった。Current ProductはOllama / BGE-M3を使わず、Serina側CPU Runtimeと分離する。数値自体はHistorical比較Evidenceとして保持する。

## Privacy / Conversation回帰

- 20件tailより古いPrivate WhisperをLocal Retrieverで実Whisper Contextへ戻せる
- 同Memoryは`recent_whispers`には存在しなくても取得できる
- `private_memories`はPublic Talk Contextへ存在しない
- Private Retriever DBはResidentごとに物理分離し、LapanからKinaのPrivate Entryを検索できない
- Private Memory Evidenceはnative Working Contextへ一度注入後、同Contextが健全な間は再送しない
- context reset / compaction後は再注入可能
- Near-missは0件を正常系とする

## Verification

最終Core回帰：

- Core全体の最新件数は`AI_ENTRY.md`を正とする

Scale Runner：

- `.tools/run-private-memory-scale.py`
- `.tools/run-private-memory-scale.ps1`
- `.tools/run-world-memory-scale.py`
- `.tools/run-world-memory-scale.ps1`

## 残件

1. Private Structured Continuity / Atomic FactはCurrent Product blockerではなくChallenger。Golden拡張でRaw Hybrid不足が確認された時だけ再評価する
2. Serina等`external-resident` Private Memory Adapter
3. Public / Private sqlite-vec + Gemini Embedding Indexを年単位で運用した時のmaintenance / quota / rebuild benchmark
4. pre-birth World HistoryをPersonal Experienceと誤認しないContext delivery

Private SemanticはGemini Embedding 2で製品化済み。製品実装そのもののPrivate 8 CaseもStructured Fact Overlayなしで8/8 / false recall 0。Public / Privateは同じrolling 24h quota guardを共有し、Nirai RuntimeはローカルBGE-M3を使わない。
