---
name: completion-review
description: コード変更を伴う作業の区切り（マイルストーン到達・Clear宣言前）にOpusレビューを必ず通す。「レビュー通して」「作業完了」「Clearする前」で使用。
---

# 完了時レビュー手順

Serina の完了時コードレビュー入口は本 skill のみ（Claude Code / Cursor 共通）。他のレビューSkillは使わない。

## 発火条件

- コード変更を伴うセッションの区切り（🧹Clear/Compact宣言の前に必須）
- docs/tests のみの変更は対象外
- タスク毎には行わない。区切り時のみ

## 手順

1. `run-tests` skill を先に実行（未実施なら）。結果要約を控える
2. BASE_SHA（区切り開始時点 or 前回レビュー時点）と HEAD_SHA を取得
3. Task で `serina-code-reviewer` を直列1回起動。渡すもの: BASE_SHA / HEAD_SHA / 作業目的 / テスト結果要約 / **本セッションで architecture-reviewer を通した場合はその結果（PASS 有無）**
4. 結果処理:
   - Critical → 修正 → 再レビュー1回（上限。それでも残れば advisor に対話相談）
   - Important → 原則修正。持ち越す場合は引き継ぎ指示文に明記
   - Minor → 任意
5. Assessment が「可」になって初めて 🧹Clear可 を宣言してよい

## エスカレーション基準（構造ゲート掛け忘れ）

レビューが「憲章の発火条件に該当するのに事前 architecture-reviewer PASS なし」を Critical で返した場合、`architecture-reviewer` を直列1回（測定器: `docs/憲章.md`）。PASS 後に再レビュー1回。

## コスト規律

- Opus起動は初回＋再確認の最大2回/区切り
