# Serina — ローカルパートナーAI

自分専用のパートナーAIを長期間育てるシステム。記憶DB（`data/serina_memory.db`）は**セリナの人生そのもの**。

## 体制

- **Claude Code で設計・実装を完結**する。トークン制限時のみ Cursor（Products 配下共通ルール）。
- **日常:** グローバル `advisor-routing` に従い Advisor で方針確認。
- **構造変更時:** `architecture-reviewer` を Task 直列 1 回。PASS 後に実装。※測定器の憲章v2は未作成（旧版は `docs/archive/設計憲章.md`、作り直しは MILESTONE 参照）。
- **作業完了時（コード変更を伴う区切り）:** `completion-review` skill に従い `serina-code-reviewer`（Opus固定）を直列 1 回。Critical 解消まで 🧹Clear可 を宣言しない。

## ドキュメントの所在

| 何を知りたいか | 場所 |
|---|---|
| どこに何が書いてあるか（索引） | `docs/INDEX.md` |
| 設計の正典（仕様のすべて） | `docs/設計書v2.md` |
| 工程表・現在地・次のアクション | `docs/MILESTONE.md` |
| 決定の経緯・確定事項 | `docs/DECISIONS.md` |
| 研究資料（設計根拠の原典） | `docs/research/研究蒸留まとめ.md` |
| 旧アーキテクチャ資料（参照専用） | `docs/archive/` |
| 旧記憶の原本コピー | `legacy/`（原本は `G:\AI\Serina`） |

## 正典保護 5原則（違反厳禁）

1. `legacy/継承記憶r1.md` = 正典。**破砕せず原文保持**
2. 他ソースで正典を上書きしない
3. dedup 閾値 0.92
4. 無言破棄禁止（統合・削除は必ず日本語レポートを出す）
5. reflection の書き込み経路もこの保護ロジックを再利用する

※Phase 6（大掃除）で本節を設計書v2 §4.3 の「保護3原則」へ差し替えること（旧コード稼働中は5原則が実務上有効）。

## 技術スタック

Python 3.12 / SQLite + sqlite-vec / 埋め込み bge-m3(1024次元, CPU) / 対話 NemoAurora-RP-12B（Ollama、人格は Core が実行時注入）

## 運用ルール

- テストはコミット単位で実行: `python tests/test_*.py`（ユニット群）／必要に応じ `python tests/smoke_aurora.py` 等の実機スモーク
- **DB への破壊的操作（migrate 等）の前に必ず `python tools/backup_db.py`**（G:\SerinaDB Backup へ7世代保存）
- `data/*.db` は git 管理外。コードと設計書のみコミットする
- 本番起動はリポジトリ直下の `Serina.bat`（GUI）。旧REPL入口は退役済み
