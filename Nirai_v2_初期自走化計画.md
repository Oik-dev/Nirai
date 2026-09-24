# Nirai v2 初期自走化計画

> Holoがv2だけでNirai自身の開発Taskを完遂するまでの期間限定計画。動作仕様は`Nirai_v2_基本設計書.md`を正本とする。

## 到達点

DashboardからHoloへTaskを渡し、Nirai-MCPで調査・修正・検証し、Resume ONなら`CompleteTask`まで自動継続する。

HoloのChatGPT assistant MessageはそのままTask Chatへ反映する。

## M1. Hub最小核

- Electron Main + Node Hub utilityProcess。
- SQLiteへTask / Holo Turn / Action Run / Master Request / Conversation / Messageを保存。
- Task state、control_epoch、Resume、Action副作用・cleanupをHubだけが所有。
- Engineは`CompleteTask / RequestMasterInput / それ以外は未完了`だけで継続判断。
- 代替CapabilityでTask開始、Tool、Request、完了、Pause / Cancel、再起動を検証。

**出口:** AC01・AC04・AC05・AC07・AC08のHub境界が成立する。

## M2. UI配線

- Task作成、Task Chat、Pause / 再開、Holo用Resume ON / OFF。
- CHECK、承認、質問回答、完了、取消、Terminal表示。
- RendererはHub Snapshotだけを表示し、Task状態を持たない。

**出口:** AC01・AC09を実画面で確認する。

## M3. Nirai-MCPとローカルTool

- 認証付きLocal MCP橋渡し。
- Active Turn + Task control_epochでHoloのTool権限を限定。
- Holo公開操作は`GetTaskContext / GetRunResult / InvokeCapability / RequestMasterInput / CompleteTask`。
- File読取・検索・patch、有限Command、inspect / cancel。
- 危険操作はAction RunとPolicy Gateで管理。

**出口:** AC04・AC07・AC10を実ファイル・Processで確認する。

## M4. Holo Web Adapter

- WebContentsViewをChatGPT実行Adapterとして使う。
- Web PromptはMCP接続名、Turn ID、`GetTaskContext`取得指示だけを送る。
- WORLD_RULESとTask Contextは`GetTaskContext`から取得する。
- ChatGPT assistant Messageを本文変更なしでTask Chatへ保存。
- Conversationは送信先ヒントのみ。
- Login / Draft / 生成中を保護する。

**出口:** 実ChatGPTでNirai送信 → Nirai-MCP Tool → GPT発言 → Nirai反映を一巡させ、AC06を確認する。

## M5. Resume OFF

- 一つのHolo Turnで複数Toolを使える。
- GPT発言後はTask未完了でも自動次Turnを開始しない。
- Masterの追加入力または明示再開で続行できる。

**出口:** AC02を実Holoで確認する。

## M6. Resume ON

Resume ONでは、Taskが未完了かつMaster待ち・安全待ちでなければ次Turnを開始する。

同じ原理で扱う対象:

- 通常のGPT発言終了
- 25分区切り
- Timeout
- Session Error
- Web切断・Conversation利用不能後の復旧

**出口:** `CompleteTask`または`RequestMasterInput`まで自動継続し、AC03を確認する。

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
4. ChatGPT上の全Holo発言をTask Chatへ反映。
5. Holoが`CompleteTask`して終了。
6. 候補版を隔離検証し、Master明示操作で切替確認。

**出口:** AC11・AC12を満たす。

## 先行させないもの

Memory本実装、Serina本接続、Cursor / Codex、Resident同士の自律会話、追加Capability、本格並列化、高度Scheduling、自動Updater、汎用Plugin基盤。

## v1の扱い

v1は仕様正本にしない。使える知見・純粋資産だけを現在の契約へReuse / Redesignし、旧Task制御・Workflow・Outboxをv2経路へ持ち込まない。

## 退役

M1〜M8成立後、本書を`archive/`へ移し、現行仕様の根拠から外す。
