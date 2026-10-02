---
name: run-tests
description: Serina のテスト一式をコミット前に実行し結果を要約する。「テスト回して」「コミット前チェック」で使用。
---

# テスト実行手順

コミット単位で実行する（毎編集では回さない）。

## 実行順（Ollama 不要 → 必要の順）

| # | コマンド | 前提 | 検証対象 |
|---|---|---|---|
| 1 | `python -m pytest tests/ -q` | なし | ユニット群（Core／GUI／帳簿／Ollamaアダプタ／白紙GO一式） |
| 2 | `python tests/smoke_bge_m3_recall.py` | Ollama（bge-m3） | 埋め込み想起スモーク |

- Ollama 未設定なら 2 は SKIP。SKIP した場合は必ず報告に明記。
- **必ず pytest 経由で回す**: 2026-07-19 以降の新テスト（`test_directed_forget.py` 等16本）は pytest フィクスチャ形式で `__main__` ランナーを持たず、`python tests/test_x.py` 単体実行では**0件実行でも成功終了する（空回り）**。合格数（現在477）が出力に表示されることを確認する。
- Brain 会話の実機疎通は `tests/test_ollama_adapter.py`（スタブ）＋手動 GUI。旧 `smoke_aurora.py` / `smoke_gemini.py` は Brain 構成刷新（2026-07-18）で削除済み。
- DB スキーマを触る変更の前は `python tools/backup_db.py` を先に実行。

## 報告形式

`✓/✗/SKIP` の一覧＋不合格時は出力原文を添える。
