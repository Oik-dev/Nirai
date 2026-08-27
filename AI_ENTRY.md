# この案件でのAIへの入口

## 正本

現行の見た目・操作の基準点は git tag `m0-pre-stabilization`（Visual QA通過済み）。

- 目的・世界観・方針：`Docs/Nirai_基本設計.md`
- World / Camera / Motion の現行仕様：`Docs/詳細設計/04_World.md`
- 3D / Avatar：`Docs/詳細設計/09_3DビジュアルとAvatarパイプライン.md`
- M0範囲と受入：`Docs/詳細設計/08_マイルストーンと受入基準.md`
- 全体構成・将来のCore構成：`Docs/詳細設計/00_全体構成.md`（目標構成。M0では `world\` のみ実装済み）
- 実装順（M1以降を含む）：`Docs/詳細設計/10_AITuberKit分析と実装ブループリント.md`

読む順は 本ファイル → 08の担当マイルストーン → 04/09（World担当時）→ 01 → 担当部品。矛盾を見つけたら実装を止めてMasterに報告する。

過去計画・反復dumpは現行仕様ではない。

- Archive：`Docs/plans/archive/`、`Docs/plans/2026-08-23-*.md`、`Docs/Nirai_M0海中空間_光学統合修正書.md`
- 現行Visualの砂は `GroundSand005` 4K。`aerial_beach` は過去の比較選定
- 回帰代表画像：`Docs/evidence/live-qa.png` および `live-qa-*.png`

## 実装の状態

Worldプロジェクトは `world\` にある。起動用bat・テストは担当マイルストーンの実装時に揃える。

- 現在: M0 Visual QA通過。基準点は `m0-pre-stabilization`。Stabilization整理後も Visual / Behavior を変えない
- M0は海中3D WorldとHumanoid Residentの存在感を成立済み。Core・Brain・会話はM0の完成条件に含めない
- 2026-08-26以降、通常移動は2026-08-26変更前の旧Move Bそのものを正とする。歩行／遊泳という製品上の別モードは増やさず、Move A側も旧Move Bと同じ経路・速度・姿勢・Animation・Overlayを使う。旧Move B内部で利用している`walk.vrma`等の実装要素は勝手に置換しない。`walk`は内部Clipであり、公開Animation ActionやDebug Pose Editorへ露出しない。DebugでStand / AFK / Sleepを確認する場合も製品と同じPresentation経路を使う
- Cameraは`Docs/詳細設計/04_World.md`のWorld Rig / Focus Rigを正とする。Focus開始時は全身Bone Envelopeを収め、Zoom InではHead側へ注視点を移して下半身の見切れを許容し顔を見やすくする。Focus RigはResidentへ追従しつつCamera Yを海底より上へ保つ。Backdropは内向きSkydome。Resident数に関係なく通常はWorld Rig、ResidentクリックでFocus Rig、背景クリックでWorld Rigへ戻る
- ExpressionはCoreから意味名を受け、WorldがVRM0やAvatar固有のExpression名へ解決する
- 自律的な生活ティック／定期アイドルSchedulerはM3で扱う
- 最新コード検証: Vitest 15 files / 65 tests成功、Production Build成功。Visualの最終確認は人間

過去の検証記録やArchiveと現行設計が矛盾する場合、現行の正本と最新Decisionを優先する。Archiveの過去記述だけを根拠に仕様を巻き戻さない。

## 残している未使用候補

Master承認なしに削除しない。

- `world/public/materials/aerial-beach-01/`
- `world/public/materials/underwater-hybrid/ground-sand-005-*-2k.webp`
- `world/public/animations/idle.vrma` と `afk.vrma`（本番は `afk-01` 以降）
- ルート `package.json`（実プロジェクトは `world/package.json`）
- 一時診断PNGは Safety Snapshot `backup/m0-pre-stabilization-2026-08-27` にのみ残している

この入口には、共通ルールを複製しない。
