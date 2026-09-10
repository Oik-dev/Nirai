# Nirai Docs Index

更新日：2026-09-09

`Docs`直下と`詳細設計`はCurrent Design / Contractを置く場所とする。`evidence`、`history`、`archive`は判断根拠・検証結果・旧設計を残す場所であり、Current Designを上書きしない。

## Current / Source of Truth

- `Nirai_基本設計.md` — Product / Architectureの上位設計
- `Nirai_設計ガバナンス.md` — 設計判断ルール
- `詳細設計/` — 現行Core / Brain / World / Conversation / Memory / Task / Holo等のContract
- `Nirai_DNA_UE427_Architecture_2026-09-08.md` — Private DNA / UE4.27 World Trackの現行正本（v0.5）

Private DNA WorldではUEを唯一の通常利用Main UX Surfaceとし、DNA Scene / Body / UI / Animation / Interactionを可能な限りReuseする。現行Electron / React UIとThree.js Worldは配布可能Standard World / Fallback / Regression用として保持する。VoiceはProvider非依存とし、VOICEVOXを新Architectureの依存にしない。

## Evidence

- `evidence/reports/` — M0〜M4、Memory、Holo、Incident、Adversarial Review等の検証結果
- `evidence/` — 画像・実機比較等のEvidence

Evidenceは「その時点で何を確認したか」の証拠であり、現在の設計正本ではない。

## History / Archive

- `history/` — ガバナンス再編前の旧Active Design履歴
- `archive/research-and-old-design/` — 旧調査、旧方針、監査、棚卸し、過去の設計判断
- `archive/plans/` — 過去の実装Brief / Plan / Deferred work

新しい設計判断でArchiveの方式へ自動的に巻き戻さない。必要な理由・Evidenceを確認したうえでCurrent Designを更新する。

## 読み順

新しい作業ではまず`../AI_ENTRY.md`を読む。次に本Index、`Nirai_基本設計.md`、`Nirai_設計ガバナンス.md`、作業対象の`詳細設計`を読む。Private DNA / UE4.27作業では`Nirai_DNA_UE427_Architecture_2026-09-08.md` v0.5を追加で読む。Evidence / Archiveは必要になった時だけ参照する。
