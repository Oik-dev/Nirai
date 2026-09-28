# Holo v2 実接続確認

基本設計§17のHolo経路を実ChatGPTで確認する。本書は接続確認手順であり、実行契約の正本ではない。

## 前提

- Holo TaskではChatGPT Webのnative surfaceを正式な会話面としてNirai内へ表示する。
- MasterはChatGPTのnative composer、Stop、Retry / New response、引用、Link、Scroll等をそのまま使う。Nirai側に同等の会話UIを複製しない。
- Task-bound native composer入力はProvider送信より先にHubへ同じ生テキストで保存する。HubがMaster MessageとActive Turnを確定した後、MCP接続名、Turn ID、Master原文だけを含むPromptをChatGPTへ送る。
- WORLD_RULESはNirai-MCP Server Instructionsから適用する。Task履歴や内部状態を別ContextとしてWeb Promptへ複製しない。
- Holo側へ公開するMCP Toolは`nirai_command`一つで、意味上の操作は`GetRunResult / InvokeCapability / AwaitMasterReply / CompleteTask`。Capabilityの利用形はMCP Tool metadataから得る。
- Task ChatはHub上の会話記録として保持する。Holoでは同じ本文を別のbubble / composer面として常時表示しない。
- Master入力を処理済みとみなすのは、そのTurnの最終assistant MessageをHubへ保存した時点。送信確認やTool呼び出しだけでは処理済みにしない。
- 通常StreamingはChatGPT native surfaceだけで表示し、途中断片をTask Chatへ逐次転写しない。正常終了した最終assistant本文だけを同文保存する。
- 最終本文を通常DOMから確定できない場合だけ、同じConversationのReload再取得をAdapter内部fallbackとして使う。

ChatGPTからNirai-MCPへ到達するTunnelは`nirai-v2-runtime` Profileを正本とする。Niraiの標準ランチャーはbuild後にこのRuntimeを停止・再接続し、ready確認後にElectronを起動する。Electron Main / HubはTunnelの認証情報を読まず、Tunnel ProcessのLifecycleをTask制御へ持ち込まない。

## Nirai側の設定

1. ChatGPT側のv2専用MCP接続名をResident設定へ保存する。
2. Resident設定の「作業フォルダー」に、Holoが読み書き・検証してよい絶対パスを保存する。新しく作るTaskから適用される。
3. Holo Taskを選択すると、そのTaskにbindされたChatGPT ConversationがNirai内の主会話面に表示される。未bindの新規Taskは新しいConversationから開始する。
4. Loginが必要な場合も同じnative surface上で行う。別のHolo専用会話Windowは使わない。

Avatarの自己表現を確認する場合は、Resident設定でローカルVRMを選択し、表情・衣装の利用可能数が表示されることを確認する。Taskに紐づく会話で本人に見た目を選んでもらい、`avatar.inspect` → `avatar.set` → `avatar.inspect`の保存状態と`display_applied`、実際の顔・衣装を照合する。衣装情報を持たないモデルでは衣装変更を期待しない。Local smokeでこの経路が通っても、実ChatGPTが適切な場面で自発的に選択することの実証とは区別する。

## 正常確認

1. Holoで新しいTaskを作る。
2. ChatGPT native composerへMaster指示を入力して送信する。
3. HubのTask Chat記録へMaster原文が一度だけ保存され、その後ChatGPTへ`@<MCP接続名>`、Turn ID、Master原文だけを含むPromptが送られる。
4. Holoが必要に応じてNirai-MCP Toolを使う。
5. 生成中はChatGPT native surfaceだけがStreaming表示し、Hubへ途中回答を増殖させない。
6. 生成終了後、ChatGPTの最終assistant Message本文が変更なしでHubへ一つ保存される。
7. `CompleteTask`がなければTaskは未完了のまま。Resume ONなら共通継続判定で次Turnへ進み、OFFなら待機する。
8. Holoが`AwaitMasterReply`を呼んだ場合、質問はnative surfaceに通常assistant本文として表示され、Hubにも同文記録される。Masterは同じnative composerから回答する。
9. Holoが`CompleteTask`した場合、完了予定だけを固定し、同Turnの完全な最終assistant Message保存と同一transactionでCompletedにする。

## Stop / Retry確認

### native Stop

- MasterがChatGPTのStopを押すと、現在のProvider生成だけを止める。
- 取得できる途中assistant本文は当該Turnの記録として保存してよいが、完了回答には使わない。
- Turnは`master_stop`で閉じる。
- Task state、Resume設定、開始済みAction Runは変更しない。
- Resume ONでも自動次Turnを開始しない。
- Taskとして続ける場合はnative composerから新しいMaster指示を送る。

### native Retry / New response

- ChatGPT上のProvider-local再生成として利用できる。
- Hubへ新しいTask Turnを自動作成しない。
- 終了済みTurn IDのNirai-MCP権限は復活しない。
- Retry生成から古いTurn IDでToolを呼んでも拒否される。
- Tool利用を含むTaskとして続ける場合はnative composerから新しい指示を送る。

## Conversation / 異常確認

- MasterがTask-bound Conversationから別Conversationへ移動してもTask stateを変更しない。
- 別Conversationを見ている間、そのTaskのHolo実行はblockedとする。Resumeのためだけに画面を強制Navigationしない。
- Taskを再選択した時は保存済みbindingへ戻してよい。
- 送信前と証明できる一過性失敗だけAdapter内部で有限Retryする。
- 送信後の成否不明、25分上限、Timeout、Session Error、Web切断は現在Turnを閉じる。結果不明の副作用はAction Run側で照合する。
- Provider固有の表示崩れでGlass Skinを安全に適用できない場合は、未加工のnative surfaceへfallbackする。

## 合格条件

実ChatGPTで次を一巡する。

`Holo Task選択 → native composer入力 → HubへMaster原文保存 → ChatGPT送信 → 必要なNirai-MCP Tool利用 → native Streaming → 最終assistant本文をHubへ同文保存 → AwaitMasterReply / Resume → CompleteTask`

加えて、native StopでTaskがPause / Cancelされないこと、Retryで終了済みTurn権限が復活しないこと、Conversation移動時にTask状態が壊れないことを確認する。
