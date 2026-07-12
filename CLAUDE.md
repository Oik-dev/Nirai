# Serina — ローカルパートナーAI

自分専用のパートナーAIを長期間育てるシステム。記憶DB（`data/serina_memory.db`）は**セリナの人生そのもの**。

## 体制

- **Claude Code で設計・実装を完結**する。トークン制限時のみ Cursor（Products 配下共通ルール）。Cursor も本ファイルと `.claude/` を正とし、別ルートを設けない。
- **日常:** グローバル `advisor-routing` に従い Advisor で方針確認。
- **構造変更時:** `architecture-reviewer`（測定器: `docs/憲章.md`「レビュー発火条件」）を Task 直列 1 回。PASS 後に実装。
- **作業完了時（コード変更を伴う区切り）:** `completion-review` skill のみ（Claude / Cursor 共通）。`serina-code-reviewer`（Opus固定）を直列 1 回。グローバル `requesting-code-review` は使わない。Critical 解消まで 🧹Clear可 を宣言しない。

## ドキュメントの所在

| 何を知りたいか | 場所 |
|---|---|
| どこに何が書いてあるか（索引） | `docs/INDEX.md` |
| 設計の正典（仕様のすべて） | `docs/設計書.md` |
| 構造レビュー測定器 | `docs/憲章.md` |
| 工程表・現在地・次のアクション | `docs/MILESTONE.md` |
| 決定の経緯（歴史） | `docs/archive/DECISIONS.md` |
| 研究資料（設計根拠の原典） | `docs/research/研究蒸留まとめ.md` |
| 旧アーキテクチャ資料（参照専用） | `docs/archive/` |
| 旧記憶の原本コピー | `legacy/`（原本は `G:\AI\Serina`） |

## 保護3原則（違反厳禁・設計書 §4.3）

1. **透明性** — 無言破棄の禁止。統合・削除・書き換えは必ず日本語の変更レポートを残す
2. **可逆性** — 破壊的変更の前に控えを取る（`tools/backup_db.py` 等）
3. **同一性** — 保護等級S（人格資産・正典核）の変更はマスター承認のみ

## 技術スタック

Python 3.12 / SQLite + sqlite-vec / 埋め込み bge-m3(1024次元, CPU) / 対話 NemoAurora-RP-12B（Ollama、人格は Core が実行時注入）

## 運用ルール

- テストはコミット単位で実行: `python tests/test_*.py`（ユニット群）／必要に応じ `python tests/smoke_aurora.py` 等の実機スモーク
- **DB への破壊的操作（migrate 等）の前に必ず `python tools/backup_db.py`**（G:\SerinaDB Backup へ7世代保存）
- `data/*.db` は git 管理外。コードと設計書のみコミットする
- 本番起動はリポジトリ直下の `Serina.bat`（GUI）
- 人格テキスト: `prompt/persona.md` ＋ `prompt/boundary.md`（Core が起動時に直読み）
