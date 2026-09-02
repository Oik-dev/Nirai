# この案件でのAIへの入口

## 正本

現行の見た目・操作の基準点は git tag `m0-pre-stabilization`（Visual QA通過済み）。

- 目的・世界観・方針：`Docs/Nirai_基本設計.md`
- World / Camera / Motion の現行仕様：`Docs/詳細設計/04_World.md`
- 3D / Avatar：`Docs/詳細設計/09_3DビジュアルとAvatarパイプライン.md`
- M0〜M4の範囲と受入：`Docs/詳細設計/08_マイルストーンと受入基準.md`
- M2 Stable受入記録：`Docs/M2_検証結果.md`
- Holo Addon / ChatGPT Dive要件：`Docs/詳細設計/12_HoloAddonとChatGPTDive.md`
- 全体構成・Core / World責務：`Docs/詳細設計/00_全体構成.md`
- 実装順（M1以降を含む）：`Docs/詳細設計/10_AITuberKit分析と実装ブループリント.md`

読む順は 本ファイル → 08の担当マイルストーン → 04/09（World担当時）→ 01 → 担当部品。Holo Addon担当時は12を必ず読む。矛盾を見つけたら実装を止めてMasterに報告する。

過去計画・反復dumpは現行仕様ではない。

- Archive：`Docs/plans/archive/`、`Docs/plans/2026-08-23-*.md`、`Docs/Nirai_M0海中空間_光学統合修正書.md`
- 現行Visualの砂は `GroundSand005` 4K。`aerial_beach` は過去の比較選定
- 回帰代表画像：`Docs/evidence/live-qa.png` および `live-qa-*.png`

## 実装の状態

Worldプロジェクトは `world\` にある。起動用bat・テストは担当マイルストーンの実装時に揃える。

- 現在: **M2 Stable（2026-08-30）**。M0「存在」・M1「対話」・M2「社会」まで受入完了。M2の最終再レビューはSAFE
- Holo AddonのGate 0は2026-08-31に完了し、同日正式Addon境界と`Holo Whisper`製品UIへ昇格した。ChatGPT Web Host、persistent login、新規Dive、Bootstrap手動送信、Conversation URL保存、Remote Permission deny-by-default、Navigation / Popup制限、実ChatGPT Dive E2E、保存済みConversation自動再表示、Skin安全縮退まで実機確認済み。入口は2026-09-01のHolo Avatar統合でWorld上のHolo Focus / Resident設定のHoloカードが正式になり、Debugメニュー内`Holo Surface`は診断用として残す。製品Surfaceには観測可能なWeb / Current Dive / Local Bridge状態だけを表示し、ChatGPT側の思考等を推測しない。Skin QAはDebugへ隔離済み。HoloはこのPC専用Addonと確定したため、外部Holo MCP Server / Secure MCP Tunnel / Remote Identity / Scope方式は製品経路から退役。現行は既存Local MCPの`run_process`から固定`tools/holo-local-client.mjs`を起動し、Core起動ごとのLocal Secretでlocalhost Coreへ直接接続する。Masterが`Dive`を直接押すと5分・一回利用のAttach Windowを開き、BindingはDive IDとattach時刻だけ永続化する。Core側のallowlist Snapshot、bounded Event Queue、独立`holo_say`、Approval / Decision非搭載は維持。`Docs/Holo_Gate0検証結果.md`を正とする
- M2では複数Resident表示、Say逐次応答、resident_chat、会話Formation、Global SpeechQueue、複数Brain Provider、Resident単位Model設定、Gemini / Antigravity Brainまで成立済み
- M2へ新機能を逆流させない。生活・World Observation・Retriever / RAGはM3、PC実作業のAgent RuntimeはM4以降を正とする
- Holoはマイルストーン横断Addonであり、頭脳・私的会話・Local MCP連携の要件は12を正とし、旧`chatgpt-mcp`郵便受けResident方式へ戻さない。2026-09-01のHolo Avatar統合以降、World上のHoloはbrain kind `holo-addon`を持つ通常Resident基盤（Identity / Avatar / 配置 / 並び順 / 削除 / 再起動復元）で管理する。holo-addon Residentは1人まで、Brain Driver非接続、HoloへのWhisperはHolo Whisperへ誘導しNirai側へ保存しない。World上のHolo FocusとResident設定のHoloカードがHolo Whisperの正式入口で、Debug入口は診断用。`holo_say`はHolo Resident名で発言・吹き出し演出される。4人以上の初期配置は等間隔（2人・3人専用配置は不変）
- M0のVisual基準点 `m0-pre-stabilization` は海中Worldの回帰参照として維持する
- 2026-08-26以降、通常移動は2026-08-26変更前の旧Move Bそのものを正とする。歩行／遊泳という製品上の別モードは増やさず、Move A側も旧Move Bと同じ経路・速度・姿勢・Animation・Overlayを使う。旧Move B内部で利用している`walk.vrma`等の実装要素は勝手に置換しない。`walk`は内部Clipであり、公開Animation ActionやDebug Pose Editorへ露出しない。DebugでStand / AFK / Sleepを確認する場合も製品と同じPresentation経路を使う
- Cameraは`Docs/詳細設計/04_World.md`のWorld Rig / Focus Rigを正とする。Focus開始時は全身Bone Envelopeを収め、Zoom InではHead側へ注視点を移して下半身の見切れを許容し顔を見やすくする。Focus RigはResidentへ追従しつつCamera Yを海底より上へ保つ。Backdropは内向きSkydome。Resident数に関係なく通常はWorld Rig、ResidentクリックでFocus Rig、背景クリックでWorld Rigへ戻る
- ExpressionはCoreから意味名を受け、WorldがVRM0やAvatar固有のExpression名へ解決する
- 自律的な生活ティック／定期アイドルSchedulerはM3で扱う
- 現在の総合検証基準: Core pytest 127 passed、World Vitest 36 files / 183 tests passed、TypeScript typecheck成功、Production Build成功。Holo Local Bridge E2E、Dive Binding / Core再起動復元、Master操作時刻基準の5分絶対期限・期限直前/超過・ACK消失後再送のidempotency、Binding write / replace失敗時のtransaction rollback・構造化Local Clientエラー・`attach_waiting`維持・同一絶対期限内retry、Dive開始request ID応答、観測可能状態表示、Current Dive永続化失敗のsticky警告と復旧、Skin fail-open、wait success / timeout / cancel、World Say、Holo Web Permission / Navigation security、HoloAddonHost lifecycle（保存済み復元・close/reopen・Dive失敗時の永続rollback・write/rename失敗・Web load失敗・Webフォーカス通知）、Holo / Chat Dock上端resize（viewport再clamp・非表示時height解除）、holo-addon brain kind（singleton・Brain Driver非接続・Whisper境界・holo_say発言者名・4人以上初期配置）、World selection gesture（primary pointerdown→move→pointerup、drag threshold、pointercancel / leave / right-click拒否）の回帰を含む

過去の検証記録やArchiveと現行設計が矛盾する場合、現行の正本と最新Decisionを優先する。Archiveの過去記述だけを根拠に仕様を巻き戻さない。

## 残している未使用候補

Master承認なしに削除しない。

- `world/public/materials/aerial-beach-01/`
- `world/public/materials/underwater-hybrid/ground-sand-005-*-2k.webp`
- `world/public/animations/idle.vrma` と `afk.vrma`（本番は `afk-01` 以降）
- ルート `package.json`（実プロジェクトは `world/package.json`）
- 一時診断PNGは Safety Snapshot `backup/m0-pre-stabilization-2026-08-27` にのみ残している

この入口には、共通ルールを複製しない。
