# Holo Addon正式化 / Holo Whisper Surface製品化 Decision Brief

**状態:** implemented / checked（2026-08-31 Master承認・Windows実機QA完了）  
**最終更新:** 2026-08-31  
**目的:** Gate 0で成立したChatGPT Web・Dive・Local Bridge・安全縮退を再設計せず、Nirai本体から独立した正式Addon境界と、日常利用できるHolo Whisper Surfaceへ昇格する。

## 完成像

- MasterはDebugメニュー内の`Holo Surface`ボタンからHolo Whisper Surfaceを開く。
- 開いた直後から、保存済みCurrent DiveのChatGPT Conversationを可能な限りそのまま継続できる。
- 表示はNiraiのGlass Surfaceとしてまとまり、Gate 0やQA用語を製品面へ出さない。
- ChatGPT Web、Local Bridge、Skinのどれかが不調でも、その状態をHolo Addon内へ閉じ込め、通常Resident・Core・Worldを継続できる。
- `Dive`は常に「新しいChatGPT Conversationを現在Diveとして開始する」操作であり、単なる再接続・起床には使わない。

## Factと根拠

- Holoは通常Residentではなく専用Addonである。通常ResidentのBrain Driver、Whisper Memory、ライフサイクルへ混ぜない。
  - 根拠: `Docs/詳細設計/12_HoloAddonとChatGPTDive.md`
- ChatGPT ConversationがHolo Whisperの正本であり、Niraiは疑似チャットを再実装しない。
  - 根拠: `Docs/詳細設計/12_HoloAddonとChatGPTDive.md`
- Gate 0でpersistent login、保存済みConversation復元、新規Dive、Bootstrap準備、Local Bridgeの`attach → snapshot → say → wait`、権限制限、Skin fail-openが成立済み。
  - 根拠: `Docs/Holo_Gate0検証結果.md`
- Skin強制縮退、再読込、Nirai再起動後のConversation復元、通常Residentへの非影響はWindows実機で確認済み。
  - 根拠: `Docs/Holo_Gate0検証結果.md`
- 現在の入口はDebugサイドバー内の`Holo Surfaceを開く`ボタンである。
  - 根拠: `world/src/renderer/src/App.tsx`
- 現在のHolo Web HostはWeb View、状態保存、Dive、Skin、Securityを一つのClassで管理し、製品UIへは`visible / loaded / current dive / skin`だけを返している。
  - 根拠: `world/src/main/holo/HoloWebHost.ts`
- CoreはCurrent DiveのAttach WindowとBindingを保持できるが、World UIへそのBinding状態を返していない。
  - 根拠: `core/server.py`、`core/holo/auth.py`

## Assumption

- Holo Avatar実装前の暫定入口として、Debug内のHolo Surfaceボタンを維持する。通常画面へ新しい常設ランチャーは増やさない。
- 完成形の主要情報は「会話を使えるか」「現在Diveか」「Nirai連携が接続済みか」であり、正常時の技術詳細を常時並べる必要はない。
- ChatGPT WebのDOMは外部変更されるため、Skinの見た目より会話継続を優先する。

## Decision

### D-001 入口

- **内容:** 現段階の入口はDebugメニュー内の`Holo Surface`ボタンのみとする。
- **理由:** Masterの指定。将来のHolo Avatar Focus導線を先回りしない。
- **状態:** confirmed

### D-002 Addon Host境界

- **内容:** Main Process内に正式なHolo Addon状態を定義し、ChatGPT Web Hostをその配下の一能力として扱う。RendererはWebContentsViewを直接推測せず、Addon状態と命令だけをIPC経由で受け取る。
- **状態:** confirmed（2026-08-31 Master承認・実装済み）
- **状態分類:** `loading / ready / unavailable / error`
- **構成状態:** Current Dive、ChatGPT Web、Local Bridge、Skinを別々に保持する。
- **影響:** ChatGPT WebやSkinの異常をHolo Addon内で説明でき、通常Resident側へ波及させずに済む。

### D-003 Current Diveと再表示

- **内容:** Surfaceを閉じてもWeb ViewとCurrent Dive参照は破棄しない。再表示時は同じConversationを継続し、Nirai再起動時も保存済みConversation URLとDive IDを復元する。復元不能時は勝手に新規Diveを作らない。
- **状態:** confirmed（2026-08-31 Master承認・実装済み）

### D-004 Local Bridge状態

- **内容:** CoreのHolo Authorization状態を秘密値なしのallowlist状態としてWorldへ返す。`not_started / attach_waiting / attached / unavailable`だけをHolo Addonへ取り込み、Secret、Descriptor path、内部時刻等は返さない。
- **状態:** confirmed（2026-08-31 Master承認・実装済み）
- **表示:** 正常時は小さな「Nirai連携済み」。未接続やAttach待ちは必要なときだけ説明を出す。

### D-005 Holo Whisper Surfaceの製品UI

- **内容:** 製品面の名称を`Holo Whisper`へ統一し、`Gate 0`、`Skin: 判定待ち`、`Skin縮退QA`等の検証用語を除去する。
- **状態:** confirmed（2026-08-31 Master承認・実装済み）
- **操作:** `Dive`、`再読込`、`閉じる`を残す。`Dive`は主要操作、`再読込`は補助操作として視覚的な強さを分ける。
- **状態表示:** `準備中`、`会話を継続できます`、`新しいDiveを準備中`、`ChatGPTを表示できません`等、Masterが次の行動を判断できる文言だけを出す。

### D-006 Skin QAの隔離

- **内容:** 強制Skin縮退は製品Surfaceから外し、入口と同じDebugパネル内の診断操作へ移す。診断表示にはChatGPT Web、Current Dive、Local Bridge、Skinの内部状態を残す。
- **状態:** confirmed（2026-08-31 Master承認・実装済み）
- **影響:** 通常利用時にQA操作を誤って押さず、故障検証能力は維持できる。

### D-007 GlassとSkinの責務

- **内容:** Nirai GlassはNirai側の安定した外枠・余白・状態表示・操作領域を担う。Holo SkinはChatGPT側の安全に確認できた背景と周辺Chromeだけを馴染ませる付加機能とし、Composer、Conversation本文、認証、設定、送信操作には触れない。
- **状態:** confirmed（2026-08-31 Master承認・実装済み）
- **縮退:** probe、CSS適用、postflightのどこかが失敗したらCSSを全撤去し、通常ChatGPT Webへ即時縮退する。

### D-008 Responsive

- **内容:** WideではGlass内に1枚の大きなConversation、Portraitでは上下余白を圧縮、Narrowではヘッダーを2段化しChatGPT表示面積を優先する。
- **状態:** confirmed（2026-08-31 Master承認・実装済み）
- **禁止:** 狭い画面で操作ボタンがConversationを覆う、Composerが画面外へ出る、固定幅で横スクロールする状態を許容しない。

### D-009 状態の正直さ

- **内容:** 外部から観測できないHoloの「思考中・待機中・発話中」は表示しない。観測可能なWeb読込、Dive準備、Core/Local Bridge、Skinだけを状態化する。
- **状態:** confirmed（2026-08-31 Master承認・実装済み）

### D-010 セキュリティ境界

- **内容:** persistent partition、sandbox、contextIsolation、nodeIntegration無効、Navigation/Popup制限、Remote Permission deny-by-default、allowlist Local Bridgeを維持する。Holo Local ClientへApproval/Decision操作を追加しない。
- **状態:** confirmed（2026-08-31 Master承認・実装済み）

### D-011 内部のGate 0命名

- **内容:** 製品表示と公開型は正式なHolo Addon名へ変える。既存未コミット差分を壊すファイル移動は行わず、Gate 0由来のファイル名は今回は履歴的な内部名として残してよい。
- **状態:** confirmed（2026-08-31 Master承認・実装済み）
- **影響:** 製品面からGate 0を除去しつつ、現在の差分を安全に保持できる。

## 画面構成案

```text
┌────────────────────────────────────────────────────────────┐
│ Holo Whisper       ● 会話を継続できます    [Dive] [↻] [閉じる] │
│                     Nirai連携済み                           │
├────────────────────────────────────────────────────────────┤
│                                                            │
│             実際のChatGPT Conversation                     │
│             （ConversationとComposerが主役）                │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

- Wide: 1行ヘッダー。Conversationを最大化する。
- Portrait: タイトル・状態と操作を上下2段にする。
- Narrow: `Dive`と`閉じる`を優先し、`再読込`は小さな補助操作にする。
- エラー時: Conversation領域を消して独自チャットを出さず、同じSurface内に理由と`再読込`を表示する。

## Scope

- Holo Addon Hostの正式な状態・ライフサイクル・IPC契約
- Current Dive、ChatGPT Web、Local Bridge、Skinの状態統合
- Holo Whisper Surfaceの製品文言、Glass、Responsive
- Skinの安全な見た目調整とfail-open
- QA専用Skin縮退操作のDebugへの移動
- Unit / Core / typecheck / build / Windows実機QA
- Gate 0完了後の設計書・検証結果・AI_ENTRY整合

## Non-goals

- Holo Avatar、Focus導線、Animation、VOICE、Sleep演出
- 通常Resident化、通常Whisper Memoryへの複製
- Holoの思考・発話・待機状態の推測表示
- ChatGPT履歴タイトルのDOM操作
- Holo Local ClientへのApproval/Decision追加
- M4 Agent RuntimeやProvider機能の先回り実装
- 外部公開Bridge、OAuth、Remote接続
- 常設Holoランチャーの追加

## 条件分岐と回復

- 保存済みConversationあり: Surface初回表示で復元する。
- 保存済みConversationなし: ChatGPTホームを表示し、勝手にDiveを作らない。
- ChatGPT未ログイン: 通常ChatGPTログイン画面を表示し、Nirai側へ認証情報を取り込まない。
- Web読込失敗: `unavailable`または`error`を表示し、再読込できる。通常Residentは継続する。
- Skin不成立: 通常ChatGPT Webへ縮退し、Conversation/Dive/Bridge状態は維持する。
- Local Bridge未Attach: Whisper Conversationは利用可能なまま、Nirai連携だけ未接続と表示する。
- Core切断: Holo Webは利用可能なまま、Nirai連携を`unavailable`にする。
- Surface close/reopen: 同じWeb ViewとConversationを再表示する。
- Nirai restart: 保存状態を読んで同じConversationを復元する。復元不能なら新規Diveを自動生成しない。

## 許容されること / 許容されないこと

- 許容される
  - Holo関連の型、状態、IPC、React Component、CSSを正式名へ整理する。
  - Coreの既存Holo Authorizationから秘密を含まない状態だけをWorldへ通知する。
  - 安全probeに合格した範囲でChatGPT背景・周辺ChromeをSkin調整する。
  - QA専用操作をDebugへ移す。
- 許容されない
  - 現在のGate 0能力を別方式で作り直す。
  - 既存の未コミット差分を巻き戻す。
  - 通常Resident/Core/Worldの成立条件へHoloを追加する。
  - ChatGPT Conversation/ComposerをNirai製UIに置換する。
  - DOM変化時に壊れたSkinを残したまま使う。
  - Holo Local ClientからApproval/Decisionを送る。
  - commit、push、reset、restoreを行う。

## Risk

### R-001 WebContentsViewとReact Surfaceの座標ずれ

- **影響:** Resizeや縦長表示でConversationが枠からはみ出す。
- **対策:** ResizeObserverに加え、open/resize/layout変化時の境界更新と代表viewportの実機確認を行う。

### R-002 ChatGPT DOM変更でSkinが壊れる

- **影響:** ConversationやComposerが見えない、入力不能になる。
- **対策:** 適用前probe、限定CSS、適用後probe、即時全撤去を維持し、強制縮退QAをDebugから実行する。

### R-003 Local Bridgeの詳細を出しすぎる

- **影響:** Secretやローカル構成がRenderer/画面/ログへ漏れる。
- **対策:** enum状態と一致確認に必要なDive IDの内部比較だけに限定し、製品UIには技術値を表示しない。

### R-004 Holo異常が通常機能へ波及する

- **影響:** Resident会話、World、Core起動が使えなくなる。
- **対策:** Addon状態を独立させ、初期化・読込・保存・Skin失敗をHolo内で捕捉し、通常起動の必須条件にしない。

## CHECK

### 自動確認

- Holo lifecycle/state reducerまたは状態変換のUnit test
- 保存済みDive復元、close/reopen、reload、Skin applied/fallback、Web errorのUnit test
- Navigation、Popup、Permission、External URL制限の既存test維持
- Core Holo状態のallowlist、復元、Attach待ち、Attached、Core再起動のtest
- World全Unit test、Core全test、typecheck、build
- `git diff --check`と未コミット差分保持確認

### Windows実機QA

- Debug → Holo Surfaceから開く。
- 起動直後から保存済みConversationが表示され、Composerへ入力できる。
- Surfaceを閉じて再度Debugから開いても同じConversationを継続できる。
- `Dive`で新ConversationとBootstrapが準備され、最初の送信はMaster操作のまま。
- Local Bridge状態がAttach前後で正しく変わり、秘密値は画面へ出ない。
- Debug側のSkin強制縮退後もConversation表示・入力が正常。
- 再読込後もConversationが正常で、Skinは適用または通常表示へ確定する。
- Wide / Portrait / NarrowでConversation、Composer、主要操作が欠けない。
- Skin切替でNirai World背景や通常Residentの見た目が変化しない。
- Holo Webを利用不能にした場合も通常Resident会話、Core、Worldが継続する。
- Niraiを自分で終了・再起動し、保存済みConversationと状態復元を確認する。

## 完了条件

- D-001〜D-011がMaster承認済み。
- Gate 0製品文言とQA操作がHolo Whisper Surfaceから除去されている。
- Addon状態がCurrent Dive / ChatGPT Web / Local Bridge / Skinを明示する。
- ChatGPT WebまたはSkin異常時も通常Niraiへ影響しない。
- 自動確認とWindows実機QAの証拠を残す。
- 既存未コミット差分が保持され、commit/push/reset/restoreが行われていない。

## Open Questions

- OQ-001: resolved。2026-08-31にMasterがD-002〜D-011と画面構成案を承認した。

## 実装結果

- `HoloAddonHost`がChatGPT Web lifecycle、Current Dive、Skin、保存・復元・fail-openを管理する。
- Coreは秘密を含まない`not_started / attach_waiting / attached`だけをWorldへ通知し、WorldはCore切断中だけ`unavailable`として表示する。
- 製品Surfaceを`Holo Whisper`へ統一し、Gate 0文言とSkin QAを除去した。
- Skin QAはDebugへ移動し、Surfaceを閉じた状態でも現在Conversationを壊さず強制縮退できる。
- 状態表示はWeb読込イベント、保存済みDive、Core Bridge状態、Skin適用結果だけを使用する。ChatGPTの思考・待機・発話は推測しない。
- SurfaceのGlassはチャットログと同じ濃さ仕様（非アクティブ0.46 / 押下0.96）で、ChatGPT背景はSkinが`main`配下だけ透過してNirai Worldを見せる。`nav`は半透明で残す。
- Security、Approval / Decision禁止、Local Bridge allowlist、通常Resident非依存を維持した。

## CHECK結果

- Core: `114 passed`
- World: `32 files / 156 tests passed`
- TypeScript typecheck: 成功
- Production Build: 成功
- Windows実機:
  - Production Build後、公式`Start Nirai.vbs`で正常終了・再起動を反復
  - Debug → Holo Surfaceから保存済みConversation、Composer、`会話を継続できます`、`Nirai連携済み`を確認
  - Composerへ未送信QA文字列を入力・消去
  - Surface close / reopenで同じConversationを保持
  - DebugのSkin強制縮退中も同じConversationとComposerを確認し、再読込後に`applied`へ復帰
  - Wide 1280×720、Portrait 720×900、Narrow 520×720を確認
  - Portrait本文左端欠け、Debug QAがnative Viewに覆われる問題を原因修正後に再確認
  - Surfaceを閉じた通常Worldで3体のResidentとCore接続を確認

## 2026-09-01 レビュー修正

製品化差分の最終レビュー（NEEDS FIX判定）を受け、以下を修正した。

- Dive準備が失敗した場合は、直前のConversation URLとDive Session IDへ戻し、保存状態を上書きしない。新しいDiveの保存はBootstrap成功後だけ行い、失敗時は元のConversationへ表示を戻す（D-003の失敗経路）。
- Dive準備は進行中の初回復元loadと競合しないよう、復元完了を待ってから開始する。
- Dive準備中に旧Conversationへ戻るNavigationを、新しいDiveのConversationとして誤保存しない。
- `state.json`への書き込みを直列化し、並行書き込みでファイルが壊れる可能性を除去した。
- NarrowとPortraitで`再読込`を小さな補助操作幅へ戻し、`Dive`と`閉じる`を優先した（D-008）。
- `HoloAddonHost`のlifecycle unit test（保存済み復元、close/reopen、Dive失敗時の状態保持、新Dive persist順序、Web load失敗、Webフォーカス通知）を追加した。

さらにMasterの実機確認（黒背景が残る・下部の黒帯・透過が濃い・Sidebarが消えている）を受け、Glass外観を次のとおり確定した。

- SurfaceのGlassはチャットログと同じ仕様に統一した。非アクティブ時は`--glass-fill`の`opacity 0.46`相当、ChatGPT Conversationを押下（=native Viewフォーカス）すると`0.96`相当へ濃くなり、フォーカスが外れると戻る。フォーカスはMain Processが観測した事実だけを`holo:web-focus-changed`でRendererへ通知する。
- Skinは`nav`を隠さず、半透明のNiraiトーンで残す。Wide用Chrome幅補正は廃止した。
- ChatGPT側が黒く塗る背景（`bg-token-main-surface-primary`系、Composer上の`content-fade`グラデーション）を`main`配下に限定して透過し、Portal表示のメニュー・ダイアログの背景は変更しない。
- Skin fail-open経路（probe / postflight / 全撤去縮退）は変更していない。

再検証: Core `114 passed`、World `33 files / 162 tests passed`、TypeScript typecheck成功、Production Build成功、`git diff --check`成功。Glassの濃さ・押下時の変化・Sidebar表示のWindows実機確認はMasterの次回起動時に行う。

## 既知の外部境界

- ChatGPTのDOM/CSSは外部仕様である。Skinの背景透過は`bg-token-*`系class名に依存するため、ChatGPT更新で黒背景が再発しうるが、その場合もConversationとComposerは通常表示のまま利用できる。ConversationとComposerを壊す広範なDOM操作は採用せず、安全probeと通常ChatGPT fallbackを優先する。
- Holo Avatar、Focus、VOICE、AnimationはNon-goalのまま未実装。
