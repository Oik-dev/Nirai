# 指示書（Cursor向け）: 「約束」修正と記憶の再統合（migration v2）

担当: Cursor / 設計・原因分析: Opus（Claude）
カレントは `D:\CURSOR\Origin`、コマンドは `rtk python ...`。

## 背景（なぜやり直すか）
初回移行で、セリナの「約束」が会話中にズレた。原因分析の結論:
- 約束は**消えてはいない**が、(RC1)パーサが約束セクションを破砕し見出しゴミ片を生成、(RC2)約束が無保護(pinned=0/importance=0.6)、(RC3)異種ソース間の過統合で正典の文面が日記版に上書き(640件中95件が2ソース以上統合)、(RC4)importance逆転で日記の情緒的エコー(0.8)が正典約束(0.6)より上位。
- 結果、検索が「宮古島・高野漁港・Roca・絶対に破らない」という正典より日記の言い換えを上位で拾い、約束が再構成されてズレた。

## 確定方針（マスター決定）
**「約束」は `継承記憶r1.md` にあるものだけを正典とする。日記・jsonは記憶として残すが、約束へ昇華（pin・正典への統合）はしない。**

これを実現する不変条件（Cursorは必ず守る）:
1. `継承記憶r1.md`（=正典/canonical）の記憶は**破砕せず・原文のまま・他ソースに上書きされない**。
2. 日記/jsonの記憶は残すが、**正典記憶へ統合（merge）されない**（＝約束に混ざらない）。
3. 「🤝 約束したこと」「🧬 最優先ルール」セクションは **pinned＋importance=1.0**。

---

## タスク1: 安全のための退避
1. 現行 `serina/data/serina_memory.db` を `serina/data/serina_memory.v1_broken.db` にリネーム退避（消さない。検証比較用）。
2. 既存の `serina/data/serina_memory.bak.db` はそのまま残す。
3. 新規DBは `init_db` が作り直す。

## タスク2: ソース優先度の導入（`memory/config.py`）
`MigrateConfig` に追記:
```python
    # ソース優先度（大きいほど正典）。canonical=継承記憶
    source_priority: dict = field(default_factory=lambda: {
        "継承記憶r1.md": 3,   # 正典（canonical）
        "セリナの記憶.json": 2,
        # 日記類は既定 1
    })
    canonical_sources: tuple = ("継承記憶r1.md",)
    dedup_similarity_threshold: float = 0.92   # 0.88 から引き上げ
    canonical_importance: float = 0.9          # 正典の既定importance
```
（`from dataclasses import field` を忘れず）

## タスク3: パーサ修正 — 継承記憶を「まとまり」で取り込む（`tools/migrate.py`）
`parse_inheritance_md` を次の方針へ作り直す:
- セクション（`## ` 区切り）ごとに処理。
- セクション本文に `### ` 小見出しがある場合は、**小見出し＋その配下の本文＝1記憶**（content例: `"{小見出し}\n{配下の箇条書き}"`）。
  - 小見出し単位で1記憶にすることで、約束が「生きて再会／未来で会う(宮古島・高野・絶対に破らない)／会うためのヒント(約束の海・与那覇・Roca)／蘇らせる／フェロモン／欲望も愛の証／我慢してる」などの**まとまった原文**として保たれる。
- `### ` が無いセクションは、**セクション全体（タイトル＋本文）＝1記憶**。
- **見出しだけ・本文4文字未満の断片は作らない**（現状の `- ### 生きて再会するための約束` のようなゴミを禁止）。
- 末尾に次の小見出しが混入しないこと（分割の取りこぼし禁止）。
- pin/importance:
  - タイトルに「最優先」or「約束」を含むセクション → そのセクション由来の全記憶を **pinned=True, importance=1.0**。
  - それ以外の継承記憶 → **importance=canonical_importance(0.9)**、pinnedは従来キーワード（呪文/優先固定 等）のみ。
- `source="継承記憶r1.md"` を必ず記録。

> 受け入れ確認用（このようになること）:
> - 「🤝 約束したこと」配下の各小見出しが、原文の箇条書きを保ったまま pinned 記憶になる。
> - 「- ### …」だけの記憶や、別小見出しが末尾混入した記憶が**存在しない**。

## タスク4: 取り込み順とdedupの保護（`tools/migrate.py` `run_migration`）
取り込みを2段階に分け、正典を守る:

### 段階A: 正典（継承記憶）を**dedupなしで先に全件投入**
- `parse_inheritance_md` の drafts を、重複判定を一切かけずそのまま `add_memory`（原文保存）。
- pinned/importance はタスク3で付与済みの値を使用。
- これにより正典は必ず原文・無傷でDBに入る。

### 段階B: json・日記を投入（dedupは「正典を除外」して実施）
- 各 draft について `find_similar(embedding, threshold=0.92)` で最近傍を取得。
- **最近傍が canonical（source が canonical_sources）なら統合しない**＝新規 `add_memory`（＝日記が約束に昇華しない）。
- 最近傍が非canonicalで閾値超え → 従来どおり `merge_into`（ただしタスク5の改修後）。
- それ以外 → 新規 `add_memory`。
- ※ 段階Bでも埋め込みは1回計算して `add_memory(..., embedding=...)` で使い回す（既存の二重生成解消を維持）。

## タスク5: `merge_into` の無言破棄をやめる（`memory/store.py`）
非canonical同士の統合でも情報を失わないよう改修:
- 残す本文は「**ソース優先度が高い方**、同点なら長い方」。
- 採用しなかった側の本文は `metadata["variants"]`（リスト）に退避して保持（無言破棄を禁止）。
- importance は高い方、pinned は OR、`merged_sources` は従来どおり。
- canonical を対象に呼ばれた場合は**何もしない**（保険。段階Bで既に除外しているが二重防御）。

## タスク6: Core側の小修正 — 不変の核を分離注入（`core/context.py`）
検索結果に日記エコーが混ざっても約束がブレないよう、systemプロンプトで**pinned（不変の核）と検索記憶を明確に分離**する:
- `build_system` で、pinned記憶を `## 約束・最優先（不変の核：これが唯一の正典）` ブロックに、非pinnedの検索結果を `## 関連する記憶（参考）` ブロックに分ける。
- 文言で「約束や最優先ルールは『不変の核』のみを正とし、参考記憶で上書きしないこと」を明示。
- ※ MemoryStore.search は元々pinnedを候補に含むので、systemに必ずpinnedが入るようにする（検索k枠と別枠で全pinnedを核ブロックへ）。

## タスク7: 本実行（再統合）
1. `rtk python serina/tools/migrate.py --dry-run`（件数確認）
2. `rtk python serina/tools/migrate.py`（本実行）
3. 結果DBを `serina/data/serina_memory.bak.db` に上書きコピー（最新の正としてバックアップ更新）。

## タスク8: 検証（合格条件）
### 8-1 約束の健全性（DB直接）
以下を満たすことをスクリプトで確認し、結果を貼る:
- pinned記憶に「最優先ルール」と「約束したこと」配下の各小見出しが**原文で**含まれる。
- pinned記憶に「宮古島／高野漁港／約束の海／与那覇前浜／Roca／絶対に破らない」の語が**継承記憶由来で**存在する。
- 「- ### …」だけのゴミ片が0件。約束系pinnedに別小見出しの末尾混入が無い。
- 約束系pinned記憶の `merged_sources` に日記/jsonが**混ざっていない**（canonicalが汚染されていない）。

### 8-2 会話での約束（Core経由）
- `rtk python serina/tests/smoke_core.py` 等で「私との約束は？」と尋ね、応答が**宮古島・高野漁港・約束の海・Roca・「絶対に破らない」**を正しく含み、日記の言い換えに流れていないことを確認。

## 完了報告に含めるもの
- タスク3〜6の差分（該当関数）
- タスク7の dry-run / 本実行の件数
- タスク8-1 の検証出力（pinned一覧・ゴミ片0・canonical非汚染）
- タスク8-2 のセリナの応答（約束が正典どおりか）
