# Nirai v2

v2の製品実装。現行仕様はリポジトリ直下の`Nirai_v2_基本設計書.md`、初期自走化中の実装順序は`Nirai_v2_初期自走化計画.md`を正本とする。

## 構成

- `src/hub/` — SQLite Store、共通Command、Capability Registry、Task Engine、utilityProcess入口
- `src/main/` — Electron Main、限定preload、Hub起動・終了、Dashboard IPC
- `src/renderer/` — prototypeを基準にした実Dashboard。Task状態は持たずHub Snapshotを表示
- `src/shared/` — 共通型と入力指紋
- `tests/` — Hubの恒久契約テスト
- `scripts/run-electron-smoke.mjs` — Main → Hubの実経路確認
- `scripts/run-ui-smoke.mjs` — Dashboard → IPC → Hub → SQLite → Dashboardの実経路確認

v1のCore / Workflow / Outboxをv2の実行正本として利用しない。
