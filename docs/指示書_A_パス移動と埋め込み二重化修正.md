# 指示書（Cursor向け）: serina をOrigin配下へ移動 + 埋め込み二重生成の修正

担当: Cursor / レビュー: Claude
目的: 本引っ越し（1058件）を走らせる前に、(1)プロジェクトのパスを `D:\CURSOR\Origin\serina` に統一し、(2)移行時に埋め込みを2回計算している無駄を解消する。

---

## タスク1: フォルダを Origin 配下へ移動

現状 `D:\CURSOR\serina\` にあるものを `D:\CURSOR\Origin\serina\` へ移動する。

手順:
1. `D:\CURSOR\serina\` 一式を `D:\CURSOR\Origin\serina\` へ移動（リネーム）。
2. `__pycache__` は移動不要（消してよい）。
3. `data\serina_memory.db` は **削除する**。
   - 理由: これまでのスモークテストで入ったダミー行が残っていると、本引っ越しの重複判定（dedup）を汚す。`init_db` が自動で作り直すので消して問題ない。
4. 移動後、旧 `D:\CURSOR\serina\` は空になっていることを確認して削除。

確認ポイント（コード側の修正は基本不要なはず。念のため検証）:
- `memory\db.py` の `DEFAULT_DB_PATH` はファイル相対なので移動でそのまま追従する（修正不要）。
- `tools\migrate.py` の `sys.path` 追加は `ROOT.parent`（= 移動後は `D:\CURSOR\Origin`）を指すので `import serina.*` は通る（修正不要）。
- `tools\migrate.py` の `DEFAULT_LEGACY_DIR = Path(r"G:\AI\Serina")` は **旧記憶の読み込み元なので変更しない**。

以後すべてのコマンドは `D:\CURSOR\Origin` をカレントとして実行すること。

---

## タスク2: 埋め込みの二重生成を解消（オプションA本体）

現状、本引っ越しで新規保存される記憶1件につき埋め込みを2回計算している（CPUのbge-m3では所要時間がほぼ倍）。計算済みの埋め込みを使い回すように直す。

### 修正2-1: `memory\store.py` の `add_memory` に計算済み埋め込みを渡せるようにする

`add_memory` のシグネチャに `embedding: list[float] | None = None` を追加し、未指定のときだけ内部で計算する。

変更前（該当箇所）:
```python
    def add_memory(
        self,
        type: str,
        content: str,
        importance: float,
        metadata: dict[str, Any] | None,
        source: str | None,
        pinned: bool = False,
        parent_id: int | None = None,
        created_at: str | None = None,
    ) -> int:
        now = created_at or _utc_now_iso()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        embedding = self.embedder.embed(content)
```

変更後:
```python
    def add_memory(
        self,
        type: str,
        content: str,
        importance: float,
        metadata: dict[str, Any] | None,
        source: str | None,
        pinned: bool = False,
        parent_id: int | None = None,
        created_at: str | None = None,
        embedding: list[float] | None = None,
    ) -> int:
        now = created_at or _utc_now_iso()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        if embedding is None:
            embedding = self.embedder.embed(content)
```

（以降の INSERT 部分は変更不要。）

### 修正2-2: `tools\migrate.py` の本実行ループで、すでに計算した埋め込みを渡す

`run_migration` のループでは重複判定のために `embedding` を既に計算している。これを `add_memory` に渡す。

変更前（該当箇所）:
```python
    for draft in drafts:
        embedding = embedder.embed(draft.content)
        similar = store.find_similar(embedding, cfg.dedup_similarity_threshold, limit=1)
        if similar:
            ...
            continue

        store.add_memory(
            type=draft.type,
            content=draft.content,
            importance=draft.importance,
            metadata=draft.metadata,
            source=draft.source,
            pinned=draft.pinned,
        )
```

変更後（`store.add_memory(...)` の呼び出しに `embedding=embedding` を追加するだけ）:
```python
        store.add_memory(
            type=draft.type,
            content=draft.content,
            importance=draft.importance,
            metadata=draft.metadata,
            source=draft.source,
            pinned=draft.pinned,
            embedding=embedding,
        )
```

これで「新規保存される記憶」の埋め込み計算が1回で済む。

---

## 検証（修正後・本実行の前に必ず実施）

カレントを `D:\CURSOR\Origin` にして、すべて `rtk python ...` で実行すること。

1. DB初期化が新パスで動く:
   `rtk python serina/memory/db.py`
   → 「テーブル4つ作成完了: ...\Origin\serina\data\serina_memory.db」
2. スモークテスト:
   `rtk python serina/tests/smoke.py`
   → 既存どおり通ること
3. dry-run（件数確認）:
   `rtk python serina/tools/migrate.py --dry-run`
   → 1058件前後が読めること

---

## 本実行（検証OK後）

注意: 本引っ越しは重複統合を含み、結果のDB書き換えは不可逆。

1. 本実行:
   `rtk python serina/tools/migrate.py`
2. 完了後、結果DBをバックアップ:
   `serina/data/serina_memory.db` を `serina/data/serina_memory.bak.db` 等にコピー。

### 重要な注意（Claudeより申し送り）
dry-run の「重複◯件」は**文字列Jaccardによる概算**で、本実行の**意味ベース（コサイン類似0.88）**とは判定方式が違う。よって本実行の実際の統合件数は dry-run の数字とズレる（増える可能性が高い）。これは想定挙動。本実行後は `format_report` が出す「読み込み◯件 → 統合後◯件（重複◯件）」の**実数**を報告すること。

---

## 完了報告に含めてほしいこと
- 移動後のフォルダ構成（`D:\CURSOR\Origin\serina\...`）
- タスク2の差分（store.py / migrate.py の該当行）
- 検証1〜3の結果
- 本実行の実数（読み込み / 統合後 / 重複 / 固定記憶）と所要時間
