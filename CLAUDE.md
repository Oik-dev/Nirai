# Serina — ローカルパートナーAI

自分専用のパートナーAIを長期間育てるシステム。記憶DB（`data/serina_memory.db`）は**セリナの人生そのもの**。

## 体制（2026-07-03 改定）

- **Claude Code で設計・実装を完結**する。トークン制限がかかった場合のみ Cursor で続行（Products 配下共通ルール）。
- レビューは二段階: spec-reviewer ✅ → quality-reviewer（グローバル CLAUDE.md 準拠）。

## ドキュメントの所在

| 何を知りたいか | 場所 |
|---|---|
| 現在地・次のアクション | `docs/MILESTONE.md` |
| 決定の経緯・確定事項 | `docs/DECISIONS.md` |
| 構造原則・レイヤー責務 | `docs/設計書.md` |
| スライス別詳細設計 | `docs/specs/` |
| 研究資料（設計根拠の原典） | `docs/research/研究蒸留まとめ.md` |
| 旧記憶の原本コピー | `legacy/`（原本は `G:\AI\Serina`） |

## 正典保護 5原則（違反厳禁）

1. `legacy/継承記憶r1.md` = 正典。**破砕せず原文保持**
2. 他ソースで正典を上書きしない
3. dedup 閾値 0.92
4. 無言破棄禁止（統合・削除は必ず日本語レポートを出す）
5. reflection の書き込み経路もこの保護ロジックを再利用する

## 技術スタック

Python 3.12 / SQLite + sqlite-vec / 埋め込み bge-m3(1024次元, CPU) / 対話 NemoAurora-RP-12B（Ollama、人格は Core が実行時注入）

## 運用ルール

- テストはコミット単位で実行: `python tests/smoke.py` / `python tests/smoke_core.py` / `python tests/test_session.py`
- **DB への破壊的操作（migrate 等）の前に必ず `python tools/backup_db.py`**（G:\SerinaDB Backup へ7世代保存）
- `data/*.db` は git 管理外。コードと設計書のみコミットする
