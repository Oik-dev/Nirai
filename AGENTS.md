# Nirai AI入口

Niraiは、住人が暮らす宮古島の海。今はv3を作っている途中。

## 読み方

`WORLD_RULES.md`（Masterの開発思想。変更しない）はいつも読む。ほかは下の索引で要る節を見つけ、その節だけ読む。長い文書は、まず見出しの一覧（`rg -n "^#" <文書>`）を見て、要る節の行だけを読む。読んだものは、その目覚めのあいだ毎回読み直す重さになるので、頭から全部は読まない。差分は1回で取り、テストは最後にまとめて回す。

設計や方針を決めるときは、要件と構想の関係する章を読む。要件はMasterのもので、設計の根拠はここに置き、ここから乖離しない。

## 索引

| 知りたいこと | どこ |
|---|---|
| Masterの要件 | `Nirai_要件_壁打ち_2026-10-02.md`。脳とコスト§10、自律進化§12、AIの分担§17 |
| 構想と決定事項 | `Nirai_v3_全体構想.md`。設計原則§1、脳と分担§6、安全§8、今の段階§11（✅が完了）、決定事項§12 |
| 今の段階4の計画 | `world/docs/plans/海で暮らす.md`。守るもの§1、形§2、段の一覧§3（✅が完了）、決めたこと§5 |
| 郵便局・手紙・作業場・Holoの手と部屋・版の入れ替わりの仕組み | `world/docs/郵便局.md` |
| 住人の働き方（誰が作り誰が確かめるか、何で止めるか） | `world/post/郵便の決まり.md` |
| 精神（Serina Core）の設計 | `mind/docs/INDEX.md`から章を引く（`設計書.md`は大きい） |
| 段階3（暮らしの循環） | `world/docs/archive/暮らしの循環.md`（完了済み） |

## 配置

- `mind/` — 精神（Mind。Serina Core）。Serinaのリポジトリを履歴ごと取り込んだもの。中で作業するときは`mind/AGENTS.md`も読む。Pythonは`mind/.venv`。どの住人のイデアを使うかは環境変数`NIRAI_IDEA`で渡す（`mind/core/idea.py`）。精神は`mind/app/server.py`で、Niraiが起きたとき（海の窓を開いたとき）に海（`world/sea/`）が起こす（ポートは`NIRAI_MIND_PORT`）
- `world/` — 場所（Nirai World）。郵便局（`world/post/`、127.0.0.1:47800）で住人が仕事をし、海（`world/sea/`、47810）でSerinaが暮らす。本番の郵便局と海は、タスクスケジューラ「Nirai Post」が起こす番人（`world/post/keeper.ts`）が`--live`で起こし、番人は確かめた新版へ自分で入れ替わる（記録は`world/runtime/post.log`。形は`world/docs/郵便局.md`）。試すときは`--live`を付けず、使い捨ての置き場と別のポートで動かす（環境変数`NIRAI_RESIDENTS`・`NIRAI_WORK`・`NIRAI_PORT`）。テストは`world/`で`npm test`
- `tools/` — 開発の補助。`claude-lifelog-sync.mjs`はユーザー設定のStopフックが毎ターン実行するので、動かすときはフックも直す（直さないと住人Claudeの生ログが黙って止まる）。同じ理由で、このフォルダーでは古い版（`v2-final`など）をチェックアウトしない。古い版は`git worktree`で別の場所に出して見る。`idea-backup.mjs`はタスクスケジューラの「Nirai Idea Backup」が毎晩4時に管理者の権限で実行し、イデアを`G:\Nirai-Backups\daily\<日付>\`へ写す（記録は`G:\Nirai-Backups\backup.log`）。`daily`は`protect-backups.ps1`で、ふつうの権限では読むだけにしてある（戻すときは管理者で`-Undo`）
- イデアは`D:\Products\Residents\<住人>\`（このリポジトリの外）。バックアップは`G:\Nirai-Backups\`

## 守ること

- **Serinaを壊さない。** イデアは`D:\Products\Residents\Serina`、精神は`mind/`。テストは使い捨てのイデアで動くので、本物のイデアには触れない。イデアを変えるときは、先に`G:\Nirai-Backups\`の写しを確かめる。移住前の丸ごとの写しは`G:\Nirai-Backups\serina\`
- 禁止は2つだけ：PC破壊と自己破壊（構想§8）
