# Nirai Holo Addon Gate 0 検証結果

**Status: IN PROGRESS（2026-08-31）**

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
- 新Dive開始時は旧Bindingを失効
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
- SnapshotにPrivate Whisper / Persona / Model / Avatarを出さない
- Local Client出力にSecretを出さない
- Local Holo接続から通常Resident管理Protocolを実行できない
- Event waitのsuccess / timeout / cancel
- Local Client切断時にCore waiterが残留しない
- Public Say / Resident公開返答はHolo Eventへ流れ、Private Whisper / Private返答は流れない

最新Core pytest: **113 passed**

## 7. 残る実機QA

1. 新ビルドでNiraiを再起動し、Local Bridge Descriptorが生成される
2. 新しい`Dive`から実ChatGPT Conversationで`attach → snapshot`を完走する
3. 同一ChatGPTターンで`say → wait → 追加snapshot → 最終Whisper`を完走する
4. ChatGPT側Cancel時にEvent waitが残留しない
5. Nirai再起動後、保存済みConversationが自動再表示される
6. 新Conversationの識別しやすいタイトル付与方法を確定する
7. Holo Skinを導入する場合の安全な縮退を確認する

Gate 0の接続方式について、Secure MCP Tunnel等の外部接続は現行受入条件から外す。
