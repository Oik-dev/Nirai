# Holo v2 実接続確認

基本設計§17のHolo経路を実ChatGPTで確認する。

## 前提

- v2専用Nirai-MCPを使う。
- ChatGPT WebはHoloの実行Adapter、Nirai Task Chatが正式な会話UI。
- HoloはAddonとして`GetTaskContext`からWORLD_RULESとTask Contextを取得する。
- Web PromptはMCP接続名、Turn ID、`GetTaskContext`取得指示だけ。
- Holoの人間向け発言はChatGPT assistant MessageをそのままNiraiへ保存する。

## Holo公開Command

- `GetTaskContext`
- `GetRunResult`
- `InvokeCapability`
- `RequestMasterInput`
- `CompleteTask`


## 正常確認

1. v2を起動し、ChatGPTへLoginする。
2. 小さな検証WorkspaceでHolo Taskを作る。
3. Niraiから指示を送る。
4. 最小PromptがChatGPTへ送られ、Holoが`GetTaskContext`を取得する。
5. Holoが必要ならNirai-MCPでToolを使う。
6. ChatGPTのassistant Messageが本文変更なしでTask Chatへ反映される。
7. `CompleteTask`がなければTaskは未完了のまま。
8. Resume ONなら次のHolo Turnを開始する。OFFなら待機する。
9. `RequestMasterInput`があれば回答まで待つ。
10. `CompleteTask`で安全・完了条件を検査して終了する。

## 中断確認

25分上限、Timeout、Session Error、Web切断、Conversation利用不能はすべて現在Turnを閉じ、同じ継続判定へ戻す。

Resume ONかつMaster待ち・危険Action待ちでなければ次Turnを開始する。

送信前の短い失敗は有限Retryする。送信後不明では現在Turnを閉じる。Nirai-MCP権限はActive Turn ID / control_epochで確定する。

File変更やProcess等の未確定副作用はAction Run側で照合し、安全が確定するまで競合ActionとTask完了を止める。

## 合格条件

実ChatGPTで次が一巡すること。

`Nirai送信 → Holo Tool利用 → GPT発言 → Niraiへ同文反映 → Resume → CompleteTask`

