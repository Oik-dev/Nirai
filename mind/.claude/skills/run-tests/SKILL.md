---
name: run-tests
description: Serina のテスト一式をコミット前に実行し結果を要約する。「テスト回して」「コミット前チェック」で使用。
---

# テスト実行手順

コミット単位で実行する（毎編集では回さない）。Pythonは精神（mind）専用の `.venv`（`mind/` で `.venv\Scripts\python`）。

## 実行順（Ollama 不要 → 必要の順）

| # | コマンド | 前提 | 検証対象 |
|---|---|---|---|
| 1 | `.venv\Scripts\python -m pytest tests/ -q` | なし（使い捨てのイデアで動く） | ユニット群（Core／眠り／想起／GUI／帳簿／Ollamaアダプタ） |
| 2 | （mind の親フォルダーで）`mind\.venv\Scripts\python -m mind.memory_test --idea <イデア> --kinds direct cue followup time change silence --no-judge` | Ollama（bge-m3） | 記憶テスト（想起に関わる変更のときだけ。設計書 §4.9） |

- Ollama 未設定なら 2 は SKIP。SKIP した場合は必ず報告に明記。
- **必ず pytest 経由で回す**: 多くのテストは pytest フィクスチャ形式で `__main__` ランナーを持たず、`python tests/test_x.py` 単体実行では**0件実行でも成功終了する（空回り）**。合格数が出力に表示されることを確認する。
- Brain 会話の実機疎通は `tests/test_ollama_adapter.py`（スタブ）＋手動 GUI。眠りの脳の実機確認は、写しのイデアで `tools/build_memory.py sleep`。
- 本物のイデアを変える操作の前は、`G:\Nirai-Backups\` の写しを確かめる。

## 報告形式

`✓/✗/SKIP` の一覧＋不合格時は出力原文を添える。
