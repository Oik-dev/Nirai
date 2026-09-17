# Nirai v2 基本設計書

> 本書はNirai v2の現行仕様を定義する。設計・実装・テスト・文書運用の共通原則は`WORLD_RULES.md`を唯一の正本とし、本書へ重複記載しない。

## 1. 目的

Nirai v2は、現行Niraiで複雑化したWorkflow、Auto Resume、Conversation所有権、実行単位管理、Agent実行制御を再設計し、AIが継続的かつ自律的に活動できる環境を構築する。

現行Control Planeを継ぎ足して修正し続けるのではなく、新しいControl Planeを構築し、既存Niraiから安定している部品のみを再利用する。

再利用候補には以下を含む。

- Agent Runtime
- Cursor / Codex / Astra等のAgent連携
- Provider接続
- Memory
- Residents
- Local MCP
- セキュリティ機構
- World
- VRM
- 独立性の高いRenderer部品
- 安定済みUtility

---

## 2. ゴール

> Niraiに住むAIが、複数の仕事を自律的かつ安全に継続し、人間の生活空間を侵食せず、必要な時だけ自然に協調できる環境を作る。

Niraiは単なるTask Runnerではない。

AIが活動し、考え、協調し、仕事を継続する場所であると同時に、MasterとAIが共存する空間とする。

---

## 3. Nirai v2の基本構造

Nirai v2では以下の概念を明確に分離する。

### Say

MasterとAIが日常的に会話する生活空間。

業務ログや長大な進捗は流さない。

### Dashboard

AIの仕事を観測・制御する司令盤。

Task、Step、AI稼働状態、利用率、残Limit等を集中表示する。

### Task

Masterが認識・作成する一つの仕事。

例：

- DNA Chapter01 HomeBase再現
- Nirai v2設計
- Serina Memory改善

Taskは専用Chat、Pause / Resume、破棄、Archiveを持つ。

### Step

Taskを構成する具体的な内部工程。

Holo、Cursor、Astra等が実行する作業単位。

例：

- 室内Material検証
- Asset探索
- 最終監査

StepはAIがTaskを進めるために分解・管理する内部実行単位であり、Masterが常に手動管理する対象ではない。

### Attempt

Stepを実際に実行した一回の試行。

### Agent

Holo、Cursor、Astra等、Stepを実行または支援するAI。

### Supervisor

Running中のTaskが止まらないよう管理する内部制御。

---

## 4. v2固有の最重要不変条件

Nirai全体の開発原則は`WORLD_RULES.md`に従う。本章ではv2の構造上、必ず守る不変条件のみを定義する。

### 4.1 ChatとTaskを完全に分離する

以下はTask状態に影響しない。

- Chat画面のReload
- Chatを閉じる
- ChatGPTの生成停止
- Conversation切替
- ChatGPT側のUI変更
- DOM変更
- 返答生成の中断
- Dashboardの開閉

Chatの操作からMasterの意思を推測してはならない。

Task状態を変更できるのは、Nirai自身の明示的Commandのみとする。

### 4.2 状態の正本を一つにする

Task、Step、Attempt、実行状態の正本は単一の永続Control Storeに置く。

同じ状態を複数のJSON、Renderer Store、Conversation、Agent Runtime等へ独立して保存してはならない。

UI、Supervisor、Agent Runtime、Dashboardはすべて同一の正本を参照する。

実装はトランザクションを利用可能な単一DBを基本とし、SQLiteを第一候補とする。

---

## 5. Task

TaskはConversationから独立して永続する。

Conversationが閉じても、ChatGPTが落ちても、Niraiの表示画面が変わってもTaskは継続する。

### Task状態

- `Running`
- `Paused`
- `NeedsInput`
- `Completed`
- `Failed`
- `Cancelled`

原則としてユーザー向けTask状態はこの6種類に限定する。

TaskはMasterに見える主要単位であり、DashboardのTask一覧もTask単位とする。

---

## 6. Step

Taskは複数Stepを持てる。

Step間には必要に応じて依存関係を定義できる。

依存関係のないStepは並列実行可能とする。

### Step状態

- `Waiting`
- `Running`
- `NeedsInput`
- `Completed`
- `Failed`
- `Cancelled`

`Waiting`は前Stepや依存処理の完了待ち、`NeedsInput`はMasterの入力・判断待ちを表す。

RetryはStep状態を増やさず、Attemptとして管理する。

UIではTaskを展開した時だけStep一覧を表示し、普段はTask一覧を主役とする。

---

## 7. Attempt

AttemptはStepの一回の実行を表す。

保持情報例：

- Agent
- Model
- Provider
- 開始時刻
- 終了時刻
- 結果
- エラー
- Retry理由

同一Stepに対して複数の有効Attemptを同時実行してはならない。

新Attempt開始前に、以前のAttemptが終了済みであることを正本Storeで確認する。

---

## 8. 複数Task・複数Step

複数Taskを同時進行可能とする。

例：

- Task A: HoloがUE作業
- Task B: Cursorがコード探索
- Task C: Astraが監査

これらは互いに独立する。

一つのTask、Step、Agent、Providerの障害が他Taskへ波及してはならない。

---

## 9. AI同士の協調

Holo、Cursor、Astra等はTask内部で相互に会話できる。

例：

Holo → Cursorへ調査依頼

Cursor → Holoへ結果返却

Holo → Astraへ監査依頼

Astra → Holoへレビュー返却

これらはTask内部の`Collaboration Thread`として保存する。

Sayへ逐一流さない。

必要な場合のみDashboardから閲覧できる。

Agent間会話そのものはTask状態を書き換えない。

Task / Step状態を変更する正式な経路はControl Planeに限定する。

モデルが「完了した」と発言しただけでTaskをCompletedにしてはならない。

---

## 10. Task Supervisor

現行Auto Resumeの複雑な責務を廃止し、Task Supervisorへ統合する。

Supervisorの基本責務は次の一つとする。

> RunningなTaskに実行可能Stepが存在し、そのStepを担当する有効な実行者が存在しない場合、実行または復旧を行う。

SupervisorはChat状態を参照しない。

`Auto Resume`はユーザー向け概念として残さず、Supervisorの内部復旧動作として扱う。

---

## 11. Pause / Resume / 破棄 / Complete

### Pause

DashboardのTask ChatヘッダーからPauseすると、`Task = Paused`となる。

Pause後は、

- 新Stepを開始しない
- 新Attemptを開始しない
- 自動復旧しない
- Supervisorによる次工程開始を行わない

現在実行中の安全な処理は、結果保存可能な区切りまで完了してよい。

そのStepが完了しても次Stepへ進まない。

### Resume

Task ChatヘッダーからResumeすると、`Paused → Running`へ遷移する。

正本Storeに保存された未完了地点からSupervisorが再開する。

Chat履歴から再開地点を推測しない。

### 破棄

Taskを展開した最下部に、ゴミ箱アイコン付きの`このタスクを破棄する`操作を置く。

押下時は`本当に破棄しますか？`と確認し、Masterが確定した場合のみ`Task = Cancelled`とする。

進行中Stepへ停止要求を送り、以後Supervisorは再開しない。

Cancelled TaskはARCHIVEへ自動移動し、再起動後も復活しない。

### Complete

必要Stepがすべて正常終了した場合のみ、`Task = Completed`とする。

Agent自身の発言だけを根拠にCompletedへ変更してはならない。

---

## 12. NeedsInput

Masterの判断が必要な場合、`Task = NeedsInput`とする。

例：

- 破壊的操作の承認
- 設計判断
- 必要情報不足
- Retry上限到達
- AIだけでは判断すべきでない重大事項

NeedsInputはDashboardで明確に表示する。

他Taskはそのまま進行可能とする。

---

## 13. Dashboardの役割

DashboardはNirai v2における作業管理の中心UIとする。

別画面へ遷移する管理画面ではなく、World上に常駐する。

Nirai既存のガラス調UIデザインを継承する。

Dashboardは「管理画面」ではなく、Niraiで今何が起きているかを見る窓とする。

---

## 14. Dashboard格納状態

通常時、Dashboardは画面端へ格納されている。

WorldやSayを邪魔しないサイズとする。

格納状態では最小限の状態件数だけを表示する。

表示は以下の4行に固定する。

- 状態ランプ + 件数 + `RUN`
- 状態ランプ + 件数 + `CHECK`
- 状態ランプ + 件数 + `PAUSE`
- 状態ランプ + 件数 + `COMPLETE`

`RUN`はRunning Task数、`CHECK`はNeedsInput Task数、`PAUSE`はPaused Task数を表す。

`COMPLETE`は累計完了数ではなく、Dashboard格納中に新しくCompletedへ到達したTask件数のみを表す。CancelledはCOMPLETEへ含めない。Dashboardを展開した時点で確認済みとしてCOMPLETE件数を0へ戻す。

格納表示にはNiraiアイコンや開閉矢印を置かない。格納パネル全体をクリックまたはタップするとDashboardを展開する。

---

## 15. Dashboard展開状態

### 横画面

Dashboard全体を画面左側へ寄せ、右側にWorldの余白を確保する。

基本構造を、

`左：Task / Archiveペイン`
`右：Chat`

とする。

横画面の幅比は概ね`Task / Archive 0.7 : Chat 1.0`とし、Dashboard右側にWorldの余白を確保する。

右側のWorld余白は将来のResident Focus領域として使用する。VRM等のResident表現を導入した際は、現在ChatしているResidentをこの領域へ優先表示し、DashboardがResident表示を覆わない配置を基本とする。

```text
┌──────────────────────────────────────────────────┐
│ Nirai      Holo ●   Cursor ●   Astra ○          │
├───────────────────────┬──────────────────────────┤
│ TASK                  │ CHAT                     │
│ ● DNA HomeBase        │ DNA HomeBase   Pause Resume [+]│
│   Running             │                          │
│   ├ Material検証 Holo │ Master / Holo conversation│
│   ├ Asset探索   Cursor│                          │
│   └ 最終監査    Astra │                          │
│ [🗑 このタスクを破棄] │                          │
│ ARCHIVE               │                          │
└───────────────────────┴──────────────────────────┘
```

### 縦画面

Dashboard全体を画面下部へまとめて配置し、画面上部にWorldの余白を確保する。

Task / Chatを画面全体の上下へ分離せず、Dashboard内部で横並びの一体レイアウトとして下部へ収める。

画面上部のWorld余白は将来のResident Focus領域として使用する。VRM等のResident表現を導入した際は、現在ChatしているResidentを上部へ優先表示する。

横画面と縦画面で機能差を作らない。

レイアウトのみ変更する。

---

## 16. Dashboard UIの概念

Taskはカードを大量に並べるのではなく、状態順に並ぶアコーディオン式リストを基本とする。

例：

```text
Task A   進行中
Task E   進行中
Task C   判断待ち
Task B   一時停止
Task D   完了
```

状態はセクション分けではなく並び順へ使用する。

Taskを展開すると、そのTaskのStep一覧が入れ子で表示される。

Taskを展開した時点でCHATペインもそのTaskへ切り替える。

Taskが`Completed`または`Cancelled`へ到達した時点で、Active一覧からARCHIVEへ自動移動する。

Masterによる`閉じる`操作は不要とする。

---

## 17. Task作成とTask Chat

CHATヘッダー右端に`＋`を表示する。

新規CHAT / Taskの対象Residentは、Dashboard上部のResident一覧を直接押して選択する。選択中Residentは視覚的に識別できるようにする。

`＋`を押した場合、作成ダイアログを挟まず、選択中Residentを担当として新しいTaskを即作成する。

作成直後はそのTaskを自動展開し、CHATへ即座に切り替える。最初のMaster指示を送る前であれば、Resident一覧から担当Residentを切り替えられる。

新規Taskは最初のMaster指示を待つ状態とする。

MasterがTask Chatへ最初の指示を送信した時点で、選択されたResidentが内容を解釈しTask名と最初のStepを生成し、実行を開始する。

Task名入力のためだけの事前ダイアログは設けない。

Task ChatはTask状態の正本ではない。

Task ChatをReload、閉じる、生成停止、別Taskへ切り替えてもTask状態は変化しない。

---

## 18. SayとTask Chat

### Say

日常会話、雑談、自然なAIとの共存空間。

### Task Chat

特定TaskについてMasterとAIが会話する場所。

Sayへ業務の詳細ログを流さない。

Task Chatにも内部Toolログを無制限に流さない。

人間向けに要約された会話のみ表示する。

---

## 19. Task / Archiveペインの情報構造

TaskとArchiveは同じ階層・同じ見出し形式の2段アコーディオンとして扱う。

Task一覧行とArchive一覧行は同一のUIコンポーネントを使用し、状態ランプ、タイトル、補足、右端ステータス、行高、余白、角丸、背景、文字サイズを共有する。Task行には開閉矢印を置かず、選択時の発光・背景変化で展開状態を示す。

見出しは`TASK`と`ARCHIVE`のみとし、`進行状況`等の副見出しは置かない。

### Task一覧

通常状態ではTASK側を展開し、現在存在するTaskを状態順に並べる。

Task一覧が表示可能件数を超えた場合、Dashboard全体を伸ばさずTASK枠内のみスクロールする。Task行や展開中Taskを表示領域に合わせて圧縮してはならない。スクロールバーは表示せず、下に続きがある場合のみ一覧下端を薄くフェードしてスクロール可能であることを示す。

Taskを展開すると、その内部Stepを入れ子表示する。

Task一覧ではTaskの性質を`type`で分類し、行頭の分類アイコン表示に利用する。`type`はTask生成時の通常のAI出力に含め、分類専用の追加AI推論は行わない。取得できない場合は`general`へフォールバックする。

Task Typeとアイコンの対応表は単一の定数として管理し、Rendererの描画処理内で毎回定義・生成しない。

Stepには最低限、

- Step名
- 状態
- 担当Agent
- 現在地 / 短い補足

を表示する。

### Archive

Archiveの状態遷移、表示、保持期間は「29. Archive」を正本とする。Task / Archiveペインでは同一の一覧行UIを使用する。

---

## 20. Resident / AI Usage / Limits

上部にはResidentごとのステータスを横並び表示する。

Residentアイコンは不要とし、Online / Offlineの状態ランプと表示を持つ。Task状態とは独立して扱う。

各Residentには必要なLimit情報を表示する。

例：

- 5時間枠
- 1週間枠
- 1か月枠
- 残量%
- 残量ゲージ
- Reset時刻 / Reset日

Providerから取得できない値は推測せず、Unknownまたは非表示とする。

Resident表示は横に引き伸ばしすぎず、コンパクトなブロックとする。

Resident一覧は常に横1列とし、Resident数が増えてもDashboardヘッダーを縦方向へ拡張・折り返ししない。通常時は右詰めで表示し、表示領域を超過した場合のみ横スクロールへ切り替える。スクロールバーは表示せず、まだ右側にResidentが続く間だけ右端を薄くフェードしてスクロール可能であることを示す。マウス / ポインタのドラッグに加え、Resident欄上では通常のマウスホイール入力も横スクロールへ変換する。

Residentカードは新規CHAT / Taskの対象者選択も兼ねる。クリックしたResidentを次の新規Taskの担当とし、選択状態をカード上で示す。既に開始済みのTaskはResidentカード操作だけでは担当変更しない。

Residentは表示名とは別に不変のResident IDを持つ。Task / Step等からResidentを参照する場合は表示名ではなくResident IDを保存し、名前変更時にTask / Step全件を書き換える設計にしない。表示時のみResident IDから現在の表示名を解決する。削除済みResidentへの既存参照は破損させず、UI上は削除済みであることが分かるフォールバック表示を行う。

Dashboard上部の歯車は、画面中央にResident管理モーダルを開く。

モーダルには全Residentを横並びのResident列として一覧表示する。各Resident列は上から以下の順で表示する。

- 状態ランプ + 名前
- `Role` + 選択値
- `AI` + 選択値
- `Model` + 選択値
- `Avatar` + 選択値
- Prompt設定
- キャラクター削除

Role / AI / Model / Avatarは各項目名を常時表示し、値側をコンパクトなプルダウンとする。RoleはResidentごとに一つのRoleを持つ単一選択とする。AIとModelは別項目として管理する。

Holoは`AI = Holo Addon`とし、Addon内部でモデルを管理するためResident設定上の`Model = -`で固定し選択不可とする。通常Residentは、例として`AI = Cursor`、`Model = Grok4.6 xhigh`のようにAIとModelをそれぞれ選択できる。

Avatarもプルダウンで選択する。例としてHoloは`Lapan`、Cursorは`Mirdo`を使用する。

Prompt設定は各Resident列に常時表示する。押下時はモーダル内で編集せず、そのResident専用のローカルPromptテキストファイルを開く。PromptはResident IDに紐づく固定パスで管理し、Resident名変更でファイル参照が壊れないようにする。

`キャラクター削除`は各Resident列の最下部へ文字リンク相当の弱い表示で常時置く。押下時は即削除せず、中央の確認ポップアップを表示し、`キャンセル / 削除`の明示操作を要求する。

モーダル最下部にはResident個別設定とは別枠で`新規キャラクター追加`を置く。

利用量・LimitはDashboard本体で表示するためResident管理モーダルへ重複配置しない。音声設定はv2ではResident管理へ持ち込まない。

---

## 21. Dashboardからの操作

Dashboardから最低限以下を実行可能とする。

- Resident一覧から新規CHAT / Taskの対象者選択
- `＋`で選択中Resident宛のTask即作成
- Task選択 / 展開
- Task Chatへの自動切替
- Pause
- Resume
- Task破棄
- Failed Step Retry
- NeedsInput回答
- Step詳細確認
- Resident状態確認
- Collaboration Thread確認
- Archive確認

Pause / Resume / `＋`はCHATヘッダーへ置く。

Task破棄はTaskを展開した最下部にのみ置き、誤操作防止の確認を必須とする。

同じ操作を複数箇所へ重複配置しない。

---

## 22. Sayへの出力制限

Sayへ以下を大量出力してはならない。

- Tool Call
- Cursor探索ログ
- テスト全件ログ
- Agent内部ログ
- Collaboration Thread全文
- Retryログ
- Supervisorログ
- ファイル探索ログ

Sayに出してよいもの：

- 短い進捗
- Masterへの質問
- NeedsInput
- 重大な異常
- Task完了
- 人間向けの結果要約

---

## 23. Task Chatへの出力制限

Task Chatも実行ログ置き場にしない。

Task Chatには人間が読む意味のある情報のみ表示する。

詳細なAgent間会話やToolログはTask内部ログへ保存し、Dashboardから必要な場合だけ閲覧する。

---

## 24. 状態変更Command

状態変更はCommand経由に限定する。

例：

- `StartTask`
- `PauseTask`
- `ResumeTask`
- `CancelTask`
- `RetryStep`
- `CompleteStep`
- `FailStep`

RendererがDBを直接書き換えてはならない。

AgentもDBを直接書き換えてはならない。

---

## 25. Restart Recovery

Nirai / Core / World / PCの再起動後に状態を復元する。

### Running

有効Attempt確認後、必要なら復旧。

### Paused

Pausedのまま。

### NeedsInput

そのままMaster待ち。

### Completed

再開しない。

### Cancelled

絶対に再開しない。

### Failed

MasterがRetryするまで再実行しない。

---

## 26. 二重実行防止

以下が同時発生してもStepを二重実行しない。

- Supervisor発火
- Nirai再起動
- PC再起動
- Agent切断
- Provider切断
- Retry
- Dashboard操作

同一Stepには同時に一つの有効Attemptのみ存在できる。

---

## 27. Retry

Retry回数には必ず上限を設ける。

同じ障害を無限に繰り返してはならない。

上限到達時は、Taskを`NeedsInput`または`Failed`へ遷移する。

---

## 28. 実行ポリシー

NiraiのTask実行ポリシーは、全Task共通で`危険操作のみ確認`とする。

通常の調査、実装、検証、Agent間協調、再開等は自動進行する。

以下のような高リスク操作のみMaster確認を要求し、Taskを`NeedsInput`へ遷移させる。

- 大量削除
- 復元困難な上書き
- 重要な既存成果物の破壊的変更
- 外部サービスへの重大な書き込み
- 権限・認証・公開範囲等に関わる変更
- その他、取り返しがつきにくい操作

Taskごとの自律性設定は持たない。

Dashboard / Task Chatにも自律性選択UIを置かない。

これにより、設定項目と状態分岐を増やさず、Nirai全体で一貫した実行原則を維持する。

---

## 29. Archive

Taskが以下のTerminal状態へ到達した場合、Active一覧からArchiveへ自動移動する。

対象：

- Completed
- Cancelled
- 終了扱いとなったFailed

少なくとも`Completed`と`Cancelled`については追加操作を要求せず、状態確定と同時にArchiveへ移す。

### Archive表示

通常時は最新2件分の高さを常時表示する。Archiveが3件以上ある場合も高さは増やさず、その枠内でスクロールする。

`ARCHIVE`見出し押下時はARCHIVEを最大表示し、TASKは見出しのみへ畳む。`TASK`見出し押下時はTASKを最大表示し、ARCHIVEを2件分表示へ戻す。

ARCHIVE最大表示時も、表示可能件数を超えた場合はARCHIVE枠内でスクロールする。

Archive内では最低限、Task名、終了状態、終了時刻を確認できるようにする。

Archiveには必要に応じて、

- Task概要
- Step履歴
- Attempt履歴
- Collaboration Thread
- 実行ログ
- エラー
- 成果物参照

を保存する。

### Archive保持期間

Archive登録から90日経過したTaskは自動削除する。

運用上、3か月 = 90日として扱う。

Task専用のログ、一時成果物、Step履歴等も削除対象とする。

PJ本体や共有成果物は削除しない。

### ArchiveとMemory

Archiveは長期Memoryではない。

将来的に価値がある情報はMemoryへ昇格する。

`Archive削除`と`Memory削除`は別概念とする。

---

## 30. UIデザイン

Nirai v2でも現在のNiraiのガラス調デザインを継承する。

新しい管理画面だけ別デザインにしない。

DashboardもWorld内の一要素として自然に存在させる。

方向性：

- 半透明Glass
- 背景Blur
- Worldを完全に隠さない
- 必要な情報だけ浮かせる
- 常時表示部分は極小
- 展開時のみ情報密度を上げる
- AIの活動を「監視画面」ではなく「住人の活動状況」として感じられるUI

左上にはNirai v2ロゴ画像を使用する。

---

## 31. 旧Niraiからの移行原則（移行期間のみ）

旧Niraiからは、v2上の責務が明確で、旧Control Planeへの暗黙依存や独自の状態正本を持たず、新設計を複雑化しない資産だけを再利用する。

以下は持ち込まない。

- Chat DOMやConversation状態によるTask制御
- Conversation ownership / Master Stop / stopped_conversations
- 複数の状態正本
- 無限Auto Resume / Retry
- Agent発言を状態正本とする設計
- Sayへの長大な業務ログ

条件を満たさない既存機能は、必要な責務だけ分離して再実装する。

---

## 32. 受け入れ条件

最低限以下を満たすこと。

1. 複数Taskを同時進行でき、一つの障害が他Taskへ波及しない。
2. ChatのReload・停止・切替・閉鎖がTask状態へ影響しない。
3. Pause / Resume / Cancel / Restart Recoveryが正本Storeに基づいて動作する。
4. 同一Stepに複数の有効Attemptが同時存在しない。
5. NeedsInput、Failed、Retry上限が明確に管理され、無限Retryしない。
6. 複数AgentがTask内で協調でき、内部ログがSay / Task Chatを占拠しない。
7. DashboardだけでActive Task、Step、Resident状態、Limitを把握・操作できる。
8. Resident選択から新規Task作成までダイアログなしで行え、開始前のみ担当Residentを変更できる。
9. Resident設定で名前・Role・AI・Model・Avatar・Prompt・追加・削除を管理できる。
10. 横画面・縦画面ともWorldのResident Focus領域を残し、Dashboard内の機能差を作らない。
11. Task / Archive一覧は表示領域内でスクロールし、行や展開内容を圧縮しない。
12. Completed / Cancelled Taskは自動でArchiveへ移動し、Archiveは90日後に自動削除される。
13. 再起動後もRunning / Paused / NeedsInput / Completed / Cancelled / Failedの意味が保持される。
14. UI / Rendererは状態正本を持たず、状態変更はCommand経由に限定される。

