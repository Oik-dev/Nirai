# Nirai v2

M1以降のv2実装。現行仕様はリポジトリ直下の`Nirai_v2_基本設計書.md`、実装順序は初期自走化中のみ`Nirai_v2_初期自走化計画.md`を正本とする。

## M1

- `src/hub/` — SQLite Store、Command、Capability Registry、Task Engine、utilityProcess入口
- `src/shared/` — 共通型と入力指紋
- `src/main/` — Electron Mainの最小起動入口
- `tests/` — M1の恒久契約テスト
- `smoke/` / `scripts/run-electron-smoke.mjs` — Electron Main → utilityProcess Hubの実経路確認

v1のCore / Workflow / Outboxはv2の実行経路へ組み込まない。
