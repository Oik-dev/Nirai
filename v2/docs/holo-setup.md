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
- Resume OFFでは、同じ実行許可のTurnへ割当済みの入力を回答保存失敗だけで自動再送しない。新しいMaster入力、Resume ON、Pause・再起動後の明示再開は既存の開始条件で判定する。
- 通常StreamingはChatGPT native surfaceだけで表示し、途中断片をTask Chatへ逐次転写しない。正常終了した最終assistant本文だけを同文保存する。
- 最終本文を通常DOMから確定できない場合だけ、同じConversationのReload再取得をAdapter内部fallbackとして使う。再読込の直前に生成終了・本文なし・Master下書きなし・同じConversationを再確認し、生成再開や本文出現時は観測へ戻る。

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
   - 送信準備中は入力欄とアプリ候補だけへ「送信中…」を表示し、会話本文は見えたままにする。送信開始後は案内を解除し、native Stopを使える。画像による会話保持は行わない。実際にページを読み込む間は読込案内を表示する。
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

続いて最初の返答前にResumeをONにし、追加のMaster入力なしで自動継続から完了まで確認した。

- Task: `69d26953-13cb-40a8-bf31-179273351c58`
- ChatGPT Conversation: `6abd096a-db5c-83e8-908b-ab907f6a875f`
- 22:06:49.928 JST: Masterの指示を1件保存。22:06:51.229 JSTにResume ONを受付。
- 最初のTurn: `f0a8d94e-4563-41df-aedf-cf161e053ca6`。22:07:08.787 JSTに「1回目」を同文保存して正常終了。
- 次のTurn: `20017951-70f7-4512-b2b5-c802660810bf`。22:07:08.805 JSTに自動開始。22:07:30.442 JSTに完了予定を受付、22:07:35.584 JSTに「2回目」を保存してTaskを`Completed`へ確定。
- 二つのTurnは同じ`instruction_seq=1`、ともに`await_master=0`。会話記録はMaster原文1件、Holo返信2件のみ。Masterも画面で完走を確認した。

この確認は接続選択、MCP完了要求・最終回答保存、Master待ち・回答後の新しいTurn、通常終了後のResume ON自動継続までを示す。実Capabilityのファイル操作・Process検証、Stop / Retry / 異常回復の実接続確認は残る。

## 表示改善のローカル確認（2026-10-01）

送信準備の入力欄案内と不要再読込の抑止を実装し、`npm test`は95件通過。`npm run smoke:holo:preparation`では独立した非表示Window内の旧／現行Provider DOMで、下書き保持、準備中のキー入力抑止、アプリ選択・本文入力・一度の送信、案内解除とStopの操作可能性を確認した。Task切替・格納中の古い読込完了、遅いHub取得、回答取得直前の生成再開・下書き・本文出現もローカルテストで確認した。

画像取得・保存は行わず、MasterのOS入力や起動中のNirai／実ChatGPTも操作していない。後続の通常送信・表示承認は下記の実確認記録を参照する。修正版のResume ON・Task切替・実接続Stop確認は未実施。

## Resume OFFでの再送不具合（2026-10-01）

Masterの手動確認で、入力1件・Resume OFFのTaskから同じ原文を二度送信した。Task `6d4e5481-ca2e-4723-b9ed-751a09419982`、Conversation `6abd31b8-8024-83e8-86f4-77f3cf7f3836`の読み取り記録で照合した。

- 00:58:47.600 JST: Turn `c65433fd-f90f-499f-b289-8da79ab30226`を開始。00:59:10.457に完了予定を受付、00:59:20.826に「ChatGPTの最終回答を取得できません」で終了した。
- 00:59:20.837 JST: Turn `23bcb6f7-2209-402e-8c44-d515c6309f70`を同じ`instruction_seq=1`で開始。00:59:34.418に最終回答を保存してCompletedとなった。Master原文は1件、`resume_enabled=0`だった。

原因は、最終回答を保存できず`handled_instruction_seq`が進まない入力を未送信の新規入力として再評価していたこと。同じ`control_epoch`内の既存Turnへ割り当てた入力番号も開始判定へ使う修正を入れた。処理済み入力の正本は正常な最終回答保存のまま維持する。回答取得失敗・未送信・新しい入力・古いMaster待ち・Pause／再起動後の明示再開の回帰確認を含め、全体100テストは通過した。

再送修正後、MasterがResume OFFで一度だけ送信したTask `f9739bfb-615d-427f-862a-77756146ed70`はTurn `a1b93a1e-c317-4614-8f4a-3fdda9ea9ece`を一つだけ開始した。10:20:52.102 JSTに回答取得失敗で終了した後も再送しなかった。

同時に、実ChatGPTの読み取り診断で回答取得失敗を再現した。10:20:31.300 JSTには実user本文のTurn IDを確認できたが、その直後にuser本文が消えた。10:20:44.800 JSTにはassistant本文「接続確認OK」が表示され、生成終了後もuser本文は戻らず、入力と回答の対応を失っていた。user本文を含まないProvider枠が残った。Sidebarに残るTurn IDや無関係な最新回答は根拠にしない。

実user本文から確認したProvider TurnをActive Turn内だけで保持し、同じdocument・Conversationの一意な枠内のassistant本文を読む修正を入れた。full navigationと別Conversationへの移動時には失効させ、遅延観測からの再利用、重複した目印、別入力を拒否する。keyがない旧DOMでは従来の本文対応を維持する。非表示の旧／現行Provider DOMでuser本文消失・回答取得・誤帰属拒否を確認した。

再確認Task `cddb28be-34a4-4e00-b1bb-c342137d5bbd`も入力1件・Turn1件で、再送は起きなかったが回答取得は失敗した。10:42:04.392 JSTにはuser本文とProvider key `5f341293-f27d-44c6-820e-5ef2b7dce2ec`を確認し、10:42:04.581にはuser本文が消えて枠も`fallback-turn-0`へ置き換わった。document自体は変わっていなかった。10:47:43にはMasterの許可でCLIから再起動し、同Conversationの履歴で元のuser本文と返信「接続確認OK」を取得できた。

この実測に合わせ、同じ観測世代で実入力を確認済みのTurnだけ、生成終了・下書きなし・同じURL／Conversationを直前にも確認して一度だけ履歴を読み直す。保持したProvider keyはその読込許可の証拠として使い、置換された枠の回答を推定しない。読込後は証拠を失効させ、復元した実user本文からのみ回答を採用する。全体106テストは通過した。起動コマンドはAIが非表示のPowerShellから実行してよく、PCのクリック・入力・送信はMasterが行う。

修正版の手動送信で正常保存とTask完了まで確認した。

- Task: `ae2b779f-dcbe-4e93-a58f-f1315101af8d`
- Turn: `6065e128-56af-4e27-aa90-b9ee58aacb90`
- ChatGPT Conversation: `6abdbd1d-d564-83ee-bf29-bc4004991247`
- 10:53:35.910 JST: 実user本文を確認。直後にuser本文とProvider keyが消えたが、再送しなかった。
- 10:54:03.319〜10:54:06.946 JST: 生成終了後に一度だけ同Conversationを再読込し、実user本文と最終回答を復元した。
- 10:54:07.083 JST: 最終回答「接続確認OK」を同文保存し、Turnを`assistant`で正常終了、Taskを`Completed`へ確定した。
- Master入力1件、返信1件、Turn1件。Resume OFF、処理済み入力番号1。`CompleteTask`の完了予定も当該Turnに保存されていた。

一時診断ファイルは削除した。Masterは画面確認後にコミットを承認した。この確認は再送抑止と回答保存・完了までを示し、修正版のResume ON／Task切替・Stop／Retry等を成立扱いにしない。
