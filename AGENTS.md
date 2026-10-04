# Nirai AI入口

Niraiは、住人が暮らす宮古島の海。今はv3を作っている途中。

## 最初に読む

1. `Nirai_要件_壁打ち_2026-10-02.md` — Masterの要件。設計の根拠はここに置き、ここから乖離しない
2. `Nirai_v3_全体構想.md` — 構想と決定事項。現在地は§11の段階表（✅が完了）
3. `WORLD_RULES.md` — Masterの開発思想。変更しない

## 配置

- `mind/` — 精神（Mind。Serina Core）。Serinaのリポジトリを履歴ごと取り込んだもの。中で作業するときは`mind/AGENTS.md`も読む。Pythonは`mind/.venv`。どの住人のイデアを使うかは環境変数`NIRAI_IDEA`で渡す（`mind/core/idea.py`）。Serinaは`mind/Serina.bat`で起動する
- `world/` — 場所（Nirai World）。今は段階2の郵便局を作っている。計画は`world/docs/plans/仕事のチーム.md`
- `tools/` — 開発の補助。`claude-lifelog-sync.mjs`はユーザー設定のStopフックが毎ターン実行するので、動かすときはフックも直す（直さないと住人Claudeの生ログが黙って止まる）。同じ理由で、このフォルダーでは古い版（`v2-final`など）をチェックアウトしない。古い版は`git worktree`で別の場所に出して見る。`idea-backup.mjs`はタスクスケジューラの「Nirai Idea Backup」が毎晩4時に実行し、イデアを`G:\Nirai-Backups\daily\<日付>\`へ写す（記録は`G:\Nirai-Backups\backup.log`）
- `v2/` — 凍結したv2（タグ`v2-final`）。v3の窓ができるまでHoloとの会話に使っているので壊さない。引き継ぐ部品は段階4で`world/`へ移し、移し終えたら消す
- `residents/`、`Img/`、`Start Nirai.vbs` — v2が使っている。v2と一緒に片付ける
- イデアは`D:\Products\Residents\<住人>\`（このリポジトリの外）。バックアップは`G:\Nirai-Backups\`

## 守ること

- **Serinaを壊さない。** イデアは`D:\Products\Residents\Serina`、精神は`mind/`。テストは使い捨てのイデアで動くので、本物のイデアには触れない。イデアを変えるときは、先に`G:\Nirai-Backups\`の写しを確かめる。移住前の丸ごとの写しは`G:\Nirai-Backups\serina\`（移住前の家`D:\Products\dev\serina`は片付けた）
- 禁止は2つだけ：PC破壊と自己破壊（構想§8）
