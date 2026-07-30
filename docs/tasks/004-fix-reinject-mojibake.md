# Task 004

**目的:** 圧縮後のルール再注入が文字化けする不具合を直す（読めない文字列で届いている）。  
**影響範囲:** `C:\Users\weize\.claude\settings.json` の SessionStart 該当箇所のみ  
**禁止:** `rules-reinject.md` の中身を書き換えるな（本体は正常）。他のフック設定に触るな。  
**読んでよい Design:** なし  
**背景:** UserPromptSubmit（日常語リマインダー）と同じ bash `cat` に揃える。  

**状態:** DOING（差分承認済み・**マスター目視待ち**）  

**完了条件:** 新しいセッションを圧縮後に開始したとき、再注入された文言が読める日本語で表示されること（**マスターの目視確認をもって PASS**）。機械的な確認だけで PASS にしない。

---

## PASS票

**変更ファイル:**
- `C:\Users\weize\.claude\settings.json`（SessionStart: PowerShell `Get-Content` → bash `cat`）

**未解決:** マスター目視（compact 後の再注入が日本語であること）  
**不安な箇所:** なし  

**ゲート結果:**
- 機械: `cat` 単体では日本語出力を確認済み（Fableも同型差分を確認）
- **最終 PASS は実際の compact → SessionStart 発火の目視のみ**（机上ではフック経路を証明できない）

---

## Claude最終承認（票＋差分。必須）

- [x] 票を読んだ
- [x] 差分を見た
- 採用（差分確認済み・二重実装なし）: SessionStart は UserPromptSubmit と同型の `cat`。役割は日常語1本／再注入1本で重複なし。rules-reinject.md 未変更。001の条件充足 Yes。Critical/Important なし。  
  **完了条件は目視待ちのまま。機械だけで PASS にしない。** — Fable 2026-07-28
