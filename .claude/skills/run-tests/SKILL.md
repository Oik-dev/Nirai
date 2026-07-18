---
name: run-tests
description: Serina のテスト一式をコミット前に実行し結果を要約する。「テスト回して」「コミット前チェック」で使用。
---

# テスト実行手順

コミット単位で実行する（毎編集では回さない）。

## 実行順（Ollama 不要 → 必要の順）

| # | コマンド | 前提 | 検証対象 |
|---|---|---|---|
| 1 | `python -c "… tests/test_*.py …"` または個別 `python tests/test_*.py` | なし | ユニット群（Core／GUI／帳簿／Qwenアダプタ） |
| 2 | `python tests/smoke_bge_m3_recall.py` | Ollama（bge-m3） | 埋め込み想起スモーク |

- Ollama 未設定なら 2 は SKIP。SKIP した場合は必ず報告に明記。
- Brain 会話の実機疎通は `tests/test_qwen_adapter.py`（スタブ）＋手動 GUI。旧 `smoke_aurora.py` / `smoke_gemini.py` は Brain 構成刷新（2026-07-18）で削除済み。
- DB スキーマを触る変更の前は `python tools/backup_db.py` を先に実行。

## 報告形式

`✓/✗/SKIP` の一覧＋不合格時は出力原文を添える。
