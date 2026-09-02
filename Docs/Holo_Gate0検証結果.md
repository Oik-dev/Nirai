# Nirai Holo Addon Gate 0 検証結果

**Status: COMPLETE（2026-08-31）**

本書は`Docs/詳細設計/12_HoloAddonとChatGPTDive.md`のGate 0実証記録である。

## 1. ChatGPT Web Host

成立済み：

- Electron `WebContentsView`内でChatGPT Webを表示
- Holo専用persistent Sessionでログイン状態をNirai再起動後も保持
- `Dive`で新規Conversationへ移動
- BootstrapをComposerへ準備し、最初の送信はMasterが直接行う
- 送信後のConversation URLとNirai内部Dive IDを`runtime/holo/state.json`へ保存
- Dive中にNiraiを閉じても`Object has been destroyed`を出さず終了
- Remote Permissionはdeny-by-default
- Clipboard Readは拒否。`clipboard-sanitized-write`はChatGPT本体WebContentsでMasterの左クリック完了を観測した直後750ms・一回利用だけ許可し、Origin一致だけでは許可しない
- Display Media / Device Permission拒否
- Top-level Navigationは既知ChatGPT / OpenAI Auth / Google・Microsoft・Apple Login Originに限定
- Popupも既知Auth Origin以外はElectron Windowを生成しない

World側の回帰はVitest / TypeScript typecheck / Production Buildで検証する。

## 2. Holo意味API

Core側に、Holoへ公開する意味情報と意味操作を通常Resident処理から分離して実装した。

現在の操作：

- `attach`
- `snapshot`
- `say`
- `wait`

`Approval / Decision`操作は存在しない。

### Snapshot境界

返すもの：

- World接続状態
- 時間帯
- Active Session
- Residentの名前と公開位置
- 直近の公開会話
- 最新Event ID

返さないもの：

- API Key / Token / Secret / Cookie / Environment
- ResidentのPrivate Whisper / Private Memory
- Brain / Model / Avatar / Persona等の内部設定
- 任意Fileや生Log

### Event Queue

- Buffer上限256件
- 1回のwait上限15秒
- 公開Eventだけを流す
- Private Whisperは流さない
- timeout / cancel /接続切断後にwaiterを残さない

### Holo World Say

- 通常Residentへ偽装せず`kind=holo_say` / `from=Holo`として保存
- World Chatと公開Eventへ流す
- 任意Resident宛て指定可
- 無効なResident名は拒否

## 3. 接続方式のDecision変更

当初は、Holo専用MCP Serverを別Processとして起動し、ChatGPTからSecure MCP Tunnel等を介して接続する案をGate 0で試した。

その後Masterと用途を再確認し、**Holo AddonはこのPCだけで使う機能**と確定した。

この条件では、外部MCP Server / HTTP Transport / MCP SDK / Bearer / OAuth / Remote Identity / Scope管理を維持する価値より、実装複雑性の方が大きい。

そのため現行Decisionは次とする。

```text
ChatGPT Dive
  ↓ 既存Local MCP / run_process
Nirai同梱 Holo Local Client
  ↓ authenticated localhost WebSocket
Nirai Core
  ↓
Snapshot / World Say / Event Queue
```

旧`holo-mcp/`方式は製品経路から退役する。将来このPC外からHoloへ接続する要件が生じた場合は、現行Local BridgeへRemote認証を継ぎ足さず、外部接続Gateとして別設計する。

## 4. Holo Local Bridge安全境界

- Core起動ごとにLocal Bridge Secretを新規生成
- 接続Descriptorは`%LOCALAPPDATA%\Nirai\holo-local-bridge.json`へ保存
- Descriptorは`D:\Products`外に置くため、通常のLocal MCP File ToolのAllowed Rootから直接読めない
- ChatGPTはDescriptor / Secretを直接扱わず、固定`D:\Products\Nirai\tools\holo-local-client.mjs`だけをLocal MCP `run_process`で実行する
- Local Clientはlocalhost WebSocketだけを受理
- Coreは`role=holo_local`＋Secret一致時だけHolo操作を受理
- 誤SecretはWebSocket close code 4003で拒否
- SecretをCore Log / World Protocol / Tool結果 / Binding / Conversationへ返さない
- Local Clientから実行できるCore操作はHolo意味操作だけ。通常Resident管理等の任意Protocol Messageは拒否

## 5. Dive Binding

- MasterがNirai UIで`Dive`を直接押した時だけ新しいAttach Windowを開く
- Attach Windowは手動送信時間を考慮して5分、一回利用
- 5分期限はMasterが`Dive`を押した時刻からの絶対期限であり、Core切断・ACK消失・再接続による`holo_dive_started`再送でも延長しない。Coreは受信時の残り時間だけをWindowへ反映し、期限切れは拒否する
- 同じDive IDの再通知はidempotentに扱い、既にpendingなら期限を巻き直さず、既にattachedならBindingを失効させない
- 新Dive開始時は旧Bindingを失効
- AttachはWindow検証→`binding.json`永続化→in-memory確定の順で行い、永続化成功後だけ`attached`へ遷移する。write / replace失敗時はLocal Clientへ構造化エラーを返し、Worldは`attach_waiting`のまま、one-shot Windowも元の絶対期限のまま再試行可能とする
- Attach成功後は`dive_session_id`と`attached_at`だけを`runtime/holo/binding.json`へ保存
- Secret / Token / Identity / ScopeはBindingへ保存しない
- Core再起動時、現在のDive IDとBinding IDが一致する場合だけBindingを復元
- Core再起動後のLocal Client認証には新しく生成されたSecretを使用

## 6. 自動検証

2026-08-31時点のLocal Bridge E2Eで以下を確認済み。

```text
Master-started attach window
  ↓
Local Client attach
  ↓
Local Client snapshot
  ↓
Local Client say
  ↓
Local Client wait → Say Event取得
```

追加確認：

- 誤Local Secretを4003で拒否
- 一回利用Attach
- 別Dive Bindingを復元しない
- Core再起動後のBinding復元
- BindingファイルにSecretを保存しない
- Binding write / replace失敗でattach成功を誤報せず、WebSocketを切断せず構造化失敗を返し、World状態を`attach_waiting`へ維持する
- Binding保存失敗後のretryでもMaster操作基準の絶対期限を延長しない
- SnapshotにPrivate Whisper / Persona / Model / Avatarを出さない
- Local Client出力にSecretを出さない
- Local Holo接続から通常Resident管理Protocolを実行できない
- Event waitのsuccess / timeout / cancel
- Local Client切断時にCore waiterが残留しない
- Public Say / Resident公開返答はHolo Eventへ流れ、Private Whisper / Private返答は流れない

最新Core pytest: **127 passed**

## 7. 実機QA結果

2026-08-31に以下を確認した。

1. Nirai再起動後、Local Bridge経由で`attach → snapshot`成功
2. 実ChatGPT DiveからHolo World Sayを実行し、直後のsnapshotで同じ公開発言を確認
3. 同一ChatGPTターン内で`say → wait → 追加snapshot → 最終Whisper`を完走
   - Holo意味操作のTool Call: 4回（`say` / `snapshot` / `wait` / `snapshot`）
   - `wait` timeout設定: 5秒
   - 対象Eventは既にQueueへ到着済みだったため待機は即時復帰
   - `timed_out=false`でEvent ID 2を取得
4. Cancel / Process cleanupは自動テストで確認
   - `test_holo_local_disconnect_cancels_event_wait`: Local Client切断後に`active_waiters == 0`
   - `test_holo_event_wait_success_timeout_and_cancel_release_waiters`: cancel後に`active_waiters == 0`、遅延Eventでも旧waiterは復帰しない
   - 5秒waitを作成した状態から、テスト上100ms以内の確認窓でwaiter消失を確認
   - 実ChatGPT UIのCancelがLocal MCP配下の子Processへどう伝播したかは外部実装境界で直接観測できないため、Gate 0の必須受入条件にはしない
   - 製品側の必須保証は「Local Client接続が切れた場合にCore waiterを残さないこと」と「waitが最大15秒のbounded timeoutで終了すること」とする
5. Nirai再起動後、保存済みChatGPT Conversationが同じHolo Whisper Surfaceへ自動再表示され、同一Conversationを継続利用できた
6. Core pytest: **114 passed**
7. Holo Web / Chat History関連Vitest: **18 passed**（`HoloWeb.test.ts` 8件、`HoloWhisperSurface.test.ts` 2件、`ChatHistoryView.test.ts` 6件、`SessionStore.test.ts` 2件）

## 8. Holo Skin安全縮退Gate

Gate 0用のSkin安全層を実装した。ここでは完成UIを作り込まず、ChatGPT WebへCSSを安全に追加・撤去できることだけを確認対象とする。

- SkinはElectron `webContents.insertCSS`で追加し、ChatGPT DOMそのものを書き換えない
- CSSは`html[data-nirai-holo-skin="product"]`配下だけへ作用する
- 適用前にHTTPS `chatgpt.com`、`document.body`、`main`、`nav`、Composer存在をprobeする
- probe不成立ならCSSを適用せず`skin_mode=fallback`へ移る
- 適用後にも同じprobeを行い、異常なら挿入CSSとSkin markerを撤去して通常ChatGPT表示へ戻す
- navigation / reload時は前のSkin CSSを破棄してから再判定する
- 製品化後の`Skin縮退QA`はHolo Whisperから外し、Debugメニューへ隔離した。Surfaceを閉じた状態でも現在Conversation URL / Dive Session IDへ触れずに`fallback`へ落とせる
- `fallback`後は再読込で通常のSkin判定へ戻る
- Skin失敗はConversation、Local Bridge、通常Residentへ伝播させないfail-openとする

自動検証：

- Holo Web helper: **8 tests passed**
- Holo Whisper状態表示: **2 tests passed**
- World全体: **32 files / 156 tests passed**
- TypeScript typecheck: 成功
- Production Build: 成功

### 実機QA完了

2026-08-31、Windows上でNiraiを正常終了・再ビルド・通常起動し、Masterの操作を介さず以下を確認した。

1. 初回QAでは保存済みConversation本文と入力欄は復元できたが、12秒以上経っても`Skin: 判定待ち`のままになる不具合を再現した
2. 保存済みConversation復元時の同一Document内Navigationまで通常Navigationとして扱い、進行中のSkin判定を取り消していたことを原因と特定した
3. 同一Document内NavigationではSkin判定を取り消さないよう修正し、main frameの通常Navigationだけをreset対象にする回帰テストを追加した
4. 修正後の再起動QAでは、Holo Surfaceは直後からフルサイズ枠で開き、2秒時点で`Skin: Gate 0適用中`へ確定、5秒時点で保存済みConversation本文とComposerを表示した
5. `Skin縮退QA`後は`Skin: 通常ChatGPTへ縮退中`へ確定し、同じConversation本文、黒いChatGPT背景、Composerを維持した。未送信QA文字列を入力・確認し、その後QA文字列だけを消去できた
6. 再読込後は5秒時点で`Skin: Gate 0適用中`へ復帰し、保存済みConversation本文と有効なComposerを再確認した
7. Gate 0時点では、Skin適用中・縮退中でChatGPTの見た目と背景に変化はなかった。Gate 0 CSSはprobe用custom property以外の見た目を変更していなかった
8. Holo Surfaceを閉じた後も3体の通常Residentを表示し、通常Niraiのメッセージ入力へ未送信QA文字列を入力・消去できた。Core接続と通常Nirai機能への影響はなかった
9. Gate 0修正後の検証はWorld **31 files / 152 tests passed**、Core **113 passed**、TypeScript typecheck成功、Production Build成功

## 9. Gate 0後の正式Addon化 / Holo Whisper製品化

2026-08-31、承認済み`Docs/plans/2026-08-31-holo-addon-productization-brief.md`を正として、Gate 0を再実装せず正式Addon境界と製品UIへ昇格した。

- Main Processの正式境界を`HoloAddonHost`とし、`loading / ready / unavailable / error`をChatGPT Webの実イベントだけから確定する
- Current Diveは`none / preparing / current`、Skinは`checking / applied / fallback`として保持する
- Coreは秘密を含まないLocal Bridge状態`not_started / attach_waiting / attached`だけをWorldへ通知する。Core切断中の`unavailable`はWorldの実接続状態から表示する
- `thinking / waiting / speaking`等、ChatGPT側から観測できない状態は実装しない
- 製品表示を`Holo Whisper`へ統一し、Gate 0文言とSkin QA操作を製品Surfaceから除去する
- SkinはChatGPT側の暗い背景を`main`配下だけ透過してNiraiの青いGlassを見せる。`nav`は隠さず半透明で残し、Wide用のChrome幅補正は行わない（2026-09-01のMaster実機確認で確定）
- Skinのpreflight / postflight失敗時は挿入CSSとmarkerを全撤去して通常ChatGPT Webへ戻す
- Surfaceを閉じてもWeb View、Conversation、Current Diveを保持する
- Current Diveの`state.json`保存失敗はWeb lifecycleと別のsticky状態として保持し、後続の永続化成功時だけ解除する。Holo WhisperとDebugに再起動時の復元リスクを明示し、ChatGPT Webの読込成功では警告を消さない

Windows実機QA：

1. Niraiを正常終了し、Production Build後に公式`Start Nirai.vbs`から複数回再起動した
2. Debugメニューの`Holo Surfaceを開く`から、保存済みConversationをフルサイズ表示し、`会話を継続できます` / `Nirai連携済み`を確認した
3. Composerへ未送信`Nirai-Holo-QA`を入力して表示を確認し、送信せず消去した
4. Surfaceを閉じ、Debugの`Skin縮退QA`を実行後に再表示し、同じConversation・通常ChatGPT背景・Composerを確認した。縮退中も未送信文字列を入力・消去できた
5. `再読込`後に同じConversationとComposerを確認し、Debug状態が`Web: ready / Dive: current / Bridge: attached / Skin: applied`へ戻った
6. 1280×720 Wide、720×900 Portrait、520×720 Narrowで、主要操作、Conversation、Composerを確認した。Portraitで本文左端が欠ける不具合を修正後に再確認した
7. Holo Surfaceを閉じた後も3体の通常Resident、World、通常入力欄、Core接続を確認した
8. 最終自動検証はWorld **32 files / 156 tests passed**、Core **114 passed**、TypeScript typecheck成功、Production Build成功

### Conversationタイトル方式

ChatGPT履歴タイトルの自動設定はGate 0で不成立と判定した。

- ChatGPT側が自動生成するConversationタイトルをNiraiの製品仕様として制御しない
- ChatGPT WebのRename UIをDOM操作する方式は、実機で再起動を挟んで複数回試したが安定して成立しなかったため採用しない
- 壊れやすいDOM自動操作を製品経路へ残さない
- Diveの識別はBootstrap先頭行の`[YYYY-MM-DD Nirai Dive]`を正規の識別子とする
- 現在Diveの復元には従来どおり保存済みConversation URLとDive Session IDだけを使用する
- 将来、ChatGPT側に安定した公式タイトル指定手段が提供された場合のみ再検討する

Gate 0の接続方式について、Secure MCP Tunnel等の外部接続は現行受入条件から外す。
