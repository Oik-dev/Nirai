# Nirai

MasterとAI Residentが同じ場所に存在し、長く会話・関係・記憶を継続する箱庭基盤です。「居場所が主、タスクは従」を中心に、人格・記憶・Brain・Avatarを分離しています。

目的と設計は [基本設計](Docs/Nirai_基本設計.md)、現行の実装状況と設計書への入口は [AI_ENTRY.md](AI_ENTRY.md) を参照してください。

## 構成

- `core/`：Pythonの会話・記憶・Resident・仕事・承認の調停。
- `world/`：現行のElectron + Three.jsによる画面・Avatar・音声。Worldの依存定義と固定バージョンは、このディレクトリの`package.json` / `package-lock.json`が正本です。
- `tools/holo-local-client.mjs`：現行のHolo Local Bridgeクライアント。旧Gate 0 MCP実装は`Docs/history/holo-mcp-gate0-retired/`へ履歴資料として退避済みで、製品Rootに実行入口は残していません。
- `Docs/`：現行設計・検証記録・履歴。文書の優先順位は [設計ガバナンス](Docs/Nirai_設計ガバナンス.md) を参照してください。

## 起動と検証

対象はWindowsです。通常利用は既存の`Nirai.lnk`から起動します。`Start Nirai.cmd`はCoreと開発用Worldを起動する入口です。

CoreのPython依存はRootの`.venv`へ隔離します。初回セットアップ、PC復元、Python再インストール後は`Setup Nirai Runtime.cmd`を実行してください。Python 3.12.xから`.venv`を作成し、`requirements.txt`の固定依存を復元します。起動時は`nirai_bootstrap.py`がCore import前に`.venv`、runtime package、config、Electron / World BuildをPreflightし、必須要件が欠けていれば`runtime/logs/startup-preflight.log`へ理由を残して停止します。Cursor / Codex / Claude / Gemini等のProvider欠損は警告であり、Core起動自体は妨げません。

状態だけ確認したい場合は`Nirai Doctor.cmd`を実行します。

開発環境を準備する場合は、リポジトリのルートで次を実行します。

```powershell
& ".\Setup Nirai Runtime.cmd"
npm --prefix world ci
npm run build
```

Resident / Providerの設定やAvatar等のローカル資産は別途必要です。上のコマンドだけでProvider認証や個人資産が用意されるわけではありません。

検証コマンドもルートから実行できます。

```powershell
npm run test:core
npm test
npm run typecheck
npm run build
```

`npm test`はWorldのテスト、`npm run test:core`はCoreのテストです。`world/`内の従来コマンドも引き続き使えます。ルートの`npm run dev`はWorld単体の開発起動なので、Coreも必要な場合は`Start Nirai.cmd`を使用します。

## 保存データ

`runtime/`、`world_memory/`、`residents/*/private/`には実際の履歴や記憶が入ります。検証にはテスト側の一時データを使用してください。UI履歴の原文は`runtime/chat_sessions/S-*.jsonl`、検索・ページ取得用の派生索引は同ディレクトリの`entries.sqlite3`です。索引と、World / Private Memoryの原文DBを混同しないでください。
