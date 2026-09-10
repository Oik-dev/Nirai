# M4 Task調停第1巡Slice 検証結果

検証日: 2026-09-04

## 判定

**SAFE（Holo再レビュー SAFE + Cursor ExHigh再レビュー SAFE）**

Codex基準Slice SAFEの後続として、Task実行前の第1巡相談、volunteer、担当決定、立候補ゼロ停止までを実装した。本Sliceでは設計上の「意見が割れた場合の第2巡以降、最大8ターン」は扱わない。意見差分を何で判定するかを実装側で推測せず、別の設計判断として残す。

## 実装範囲

- `task_request`受信時にTask IDを発行し、Agent起動前に`runtime/workspace/<task_id>/task.md`を保存
- Agent起動前に`task_update phase=consulting`を通知
- Holo Addonを通常Brain Driverへ流さず、通常Brainを持つResidentだけを順番に`consult`
- Codex / Cursor / Claude / Gemini Brain Driverへ共通`consult` modeを追加
- `consult`応答は`opinion(say) + volunteer`を構造化し、通常talk / whisperのSchemaとは分離
- ProviderにAgent Runtimeが無いResidentは相談・意見表明できるが、Core側で担当資格をFalseへ固定
- 資格外Residentが`volunteer=true`を返しても採用しない
- 有資格立候補が複数ならResident順で最初の立候補者を決定論的に採用
- `task_update phase=assigned`へ`assigned_resident`と`assignment_policy=first_eligible_volunteer`を付与
- 有資格立候補ゼロ、または相談参加可能ResidentゼロならAgent Sessionを作らずTaskを終了
- 相談中の2件目TaskはQueue完成までbusy拒否
- Core終了開始後の新規Task相談は`stopping`で拒否
- Core終了時は進行中Task相談のBrain invocationを有限時間でcancelし、cancel失敗・timeoutでもTask Flow停止へ進む。共通Brain `ProcessManager`も`taskkill → terminate → kill`を各段有限化し、所有Task自体のcancel時も子Process停止を試してからactive参照を外す
- `task_request`受理後はTask Flowを最初のawaitより前に同期登録し、consulting通知待ちの開始Raceでshutdownや2件目Taskに抜けられないようにする
- 第1巡consultは先行Residentの`opinion + effective volunteer`履歴を後続Residentへ累積で渡す
- 相談中およびAgent作業中は、対象Chat Sessionの削除 / World Memory Forget、Residentの削除 / Brain変更を拒否する
- 3人以上のTask相談ではgather後、各発言前に他Residentを現在話者へfaceさせる
- World切断 / 新World接続への置換でFormation Action待ちが失敗しても、Task相談だけはPresentation失敗として継続する。Agent Session作成前に相談がterminalへ到達した場合の最新Task Updateは同一Core内で保持し、次回World接続へ再送する。通常resident_chatの既存キャンセル意味は維持する
- 相談時のResident発言は既存`resident_chat`経路を使い、Chat / World Memory / Speech Bubble / TTSの既存Presentationへ載せる
- Agent Session未作成の`consulting` / 立候補ゼロ`failed` Task UpdateはWorld Noticeでも観測可能
- Provider Capability表示の`agent_work`はCodex固定判定をやめ、AgentRuntimeManagerがAdapterを持つProviderから算出

## 自動回帰

専用回帰で少なくとも以下を固定した。

- consult promptが担当資格の有無を明示し、資格なしでは`volunteer=false`を要求
- Codex consultが専用Output Schemaを使い`volunteer`を解析
- Cursor consultが構造化`volunteer`を解析
- Claude consultが専用JSON Schemaを使い`volunteer`を解析
- Gemini consultがInteractions APIの専用Schemaを使い`volunteer`を解析
- 本Slice実装時点でAgent Runtime未実装だったProviderが立候補しても担当にならない（現行ではCursor ACP Adapter追加済みのためCursorは有資格）
- 複数の有資格Residentが立候補した場合は最初の有資格者を採用
- 資格外Residentしか立候補しなければAgent Sessionを作成しない
- 相談中の2件目`task_request`をbusy拒否
- Core停止開始後の`task_request`を相談開始前に拒否
- consulting通知が未完了でもTask Flowは既に登録済みで、Core stopが確実にcancelできる
- 別World接続から同時に2件目Taskが来ても、consulting通知前の開始窓を含めbusy拒否する
- consult cancelがhang / OSErrorでもCore側停止処理を有限時間で継続し、共通Brain Process Managerもtaskkill起動失敗 / owner task cancelで子Processを後始末する
- 第2 Resident以降へ、それまでの相談発言と有効立候補状態を順に渡す
- 相談中の元Chat Session削除 / ForgetとResident削除 / Brain変更を拒否する
- 3人相談で各発言者へ他2人がfaceする
- 2人Formation待ち中のWorld切断、および新helloによるWorld置換後もTask Flowが継続する。World不在のままゼロ立候補で終了した場合も`failed`を保持し、再接続へ1回再送する。World置換後に有資格立候補者がいる場合はAgent Session開始から`done`まで進む
- 従来のAgent Approval / Question / reconnect / terminal result復旧を維持

## 現行検証

- Core pytest: **202 passed**
- World Vitest: **39 files / 210 tests passed**
- TypeScript typecheck: **成功**
- World Production Build: **成功**
- `git diff --check`: **成功**
- 実Codex File Change Approval Smoke: **成功実績あり / 最終再試行はProvider quotaで完走不能**
  - Task調停6件修正後の16:45 Smokeでは、新しい相談Flowを通過し、相談だけを決定論Fakeに固定した上で実Agent Runtime / Codex app-server / File Change Approvalを使用。Approval 1回→`approve_once`→Task `done`、Chat結果1件、World Memory Episode 1件、一時Codex Home削除までSAFE
  - その後の共通Brain ProcessManager有限停止補強はAgent Runtime経路とは別で、Core全回帰202件で検証
  - 最終Treeから16:52に再Smokeした際は、実File Change Approval→`approve_once`→File Change completedまでは通過した後、Codex側usage limitでProviderが`failed`を返したためTerminal successの再確認は不能。コード起因のFailureとは扱わない
- Smoke Harnessは成功 / 失敗に関係なくM4 Smoke接頭辞をcleanupするよう修正し、`-CleanupOnly`も追加。現在`runtime`配下のM4 Smoke残骸 **0件**

## 2026-09-04 独立レビュー指摘の修正

Holo独立レビュー5件とCursor ExHigh独立レビュー1件を統合し、以下6件を修正した。

1. Task Flow開始登録前のawait窓を廃止し、shutdown / 二重Task開始Raceを防止
2. consult Brain cancelへ有限Timeoutと広い例外境界を追加し、共通Brain ProcessManagerにも有限`taskkill / terminate / kill / wait`とowner-task cancel cleanupを追加
3. 設計03どおり、先行Residentの相談履歴を後続consultへ入力
4. 相談中のChat Session / World Memory / Resident Brain・削除mutationを保護し、Task origin sessionをrequest受理時に固定
5. 3人以上の相談で現在の発言者へfaceするPresentationを追加
6. World切断 / connection replacementでFormation waiterがSemantic Task Flowをcancelしないよう、Task相談専用の切断許容境界を追加。Agent Session前のterminal Task Updateも同一Core内で保持し、再接続へ復旧

修正後はCore 202件、World 210件、typecheck、Production Buildまで再検証済み。実Codex SmokeはTask調停6件修正後にSAFE完走済みで、最終Tree再試行もApproval / File Change完了までは成立したが、その直後にProvider quotaで停止した。Holo独立再レビューでは6件の修正と追加回帰を再確認し、新しいBlocking / 回帰なしでSAFE。Cursor ExHigh再レビューでも前回P1の修正と必須20項目を確認し、新しいBlocking / 回帰なしでSAFE。二重レビューが両方SAFEとなったため、本Task調停第1巡Sliceを正式SAFEとする。

## 後続

本書は第1巡Slice受入時点の歴史記録として、当時の「第2巡未実装 / Queue未実装 / `/task @対象フォルダ名`未実装」状態を本文に残す。2026-09-05にこれら3件は後続Sliceで実装済みとなったため、現在状態は`M4_Task調停第2巡_Queue_Target検証結果.md`を正とする。

- Task相談第2巡以降: `needs_followup`による構造化判定 + 最大8追加ターンとして後続実装済み
- Task Queue: `runtime/task_queue.json`を正本とする永続FIFOとして後続実装済み
- `/task @対象フォルダ名 ...`: `tasks.allowed_dirs` Root basename指定として後続実装済み
- Cursor ACP基準Slice: 2026-09-05 Holo + Cursor ExHigh二重レビュー通過で正式SAFE
- Claude Agent Runtime: 追加有料依存を避けるMaster判断で延期し、現M4完走条件から除外
- Antigravity Agent Runtime: 修正後再レビューとMasterのProvider残余制約受容を経て正式SAFE

Codex基準Sliceと本第1巡SliceのSAFEは、後続最終Sliceの再レビュー状態とは独立して維持する。
