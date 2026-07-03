---
name: run-tests
description: Serina のテスト一式（スモーク・セッション・想起品質評価）をコミット前に実行し結果を要約する。「テスト回して」「コミット前チェック」で使用。
---

# テスト実行手順

コミット単位で実行する（毎編集では回さない）。

## 実行順（Ollama 不要 → 必要の順）

| # | コマンド | 前提 | 検証対象 |
|---|---|---|---|
| 1 | `python tests/test_session.py` | なし | 3a セッション基盤（TZ・タイムアウト・アーカイブ） |
| 2 | `python tests/smoke_core.py` | Ollama | Core 縦通し |
| 3 | `python tests/smoke.py` | Ollama | Memory 層 CRUD＋検索 |
| 4 | `python tests/eval_recall.py` | Ollama | 想起品質（ゴールデンクエリ、実DBは変更しない） |

- Ollama 未起動なら 2〜4 は SKIP される／する。SKIP した場合は必ず報告に明記。
- **eval_recall が不合格になったら減衰式・係数・検索ロジックの変更を疑う**こと（ベースラインは全5件1位、2026-07-03）。
- DB スキーマを触る変更の前は `python tools/backup_db.py` を先に実行。

## 報告形式

`✓/✗/SKIP` の一覧＋不合格時は出力原文を添える。
