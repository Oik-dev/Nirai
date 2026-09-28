# Nirai v2

v2の製品実装。現行仕様はリポジトリ直下の`Nirai_v2_基本設計書.md`、初期自走化中の実装順序は`Nirai_v2_初期自走化計画.md`を正本とする。

## 構成

- `src/hub/` — SQLite Store、共通Command、Capability Registry、Task Engine、utilityProcess入口
- `src/main/` — Electron Main、限定preload、Hub起動・終了、Dashboard IPC
- `src/renderer/` — 実Dashboardと共有Theme。Task状態は持たずHub Snapshotを表示
- `src/shared/` — 共通型と入力指紋
- `resources/` — v2同梱の画像・アイコン。v1の純粋資産から採用し、実行時にv1を参照しない
- `tests/` — Hubの恒久契約テスト
- `scripts/run-electron-smoke.mjs` — Main → Hubの実経路確認
- `scripts/run-ui-smoke.mjs` — Dashboard → IPC → Hub → SQLite → Dashboardの実経路確認
- `src/verification/` — 検証構成だけで登録する代替Capability。製品起動では登録しない

v1のCore / Workflow / Outboxをv2の実行正本として利用しない。

## 現在の到達範囲

Holo経路は **Task / Holo Turn / Action Run** で実装済み。

`Holo Task選択 → ChatGPT native composer入力をHubへ先に保存 → MCP接続名 + Turn ID + Master原文をChatGPTへ送信 → HoloがNirai-MCPを使用 → native Streaming → 最終assistant MessageをHubへ同文保存 → 未完了ならResume`

Holo TaskではChatGPT native surfaceを会話面とし、Task ChatはHub上の記録として保持する。Task完了の要求は`CompleteTask`、Master待ちは通常assistant本文＋`AwaitMasterReply`を正本とし、専用質問Request/UIは持たない。`CompleteTask`はActive Turnへ完了予定を固定し、同じTurnの最終assistant Message保存と同一transactionでTaskをCompletedにする。通常Streamingの途中断片はHubへ逐次転写しない。Resume ONでは未完了Taskを次Turnへ進めるが、Masterがnative StopしたTurnだけは自動継続せず新しいMaster入力を待つ。native Retry / New responseはProvider-localで、終了済みTurnのMCP権限を復活させない。

Tool実行と副作用管理はAction Runに集約する。Conversation ID / URLはChatGPTの送信先ヒントとして扱う。

Hub Schemaは16。Unit 60件、Main smoke、UI smoke、Holo Web Adapter fixture smokeは現行契約で通過済み。fixtureではnative surface埋め込み、Hub先行入力受付、途中Streaming非転写、最終本文取得、Reload fallback、有限送信前待ち、Conversation誤再bind防止を確認している。M1〜M3の出口はHub境界・実ファイル・実Processで確認済み。実ChatGPTでは旧経路でMCP接続とTool往復までは確認したが、native surface化後の再E2Eは未実施のため、M4〜M7の出口はまだ成立扱いにしない。

## 開発と検証

UIの現行基準は[ui-design.md](docs/ui-design.md)。Holoと通常会話は同じ透過Glassを共有し、動く3D海中Worldを背景に保つ。海は見通しのよい南国の浅瀬を基準とし、不規則な小波、白砂、控えめな光の網目、小さな泡を描く。水面と光の模様は同じ波から計算し、海底・水面・Avatarの水中光を`src/renderer/world/optics.js`で共有する。制限付き自由カメラ、クリックFocus、自然な範囲の視線追従を実装している。WASD移動と右ドラッグの見回しは同じ描画周期へまとめて反映する。Resident設定で選んだローカルVRMの参照をHubへ保存し、再起動後も読み込む。描画不能時は同梱静止画へ戻る。狭幅ではTask一覧と会話を切り替える。Holo内部はnativeの会話・操作を維持し、監査済みの面だけ共有Themeへ対応付ける。矩形問題の調査範囲と実画面の確認記録は[holo-surface-audit.md](docs/holo-surface-audit.md)を参照する。

Residentの自己表現は`avatar.inspect/set`を既存Capability経路へ接続済み。現在はTaskに紐づくTurnから、モデルが公開する標準表情・衣装部品の表示を本人が選べる。選択を既存Run結果へ保存し、表情を補間しながらWorldへ反映する。保存成功と表示確認を区別し、モデル差替え・古い版・終了したTurnからの変更を拒否する。瞬き・自然な視線・髪の揺れは身体機能として動く。自律歩行、意図的なPose / Gesture、音声LipSync、Task外での自己表現呼出は未実装。実ChatGPTが会話中に適切な自己表現を選ぶ実運用確認は未実施。

Windows上で`v2/`から実行する。

```powershell
Set-Location D:\Products\Nirai\v2
npm ci
npm test
npm run smoke
npm run smoke:ui
npm run smoke:holo
npm run smoke:world
npm start
```

恒久テストは、Turn権限、assistant Message同文反映、Resume、Master待ち、Action副作用、Pause / Cancel / 再起動、MCP認証とCommand idempotencyを検証する。

`smoke:holo`はWeb Adapterのfixture検証。実ChatGPT接続は`docs/holo-setup.md`で確認する。

`smoke:world`は独立Data RootのElectronで実描画、WASDと右ドラッグの同時操作、UIへフォーカスを移した際の移動停止、停止・後始末、描画Context喪失と復旧を検証する。VRM未指定でも360×600・620×980・1500×930の配置と、Dashboard格納時の初期視点・見上げ・反対向き・海底近く・水面近くを撮影する。`NIRAI_V2_UI_CAPTURE_DIR`を指定すると、その画像を保存できる。撮影は外観比較の材料であり、見た目の良否を自動判定するものではない。

`NIRAI_V2_WORLD_SMOKE_AVATAR`に手元のVRMの絶対パスを渡した場合だけ、実モデルの読込・クリックFocus・首の制限・UIへの入力分離・自己表現の保存と実反映・紛失ファイル・Reload・Hub再起動後の再表示も確認する。`NIRAI_V2_WORLD_SMOKE_WARDROBE=1`では実衣装部品の切替を必須にする。現在の描画はLapan（VRM 0.x）とMirdo（VRM 1.0）で検証済み。衣装対応モデルの実部品切替は今回の再検証には含めていない。任意のVRMの外観や、動く背景上の実ChatGPTとの同時運用すべてを保証するものではない。モデルはリポジトリに同梱しない。

## M3の利用と制約

Holo用Local MCPは専用接続を使い、公開Toolは`nirai_command`一つに集約する。標準ランチャーはbuild後、既存の`nirai-v2-runtime` Profileを正本としてNirai-MCP Tunnelを再接続し、ready確認後にElectronを起動する。Hub / Electron MainはTunnelの認証情報やProcess管理を持たない（確認手順は`docs/holo-setup.md`）。

Taskの作業範囲はResident設定の「作業フォルダー」から新規Taskへ固定する。未設定のTaskではローカルToolを使えない。

Holoへ公開するMCP Toolは`nirai_command`一つで、意味上の操作は`GetRunResult / InvokeCapability / AwaitMasterReply / CompleteTask`だけ。WORLD_RULESはNirai-MCP Server Instructions、Capabilityの利用形はMCP Tool metadataから与える。会話だけで完結するTaskでは、疎通確認のためだけに`InvokeCapability`しない。

ローカルCapabilityはread / search / apply_patch / inspect / run_command / cancelを提供する。File変更とProcess実行はAction RunとしてScope、入力指紋、副作用、停止・cleanupをHubで管理する。

任意Shell、Scope外書込、依存導入、公開、破壊的Git等はPolicy Gateを迂回させない。結果不明のActionを自動再実行しない。

## 次の着手点と引き継ぎ

ChatGPT側の接続名と作業フォルダーを設定したうえで、実ChatGPTで次を一巡確認する。Holo Taskを選択するとChatGPT native surfaceをNirai内へ表示し、Loginも同じsurfaceで行う。

`native composer入力 → HubへMaster原文保存 → ChatGPT送信 → Holo → Nirai-MCP Tool → native最終回答 → Hubへ同文保存 → Resume → CompleteTask`

確認対象は、native composer送信の一度だけのHub保存、最終assistant本文の完全一致、`CompleteTask`後の最終回答反映、`AwaitMasterReply`→native composer回答→次Turn、複数Turn継続、native Stop後の自動再開抑止、Retryでの旧Turn権限拒否、Conversation移動、25分 / Timeout / Session Error後のResume。
