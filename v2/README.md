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

Hub Schemaは17。恒久テスト全体、Main smoke、UI smoke、Holo fixture smoke、World smokeが通過済み。今回のWorld smokeはVRM未指定で、描画・カメラ操作・Context復旧を確認した。実VRMの検証範囲は後述する。Holo fixtureでは旧・現行DOMのHub先行入力受付、実アプリ選択・選択失敗時の未送信・下書き保護・取消時の入力片付け、別Conversationの通常送信維持、Stop、最終本文取得とReload fallbackを確認している。Enter受付を送信ボタン名に依存させず、活動表示や操作ボタンを回答本文へ混ぜない。

Task外の通常Conversationは、Say・相手ごとのWhisper・送信時の参加者・本文原文・記録時刻・返答状態をHubへ保存する。ChatModeの左下に会話窓を表示し、Masterの発言を右、Residentの発言を左へ寄せる。初期幅は528pxで、上中央のハンドルは高さ、右辺は幅、右上の角は幅と高さを調整する。相手ごとの下書きと読書位置を保持し、空の入力欄で通常ResidentをFocusしたときだけWhisperを開く。背景クリックはカメラを自由視点へ戻すが、会話の相手は変えない。Resident設定から通常Residentの追加・名前・Role・会話用AI・Model・ローカルPersona File参照を設定できる。Persona本文は生成時にFileから読む。

製品起動でHoloの通常会話Providerを登録する。ChatModeのSay / HoloへのWhisperをHubへ保存してから、MCP接続名やTurn IDを付けずChatGPTへ送る。最終返答は対象Messageに結び付けて元のSay / Whisperへ同文保存する。通常会話用の一つのChatGPT Conversation参照を永続保存し、Task用Conversationとは混ぜない。Resident設定の「ChatGPTを開く」からTaskを作らずログインできる。Taskと通常会話は一つのWeb表示を直列使用し、失敗・Timeout・終了時に無断再送しない。Serina等の通常Providerは未接続。

通常Providerへ渡す履歴は、同一Residentが直接受信したSay / 本人Whisperを横断した直近40件。対象入力より後の新しい入力は含めず、以前の入力に対する遅い返答は引き継ぐ。Holoの通常送信は対象Message ID、Say / Whisper・送信者・直接受信者、Master原文を中心に短くする。共通ChatGPT Conversationの既存履歴は再複製せず、新会話開始時の受信履歴、他Residentの未送信発言、Personaの変更を必要時だけ補う。送信済み情報は配信確認後に同じConversationへ結び付けて記録する。Whisper原文を当事者外へ直接渡さず、内容を別の場で話すかは基本設計§13の本人裁量とする。会話窓の履歴・下書きの分離は本人の記憶の分離ではない。Local Memoryの長期保存・検索は未実装で、ChatGPT側の記憶機能とは区別する。

通常会話の恒久テストではPersona読込、共有履歴と他人のWhisper除外、未来入力の混入防止、返答の対応、終了・Timeout・遅延報告、同一Commandの再送防止を確認する。Holo fixtureは実Hub / SQLiteと模擬ProviderのWebContentsViewを接続し、1通目生成中にSay / Whisper / Sayを連投して、共通Conversationへの送信と対応する返答3件の保存、Task / Runが増えないこと、Provider履歴に各入力・返答が1件ずつ残り後続Promptへ重ねて貼らないことを確認した。Promptの恒久テストは既存会話への移行・再起動、Persona変更と解除、他ResidentのSay差分、未送信履歴の参照、配信未確認時の記録維持、履歴サイズ上限を確認する。UI smokeでは実画面からHubへのSay / Whisper保存、Holo Focus、時刻、下書き、上下・横・角のドラッグと取消、サイズ縮小後の復元、TaskなしChatGPT設定画面、360 / 620 / 1500px配置と起点へのフォーカス復元を確認した。左右の吹き出し、日付区切りとサイズ変更後の読書位置は表示用履歴による検証。これらのローカル検証は実ChatGPTでの通常会話成立を示さず、実接続の基本送受信記録は[Holo setup](docs/holo-setup.md)へ分けて記載している。

2026-09-30の実ChatGPTで、アプリ選択、Master原文の一度だけの保存、Turn開始、Conversationへの紐づけ、Nirai-MCPの`CompleteTask`受付、返信「接続確認OK」の同文保存、同じTurnの正常終了とTaskのCompleted確定を確認した。さらに`AwaitMasterReply`で「好きな色は？」を質問して待機し、Masterの「青」を受けて同じConversationの新しいTurnを開始、「確認完了」を同文保存してTaskをCompletedにした。各入力・質問・最終回答は1件ずつで、Resume OFFのまま回答待ちを維持した。別Taskでは最初の返答前にResumeをONにし、「1回目」の保存後、新しいMaster入力なしに次Turnへ自動継続、「2回目」を保存してCompletedへ確定した。完了予定の受付と最終回答保存を別時刻のDB記録で照合した。M1〜M3の出口はHub境界・実ファイル・実Processで確認済み。実Capability利用、Stop / Retry / 異常回復等を含むnative surface化後の一巡はまだ未成立のため、M4〜M7全体の出口は成立扱いにしない（実接続記録は[Holo setup](docs/holo-setup.md)）。

## 開発と検証

Codex CLIをResidentのAI接続として追加した。既存のChatGPTログインを使い、APIキー欄を持たない。接続確認でModel一覧を取得し、Say / WhisperとTaskへ同じ接続を使う。Taskの操作は既存のHub Command / 承認 / Action Runへ通し、最終本文の保存で完了を確定する。Codexの直接ローカル操作と既存MCPは無効にし、ResumeはHolo専用のままにする。設定と検証範囲は[Codex CLI setup](docs/codex-setup.md)を参照する。

UIの現行基準は[ui-design.md](docs/ui-design.md)。Holoと通常会話は同じ透過Glassを共有し、動く3D海中Worldを背景に保つ。海は見通しのよい南国の浅瀬を基準とし、不規則な小波、白砂、控えめな光の網目、小さな泡を描く。水面と光の模様は同じ波から計算し、海底・水面・Avatarの水中光を`src/renderer/world/optics.js`で共有する。制限付き自由カメラ、クリックFocus、自然な範囲の視線追従を実装している。WASD移動と右ドラッグの見回しは同じ描画周期へまとめて反映する。Resident設定で選んだローカルVRMの参照をHubへ保存し、再起動後も読み込む。描画不能時は同梱静止画へ戻る。狭幅ではTask一覧と会話を切り替える。Holo内部はnativeの会話・操作を維持し、監査済みの面だけ共有Themeへ対応付ける。矩形問題の調査範囲と実画面の確認記録は[holo-surface-audit.md](docs/holo-surface-audit.md)を参照する。

Residentの自己表現は`avatar.inspect/set`を既存Capability経路へ接続済み。現在はTaskに紐づくTurnから、モデルが公開する標準表情・衣装部品の表示と、意味付きの衣装・アクセサリ・体形の選択肢を本人が選べる。複数MeshとMorphの組み合わせはVRM内のMetadataで定義し、モデル名の分岐をNiraiへ持たない。既存の部品表示方式も維持する。契約と操作例は[avatar-appearance.md](docs/avatar-appearance.md)を参照。選択を既存Run結果へ保存し、表情を補間しながらWorldへ反映する。保存成功と表示確認を区別し、モデル差替え・古い版・終了したTurnからの変更を拒否する。瞬き・自然な視線・髪の揺れは身体機能として動く。自律歩行、意図的なPose / Gesture、音声LipSync、Task外での自己表現呼出は未実装。実ChatGPTが会話中に適切な自己表現を選ぶ実運用確認は未実施。

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

恒久テストは、Turn権限、assistant Message同文反映、Resume、Master待ち、Action副作用、Pause / Cancel / 再起動、MCP認証とCommand idempotency、分割された通信の復元とサイズ上限を検証する。build時には未使用の変数・引数・importも検出する。Electron検証の起動・ログ・一時環境の後片付けは`scripts/electron-smoke.mjs`へ集約する。子Processの作業フォルダーも専用の一時保存先にし、Windowsのスペルチェックが制限環境で作る不正な相対パスの残骸をプロジェクトへ残さない。相対指定の画像出力先は呼出元基準の絶対パスへ変換し、検証後も成果物を保持する。CLIから実Niraiを起動する場合も、アプリ引数は絶対パス、作業フォルダーは製品Data Rootを指定する。

`smoke:holo`はWeb Adapterのfixture検証。実ChatGPT接続は`docs/holo-setup.md`で確認する。

`smoke:world`は独立Data RootのElectronで実描画、WASDと右ドラッグの同時操作、UIへフォーカスを移した際の移動停止、停止・後始末、描画Context喪失と復旧を検証する。VRM未指定でも360×600・620×980・1500×930の配置と、Dashboard格納時の初期視点・見上げ・反対向き・海底近く・水面近くを撮影する。`NIRAI_V2_UI_CAPTURE_DIR`を指定すると、その画像を保存できる。撮影は外観比較の材料であり、見た目の良否を自動判定するものではない。

`NIRAI_V2_WORLD_SMOKE_AVATAR`に手元のVRMの絶対パスを渡した場合だけ、実モデルの読込・クリックFocus・首の制限・UIへの入力分離・自己表現の保存と実反映・紛失ファイル・Reload・Hub再起動後の再表示も確認する。`NIRAI_V2_WORLD_SMOKE_WARDROBE=1`では実衣装部品の切替を必須にする。現在の描画はLapan（VRM 0.x）とMirdo（VRM 1.0）で検証済み。Yumeka v1.0.4の対応VRMでは、通常・下着・ジャケットの往復、各衣装と全11外見項目の選択肢、実Mesh / Morph値の反映確認、下着状態のReload・Hub再起動復元を実描画で確認済み。確認画像はModel Converterの`validation/yumeka-appearance/review.html`へ保存。正本の材質・テクスチャ・顔は保持し、不足するBraとJacketのみを追加した別VRMを使用する。Yumekaの外観はMasterが目視承認済み。実ChatGPTが選ぶ運用確認は未実施。任意のVRMの外観や、動く背景上の実ChatGPTとの同時運用すべてを保証するものではない。モデルはリポジトリに同梱しない。

## M3の利用と制約

Holo用Local MCPは専用接続を使い、公開Toolは`nirai_command`一つに集約する。標準ランチャーはbuild後、既存の`nirai-v2-runtime` Profileを正本としてNirai-MCP Tunnelを再接続し、ready確認後にElectronを起動する。Hub / Electron MainはTunnelの認証情報やProcess管理を持たない（確認手順は`docs/holo-setup.md`）。

Taskの作業範囲はResident設定の「作業フォルダー」から新規Taskへ固定する。未設定のTaskではローカルToolを使えない。

Holoへ公開するMCP Toolは`nirai_command`一つで、意味上の操作は`GetRunResult / InvokeCapability / AwaitMasterReply / CompleteTask`だけ。WORLD_RULESとTask完了・Master待ちの呼び出し順はNirai-MCP Server Instructions、Capabilityの利用形はMCP Tool metadataから与える。会話だけで完結するTaskでは、疎通確認のためだけに`InvokeCapability`しない。実MCP通信のローカル検証では、`CompleteTask`の受付だけではRunningを維持し、同Turnの最終回答を同文保存してCompletedへ確定することと、終了済みTurnの新規操作拒否を確認している。

ローカルCapabilityはread / search / apply_patch / inspect / run_command / cancelを提供する。File変更とProcess実行はAction RunとしてScope、入力指紋、副作用、停止・cleanupをHubで管理する。

任意Shell、Scope外書込、依存導入、公開、破壊的Git等はPolicy Gateを迂回させない。結果不明のActionを自動再実行しない。

## 次の着手点と引き継ぎ

Task外の通常Conversation・ChatMode・Resident設定・Persona File参照と、HoloのSay / Whisper接続経路を実装した。2026-10-01にMasterが実ChatGPTへWhisper / Sayを各1件送り、元の発言先への返答保存を読み取り専用で照合した。短縮前Promptでの基本送受信は成立。次は[Holo setup](docs/holo-setup.md)の「Task外のSay / Whisper確認」で、短縮版Prompt、受信履歴の引継ぎ、Taskとの切替、異常時を利用中に確認する。Serinaの既存`/api/chat`は本文だけを受け取り、GUI共通の一つのSessionを使う。本人の記憶共有は基本設計§13に適合し、記憶隔離やSession分割APIを接続の前提にしない。発言先・当事者の伝達、元発言への返答の対応、送信結果の照合をどう接続するかと、Serina側APIの追加範囲は未確定。Serinaの実Providerは未接続。採用する表示・操作は基本設計§13・§21と[ui-design.md](docs/ui-design.md)を参照する。実接続確認が残る範囲は成立扱いにせず、利用中の確認と保存記録の照合を継続する。

Provider Adapterの接続選択を実ChatGPTのDOMに合わせた。`@<接続名>`を普通の本文として送る方式をやめ、名前が完全一致する一つのアプリを選択し、Providerのアプリ表示を保持して本文を追加する。送信前に実アプリの選択状態と本文を照合する。候補がない・複数ある場合は送信せず、Masterの既存下書きや準備中の編集を保護する。アプリ一覧のEnterはProviderの選択操作として維持する。ローカルの`nirai-v2-runtime` Profileと起動中MCP Processはv2の`out/src/bridge/mcp.js`を指すことを確認済み。ChatGPTの`Nirai`接続からv2の終了済みTurnに対する拒否応答も取得した。接続名の設定と製品名・Repositoryの正式移行は分けて扱う。

接続選択からTask完了、Master待ちから回答後の新しいTurn、Resume ONの自動継続までの実確認は通過し、接続名とv2作業フォルダーを設定済み。送信準備時は入力欄とアプリ候補だけへ「送信中…」を重ね、会話本文を表示し続ける。画像保存・画像による会話保持は使わない。回答取得fallbackの再読込は、同じConversationで生成終了・本文なし・下書きなしを直前にも確認した場合へ限定した。Task変更や格納は前の読込完了を待たずnative画面を隠す。これらの表示改善は非表示の模擬Providerとローカルテストで検証し、修正版の通常送信は実ChatGPTでも正常保存とTask完了まで確認した。Masterの表示確認とコミット承認も済んでいる。修正版でのResume ON・Task切替の実確認は残る。その後、ローカルCapabilityによるファイル操作と検証、native Stop / Retry / 異常回復を実接続で確認し、次を一巡する。PCの画面操作はMasterが行い、AIは手順案内と保存記録の照合を担当する。Holo Taskを選択するとChatGPT native surfaceをNirai内へ表示し、Loginも同じsurfaceで行う。

2026-10-01の手動確認で見つかったResume OFFの再送と回答取得失敗を修正した。同じ実行許可内で既存Turnへ割り当てた入力を、新規入力として再送しない。ChatGPTが新Conversation確定時にuser本文とProvider keyを消す場合は、実入力を確認済みのTurnだけ、生成終了・同じ会話・下書きなしを直前にも確認して一度だけ履歴を読み直し、復元された実user本文から回答を取得する。別会話・重複した目印・異なる入力・full navigation後の古い証拠は使わない。修正版の実機でMaster入力1件・Turn1件・返信「接続確認OK」1件、Resume OFF、正常保存とCompleted確定を照合した。一時診断ツールは削除済み。CLIの起動はAIが担当してよく、PCのクリック・入力・送信はMasterが行う。

`native composer入力 → HubへMaster原文保存 → ChatGPT送信 → Holo → Nirai-MCP Tool → native最終回答 → Hubへ同文保存 → Resume → CompleteTask`

確認対象は、native composer送信の一度だけのHub保存、最終assistant本文の完全一致、`CompleteTask`後の最終回答反映、`AwaitMasterReply`→native composer回答→次Turn、複数Turn継続、native Stop後の自動再開抑止、Retryでの旧Turn権限拒否、Conversation移動、25分 / Timeout / Session Error後のResume。
