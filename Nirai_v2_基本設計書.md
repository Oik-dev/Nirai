# Nirai v2 基本設計書

> 本書はNirai v2の現行仕様の正本である。共通の設計・開発・文書運用原則は`WORLD_RULES.md`を参照する。実装順序と各段階の出口は、初期自走化の期間だけ`Nirai_v2_初期自走化計画.md`で管理する。

## 1. 目的

Niraiは、AI・長期Memory・Tool・外部サービス・Worldを自然に組み合わせて使う、Master専用のAI Hubである。

PCに例えるならHubはマザーボード、Memoryは内蔵ストレージ、AIは演算能力、Toolは周辺機器、World / Dashboardは表示・操作面に相当する。Holoは専用の接続方式を持つ重要な能力として接続する。同一Process内の責務は直接呼び出し、外部境界だけに必要なTransportを置く。

## 2. System Scope

Niraiは、Master / Residentの会話、Task実行、AI / Tool / Memory / World / 外部サービスを共通Hubから扱う。

個別能力はCapabilityとして接続し、利用可能状態・権限・実行結果をHubの共通契約へ収束させる。

## 3. 論理責務と物理構成

### 3.1 論理責務

| 責務 | 担当すること |
|---|---|
| Hub Core | 共通Command、状態保存、権限・安全確認、Capability接続 |
| Task Engine | 保存済み状態から実行許可と次のHolo Turn開始を判断する小さなHub内Module |
| Capability | AI、Tool、Memory等の個別能力。外部仕様をAdapter内部へ閉じ込める |
| Resident | 不変ID、Persona、使用AI、Model、Avatar等を束ねるIdentity |
| Local Memory | ローカルの長期記憶。Task制御とは独立したCapability |
| World / UI | Hub状態の表示とMaster操作。状態の正本を持たない |

### 3.2 採用構成

**Windows上のElectronアプリに、TypeScript製Hubを一つのNode子Processとして同梱する。HubはElectronの`utilityProcess`で起動する。**

| 実行場所 | 所有する責務 |
|---|---|
| Electron Main | Window / Tray、HoloのWebContentsView、Navigation・Permission制限、UI IPCの入口、Hubの起動・終了 |
| Node Hub子Process | Hub Storeの唯一のWriter、Command、Engine、Policy、Registry、Run実行の管理 |
| Nirai Renderer | Dashboard / Task管理 / Worldの表示。通常ResidentのTask Chatを表示し、Holo TaskではChatGPT native surfaceの外側にTask状態・Resume・Approval・Activity・成果物を表示する。sandbox有効、Node無効、限定preload経由でCommandを呼ぶ |
| Holo Web Adapter | ChatGPT WebをHoloのnative conversation surfaceとして表示し、Task binding、送信境界、生成観測、assistant Message記録、Provider固有操作の橋渡しを担当する。Node無効、contextIsolation / sandbox有効。Task判断・Master権限・汎用ローカル操作を持たない |
| 必要時だけ起動するWorker / 子Process | 大きなファイル処理、外部Command等。Hub DBを直接開かず、限定したRunの結果を返す |

画面・Holo接続とHubの言語・配布物を揃えつつ、DB処理やHub障害を画面のProcessから分離するため、この構成を採る。Hub本体はTypeScript / Nodeで構成し、Python資産を利用するCapabilityは必要な処理をCapability内部のWorkerとして呼び出す。

MainとHubは非同期MessagePort通信、UIはMainの限定IPCを経由する。Task判断はHubが所有する。Hubは`node:sqlite`の`DatabaseSync`を利用し、DB操作を短く保つ。重い探索や処理はHub / Worker側で扱い、MainはWindow・Provider表示・IPC境界に集中する。utilityProcessはProcess分離として利用し、OS-level sandboxとは区別する。

使用APIは[Electron utilityProcess](https://www.electronjs.org/docs/latest/api/utility-process)、[Node SQLite](https://nodejs.org/api/sqlite.html)に従う。依存版はv2のlockfileで固定し、Electron同梱Node上でDB・IPC・終了動作を確認して更新する。Hubの実行Runtimeは配布物に同梱し、Capability固有RuntimeはそのCapabilityが所有する。

### 3.3 配置と起動

- v2のSource / package / lockfile / build設定は`v2/`へ独立配置する。Main、Hub、shared契約、Renderer、Capability、Local MCP橋渡しをその配下へ置く。
- 製品Data Rootは`%LOCALAPPDATA%/Nirai-v2/`。Hub DB、Run成果物、ログ、接続情報、HoloのElectron userDataをここへ分けて置き、v1のRuntime / Login保存領域から分離する。
- 検証用Data Rootは明示指定した別Folderを使い、Data Rootごとに一つのHubが排他的に所有する。
- Data Rootは実在Folderの実パスへ解決してから排他名とDBパスを決める。junctionや別表記も同一実パスへ正規化する。Electron userDataもそのData Root配下へ設定し、製品・検証のCookie、localStorage、単一起動識別を分離する。
- 標準ランチャーはbuild後、既存のNirai-MCP Tunnel Profileを正本としてTunnel Runtimeを停止・再接続し、ready確認後にElectronを起動する。Tunnel ID、認証参照、MCP commandはProfileへ集約し、Hub / Electron Mainへ複製しない。
- Mainは単一起動を確保し、app ready後にHubを起動する。HubはData Rootから決まるWindows named pipeを排他的に確保してからDBを開く。確保失敗時は起動を止め、既存Hubを殺したりDBを初期化したりしない。
- Hub内Moduleは同じHub Process内で直接連携する。外部Local MCP橋渡しだけが認証付きnamed pipeを使用する（§20）。
- Hubの準備完了と復旧結果を受け取るまでは実行操作を無効にする。Hub切断時はMainの新規送信を止める。予期せぬ終了時は、既存Hubの終了とpipe解放を確認した後に有限回の自動再起動を行い、Hub DBの復旧結果を再取得してから操作を再開する。
- Windowの×は「Niraiを終了」として扱い、§10の停止保存を行ってアプリを終了する。Trayは起動中の再表示と明示終了に使う。

## 4. 不変条件

1. Task、Turn、Action Run、Approval、設定の同じ状態を複数箇所へ二重に正本化しない。
2. Residentの人間向け発言はProvider上で生成されたassistant本文を唯一の本文とし、Nirai用に再生成しない。
3. Holo TaskでMasterがChatGPT native composerから送る本文は、Provider送信より先にHubのTask Chatへ同じ生テキストで保存する。Web PromptにはMCP接続名、Active Turn ID、Master原文または短い継続指示だけを載せ、WORLD_RULESはNirai-MCPのServer Instructionsから適用する。Task状態や内部Contextを会話Promptへ複製しない。
4. Holoの会話表示、思考表示、Streaming、Stop、Retry / New response、引用、Link、Scroll等のProvider固有UXはChatGPTを正本とし、Niraiは同じ会話UIを再実装しない。Provider固有操作はTask状態を直接変更しない。
5. Task完了を要求できるのは`CompleteTask`だけ。Holo Taskは、その完了予定を持つTurnの最終assistant Message保存と同一transactionでのみCompletedへ確定する。
6. Masterへの質問本文はProvider上のassistant本文をTask Chatへ同文保存した記録を正本とする。`AwaitMasterReply`は本文を持たず、正常に保存されたassistant発言の次の手番をMasterへ渡すTurn制御だけを表す。
7. Holo Turn自体はMaster環境への副作用を持たない。File変更・Process等の副作用、安全確認、停止・復旧はAction Runだけで扱う。
8. Provider Conversation / Sessionは表示・送信先と推論Contextの参照であり、Taskの目的・完了・実行権限・復旧状態の正本にしない。
9. Master環境へのNirai管理下の操作はHubのCapabilityとPolicy Gateを通す。外部Toolの直接経路で迂回しない。
10. 結果不明のActionを未実行や成功と解釈せず、同じ副作用を盲目的に再実行しない。
11. Pause / Cancel / Terminal後の古いTurnやActionから新規実行を許可しない。遅延結果は保存するがTaskを勝手に復活させない。
12. Residentの意図的な自己表現の決定主体はResidentとする。LipSync、Blink、通常のLookAt追従、補間、SpringBone等の身体機能はAvatar Runtimeが担う。

## 5. Capability契約

Registryは明示登録されたCapabilityを正本として扱う。Capabilityは以下を提供する。Holo向けMCP Tool metadataには利用に必要な操作名と入力形だけを公開し、実際の許可・検証・副作用判定はHub Registryを正本とする。

| 項目 | 契約 |
|---|---|
| `id / operations` | 不変ID、Operation名、入力Schema、戻り値Schema、必要資源、副作用の有無、既知のRisk |
| `availability` | `ready / busy / blocked / unavailable`と理由。取得不能なUsageは不明 |
| `invoke(operation, input, context)` | Hubが作成したRunを実行し、受付または結果を返す。長時間処理は後から同じRunへ結果通知 |
| `cancel(run_id)` | 停止要求。受付と停止完了を区別し、実処理の終了を報告 |
| `usage()` | 取得可能な使用量だけ返す。対応しないCapabilityでは省略 |

contextには`task_id / run_id / parent_run_id / control_epoch / workspace_scope`と許可済みの操作を含む。Adapterは入力と対象を検査し、他Runの結果を返せない。結果は要約、成果物参照、検証対象の版、エラー、実行済み副作用を含む。Provider固有形式をEngineへ漏らさない。

Operationは副作用なし / 副作用あり得る、をCapability境界で宣言する。`none`は「予期せぬ中断でも外部変更や生存Process等の後始末義務を残さない」Operationだけに使う。単に読取目的でも外部Processを起動して生存し得るなら`possible`とする。Runへ保存する区分はHubがRegistryのOperation定義から固定し、AIや呼出元の自己申告を採用しない。通常の失敗はAdapterが実際のeffectsを返す。Adapter自体が予期せず落ちた場合も、`none`を一律`unknown`へ昇格させず、`possible`だけを保守的に照合対象にする。

Task外の通常会話、軽いMemory参照、設定取得は直接呼び出してよい。Task外からのファイル変更、Command実行、長時間作業、承認を要する操作は、表示可能なTaskへ関連付けてから実行する。入口がSayでも安全確認を省略しない。

## 6. Task

TaskはMasterが認識する一つの仕事。最低限、次を保存する。

| 項目 | 意味 |
|---|---|
| `id / title / resident_id` | 不変ID、表示名、担当Resident |
| `objective / initial_message_id` | 依頼の目的と最初のMaster指示 |
| `workspace_scope / completion_criteria` | 作業対象・保護範囲、確認可能な完了条件 |
| `state / resume_enabled` | 実行許可とHolo自動継続設定 |
| `revision / control_epoch` | 更新番号と実行許可の世代 |
| `conversation_id / handled_instruction_seq` | Task Chatと処理済みMaster入力範囲 |
| 時刻・結果 | 作成・開始・更新・終了時刻、結果要約と参照 |

状態は`Running / Paused / Completed / Failed / Cancelled`。待機理由やResume中を追加Task状態にしない。

ResumeはHolo専用の自動継続機能とする。

| 状態 | 挙動 |
|---|---|
| Running + ON | Taskが未完了でMaster待ちでも安全待ちでもなければHolo Turnを継続する |
| Running + OFF | 現在のHolo Turn終了後は自動で次Turnを開始しない |
| Paused | Resume設定に関係なく新規Turn / Actionを開始しない |
| Terminal | 再開しない。続きは新Taskとして扱う |

Task開始時のResume初期値はOFF。Holoが`CompleteTask`するまでTaskは未完了であり、途中発言、Timeout、Session Error、25分区切りを完了扱いしない。

## 7. Holo TurnとAction Run

### 7.1 Holo Turn

TurnはHubがHoloへ一度のTask-bound Provider生成を許可する最小単位。Task、Turn ID、control_epoch、開始・終了時刻と、必要な場合だけ`await_master`または`completion_summary`を持つ。Turn IDはNirai-MCPの当該生成に対する実行権限を限定する識別子であり、終了済みTurnを再利用しない。

`await_master`は`AwaitMasterReply`で指定する次手番の制御であり、質問本文を保存しない。assistant本文がTask Chatへ正常保存されてTurnが終了した場合だけMaster待ちとして有効になる。`completion_summary`は`CompleteTask`検査済みの完了予定で、最終assistant保存前だけActive Turnに存在し、中断時は破棄する。

Turnは次のいずれかで閉じる。

- ChatGPTのassistant生成が終了した。
- MasterがChatGPT native Stopで現在の生成を打ち切った。
- 25分上限、Timeout、Session Error、接続断等で現在Turnを継続できない。
- Pause / Cancelで実行権限を失った。

native Retry / New responseはProvider固有の再生成操作として利用できるが、終了済みTurnのNirai-MCP権限を復活させず、新しいTask Turnも自動作成しない。TaskとしてTool利用を含めて続ける場合は、Masterがnative composerから新しい指示を送って新Turnを開始する。Provider操作のためにTask状態やRetry専用状態を追加しない。

### 7.2 Action Run

Action RunはCapabilityを一回利用した記録。Holo Turnから要求するFile / Process / その他Tool利用をAction Runとして保存する。

保存項目は、ID、Task、親Turn、Capability / Operation、状態、受付時control_epoch、副作用区分、固定した入力・作業範囲、結果・エラー、実処理参照、開始終了時刻。

状態は`Pending / Running / Completed / Failed / Cancelled / Interrupted`。副作用があり得るOperationだけ`effects=none|applied|partial|unknown`とcleanup確認を持つ。

Terminal Actionを再実行状態へ戻さない。再試行は必要なら新しいAction Runとして行う。結果不明の副作用や未完了cleanupがある間だけ、競合ActionとTask完了を止める。

## 8. Task Engineと完了

EngineはHub内の小さなModule。保存済み状態から次のHolo Turnを開始してよいかを判断する。

### 8.1 Holo Turnの開始

Turnを開始できるのは、TaskがRunningで、別のActive Turnがなく、未解決Approvalと未確定の危険Actionがなく、Residentが利用可能な場合だけ。直前の正常終了Turnが`await_master`を指定し、そのTurnで処理した入力より新しいMaster入力がなければ開始しない。

開始理由は次の二つだけ。

- 未処理のMaster入力がある。
- Resume ONでTaskが未完了である。

Holo Taskでnative composerから送信されたMaster入力は、AdapterがProvider送信を先行させず、信頼済みMain経由の`SendConversationMessage`でTask Chatへ生テキストを保存する。HubがMaster MessageとActive Turnを確定した後だけ、Adapterが同じMaster原文をChatGPTへ送る。Hub受付に失敗した場合はProviderへ送信しない。TaskにbindされていないChatGPT Conversationの通常送信は遮らず、Nirai Taskへ取り込まない。

Master入力はTask ChatのMessage列へ保存する。Approval回答は対象Approvalへ保存し、いずれの解決後も同じEngine判定へ戻す。Webへ送るTask-bound PromptはMCP接続名、Active Turn ID、そのTurnで処理するMaster原文または短い継続指示だけとする。

### 8.2 Holo発言の反映

ChatGPT上でActive Turnに対して生成されたassistant Messageを、その本文のままTask Chatへ保存する。Task ChatはHub上のTask記録であり、Holoでは同じ会話本文を別の会話surfaceとして常時二重表示しない。Nirai用の回答を別生成しない。

Masterがnative Stopを実行した場合は、その時点で取得できるassistant途中本文があれば同じTurnの記録として保存し、Turnを`master_stop`で閉じる。途中本文を完了回答として扱わず、`completion_summary`があれば破棄する。StopだけではTask state、Resume設定、開始済みAction Runを変更しない。

Holoが同じTurn内で`CompleteTask`を呼んだ場合、Hubは完了条件を検査して、そのTurnへ短い`completion_summary`だけを完了予定として固定する。この時点ではTaskをCompletedにしない。以後そのTurnから新しいMCP操作を受け付けず、Masterからの新規指示・Task定義変更も最終回答の保存まで受け付けない。

最終assistant MessageをTask Chatへ保存し、Turnを閉じる処理とTaskをCompletedへ確定する処理は同じStore transactionで行う。最終assistantを取得できずTurnが中断した場合、またはMasterが最終回答途中でStopした場合は完了予定を破棄し、Taskは未完了のまま共通の継続判定へ戻る。これにより「Completedだが最終回答がTask Chatに無い」状態を作らない。

### 8.3 継続判定

Turn終了またはHolo処理の中断後は、常に次の順で判断する。

1. Active Turnに`CompleteTask`の完了予定があれば、新しいTool / Turnを開始せず最終assistant Messageを待つ。保存できれば同一transactionでTaskをCompletedにし、取得できず中断した場合は完了予定を破棄して未完了として再評価する。
2. 直前Turnが`master_stop`で終わっていれば、Resume ONでも自動で次Turnを開始せずMasterの新しいTask指示を待つ。
3. 正常終了した直前Turnが`await_master`を指定し、その後のMaster入力がなければMaster待ち。
4. Paused / Cancelled / Failedなら停止。
5. 未解決Approvalまたは未確定の危険Actionがあれば安全確認を待つ。
6. それ以外は未完了。Resume ONなら次Turn、OFFなら待機。

通常のHolo発言、25分区切り、Timeout、Session Error、Web切断は別々の復旧フローにしない。いずれも「Taskが未完了なら同じ継続判定へ戻る」で扱う。`master_stop`だけはMasterの明示的な生成停止意思を尊重する入力境界であり、自動復旧対象にしない。native Retry / New responseはProvider-local操作なのでこの判定を動かさない。

### 8.4 完了

Task完了を要求できる入口は`CompleteTask`だけ。

Holoからの`CompleteTask`は、未解決Approval、Pending / Running Action、未確定effects / cleanup、未処理Master入力、必須完了条件・検証を確認したうえで、そのActive Turnへ短い`completion_summary`を完了予定として保存する。個別Actionの確定済み失敗履歴だけを理由に拒否しない。TaskのCompleted確定は、続く最終assistant MessageをTask Chatへ保存する同一transactionで行う。

`completion_summary`は内部記録であり、Task Chat本文、Task一覧の結論、回答代替として表示しない。Master向け最終回答の正本はProviderから取得してTask Chatへ保存したassistant Messageだけとする。

Master UIからの明示完了はActive Holo Turnがない場合に同じ安全・整合検査を通して即時確定できる。作業を打ち切る場合はCancelTaskを使う。

## 9. 資源と並列実行

最終的には複数Task・Action Runを同時に進める。同じ作業Folderへの変更、Provider側上限、CPU / GPU、HoloのWeb表示等、実資源の競合だけを制限する。

初期Holoは一つのWeb表示・一つのTurnを直列使用する。Taskごとの専用会話は常時WebContentsViewを一つずつ保持する意味ではない。Resource予約はAction Runに結び付けてHubで管理し、期限切れLeaseによる取り戻しはしない。

TaskをPaused / Terminalにしただけで資源を解放しない。実処理と後始末の終了、または競合しない隔離を確認して解放する。確認不能なら関係する作業範囲をblockedにし、無関係なTaskは止めない。ProcessはPIDに加えて起動時刻・実行元・Runの起動識別を照合し、PIDだけで再接続・停止しない。

## 10. Pause / Cancel / Resume / 再起動

| 操作・事象 | 挙動 |
|---|---|
| PauseTask | Pausedとcontrol_epoch更新を先に保存し、新規Turn / Actionを閉じる。開始済みActionは安全停止または結果照合 |
| ResumeTask | Runningへ戻して再評価する。Resume設定は変更しない |
| SetTaskResume | Holo Taskの自動継続設定だけを変更。ONなら即再評価、OFFでも現在Turnや開始済みActionを強制停止しない |
| CancelTask | Cancelledと世代更新を保存し、未開始処理を閉じ、開始済みActionを停止・照合 |
| Holo生成終了 | Task未完了なら§8.3へ戻る |
| ChatGPT native Stop | 現Turnを`master_stop`で閉じる。Task state、Resume設定、開始済みActionは変更せず、自動次Turnを開始しない |
| ChatGPT native Retry / New response | Provider-local再生成として許可する。Task / Turn / Resumeを変更せず、終了済みTurnのNirai-MCP権限を復活させない |
| 25分 / Timeout / Session Error / Web切断 | 現Turnを閉じ、Task未完了なら§8.3へ戻る |
| Master CHAT入力 | Holo Taskではnative composer入力をProvider送信前にTask Chatへ保存する。通常ResidentではTask Chatから新しいMaster Messageとして保存し、`await_master`中ならその待機を自然に解消して再評価する |
| Approval回答 | 対象Approvalを解決して再評価する |
| Hub / Nirai / PC再起動 | 未完了Taskと開始済みActionを復元・照合し、安全が確定するまで新規実行しない |

Resumeは未完了Holo Taskの通常継続である。

古いTurnからのMCP要求はcontrol_epochとActive Turnで拒否する。Actionの副作用が不明な場合だけAdapter側の照合を続け、安全が確定するまで競合実行とTask完了を止める。

## 11. Approval

Masterへの通常の質問・回答はTask本文として扱い、専用Requestや専用回答UIを持たない。ResidentがMasterの判断を待つ場合は、質問を通常のassistant本文としてTask Chat記録へ保存し、`AwaitMasterReply`で次の手番だけをMasterへ渡す。通常ResidentはTask Chat UI、HoloはChatGPT native surfaceで同じ本文を表示する。

Approvalは会話ではなく権限境界であるため、専用Requestとして扱う。状態は`Pending / Resolved / Cancelled`。ID、Task、対象Run、revision、理由、固定した提案操作と入力指紋・作業範囲、回答者・回答・時刻を保存する。

Approvalの提案は、Policy Gateが受け付けたPending Action Runの`capability_id / operation / input / workspace_scope`から固定する。別内容の提案や対象Runのない汎用承認を受け付けない。AI自身はApproveできず、回答は`approved: boolean`だけを受け付ける。

Approveは当該操作だけを許可する。Rejectはその提案を閉じ、未開始の対象RunをCancelledにする。一つのApprovalで無関係なRunまで停止しない。Runningなら解決後にTaskを再評価し、Pausedなら回答だけ保存して再開を待つ。終了したTurnを復活させない。

起動復旧で旧Pending Runは§10に従い取り消す。再開後に同じ操作が必要なら新RunとしてPolicy Gateへ通す。旧承認を根拠として再利用できるのは、同じ操作・入力・範囲で未実行と確認でき、承認対象が現在も同一の場合だけとする。変更・部分適用・結果不明では旧承認を流用しない。同じ回答の再送は同じ結果を返し、実行を増やさない。承認済みという状態と、停止後に実行を再許可することは別であり、再開時は§10の検査を通す。

## 12. 安全確認と初期ローカルTool

### 12.1 共通Policy

通常の調査・実装・検証は依頼の範囲で進める。次は実行前にMaster承認を要する。

- 課金、大規模破壊・大量削除、取り返しがつきにくい変更
- 大容量ダウンロード、大規模改修・修正
- 大型Program / Runtime導入、CPU / GPUを長時間ほぼ占有する処理、PC再起動
- 権限・公開範囲等の変更で、実質的に取り返しがつきにくいもの

依頼中に同じ具体的操作がすでに承認されていれば根拠を記録して進め、重複確認しない。承認済みの範囲をAIが拡大しない。規模や外部影響が確認できず承認要否を判断できない操作は、実行可能な具体案を作って確認する。旧実装の件数・容量閾値を無条件には引き継がない。

CapabilityのRisk自己申告だけで決めず、Hubは実際のパス、差分、実行内容、範囲、承認を検査する。安全確認はすべて共通Gateへ集約する。

### 12.2 初期Operation

| Operation | 入力と保証 |
|---|---|
| `local.read / local.search` | 許可Folder、相対パスまたは検索条件、取得量上限。秘密領域を除外 |
| `local.apply_patch` | 対象パス、変更前の指紋、具体的差分。新規作成は不存在を条件とする |
| `local.run_command` | 固定した実行ファイル、argv、cwd、限定env、期限、出力量上限。受付後にrun_idを返す |
| `local.inspect` | `profile`指定では固定Command案とSource指紋を取得し、`run_id`指定では当該Runの保存済み状態を限定取得 |
| `local.cancel` | 当該Runの停止要求。停止完了と部分適用は別途記録 |

ローカルCapabilityはTaskに絶対パスの`workspace_scope`が設定されている場合だけ、Workspaceへ触れる操作を行える。会話だけで完結するTaskでは接続確認やSchema確認のためだけに`InvokeCapability`を呼ばない。`local.inspect {profile}`を含むWorkspace依存操作も同じScope契約に従う。

File操作は正規化した実パスでScopeを確認し、外部へ抜けるリンク・junction・共有書込を拒否する。Hub DB、Credential、実行中の製品版は通常の作業対象から除外する。Nirai Sourceは明示されたTask Scopeに含められる。AIはScopeを変更できない。

変更前の指紋を適用直前に再照合し、退避と適用記録を確保してから書く。複数ファイルを一括で原子的に変更できると仮定しない。中断時は適用済みを記録し、安全に復元できる時だけ戻す。他者の変更があれば上書きせずpartialとして止める。実書込・復元が落ち着くまで占有を解放しない。

Commandはshell文字列連結を標準経路にせず、具体的argvで起動する。初期は対象Projectの既知の検索・テスト・buildを、内容を確認した実行Profileとして登録する。Profileは実行ファイル・引数制約・許可出力先・起動Script等の指紋を持ち、変更時には再検査する。実行したSource / Scriptの版と結果をRunへ残す。

cwdやstagingはOS権限の隔離ではない。Project内Scriptでも任意のファイル・ネットワークを扱えるため、未確認の任意Script、依存導入、公開、破壊的Git操作を無条件で許可しない。実行前に内容と外部影響を確認し、上記承認対象ならRequestを出す。初期は自動で実行可能と判断できないものを明示的に保留する。秘密をCommand引数やログへ流さず、出力量と時間を制限する。

## 13. Conversation / Say / Task Chat

ConversationはMasterとResident、Resident同士に共通の会話基盤。Task Chatは一つのTaskに対応するHub上の会話記録であり、Master指示とResidentの人間向け発言を保存する。接続方式や表示surfaceが違っても、Taskに属する本文はこの記録へ合流させる。

通常ResidentではTask Chat UIを会話面として使う。HoloではChatGPT native surfaceを会話面とし、同じ本文をNirai側へ別の会話UIとして常時複製しない。Holo Taskを選択中のnative composer送信は§8.1の境界でHubへ先に保存してからProviderへ渡すため、ChatGPT画面から入力してもTask指示の正本はHubに保たれる。TaskにbindされていないChatGPT Conversationの入力はNirai Taskへ取り込まない。

ResidentのTask発言は、Hubが開始したActive Turnに対してProvider上で生成されたassistant本文をそのまま保存する。Provider用とNirai用の二つの回答を作らず、ResidentからTask Chatへ本文を別経路で再送させない。Masterへの質問も同じ本文経路を使う。

Holoの会話Contextは、Hubへ保存済みのMaster原文とChatGPT Conversation上の会話をそのまま使う。Task状態、履歴要約、WORLD_RULES、Capability一覧を別の巨大Contextとして再構成しない。Actionの大きな結果だけは必要なRunを`GetRunResult`で限定取得する。

## 14. AI同士の連携

Resident同士の人格的な会話は、共通Conversationの参加者として扱う。HoloとSerina等がMaster不在でも同じConversationを継続できることを最終要件とする。

仕事の委譲は、担当Taskから別AI CapabilityをAction Runとして呼び出し、結果をTaskへ戻す。会話の継続と仕事の実行権限を分離し、Action Runは共通の権限・安全確認に従う。

## 15. Local Memory

Local Memoryは標準搭載する独立Capabilityで、Operationは`remember / recall / forget`。正本はローカルに保存し、検索Index / Embeddingは再生成可能な派生データとする。特定AI Providerがなくても最低限の保存・取得が成立する。

Task / Runの状態を持たず、Archiveを自動的に長期Memoryにしない。HoloやSerina等の別Projectが管理するMemoryは吸収せず、必要なら外部能力として接続する。Memory StoreはHub Storeと別に持ち、検索技術・外部AI利用はその境界内で実装する。初期自走化後に着手する。

## 16. Resident / Persona / Holo

Residentは不変ID、表示名、Role、Persona参照、使用Capability、Model、Avatarを持つ。Persona本文はFileを正本とし、WORLD_RULES本文を複製しない。

### 16.1 Residentの自己表現

Residentの意図的な見た目・感情表現は、Resident自身が唯一の判断主体になる。

Residentが選んでよいものは、Avatarが実際に公開しているCapabilityの範囲に限る。

- 基本表情とその強さ
- 衣服・アクセサリー・外装の表示状態
- 将来追加する意図的なPose / Gesture等

ResidentはPersona、会話、状況、Memory、現在の見た目と利用可能Capabilityを材料に、自分をどう表現するかを選ぶ。

**禁止:** `joy=0.8ならhappy`のように、Program側の固定対応でResidentの意図的な自己表現を決定してはならない。

LipSync、Blink、通常のLookAt追従、補間、SpringBone等は意図的な自己表現の選択ではなく、身体機能としてAvatar Runtimeが担う。

Avatar Runtimeは次だけを担当する。

- 現在のAppearance状態を返す
- そのAvatarで利用可能な表情・Wardrobe等を意味的なCapabilityとして返す
- Residentが選んだ有効なAppearance変更を実Avatarへ適用する
- 存在しないItemや不正な値を境界で拒否する

Residentには意味的なAvatar Capabilityを提示し、Avatar固有のBone / BlendShape / Nodeとの対応はAvatar Runtime境界が担う。

同じ表情や服装への偏りが実運用で問題として観測された場合は、最近のAppearance履歴をResidentの判断材料として利用できる。履歴はResident自身の表現判断を支えるContextとして扱う。

### 16.2 Avatar成果物との境界

NiraiがAvatar Runtimeへ受け入れる成果物はVRM 1.0を基本とする。

- 標準表情はVRM Expressionを利用する
- VRM標準にない任意Capabilityは、VRM内部のNirai Capability Metadataから読む
- Wardrobeは`extras.nirai.capabilities.wardrobe`を利用する
- AvatarのRuntime Capability情報はVRM内部を正本とする

Model ConverterはSource固有差を変換境界で吸収し、「このAvatarで何が可能か」を成果物へ保存する。何を選ぶかは決めない。

Nirai側はVRMの標準SemanticとNirai Capability Metadataだけを利用し、Source固有差はModel ConverterとAvatar Runtime境界で吸収する。

World / Avatar Loader実装時に、この契約を既存Capability境界へ接続する。

### 16.3 Appearanceの保存と表示

`avatar` Capabilityを共通`InvokeCapability`から使う。`inspect {}`はTaskの担当Resident本人の利用可能な表情・衣装、保存した選択、表示への反映状況を返す。`set {model_id, expected_revision, appearance}`は本人の完全な選択状態を保存する。Resident ID、Bone、Nodeは入力で指定させない。表情の`null`はモデルの基準顔（感情ExpressionのWeightがすべて0）を表す。

保存済み選択の正本は正常終了した`avatar.set`のRun結果とし、Worldはその投影だけを持つ。新しい設定Storeや表示専用の永続Queueは設けない。同一モデルの内容指紋と前回選択の版を検査し、選択保存を直列化して古い判断による上書きを拒否する。モデル固有の選択は再読込・再起動後も復元する。失敗・中断したRunとその遅延結果は表示の選択に採用しない。

`set`の正常終了は選択の保存を表し、画面への反映完了を意味しない。Worldが現在のモデル・読込世代・選択の版に一致する描画を確認したときだけ、`inspect`の`display_applied`をtrueにする。描画喪失・未読込・非表示中は表示確認不能として返す。表示の復旧は保存済み選択を再表示するだけで、新たなAI TurnやActionを開始しない。

このOperationは外部処理を先行実行せず、共通Run結果の保存一回で選択を確定するため、`side_effects=none`とする。未確定Runには外部変更や後始末義務がなく、既存のPause / Cancel / 再起動によるRun中断規則をそのまま適用する。

HoloはChatGPTへNirai固有能力を足すAddonとして扱う。ChatGPTは会話表示、思考表示、Streaming、Stop / Retry、引用、Link等のProvider固有UXを所有し、NiraiはTask / Resume / Approval / Action Run / Capability / Memory / Resident連携を所有する。Holo Adapterはこの境界を橋渡しするだけで、独自のTask lifecycleを持たない。

WORLD_RULESはNirai-MCP Server Instructions、Task固有の会話内容はHubへ保存したMaster原文とChatGPT Conversationから得る。RoleやPersonaの自然文を実行権限の根拠にしない。未接続Capabilityの利用可能状態・Usageを推測して表示しない。

## 17. Holo Web Adapter

### 17.1 Native surfaceと責務

ChatGPT WebをHoloの正式なconversation surfaceとしてNirai内へ表示する。WebContentsViewの会話本文、思考表示、Streaming、composer、Stop、Retry / New response、引用、Link、Scroll等のProvider固有UXはChatGPTを正本として利用する。

Holo Taskを選択した時はnative surfaceをTaskの主会話面にし、その外側へNiraiのTask状態、Resume、Approval、Activity、成果物を配置する。Task ChatはHub上の記録として保持し、Holoの会話表示面はnative surfaceに一本化する。Task外では同じsurfaceを通常のChatGPTとして利用できる。

AdapterはTask binding、Task-bound送信境界、Provider生成観測、assistant Message記録、Stop等のProvider操作通知、Navigation・Permission制限を担当する。Task ownership、完了、Resume、Approval、Action権限、復旧判断はHubが所有する。

Conversation ID / URLは表示・送信先と推論Contextの参照である。MasterがTask-bound Conversationから別Conversationへ移動した場合もTask状態はHubに保持し、現在TaskのHolo利用可能状態だけをblockedとして扱う。Resumeは現在の表示を尊重し、Taskを再選択した時に保存済みbindingへ戻す。

### 17.2 Task-bound送信

Task-bound native composerではChatGPTの見た目と入力操作をそのまま使うが、送信確定だけをAdapter境界で捕捉する。AdapterはProviderへの送信を先行させず、Master原文を信頼済みMain経由の`SendConversationMessage`でHubへ保存する。HubがMaster MessageとActive Turnを確定した場合だけProvider送信を進める。Hub受付失敗、Paused / Terminal、未解決安全条件では送信をProviderへ流さない。

Turn開始時のWeb Promptは次だけで構成する。

- Nirai-MCP接続名
- Active Turn ID
- そのTurnで処理するMaster原文。新しい入力がないResume Turnでは短い継続指示

Master原文は要約・構造化しない。Task状態、履歴要約、内部ContextはWeb Promptへ載せない。WORLD_RULESはNirai-MCP Server Instructionsから適用する。TaskにbindされていないConversationのcomposerとNavigationは通常のChatGPT操作として扱い、Nirai Taskを作成・更新しない。

### 17.3 Stop / Retry / New response

native StopはProvider生成だけを止めるMaster操作であり、PauseTask / CancelTaskではない。Adapterは実際のStop操作と対象Active Turnを確認してHubへ通知し、取得できる途中assistant本文があれば保存する。HubはTurnを`master_stop`で閉じ、完了予定を破棄するが、Task state、Resume設定、開始済みAction Runは変更しない。Resume ONでも自動で次Turnを開始しない。

native Retry / New responseはProvider固有の再生成操作としてそのまま利用できるが、Hubへ新しいTask Turnを作らず、終了済みTurnのNirai-MCP権限も再発行しない。その生成から古いTurn IDでNirai-MCPを呼んでもHubは拒否する。TaskとしてTool利用を含めて続ける場合は、Masterがnative composerから新しい指示を送り、新しいTurnを開始する。

この制限はProvider固有UXを壊さず、終了済みTurnを再Active化したりRetry専用Queue / Flagを追加したりせずにTask権限を保つための境界とする。

### 17.4 assistant Messageの記録

AdapterはActive Turnで新しく生成されたChatGPT assistant Messageを観測し、Provider本文を変更せずTask Chatへ記録する。native surfaceがMaster向け表示の正本なので、Nirai側への途中転写を会話UXの成立条件にしない。Tool実行中の一時的な生成停止を完了とみなさない。

正常終了では、ChatGPTがユーザー入力待機へ戻り、同じTurnのassistant本文が安定したことを確認して最終本文としてHubへ保存し、Turnを閉じる。DOMが生成途中に欠ける等、確定本文を取得できない場合だけ同じConversationの再読込後に再取得してよい。Reloadは通常経路の必須Stepにせず、最終本文回収のProvider境界内fallbackに限定する。

Stop時の途中本文は記録してよいが成功完了には使わない。Task-boundでないRetry / New responseの本文はTask ChatへTask発言として取り込まない。Provider上の別branchや古いTurnの本文を現在Turnへ付け替えない。

### 17.5 認可と中断

Nirai-MCP要求はActive Turn IDとTask control_epochで検査し、TaskがRunningかつ対象TurnがActiveな場合だけ許可する。終了済みTurn、Paused / Cancelled / Terminal後の要求を拒否する。ChatGPTがTool callへ付けるProvider session metadataはConversation相関の補助に使ってよいが、Task権限の正本や終了済みTurn再認可には使わない。

送信前と証明できる短い通信失敗だけAdapter内で有限Retryしてよい。送信後の成否が不明なら同じTurnを盲目的に再送しない。25分上限、Timeout、Session Error、Web切断、Conversation破損はTurn終了として§8.3へ戻す。Task状態や専用Recovery state、Retry Queueを増やさない。

## 18. Settingsと有限な待ち

動的設定はHub Storeに集約する。初期値は一つの設定定義に置き、Connector / UIへ別々に埋め込まない。

| 項目 | 初期値と処理 |
|---|---|
| 送信前Retry | 初回込み3回。送信後不明は同じTurnを再送しない |
| Holo Turn上限 | 25分。到達したらTurnを閉じ、Task未完了なら§8.3へ戻る |
| Command実行期限 | 既定5分、通常上限30分。高負荷や延長はPolicyに従う |
| Command出力 | stdout + stderr合計8 MiB。超過時は停止要求と記録 |
| Controlメッセージ | 1 MiB。大きな出力は成果物参照と限定読取を使う |

Login、Draft、未解決Approval、未確定ActionをRetryで突破しない。これらは条件が解消した時に同じEngine判定を再評価する。

## 19. 保存構造

Hub StoreはData Rootの`hub.sqlite3`一つ。Node Hubだけが読み書きし、Main / UI / Local MCP / Capability Workerは共通APIを使う。

| Table / 記録 | 正本と制約 |
|---|---|
| tasks | §6。Task ID不変、state / revision / control_epoch / resume_enabled |
| holo_turns | §7。TaskごとにActiveは最大一つ。Turn IDとcontrol_epochでHolo権限を限定し、`await_master`と最終回答待ちの`completion_summary`をTurn内だけに保持 |
| runs | §7。Action Runだけを保存。副作用・cleanup・成果物はここへ結び付ける |
| master_requests | §11のApprovalだけを保存。対象Runと提案内容を固定し、回答は一度だけ確定 |
| conversations / messages | §13。Task ChatはTaskごとに一つ。Master入力とChatGPT assistant Messageを保存 |
| residents / settings | Residentと動的設定の正本 |
| provider_bindings | ChatGPT Conversation等の送信先ヒント。Task権限を持たない |
| command_receipts | 呼出主体＋command_id、入力指紋、受付時刻、返却結果 |

Action Runが実処理の副作用とcleanupを所有する。

状態更新とCommand受付結果を短いtransactionで保存し、その後に通知・実行を行う。通知やProvider画面を第二の正本にしない。

大きなAction結果はRun専用Folderへ保存し、内容指紋と所有者をDBへ登録する。Migration前は整合Backupを取得し、対応外Schema・Migration失敗・DB破損を空DBで隠さない。

Local Memoryは責務が異なるため独立Storeを持つ。

## 20. Control API

### 20.1 共通Command

UI / Holo / Local Toolの意味上の入口はHub handlerへ集約し、任意のTask / Turn / Action状態を書き換えるAPIを公開しない。

| 呼出主体 | 公開操作 |
|---|---|
| Masterの信頼済みUI | CreateTask、UpdateTaskDefinition、PauseTask、ResumeTask、SetTaskResume、CancelTask、ResolveMasterRequest、SendConversationMessage、GetSnapshot、GetSettings、UpdateSettings、CompleteTask |
| Active Holo Turn | GetRunResult、InvokeCapability、AwaitMasterReply、CompleteTask |
| Hub / 登録Adapter | Turn開始・終了、assistant Message観測、Task-bound native送信、native Stop等のProvider操作、Action結果、停止・後始末結果、Capability状態 |

Holoは承認回答を送れない。approval Requestは具体的操作を検査したPolicy Gateが作成する。Task定義変更はMaster操作へ集約する。

人間向け本文は§17.4、Provider操作の観測はAdapterを入口とし、Task / Turn状態の確定はHubだけが行う。

### 20.2 受付と古い要求

共通Envelopeは`protocol_version / command_id / issued_at / type / target / expected_revision / payload`。外部Holo要求にはActive Turn IDを付ける。

認証後、HubはTaskがRunning、TurnがActive、control_epoch一致、期限内であることを検査して新規Commandを許可する。保存済みcommand_idの同一結果取得は、新規実行権限と分けて返してよい。Providerが付与するConversation / Session metadataは相関補助に利用してよいが、Task権限の正本にはしない。

Pause / Cancel / Complete、Turn終了、Hub再起動、期限切れ後の古い要求は拒否する。native Retry / New responseを含め、終了済みTurnの要求を新Turnへ付け替えない。

### 20.3 Transport

Mainは信頼済みNirai UIだけからMaster Commandを受ける。外部Web frameへMaster権限を公開しない。

Local MCP橋渡しはv2同梱の小さなNode Moduleとし、認証付きnamed pipeから共通Commandへ変換するだけにする。独立Engine、Queue、Task状態を持たない。Holo側へ公開するMCP Toolは`nirai_command`一つに集約し、`turn_id`とCommand Envelopeの`type / payload`で§20.1の操作を表す。WORLD_RULESはServer Instructions、操作形はTool metadataを正本とし、Task Promptへ複製しない。

接続Secretは当該Windows利用者だけが読めるData Root内へ保存し、引数・ログ・Webへ渡さない。Holo側の権限は接続認証に加えてActive Turn / Task control_epochで検査する。

## 21. Dashboard

見た目・配置・Resident欄・Task欄の基準は`v2/docs/ui-design.md`と実Rendererとし、色・透明度・Border・Typography等のDesign Tokenは`v2/src/renderer/theme.css`へ集約する。Holo・通常Residentとも海中Worldが透けるGlassを使う。実データの動作は本書とHub状態を正本とする。

表示するのは、Task状態、Resume、現在または最近のRunを要約したActivity、Approval、Capability状態、取得できるUsage / Limit、結果。ActivityはHub状態から導出し、詳細Logは必要な時だけ開く。

通常ResidentではTask Chatを会話面にする。Holo TaskではChatGPT native surfaceを主会話面とし、Task一覧・Resume・Approval・Activity等のNirai UIをその外側へ配置する。GlassはNirai側の外枠が所有し、Provider固有の会話surface・状態表示・portal・native操作は原則そのまま維持する。実DOM監査でNirai表示と衝突する補助UIや特定surfaceだけをHolo Adapterが最小overrideする。Nirai RendererとHolo Skinは同じTheme正本を共有し、Skin適用に失敗した場合は未加工のnative surfaceへfallbackする。

縦長または900px以下ではDashboardを画面下側へ寄せ、上側にWorld表示領域を残す。高さは58vhを目安とし、小さい画面では操作領域を保つため430pxまで広げるが画面内に収める。760px以下ではHolo・通常ResidentともTask一覧と会話を切り替え、それより広い画面では左右配置にする。Nirai固有Controlと通知は会話本文の上へ重ねず、Holoの表示領域外に確保する。

| UI操作・表示 | 接続契約 |
|---|---|
| ＋ / 最初の指示 | ＋はCreateTaskでPaused Draftを作る。Holo Taskではnative composer送信をAdapterが捕捉してSendConversationMessageへ渡し、Hub保存後にProviderへ送る。通常ResidentはTask Chatから送る。各Commandの再送は同じcommand_id |
| Pause / 再開 | Task状態操作。Resume設定は変えない |
| Resume ON / OFF | 設定だけ変更。ON時の再評価はHubが行う |
| CHECK | Pending Approvalを持つTask件数。RUN / PAUSEとの重複を許す |
| 承認 | Approval IDと具体的対象を表示して承認 / 却下。通常Chat送信では権限を与えない |
| Residentへの回答 | 通常ResidentはTask Chatから回答する。HoloではChatGPT native surface上の質問へnative composerから回答し、Provider送信前にHubへ同文保存する |
| 完了を確定 | Running / PausedのTaskをMasterが明示完了。Hubの安全・整合検査を通し、拒否された条件を表示 |
| Taskを取り消す | 確認後CancelTask。成功完了に変換しない |
| Terminal Task | Completed / Failed / CancelledをTask一覧内で区別。元Taskの再開・内容変更は禁止。Completedの「再開」は旧結果を参照する新Taskを作る |
| 接続切れ | 未保存・未確定を表示し、成功を先行表示しない。再接続時はHubを再取得 |

RUNはRunning Task、PAUSEはPaused Task、COMPLETEはCompleted Taskから計算する。未着手のPausedには「入力待ち」、Running + OFFで応答待ちには「指示待ち」、実処理停止前には「停止処理中」をActivityとして表示できる。これらを追加Task状態にしない。

モックの「完了扱い」は実接続時に上表の「完了を確定」と取消へ分け、未完了Runを強制成功にしない。最初の指示と通常の回答欄は区別し、CHECKがあるだけで通常Messageを承認として解釈しない。

## 22. Task作成

Dashboard上部でResidentを選び、＋の`CreateTask`で未着手Paused TaskとTask Chat記録を作る。通常ResidentではTask Chatから最初の指示を送る。Holo TaskではTaskを選択したnative surfaceのcomposerを使い、Adapterが送信を捕捉して`SendConversationMessage`へ渡す。最初の`SendConversationMessage`は、Master Message保存、暫定Title / objective / initial_message_id設定、Running遷移を一つのtransactionで確定する。Hub確定前にChatGPTへ先行送信しない。

Holoを使うTaskだけResume設定を持ち、初期値はOFF。最初の指示がないTaskのResumeは拒否する。

作業対象はMasterが設定したWorkspaceまたは明示許可対象から決める。AIに全PCを書込可能な既定Scopeを渡さない。

HoloのMaster追加指示もnative composerから同じ`SendConversationMessage`契約へ合流させる。Paused中の送信だけではTaskを再開しない。Terminal Taskの続きは旧成果物を参照する新Taskとして作る。TaskにbindされていないChatGPT入力やProvider-local RetryはTask指示に昇格させない。

## 23. Terminal表示 / Retention

Terminal Taskは同じTask一覧へ統合する。Completedは終了後72時間だけ一覧に表示し、Masterの「閉じる」で即時に表示対象から外せる。Failed / Cancelledは確認のため一覧に残し、「閉じる」で非表示にできる。この非表示はUI上の表示制御でありTask状態やHub保存記録を変更しない。終了から90日経過したTaskと専用の一時結果・Logを整理対象にする。

成果物参照には所有Taskと、一時物・Project本体・共有物・復旧資料の区分を付ける。Project本体、共有成果物、Local Memory、他Taskから参照中の結果、未確定副作用の復旧資料はTask削除に連鎖させない。必要な参照を保全・移し替えてから削除し、Task専用でないConversationも巻き込まない。

未終了実処理や復旧未完了があるTerminal Taskは整理を保留し、理由を表示する。削除済みTask / Run IDの要求は拒否する。Command受付期限を過ぎた新規要求も拒否し、古いCreateTask等の再送で履歴を再生成しない。削除はHubの既知の専用Folderと所有情報から行い、AI指定パスを再帰削除しない。

文書の`archive/`はこの機能とは別。初期計画の退役は同計画の定義に従う。

## 24. 外部能力の追加

新しいAI / ToolはCapability Adapterとして接続する。Provider固有仕様はAdapter内部へ閉じ込め、EngineとDashboardは共通Capability契約を扱う。共通契約に固有機能が収まらない場合はCapability固有Operationとして表現する。

Python資産を利用するCapabilityでは、Adapterが入力検証済みRunをWorkerへ渡し、結果・停止・後始末をHubへ返す。Task状態、承認、Queue、Hub DBのWriterはHubが所有し、必要Runtimeの配布と終了管理はCapabilityが所有する。

## 25. 自分自身の開発と受け入れ条件

### 25.1 実行版と開発対象の分離

通常の開発Taskは許可されたSourceを編集し、候補版を実行中の配布版とは別の出力先へbuildする。Action RunにはSource指紋、候補build ID、検証結果、実行アプリ版、Schema版を記録する。

候補版は検証用Data Rootで確認し、使用中Niraiの切替はMasterの明示操作に分ける。切替前にTaskとActionを安全停止し、DBと必要資料をBackupする。

### 25.2 初期自走化の必須保証

| ID | 受け入れる保証 |
|---|---|
| AC01 | HubだけがTask / Turn / Action Run / Approvalを保存し、重複Eventでも二重開始しない |
| AC02 | Resume OFFでは一つのHolo Turn内で複数Toolを使え、Turn終了後に自動次Turnを作らない |
| AC03 | Resume ONではCompleteTaskまたはMaster待ちまで未完了Taskを継続する。通常発言、25分、Timeout、Session Error、接続復旧を同じ原理で扱い、Masterのnative Stopだけは自動継続しない |
| AC04 | Pause / Cancel / Terminal後の古いTurn / Actionを拒否し、遅延結果と部分適用は保存する |
| AC05 | 再起動後もTaskとActionの正本を保ち、未確定副作用を自動再実行しない |
| AC06 | Holo TaskでChatGPT native surfaceを会話面として使い、native composer入力をHub保存後にChatGPTへ送信し、HoloがNirai-MCPを使い、Task-bound assistant本文をそのままTask Chat記録へ保存できる |
| AC07 | AI自己承認・対象差替え・古い承認を拒否し、Approval回答後は同じEngine判定へ戻る |
| AC08 | CompleteTaskだけがTask完了を要求でき、未終了Action・未解決Approval・未処理指示・必須検証失敗が残る完了を拒否する |
| AC09 | native Stop / Retry、Web表示 / Reload / Conversation切替でTask状態を二重管理せず、終了済みTurnの権限を復活させず、UI再接続でHub正本へ戻る |
| AC10 | Scope外操作とPolicy迂回を拒否し、Nirai Sourceの小変更・検証をv2 Toolだけで実行できる |
| AC11 | 実際のNirai開発Taskをv1制御へ戻らず、ChatGPT native surfaceを保ったままResume ONでCompleteTaskまで自走し、Task-bound Holo発言と結果をHub記録へ反映できる |
| AC12 | 候補版を隔離検証し、明示切替後もTask履歴と設定を読め、新版上の短いTaskを完走できる |

恒久テストは上記を少数のInvariant / Critical Flow / Boundaryへまとめる。Fake Providerの成功を実ChatGPT接続成立へ読み替えない。

### 25.3 初期自走化後の必須要件

Local Memoryの独立性、Resident同士の共通Conversation、複数TaskとAction Runの資源単位並列、Capability追加性、Persona保全、Retentionによる共有成果物保護を満たす。

## 26. v2全体の完了条件と依存順

初期自走化のAC01〜AC12に加え、以下の出口を全て満たした状態をv2全体の完成とする。初期計画を退役しても本節は現行の受け入れ基準として残す。現在どこまで成立しているかは`v2/README.md`で確認し、本書に進捗履歴を蓄積しない。

| 順序 / ID | 対象と前提 | 出口 |
|---|---|---|
| 1 / V201 | Resident・通常Conversation・World。AC01〜AC12成立後、§13・§16・§21を接続 | 不変Resident IDで設定・Persona・Avatarを参照し、Masterとの通常会話とTask Chatを区別できる。World / Dashboard切替・再起動でも同じHub記録へ戻る。既存Persona本文を照合し、未接続能力とUsage不明を事実どおり表示する |
| 2 / V202 | Local Memory。§15の独立Capability | `remember / recall / forget`がローカルで動き、再起動しても保持する。検索Indexを失っても正本から再構築でき、外部AIの停止やTaskの取消・削除でMemoryが壊れない。MemoryはTask実行判断の第二の正本にならない |
| 3 / V203 | 追加AI・Tool。共通Capability境界を使用 | Serina、Cursor、Codexを各Adapterから接続し、実際の応答・使用可能状態・失敗・停止を確認する。Web / File / App操作と調査・開発・生成を必要なOperationで実行し、Task制御と承認をHubで保つ。新CapabilityのためにEngine / UIへProvider名の分岐を追加しない。外部仕様と認証は着手時に公式資料・実機で確定する |
| 4 / V204 | Resident同士の会話・仕事の委譲。V201・V203成立後 | Master不在のResident同士の会話を共通Conversationで継続できる。仕事の委譲は親子Runで結果を戻す。会話の継続と有料・長時間作業の実行許可を混同しない。Masterが継続を止められ、再起動で無断再開しない |
| 5 / V205 | 複数Task / Run。初期の資源予約を拡張 | 独立した作業範囲のTaskは並行し、同じFolderへの変更と一つのHolo表示は競合を防ぐ。一方のPause / Cancel / 不明結果で別Taskを巻き込まず、同じ資源の再利用は実処理終了後に限る。二つ以上のTaskを実能力で確認する |
| 6 / V206 | Retention・運用・Repository Cutover。成果物所有関係が成立済み | 終了Taskを90日経過後に整理できる。Project本体・共有物・他Task参照・復旧資料・Local Memoryを保存し、削除前後・再起動・古いCommand再送で保護が崩れない。実行版の切替、Backup、戻せるSchemaの範囲を確認する。Niraiの標準起動口を現行実装へ切り替え、v1を起動せず短いTaskを完走できることを確認した後、v1専用の起動処理・Runtime・依存・不要資産を整理する。移行用の`v2/`という名称は最終構成に残さず、現行Niraiの正式な配置へ昇格させる |

各出口の証拠には対象Source / build / Schemaの版、Task / Runまたは検証資料への参照、実行手順、成功・失敗条件、未検証範囲を含める。後続のAIは説明文や過去の件数だけで合格を引き継がず、変更の影響を受ける保証を現在の版で再確認する。

外部サービスの接続不能、Masterの認証・契約・具体的承認が必要な場合は、停止地点と必要操作を明示する。代替Capabilityの成功を実接続成立へ読み替えず、利用不能な能力を使えるように表示しない。v2全体の完成判定は本節の出口で行い、それ以外の機能は必要になった時点で目的・契約・依存関係を定めて追加する。
