# Nirai v2

Nirai v2は、AI・ローカル長期Memory・Tool・外部サービス・Worldを一つのHubへ接続し、Master専用の一つの環境として使うための基盤である。

## 正本

1. `WORLD_RULES.md` — 共通原則
2. `Nirai_v2_基本設計書.md` — 現行仕様
3. `prototype/` — UIを扱う場合の構造・見た目の基準

現在の期間限定実装順序は`Nirai_v2_初期自走化計画.md`を参照する。M1〜M8完了後はArchiveへ退役させ、現行仕様の根拠にはしない。

同じ仕様を別文書へ複製しない。

## 現在のTree

- `core/` — v1実装を含む移行元
- `world/` — World / Presentation実装
- `tools/` — 開発・Provider・Holo連携Tool
- `prototype/` — v2 UIモック
- `Img/` — v2画像資産

既存コードはv2仕様の根拠ではない。再利用でv2が単純・安全・保守しやすくなる場合だけ採用し、過去の履歴はGitから参照する。
