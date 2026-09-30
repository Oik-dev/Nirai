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

1. ChatGPTのアプリ一覧に実際に表示されるv2専用MCP接続名をResident設定へ保存する。製品名やTunnel Profile名から推測しない。2026-09-30に確認した接続表示は`Nirai`で、v2のHubへ到達する。これは製品・Repositoryの正式な名称移行とは別の設定である。
2. Resident設定の「作業フォルダー」に、Holoが読み書き・検証してよい絶対パスを保存する。新しく作るTaskから適用される。
3. Holo Taskを選択すると、そのTaskにbindされたChatGPT ConversationがNirai内の主会話面に表示される。未bindの新規Taskは新しいConversationから開始する。
4. Loginが必要な場合も同じnative surface上で行う。別のHolo専用会話Windowは使わない。

Avatarの自己表現を確認する場合は、Resident設定でローカルVRMを選択し、表情・衣装の利用可能数が表示されることを確認する。Taskに紐づく会話で本人に見た目を選んでもらい、`avatar.inspect` → `avatar.set` → `avatar.inspect`の保存状態と`display_applied`、実際の顔・衣装を照合する。衣装情報を持たないモデルでは衣装変更を期待しない。Local smokeでこの経路が通っても、実ChatGPTが適切な場面で自発的に選択することの実証とは区別する。

## 正常確認

1. Holoで新しいTaskを作る。
2. ChatGPT native composerへMaster指示を入力して送信する。
3. HubのTask Chat記録へMaster原文が一度だけ保存され、その後Adapterが`@<MCP接続名>`の一覧から名前が完全一致するアプリを一つ選択する。Providerが作ったアプリ表示を保持してTurn IDとMaster原文を追加し、選択状態と本文を確認してから送信する。単なる`@`文字列では送信しない。Masterが先にアプリを選んで入力した場合も、アプリ表示を本文へ混ぜずMasterの本文を保存する。
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
- 接続候補が見つからない・複数一致する・選択状態を確認できない場合は未送信として止める。Masterの既存下書きや準備中に編集された本文は上書き・消去しない。アプリ一覧でのEnterはアプリ選択としてProviderに任せ、Task送信にしない。
- 送信後の成否不明、25分上限、Timeout、Session Error、Web切断は現在Turnを閉じる。結果不明の副作用はAction Run側で照合する。
- Provider固有の表示崩れでGlass Skinを安全に適用できない場合は、未加工のnative surfaceへfallbackする。

## 合格条件

実ChatGPTで次を一巡する。

`Holo Task選択 → native composer入力 → HubへMaster原文保存 → ChatGPT送信 → 必要なNirai-MCP Tool利用 → native Streaming → 最終assistant本文をHubへ同文保存 → AwaitMasterReply / Resume → CompleteTask`

加えて、native StopでTaskがPause / Cancelされないこと、Retryで終了済みTurn権限が復活しないこと、Conversation移動時にTask状態が壊れないことを確認する。

## 実確認記録（2026-09-30）

Masterが修正版のnative composerから「接続確認OKとだけ返答して、このTaskを完了してください。」を送信した。

- Task: `0d1c7428-048e-482e-b7bb-07a3c03dc2a1`
- Turn: `24e19f1d-1bcd-41ba-98a5-4d2d868b2205`
- ChatGPT Conversation: `6abd0025-39e4-83e8-8723-8e9b10e29354`
- 21:27:33.952 JST: 当該TurnのMCP Command receiptに`completion_pending=true` / `reply_required=true`を保存。
- 21:27:47.036 JST: 最終回答「接続確認OK」を同文保存し、Turnを`assistant`で正常終了、Taskを`Completed`へ確定。
- Hubの会話記録はMaster原文1件、Holo最終回答1件のみ。実画面にも返信とTask完了を確認。

続いてMasterの手動操作で質問待ちと回答後の継続を確認した。AIはPCの入力操作を行わず、DBを読み取り照合した。

- Task: `479a9b57-ba4e-4dab-8857-dde9cb6b4f71`
- ChatGPT Conversation: `6abd02c3-7d34-83e8-b40f-0acc6a054200`
- 最初のTurn: `e38adac0-e51f-4218-9ff5-7f408571dd2e`。21:38:36.591 JSTに`AwaitMasterReply`受付。21:38:53.529 JSTに質問「好きな色は？」を同文保存し、Turnを正常終了して回答待ちを維持。
- 次のTurn: `fd3696f2-a6d3-401c-a8f5-0ad40e601bb7`。Masterの「青」を保存後、21:46:40.155 JSTに開始。21:46:57.119 JSTに完了予定を受付、21:47:01.991 JSTに「確認完了」を保存してTaskを`Completed`へ確定。
- 会話記録は最初の指示・質問・Master回答・最終回答の4件のみ。同じConversationで二つのTurnを使用した。ResumeはOFF。

この確認は接続選択、MCP完了要求・最終回答保存、Master待ち・回答後の新しいTurnまでを示す。Resume ONの自動継続、実Capabilityのファイル操作・Process検証、Stop / Retry / 異常回復の実接続確認は残る。
