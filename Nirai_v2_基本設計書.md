# Nirai v2 基本設計書

> 本書はNirai v2の現行仕様の正本である。共通の設計・開発・文書運用原則は`WORLD_RULES.md`を参照する。実装順序と各段階の出口は、初期自走化の期間だけ`Nirai_v2_初期自走化計画.md`で管理する。

## 1. 目的

Niraiは、AI・長期Memory・Tool・外部サービス・Worldを自然に組み合わせて使う、Master専用のAI Hubである。

PCに例えるならHubはマザーボード、Memoryは内蔵ストレージ、AIは演算能力、Toolは周辺機器、World / Dashboardは表示・操作面に相当する。Holoは専用の接続方式を持つ重要な能力として接続する。接続のために分散SystemやMessage Brokerを導入せず、同じProcess内では直接呼び出す。

## 2. 到達点と初期範囲

最終的には、MasterとResidentの会話、Resident同士の会話、複数Taskの同時実行、調査・開発・生成、Web / File / App操作、ローカル長期Memoryを共通Hubから扱えるようにする。

最初の到達点は、**Dashboardから渡したNirai自身の開発Taskを、Holoがv2のToolで調査・修正・検証し、必要ならAuto Resumeで続行して完遂できること**とする。UIは初期から実Hubへ配線する。

この段階の必須能力はHolo ConnectorとローカルFile / Process Tool。Memory、Serina本接続、Cursor、Codex、追加Capability、Resident同士の自律会話、高度な並列Schedulingはその後に実装する。初期からTask間の識別と資源競合の境界は守るが、将来機能を動作するように見せる仮実装は作らない。

## 3. 論理責務と物理構成

### 3.1 論理責務

| 責務 | 担当すること |
|---|---|
| Hub Core | 共通Command、状態保存、権限・安全確認、Capability接続 |
| Task Engine | 保存済み状態から実行許可と次の応答の開始を判断する小さなHub内Module |
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
| Nirai Renderer | Dashboard / Task Chat / Worldの表示。sandbox有効、Node無効、限定preload経由でCommandを呼ぶ |
| ChatGPT Web Renderer | Holoの外部Web表示。Node無効、contextIsolation / sandbox有効。Master用IPCや汎用ローカル操作を公開しない |
| 必要時だけ起動するWorker / 子Process | 大きなファイル処理、外部Command等。Hub DBを直接開かず、限定したRunの結果を返す |

画面・Holo接続とHubの言語・配布物を揃えつつ、DB処理やHub障害を画面のProcessから分離するため、この構成を採る。初期の必須機能にPython常駐部は不要であり、将来Python資産を使う場合も、そのCapability内部のWorkerとして必要部分だけ呼ぶ。

MainとHubは非同期MessagePort通信、UIはMainの限定IPCを経由する。MainへTask判断を置かない。Hubは`node:sqlite`の`DatabaseSync`を利用し、DB操作を短く保つ。同期DB処理や重い探索をMainへ載せない。utilityProcessはProcess分離であり、任意Commandを安全に閉じ込めるOS sandboxではない。

使用APIは[Electron utilityProcess](https://www.electronjs.org/docs/latest/api/utility-process)、[Node SQLite](https://nodejs.org/api/sqlite.html)に従う。依存版はv2のlockfileで固定し、Electron同梱Node上でDB・IPC・終了動作を確認して更新する。システムに別途入ったNode / PythonをHub起動の前提にしない。

### 3.3 配置と起動

- v2のSource / package / lockfile / build設定は`v2/`へ独立配置する。Main、Hub、shared契約、Renderer、Capability、Local MCP橋渡しをその配下へ置く。
- 製品Data Rootは`%LOCALAPPDATA%/Nirai-v2/`。Hub DB、Run成果物、ログ、接続情報、HoloのElectron userDataをここへ分けて置く。v1のRuntime / Login保存領域を共有しない。
- 検証用Data Rootは明示指定した別Folderを使う。同じData Rootを複数のHubで開かない。
- Mainは単一起動を確保し、app ready後にHubを起動する。HubはData Rootから決まるWindows named pipeを排他的に確保してからDBを開く。確保失敗時は起動を止め、既存Hubを殺したりDBを初期化したりしない。
- Hub内Module用に別Service、WebSocket Server、Broker、Supervisorは作らない。外部Local MCP橋渡しだけが認証付きnamed pipeを使用する（§20）。
- Hubの準備完了と復旧結果を受け取るまでは実行操作を無効にする。Hub切断時はMainの新規送信を止める。既存Hubの終了とpipe解放を確認せず代替Hubを起動しない。
- Windowを閉じた時はTrayへ格納し、Task状態を変えない。明示的な「Niraiを終了」は§10の停止保存を行ってアプリを終了する。

## 4. 不変条件

1. Task、Run、Request、設定の同じ状態をHubとRenderer / Provider / 外部会話へ二重に正本化しない。
2. ChatのReload・閉鎖・切替、Dashboardを閉じること、ChatGPTの生成StopだけではTask状態を変えない。
3. Provider native Sessionは推論Contextの参照であり、Taskの目的・進行・再開地点はHubから復元する。
4. Master環境へのNirai管理下の操作はHubのCapabilityとPolicy Gateを通す。外部Toolの直接経路で迂回しない。
5. 実行許可・開始予約を保存してから副作用を開始する。結果を保存してから完了を通知する。
6. 「結果が見えない」を「実行していない」「成功した」と解釈しない。結果不明のまま同じ副作用を再実行しない。
7. 新しい実行の許可と、実行済み結果の受理を分ける。Pause / Cancel後でも遅延結果を記録し、Taskを勝手に復活させない。

## 5. Capability契約

Registryは明示登録とし、動的Plugin探索を初期必須にしない。Capabilityは以下を提供する。

| 項目 | 契約 |
|---|---|
| `id / operations` | 不変ID、Operation名、入力Schema、戻り値Schema、必要資源、既知のRisk |
| `availability` | `ready / busy / blocked / unavailable`と理由。取得不能なUsageは不明 |
| `invoke(operation, input, context)` | Hubが作成したRunを実行し、受付または結果を返す。長時間処理は後から同じRunへ結果通知 |
| `cancel(run_id)` | 停止要求。受付と停止完了を区別し、実処理の終了を報告 |
| `usage()` | 取得可能な使用量だけ返す。対応しないCapabilityでは省略 |

contextには`task_id / run_id / parent_run_id / control_epoch / workspace_scope`と許可済みの操作を含む。Adapterは入力と対象を検査し、他Runの結果を返せない。結果は要約、成果物参照、検証対象の版、エラー、実行済み副作用を含む。Provider固有形式をEngineへ漏らさない。

Task外の通常会話、軽いMemory参照、設定取得は直接呼び出してよい。Task外からのファイル変更、Command実行、長時間作業、承認を要する操作は、表示可能なTaskへ関連付けてから実行する。入口がSayでも安全確認を省略しない。

## 6. Task

TaskはMasterが認識する一つの仕事。最低限、次を保存する。

| 項目 | 意味 |
|---|---|
| `id / title / resident_id` | 不変ID、表示名、担当 |
| `objective / initial_message_id` | 依頼の目的と最初のMaster指示 |
| `workspace_scope / completion_criteria` | 作業対象・保護範囲、確認可能な完了条件 |
| `state / resume_enabled` | 実行許可と自動呼び起こし設定 |
| `revision / control_epoch` | 更新番号と実行許可の世代 |
| `conversation_id / handled_instruction_seq` | Task Chatと、応答へ渡したMaster指示の範囲 |
| `wake_seq / handled_wake_seq` | 明示的な呼び起こし要求の連番と、応答へ引き渡した連番 |
| 時刻・結果 | 作成・開始・更新・終了時刻、結果要約と参照 |

状態は`Running / Paused / Completed / Failed / Cancelled`の5種類。`NeedsInput`、`Draft`、`AutoResuming`は追加しない。Runningは「実行してよい」であり、常に処理が走っている意味ではない。未着手は最初の指示がないPaused Taskとして表す。

**Resume設定は、AIの一度の応答が終わった後に、自動で次の応答を呼び起こしてよいかを表す。Toolを一回ずつ止める設定ではない。**

| 組合せ | 挙動 |
|---|---|
| Running + ON | 応答内の作業を進め、応答後も条件を満たせば自動で呼び起こす |
| Running + OFF | 一度の応答内の調査・修正・検証は続け、応答後の自動呼び起こしをしない |
| Paused + ON | 新規実行禁止。Masterが再開した後は自動継続 |
| Paused + OFF | 新規実行禁止。Masterが再開した後も応答後は自動継続しない |

最初の指示を送るとRunning、Resume初期値はOFF。Terminal TaskをRunningへ戻さず、続きは新Taskとして依頼する。作業範囲の拡大や依頼範囲の縮小はMasterの明示指示が必要。AIは目的から完了条件を具体化できるが、必須条件を勝手に取り除けない。

## 7. Run

RunはTaskがCapabilityを一回利用した記録。AIの一度の応答もRunであり、応答から要求するTool利用はそれぞれ子Runにする。`Turn / Step / Attempt`という別の恒久Entityは作らない。

保存項目は、ID、Task、Capability / Operation、`kind=response|action`、`parent_run_id`、状態、受付時のcontrol_epoch、開始を許可する`dispatch_epoch`、固定した入力・入力指紋・作業範囲、結果・エラー、実処理参照、開始終了時刻。再試行時は`retry_of`、外部送信時は§17の送信情報を同じRunに持つ。

実行開始時はdispatch_epochとTaskの現在のcontrol_epochが一致することを検査する。Capabilityへ渡す実行contextのcontrol_epochもこの値とし、受付時の古い世代だけで開始を許可しない。

| 状態 | 意味 |
|---|---|
| Pending | 受付済み、未開始。承認待ち・資源待ちを含む |
| Running | 開始を予約・保存済み。実処理開始前の切断もあり得るため副作用を照合する |
| Completed | 契約した処理の結果が確定 |
| Failed | エラー結果が確定 |
| Cancelled | 取消が確定 |
| Interrupted | 中断し、通常の結果が確定していない |

状態とは別に`effects=none|applied|partial|unknown`、停止・後始末の確認状況を保存する。Terminal Runでもunknownや後始末未完了なら資源を解放せず、Task完了を許可しない。

Terminal Runは書き換えて再実行しない。業務上のRetryは新Run＋retry_of、未送信と証明できる通信Retryだけは同じRun・同じdelivery_idを使う。遅延した実結果は補足として保存できるが、RunをRunningへ戻さない。

Failed / Interrupted Runの扱いは、未処理、後続Runで回復済み、目的達成に不要と確認済み、のいずれかを根拠参照とともに残す。AIの一言だけで未知の副作用や必須検証失敗を処理済みにできない。

応答中の新規Tool要求はRunningな親応答Runへ結び付ける。すでに受け付けた子Runは、親応答の終了後もTaskがRunningで許可が有効なら続けられる。終了した親から新たな要求を受け付けない。

## 8. Task Engineと完了

EngineはHub内の小さなModule。次のAI応答を呼ぶべきかを決めるための別AIや固定DAGを作らない。仕事の内容は担当AIが判断し、Hubは実行条件を検査する。

再評価の契機は、Task開始・再開、Master新指示、Resume OFF→ON、Request回答、Run終了、資源解放、Capability状態変化・再接続。再接続時は現在のHub状態を読み直す。Event通知の受信だけに頼らず、保存済みのPending処理を評価する。全体stalled監視、Workflow Lease、Heartbeat、別の永続Queueは作らない。

### 8.1 応答の開始と終了

次の応答を予約するには、TaskがRunning、同Taskに別の未終了応答がない、待つべき子Run・Request・不明な副作用や配送がない、必要資源とCapabilityが利用可能であることを確認する。その上で、未処理のMaster明示指示があるか、Resume ONで続行が必要な場合だけ開始する。確認とRun予約を一つの短いDB transactionで行う。

Task開始・Master追加指示・再開・Request回答ではwake_seqを増やす。応答予約時に引き渡す連番をRunへ固定し、handled_wake_seqを同じtransactionで更新する。blocked中に届いた明示操作もこの差分として残し、Resume OFFでも利用可能になった時に一度だけ渡す。複数操作を一つの応答へまとめてよい。Resume設定変更や接続復旧だけでは明示要求の連番を増やさない。

Masterの新指示はTask Chatへ先に保存する。応答中なら割り込んで第二応答を作らず、現在の応答が取得して扱うか、終了後の明示呼び起こしで渡す。渡した指示の範囲をRunへ固定し、それ以降の未処理指示がある完了要求は拒否する。指示を渡しただけで失敗時のContextから消してはならない。

GetTaskContextは返した指示・Request回答と連番を当該Runへ記録する。現在の応答が追加指示も扱った時は、終了報告でその連番を指定し、Hubが実際に渡した範囲内でhandled_instruction_seq / handled_wake_seqを進める。新指示を見ないまま古い応答が消費済みにできない。

応答は次のいずれかを構造化して返す。

- `continue`：今回の応答を終了し、残作業と次に必要なことを記録。
- `wait`：受付済み子RunまたはPending RequestのIDを指定して終了。存在しない待ち先は拒否。
- `complete`：成果物、検証、残課題、完了条件への対応を提示してTask完了を要求。
- `fail`：安全に続行できない理由を提示。Hubが未終了処理を整理してTask失敗を確定。

Resume OFFでも、応答内で複数のTool Runを要求できる。応答がwaitで終了した後に子Runが終わっても、OFFなら自動では呼び起こさない。Masterの回答・追加指示・再開は明示呼び起こしとして扱う。

DOMの生成終了は応答結果ではない。正規終了報告が欠けた場合は、Connector観測とRun期限でInterruptedにし、実処理・配送を照合する。Task成功へ変換しない。

### 8.2 完了と失敗

`CompleteTask`は、呼出元応答の終了、Task完了、結果Message保存を一つの操作で確定する。検査時に除外できる未終了Runは呼出元応答自身だけである。Master UIからの完了確定では除外する応答Runはない。

次が残っている場合は完了を拒否する。

- 他のPending / Running Run、停止確認・後始末待ち、不明な配送・副作用
- Pending Request、未処理のMaster指示
- 未処理の失敗・中断、未達の完了条件、成果物や検証根拠の不足
- 古い応答・control_epoch・会話対応からの要求

必要な検証は実ToolのRun結果と対象の版へ結び付ける。修正前のテスト成功を修正後の証拠にしない。HoloはTool側の実結果を上書きできない。Hubは記録と境界を検査するが、文章の自己申告だけで成果物の正しさを証明する仕組みではない。

個別Run失敗だけではTaskをFailedにしない。限定Retry・別手段で続行できるなら履歴を残して続ける。同一原因のRetry上限後、Master判断で進められるならRequestを出し、安全な続行方法がなければTaskをFailedにする。Failed / Cancelledにも未確定結果や後始末を隠さず表示する。

## 9. 資源と並列実行

最終的には複数Task・Runを同時に進める。同じ作業Folderへの変更、Provider側上限、CPU / GPU、HoloのWeb表示等、実資源の競合だけを制限する。

初期Holoは一つのWeb表示・一つの応答を直列使用する。Taskごとの専用会話は常時WebContentsViewを一つずつ保持する意味ではない。Resource予約はRunに結び付けてHubで管理し、期限切れLeaseによる取り戻しはしない。

TaskをPaused / Terminalにしただけで資源を解放しない。実処理と後始末の終了、または競合しない隔離を確認して解放する。確認不能なら関係する作業範囲をblockedにし、無関係なTaskは止めない。ProcessはPIDに加えて起動時刻・実行元・Runの起動識別を照合し、PIDだけで再接続・停止しない。

## 10. Pause / Cancel / Auto Resume / 再起動

| 操作・事象 | 保存と実行 |
|---|---|
| PauseTask | 先にPausedとcontrol_epoch更新を保存し、新規開始を閉じる。進行中は安全停止、または短い処理の実結果を保存 |
| ResumeTask | Master操作でRunningへ戻し、一度の明示呼び起こしを許可。Resume設定は保持 |
| SetTaskResume | 設定だけを変更。ONならRunning Taskを再評価、OFFでも開始済み応答とその作業は止めない |
| CancelTask | Cancelledと世代更新を保存。未開始Run / 不要Requestを閉じ、進行中処理を停止・照合 |
| ChatGPT Stop / Reload / 会話切替 | 接続・応答の観測だけ更新。TaskをPause / Cancel / Completedにしない |
| Master回答 | Runningなら回答に必要な続行を許可。Pausedなら回答保存のみ |
| Hub / Nirai / PC再起動 | 未完了TaskをPausedで復元。Resume設定を保持し、自動でRunningへ戻さない |

Pause / Cancel後の古い応答からのTool要求と遅延送信は拒否する。Pauseの状態表示と実処理の停止完了は別であり、必要ならActivityに「停止処理中」を表示する。

Pauseで未送信のPending応答RunはCancelledにし、再開時は新応答を予約する。受付済みのPending action Runは保持できるが、旧世代のまま開始しない。MasterのResume時に未実行・同じ入力・Scope・承認を再検査してdispatch_epochを更新したものだけ再許可する。受付時の世代と親Runは履歴として残す。先に待機中の処理を整理し、同じ操作を新応答から重複要求しない。

Mainの短い送信処理と許可失効を直列化し、送信直前にTask世代・Run・宛先・Draftを再検査する。開始済みのクリックや短い書込が取り消せなければその結果を照合する。Pause受付後に古いタイマーから新しくクリックしない。Mainの送信停止確認を受けるまでは、画面に物理停止完了と表示しない。

起動復旧では、Pendingは未開始と確認できたものだけ保持する。RunningだったRunは外部Process・書込記録・配送を照合して結果を保存し、確定不能ならInterrupted＋effects unknownを保持する。応答の許可は失効させ、Master再開後も古い応答を再利用しない。結果不明を未実行扱いにして新Runへ複製しない。

Command WorkerはHubとの接続断を検知したら新規操作を止め、管理中Processの停止を試みる。外部Processや子孫まで停止した証拠がなければ「停止済み」としない。残存処理の確認・停止が必要ならMaster Requestとして提示する。

明示終了は、新規受付停止→未完了TaskのPaused保存・許可失効→Main送信停止→実処理停止と結果保存→DB close→Process終了の順。強制終了や電源断は起動復旧で扱う。Main消失時にもHubは新規受付を閉じて同じ停止を試みる。再起動後の安全をProcess自動消滅だけに依存させない。

## 11. Master Request

承認と質問を同じRequestで扱う。`kind=approval|input`、状態は`Pending / Resolved / Cancelled`。ID、Task、対象Run、revision、質問・理由、Masterに提示する内容、固定した提案操作と入力指紋・作業範囲、回答者・回答・時刻を保存する。

承認は具体的な差分またはCommand・引数・対象へ結び付ける。回答待ちに対象内容が変わったら元承認では実行しない。AI自身はApproveできない。回答はRequest IDを指定し、通常のChat送信でCHECKを消さない。

Approveは当該操作だけを許可する。Rejectはその提案を閉じ、未開始の対象RunをCancelledにする。Task取消や不要になった質問も明示的に閉じる。一つのRequestで無関係なRunまで停止しない。

Master回答は明示的な続行指示なので、RunningならResume OFFでも必要な応答を呼べる。Pausedなら保存だけで再開を待つ。終了した応答を復活させず、必要なら新応答Runで回答と対象を取得する。

再起動後、未実行と確認できるPending操作は承認対象が同一であることを再検査する。InterruptedへのRetryで承認を引き継げるのは、同じ操作・入力・範囲で未実行と確認できる場合だけ。変更・部分適用・結果不明では旧承認を流用しない。同じ回答の再送は同じ結果を返し、実行を増やさない。承認済みという状態と、停止後に実行を再許可することは別であり、再開時は§10の検査を通す。

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
| `local.inspect` | 当該Runの出力末尾・終了結果・実処理状態を限定取得 |
| `local.cancel` | 当該Runの停止要求。停止完了と部分適用は別途記録 |

File操作は正規化した実パスでScopeを確認し、外部へ抜けるリンク・junction・共有書込を拒否する。Hub DB、Credential、実行中の製品版は通常の作業対象から除外する。Nirai Sourceは明示されたTask Scopeに含められる。AIはScopeを変更できない。

変更前の指紋を適用直前に再照合し、退避と適用記録を確保してから書く。複数ファイルを一括で原子的に変更できると仮定しない。中断時は適用済みを記録し、安全に復元できる時だけ戻す。他者の変更があれば上書きせずpartialとして止める。実書込・復元が落ち着くまで占有を解放しない。

Commandはshell文字列連結を標準経路にせず、具体的argvで起動する。初期は対象Projectの既知の検索・テスト・buildを、内容を確認した実行Profileとして登録する。Profileは実行ファイル・引数制約・許可出力先・起動Script等の指紋を持ち、変更時には再検査する。実行したSource / Scriptの版と結果をRunへ残す。

cwdやstagingはOS権限の隔離ではない。Project内Scriptでも任意のファイル・ネットワークを扱えるため、未確認の任意Script、依存導入、公開、破壊的Git操作を無条件で許可しない。実行前に内容と外部影響を確認し、上記承認対象ならRequestを出す。初期は自動で実行可能と判断できないものを明示的に保留する。秘密をCommand引数やログへ流さず、出力量と時間を制限する。

## 13. Conversation / Say / Task Chat

ConversationはMasterとResident、Resident同士に共通の会話基盤。ID、参加者、Message、作成更新時刻を保存する。Messageは送信者、本文、順序番号、必要なTask / Run / Request参照を持つ。会話自体はTask状態を持たない。

Sayは通常会話をWorld上で扱う場であり、内部実行ログを流さない。Task Chatは一つのTaskに対応するHub内Conversation。Master指示の正本はここに保存し、Holoからも人間向けの報告を共通Message経路で記録する。

ChatGPT会話の全文を常時同期する基盤は作らない。Webへ直接入力された文だけでTask目的・承認・状態を変更せず、MasterがTask指示として採用する時はHubのMessage Commandで保存する。

Task Contextは現在の目的・Scope・完了条件、Masterの未処理/直近指示、結果要約、未処理Run / Request、必要時に取得できる参照から組み立てる。Chat全文・全ログを毎回Promptへ詰め込まない。要約を再生成しても元の指示と結果は参照できる。これは長期Memory実装を前提としない。

## 14. AI同士の連携

Resident同士の人格的な会話は、共通Conversationの参加者を変えて扱う。HoloとSerina等がMaster不在でも会話を継続できることを最終要件とする。専用の会話DBやCollaboration Thread制御は追加しない。

仕事の委譲は、担当応答Runから別AI Capabilityを子Runとして呼び出し、結果をTaskへ戻す。会話と仕事の実行を混同せず、双方とも共通の権限・安全確認に従う。初期自走化に別AIへの委譲は必須ではない。

## 15. Local Memory

Local Memoryは標準搭載する独立Capabilityで、Operationは`remember / recall / forget`。正本はローカルに保存し、検索Index / Embeddingは再生成可能な派生データとする。特定AI Providerがなくても最低限の保存・取得が成立する。

Task / Runの状態を持たず、Archiveを自動的に長期Memoryにしない。HoloやSerina等の別Projectが管理するMemoryは吸収せず、必要なら外部能力として接続する。Memory StoreはHub Storeと別に持ち、検索技術・外部AI利用はその境界内で実装する。初期自走化後に着手する。

## 16. Resident / Persona

Residentは不変ID、表示名、Role、Persona参照、使用Capability、Model、Avatarを持つ。表示名変更でTaskや保存先の識別を変更しない。RoleやPersonaの自然文を実行権限の根拠にしない。

Persona本文はFileを正本とし、旧本文を変更せず移行する。WORLD_RULES本文を複製しない。通常Residentは設定されたAI Capability、Holoは専用Connectorを使う。未接続Residentの利用可能状態・Usageを推測して表示しない。

## 17. Holo Connector

### 17.1 Task専用Conversation

HubはTaskごとに専用ChatGPT Conversationへの対応を保存する。保存するのはProvider、外部Conversation ID / URL、対応の版、作成要求ID。これは送信先でありTask ownershipではない。表示中の会話を自動的にTaskへ採用しない。

最初の応答では、Hubが応答Runと作成要求を保存し、MainがHolo資源を占有する。Draft・生成・Loginを確認した上で新規会話へ一度だけ初回依頼を送る。Mainが確定した外部Conversation IDを観測し、HubがTaskとの対応を保存するまでTool実行を許可しない。Holoが先に受領を通知した場合は対応確定待ちとして返す。

初回クリック後に切断し会話作成の成否が不明なら、自動で別会話を増やさない。同じ作成要求・delivery_idを照合し、確認不能ならMasterへ対象会話の特定を求める。紛失時の新会話への付け替えはMaster操作とし、旧送信・実処理を整理してから対応版と実行許可を更新する。旧会話の応答から新Task処理を受け付けない。

一つのHolo表示を使う初期構成でも、Task A / Bの対応先を混ぜない。別会話にDraftや生成がある場合は上書き・自動切替せずblocked理由と必要操作をDashboardへ返す。障害対応後はHubの保存先を再取得する。

### 17.2 観測・送信・応答

ConnectorはWeb利用可否、対象Conversation、生成中、ComposerのDraft、Login要求を観測し、`ready / busy / blocked / unavailable`と理由をHubへ通知する。DOMの短い観測や再接続は境界内で行うが、Task継続・完了判断や独立した永続Auto Resume Queueを持たない。

Hubが応答Runを予約すると、Connectorは指定された一通を送る。初回はv2 Toolを使うための短い導入指示を含め、以後の呼び起こしは原則Task ID、Run ID、delivery_id、当該応答だけの接続許可を渡す。依頼本文やRun履歴を再送ごとに複製せず、HoloがHubから現在情報を取得する。

Holoは`AcceptResponse`で受領、`GetTaskContext`で取得、`InvokeCapability`でTool要求、`SendConversationMessage`で報告、`FinishResponse / CompleteTask`で応答結果を返す。Local MCP橋渡しはこれらの共通Commandを公開し、旧Workflow ToolやHubを通らないFile / Shell経路をv2用Tool構成へ含めない。

生成終了後に正規報告が来なければ、猶予時間後にInterruptedとして照合へ進む。生成が続く場合にも応答Runの絶対期限を持つ。`busy -> ready`、自然文の「完了」、送信成功だけで応答やTaskを成功にしない。

### 17.3 配送記録

応答Runに次を保存する。別Delivery Entity / Outbox DBは作らない。

- `delivery_id`、固定した送信先と対応版、送信内容の指紋
- `delivery_state=unsent|started|acknowledged|unknown`
- 送信開始・確認時刻、同一送信の試行回数、確認根拠

送信予約を保存してからMainへ渡す。Mainは送信直前の条件を再検査し、Hubがstartedを保存した確認を得てからクリックする。直前のDraft・対象会話・許可失効もMainの送信Gateで検査する。

| 状況 | 処理 |
|---|---|
| クリック以前の失敗と証明できる | 同じRun・同じdelivery_idで有限回Retry |
| クリック後、受領確認前に切断 | unknown。照合するまで自動再送・別ID発行を禁止 |
| 正の受領証拠あり | acknowledged。応答の結果を待つ |
| 受領済み応答が失敗・中断 | 副作用と待機処理を整理した後、新Run・新delivery_idで続行可能 |

受領証拠は、対象Conversationのuser messageにある不変の送信ID、または当該応答の認証済みAcceptResponse。DOMに見えない、Reloadで履歴が少ない、localStorageに印がないことは未送信の証拠にしない。再起動でRunをInterruptedにしても配送記録は保持する。

Web配送の完全な一回保証は仮定しない。重複した応答が来ても、Hubが同一応答とCommand IDを検査し、Toolの二重受付を拒否する。

### 17.4 呼出元の権限

MainとLocal MCP橋渡しの認証情報をChatGPTページへ渡さない。HoloにはHubが発行した推測不能な`response_token`だけを渡す。これは対象Task・応答Run・control_epoch・会話対応版・期限へ限定され、Master操作や他Taskを許可しない。Hubは指紋だけを保存し、Promptに含める限定Tokenをログでは伏せる。

Local MCP橋渡しは接続認証に加えて各Tool要求のresponse_tokenをHubへ渡す。Hubは送信済みの対象、対応確定、Run許可を確認する。Task IDやdelivery_idだけでは認証しない。現在表示中のTaskから呼出元を推測しない。

応答終了、Pause / Cancel、対応変更、Hub再起動、期限切れでTokenを失効させる。未知の古い要求を新応答へ付け替えない。Hub停止時はTool利用不能を返し、橋渡し側へ未送信操作を溜めない。汎用Master権限を持つpreloadを外部Webへ公開しない。

## 18. Settingsと有限な待ち

動的設定・Resident設定はHub Storeに集約する。Persona等の直接編集FileとProviderのCredentialは各境界を正本とし、DBへ秘密を複製しない。実行中のRunは受付時設定を参照し、設定変更で入力や承認対象をすり替えない。

初期値は一つの設定定義に置き、Connector / UIへ別々に埋め込まない。

| 項目 | 初期値と期限時の処理 |
|---|---|
| 未送信と証明できる通信Retry | 初回込み3回、再試行前1秒・2秒。上限後は理由を表示して待機 |
| 配送確認 | 15秒。証拠なしはunknownにして照合 |
| 生成終了後の終了報告猶予 | 15秒。報告なしはInterrupted |
| 応答Runの絶対期限 | 30分。新規Tool受付を閉じ、中断として照合 |
| 同じ原因での自動応答Retry | 初回込み3回。DOM変化や再送だけでは回数をリセットしない |
| Command実行期限 | 既定5分、通常上限30分。延長は実行内容を確認し、高負荷なら承認 |
| Command出力 | stdout + stderr合計8 MiB。超過時は停止を要求し、上限到達を記録 |
| Controlメッセージ | 1 MiB。大きな出力は成果物参照と限定読取を使う |
| 初回の変更Command受付期限 | 発行後5分。保存済みCommandの同一結果取得は別扱い |

Login、Draft、Master RequestはRetryで突破しない。復旧操作や新指示で再評価する。タイムアウトは成功や物理停止の証拠ではなく、終了・副作用確認まで必要な資源を保持する。

## 19. 保存構造

Hub StoreはData Rootの`hub.sqlite3`一つ。Node Hubだけが読み書きし、Main / UI / Local MCP / Capability Workerは共通APIを使う。`foreign_keys=ON`、WAL、`synchronous=FULL`を初期設定とする。重いファイル処理や外部IO、Master回答をtransaction内で待たない。

| Table / 記録 | 正本と制約 |
|---|---|
| tasks | §6。Task ID不変、状態・revision・control_epochを条件付き更新 |
| runs | §7・§17。Task参照、親Run参照、delivery_id一意。同Taskの未終了response Runは最大一つ |
| master_requests | §11。対象と提案内容を固定、回答は一度だけ確定 |
| conversations / messages | §13。Task ChatはTaskごとに一つ、Message順序番号一意 |
| residents / settings | 不変Resident ID、動的設定の正本 |
| provider_bindings | Task専用Conversationの対応。一つの外部会話を複数Taskへ対応させない |
| command_receipts | 呼出主体＋command_id、一致すべき入力指紋、受付時刻、返却結果 |

Resource予約、失敗解決、承認根拠、成果物参照はRunに付属する記録として扱う。Queryに必要なら従属Tableへ正規化してよいが、別の状態正本・独立Queueにはしない。

状態更新とCommand受付結果を同じtransactionで保存し、その後に通知・実行を行う。通知は状態の変更を知らせるだけで、別のEvent Storeを正本にしない。UIはrevision付きSnapshotを再取得でき、接続断で失った通知を復元するための独立Outboxは不要。

大きな結果はRun専用Folderへ一時名で保存・flush・置換し、内容指紋と所有者をDBへ登録してから成功通知する。DB登録前に残ったファイルは未参照物として回収可能。書込の退避・適用途中記録は結果Logと区別し、復旧まで削除しない。

Schema版とアプリ版を記録する。Migrationは新規実行を閉じ、SQLiteの整合したBackupを取得してからtransactionで行う。WAL稼働中のDB本体だけをコピーしない。対応外のSchema、Migration失敗、DB破損は起動エラーとして示し、空DBの作成や暗黙のdown migrationで隠さない。未知の版へ移ったDBを旧アプリで開かない。

Local Memoryは責務が異なるので独立Storeを持つ。初期Hub起動にMemory DB、Embedding、v1 Memory依存を必要としない。

## 20. Control API

### 20.1 共通Commandと権限

UI / Holo / Local Toolの意味上の入口は同じHub handlerへ集約する。任意のTask / Run状態を書き換えるAPIは公開しない。

| 呼出主体 | 公開操作 |
|---|---|
| Masterの信頼済みUI | CreateTask、UpdateTaskDefinition、PauseTask、ResumeTask、SetTaskResume、CancelTask、ResolveMasterRequest、RebindTaskConversation、StartConversation、SendConversationMessage、UpdateResident、UpdateSettings、CompleteTask |
| 許可された応答Run | AcceptResponse、GetTaskContext、RefineTaskDefinition、InvokeCapability、RequestMasterInput、SendConversationMessage、ResolveRunFailure、FinishResponse、CompleteTask |
| Hub / 登録Adapter | 実Run結果、配送観測、停止・後始末結果、Capability状態の報告 |

AIは承認を要求できても承認回答を送れない。approval Requestは具体的操作を検査したPolicy Gateが作成する。AIが自由な文章の承認を作り、それを汎用許可に変換しない。MasterのCompleteTaskも同じ完了検査を通し、作業をやめる時はCancelTaskを使う。

RefineTaskDefinitionは担当応答からのタイトル・完了条件の具体化だけを受け付け、目的・Scopeの変更や必須条件の削除を許可しない。ResolveRunFailureは§7・§8の根拠検査を通して扱いを記録し、実Run結果を書き換えない。読み取ったMaster指示の順序番号をContextへ含め、完了要求はその範囲を明示する。

取得用QueryはTask一覧・詳細、Context、Request一覧、限定したRun結果、Capability状態、Settings。必要な範囲だけ返す。応答Runは自分のTask以外を操作・取得できない。UIへの結果も秘密情報を含めない。

### 20.2 受付と古い要求

共通Envelopeは`protocol_version / command_id / issued_at / type / target / expected_revision / payload`。Run由来の要求にはrun_id、control_epoch、response_tokenを付ける。呼出主体は接続認証とTokenからHubが確定し、payloadのactor自己申告を信用しない。

認証後、保存済みcommand_idがあれば入力指紋を比較し、同一なら保存済み結果、別内容ならconflictを返す。初回は期限、Schema、対象、権限、許可世代、必要なrevisionを検査してから処理する。受付と副作用の開始を同じ呼出元の再送で二重化しない。

Masterの設定・内容更新・承認・手動完了にはexpected_revisionを要求し、古ければ現在値を返して再表示する。Pause / Cancelは最新の同じTaskへ安全側の停止を適用できる。AIの実行要求は応答世代・固定したContext範囲で検査し、無関係なログ更新のrevisionだけで拒否しない。完了時は未処理の新指示も確認する。

受付成功は`accepted + run_id/request_id`または確定結果を返す。長時間実行のacceptedは成功完了ではない。再接続時は同じcommand_idの結果を照会する。新しいIDで盲目的に再送しない。拒否理由は少なくともunauthorized、stale、conflict、blocked、invalid、unavailableを区別する。

Run結果通知はAdapterに渡した当該Run用の呼出権限で受ける。Pause後の結果受理は許すが新実行を許さない。完了済みCommandの返却と未知の古いCommandの実行許可を分ける。

### 20.3 Transport

Mainは信頼済みNirai UIのframe / originを検証してMaster Commandを転送する。外部Web frameからのMaster Commandを受け付けない。MainとHubのMessagePortはアプリ内部の専用経路とし、通信IDで要求・返答を対応させる。

Local MCP橋渡しはv2同梱の小さなNode Module。外部のTool接続から共通Envelopeへ変換するだけで、Engineや独立したProcess Job Storeを持たない。Hubへの接続はWindows named pipe、JSONの長さ付きメッセージ、上限§18を使用する。公開TCP Listenerは設けない。

pipe名・起動識別・起動ごとの推測不能な接続Secretを、Data Rootの接続Fileに保存し、当該Windows利用者だけが読める権限にする。Secretを引数・ログ・Webへ渡さない。終了時に無効化し、古い接続Fileだけでは次のHubへ認証できない。pipe Secretは橋渡しの接続許可であり、Holo操作にはさらに§17.4のTokenを要する。

同じWindows利用者として任意コードを実行できる相手に対する完全な隔離は、この認証だけでは提供しない。ローカルCommandの実行可否は§12で制限する。

## 21. Dashboard

見た目・配置・Resident欄・Task / Archive欄・Task Chatの基準は`prototype/`。実データの動作は本書を正本とし、モックの状態更新処理を制御として移植しない。

表示するのは、Task状態、Resume、現在または最近のRunを要約したActivity、Master Request、Capability状態、取得できるUsage / Limit、結果。固定Step / DAGや手動attentionを裏の正本にしない。詳細Logは必要な時だけ開く。

| UI操作・表示 | 接続契約 |
|---|---|
| ＋ / 最初の指示 | §22の作成と開始。二重クリックは同じcommand_id |
| Pause / 再開 | Task状態操作。Resume設定は変えない |
| Resume ON / OFF | 設定だけ変更。ON時の再評価はHubが行う |
| CHECK | Pending Requestを持つTask件数。RUN / PAUSEとの重複を許す |
| 承認 / 質問への回答 | Request IDと具体的対象を表示して回答。任意Chat送信では解決しない |
| 完了を確定 | Hubの完了検査を通す。拒否された条件を表示 |
| Taskを取り消す | 確認後CancelTask。成功完了に変換しない |
| Terminal / Archive | Completed / Failed / Cancelledを区別。再開・内容変更を禁止 |
| 接続切れ | 未保存・未確定を表示し、成功を先行表示しない。再接続時はHubを再取得 |

RUNはRunning Task、PAUSEはPaused Task、COMPLETEはCompleted Taskから計算する。未着手のPausedには「入力待ち」、Running + OFFで応答待ちには「指示待ち」、実処理停止前には「停止処理中」をActivityとして表示できる。これらを追加Task状態にしない。

モックの「完了扱い」は実接続時に上表の「完了を確定」と取消へ分け、未完了Runを強制成功にしない。最初の指示と通常の回答欄は区別し、CHECKがあるだけで通常Messageを承認として解釈しない。

## 22. Task作成

Dashboard上部でResidentを選び、＋で即座に未着手Paused TaskとTask Chatを作る。事前ダイアログは挟まない。最初の指示送信前だけ担当Residentを変更できる。

最初のMaster Message保存・目的の初期設定・Runningへの遷移・明示呼び起こし予約は一つのCommandで確定する。Resume初期値はOFF。担当AIは内容からタイトルと完了条件を具体化する。最初の指示がないTaskのResumeは拒否する。

作業対象はMasterが設定した既定Workspace、または依頼で明示した許可対象から決める。曖昧なら読取可能な情報で具体案を作り、必要な範囲だけ質問する。AIに全PCを書込可能とする既定Scopeを渡さない。開始後の担当変更は初期機能に含めない。

Task ChatへのMaster追加指示は共通SendConversationMessageで保存する。Paused中の送信だけではTaskを再開しない。再開は明示操作に分ける。Terminalへの続きの依頼は、旧成果物を参照する新Taskとして作成する。

## 23. Archive / Retention

TaskのArchiveはTerminal Taskを同じHub Storeから表示する区分。別状態・別Storeへ移さない。終了から90日経過したTaskと専用の一時結果・Logを整理対象にする。

成果物参照には所有Taskと、一時物・Project本体・共有物・復旧資料の区分を付ける。Project本体、共有成果物、Local Memory、他Taskから参照中の結果、未確定副作用の復旧資料はTask削除に連鎖させない。必要な参照を保全・移し替えてから削除し、Task専用でないConversationも巻き込まない。

未終了実処理や復旧未完了があるTerminal Taskは整理を保留し、理由を表示する。削除済みTask / Run IDの要求は拒否する。Command受付期限を過ぎた新規要求も拒否し、古いCreateTask等の再送で履歴を再生成しない。削除はHubの既知の専用Folderと所有情報から行い、AI指定パスを再帰削除しない。

文書の`archive/`はこの機能とは別。初期計画の退役はWORLD_RULESと計画の退役手順に従う。

## 24. 外部能力の追加

新しいAI / Toolは原則Capability Adapterの追加で接続し、EngineやDashboardへProvider名ごとの分岐を増やさない。共通契約に合わない個別機能は、まずCapability固有Operationで表現する。

Python資産を採用する場合は、当該Adapterが入力検証済みのRunをWorkerへ渡し、結果・停止・後始末をHubへ返す。Python側にTask状態、承認、Queue、Hub DBのWriterを追加しない。必要Runtimeの配布と終了管理もそのCapabilityの責務とし、Hub全体の起動要件にしない。

## 25. 既存資産の採用境界（移行期間のみ）

v1は再利用元。以下の判定単位で採用し、v2の構成入口からv1 Core全体を起動しない。「そのまま」は部品責務を変えず薄い接続で利用する意味であり、接続先との検証は行う。本章は移行完了後に削除する。

| 判定 | 資産 | 採用単位・条件 |
|---|---|---|
| そのまま再利用 | `prototype/index.html`、`prototype/styles.css`、`Img/` | UI構造・見た目・画像。状態制御は含めない |
| そのまま再利用 | `residents/*/persona.md` | 本文を変更せず保持し、指紋で保全 |
| そのまま再利用 | `world/src/renderer/src/world/vrm/`のVrmLoader / AnimationController / LipSyncController | Task依存のない描画部品。World拡張時に採用 |
| そのまま再利用 | `world/src/renderer/src/world/environment/EnvironmentController.ts`と描画依存 | 表示機能。旧起動・状態管理を含めない |
| 責務を剥がして再利用 | `world/src/main/holo/HoloWebHost.ts` | WebContentsView、Login領域、Navigation / Permission制限。Queue、watchdog、owner、Task復元を除去 |
| 責務を剥がして再利用 | `world/src/main/holo/holoWeb.ts`、`world/src/shared/holoDom.ts`、`holoAutoResume.ts` | DOM可視性・送信可否、Conversation URL比較、送信ID照合。旧Prompt、Trigger、Task取消への接続を除去 |
| 責務を剥がして再利用 | `tools/holo-transport.mjs`、`core/holo/auth.py` | 接続制限・認証・秘密伏せの必要部分。v2のpipe / 権限へ置換し、旧message / ownerを持ち込まない |
| 排除・作り直し | 初期Toolに対する`core/agents/safety.py`、`cursor_workspace.py`の直接移植 | 初期Toolに必要なパス検査、固定差分、変更前照合、退避・復元・取消待ちをTSで実装。旧Mixin / AgentSession / 承認・保存制御は持ち込まない |
| 排除・作り直し | 初期Hubに対する`core/brains/process_manager.py`、`core/atomic_json.py`、`core/world_rules.py`の実行依存 | 有限なProcess管理、原子的File置換、一つのRules読込はNodeで実装。このためだけのPython常駐部を作らない。後続のPython Capabilityでは純粋部品としての再利用が可能 |
| 責務を剥がして再利用 | `core/agents/cursor_credentials.py`、`codex_credentials.py` | 限定Credential・env・後始末。AgentSessionへの結合を外す。各Capability着手後 |
| 責務を剥がして再利用 | `core/agents/cursor_acp.py`、`codex_app_server.py`、`core/brains/` | Provider通信と応答解析。独自Task状態・承認・Memory注入を除去。native Sessionは参照だけ |
| 責務を剥がして再利用 | `core/usage_budget.py`、`usage_providers.py` | 取得・解析・不明の表現。旧routingや重いAdapter依存を除去 |
| 責務を剥がして再利用 | `core/residents/service.py` | Persona / Avatar等の入力検査。表示名識別・設定書換・Role別実行を除去 |
| 責務を剥がして再利用 | `world/src/renderer/src/runtime/CoreConnection.ts`、`SceneRuntime.ts`、旧IPC | 要求応答対応・描画組立・限定資産アクセス。旧Protocol / Store / Auto Resume配線を除去 |
| そのまま再利用 | `core/memory/lexical.py` | Memory着手後、Python Capabilityを採用した場合の文字列分割・照合関数。初期Hubの依存にはしない |
| 責務を剥がして再利用 | `core/memory/structured.py`、`private.py` | Memory着手後に必要な検索・照合技術だけ。旧会話取込・Provider依存正本を除去 |
| 責務を剥がして再利用 | `tools/world-build-state.mjs` | 入力指紋、build前後照合、build排他。Holo Runtimeへの保存・Workflow完了条件を除去 |
| 責務を剥がして再利用 | `core/incidents.py`等の診断・Logging | Hub / Connectorの障害表示と秘密伏せに必要な部分だけ。旧復旧Storeを移植しない |
| 排除・作り直し | `core/server.py`、`task_queue.py`、`task_runtime.py`、旧起動処理 | v2 Hub構成入口、Task / Run Store、共通Commandとして作る |
| 排除・作り直し | `core/holo/workflow.py`、`tools/holo-workflow*.mjs` | Task / Runの開始・完了検査へ集約。Workflow / Leaseを作らない |
| 排除・作り直し | `core/agents/manager.py`、`store.py`の実行正本 | HubのRunへ統合。Agent Sessionを第二正本にしない |
| 排除・作り直し | `HoloAutoResumeOutbox`、HostのQueue / watchdog、task_owners / owner tombstone | Runの配送記録とHubの実行許可へ統合 |
| 排除・作り直し | `world/src/preload/holo.ts`のmaster-stop→Task取消 | ChatGPT Stopは応答観測のみ。Task停止はMaster Command |
| 排除・作り直し | `core/sessions/chat_store.py`、`core/conversation/runtime.py`、旧Memory会話取込 | HubのConversation / Message。旧Journal・Session・Memory outbox制御を移植しない |
| 排除・作り直し | `prototype/app.js`の模擬Task更新、旧Agent中心UI Store / Bootstrap | 表示用Projectionとv2 Commandへ配線。強制Completed・任意ChatでCHECK解除を持ち込まない |
| 排除・作り直し | 旧互換Migration、全テストの機械移植 | v2の不変条件を守る必要な保証だけをテストする |

既存Local MCPのFile / Process実装を部品として使う場合も、実装を確認し、Run帰属・Policy・停止・結果保存を共通契約へ接続できるものに限る。外部Tool定義があるだけで採用済みとはしない。初期はv2の最小Toolで成立させる。

v1のHolo Local連携はv2経路が成立するまで開発用の足場として保持する。v2完走の証拠には数えない。v1の停止・削除・データ移行は別作業とし、初期自走化前に先行撤去しない。

## 26. 自分自身の開発と受け入れ条件

### 26.1 実行版と開発対象の分離

通常の開発Taskは許可されたSourceを編集し、候補版を別出力先へbuildする。実行中の配布版を逐次上書きしたり、製品起動へ開発用hot reloadを接続したりしない。RunにはSource指紋、候補build ID、検証結果、実行アプリ版、Schema版を記録する。

候補版の起動確認は検証用Data Rootで行う。通常Taskの完了と、使用中のNiraiをその版へ切り替える操作は分ける。初期は自動Updaterを作らず、Masterが明示的に切替を実行する。

切替はTaskを安全停止し、DBと必要な復旧資料をBackupし、対応Schemaを確認して旧版を終了してから新版を起動する。旧版へ戻す場合は互換DBを使うかBackupを戻す。Migration後の非互換DBを旧版へ渡さず、Backup時点以降の変更が失われる復元はMaster確認を要する。

### 26.2 初期自走化の必須保証

| ID | 受け入れる保証 |
|---|---|
| AC01 | HubだけがTask / Run / Requestを保存し、二重Command・重複Eventでも開始が増えない。UI再接続で正本へ戻る |
| AC02 | Resume OFFで一度のHolo応答から調査→修正→検証の複数Toolを使える。応答後の自動送信は0件 |
| AC03 | Running + ONで必要な次応答を呼び起こす。Master明示指示、ON切替、資源解放、接続復旧を取りこぼさない |
| AC04 | Pause / Cancel / Terminal後の新規実行・古い応答を拒否し、遅延結果と部分適用は保存。実処理確認前に資源を解放しない |
| AC05 | 再起動後は未完了TaskをPausedで復元し、Resume保持。生存Process・配送・書込不明を自動再実行しない |
| AC06 | 専用Conversationの作成から受領・Tool・終了報告まで通り、別会話 / Draft / busy / Loginを保護。不明配送を自動再送しない |
| AC07 | AI自己承認・対象差替え・古い承認を拒否。Paused中の回答は実行しない。Running + OFFでMaster回答した仕事は続けられる |
| AC08 | 未終了Run・不明副作用・未解決Request・未処理指示・必須検証失敗が残る完了を拒否。呼出元応答自身は原子的に終了できる |
| AC09 | Chat / Web Stop / Reload / 切替 / Window閉鎖でTask状態不変。CHECKは指定Request回答で解決し、取消と成功を分けて表示 |
| AC10 | Scope外操作とPolicy迂回を拒否し、Nirai Sourceの小変更・検証はv2 Toolだけで実行できる |
| AC11 | 実際のNirai開発Taskを、v1 Core / Workflow / Outboxを経由せず完了。応答が一区切りついた後のAuto Resume、成果物の版と検証、Dashboard反映まで記録できる |
| AC12 | 候補版を隔離検証し、明示切替後もTask履歴と設定を読め、新版上の短いTaskが完走。旧版へ戻せる範囲を確認できる |

恒久テストはこれらを少数の状態組合せ・主要フロー・境界テストへまとめる。代替Capability / Fake DOMによる自動検証と、実Electron / ChatGPT / Processを使う確認は分けて報告する。実接続の成立を古いテスト件数で代用しない。

### 26.3 初期自走化後の必須要件

Local Memoryのローカル正本と独立性、Resident同士の共通Conversation、複数TaskとRunの資源単位の並列実行、Capability追加で接続できる拡張性、Persona保全、90日Retentionによる共有成果物保護を満たす。初期機能の完成をNirai全体の完成と呼ばない。
