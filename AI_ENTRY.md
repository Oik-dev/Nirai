# この案件でのAIへの入口

## 正本

- 目的・世界観・方針：`Docs/Nirai_基本設計.md`
- 実装仕様：`Docs/詳細設計/00〜09`（読む順は00 → 08の担当マイルストーン → 01 → 担当部品。3DビジュアルとAvatarを扱う場合は09も読む）

矛盾を見つけたら、実装を止めてMasterに報告する。

## 実装の状態

Worldプロジェクトは `world\` にある。起動用bat・テストは担当マイルストーンの実装時に揃える。

- 現在: M0最終回帰確認中。Move A / Move Bの旧Move B体感をMasterが実画面確認した後にM1へ移行する
- M0は海中3D WorldとHumanoid Residentの存在感を成立済み。Core・Brain・会話はM0の完成条件に含めない
- 2026-08-26以降、通常移動は2026-08-26変更前の旧Move Bそのものを正とする。歩行／遊泳という製品上の別モードは増やさず、Move A側も旧Move Bと同じ経路・速度・姿勢・Animation・Overlayを使う。旧Move B内部で利用している`walk.vrma`等の実装要素は勝手に置換しない。`walk`は内部Clipであり、公開Animation ActionやDebug Pose Editorへ露出しない。DebugでStand / AFK / Sleepを確認する場合も製品と同じPresentation経路を使う
- Cameraは`Docs/詳細設計/04_World.md`のWorld Rig / Focus Rigを正とする。Focus開始時は全身Bone Envelopeを収め、Zoom InではHead側へ注視点を移して下半身の見切れを許容し顔を見やすくする。Focus RigはResidentへ追従しつつCamera Yを海底より上へ保つ。Backdropは内向きSkydome。Resident数に関係なく通常はWorld Rig、ResidentクリックでFocus Rig、背景クリックでWorld Rigへ戻る
- ExpressionはCoreから意味名を受け、WorldがVRM0やAvatar固有のExpression名へ解決する
- 自律的な生活ティック／定期アイドルSchedulerはM3で扱う
- 最新コード検証: Vitest 15 files / 65 tests成功、TypeScript成功、Production Build成功

過去の検証記録やArchiveと現行設計が矛盾する場合、現行の正本と最新Decisionを優先する。Archiveの過去記述だけを根拠に仕様を巻き戻さない。

この入口には、共通ルールを複製しない。
