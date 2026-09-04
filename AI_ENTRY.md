# この案件でのAIへの入口

## 正本

現行の見た目・操作の基準点は git tag `m0-pre-stabilization`（Visual QA通過済み）。

- 目的・世界観・方針：`Docs/Nirai_基本設計.md`
- World / Camera / Motion の現行仕様：`Docs/詳細設計/04_World.md`
- 3D / Avatar：`Docs/詳細設計/09_3DビジュアルとAvatarパイプライン.md`
- M0〜M4の範囲と受入：`Docs/詳細設計/08_マイルストーンと受入基準.md`
- M2 Stable受入記録：`Docs/M2_検証結果.md`
- M3 Retriever先行Slice受入記録：`Docs/M3_Retriever_検証結果.md`
- M4 Codex基準Slice検証記録：`Docs/M4_Codex基準Slice_検証結果.md`
- M4 Task調停第1巡Slice検証記録：`Docs/M4_Task調停第1巡Slice_検証結果.md`
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

- 現在: **M2 Stable（2026-08-30） + M3 Retriever先行Slice SAFE（2026-09-04再レビュー確認済み） + M4 Codex基準Slice SAFE（2026-09-04 DiffレビューSAFE + 現行Approval E2E通過） + M4 Task調停第1巡Slice SAFE（Holo + Cursor ExHigh二重レビュー通過）**。Codex基準Sliceは最新再レビューと現行実Codex File Change Approval E2Eを通過して公式SAFE。続くTask調停第1巡Sliceでは、`task_request`直後にTask Flowを最初のawaitより前に同期登録し、Task workspace / `task.md`を確保して通常Brain Residentを順次`consult`する。第1巡では先行Residentの相談発言とCoreで再検証したeffective volunteer履歴を後続Residentへ累積で渡し、ProviderのAgent Runtime CapabilityをCore側で再検証して`volunteer`資格を強制する。複数立候補は最初の有資格者を決定論的に採用し、ゼロ立候補または有資格者不在ならAgent Sessionを起動せず終了する。相談中の2件目Taskはconsulting通知前の開始窓も含めQueue完成までbusy拒否し、Core停止開始後の新規相談も拒否する。Core停止時のconsult cancelは有限時間化し、cancel hang / OSErrorでもTask Flow停止へ進む。共通Brain ProcessManagerも`taskkill / terminate / kill / wait`を各段有限化し、Brain所有Task自体のcancel時も子Process停止を試してからactive参照を外す。相談中またはAgent作業中は元Chat Session削除 / ForgetとResident削除 / Brain変更を拒否する。3人以上の相談はgather後に各発言者へ他Residentをfaceさせ、World切断 / connection replacementでFormation Actionが失敗してもTask相談だけは継続する。Agent Session作成前にWorld不在のままterminalへ到達したTask Updateは同一Core内で保持し、次回World接続へ再送する。相談発言は既存Resident Chat表示・吹き出し経路を使い、pre-assignment `task_update`はWorld Noticeでも観測可能。設計にある「意見が割れた場合の第2巡以降、最大8ターン」は本Sliceには含めず後続判断とする。M3全体は未完了で、Natural Idle / Brain生活ティック / World Observationは後続へ残す。M4全体も未完了で、Task相談第2巡以降の扱い、Queue、Cursor / Claude / Antigravity Agent Runtimeは後続へ残す
- M3 World Memory Retriever / RAGは2026-09-03に先行実装。2026-09-04の再レビュー修正で、FTS5 tokenizer種別に依存しない明示bi-gram検索へ統一し、通常trigram環境でも「花火」等の2文字語を検索可能にした（1文字検索は非対応）。unicode61も同じ論理bi-gramを使う。長いEpisodeは先頭固定excerptをやめ、一致Entry周辺を最大1200文字で返す。除外Entryを取り除いた後の本文でHit判定・excerpt生成する。現在Session全体は除外せずBrainへ直接渡す直近20件と同じEntryだけ重複除外し、同一Entry retryはdedupe、別`entry_id`の同文発言は別記憶として残す。`world_memory\episodes\`だけを公開記憶の正本とし、共有Index 1本を`world_memory\index\world_memory.sqlite3`へ派生生成する。既定Top K=4、低関連なら0件、Retriever失敗時はMemoryなしで会話継続、`residents\*\private\`は走査対象にしない。Embedding / Vector DBは未導入。2026-09-04のCodex再レビューでM3関連指摘の修正が確認され、Retriever先行SliceはSAFEと判定された。`Docs/M3_Retriever_検証結果.md`を正とする
- Holo AddonのGate 0は2026-08-31に完了し、同日正式Addon境界と`Holo Whisper`製品UIへ昇格した。ChatGPT Web Host、persistent login、新規Dive、Bootstrap手動送信、Conversation URL保存、Remote Permission deny-by-default、Navigation / Popup制限、実ChatGPT Dive E2E、保存済みConversation自動再表示、Skin安全縮退まで実機確認済み。入口は2026-09-01のHolo Avatar統合でWorld上のHolo Focus / Resident設定のHoloカードが正式になり、Debugメニュー内`Holo Surface`は診断用として残す。製品Surfaceには観測可能なWeb / Current Dive / Local Bridge状態だけを表示し、ChatGPT側の思考等を推測しない。Skin QAはDebugへ隔離済み。HoloはこのPC専用Addonと確定したため、外部Holo MCP Server / Secure MCP Tunnel / Remote Identity / Scope方式は製品経路から退役。現行は既存Local MCPの`run_process`から固定`tools/holo-local-client.mjs`を起動し、Core起動ごとのLocal Secretでlocalhost Coreへ直接接続する。Masterが`Dive`を直接押すと5分・一回利用のAttach Windowを開き、BindingはDive IDとattach時刻だけ永続化する。Core側のallowlist Snapshot、bounded Event Queue、独立`holo_say`、Approval / Decision非搭載は維持。`Docs/Holo_Gate0検証結果.md`を正とする
- 2026-09-03にNirai共通Skillの配線だけを追加した。正本は`skills\<name>\SKILL.md`で、現時点のNirai本体にはSkillを1本も同梱しない。CoreのProvider中立Skill Registryがtalk / whisper / resident_chatへ必要時Contextとして渡し、Holoはattach後にLocal Clientの`skills`から同じRegistryを取得する。Skillは呼び出し時に読み直すため、後から`SKILL.md`を置けばCore再起動なしで次回呼び出しから反映する。0件なら既存PromptへSkill Sectionを追加しない。Provider固有Global Skill Directoryは正本にしない
- M4 Codex基準SliceはBrain DriverとAgent Runtimeを分離し、Codex app-server 0.147.0をNirai共通Agent Eventへ正規化。過去レビューで見つかったBlocking群は下記の世代順で修正確認を重ね、最新Diffレビューで新しいBlocking／回帰なしのSAFEを確認した。Codex Process停止は`taskkill / terminate / kill / wait`を含む全体へ有限上限を置き、各停止手段のOSエラーやtimeout後もCredential Home cleanupへ必ず進むよう分離した。Terminal結果はChat / World Memory保存済み`result_reported`とWorld通知済み`result_notified`を別々に永続化し、`reported=true / notified=false`なら次回World接続へTerminal Snapshot / Chat Entry / Task Updateを1回復旧し、送信成功後だけ`notified=true`へ進める。Codex stderrは512-byte chunkで読み、1行最大2048 bytesのみ一時bufferし、DEBUGログには改行escapeした先頭500文字だけを残して以降を破棄する。既存の`itemId / grantRoot` Approval境界、Agent同時実行1件、Event payload上限、Markdown / File Path IPC、World Secret認証、Cancel/Timeout、Event Crash整合、Snapshot順序、Credential Home隔離も維持する。`grantRoot / itemId`変更後の実Codex File Change Approval Live E2Eも2026-09-04に再実施してSAFEを確認済み。Windows Codex 0.147の任意filesystem read完全制限は引き続き保証外。`Docs/M4_Codex基準Slice_検証結果.md`を正とする
- 2026-09-04前回再レビューのBlocking 3件は、(1) Cancel / Timeout後のProvider cleanup失敗を捨てずError Event化して`failed`へ確定、(2) World切断中のTerminal結果を同一Core内の再通知対象へ保持し再接続で復旧、(3) 通常Task開始時のTask Updateへ`working_dir`を含めWorld Storeへ反映、の3点を修正済みで、次の再レビューで修正確認済み。上記M4説明中のProcess停止上限・stderr診断上限は前回修正群として維持する
- 2026-09-04前回Blocking P1（二重停止）は、Sessionがすでに`cancelling`ならManager `cancel()`を再受付せず`False`を返すよう冪等化し、Provider cleanup中に二度目の停止を送っても管理Taskを再cancelしないよう修正。World Agent UIも`cancelling`中は停止ボタンを非表示にし、Master入力カードは`waiting_for_master`時だけ操作可能にした。専用回帰を含め、次の再レビューで修正確認済み
- 2026-09-04前回Blocking P1（起動／終了競合）は、Agent Runtimeに`_stopping`と開始処理完了Eventを持たせ、開始予約をProvider管理Task登録または安全中断まで保持する。`stop()`は最初に新規開始禁止へ切り替え、進行中の開始処理が安全地点へ到達するまで待ってから全Non-Terminal Sessionを停止する。開始Event通知中にstopが割り込んだ場合はProviderを起動せずSessionを`cancelled`へ畳み、管理Taskを残さない。CoreServerも終了処理の冒頭でAgent Runtimeを停止開始状態へ切り替えるため、既存World接続から終了中に届く新規`task_request`も拒否する。固定割り込みのManager回帰と実WebSocket経由のCoreServer回帰を追加し、次の再レビューで修正確認済み
- 2026-09-04最新Blocking P1（Timeout cleanup競合）は、Timeout検出直後にSessionを`cancelling`へ遷移し、`session_timeout` Error Eventを永続化してからProvider interrupt / cleanupへ進む。cleanup中の通常`cancel()`は`False`で冪等拒否され、Provider interruptは1回だけ、cleanup完了後は`failed`へ確定する。固定割り込み回帰で`cancelling`状態・`session_timeout`保持・追加Cancel拒否・cleanup完了・最終`failed`を固定した
- M2では複数Resident表示、Say逐次応答、resident_chat、会話Formation、Global SpeechQueue、複数Brain Provider、Resident単位Model設定、Gemini / Antigravity Brainまで成立済み
- M2へ新機能を逆流させない。生活・World Observation・Retriever / RAGはM3、PC実作業のAgent RuntimeはM4以降を正とする
- Holoはマイルストーン横断Addonであり、頭脳・私的会話・Local MCP連携の要件は12を正とし、旧`chatgpt-mcp`郵便受けResident方式へ戻さない。2026-09-01のHolo Avatar統合以降、World上のHoloはbrain kind `holo-addon`を持つ通常Resident基盤（Identity / Avatar / 配置 / 並び順 / 削除 / 再起動復元）で管理する。holo-addon Residentは1人まで、Brain Driver非接続、HoloへのWhisperはHolo Whisperへ誘導しNirai側へ保存しない。World上のHolo FocusとResident設定のHoloカードがHolo Whisperの正式入口で、Debug入口は診断用。`holo_say`はHolo Resident名で発言・吹き出し演出される。4人以上の初期配置は等間隔（2人・3人専用配置は不変）
- M0のVisual基準点 `m0-pre-stabilization` は海中Worldの回帰参照として維持する
- 2026-08-26以降、通常移動は2026-08-26変更前の旧Move Bそのものを正とする。歩行／遊泳という製品上の別モードは増やさず、Move A側も旧Move Bと同じ経路・速度・姿勢・Animation・Overlayを使う。旧Move B内部で利用している`walk.vrma`等の実装要素は勝手に置換しない。`walk`は内部Clipであり、公開Animation ActionやDebug Pose Editorへ露出しない。DebugでStand / AFK / Sleepを確認する場合も製品と同じPresentation経路を使う
- Cameraは`Docs/詳細設計/04_World.md`のWorld Rig / Focus Rigを正とする。Focus開始時は全身Bone Envelopeを収め、Zoom InではHead側へ注視点を移して下半身の見切れを許容し顔を見やすくする。Focus RigはResidentへ追従しつつCamera Yを海底より上へ保つ。Backdropは内向きSkydome。Resident数に関係なく通常はWorld Rig、ResidentクリックでFocus Rig、背景クリックでWorld Rigへ戻る
- ExpressionはCoreから意味名を受け、WorldがVRM0やAvatar固有のExpression名へ解決する
- 自律的な生活ティック／定期アイドルSchedulerはM3で扱う
- 現在の総合検証基準: Core pytest **202 passed**、World Vitest **39 files / 210 tests passed**、TypeScript typecheck成功、Production Build成功、`git diff --check`成功。Holo Local Bridge E2E、Dive Binding / Core再起動復元、Master操作時刻基準の5分絶対期限・期限直前/超過・ACK消失後再送のidempotency、Binding write / replace失敗時のtransaction rollback・構造化Local Clientエラー・`attach_waiting`維持・同一絶対期限内retry、Dive開始request ID応答、観測可能状態表示、Current Dive永続化失敗のsticky警告と復旧、Skin fail-open、wait success / timeout / cancel、World Say、Holo Web Permission / Navigation security、HoloAddonHost lifecycle（保存済み復元・close/reopen・Dive失敗時の永続rollback・write/rename失敗・Web load失敗・Webフォーカス通知）、Holo / Chat Dock上端resize（viewport再clamp・非表示時height解除）、holo-addon brain kind（singleton・Brain Driver非接続・Whisper境界・holo_say発言者名・4人以上初期配置）、World selection gesture（primary pointerdown→move→pointerup、drag threshold、pointercancel / leave / right-click拒否）、M3 Retriever、M4 Codex Agent Runtime共通Event / Manager / Protocol / Task結果保存 / Codex Home・秘密環境隔離・Cancel冪等性・起動／終了競合・Timeout cleanup競合、Task調停第1巡 / volunteer資格境界 / ゼロ立候補停止 / 相談中busy回帰を含む

過去の検証記録やArchiveと現行設計が矛盾する場合、現行の正本と最新Decisionを優先する。Archiveの過去記述だけを根拠に仕様を巻き戻さない。

## 残している未使用候補

Master承認なしに削除しない。

- `world/public/materials/aerial-beach-01/`
- `world/public/materials/underwater-hybrid/ground-sand-005-*-2k.webp`
- `world/public/animations/idle.vrma` と `afk.vrma`（本番は `afk-01` 以降）
- ルート `package.json`（実プロジェクトは `world/package.json`）
- 一時診断PNGは Safety Snapshot `backup/m0-pre-stabilization-2026-08-27` にのみ残している

この入口には、共通ルールを複製しない。
