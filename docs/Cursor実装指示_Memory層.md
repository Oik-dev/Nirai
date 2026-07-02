# Cursor 実装指示書 — Serina Memory層（v1）

設計の根拠は `設計書.txt`（9章・付録A）を参照。本書はその実装手順。
**Opusが設計、Cursorが実装**。各ステップに「完了条件（人間が目で確認できる形）」を必ず用意すること。

---

## 0. 前提・環境
- OS: Windows 11 / 言語: Python（venv推奨）
- 永続化: SQLite ＋ **sqlite-vec**（`pip install sqlite-vec`）
- 埋め込み: Ollama の **bge-m3**（`ollama pull bge-m3`）、**CPU実行**で呼ぶ（対話用12BのVRAMを侵食しない）
- 原則: Memory層は **CRUD＋検索のみ**。何を覚えるか/いつ整理するかの判断は持たない（Coreの責務）

## 1. プロジェクト構成（D:\CURSOR\serina\ を想定）
```
serina/
  memory/      db.py（初期化・接続・スキーマ） / store.py（記憶API）
  connectors/  embedder.py（埋め込み器：bge-m3, CPU）
  tools/       migrate.py（旧記憶の引っ越し＋重複統合）
  data/        serina_memory.db（生成物・gitignore対象）
  tests/       smoke.py（動作確認用）
```

## 2. 実装ステップ（この順で。各ステップ単体で検証可能に）

### Step1: DB初期化（memory/db.py）
- `serina_memory.db` を作成し sqlite-vec をロード、付録Aの4テーブルを作成
  （profile / memories / memory_vec[FLOAT[1024]] / history ＋ 各index）
- **完了条件**: スクリプト実行後「テーブル4つ作成完了」と表示され、data/にdbが出来る

### Step2: 埋め込み器（connectors/embedder.py）
- インターフェース `embed(text) -> float[1024]` を定義し、Ollama bge-m3 実装を1つ用意
- store.py へは**注入**で渡す（store.pyはOllamaを直接importしない＝疎結合）
- **完了条件**: 任意の文字列を渡すと長さ1024の数値配列が返り、それを表示できる

### Step3: 記憶API（memory/store.py）
実装する操作（これ以外は作らない）:
- `add_memory(type, content, importance, metadata, source, pinned=False)` … 保存時に埋め込みも自動生成しmemory_vecへ
- `search(query, k, type=None)` … 下記Step4のランキングで上位k件
- `get / update / delete / pin / unpin`
- `add_history(session_id, role, content)` / `get_recent_history(session_id, n)`
- `get_profile(key)` / `set_profile(key, value)`
- **完了条件**: 記憶を3件入れて `search` で関連順に取り出せることをsmoke.pyで表示

### Step4: ハイブリッド検索ランキング
- スコア = α·関連度 + β·新しさ + γ·重要度
  - 関連度 = ベクトル類似度（sqlite-vecで近傍取得）
  - 新しさ = 最終想起からの指数減衰
  - 重要度 = importance（0–1）
- **pinned=1 は減衰を無視して常に候補に含める**
- 想起したら対象の `last_accessed` を今に、`access_count`+1
- 係数 α/β/γ は設定値として外出し（後で調整可能に）
- **完了条件**: 「固定記憶は古くても必ず出る」「新しい記憶ほど上位」をsmoke.pyで確認

### Step5: 旧記憶の引っ越し＋重複統合（tools/migrate.py）
- 入力: `G:\AI\Serina\` の 継承記憶r1.md / セリナの記憶.json / セリナの日記×4
- 処理:
  1. 各記録を小さな記憶へ分解（要約→出来事/洞察→知識/情緒・関係→付帯情報metadata）
  2. 「呪文」「優先固定」等の語を含むものは pinned＋importance最大
  3. **重複統合**: 取り込む記憶を既存と埋め込み類似度で照合し、閾値超えはマージ
     （詳しい版を本文に残し、metadataと出所を統合、importanceは高い方、pinnedはOR）
  4. source に由来ファイルを記録
- **完了条件（重要）**: 最後に**日本語レポート**を表示
  例:「読み込み 120件 → 統合後 78件（重複42件をまとめた）。固定記憶 5件。」
- ※ 破壊的なので、まず**dry-run（DBに書かず件数だけ表示）**を用意し、確認後に本実行

### Step6: スモークテスト（tests/smoke.py）
- 上記Step3〜5を通しで叩き、人間が読める結果を標準出力に出す

## 3. 検証の流れ（非エンジニアでも確認できるよう）
各ステップは「実行 → 日本語で結果表示」をセットにする。
マスターは表示されたレポート/件数を見て進捗を判断する（コードは読まない前提）。

## 4. 保留事項（実装前に決め切らない・後続で扱う）
- **2台間の記憶DB同期**: コードはGitHub同期で良いが、`serina_memory.db`（個人データ・更新頻繁）をGitに乗せるのは非推奨。
  当面は**メイン機を正本**とし、DB同期方式は別途設計（gitignoreに追加しておく）。
- reflection（生記憶→蒸留）の自動発火はCore実装時に対応。今回はparent_idの器だけ用意。

## 5. やらないこと（スコープ外＝作り込まない）
- 階層メモリの自己編集（MemGPT流）/ ナレッジグラフ（将来Core/Skills側で検討）
- 外部サービス連携 / 対話ループ本体（Memory層の次フェーズ）
