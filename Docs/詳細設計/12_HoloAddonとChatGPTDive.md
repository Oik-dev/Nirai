# Nirai 詳細設計 12：Holo AddonとChatGPT Dive

本書はHolo Addonの要件正本である。Holoは通常Residentではなく、ChatGPT WebとLocal MCPを利用してNiraiへ参加する専用Addonとして扱う。

**Status: Requirements Defined（2026-08-31）**

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

Holoは`residents/<name>/`で管理する通常Residentではない。

```text
Nirai
  ├ Resident System
  │   ├ Codex
  │   ├ Cursor
  │   └ Gemini
  │
  └ Holo Addon
      ├ Holo Avatar
      ├ Holo Whisper Surface
      ├ ChatGPT Dive Session
      ├ Local MCP Bridge
      └ Holo State / Event Queue
```

### 通常Residentと分ける理由

- Brainの実体がNirai Coreから直接起動するCLI/APIではなくChatGPT Webである
- Masterとの私的会話履歴の正本がChatGPT Conversationである
- 1回の推論中に複数のLocal MCP Actionを行う
- Holo自身がNirai全体を観測・調停するDirector寄りの役割を持つ
- ChatGPT Webを表示する専用UIが必要になる

Holo Addonを無効化しても、通常Resident、Core、World、会話、M3/M4等の基本機能は成立し続けること。

---

## 3. Holo Avatar

Holo AddonはHolo専用VRM Avatarを持つ。

- Avatarは通常Residentの新規作成・削除UIとは分離して管理する
- Holo Addonが有効な間、ChatGPTが現在推論中でなくてもHolo AvatarはWorldに存在できる
- ChatGPT推論終了をAvatar削除やHolo不在として扱わない
- Holoは通常Residentと同じWorld上で公開会話・演出へ参加できる

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
2. 日付と`Nirai Dive`を含む識別しやすいConversation名を目標とする
3. Nirai用Bootstrap Templateを新しいConversationの入力欄へ準備する
4. **最初の送信はMasterが行う**
5. ChatGPT側HoloがLocal MCPでNirai状態を取得し、Holo Addonへattachする
6. 以後、このConversationを現在Dive Sessionとして扱う
7. 以前のDive ConversationはChatGPT側の通常履歴として残す

Conversation名の例：

```text
2026-08-31 Nirai Dive
```

日付表示形式そのものはUI仕様として後で確定してよい。重要なのは自動日次ローテーションではなく、MasterがDiveした単位で履歴を分け、ChatGPT履歴から識別しやすくすることである。

### Bootstrap Template

概念内容：

```text
Local MCPを使用してNiraiへ接続してください。
あなたはHoloとしてNiraiへDiveします。
現在のNirai状態を取得し、Holo Addonへattachしてください。

このConversationの通常Assistant返答はMasterへのHolo Whisperです。
Nirai World上で公開発言・状態確認・操作が必要な場合はLocal MCPを使用してください。
```

実際の文面は、Local MCP ToolとHolo Addon Protocolが確定した時点で短く最適化する。

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

Project FileやCommand結果等、実作業に必要な情報はHolo Snapshotへ混ぜず、M4 Agent Runtimeの`allowed_dirs`・Approval・Provider Adapterの境界を利用する。Holo専用MCP Server側でも返却項目を検証し、モデルが指定した引数だけでallowlist外情報へ拡張できないようにする。

### Holo MCPの認証・認可境界

Holo専用MCPはPrivate Dataの参照とNirai上のUser Actionを扱うため、**接続できたこと自体を認証済みとはみなさない。** Server側で呼出元Identityと権限Scopeを検証し、未認証・未許可のRequestはTool実行前に拒否する。

- 未認証Requestは拒否する。認証失敗時にSnapshot・Event・内部状態等を返さない
- 許可対象はMasterがHolo用として連携したChatGPT側Identity / Workspace / Connector等、認証層からServerが検証できるClaimだけに限定する
- Workspace名、Conversation名、`dive_session_id`等をモデルがTool引数へ書いただけでは認証証拠にしない
- 現在Dive Sessionは、検証済みの呼出元IdentityとNirai側の`Dive Binding`を結び付けて管理する
- ChatGPT側から検証可能なConversation / Workspace識別Claimが提供される場合はDive Bindingへ利用してよい。提供されない場合は、Niraiの`Dive`直接操作から発行する短寿命・一回限りのAttach Binding等、Server側で検証可能な方式へ縮退する
- Attach用Secret / Token等を採用する場合は十分なランダム性・短い有効期限・一回利用を基本とし、使用後は再利用できないようにする。認証情報そのものをChatGPTへの通常Tool結果、World Protocol、会話履歴、Core Logへ出さない
- 認証済みでも最小権限とし、少なくとも`read_snapshot` / `world_action` / `task_control`等のScopeを分離できる構造にする。不要Scopeは付与しない
- Approvalの最終DecisionはHolo Scopeへ含めない。承認・決裁境界は次項を正とする
- Dive終了・連携解除・認証失効時は対応するDive Binding / Scopeを失効させ、古いSessionから継続操作できないようにする

具体的なOAuth / Authorization Flow、利用可能な認証Claim、Dive Binding方式は現行のChatGPT / MCP仕様に依存するためGate 0で確定する。**モデル自身が申告したIdentityをServer認証の代替にしないこと**を不変条件とする。

### 承認・決裁境界

HoloはDirectorとしてApproval内容を読み、危険性・影響範囲・推奨判断をMasterへ説明してよい。ただし、**ApprovalやPlan承認等、Masterの明示的な決裁を必要とする操作をHolo自身の判断だけで確定してはならない。**

- Holoが自動で`approve_once` / `approve_session`等の承認Decisionを送ることを禁止する
- Holoが「この操作は安全そう」と判断しても、それ自体をMaster承認として扱わない
- **Holo Whisper上の「OK」「進めて」等は承認証拠として扱わない**。ChatGPTモデルが生成したMCP Tool引数とMasterの直接操作を同一視しない
- Approval / Plan承認等の最終Decisionは、NiraiのApproval UI等、Masterが直接操作する専用UIで確定する
- Nirai Approval UIはDecisionをCoreへ直接送信し、Coreが保存済み`request_id`・未解決状態・二重適用有無を検証した上でAgent Runtimeへ一度だけ反映する
- **HoloにはDecision値を送信・中継するToolやScopeを与えない。Approval Decision経路へHoloのMCP Tool Callを入れない**
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

現時点ではLocal MCP経由でNiraiへ接続する経路は成立候補として扱えるが、次のChatGPT Web操作は**成立済み前提にしない**。

- ChatGPT WebをNirai内のElectron Surfaceへ安全・安定して表示できるか
- 既存ConversationをNirai側から再表示・復元できるか
- `Dive`押下から新しいConversationを用意できるか
- 新Conversationへ識別しやすいタイトルを付けられるか。自動設定できない場合はBootstrap先頭行へDive識別子を入れる方式で縮退できるか
- Bootstrap Templateを入力欄へ準備できるか
- ChatGPT WebのDOM/CSSへ依存したSkinを、壊れた時に安全に無効化できるか
- Holo MCPの正規認証接続が成功し、Server側で検証可能な呼出元Claimを取得できるか
- 未認証Request、および許可されていない別Identity / 別接続元からのRequestをTool実行前に拒否できるか
- 現在Dive Sessionと認証済みIdentityを安全に結び付けるDive Binding方式が成立するか
- 失効済み・別DiveのBindingを拒否し、現在の正規Dive Bindingだけが利用できるか
- Scope不足のIdentityで`world_action` / `task_control`等を要求した場合に拒否し、許可Scope内だけ実行できるか
- **1回のChatGPT推論中にLocal MCPを複数回呼び出せるか**
- **同一推論中に短い`holo_wait_events`相当の待機を行い、返ったEventを読んだ後でも追加のMCP Actionと最終Assistant返答を継続できるか**

複数MCP Action / Event待機のSpikeは、成功・timeout・cancelの3経路を実証する。

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

Cancel:
Event待機または推論中
  ↓ Master Cancel / Dive切替等
待機を停止
  ↓
未完了wait / waiter / Queue登録を解除
  ↓
Cancel後の遅延Eventで旧処理を再開しない
```

Gate 0結果には、少なくとも実際に成立した**1ターン内のTool Call回数、Event待機時間、timeout設定値、Cancelから待機解除までの観測結果**を記録する。数値を将来保証値として固定するのではなく、実機で成立した能力の基準値として残す。

Gate 0で長時間常駐、特定の最大Tool Call数、数十分単位のEvent待機まで保証する必要はない。Holoの核心である「複数Action + bounded wait + timeout / cancel時に残留しない継続推論」が実用的に成立する経路を確認することを目的とする。

Gate 0では実製品機能を作り込まず、各項目を最小コードで検証する。未確認のDOM操作・Browser自動操作・内部URL/内部API等を「たぶん動く」で製品設計へ固定しない。

### Gate 0の判定

- **成立**：確認できた方式だけを後続実装の正規経路として採用する
- **一部不成立**：本書の縮退順に従い、通常ChatGPT表示や別Window等の成立した経路へ要件を寄せる
- **主要要件が不成立**：Holo Addon本実装を開始せず、Masterへ成立しない要件・利用可能な代替UXを提示して再決定する

Gate 0の結果は設計書へ記録し、ChatGPT / Electron側仕様が大きく変わった場合は再確認する。

---

## 17. 実装前に確定する未決事項

以下は要件ではなく実装方式の未決事項とする。

1. Gate 0で成立確認されたChatGPT Web表示方式（`WebContentsView`、専用Window等）
2. ChatGPTの認証状態を安全に利用するSession分離方式
3. 既存Dive Conversationを再表示・復元する方法
4. 新Dive Conversationを作成し、識別しやすいタイトルへする方法
5. Bootstrap Templateを入力欄へ準備する方法
6. Holo Skinの適用方法と壊れた場合の検出方法
7. Holo用Local MCP Toolの正式なTool set / Payload
8. Holo MCPのAuthorization方式、検証可能なIdentity Claim、Scope、Dive Binding方式
9. Event待機方式と1回の推論で安全に待てる上限
10. Holo Avatarの配置・VOICE・Animation・状態表現
11. Holo AddonをNirai本体へ組み込む最小のAddon Host境界

これらはGate 0と実装開始時の現行Electron / ChatGPT / Local MCP仕様を正とし、本書のUX要件を最も単純に満たす方式を選ぶ。
