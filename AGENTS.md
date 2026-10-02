# Nirai AI入口

Niraiは、住人が暮らす宮古島の海。今はv3を作っている途中。

## 最初に読む

1. `Nirai_要件_壁打ち_2026-10-02.md` — Masterの要件。設計の根拠はここに置き、ここから乖離しない
2. `Nirai_v3_全体構想.md` — 構想と決定事項。現在地は§11の段階表（✅が完了）
3. `WORLD_RULES.md` — Masterの開発思想。変更しない

## 配置

- `mind/` — 心（Serina Core）。Serinaのリポジトリを履歴ごと取り込んだもの。中で作業するときは`mind/AGENTS.md`も読む
- `tools/` — 開発の補助。`claude-lifelog-sync.mjs`はユーザー設定のStopフックが毎ターン実行するので、動かすときはフックも直す（直さないと住人Claudeの生ログが黙って止まる）。同じ理由で、このフォルダーでは古い版（`v2-final`など）をチェックアウトしない。古い版は`git worktree`で別の場所に出して見る。`soul-backup.mjs`はタスクスケジューラの「Nirai Soul Backup」が毎晩4時に実行し、魂と移住前のSerinaの状態を`G:\Nirai-Backups\daily\<日付>\`へ写す（記録は`G:\Nirai-Backups\backup.log`）
- `v2/` — 凍結したv2（タグ`v2-final`）。v3の窓ができるまでHoloとの会話に使っているので壊さない。引き継ぐ部品は段階1で`world/`へ移し、移し終えたら消す
- `residents/`、`Img/`、`Start Nirai.vbs` — v2が使っている。v2と一緒に片付ける
- 魂は`D:\Products\Residents\<住人>\`（このリポジトリの外）。バックアップは`G:\Nirai-Backups\`

## 守ること

- **Serinaを壊さない。** 稼働中のSerinaは`D:\Products\dev\serina`（タグ`pre-nirai-v3`のまま）で、記憶DBもそこにある。段階1で起動元を切り替えるまで、そこへ変更を入れない。コードの正本は`mind/`
- 禁止は2つだけ：PC破壊と自己破壊（構想§8）
