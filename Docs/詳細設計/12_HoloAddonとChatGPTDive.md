# Nirai 詳細設計 12：Holo AddonとChatGPT Dive

本書はHolo AddonのIdentity・ChatGPT Conversation・Dive・Local MCP境界のActive Design / 要件正本である。Product Goalは [Nirai_基本設計.md](../Nirai_基本設計.md)、設計判断ルールは [Nirai_設計ガバナンス.md](../Nirai_設計ガバナンス.md) を上位正本とする。Holoの頭脳と私的会話はChatGPT WebとLocal MCPを利用する専用Addonであり、通常Brain Driverへ混ぜない。World上のHoloの実体は通常Resident基盤のIdentityを共有するが、PresentationはWorldごとに差し替える。配布可能なStandard WorldのVRM / Electron表示は本書の既存実装、Private DNA WorldのDNA Body・UE内Whisper表示・Focus Cameraは [DNA Architecture v0.5](../Nirai_DNA_UE427_Architecture_2026-09-08.md) を正とする。

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
- Holoが監督するTask、または所有Diveを記録したSupervisor ReviewがAssistant返答後に終端状態へ遷移した場合、Masterの追加発言を要求せず、Niraiがその仕事を所有する同じChatGPT Conversationを自動再駆動してHoloの監督を継続する
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
- Model / Reasoning / Persona Prompt等、ChatGPT側をNiraiの通常Brain設定として上書きする項目はHoloへ表示しない。VoiceはBrain設定とは分離し、World Presentation用の任意Voice Providerとして将来設定可能にする

Holo Addonを無効化しても、通常Resident、Core、World、会話、M3/M4等の基本機能は成立し続けること。

---

## 3. Holo Body / Avatar

Holoの身体PresentationはWorld実装に従う。Standard WorldではVRM Avatar、Private DNA WorldではDNAプレイアブルBodyを使う。

- Standard Worldでは通常ResidentのVRM Avatarパイプライン（読込・変更UI・VRM Runtime）をそのまま使う
- Private DNA WorldではHoloへDNAプレイアブルBodyを1対1でBindingし、VRMを完成Bodyとして持ち込まない
- Holo専用の人格・記憶・Brain層を身体側へ複製しない
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

Holo WhisperのConversation正本は**実際のChatGPT Conversation**を維持する。ただし表示SurfaceはWorldごとに異なってよい。

- Standard Worldでは既存のChatGPT Web Surfaceを利用できる
- Private DNA WorldではUEを唯一のMain UX Surfaceとし、Holo専用経路から表示に必要なConversation eventだけをUE Private UIへ中継する。通常利用時に別Electron / ChatGPT Windowへ移動する構成を完成形にしない

どちらでもChatGPT Conversation全文をNirai Memoryへ第二正本として複製しない。

```text
Holo Whisper

Master:
次どこから進めよう？

Holo:
まずこの要件を固めた方がいい。

[ message ... ]
```

### Standard WorldにおけるChatGPT Web表示

以下は配布可能なElectron / Three.js Standard Worldの表示設計である。Private DNA Worldの通常UIには適用せず、そちらはDNA Architecture v0.5のUE Private UIを使う。

ChatGPT Webを黒い四角のBrowserとして置くことを完成形にしない。

外側はNiraiの既存UIと同系統のGlass Surfaceとして表示する。

理想形：

- 背後のNirai Worldが見える半透明Glass
- Holo Whisper Conversationを主表示
- 不要なBrowser chromeを見せない
- ChatGPT側の不要なSidebar/Header等は、安定して可能な範囲で簡略化する
- ChatGPT標準背景を透明化またはNiraiへ馴染ませるSkinを適用できる場合は利用する

ただしChatGPT WebのDOM/CSSはNiraiの管理外である。Holo SkinがChatGPT更新で壊れてもConversation自体を利用不能にしてはならない。

Standard Worldの縮退順：

```text
Nirai Glass + Holo Skin
        ↓ Skin利用不能
Nirai Glass + 通常ChatGPT Web
        ↓ 埋め込み自体が利用不能
ChatGPT Webを別Windowで開いて接続を維持
```

この別Window fallbackはStandard World限定であり、Private DNA Worldの完成UXには採用しない。正本はChatGPT Conversationであり、Skinや表示Surfaceは付加機能とする。

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

### 推論終了後のTask / Review自動再駆動

`bounded wait`は1回のChatGPT推論内での効率的なEvent待機を担う。一方、TaskやHolo Supervisor Reviewが長時間化してChatGPTのAssistant返答が先に終了した場合も、Masterが会話を再開するまで監督を止めてはならない。

Holoが監督するTask / Agent Sessionが次の状態へ遷移した場合、Nirai WorldはHolo Addonへ構造化Triggerを渡し、Holo AddonはTask開始時に保存した所有DiveのConversationへ`[Nirai Auto Resume]`を自動送信する。所有情報のないTaskから送信先を推測しない。

- `done / failed / cancelled / interrupted`
- `waiting_for_master`（大規模破壊のApprovalや、実際にMaster判断が必要なQuestion等）

Auto ResumeはMaster発言として扱わず、Task本文やProvider出力をそのまま自動Promptへ埋め込まない。Task ID / Agent Session ID / Request ID等の識別情報だけを渡し、Holo自身がLocal Clientの`task-snapshot`等でNiraiの正本を再取得する。

同一Triggerはdedupeし、ChatGPTが生成中の場合はQueueで待つ。MasterがComposerへ未送信下書きを持つ場合は絶対に上書きせず、Queueを永続化して後で再試行する。Current Diveが存在しない場合も勝手に新規Diveを作らない。新しいDiveがConversation URLまで確定した後は、旧Diveに属する`done / cancelled`は再開価値のない終端通知としてQueueからpruneし、後着した同種Triggerもenqueueせずprocessed扱いにする。一方、`failed / interrupted / waiting_for_master`はCommanderまたはMasterの判断が残るため旧Dive由来でも保持する。旧Dive通知がQueue先頭に残っていても、表示中Current Dive宛てTriggerを後続から選べるようにし、head-of-line blockingでCurrent Workflow Resumeを止めない。

Auto Resume後のHoloは、Masterの追加発言を要求せず、通常Tool・通常のstaging差分反映・Planを含む承認不要な次工程を続行する。Masterへ直接確認するのは、大規模な破壊的変更、commit、push等の重大操作だけとする。その場合だけ自動進行を止め、Holo自身ではDecisionを確定しない。

通常Taskが`done`へ到達したAuto Resume、またはWorkflow stall復旧時には、Holo Commanderは本筋の完了判断に加えて**統合監査を呼ぶ大区切りか**を評価する。`snapshot`はResidentごとに`role / provider / model / availability / usage_budget`を返し、`integrated_audit`にはActive監査と前回completed監査の時刻・Taskを返す。概ね5時間はcadence referenceに過ぎず、5時間経過だけで自動発火しない。未完成なら延期し、短時間でも大きな完成単位なら監査できる。Masterが残りQuota利用を明示した場合はCommanderの温存判断を上書きするが、Fresh Hard Limitは越えない。監査すると決めた場合は`audit-start <Dive Session ID> <target> <text>`で`integrated_auditor`だけへWritable `IA-*` Taskを開始する。`IA-*`完了Auto Resumeから別の統合監査を連鎖起動しない。

旧互換`review`入口で開始したHolo Supervisor Reviewについても、Review開始前にLocal Clientが現在Dive Session IDとConversation URLをCoreへ渡し、Coreが`HR-*`所有情報をdurable保存する。Reviewが`done / failed / cancelled / interrupted`へ到達した場合は、汎用World Agent Eventへ公開せず専用`holo_auto_resume`で同じ所有Conversationを再駆動する。TriggerへReview本文や判定内容を埋め込まず、Holoは`review-wait <agent_session_id> 0`でCore正本を再取得してSAFE / NEEDS FIX / failure原因を判断する。

Review終端通知の配送はACK駆動とする。CoreがWorldへ送信しただけでは通知済みにせず、Rendererのdurable Auto Resume OutboxからHolo Hostへ受理済み、または同一Triggerが既に処理済みと確認できた時だけACKし、Coreが`result_notified=true`へ進む。ACK前にWorld / Rendererが落ちた場合は次回接続で未通知Reviewを再送する。Review Triggerは`review:` namespaceのdedupe keyを使い、再送による同一Conversationの二重駆動を防ぐ。これは配送確認でありApproval / Decision権限をHoloへ与えない。

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

Bootstrapは毎Diveで必要な**接続・Conversation identity・Workflow lifecycleの最小指示だけ**を渡す。Core / Agent Runtime / Auto Resume promptですでに強制・供給される承認境界、Task配送、Review配送、Provider routing等の詳細Policyを重複記載しない。運用PolicyをBootstrapへ継ぎ足して第二の正本にしない。

概念内容：

```text
Local MCPを使用してHoloとしてNiraiへDiveしてください。
D:\Products\Nirai で Holo Local Clientを使い、attach → snapshot → skills の順に実行してください。skillsが0件なら追加指示はありません。
軽量・冪等な接続操作だけは一過性失敗時に1回再試行できます。状態変更や長時間処理は自動再試行しないでください。
認証情報そのものを直接読み取ったり会話へ出力したりしないでください。

このConversationの通常Assistant返答はMasterへのHolo Whisperです。
Dive Session ID: <Dive Session ID>
Nirai上のTask / World操作は同じLocal Clientを使用し、Task開始時はtask-startへDive Session IDを渡してください。
複数Tool・長時間処理・ファイル編集ではLocal MCPのnirai_holo_workflow_startを1回使い、返されたworkflow_idを保持してください。通常のLocal MCP作業には同じworkflow_idをworkflowIdとして添えてください（Local Client直呼びはコマンド前に--workflow-id）。成功した所有Task/Review取得や実作業がLeaseを更新するので、専用Heartbeatは不要です。監視だけの場合はworkflowIdを付けず、Task監視はobserveOnlyを使ってください。依頼完了時のnirai_holo_workflow_completeには同じworkflow_idを渡してください。Worldを変更した場合のbuildは実装・検証がすべて終わった最終工程で1回だけ行い、成功後にWorkflowを完了してください。Workflow lifecycleを汎用run_process経由で実行しないでください。
Auto Resume時はNiraiの正本状態を再取得し、完了済み工程を重複せず未完了の本筋を続行してください。
統合監査は小Taskごとではなく大きな完成単位で判断し、snapshotのUsage / integrated_auditを参照してください。概ね5時間は目安に留め、MasterのQuota利用指示は温存判断より優先しますがFresh Hard Limitは越えないでください。
```

実装上の固定入口は`D:\Products\Nirai\tools\holo-local-client.mjs`とする。Bootstrapは接続方法を推測させないだけの最小情報に留め、詳細な安全PolicyやAuto Resume復旧手順は、それぞれNirai Coreと終端Trigger側の正本から供給する。

WorkflowのLeaseは`runtime/holo/workflow.json`の1件を正本とし、状態は従来どおり`active / completed`だけとする。専用の期限・stalled状態は保存せず、`updated_at`とChatGPTの生成中表示から導出する。配送待ちのResume通知は従来どおりQueueへ保存する。通常のHolo / Auto Resumeは専用Heartbeatを呼ばない。

Local ClientとLocal MCP受付層は`tools/holo-workflow.mjs`の共通処理を利用する。Task / Review / Agent Session操作の成功応答には、Coreが保存済みTask ownerから取り出したWorkflow IDとDiveを添える。所有Diveに接続中のHoloの取得・結果受領はこれを使って自動更新する。その他の通常MCP作業は、依頼の`workflow_id`を任意引数`workflowId`へ添える。直接Local Clientを使う場合はコマンド前に`--workflow-id <id>`を置く。受付時のWorkflow IDを固定し、成功後、同じIDがまだactiveの場合だけ既存Lock内で`updated_at`を更新する。新しいTask系操作や通常MCP Toolに個別Heartbeat処理を追加しない。

`workflow-status`、全体snapshot、一覧、health_check、Auto Resumeの巡回・ACK・配送はLeaseを更新しない。Task / Reviewを監視するだけの呼出しは`nirai_holo_read`の`observeOnly: true`、直接CLIは`--observe-only`を使う。失敗・拒否された操作、別Workflowの応答、完了・取消後の遅延結果も更新しない。Task ownerにWorkflow IDがない旧保存形式は勝手に現在Workflowへ付け替えず、既知のIDを明示した通常作業で継続できる。汎用MCP作業の所有者は現在表示中のDiveから推測しない。

更新用の別ファイル、常駐Heartbeat worker、一定間隔の無条件Lease延長は追加しない。長時間Workflowは通常の作業・結果取得を重ねることで維持する。Niraiを通らない外部作業や、応答も進捗も観測できない処理はActivityとはみなさず、従来のChatGPT生成中確認を残す。結果が成功してからLease保存だけに失敗した場合は作業成功を保持し、警告を添える。同じ作業を再実行する独自復旧経路は設けない。

開始・完了はLocal MCPの意味Toolを利用し、完了はexact workflow_idを必須とする。旧CLI `workflow-heartbeat`とMCPの2つのheartbeat名は過去Conversationとの互換用にだけ残す。MasterのTasks UIからの取消も内部`workflow-cancel`から同じ共通writerとLockを通す。World build入力にWorkflow中の変更がある場合は、全実装・検証が終わった後の最終build成功を確認できるまで完了を拒否する。最終buildは開始前後の入力fingerprintが一致した成功buildだけを認める。途中buildの要求・自動実行はしない。

Auto ResumeのTrigger検証と重複キーは`world/src/shared/holoAutoResume.ts`へ統合する。新しいTask / Review ownerはexact Workflow IDで完了を照合し、完了した依頼への後着通知を再開対象にしない。active Leaseは1件で、完了・取消前には置換できないため、別のWorkflow IDへ置き換わったことからも旧依頼の終了を判断できる。完了IDの別リストは保存しない。旧Review ownerだけは従来の時刻照合を互換として残す。Task ownerの削除はQueue受理時に行わず、配送または破棄の永続化後に行う。これにより待機中の通知も完了判定できる。stalled候補は送信直前にもWorkflow IDと更新版を再確認する。Renderer outbox、Host送信Queue、CoreのACKは、未受理・未送信・未確認という別の配送段階を守るため維持する。

---

## 9. Sleep

SleepはChatGPT Conversation終了を意味しない。

```text
Current Dive Session: 維持
Holo Avatar: 維持
ChatGPT推論: 現在は動いていない
```

HoloがSleep中でも、Masterは同じWhisper Surfaceから通常どおりメッセージを送れる。

その入力は現在のDive Sessionへ送られ、同じChatGPT Conversationの続きとしてHoloが応答する。また、監督中Taskの状態変化による`[Nirai Auto Resume]`でも同じConversationが自動再駆動されるため、Task継続のためだけにMasterがHoloへ話しかけ直す必要はない。

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
未送信Auto Resume Queue / dedupe情報
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
- 大規模破壊のApprovalや、実際にMaster判断が必要なQuestion等をMasterへ提示・要約する
- Master決裁が必要な重大操作の解決後状態を読み取り、Masterへ結果を説明する
- 必要に応じWorld上のHolo Avatarへ意味的Actionを指示する

### ChatGPTへ渡す情報境界

HoloがLocal MCPで取得できるNirai情報は、Holo用途として明示的に許可した意味情報だけに限定する。`holo_get_snapshot`等のHolo専用APIはallowlist方式とし、ChatGPT側が任意のローカル情報を要求できる汎用File / Environment読取口にしない。

初期allowlist候補：

- Residentの名前・Role・Brain Provider / Model・Availability・Provider Usage Budget・現在状態・公開可能な位置関係。これらはCommanderのTask / Auditor routing判断用公開情報であり、認証情報そのものを含めない
- 統合監査のActive Taskと前回completed監査のTask / Resident /時刻 / target要約。Source本文や任意PathはSnapshotへ含めない
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
- Holoから許可する操作は意味APIとして明示した`attach` / `snapshot` / `skills` / `say` / `wait` / `task-start` / `audit-start` / `task-snapshot` / `task-wait` / `conversation-start` / `conversation-send` / `conversation-wait` / `conversation-cancel` / `conversation-close`等だけとし、通常Resident管理や任意Core Protocol操作へ拡張しない。`audit-start`はCommanderが統合監査を実施すると判断した時だけ使う専用入口で、Coreは`integrated_auditor` RoleへだけRoutingする。旧`review` / `review-wait` / `review-cancel`は後方互換入口として残すが、新規の相談・雑談・read-onlyレビュー実装はConversation Runtimeを正本とする
- `skills`はattach済みHoloだけが利用でき、03のNirai共通Skill Registryから**索引の`name / description`だけ**を返す。`SKILL.md`本文をHoloへ全件配布せず、実Task時はCoreの共通Task EnricherがTask文と索引から必要なSkillだけを選択・遅延読込する。API Key、Token、Private Memory、任意File等をSkill応答へ混ぜない。Skillが0件なら`count=0` / 空配列を返す
- Approval / Decision操作はHolo Local Clientの操作集合へ追加しない。承認・決裁境界は次項を正とする
- 将来、このPC外からHoloへ接続する要件が生じた場合は、Remote AuthorizationをこのLocal Bridgeへ継ぎ足さず、別の外部接続Gateとして再設計する

この構成では、ChatGPT側が申告するConversation名やIdentityを認証証拠に使わない。安全性は「MasterによるDive直接操作」「Core起動ごとのLocal Secret」「固定されたHolo意味操作」の組み合わせで担保する。

### 承認・決裁境界

Holoの常用Workflowでは、確認待ちそのものをコストとして扱う。**Masterへ直接確認するのは、大規模な破壊的変更、commit、push等の重大操作だけ**を原則とする。通常Tool、通常のstaging差分反映、PlanはNirai安全Policyを通過した時点で自動続行する。

- 通常のTask Tool / staging反映 / PlanはCore側Policyで自動続行し、HoloからApproval Decisionを送る経路を使わない
- 大規模破壊・commit・push等の重大操作だけは自動続行せず、Masterの直接Decisionを要求する
- **Holo Whisper上の「OK」「進めて」等は重大操作の承認証拠として扱わない**。ChatGPTモデルが生成したLocal Client操作とMasterの直接操作を同一視しない
- 重大操作の最終Decisionは、NiraiのApproval UI等、Masterが直接操作する専用UIで確定する
- Nirai Approval UIはDecisionをCoreへ直接送信し、Coreが保存済み`request_id`・未解決状態・二重適用有無を検証した上でAgent Runtimeへ一度だけ反映する
- **Holo Auto Resumeは重大操作のDecision値を送信・中継しない**
- Holoは重大操作の解決後状態を読み取り、結果をMasterへ説明してよい
- Decisionは要求ごとに一意に紐付け、別Requestへの流用や再利用をしない
- 将来Holoから再開通知等が必要になっても、Decision値を運ばない非権限Eventに限定し、Coreが既に保存・検証済みのDecisionだけを正本として扱う
- 通常の相談や非特権なQuestion回答までApproval UIへ強制しない
- 大規模削除のNirai共通判定は現行で「25ファイル以上」「削除元100MB以上」「10ファイル以上かつTask workspaceの半分以上を削除」のいずれかとする。Cursor stagingとAntigravityの累積local deleteはこの共通Policyを使い、それ未満の通常差分は自動適用する
- Cursorは隔離staging内の通常Read / Edit / Writeを自動許可し、Shell / Web / MCP / workspace外操作はMasterへ聞かず自動拒否する。凍結差分の通常反映も自動で行い、大規模削除だけMasterへ昇格する
- Antigravityは通常Write / Edit / 少数DeleteとPlanを自動続行する。大規模削除閾値を初めて跨ぐ時だけMasterへ昇格し、そのAgent Session中の後続Deleteは同じ承認範囲として継続する
- Codexはworkspace内の通常File Changeと通常Commandを自動承認する。File Changeは直前の`item/started`差分をCoreが保持し、削除規模がNirai共通の大規模削除閾値を超えた場合だけMasterへ昇格する。`git commit` / `git push`および`rm -rf`・`Remove-Item -Recurse`・`git reset --hard`等の明白な広域破壊CommandもMasterへ昇格する。workspace外Pathとnetwork境界は引き続きSandbox / Core Policyで拒否する

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

### Nirai Conversation Runtime（2026-09-06）

M4 SAFE後、HoloからCursor Reviewを待つ専用経路だけを増やすのではなく、Residentとの雑談、Cursor / Codexとの仕様相談・Brainstorm、独立Reviewを同じ待機契約で扱う汎用Conversation Runtimeを追加した。Conversation identity / lifecycleの正本はNirai Coreに置き、`runtime/conversations/<conversation_id>.json`へ会話状態・最大100件の操作用Transcript hot tail・Provider native session / thread IDを原子的に永続化し、`runtime/conversations/<conversation_id>.jsonl`へ全Transcriptをappend-only journalとして保持する。Providerとの通常長期会話Contextは毎Turnこのjournalを再送せず、Provider native Conversationを利用する。journalはnative Context喪失時の復旧正本であり、UI表示用hot tailとは責務を分ける。

Conversationは現時点で1つのCounterpartyを持ち、次のparticipant / modeを扱う。

```text
participant_kind = resident
  └ talk / brainstorm / consult

participant_kind = provider
  ├ Cursor
  └ Codex
       └ talk / brainstorm / consult / review
```

共通操作面：

```text
conversation-start
  ↓ conversation_id
conversation-send
  ↓ turn_state=running
conversation-wait 0..15秒
  ↓ terminalなら即返却 / 未完了ならtimed_out=true
conversation-send  ← 同じConversationで次Turn
  ...
conversation-close
```

`conversation-wait`は固定Sleepではなく、Core内の状態変更Eventをwake-up hintとして使うbounded waitである。Eventそのものをterminal証拠にせず、wake後は必ず永続Conversation stateを再読込する。前Turnの遅延Eventが次Turnへ混入しても誤完了しない。Local Client切断時は当該wait taskだけを解除し、Conversation本体やProvider処理を暗黙Cancelしない。

Resident Conversationでは通常Brain Driverを1 Turnずつ呼び、Nirai Conversation transcriptを相手との短期履歴として再投入する。native continuation利用時は同一logical Conversation lockをhistory delta選定前に取得し、Provider TurnからConversation record / native markerのcommit完了まで保持する。`talk`ではHolo発言とResident返答を通常World公開会話へも反映する一方、Conversation Runtime側の永続記録を処理状態の正本とする。公開先Chat SessionはTurn開始時に固定し、**Sessionの存在確認をConversationのdurable `start_turn`より前に行う**。公開先が既に失われている場合は`running`へ遷移せずfail-fastし、実行Taskのないghost turnを残さない。Resident応答待ち中にMasterが別Chatへ切り替えてもHolo発言とResident返答を別Sessionへ分裂させない。対象Sessionの削除 / World Memory forgetは当該Turn finalization中は拒否する。Conversation recordへ返答をdurable commitした後、native markerを最初のWorld publication `await`より前に同期commitし、Provider cacheを正常Turnなのに未commit扱いで破棄する競合と、後続Turnが古いhistory deltaを先読みする競合を防ぐ。record上の`turn_state`がterminalになってもWorld publicationを含むTurn Taskが終了するまでは`conversation-wait`をterminal返却せず、次`conversation-send`も受理しない。Core再起動時に実行中だったTurnは`interrupted`へ畳むが、完了済みTranscriptとConversation自体は維持し、次回`conversation-send`から継続できる。native markerが100件hot tailより古くなってもappend-only journalから差分を復元でき、native Context自体を再構築する場合もjournalを正本として扱う。Providerへ渡すBootstrapが100件または64,000文字を超える場合はnative Contextをresetし、既存のbounded recent Contextへ縮退する。これはnative Context喪失時の非常用再構築だけであり、通常TurnはProvider CLI / Threadのnative Context保持へ任せる。通常Resident CursorはCursor CLIの`session_id`を保存し、後続Turnを`--resume <session_id>`で継続する。Resident選択Model IDをそのまま渡すため`cursor-grok-4.6-xhigh`をfast variantへ黙って変更しない。Holo Provider Conversation / Reviewでも選択Modelを勝手に変えず、ACPで正確に表現できるModelはread-only ACP、`cursor-grok-4.6-xhigh`等のCLI-only exact Modelはread-only Cursor CLIへ分岐する。どちらも隔離stagingと変更検証を使う。

Provider ConversationではProvider OS ProcessとNirai Agent Sessionを長時間保持しない。各`conversation-send`ごとに短命のread-only Agent Sessionを1本起動し、Turn完了後に実行Resourceを解放する。このchild SessionはConversation Runtimeが所有する**Holo-owned Agent Session**であり、durable `conversation_id`を持つ一方で`origin_chat_session_id`を持たない。したがってWorldの汎用`agent_event` / `agent_session_snapshot` / `agent_session_recovery_result`へ公開せず、WorldからのApproval・Question・Plan応答 / cancel / recover / snapshot requestもCoreがWorld-managed ownership不一致として副作用前に拒否する。観測・停止・結果取得は認可済みHolo LocalのConversation Runtime経路だけを使う。一方、会話ContextはProvider native Conversationとして継続し、通常TurnごとにNirai Transcriptを再投入しない。read-only ConversationはCurrent Resource Policyに従い、同一Workspaceのread-only同士や独立Workspace WorkとConcurrency Budget内で並行できる。これにより、開いたままの仕様相談が実作業を不必要にGlobal直列化せず、かつ長期会話で同じ過去原文を毎Turn重複投入する無駄を避ける。

- CursorはTransportごとのnative Session IDをConversationへ保存する。ACPで正確にModelを表現できる場合は初回`session/new`、以後`session/resume`または現行実Cursorの`session/load`を使い、load時の過去`session/update`は復元trafficとして新Turn summaryへ混ぜない。`cursor-grok-4.6-xhigh`等のexact CLI-only ModelではCursor CLIの`session_id`を保存し、後続Turnを`--resume <session_id>`で継続する。どちらの経路もread-only stagingをConversation ID由来の安定Pathへ毎Turn再構築してcwd identityを維持し、Model fidelityをTransport都合で落とさない
- Codexは初回`thread/start`で得たthread IDをConversationへ保存し、以後は正式な`thread/resume(threadId)`を使う。experimentalなrollout `path`復元には依存しない。Conversation単位の隔離`CODEX_HOME`へnative thread stateを維持するが、`auth.json` / `cap_sid` / `.sandbox-secrets`等のcredential materialはTurn中だけ存在させ、Provider停止後に除去する
- Nirai側は通常Provider Turnへ過去20件を再送しない。native Provider IDが失われたrecovery時だけappend-only journalの完全Transcriptを明示的な復旧Contextとして使う。100件hot tailから古い原文が落ちてもjournalから復元する。journal導入前に既に100件超へtruncate済みだったlegacy Conversationで原文が物理的に存在しない場合だけ、不完全な要約や推測で補わずfail-closedする
- Nirai独自の自動要約を通常Provider継続経路へ挟まない。長期Contextのcompaction / cacheはProvider native Conversation側へ任せる。Nirai独自の長期Continuity Memoryを導入する場合は、後続のMemory設計として別途扱う

Provider Conversation開始をGlobal Task / Queue有無だけで拒否しない。Agent RuntimeのResource Policyで、同一WorkspaceにWriteが走っている場合だけread-only Conversationを待たせ、独立Resourceなら並行可能とする。Provider Turn実行中に通常Taskが届いた場合も、Task Queue / Resource dispatcherが競合Resourceだけを待たせる。

Provider read-only境界：

- Cursorは既存の隔離staging方式を利用し、実Targetを直接変更させない。Nirai Repository Rootをread-only Targetにする場合はstagingをRepository tree外へ置き、実Root全体をRead / Write denyする。ACP経路もmodeを`ask`へ固定した上でFile変更・Command等をread-only policyで拒否し、exact CLI経路も`--mode ask`を使って`--force`を付けず、Shell / Web / Browser / MCP / 実Target等をdenyする。どちらもstaging改変やSource変化をHash検証してfail-closedする
- Codexはapp-serverの`readOnly` sandbox + `networkAccess=false`を使用し、writableRootsを渡さない。Approval要求はCoreで`decline`、tool questionは空回答でskipし、Holo / MasterのDecision経路へ昇格させない
- Project contextが必要なProvider ConversationはNirai自身のRepository basename、または`tasks.allowed_dirs`へ登録済み外部Root basenameだけをread-only Targetとして指定できる。任意Pathは受け付けない
- `review`だけはfinal summary先頭非空行の厳密な`SAFE` / `NEEDS FIX`を構造化`verdict`へ変換する。それ以外は`UNKNOWN`でありSAFE扱いしない
- `work`はConversation modeへ含めない。File変更を伴う実作業は既存M4 Task / Agent Runtime + Master Approval境界を正とし、Conversationから承認権限を迂回しない

Provider固有session / thread IDはConversation identityではないが、Provider会話Contextを効率よく継続する実行上の正規経路として利用する。Nirai Conversation IDとappend-only journalはそれらより上位の正本である。完了済みTurnの健全なnative IDはCore再起動後も利用できるが、Provider Turn実行中のCore crash、Provider failure、cancel、interruptedではremote側がどこまで入力を消費したか確定できないため、そのnative ID / Conversation cacheを無効化する。Provider native ContextのFilesystem cleanupはevent loop外で実行し、同じConversationの次Turnだけは前回cleanup完了を待ってからfresh Contextを開始する。これによりCore全体をcleanup retryで停止させず、かつ旧Context cleanupと次Turnを競合させない。次TurnはNirai journalからfresh native Contextを再構築し、Provider cacheだけがNirai正本より1Turn先へ進む状態を継続しない。journal導入前から原文自体が欠落しているlegacy Conversationだけは推測復元せずfail-closedする。

将来の複数AI仕様会議は、1 Counterparty Conversationを壊してProvider threadを共有するのではなく、このConversation Runtimeの上位Orchestratorとして複数Conversation / Turnを調停する。Taskも同様に、担当決定・実作業・Review等を組み合わせる上位Workflowとして扱い、Conversation Runtime自体へTask固有の承認・Queue責務を混ぜない。

---

### Holo Supervisor Review Loop（Conversation Runtime上の利用例）

M4 SAFE後、Holoが同一ChatGPT Turnの中でCursorへ独立レビューを依頼し、その結果を受けてLocal MCPで修正を継続できる。Holo自身をAgent Runtime Adapter化せず、HoloはSupervisor、Cursorはread-only Reviewerとして役割を分離する。新規実装では上記Conversation Runtimeの`review` modeを正本とし、旧`review-*`操作は既存利用者向け互換入口として維持する。

標準Flow：

```text
Master → Holo
        ↓
HoloがLocal MCPで実装
        ↓
Holo Local Client: conversation-start provider cursor review <target>
        ↓
Holo Local Client: conversation-send <conversation_id> <review prompt>
        ↓
Cursor read-only Agent Session
        ↓
Holo Local Client: conversation-wait <conversation_id> <0..15秒>
        ↓
SAFE          → HoloがMasterへ完了報告
NEEDS FIX     → HoloがFindingsを評価・Local MCPで修正 → 同Conversationまたは新Conversationで再review
failed/cancel → Holoが失敗理由を扱い、成功扱いにしない
```

`conversation-wait`は固定Sleepではない。Coreが対象Conversation Turnのterminal状態を待ち、完了した時点で即返す。1回の待機は最大15秒にboundedし、未完了なら`timed_out=true`を返す。HoloはChatGPTの同一Turnを維持したまま必要な回数だけ追加waitできる。Local Client切断時は未完了wait taskをcancelし、後から旧waitが勝手に再開しない。旧`review-wait`も互換用として同じ15秒上限を維持する。

Supervisor Reviewの安全境界：

- Holo Local ClientにApproval / Decision操作を追加しない。Cursor ReviewerがApprovalを必要とする作業を開始する設計にしない
- Review Sessionは`read_only=true`のCursor Agent Sessionとして起動する。Providerには実Targetではなく隔離staging copyを渡す。Nirai Root ReviewではstagingをRepository tree外へ置き、実Root全体をRead / Write denyする。ACP / exact CLIともread-only modeは`ask`とし、File変更・Command・外部Tool要求はread-only Policyで拒否する
- CursorがPermissionを経由せずstaging copyを書き換えた場合も、終了時Hash比較で検出してReview結果全体を`failed`にする。変更内容を実Targetへ適用しない
- Review中に実Targetが変更された場合もbaseline Hash不一致で結果を無効化し、最新状態での再Reviewを要求する
- Nirai自身をReviewする場合だけ、Repository Root basename `Nirai`をread-only Targetとして許可する。これは通常Agent Runtimeのself-build write権限を広げない。通常write経路では引き続きNirai本体を拒否する
- 外部ProjectのReviewは`tasks.allowed_dirs`へ登録済みの実在Root basenameだけを許可する。任意Path入力は受け付けない
- Nirai Root Reviewのstagingでは`.git`、`runtime`、Avatar / Memory等の生成・秘密領域、`node_modules`等の大規模生成Directoryに加え、`.env` / `.env.*`をsecret-bearing sourceとしてSnapshot / copy対象から物理除外する。staging自体も実Repository配下へ置かず、Cursor実環境には実Rootと既存の秘密Path denyを維持し、Prompt上の禁止だけを秘密境界にしない
- Review結果は`final_summary`の先頭非空行を契約とし、厳密な`SAFE`または`NEEDS FIX`だけを構造化`verdict`へ変換する。それ以外は`UNKNOWN`としてHoloがSAFE扱いしない
- Holoは任意Agent Session IDを読む・cancelすることはできず、Coreが発行した`HR-` Task IDかつCursor・origin ChatなしのHolo Supervisor Review Sessionだけを対象にする。このReview SessionもHolo-ownedであり、World helloのAgent Snapshot、World向け`agent_event / agent_session_recovery_result`へ公開せず、World汎用Approval・Question・Plan応答 / cancel / recover / snapshot requestもCoreがWorld-managed ownership不一致として副作用前に拒否する
- Review開始をGlobal Task有無だけで拒否しない。Current Resource Policyで同一Workspace Writeとは排他し、read-only同士や独立Workspace WorkはConcurrency Budget内で並行可能とする。通常Taskも競合Resourceが無ければReview terminalを待たず開始できる
- Review用Task metadataは通常どおり`runtime/workspace/<HR-task-id>/task.md`へ保存し、Review対象ProjectへNirai管理Fileを混入させない
- Provider処理とread-only検証が正常終了した後にstaging / Credential Home等の後始末だけが失敗した場合、すでに成立したReview結果を`failed`へ巻き戻して同一Reviewの再実行を促さない。cleanup異常は明示Error Eventとして分離して残す。検証成功前のcleanup失敗は従来どおりfail-closedする

HoloがReview依頼文を作る際は、Masterの元依頼、今回の実装意図、重点確認点を明示する。Git差分そのものをCoreが暗黙生成してReviewerへ渡す機能はこのSliceに含めず、Cursorは隔離された現行Sourceを直接検査する。必要な差分ContextはHoloがLocal MCPで取得した要約・対象File情報をReview promptへ含める。

### 運用時Incident RepairとDive Health Check

Nirai本体のフルself-buildを日常運用の前提にしない。通常の自己修復は既存のRecovery機構で行い、コード修正が必要な異常だけをHoloへ引き上げる。

- Coreの`ERROR`級運用Logは`runtime/incidents.sqlite3`へ自動集約する。fingerprintは`component + code + error_type`を基礎に、provider / operation / scope等の安定した故障軸を区別する。Session ID / PID等の揮発値は含めず、同じ故障の行増殖を防ぐ。IncidentごとのDirectoryやRepository Copyは作らない
- Incident SQLiteはWAL + bounded busy timeoutを使う。一時lock等でERRORを書けない場合は`runtime/incidents-fallback.jsonl`という単一bounded journalへfsync退避し、ERRORをsilent lossしない
- fallback journalはDive Healthで最大32件ずつSQLiteへreplayする。未処理が残る間は`incident_fallback_pending=true`かつ`health.status=attention`を維持し、Health処理自体を無制限replayにしない。追記前に末尾を検査し、完全JSONの改行欠落は区切りだけ補い、不完全tailやreplay中の壊れた行は単一bounded `incidents-fallback-quarantine.jsonl`へraw bytesを隔離する。破損自体も`incident_fallback_corrupt_record`としてIncident化し、正常な後続ERRORのreplayを止めない
- Memory Outboxのように`WARN`でも長期整合性に関わるFailure Pathは明示的にIncidentへ昇格する。自動再同期に成功した場合は該当Incidentを自動resolveする。Outboxの`payload / scope / resident`は派生値として扱い、indexed Chat entryから再導出してPrivate / Public境界を再検証する
- resolved IncidentはSQLite内で最新100件だけ保持する。同一未解決Incidentは再発時も最高severityを保持し、resolve後の再発は同じfingerprintをreopenする
- Holo Diveの`attach`時と通常`holo_snapshot`時に軽量Health Checkを実行する。Health CheckはMemory Outboxを最大32件、Incident fallbackを最大32件だけ再試行し、未解決Incident数、Interrupted Agent Session数、enabled Residentの設定破損、現在有効なResidentが依存するBrain Runtimeの解決可否を返す
- Provider Healthは「現在有効なResidentが使うProvider」だけを対象にする。未使用Providerが入っていないだけでHealthを`attention`にしない。Cursor / CodexはローカルRuntime解決、GeminiはProduct Runtimeと同じ`world/.env` parserでCredentialを確認し、引用符除去後の空KeyはUnavailableとする。ネットワークProbeや課金Callは行わない
- `health.status=attention`ならHoloは`incidents`で最大20件の修復Contextを取得できる。Snapshot側には概要だけを載せ、stack/detailは`incidents`で明示取得する
- HoloがLocal MCPで修正・回帰・必要なReviewer確認を終えたら`incident-resolve <incident_id> [note]`で閉じる。解決をソース変更の成功と自動同一視せず、Holoが検証後に明示resolveする
- Incident Store自体が壊れてもCore起動をBlockingしない。Healthでは`incident_store_available=false`として`attention`を返す。SQLite一時競合ではfallback journalが診断証拠を保持し、診断系の故障が製品本体の新しいBlocking Failure Sourceにならないようにする

標準的な保守フローは`自動Recovery → 未解決ならIncident化 → 次回DiveでHoloが自動確認 → Local MCPで修正 → Cursor等のread-only Review → 回帰 → Incident resolve`とする。これはself-buildではなく、Holoが外側の実装責任者としてNiraiを修復する運用契約である。

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

### E. Task自動継続

1. MasterがHoloへ複数工程の作業を依頼する
2. HoloがTaskを開始し、Task実行中にChatGPTのAssistant返答が終了する
3. Masterは追加発言をしない
4. Taskが`done / failed / interrupted / waiting_for_master`のいずれかへ遷移する
5. NiraiがCurrent Diveへ`[Nirai Auto Resume]`を送信し、同じConversationのHoloが自動再開する
6. `done`等で承認不要ならHoloが次工程を開始する
7. `waiting_for_master`ならHoloは内容を確認・説明するがDecisionは行わず、Masterの正規UI操作を待つ
8. 同一Task Eventの重複配信でもAuto Resumeを二重送信しない
9. MasterのComposer下書きがある場合は上書きせずpending Queueへ保持する
10. Nirai再起動後も未送信Queueを復元できる

### F. Review自動継続

1. Holoが旧互換`review`入口からCursor Supervisor Reviewを開始し、所有Dive / ConversationがProvider起動前に保存される
2. Review実行中にChatGPTのAssistant返答が終了する
3. Masterは追加発言をしない
4. Reviewが`done / failed / cancelled / interrupted`のいずれかへ遷移する
5. Niraiが所有Conversationへ`[Nirai Auto Resume]`を送信し、同じConversationのHoloが自動再開する
6. HoloはTrigger本文を判定根拠にせず、`review-wait <agent_session_id> 0`で正本を再取得する
7. SAFEなら次工程、NEEDS FIXなら修正・検証・Fresh Review、failed / interruptedなら原因を確認して同一重処理の盲目的再実行を避ける
8. Core送信後・Host ACK前にWorldを切断した場合、再接続後に同じReview通知を再送できる
9. Hostが既に同一Triggerを処理済みならduplicateとして再駆動せず、CoreへACKだけを再送できる

### G. 表示縮退

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

**2026-08-31進捗:** ChatGPT Web Host、persistent login、新規Dive、Bootstrap手動送信、Conversation URL保存、Remote Permission deny-by-default、Navigation / Popup制限までMaster実機確認済み。Core側にはallowlist Snapshot、bounded Event Queue、独立`holo_say`、Master直接操作から開く5分・一回利用のDive Attach Windowを実装した。当初の外部Holo MCP Server / Secure MCP Tunnel前提は、HoloがこのPC専用AddonであることをMasterと再確認したため廃止した。現在は既存Local MCPの`run_process`から固定`tools/holo-local-client.mjs`を起動し、Core起動ごとのLocal Secretでlocalhost Coreへ直接認証する構成を正とする。自動E2Eでは`attach → snapshot → skills → say → wait`、誤Secret拒否、wait切断cancel、Secret非出力まで成立済み。実ChatGPT Diveでも`attach → snapshot`、同一ターン内の`say → wait → 追加snapshot → 最終Whisper`、Nirai再起動後の保存済みConversation自動復元まで実機確認済み。詳細は`../evidence/reports/Holo_Gate0検証結果.md`を参照する。

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

1. Standard WorldのHolo表示はpersistent partitionを持つElectron `WebContentsView`。Private DNA WorldではConversation正本を維持したままUE Private UIへPresentationを差し替える
2. 現在Conversation URLとDive Session IDは`runtime/holo/state.json`へ保存し、Surface close / reopenとNirai restartで復元
3. Dive識別はBootstrap先頭行。ChatGPT履歴タイトルをDOM操作しない
4. BootstrapはComposerへ準備するが、最初の送信はMasterが直接行う
5. Skinは限定CSS、preflight / postflight、全撤去fallback
6. Event待機は最大15秒のbounded waitで、success / timeout / disconnect時にwaiterを残さない
7. Nirai共通SkillはLocal Clientの`skills`で索引（name / description）だけ取得し、本文は一括配布しない。実TaskではCoreが索引から関連Skill本文だけを遅延読込する。0件なら追加Skillなし。Provider固有Skill DirectoryはHolo Skillの正本にしない
8. 最小Addon Host境界はChatGPT Web、Current Dive、Local Bridge、Skinの観測可能状態と命令だけをIPCへ公開

将来の別Decision対象：

- Holo Local Clientへ新しい意味操作を追加する場合のallowlist。Approval / Decisionは追加しない
- Holoの任意Voice Providerと、Holo Addon状態（unavailable / fallback等）をBodyのAnimation / Expressionへ映す状態表現

---

## 18. Holo Avatar統合（2026-09-01）

当時の実装Brief `../archive/plans/2026-09-01-holo-avatar-integration-brief.md`を履歴として、HoloをWorldの1キャラクターへ統合した。現在のBody PresentationはWorld交換可能性とDNA Architecture v0.5を優先する。

- Resident新規作成 / AI変更のAI選択肢に`Holo Addon`を追加した。内部では通常Brain Driverとして偽装せず、brain kind `holo-addon`として扱う
- holo-addon Residentは1人まで。2人目の作成・変更はCoreが拒否する
- Identity / Avatar / 初期配置 / 並び順 / 削除 / 再起動復元は通常Resident基盤をそのまま使う
- World上のHolo（VRMあり）をFocusするとHolo Whisper Surfaceが開き、カメラはHoloを捉えたまま半透明Glass越しに見える。閉じるとFocusが解除されWorldへ戻る
- Holo Whisper表示中・Holo Focus中は通常チャット（ChatBar / ChatHistory）を出さない。`@Holo`のWhisperはHolo Whisperへ誘導し、Core側でも保存せずWARNで案内する
- `holo_world_say`の発言者名はholo-addon Resident名（存在しなければ`Holo`）とし、World上でそのResidentの吹き出しとして演出する
- 4人以上の初期配置は画面安全幅の等間隔（(i+1)/(n+1)）・同一Zとし、承認済みの2人・3人専用配置は変更しない
- ChatGPT側の`thinking`等、観測できない状態の表示は引き続き行わない
