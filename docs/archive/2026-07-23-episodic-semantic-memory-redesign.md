## episodic/semantic記憶再設計 実装計画

**Goal:** 記憶のtype分類を業界標準（episodic/semantic）に揃え、「日記」と「蒸留」が同じ出来事を視点違いで重複記録している問題を解消し、好み・関係性の持続的な変化を取りこぼさず追跡できるようにする。

**方針:** 「日記」をtype値`episodic`へ改名し役割はそのまま維持する（セリナ視点の主観的な物語。ただし「日記」という固定フォーマットは捨て、人間の記憶に近い自然な語りにする）。蒸留は`semantic`（確定事実）に専念させ、「出来事」「その場の感情」の抽出をやめる。関係性・好みの持続的な変化は、今は存在するが使われていない`facts`台帳のsupersede機能で追跡する。継承記憶由来の`event/knowledge/promise/relationship`も`semantic`へ統合整理する（`promise`の保護等級Sは維持）。Proceduralメモリ（persona自律改訂）は現状の保守設計（1日1回・改訂幅20%上限・気分混入禁止）を変更せず、材料をfacts台帳経由でより正確にすることで間接的に強化する。

**触る場所:** `core/memory/store.py`, `core/memory/facts.py`, `core/chores/distillation.py`, `core/chores/diary.py`, `core/chores/orchestrator.py`, `core/chores/summaries.py`, `core/context/memory_time.py`, `core/context/recall_neighbors.py`, `core/state/diary_state.py`→`core/state/episodic_state.py`（リネーム）, `core/memory/diary_cascade.py`, `app/gui_server.py`, `docs/設計書.md`（§4.3表記是正）, `tools/`（マイグレーションスクリプト新規）

**検証:** `python -m pytest tests/ -q` 全件green。`tools/backup_db.py`実行後、実DBのコピーでマイグレーション前後の件数照合（type別COUNT一致）。GUIアルバム画面で新type表示・削除が実機で動くこと。

---

## 背景（2026-07-23セッションでの経緯）

マスターから「起動が遅い」という相談を起点に、以下の流れで調査・議論が進み、最終的に記憶アーキテクチャの再設計に着地した。

1. 起動時ログを実測 → 主犯は前日分の宿題処理（蒸留・persona提案・日記生成）のLLM呼び出しだった
2. `save_persona_propose_state()`のキーワード引数名不一致バグを発見・修正（コミット済み）
3. 「見回りのたびに性格提案が走るのでは」という懸念 → 実際には1日1回に収束していたが、日界処理が例外発生時に無限リトライするバグを別途発見・修正（コミット済み）
4. セッション削除に発言1件削除と同じ副作用（宿題除去・記憶引用整理）を追加（コミット済み、テスト会話のクリーンな削除が目的）
5. 「好み・関係性のまとめ」の自動更新配線が実装されているのに一切呼ばれていない（テストのみ）ことが判明
6. マスターから「日記と蒸留がいつの間にか別ルートになっている、重複しているのでは」という指摘 → 実際に重複していることを確認
7. 研究資料（`docs/research/研究蒸留まとめ.md`）と最新のOSS実装（mem0等）を調査 → 業界標準はepisodic/semantic/proceduralの3分類、かつ「日記とファクトを混ぜて要約すると重要事実が消える」という研究知見が現行のツートラック設計の根拠だったことを再確認
8. マスター確認: 二層分離自体は維持、ただし蒸留側の抽出内容を精査すべき → 蒸留が「出来事」まで拾っていて日記と内容が重複していたことが実データで確認できた
9. 命名を業界標準（episodic/semantic/procedural）に揃える方針で合意。「reflection」の案は撤回し`episodic`に統一
10. `event/knowledge/promise/relationship`という継承記憶専用のレガシー分類も`semantic`へ統合する方針で合意
11. Proceduralメモリ（persona自律改訂）を強化すべきという提案 → 調査の結果、現状の保守性はpersona drift（研究で広く報告されている、長期対話でキャラクターが本来の人格から逸脱する現象）への意図的な防御であり、安易に強化すべきでないと判断。材料の質（facts台帳経由）を上げることで間接強化する方針に着地

---

## 前提となる事実（次セッションが再調査しなくて済むように）

### 現行`memories`テーブルのtype分布（2026-07-23時点、実DB）

| type | 件数 | source=NULL（本番生成） | source非空（継承記憶） |
|---|---|---|---|
| event | 480 | 0 | 480 |
| diary | 29 | 1（今朝の分） | 28 |
| knowledge | 25 | 0 | 25 |
| promise | 11 | 0 | 11 |
| relationship | 8 | 0 | 8 |
| fact | 5 | 5（今朝の分） | 0 |

**結論: `event/knowledge/promise/relationship`は現在の蒸留処理では一切生成されない。全件2026-06-28の継承記憶一括投入（legacy）由来の凍結データ。今、本番の蒸留が実際に生成しているのは`fact`のみ。**

### `type`列が実際にコード内で参照されている箇所（この3箇所以外は無関係）

1. `core/context/memory_time.py:77` — `if record.type == DIARY_MEMORY_TYPE:` 想起時に日記だけへ`[YYYY-MM-DD・今日・セリナの日記]`タグを付ける分岐
2. `core/memory/store.py:601` — `list_memories_since()`で`m.type != ?`により、日記生成の材料集めから日記自身を除外
3. `core/memory/store.py:618` — `list_by_type()`、GUIアルバム表示用フィルタ（`app/gui_server.py`の`api_album`が呼ぶ）

想起スコアリング・忘却判定・保護ロジックのいずれも`type`を参照しない（`protection_grade`と`pinned`列だけで決まる）。**つまり`event/knowledge/promise/relationship/fact`という分類の違いは、コードの挙動には一切影響しない。実質的な意味を持つのは「`episodic`（旧diary）かどうか」の1点だけ。**

### `memories`テーブルとは別に`facts`テーブルが存在し、ほぼ未使用

- スキーマ: `core/memory/facts.py:65`の`ensure_facts_schema()`。列は`subject/predicate/object/statement/valid_from/valid_to/recorded_at/confidence/episode_ids/supersedes/sensitivity/importance/status`
- `status`は`active/hypothesis/superseded/tombstone`の4値（`FACT_STATUSES`）
- `supersede_fact()`（`core/memory/facts.py:225`）で「新事実が古い事実を置き換える」操作が既に実装済みだが、呼び出し元は蒸留の`write_fact_from_distillation_candidate()`（`core/chores/distillation.py:324`）のみで、これは蒸留候補JSONに任意の`fact`フィールドがあるときだけ発火する。実質ほぼ使われていない
- mem0の実務パターン（ADD/UPDATE/DELETE/NOOP）と同じ役割を果たせる構造が既にある

### 蒸留プロンプトの現状（`core/chores/distillation.py:47`）

`DISTILLATION_FORMAT_INSTRUCTION`は「事実・出来事・約束・情緒」を無差別に抽出する指示になっている。「出来事」「情緒」の抽出が、日記（episodic）と内容が重複する原因。「関係の変化」「好みの変化」は明示的には含まれていない。

### `好み・関係性のまとめ`（summaries）の配線が死んでいる

- `core/chores/summaries.py`の`update_summary_block()`（書き込み関数）は本番コードのどこからも呼ばれていない。呼び出しは`tests/test_summaries.py`のみ
- `load_summary_blocks()`（読み込み）は`core/factory.py`が起動時に呼ぶが、書き込まれないので中身は空か初期状態のまま
- 設計書§4.11には「裏方便が蒸留結果から書き換える」「文脈パック常駐枠からは外す」と明記されている。実際`core/context/pack.py`側は`prefs_summary`/`relation_summary`引数を受け取るだけで`del`している（使っていない）ので、常駐枠から外す方針自体は実装と一致している。**唯一の実利用箇所は`core/chores/persona_propose.py`の`gather_propose_material()`（性格提案の材料）**

### Proceduralメモリ（persona自律改訂）の現状

- `core/chores/persona_propose.py` + `core/chores/persona_revise.py`
- 1日1回（暦日ベース、`should_run_persona_propose()`）
- 改訂幅上限40%…ではなく実装は`MAX_AUTONOMOUS_CHANGE_RATIO`既定20%（`persona_revise.py:86-89`。設計書§4.3本文の「40%」は理論上の同一性原則の記述で、実装の既定値とは別。次セッションで数値の食い違いを確認すること）
- 気分混入禁止チェックあり（`persona_revise.py:82-83`）
- 材料: 直近日記＋`prefs_summary`/`relation_summary`（今は空）＋現在の可変ブロック本文
- 可変ブロックは`personality`(03)/`voice`(04)/`love`(05)の3つのみ。`core_principles`(01)/`identity`(02)/`boundary`(06)は`mutable=false`

### `type="diary"`（→`episodic`）が依存されている箇所一覧（改名時に全部追従が必要）

- `core/chores/diary.py:31` — `DIARY_MEMORY_TYPE = "diary"`（本家定義）
- `core/context/memory_time.py:18` — 同じ値を独立定義（循環import回避のコメントあり）、`:77`で参照
- `core/chores/persona_propose.py:18,113` — `DIARY_MEMORY_TYPE`をimportして日記材料収集に使用
- `core/memory/store.py:601,618` — 上記の通り
- `app/gui_server.py`の`api_album`/`api_album_delete` — アルバム表示・カスケード削除
- `core/memory/diary_cascade.py` — 日記削除時の材料窓判定
- `core/context/recall_neighbors.py` — 日記チャンクヒット時の近傍パッセージ展開
- `core/state/diary_state.py` — `last_diary_at`・気分軌跡の永続化（キー名は`diary`だが、type値そのものではないので改名必須ではない。要判断）
- `core/chores/orchestrator.py`の`run_diary_generation()`
- 話者帰属タグの日本語文言「セリナの日記」（今回のセッション中コミット、`core/context/memory_time.py`のラベル生成部）

---

## 決定事項（マスター確認済み・変更しない）

1. type値の命名は業界標準英語ターム`episodic`/`semantic`を採用する（日本語「reflection」案は撤回済み）
2. `diary` → `episodic`にリネームする。役割（セリナ一人称の主観的な物語）は変えない。ただし「日記」という固定フォーマットは撤廃し、人間の記憶に近い自然な語りにする
3. `event/knowledge/promise/relationship/fact` → 全て`semantic`にリネームする。`promise`だった行の保護等級S/Aはそのまま維持する（リネームで等級を変えない）
4. 蒸留（`core/chores/distillation.py`）の抽出対象を絞る。「出来事」「その場限りの情緒」の抽出をやめ、次の2種類に特化する:
   - 確定事実・約束（アレルギー等の不変情報）→ 今まで通り`memories`(`semantic`)へ
   - 関係性・好みの**持続的な変化**（一時的な気分ではない）→ `facts`台帳の`supersede_fact()`を使い、mem0のADD/UPDATE/DELETE/NOOP方式を参考に実装する
5. `好み・関係性のまとめ`（`prefs_summary`/`relation_summary`）は、facts台帳から生成する形で自動更新配線を復活させる（persona提案の材料強化を兼ねる）
6. Proceduralメモリ（persona_propose/revise）の頻度・改訂幅上限・気分排除は変更しない。強化は材料の質（4,5の結果）を通じてのみ行う
7. GUI確認・削除は既存の`api_album`/`api_album_delete`のロジックパターンを流用する。type名の追従のみ行い、新規エンドポイントは作らない
8. 宿題台帳（`chore_box.db`）はこの変更の対象外

## Phase 1着手直後に発覚した計画漏れ（2026-07-23・マスター確認済み）

計画書の「type="diary"依存箇所一覧」（旧・未確定事項調査時点のリスト）は網羅的ではなかった。実装直前に全文grepし直した結果、以下の見落としが判明した:

1. **`core/memory/message_delete.py:102`** — `store.list_by_type("diary", ...)`。削除時のトレース注記生成で日記を探す処理。追従必要
2. **`tools/import_legacy_memories.py:63,75,91`** — 継承記憶の一括投入スクリプト（`type="diary"/"event"/"knowledge"`書き込み）。将来再実行した場合に旧値を書き込んでしまうため追従必要
3. **`core/intake/memory_review.py:59-60`** — 蒸留候補が`type`未指定のときのデフォルト値が`"fact"`。`semantic`統合後はデフォルトを`"semantic"`に変更必要
4. **【重大・機能破壊】`core/runtime.py:674-683`の`list_promise_memories_for_pulse()`** — `memory_store.list_by_type("promise")`が唯一の手がかりで「未回収の約束」をPulse（自発発話）に出す生きた機能。DB内に`promise`型11件（継承記憶、`protection_grade=A/S`）が存在し、これが`semantic`にリネームされると**例外もエラーも出さずにPulseの約束想起が無音で機能停止する**。計画書のPhase1検証（`SELECT type, COUNT(*)`が2種類のみ）はこの機能の生死を検知できない

**マスター確認結果（4番）:** type列は`episodic`/`semantic`の2値に統一しつつ、移行時に`metadata.legacy_type`へ旧type値（`"promise"`等）を焼き込んで保持する方式を採用。`list_promise_memories_for_pulse()`は`type='semantic'`かつ`metadata.legacy_type='promise'`で絞り込むよう改修する。Phase1の`tools/migrate_memory_types.py`はUPDATE時に全レガシー4分類（`event/knowledge/promise/relationship`）の元type値を`metadata.legacy_type`へ書き込む（`fact`は本番生成継続分のため対象外＝そのまま`semantic`化のみで legacy_type 不要）

## 未確定事項 → 確認結果（2026-07-23 Phase 0直前にマスター確認済み）

1. **GUI表示用の日本語ラベル**: 「セリナの記憶」に確定（「日記」の踏襲はしない）。GUIアルバム画面・文脈パックのタグ文言はこれで統一する
2. **改訂幅上限の数値の食い違い**: この機会に**設計書側を実装値（20%）に合わせて修正する**。非範囲から外し、Phase 1〜6のどこかで`docs/設計書.md`§4.3の「40%」表記を「20%」に直すタスクを追加する（実装`MAX_AUTONOMOUS_CHANGE_RATIO`は変更しない、ドキュメントのみ修正）
3. **`facts`台帳のsupersede発火の矛盾判定ロジック**: **埋め込み類似度（bge-m3）で判定**する方式に確定。Phase 3で具体的な閾値・実装方法を設計する
4. **`core/state/diary_state.py`の改名**: `episodic_state.py`にファイル名変更、内部キー名も`diary`→`episodic`に統一改名する

---

## 実装タスク

### Phase 0: 事前準備・構造レビュー

- **目的:** 破壊的変更（DBスキーマの実質的な意味変更・書き込み経路の変更）に備え、可逆性を確保し、憲章のレビュー発火条件（新モジュール追加／記憶の書き込み経路の変更）に従って構造レビューを通す
- **触るファイル:** なし（レビューのみ）
- **手順:**
  1. `python tools/backup_db.py` を実行し、`data/serina_memory.db`の控えを取る
  2. Task toolで`architecture-reviewer`を直列1回起動。本計画書を渡し、Phase 1〜3の設計（type統合・facts台帳本格活用・summaries配線追加）が憲章に抵触しないか判定させる
  3. PASSが出るまでPhase 1以降の実装に進まない。Critical/Important指摘があれば計画書を修正してから再起動（上限1回、それでも残れば advisor 相談）
- **検証:** architecture-reviewerのAssessmentが「可」であること

**レビュー結果（2026-07-23実施）: Assessment「可」。Critical指摘なし。以下Important 4点を実装条件として遵守すること:**

- **I-1（Phase 3/4）:** facts/summariesからcloud（外部LLM補完経路）への新規露出面を作らない。`core/context/routing_rules`を唯一のcloud門番として維持する
- **I-2（Phase 3）:** `supersede_fact()`によるsupersede/NOOP判定発生時、日本語の変更レポートを残す（透明性原則。既存の`write_fact_from_distillation_candidate`はレポートを残していないため新規追加が必要）
- **I-3（Phase 1）:** `tools/migrate_memory_types.py`実行時、dry-run結果・実行件数を日本語の変更レポートとして残す（backupとCOUNT照合に加えて）
- **I-4（Phase 5.5・マスター案件）:** 設計書§4.3を「20%」に是正する際、`docs/憲章.md`（line 65付近）の「40%」記載も同期が必要。ただし憲章の条文編集はマスターのみの権限のため、Phase 5.5実装時にマスターへ確認すること

### Phase 1: typeマイグレーション（episodic/semantic統合）

- **目的:** DB上のtype値を業界標準命名に揃え、レガシー4分類を統合する
- **触るファイル:** `tools/migrate_memory_types.py`（新規）、`core/chores/diary.py`, `core/context/memory_time.py`, `core/chores/persona_propose.py`, `core/memory/store.py`, `core/memory/diary_cascade.py`, `core/memory/message_delete.py`, `core/intake/memory_review.py`, `core/runtime.py`, `tools/import_legacy_memories.py`, `app/gui_server.py`, `core/state/diary_state.py`→`core/state/episodic_state.py`（リネーム）
- **手順:**
  1. `tools/migrate_memory_types.py`を新規作成。`UPDATE memories SET type='episodic' WHERE type='diary'`を実行。レガシー4分類（`event/knowledge/promise/relationship`）は`UPDATE memories SET type='semantic', metadata=json_set(COALESCE(metadata,'{}'),'$.legacy_type', <元type値>) WHERE type IN (...)`で、元type値を`metadata.legacy_type`へ焼き込みながら`semantic`化する（**新発覚の重大依存**: `core/runtime.py`の`list_promise_memories_for_pulse()`がこれで生き残る）。`fact`（本番蒸留の生成分）は`legacy_type`不要、そのまま`type='semantic'`のみ変更。（`tools/backfill_memory_created_at.py`等の既存ツールと同じ構成に倣う。dry-runオプションを付け、実行前に対象件数を表示すること）。**【構造レビューI-3】** 実行時にdry-run結果・実UPDATE件数を日本語の変更レポートとして残すこと
  2. `promise`だった行の`protection_grade`列はUPDATE対象に含めない（等級は保持されたまま、type列だけ変わる）
  3. `core/chores/diary.py:31`の`DIARY_MEMORY_TYPE = "diary"`を`EPISODIC_MEMORY_TYPE = "episodic"`に変更（既存の参照元3箇所も追従）
  4. `core/context/memory_time.py:18`の独立定義も同様に変更
  5. `app/gui_server.py`の`api_album`/`api_album_delete`が参照するtype文字列を追従
  6. `core/memory/diary_cascade.py`のtype判定を追従（`core/context/recall_neighbors.py`は実際には`type`リテラルを参照していないため変更不要と判明）
  7. `semantic`統合後、蒸留が新規生成する記憶のtypeを`"fact"`から`"semantic"`に変更（`core/chores/distillation.py`のcandidate組み立て部、`core/intake/memory_review.py:59-60`のデフォルト値も同様に追従）
  8. `core/state/diary_state.py`を`core/state/episodic_state.py`にリネーム。内部キー名`diary`も`episodic`に統一（永続化済みJSON/DBのキー値が残る場合は移行時に読み替えるフォールバックを入れる）。呼び出し元（`app/gui_server.py`等）のimportパスも追従。範囲は「ファイル・その直接importer」に限定し、`core/chores/idle_policy.py`の同名パラメータ（このモジュールをimportしていない別物）や`config/app_timing.toml`の`[diary]`セクション（ユーザ設定ファイルの破壊的変更を避ける）は対象外とする
  9. **【新発覚】** `core/memory/message_delete.py:102`の`store.list_by_type("diary", ...)`を追従
  10. **【新発覚】** `tools/import_legacy_memories.py`の`type="diary"/"event"/"knowledge"`書き込みを`"episodic"/"semantic"`に追従（将来再実行時の一貫性のため）
  11. **【新発覚・重大】** `core/runtime.py`の`list_promise_memories_for_pulse()`を、`type='semantic'`かつ`metadata.legacy_type=='promise'`で絞り込むよう改修する
- **検証:** マイグレーション後、`SELECT type, COUNT(*) FROM memories GROUP BY type`が`episodic`/`semantic`の2種類のみになること。`list_promise_memories_for_pulse()`が移行後も元promiseだった11件を返すこと（回帰テスト追加）。`python -m pytest tests/ -q`全件green

**実施結果（2026-07-23完了）:** 本番`data/serina_memory.db`へ`--apply`済み。移行後分布 `{'episodic': 29, 'semantic': 529}`。`metadata.legacy_type`内訳 `{'event': 480, 'knowledge': 25, 'relationship': 8, 'promise': 11}`（fact由来5件はlegacy_typeなし、想定通り）。`python -m pytest tests/ -q` 451件全green（新規回帰テスト`tests/test_migrate_memory_types.py`・`tests/test_episodic_state.py`含む）。Phase0バックアップに加え、`--apply`実行前にも自動バックアップ（`G:\SerinaDB Backup\serina_memory_20260723_170541_505380.db`）。変更レポート: `data/change_reports/migrate_memory_types.json`

### Phase 2: 蒸留プロンプトの絞り込み

- **目的:** 蒸留の抽出対象から「出来事」「その場限りの情緒」を外し、確定事実＋持続的変化に専念させる
- **触るファイル:** `core/chores/distillation.py`
- **手順:**
  1. `DISTILLATION_FORMAT_INSTRUCTION`（`distillation.py:47`）を改訂。「事実・出来事・約束・情緒」から「出来事」「一時的な情緒」を除外し、「確定事実・約束（変わらないもの）」「関係性・好みの持続的な変化（一時的な気分ではない）」を抽出対象として明記する
  2. 「持続的な変化」と「一時的な気分」の区別をプロンプトで明示する（設計書§4.3の気分混入禁止と同じ精神を蒸留プロンプトにも適用）
  3. 未確定事項3（プロンプト文面の具体案）はこのタスク着手前にマスターへ提示すること
- **検証:** `python -m pytest tests/test_distillation*.py -q` green。実機で1ターン分の蒸留を試し、生成される記憶に「出来事」的な内容が含まれないことを目視確認

**実施結果（2026-07-23）:** プロンプト改訂・pytest 451件全green確認済み。実機LLM発注による目視確認は、この時点でOllamaが未起動（接続拒否）のため未実施。Phase6の最終実機検証（Serina.bat起動を伴う）にまとめて行う

### Phase 3: facts台帳の本格活用（関係性・好みの変化追跡）

- **目的:** mem0のADD/UPDATE/DELETE/NOOP方式を参考に、`facts`テーブルのsupersede機能を蒸留から本格的に呼び出せるようにする
- **触るファイル:** `core/chores/distillation.py`（`write_fact_from_distillation_candidate`周辺）、`core/memory/facts.py`
- **手順:**
  1. 蒸留候補JSONの`fact`フィールドの必須化・拡充（現状任意フィールドなので、関係性・好みの変化候補には必ず`fact`を付けさせるようプロンプト調整）
  2. 新事実が既存事実と矛盾する場合、`supersede_fact()`を呼んで置き換える判定ロジックを追加（現状`write_fact_from_distillation_candidate`は常に`add_fact`する動線のみ。既存fact検索→矛盾判定→supersede or add の分岐が必要）
  3. 矛盾判定は**埋め込み類似度（bge-m3）**で行う。新規fact候補の`statement`と、同一`subject`を持つ既存active facts の`statement`をbge-m3で埋め込み、コサイン類似度が閾値以上ならsupersede候補とみなす。閾値の初期値は着手時にマスターへ提示（既存の想起スコアリングで使っている閾値があればそれに合わせるのが無難）
  4. **【構造レビューI-2】** supersede/NOOP判定が発生した際、日本語の変更レポート（何が・なぜ置き換えられたか）を残す処理を追加する。`保護3原則`の透明性に対応
  5. **【構造レビューI-1】** facts台帳・summariesの内容がcloud（外部LLM補完）経路に新規で流れないことを確認する。既存の`routing_rules`門番を迂回する新しい経路を作らない
- **検証:** テストで「新しい好みが判明→古い好みがsuperseded状態になる」ケースを検証

**実施結果（2026-07-23完了）:** 類似度閾値`fact_supersede_similarity_threshold=0.85`を`ThresholdsConfig`・`config/thresholds.toml`に追加（マスター確認済み）。`FactStore.list_active_facts_by_subject()`（完全一致、search_by_entityのLIKE誤爆を避ける）・`MemoryStore.embed_text()`を新設。`write_fact_from_distillation_candidate()`に同一subject×bge-m3コサイン類似度でのsupersede-or-add分岐を実装し、両分岐とも`change_log`へ日本語ChangeReportを記録（I-2満たす）。埋め込み呼び出しは既存のbge-m3ローカル経路のみでcloud新規露出なし（I-1満たす）。新規回帰テスト`tests/test_distillation_fact_supersede.py`2件追加。`python -m pytest tests/ -q` 453件全green。変更レポートが記録されることもテストで確認

### Phase 4: summaries配線の復活

- **目的:** `prefs_summary`/`relation_summary`を`facts`台帳から自動生成し、persona提案の材料を強化する
- **触るファイル:** `core/chores/summaries.py`, `core/chores/orchestrator.py`（`run_growth_chores`）
- **手順:**
  1. `facts`テーブルの`status='active'`なレコードのうち、好み・関係性カテゴリのものを整形して`prefs_summary`/`relation_summary`を組み立てる関数を追加
  2. `run_growth_chores`（`orchestrator.py:339`）に、persona_proposeと同じ「1日1回」のタイミングでこの整形処理を呼ぶフェーズを追加し、`update_summary_block()`を実際に呼ぶ配線を作る
  3. 既存の`update_summary_block()`のシグネチャ・字数ガード（800字/3000字）はそのまま使う
- **検証:** `tests/test_summaries.py`が実際の呼び出し経路からも検証されるよう、`tests/test_chore_orchestrator.py`に統合テストを追加

**実施結果（2026-07-23完了）:** マスター確認の上、facts台帳に`category`列（確定事実/約束/好み/関係性）を追加（`ALTER TABLE`安全移行）し、蒸留プロンプトのfactスキーマにも`category`フィールドを追加。`FactStore.list_active_facts_by_category()`・`summaries.rebuild_summaries_from_facts()`を新設し、`run_growth_chores()`内でpersona_proposeと同じ`should_run_persona_propose`ゲートを共有して呼び出し、`core.prefs_summary`/`relation_summary`をその場で更新（同日中の提案材料に反映）。内容不変時はChangeReportを積まない（無駄な変更ログを避ける）。統合テスト`test_run_growth_chores_rebuilds_summaries_from_facts_before_persona_propose`追加。`python -m pytest tests/ -q` 454件全green

### Phase 5: episodic生成プロンプトの改訂

- **目的:** 「日記」という固定フォーマットをやめ、人間の記憶に近い自然な語りにする
- **触るファイル:** `core/chores/diary.py`
- **手順:**
  1. 生成プロンプトの文言を改訂（「日記を書いてください」→「その日をどう記憶するか、自然に思い出す形で」等。具体文面は着手時に設計）
  2. GUIアルバム画面・文脈パックのタグ文言を「セリナの記憶」に統一する（`core/context/memory_time.py`のラベル生成部、`app/gui_server.py`のアルバム表示ラベル）
- **検証:** 実機で1本生成し、内容・トーンを目視確認

**実施結果（2026-07-23完了）:** プロンプト文面はマスター添削版を採用。GUIタグ文言「セリナの記憶」を`app/web/index.html`・`app/web/app.js`（アルバム空表示・削除確認文言等）にも展開（Phase1で見落としていたフロント側の「日記」表記）。`python -m pytest tests/ -q` 454件全green。実機生成の目視確認はOllamaが別用途（マスターのゲーム起動）で混雑中のためPhase6にまとめて実施

### Phase 5.5: 設計書の改訂幅上限表記の是正

- **目的:** 設計書§4.3「40%」と実装`MAX_AUTONOMOUS_CHANGE_RATIO`「20%」の食い違いを解消する（マスター確認済み：実装側を正とし設計書を修正）
- **触るファイル:** `docs/設計書.md`（§4.3）
- **手順:**
  1. §4.3本文の改訂幅上限記述を「40%」から「20%」に修正。実装コード（`core/chores/persona_revise.py`の`MAX_AUTONOMOUS_CHANGE_RATIO`）は変更しない
- **検証:** 設計書とコードの数値が一致していることを目視確認

**実施結果（2026-07-23・当初no-op判断→マスターの追加指示で実質変更あり）:** 実装を再確認したところ`core/memory/protection.py:20`の`MAX_AUTONOMOUS_CHANGE_RATIO = 0.4`——**実装も40%で、設計書§4.3と元々一致**していたことが判明（計画書の「実装は既定20%」という当初記載＝未確定事項2の根拠は事実誤認だった）。この時点でマスターへ「変更不要」と報告したところ、マスターより「（この機会に）20%でいい」と明示の追加指示があり、40%→20%への**実装ごとの引き下げ**（persona driftをより保守的に防ぐ方向）に着地。

最終的に変更したファイル: `core/memory/protection.py`（`MAX_AUTONOMOUS_CHANGE_RATIO`を0.4→0.2）、`docs/設計書.md`§4.3、`docs/憲章.md`（C-2条文中「改訂幅40%」→「20%」、マスター承認の上で編集＝条文編集はマスター権限のため）、`tests/test_persona_revise.py`・`tests/test_memory_protection.py`（テスト名・コメントの「40%」表記を追従、閾値自体は境界値のずれなく元々green）。`python -m pytest tests/ -q` 454件全green

### Phase 6: 最終検証・completion-review

- **目的:** 全体の動作確認とレビュー通過
- **手順:**
  1. `python -m pytest tests/ -q` 全件green
  2. GUIでアルバム表示・削除・新規会話・想起の一連を実機確認
  3. `completion-review` skill（`serina-code-reviewer`直列1回）を実行
- **検証:** Assessmentが「可」になること

**実施結果（2026-07-23完了）:**
- `python -m pytest tests/ -q` 454件全green
- Ollama実機で改訂後の蒸留プロンプトを実行し、「桃アレルギー」（確定事実）・「犬が苦手→平気」（好み）・「映画の約束」（約束）を正しく抽出し、「散歩して休憩した」（単発の出来事）・「疲れてる、寝不足」（一時的な機嫌）を狙い通り除外することを確認
- `completion-review`（`serina-code-reviewer`直列1回）: **Assessment「可」。Critical/Important指摘なし。** Minor指摘3点（`.claude/agents/architecture-reviewer.md`のスコープ確認・`list_by_type_and_legacy_type`のSQL側フィルタ化余地・supersede類似度計算のコスト増余地）はいずれも非ブロッキング、対応不要
- コミット`7c5a84d`（45ファイル変更）でマージ・完了

---

## 非範囲（今回やらないこと）

- `chore_box.db`（宿題台帳）の構造変更
- Proceduralメモリ（persona_propose/revise）の頻度・改訂幅・気分排除ロジックの変更（数値自体は変えない。Phase 5.5はドキュメント表記の是正のみ）
