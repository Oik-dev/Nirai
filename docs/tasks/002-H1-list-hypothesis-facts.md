# Task 002

**目的:** `list_hypothesis_facts_by_subject` を追加する（Phase H-1）。  
**影響範囲:** `core/memory/facts.py` と対応テストのみ  
**禁止:** Memory 以外を触るな。Design を書き換えるな。予定/記念日カテゴリの扱いを変えるな（除外のみ）。  
**読んでよい Design:** §4.9 付近のみ  

**状態:** PASS（承認済み）  

---

## PASS票

**変更ファイル:**
- `core/memory/facts.py`（+22）
- `tests/test_facts.py`（+70）

**未解決:** なし  
**不安な箇所:** なし  

**ゲート結果:**
- テスト: `pytest tests/` → **573 passed**（Fable 再実行 34.41s でも一致）
- Design突合: hypothesisのみ／予定記念日除外／NULL含む — 一致

---

## Claude最終承認（票＋差分。必須）

- [x] 票を読んだ
- [x] 差分を見た
- 承認: テスト再実行 573 passed を独自確認。設計 §4.9（hypothesisのみ／予定・記念日除外／分類なしは含む／他状態は除外）と実装・テストが一致。差し戻しなし。 — Fable 2026-07-28
