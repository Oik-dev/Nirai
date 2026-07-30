# Task 003

**目的:** `hypothesis_retention_days = 30` を config 化する（Phase H-2）。類似度の新規ツマミは作らない。  
**影響範囲:** `config/thresholds.toml`、`core/config.py`、読み込みテストのみ  
**禁止:** facts／distillation のロジックを触るな。Design を書き換えるな。新しい類似度閾値キーを増やすな。  
**読んでよい Design:** `docs/設計書.md` §4.9 の保存期間に関する記述のみ。  

**状態:** PASS（承認済み）  

---

## PASS票

**変更ファイル:**
- `config/thresholds.toml`（+2）
- `core/config.py`（+2）
- `tests/test_thresholds_config.py`（+10）

**未解決:** なし  
**不安な箇所:** なし（値の消費は H-5 以降）  

**ゲート結果:**
- テスト: 573 passed（Fable 再確認含む）
- Design突合: 保存期間30日ツマミ化。新規類似度キーなし

---

## Claude最終承認（票＋差分。必須）

- [x] 票を読んだ
- [x] 差分を見た
- 承認: 保存期間30日のツマミ化のみ。新しい類似度ツマミを増やしていないことも確認。次回から票の「禁止」「読んでよいDesign」欄を埋めること。 — Fable 2026-07-28
