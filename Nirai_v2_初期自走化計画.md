# Nirai v2 初期自走化計画

> Holoがv2だけでNirai自身の開発Taskを完遂するまでの期間限定計画。動作仕様は`Nirai_v2_基本設計書.md`を正本とする。

## 到達点

DashboardからHoloへTaskを渡し、ChatGPT native surfaceを会話面として使いながらNirai-MCPで調査・修正・検証し、Resume ONなら`CompleteTask`まで自動継続する。

Holo TaskのMaster入力はnative composerからHubへ先に保存してChatGPTへ送り、Task-bound assistant MessageはそのままTask Chat記録へ保存する。

## M1. Hub最小核

- Electron Main + Node Hub utilityProcess。
- SQLiteへTask / Holo Turn / Action Run / Approval / Conversation / Messageを保存。
- Task state、control_epoch、Resume、Action副作用・cleanupをHubだけが所有。
- Engineは`CompleteTask / AwaitMasterReply / それ以外は未完了`を共通原理として継続判断する。質問本文とMaster回答は通常のTask Chatへ置く。
- 代替CapabilityでTask開始、Tool、Master待ち、Approval、完了、Pause / Cancel、再起動を検証。

**出口:** AC01・AC04・AC05・AC07・AC08のHub境界が成立する。

## M2. UI配線

- Task作成、Pause / 再開、Holo用Resume ON / OFF、Approval / Activity / Terminal表示。
- 通常ResidentはTask Chat、Holo TaskはChatGPT native surfaceを主会話面とし、Nirai固有Controlをその外側へ配置する。
- v1の有用なWebContentsView埋め込み・Glass / Skin表現を候補として再利用し、Providerのcomposer、Stop、Retry、思考表示、Link、Scrollを阻害しない。
- RendererはHub SnapshotだけをTask状態の正本として表示する。

**出口:** AC01・AC09を実画面で確認する。

## M3. Nirai-MCPとローカルTool

- 認証付きLocal MCP橋渡し。
- Active Turn + Task control_epochでHoloのTool権限を限定。
- Holo公開操作は`GetRunResult / InvokeCapability / AwaitMasterReply / CompleteTask`。MCP Toolは`nirai_command`一つに集約する。
- File読取・検索・patch、有限Command、inspect / cancel。
- 危険操作はAction RunとPolicy Gateで管理。

**出口:** AC04・AC07・AC10を実ファイル・Processで確認する。

## M4. Holo Web Adapter

- WebContentsViewをChatGPT native conversation surfaceとしてNirai内へ表示する。
- Holo Taskのnative composer送信をAdapter境界で捕捉し、Master原文をHubへ保存してActive Turn確定後にだけChatGPTへ送る。Task外の通常ChatGPT操作はTaskへ取り込まない。
- Web PromptはMCP接続名、Turn ID、そのTurnで処理するMaster原文または短い継続指示だけを送る。
- WORLD_RULESはNirai-MCP Server Instructionsから適用し、Task履歴・内部状態を別Contextとして再構成しない。
- Task-bound ChatGPT assistant Messageを本文変更なしでTask Chat記録へ保存。`CompleteTask`はActive Turnへ完了予定を固定するだけで、同Turnの最終assistant Message保存と同一transactionでTaskをCompletedにする。
- native StopはTurnを`master_stop`で閉じるだけでTask / Resume / Actionを変更せず、Resume ONでも自動継続しない。native Retry / New responseはProvider-localとし、終了済みTurnのMCP権限を復活させない。
- Conversationは表示・送信先と推論Contextの参照のみ。別Conversationへ移動してもTask状態を変えず、Resumeのために画面を強制Navigationしない。
- 最終本文を通常DOMから確定できない場合だけ、同ConversationのReload再取得をProvider境界内fallbackとして使う。Nirai Task Chatへ転写する別会話surfaceを恒久fallbackとして残さない。

**出口:** 実ChatGPTでnative composer → Hub保存 → ChatGPT送信 → Nirai-MCP Tool → native GPT発言 → Hub記録を一巡させ、Stop / Retryを含めAC06・AC09を確認する。

## M5. Resume OFF

- 一つのHolo Turnで複数Toolを使える。
- GPT発言後はTask未完了でも自動次Turnを開始しない。
- native Stopを押してもTask / Actionが壊れず、native Retry / New responseで終了済みTurnのMCP権限が復活しない。
- Taskとして続ける時はnative composerから新しいMaster指示を送り、新Turnを開始する。

**出口:** AC02・AC09を実Holoで確認する。

## M6. Resume ON

Resume ONでは、Taskが未完了かつMaster待ち・安全待ちでなければ次Turnを開始する。

同じ原理で扱う対象:

- 通常のGPT発言終了
- 25分区切り
- Timeout
- Session Error
- Web切断・Conversation利用不能後の復旧

Masterのnative Stopだけは明示的な停止意思として自動継続対象から除外する。

**出口:** `CompleteTask`または`AwaitMasterReply`によるMaster待ちまで自動継続し、AC03を確認する。

## M7. 停止・異常・再起動

- Pause / Cancel後の古いTurn / Actionを拒否。
- Actionのpartial / unknown effectsを安全に照合。
- Provider障害はTaskを例外状態へ変えず、未完了ならM6の継続判定へ戻す。
- 再起動時も未確定副作用を自動再実行しない。

**出口:** AC04〜AC09を実接続を含めて確認する。

## M8. Self-host Cutover

実際のNirai開発Taskをv2だけで実行する。

1. Dashboardから依頼。
2. HoloがNirai-MCPで調査・変更・検証。
3. 必要な複数TurnをResumeで継続。
4. ChatGPT native surfaceを主会話面として維持し、Task-bound Holo発言をHubのTask Chat記録へ保存。
5. Holoが`CompleteTask`で完了予定を固定し、同じTurnの完全な最終assistant回答がHubへ保存された時点でTaskもCompletedになって終了。
6. 候補版を隔離検証し、Master明示操作で切替確認。

**出口:** AC11・AC12を満たす。

## v1の扱い

v1は仕様正本にしない。使える知見・純粋資産だけを現在の契約へReuse / Redesignし、旧Task制御・Workflow・Outboxをv2経路へ持ち込まない。

## 退役

M1〜M8成立後、本書を`archive/`へ移し、現行仕様の根拠から外す。
