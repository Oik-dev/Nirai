# Nirai v2

Nirai v2は、MasterとAI Residentが同じWorldで会話・生活しながら、複数の仕事を自律的かつ安全に継続できる環境を目指す。

## 正本

開発時は次の順に参照する。

1. `WORLD_RULES.md` — 設計・実装・テスト・文書運用の共通原則
2. `Nirai_v2_基本設計書.md` — Nirai v2の現行仕様
3. `prototype/` — Dashboard / Task Chat / Resident管理UIの構造と操作の基準モック

同じルールや仕様を別文書へ複製しない。

## 現在の構成

- `core/` — v1からの再利用候補を含むCore実装。v2 Control Planeへ段階的に置換・整理する。
- `world/` — Electron / React / Three.js / VRMを含むWorld実装。独立したPresentation資産を再利用し、旧Task / Holo制御はv2へ合わせて整理する。
- `tools/` — 開発・Provider・Holo連携用Tool。移行中に必要な旧Control経路を含む。
- `prototype/` — v2 UIモック。製品Control Planeは持たない。
- `Img/` — v2で使用する画像資産。

既存実装は移行元であり、v2仕様の正本ではない。既存コードと現行設計が衝突する場合は、既存挙動を温存するための互換層を追加せず、v2の責務へ整理して実装する。

## 開発方針

過去の設計書、検証記録、廃止済み案を現行Treeへ蓄積しない。必要な履歴はGitから参照する。

実装前に責務と正本を定め、少数の強いテストでInvariant、主要フロー、重要な境界を守る。特殊事情のための一時検証は目的達成後に削除する。
