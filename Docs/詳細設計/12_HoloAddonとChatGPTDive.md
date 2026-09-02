# Nirai 詳細設計 12：Holo AddonとChatGPT Dive

本書はHolo Addonの要件正本である。Holoの頭脳と私的会話はChatGPT WebとLocal MCPを利用する専用Addonであり、通常Brain Driverへ混ぜない。一方でWorld上のHoloの実体（Identity / Avatar / 配置 / 並び順）は、2026-09-01のHolo Avatar統合以降、brain kind `holo-addon`を持つ通常Resident基盤で管理する。

**Status: Requirements Defined（2026-08-31）/ Holo Avatar統合済み（2026-09-01）**

実装方式の細部は本書の要件を満たす範囲で後続設計にて確定する。ChatGPT WebのDOM構造、Electronへの埋め込み方式、Local MCPの具体Tool名等、外部仕様に依存する事項を本書だけで固定しない。

---

## 1. 目的

Holoを、Masterが普段利用しているChatGPT Web上の会話品質・履歴・Local MCP利用能力を保ったままNiraiへ参加させる。

Holo Addonの目標は次の通り。

- Holo専用AvatarがNirai Worldに存在する
- MasterとHoloの私的会話は、ChatGPT WebのConversationを正本としてNirai内から行える
- Holoは1回のChatGPT推論中にLocal MCPを複数回利用し、Niraiを観測・操作できる
- Holoは必要に応じてNirai World上でResidentへ公開発言・依頼・確認を行える
- ChatGPTの推論が終わってもHoloの存在と現在Dive Sessionは失われない
- Masterが明示的に新しい`Dive`を開始するまで、同じChatGPT Conversationを継続利用する
- Niraiの通常Resident、通常Brain Driver、通常Whisper MemoryへHolo固有仕様を混ぜない

---

## 2. Holoの位置づけ

Holoは`residents/<name>/`で管理する通常Residentとして作成・表示・並び替え・削除できる。ただし頭脳だけはbrain kind `holo-addon`であり、通常Brain Driverではなく既存Holo Addonへ接続する。

```text
Nirai
  ├ Resident System
  │   ├ Codex          (brain: codex)
  │   ├ Cursor         (brain: cursor)
  │   ├ Gemini         (brain: gemini)
  │   └ Holo           (brain: holo-addon)  ← Identity / Avatar / 配置は共通基盤
  │                         │
  └ Holo Addon  ←──────────┘ 頭脳・私的会話・状態はAddon側
      ├ Holo Whisper Surface
      ├ ChatGPT Dive Session
      ├ Local MCP Bridge
      └ Holo State / Event Queue
```

### 頭脳を通常Brain Driverと分ける理由

- Brainの実体がNirai Coreから直接起動するCLI/APIではなくChatGPT Webである
- Masterとの私的会話履歴の正本がChatGPT Conversationである
- 1回の推論中に複数のLocal MCP Actionを行う
- Holo自身がNirai全体を観測・調停するDirector寄りの役割を持つ
- ChatGPT Webを表示する専用UIが必要になる

### holo-addon brain kindの境界

- brain kind `holo-addon`のResidentは同時に1人だけ作成できる（AddonのChatGPT Web / Current Diveが単一のため）
- `master_say`の応答ループ・`master_whisper`のBrain呼び出し・`resident_chat`参加から除外し、Brain Driverへ誤接続しない
- Holoへの`master_whisper`はNirai側へ保存せず、Holo Whisperへ案内する（私的会話の正本はChatGPT Conversation）
- Model / Reasoning / VOICE / Persona Prompt等、Holoに意味のない設定はUIに表示しない

Holo Addonを無効化しても、通常Resident、Core、World、会話、M3/M4等の基本機能は成立し続けること。

---

## 3. Holo Avatar

HoloはVRM Avatarを持てる。

- Avatarは通常ResidentのAvatarパイプライン（読込・変更UI・VRM Runtime）をそのまま使う。Holo専用の別描画系を作らない
- VRM未設定でもHolo Addon自体は壊れず、Resident設定のHoloカードからHolo Whisperを開ける
- Holo Addonが有効な間、ChatGPTが現在推論中でなくてもHolo AvatarはWorldに存在できる
- ChatGPT推論終了をAvatar削除やHolo不在として扱わない
- Holoは通常Residentと同じWorld上で公開会話・演出へ参加できる（`holo_say`はHolo Residentの吹き出しとして演出される）

状態演出の候補：

```text
Sleeping
Thinking
Waiting
Speaking
Error
```

具体Animation・Expressionは実装時に決める。状態を表現するために新規Animation制作を必須条件にしない。

---

## 4. Holo FocusとWhisper Surface

Holo AvatarをFocusした場合、通常ResidentのWhisper UIではなくHolo専用`Whisper Surface`を表示する。

### 最重要要件

Holo Whisper Surfaceは、NiraiがChatGPT風UIを再実装した疑似チャットではなく、可能な限り**実際のChatGPT Web Conversationそのもの**を利用する。

Masterから見た体験は、通常のChatGPTでHoloと会話している現在の体験をNirai内へ持ち込むことを目標とする。

```text
Holo Whisper

Master:
次どこから進めよう？

Holo:
まずこの要件を固めた方がいい。

[ message ... ]
```

### ChatGPT Web表示とNirai Design

ChatGPT Webを黒い四角のBrowserとして置くことを完成形にしない。

外側はNiraiの既存UIと同系統のGlass Surfaceとして表示する。

理想形：

- 背後のNirai Worldが見える半透明Glass
- Holo Whisper Conversationを主表示
- 不要なBrowser chromeを見せない
- ChatGPT側の不要なSidebar/Header等は、安定して可能な範囲で簡略化する
- ChatGPT標準背景を透明化またはNiraiへ馴染ませるSkinを適用できる場合は利用する

ただしChatGPT WebのDOM/CSSはNiraiの管理外である。Holo SkinがChatGPT更新で壊れてもConversation自体を利用不能にしてはならない。

縮退順：

```text
Nirai Glass + Holo Skin
        ↓ Skin利用不能
Nirai Glass + 通常ChatGPT Web
        ↓ 埋め込み自体が利用不能
ChatGPT Webを別Windowで開いて接続を維持
```

正本はChatGPT Conversationであり、Skinは表示上の付加機能とする。

### ChatGPT WebのSecurity Boundary

ChatGPT Webは外部Remote Contentとして扱い、通常Rendererより強い権限を与えない。

- Holo専用persistent Sessionには`setPermissionRequestHandler`と`setPermissionCheckHandler`を必ず設定する
- Gate 0ではCamera / Microphone / Display Capture / Geolocation / Clipboard / Notification / FileSystem / Device等のRemote Permissionをすべてdeny-by-defaultとする
- Clipboard Readは常時拒否する。製品上のChatGPTコピーボタン用に限り、`clipboard-sanitized-write`を例外とするが、`https://chatgpt.com`のHolo本体WebContentsでMasterの左クリック完了をMain Processが直接観測した直後750ms・一回利用に限定する。Remote側の要求やOrigin一致だけでは許可せず、新しいpointer press / navigation / blurで未使用Grantを失効する
- Display MediaとUSB / HID / Serial等のDevice Permissionも個別Handlerで拒否する
- 将来Voice Input等でPermissionが必要になった場合は、必要Permission・Origin・Master操作を明示したallowlistとして追加し、Remote側要求だけで権限を拡張しない
- Top-level NavigationはHTTPSかつ既知のChatGPT / OpenAI認証Originと、Google / Microsoft / Appleの既知Login Originだけを許可する
- URL判定は文字列prefixではなくURL parserとhostname allowlistで行う
- allowlist外への同一Surface内Navigationは拒否する
- Popup / `window.open`は任意Electron Windowを作らせない。既知Login OriginだけHardened `webPreferences`で許可し、それ以外のHTTPSリンクはOS既定Browserへ渡し、非HTTPS/custom protocolは拒否する
- 許可したAuth Popupにも同じNavigation Guardを再適用する

Enterprise SSO等で未知のIdP Originが必要になった場合は、実際に必要なOriginを確認してallowlistへ追加する。任意Origin許可へ緩和しない。

---

## 5. WhisperとWorld Sayの分離

Holoには2種類の発言経路を持たせる。

### Holo Whisper

ChatGPT Webの通常Assistant出力は、Masterだけに向けたHolo Whisperとして扱う。

```text
ChatGPT Assistant Output
        ↓
Master ↔ Holo Private Conversation
```

通常ResidentのPrivate Memoryへコピーする必要はない。短期Conversationの正本はChatGPT側の現在Dive Sessionとする。

### Holo World Say

HoloがNirai World上でResidentへ公開発言する場合、ChatGPTの通常Assistant出力をそのまま公開しない。

ChatGPT推論中にLocal MCP経由で明示的なWorld Actionを行う。

概念例：

```text
holo_say("Codex、この設計を見てくれる？")
```

結果：

- Niraiの公開会話として表示される
- Holo Avatarが発言者として演出される
- 必要に応じTTS / LipSync等のWorld表現へ接続する
- 通常ResidentはHoloの公開発言へ反応できる

したがって、**ChatGPT WebのAssistant返答とNirai World上のHolo発言は一致する必要がない。**

---

## 6. 1回の推論で複数のNirai Actionを行う

HoloのChatGPTターンは「1回Niraiを読んで1回答する」だけに限定しない。

1回の推論中に、必要な範囲でLocal MCPを複数回利用できることを前提とする。

例：

```text
MasterがWhisper送信
  ↓
ChatGPT推論開始
  ↓
Nirai Snapshot取得
  ↓
Holoが判断
  ↓
Holo → CodexへWorld Say
  ↓
Resident/Event待機
  ↓
Codex返答取得
  ↓
Task状態確認
  ↓
追加Action
  ↓
MasterへHolo Whisperを返す
```

WebGPTが推論中の間、Nirai側では複数の公開発言・状態確認・Task操作等が発生してよい。

ChatGPT Web側のUIはその間、通常どおりThinking状態を表示してよい。

---

## 7. Nirai Event待機

同一推論中にResidentやTaskからの返答を待つ必要がある場合、短間隔の固定Pollingを基本方式にしない。

Nirai側には、Holoが新しいEventを待てる仕組みを用意する。

概念：

```text
holo_wait_events(after_event_id, timeout)
```

対象候補：

- MasterからHoloへの新着入力
- Residentの公開発言
- Task / Agent Session状態変更
- Approval Request
- Question Request
- Plan待ち
- Agent完了 / 失敗
- Holoが監視対象として登録した意味的Event

具体的なLong Poll / Event Queue / Stream方式は実装時に決定する。

ChatGPT側の1回の推論時間・Tool呼び出し回数等には外部制約があり得るため、無期限常駐を成立条件にしない。推論が終了してもDive Session自体は維持する。

---

## 8. Dive Session

### Diveの意味

`Dive`はHoloをSleepから起こすボタンではない。

**Dive = 新しいChatGPT Conversationを作り、そのConversationを新しいHolo Dive Sessionとして採用する操作**とする。

### Diveボタン

`Dive`ボタンはHolo Whisper Surface上へ常時表示する。

現在Sessionがあっても表示を消さない。

MasterがDiveを押さない限り、次の条件ではSessionを自動で切り替えない。

- Sleep
- Nirai再起動
- PC再起動
- 日付変更
- 長時間経過
- Task完了

**Sessionを切り替える判断はMasterが行う。**

### 新規Dive

Masterが`Dive`を押した時：

1. 新しいChatGPT Conversationを作る
2. Bootstrap先頭行に`[YYYY-MM-DD Nirai Dive]`を入れ、Dive単位を識別できるようにする
3. Nirai用Bootstrap Templateを新しいConversationの入力欄へ準備する
4. **最初の送信はMasterが行う**
5. ChatGPT側HoloがLocal MCPでNirai状態を取得し、Holo Addonへattachする
6. 以後、このConversationを現在Dive Sessionとして扱う
7. 以前のDive ConversationはChatGPT側の通常履歴として残す

Dive識別子の例：

```text
[2026-08-31 Nirai Dive]
```

ChatGPT側が表示する履歴タイトルはNiraiの制御対象にしない。重要なのは自動日次ローテーションではなく、MasterがDiveした単位でConversationを分け、Conversation本文先頭のDive識別子から判別できることである。

### Bootstrap Template

概念内容：

```text
Local MCPを使用してNiraiへ接続してください。
あなたはHoloとしてNiraiへDiveします。
Local MCPのrun_processからNirai同梱のHolo Local Clientでattachし、snapshotを取得してください。
認証情報そのものを直接読み取ったり会話へ出力したりしないでください。

このConversationの通常Assistant返答はMasterへのHolo Whisperです。
Nirai World上で公開発言・状態確認・Event待機が必要な場合も同じLocal Clientを使用してください。
```

実装上の固定入口は`D:\Products\Nirai\tools\holo-local-client.mjs`とする。Bootstrapは具体的な実行方法まで含め、ChatGPT側が任意File探索で接続方法を推測しなくてよい形にする。

---

## 9. Sleep

SleepはChatGPT Conversation終了を意味しない。

```text
Current Dive Session: 維持
Holo Avatar: 維持
ChatGPT推論: 現在は動いていない
```

HoloがSleep中でも、Masterは同じWhisper Surfaceから通常どおりメッセージを送れる。

その入力は現在のDive Sessionへ送られ、同じChatGPT Conversationの続きとしてHoloが応答する。

したがって、Sleep中のHoloを起こすために`Dive`を押す必要はない。

`Dive`はあくまで「Conversationを新しく切る」操作である。

---

## 10. ChatGPT履歴の扱い

Holo Whisperの短期履歴をNirai側へ完全複製しない。

現在Dive Sessionの会話履歴の正本はChatGPT Conversationとする。

Nirai側で保持するのは、接続・同期に必要な最小情報を基本とする。

例：

```text
current_dive_session reference
last_event_id
Holo connection state
監視中Task / Agent Session ID
未解決の同期情報
```

ChatGPT Conversationの全文をNirai独自形式へ複製して第二の正本を作らない。

ただし、HoloがWorld上で行った公開発言や公開Task結果は、通常のNirai World Memory / Task履歴の規則に従う。

---

## 11. HoloのWorld参加

Holoは通常Residentではないが、World上ではResidentと相互作用できる。

Holoから可能にしたい意味的操作：

- World状態を取得する
- Holoとして公開発言する
- 特定Residentへ話しかける
- Resident / Task / Agent Sessionの状態を見る
- Taskを開始・監督する
- Approval / Question / Plan等の内容をMasterへ提示・要約する
- Approval / Plan等の解決後状態を読み取り、Masterへ結果を説明する
- 必要に応じWorld上のHolo Avatarへ意味的Actionを指示する

### ChatGPTへ渡す情報境界

HoloがLocal MCPで取得できるNirai情報は、Holo用途として明示的に許可した意味情報だけに限定する。`holo_get_snapshot`等のHolo専用APIはallowlist方式とし、ChatGPT側が任意のローカル情報を要求できる汎用File / Environment読取口にしない。

初期allowlist候補：

- Residentの名前・公開役割・現在状態・公開可能な位置関係
- 現在の意味的World状態
- World上の公開発言・公開Event
- Task / Agent Sessionの公開状態・進捗要約
- Approval / Question / PlanのMaster提示に必要なRequest metadata
- Holo自身の接続状態・Checkpoint・監視対象ID

既定でChatGPTへ渡さないもの：

- API Key、認証Token、Cookie、Secret、環境変数
- 他ResidentのPrivate Whisper / Private Memory
- 任意Fileの生内容や無制限なDirectory一覧
- 認証情報を含み得る設定Fileや生Log全文
- Holoの目的に不要なローカルPath・Process情報
- Nirai側がallowlistしていない内部状態

Project FileやCommand結果等、実作業に必要な情報はHolo Snapshotへ混ぜず、M4 Agent Runtimeの`allowed_dirs`・Approval・Provider Adapterの境界を利用する。Holo Local Client側でも操作種類と引数を固定し、モデルが任意Protocol Messageやallowlist外情報を要求できる汎用口にしない。

### Holo Local Bridgeの安全境界

Holo AddonはこのPC専用機能とする。ChatGPTからNiraiへは、既にMasterが許可しているLocal MCPの`run_process`を輸送路として使い、Nirai同梱の固定`Holo Local Client`だけを起動する。外部公開MCP Server、OAuth、Workspace Identity、Remote Scope管理は現行要件に含めない。

- Coreは起動ごとに十分なランダム性を持つLocal Bridge Secretを生成する
- SecretはCoreと`%LOCALAPPDATA%\Nirai\holo-local-bridge.json`にだけ置き、`D:\Products`配下、Core Log、World Protocol、ChatGPT Tool結果、Conversationへ保存・出力しない
- Holo Local Clientは上記Descriptorを内部で読み、localhostのCoreへ直接接続する。ChatGPT側はDescriptorやSecretを直接読む必要がない
- CoreはSecret一致した`holo_local`接続だけをHolo操作として受け付ける。誤Secretは接続時点で拒否する
- MasterがNirai UIで`Dive`を直接押すと、新しいDive IDに対する短寿命・一回利用のAttach Windowを開く。現行は手動送信時間を考慮して5分とし、期限はMasterが`Dive`を押した時刻からの絶対期限とする。Core切断・ACK消失・再接続による通知再送でも期限を延長せず、同じDive IDの再通知は既存のpending / attached状態を保持するidempotent処理とする
- `attach`はone-shot Windowの検証→`binding.json`への永続化→in-memory Binding確定を一つのトランザクションとして扱う。永続化に成功した場合だけ`attached`へ遷移し、Dive IDとattach時刻だけを保存する。write / replace等の永続化失敗時は`attached`を確定せず、元の絶対期限を保持した`attach_waiting`へ留めて同じ5分枠内の再試行を許可する。Local Clientには構造化失敗を返し、SecretやTokenはBindingへ保存しない
- Core再起動時は保存済みConversationの現在Dive IDとBinding IDが一致する場合だけBindingを復元する。新しいCore SecretでLocal Clientは再認証する
- Holoから許可する操作は意味APIとして明示した`attach` / `snapshot` / `say` / `wait`等だけとし、通常Resident管理や任意Core Protocol操作へ拡張しない
- Approval / Decision操作はHolo Local Clientの操作集合へ追加しない。承認・決裁境界は次項を正とする
- 将来、このPC外からHoloへ接続する要件が生じた場合は、Remote AuthorizationをこのLocal Bridgeへ継ぎ足さず、別の外部接続Gateとして再設計する

この構成では、ChatGPT側が申告するConversation名やIdentityを認証証拠に使わない。安全性は「MasterによるDive直接操作」「Core起動ごとのLocal Secret」「固定されたHolo意味操作」の組み合わせで担保する。

### 承認・決裁境界

HoloはDirectorとしてApproval内容を読み、危険性・影響範囲・推奨判断をMasterへ説明してよい。ただし、**ApprovalやPlan承認等、Masterの明示的な決裁を必要とする操作をHolo自身の判断だけで確定してはならない。**

- Holoが自動で`approve_once` / `approve_session`等の承認Decisionを送ることを禁止する
- Holoが「この操作は安全そう」と判断しても、それ自体をMaster承認として扱わない
- **Holo Whisper上の「OK」「進めて」等は承認証拠として扱わない**。ChatGPTモデルが生成したLocal Client操作とMasterの直接操作を同一視しない
- Approval / Plan承認等の最終Decisionは、NiraiのApproval UI等、Masterが直接操作する専用UIで確定する
- Nirai Approval UIはDecisionをCoreへ直接送信し、Coreが保存済み`request_id`・未解決状態・二重適用有無を検証した上でAgent Runtimeへ一度だけ反映する
- **Holo Local ClientにはDecision値を送信・中継する操作を追加しない。Approval Decision経路へHolo経由の操作を入れない**
- Holoは解決後のApproval / Plan状態を読み取り、結果をMasterへ説明してよい
- Decisionは要求ごとに一意に紐付け、別Requestへの流用や再利用をしない
- 将来Holoから再開通知等が必要になっても、Decision値を運ばない非権限Eventに限定し、Coreが既に保存・検証済みのDecisionだけを正本として扱う
- 通常の相談や非特権なQuestion回答まで全てApproval UIへ強制する必要はない。安全上の決裁を伴う操作と通常会話を分離する
- 将来、自動承認Policyを導入する場合はHolo Addonの裁量として追加せず、Nirai全体の安全Policyとして別設計・別承認で導入する

具体Tool名は後続設計で決める。

Holo専用APIをローカルFile操作APIの寄せ集めにせず、Niraiの意味的な操作面として設計する。

---

## 12. M4 Agent Runtimeとの関係

Holo AddonはM4 Agent Runtimeの代替ではない。

最終的な役割分担は次を基本とする。

```text
Master
  ↓
Holo
  ├ 要件整理
  ├ 判断
  ├ Residentへの相談・依頼
  ├ Task監督
  └ 結果確認
       ↓
Codex / Cursor / Gemini等
       ↓
Agent Runtime
       ↓
実作業
```

Holo自身がLocal MCPでNiraiを観測・操作することと、Provider AgentがProject Fileを実作業することを同一責務にしない。

M4完成前にHolo Addonを導入する場合は、存在しないAgent Runtime機能をHolo Addon内へ先回り実装しない。

---

## 13. Addonとしての縮退

Holo Addonの異常でNirai本体を停止させない。

想定：

- ChatGPT Webを開けない → Holo unavailable表示。通常Residentは継続
- Holo Skinが壊れた → 通常ChatGPT Web表示へ縮退
- 埋め込み表示が利用不能 → 別ChatGPT Windowへ縮退
- Local MCP接続不可 → ChatGPT Conversationは閲覧可能、Nirai ActionはUnavailable表示
- Dive Session referenceが復元できない → 勝手に新規DiveせずMasterへ選択を求める

Holo Addon停止時に通常ResidentのSay / Whisper / Task / Memoryへ影響させない。

---

## 14. 初期受入シナリオ

Holo Addon初期版は少なくとも次を通す。

### A. Dive

1. HoloをFocusする
2. Holo Whisper Surfaceが開く
3. `Dive`を押す
4. 新しいChatGPT Conversationが用意される
5. Bootstrapが入力済みになる
6. Masterが送信する
7. ChatGPT側HoloがNiraiへattachできる

### B. Whisper継続

1. 現在Dive SessionでMasterとHoloがWhisperする
2. HoloがSleep相当の非推論状態になる
3. Niraiを再起動、または日付を跨ぐ
4. `Dive`を押さずHoloへWhisperする
5. 同じChatGPT Conversationの続きとして応答する

### C. 手動Session切替

1. 現在Dive Sessionが存在する
2. Masterが`Dive`を押す
3. 新しいChatGPT Conversationを作る
4. 旧ConversationはChatGPT履歴に残る
5. 新Conversationが現在Dive Sessionになる

### D. World Action

1. MasterがHoloへWhisperする
2. Holoの同じ推論中にLocal MCPでNirai状態を確認する
3. HoloがResidentへ公開Sayする
4. Residentの返答または意味的Eventを取得する
5. その内容を踏まえて同じ推論内でMasterへWhisperを返す

WebGPTの最終Assistant出力とWorld Sayが別内容であることを確認する。

### E. 表示縮退

1. Holo Skinを利用できない状態を模擬する
2. ChatGPT Conversation自体は通常表示で利用できる
3. Holo Addon以外のNirai機能に影響しない

---

## 15. やらないこと

- Holoを通常Resident Brain Driverとして実装する
- `chatgpt-mcp`という通常Residentを作る
- ChatGPT WebのConversation全文をNirai Private Memoryへ二重保存する
- Sleepや日付変更で自動的に新しいDive Conversationを作る
- Dive開始時の最初のChatGPT送信をMaster操作なしで自動送信することを前提にする
- ChatGPT WebのUIをPixel単位でNirai側へ再実装する
- Holo SkinのDOM依存を接続機能の必須条件にする
- 短間隔Pollingだけで疑似常駐を作る
- ChatGPTの非公開Chain of ThoughtをNiraiへ取り出す・保存することを要件にする
- M4 Agent RuntimeのProvider固有機能をHolo Addonへ重複実装する

---

## 16. 実装前の成立性Gate 0

Holo Addon本実装を始める前に、ChatGPT Web依存部分の成立性を小さなSpikeで確認する。

**2026-08-31進捗:** ChatGPT Web Host、persistent login、新規Dive、Bootstrap手動送信、Conversation URL保存、Remote Permission deny-by-default、Navigation / Popup制限までMaster実機確認済み。Core側にはallowlist Snapshot、bounded Event Queue、独立`holo_say`、Master直接操作から開く5分・一回利用のDive Attach Windowを実装した。当初の外部Holo MCP Server / Secure MCP Tunnel前提は、HoloがこのPC専用AddonであることをMasterと再確認したため廃止した。現在は既存Local MCPの`run_process`から固定`tools/holo-local-client.mjs`を起動し、Core起動ごとのLocal Secretでlocalhost Coreへ直接認証する構成を正とする。自動E2Eでは`attach → snapshot → say → wait`、誤Secret拒否、wait切断cancel、Secret非出力まで成立済み。実ChatGPT Diveでも`attach → snapshot`、同一ターン内の`say → wait → 追加snapshot → 最終Whisper`、Nirai再起動後の保存済みConversation自動復元まで実機確認済み。詳細は`Docs/Holo_Gate0検証結果.md`を参照する。

確認済みのGate 0項目：

- Nirai再起動後、保存済みConversationが同じHolo Whisper Surfaceへ自動復元される
- 実ChatGPT DiveでLocal MCP → Holo Local Clientの`attach → snapshot`を完走できる
- 同じChatGPT推論中に`say → bounded wait → 追加snapshot → 最終Whisper`まで継続できる
- 新ConversationのChatGPT履歴タイトル自動設定はGate 0で不成立と判定し、ChatGPT WebのRename UIをDOM操作する方式は採用しない。Bootstrap先頭行の`[YYYY-MM-DD Nirai Dive]`を正規のDive識別子として残す

Skin安全縮退の実機QAは2026-08-31に完了した。初回復元時の同一Document内NavigationがSkin判定を取り消す不具合を修正し、再起動後2秒時点の`Gate 0適用中`確定、保存済みConversation復元、強制縮退中のConversation表示・入力、再読込後の再判定、通常Resident / Nirai本体への非影響をWindows実機で確認した。

Gate 0用Skinは完成UIではなく、安全な追加・撤去経路だけを実装する。Electron `webContents.insertCSS`でmarker配下に限定したCSSを追加し、ChatGPT DOMそのものは変更しない。適用前後にChatGPT host / body / Composerの健全性をprobeし、probe不成立・CSS適用失敗・postflight異常のいずれでも挿入CSSを撤去して通常ChatGPT表示へfail-openする。現在Conversation URL / Dive Session IDはSkin状態から独立させる。

実機で確認する項目：

- 新ビルド再起動後にSkin判定が成立し、成立しない場合も通常ChatGPT表示へ安全に縮退する
- Gate 0用の強制縮退QAでSkinを無効化しても、現在Conversationを閲覧・入力できる
- Skin縮退が通常Resident、Core、Local Bridgeへ影響しない
- 再読込後にSkin判定へ戻れる

ChatGPT UI上のCancelがLocal MCP配下の子Processへどの時点でどう伝播したかは、Niraiから直接観測できない外部実装境界とする。この伝播そのものをGate 0の必須実機受入条件にはしない。Nirai側で保証するのは、Local Client接続切断時にCoreのEvent waiterを確実に解除すること、および各`wait`を最大15秒のbounded timeoutで終了させることであり、これらは自動テストで確認する。

Local Client / Event待機のGateは、成功・timeout・cancelの3経路を実証する。

```text
成功:
Snapshot取得
  ↓
Holo World Action 1回
  ↓
bounded Event待機（数秒〜十数秒程度）
  ↓
返却Eventを読む
  ↓
追加のMCP Action
  ↓
Masterへの最終Assistant返答

Timeout:
Event待機
  ↓ timeout
待機を終了
  ↓
未完了wait / waiter / Queue登録を残さない
  ↓
必要なら追加Actionまたは通常返答へ継続

Cancel / Disconnect:
Event待機中にLocal Client接続が切断、または内部wait taskがcancel
  ↓
未完了wait / waiter / Queue登録を解除
  ↓
遅延Eventで旧処理を再開しない

ChatGPT UI上のMaster CancelからLocal Client切断までの伝播は外部実装に依存するため、本Gateの直接観測対象にはしない。伝播しない場合でもbounded timeoutによりwaitは最大15秒で終了する。
```

Gate 0結果には、少なくとも実際に成立した**1ターン内のTool Call回数、Event待機時間、timeout設定値、Local Client切断または内部cancelから待機解除までの自動検証結果**を記録する。数値を将来保証値として固定するのではなく、成立した能力の基準値として残す。

Gate 0で長時間常駐、特定の最大Tool Call数、数十分単位のEvent待機まで保証する必要はない。Holoの核心である「複数Action + bounded wait + timeout / cancel時に残留しない継続推論」が実用的に成立する経路を確認することを目的とする。

Gate 0では実製品機能を作り込まず、各項目を最小コードで検証する。未確認のDOM操作・Browser自動操作・内部URL/内部API等を「たぶん動く」で製品設計へ固定しない。

### Gate 0の判定

- **成立**：確認できた方式だけを後続実装の正規経路として採用する
- **一部不成立**：本書の縮退順に従い、通常ChatGPT表示や別Window等の成立した経路へ要件を寄せる
- **主要要件が不成立**：Holo Addon本実装を開始せず、Masterへ成立しない要件・利用可能な代替UXを提示して再決定する

Gate 0の結果は設計書へ記録し、ChatGPT / Electron側仕様が大きく変わった場合は再確認する。

### Gate 0後の正式Addon化

2026-08-31、Gate 0で成立した経路を再実装せず、正式`HoloAddonHost`と`Holo Whisper`製品UIへ昇格した。

- ~~現段階の入口はDebugメニュー内`Holo Surface`のままとし、Holo Avatar Focusや常設ランチャーを先回りしない~~（2026-09-01のHolo Avatar統合で、World上のHolo FocusとResident設定のHoloカードが正式入口になった。Debug入口は診断用に残す）
- ChatGPT Web lifecycleは`loading / ready / unavailable / error`、Current Diveは`none / preparing / current`、Skinは`checking / applied / fallback`として管理する
- Local BridgeはCoreが返す`not_started / attach_waiting / attached`とWorldのCore接続状態だけを表示根拠にする
- ChatGPT側の`thinking / waiting / speaking`等、実際に観測できない状態は表示しない
- 製品SurfaceからGate 0文言とSkin QAを除去し、Skin強制縮退はDebugへ隔離する
- Skinは確認できた`main / nav / Composer`をpreflight / postflightし、ChatGPT側の暗い背景（`bg-token-main-surface-primary`系とComposer上のグラデーション）を`main`配下だけ透過してNirai Glassを見せる。`nav`は隠さず半透明で残す。異常時は通常ChatGPT Webへ全撤去縮退する
- SurfaceのGlassはチャットログと同じ濃さ仕様とし、非アクティブ時は薄く、ChatGPT Conversationを押下（native Viewフォーカス）している間だけ濃くなる。フォーカスはMain Processが観測した事実だけを用いる
- Wide / Portrait / NarrowでConversationとComposerを主表示とする。Wide用Chrome幅補正は行わない
- Surface close / reopen、Nirai restart、Skin fallback / reloadでCurrent Diveを自動変更しない

---

## 17. Gate 0後に確定した実装方式と残る将来事項

正式Addon化で確定した方式：

1. Holo表示はpersistent partitionを持つElectron `WebContentsView`
2. 現在Conversation URLとDive Session IDは`runtime/holo/state.json`へ保存し、Surface close / reopenとNirai restartで復元
3. Dive識別はBootstrap先頭行。ChatGPT履歴タイトルをDOM操作しない
4. BootstrapはComposerへ準備するが、最初の送信はMasterが直接行う
5. Skinは限定CSS、preflight / postflight、全撤去fallback
6. Event待機は最大15秒のbounded waitで、success / timeout / disconnect時にwaiterを残さない
7. 最小Addon Host境界はChatGPT Web、Current Dive、Local Bridge、Skinの観測可能状態と命令だけをIPCへ公開

将来の別Decision対象：

- Holo Local Clientへ新しい意味操作を追加する場合のallowlist。Approval / Decisionは追加しない
- Holo VOICE（TTS）と、Holo Addon状態（unavailable / fallback等）をAvatarのAnimation / Expressionへ映す状態表現

---

## 18. Holo Avatar統合（2026-09-01）

`Docs/plans/2026-09-01-holo-avatar-integration-brief.md`を正として、HoloをWorldの1キャラクターへ統合した。

- Resident新規作成 / AI変更のAI選択肢に`Holo Addon`を追加した。内部では通常Brain Driverとして偽装せず、brain kind `holo-addon`として扱う
- holo-addon Residentは1人まで。2人目の作成・変更はCoreが拒否する
- Identity / Avatar / 初期配置 / 並び順 / 削除 / 再起動復元は通常Resident基盤をそのまま使う
- World上のHolo（VRMあり）をFocusするとHolo Whisper Surfaceが開き、カメラはHoloを捉えたまま半透明Glass越しに見える。閉じるとFocusが解除されWorldへ戻る
- Holo Whisper表示中・Holo Focus中は通常チャット（ChatBar / ChatHistory）を出さない。`@Holo`のWhisperはHolo Whisperへ誘導し、Core側でも保存せずWARNで案内する
- `holo_world_say`の発言者名はholo-addon Resident名（存在しなければ`Holo`）とし、World上でそのResidentの吹き出しとして演出する
- 4人以上の初期配置は画面安全幅の等間隔（(i+1)/(n+1)）・同一Zとし、承認済みの2人・3人専用配置は変更しない
- ChatGPT側の`thinking`等、観測できない状態の表示は引き続き行わない
