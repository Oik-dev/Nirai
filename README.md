# Nirai v2

Nirai v2は、AI・ローカル長期Memory・Tool・外部サービス・Worldを接続し、Master専用の一つの環境として使うためのHubである。

## 正本

1. `WORLD_RULES.md` — 共通原則
2. `Nirai_v2_基本設計書.md` — 現行仕様
3. `prototype/` — UIの構造・見た目

初期の実装順序と出口は`Nirai_v2_初期自走化計画.md`を参照する。M1〜M8完了後はArchiveへ退役させ、現行仕様の根拠にはしない。実装に着手するAIの入口は`AI_ENTRY.md`。

## 配置

- `v2/` — v2実装の配置先。物理構成は基本設計§3
- `core/`、`world/`、`tools/` — v1を含む既存資産。採用境界は基本設計§25
- `residents/` — Persona本文
- `prototype/` — v2 UIモック
- `Img/` — v2画像資産

既存コードはv2仕様の根拠ではない。再利用でv2が単純・安全・保守しやすくなる場合だけ採用する。
