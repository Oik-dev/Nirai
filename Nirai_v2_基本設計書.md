# Nirai v2 基本設計書

> 本書はNirai v2の現行仕様の正本である。共通の設計・開発・文書運用原則は`WORLD_RULES.md`を参照する。実装順序と各段階の出口は、初期自走化の期間だけ`Nirai_v2_初期自走化計画.md`で管理する。

## 1. 目的

Niraiは、AI・長期Memory・Tool・外部サービス・Worldを自然に組み合わせて使う、Master専用のAI Hubである。

PCに例えるならHubはマザーボード、Memoryは内蔵ストレージ、AIは演算能力、Toolは周辺機器、World / Dashboardは表示・操作面に相当する。Holoは専用の接続方式を持つ重要な能力として接続する。接続のために分散SystemやMessage Brokerを導入せず、同じProcess内では直接呼び出す。

## 2. 到達点と初期範囲

最終的には、MasterとResidentの会話、Resident同士の会話、複数Taskの同時実行、調査・開発・生成、Web / File / App操作、ローカル長期Memoryを共通Hubから扱えるようにする。

最初の到達点は、**Dashboardから渡したNirai自身の開発Taskを、HoloがNirai-MCPで調査・修正・検証し、Resume ONならCompleteTaskまで自動継続して完遂できること**とする。UIは初期から実Hubへ配線する。

この段階の必須能力はHolo Web Adapter、Nirai-MCP、ローカルFile / Process Tool。Memory、Serina本接続、Cursor、Codex、追加Capability、Resident同士の自律会話、高度な並列Schedulingはその後に実装する。初期からTask間の識別と資源競合の境界は守るが、将来機能を動作するように見せる仮実装は作らない。

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
| Nirai Renderer | Dashboard / Task Chat / Worldの表示。sandbox有効、Node無効、限定preload経由でCommandを呼ぶ |
| Holo Web Adapter | ChatGPT Webの送信・生成観測・assistant Message取得。Node無効、contextIsolation / sandbox有効。Master用IPCや汎用ローカル操作を公開しない |
| 必要時だけ起動するWorker / 子Process | 大きなファイル処理、外部Command等。Hub DBを直接開かず、限定したRunの結果を返す |

画面・Holo接続とHubの言語・配布物を揃えつつ、DB処理やHub障害を画面のProcessから分離するため、この構成を採る。初期の必須機能にPython常駐部は不要であり、将来Python資産を使う場合も、そのCapability内部のWorkerとして必要部分だけ呼ぶ。

MainとHubは非同期MessagePort通信、UIはMainの限定IPCを経由する。MainへTask判断を置かない。Hubは`node:sqlite`の`DatabaseSync`を利用し、DB操作を短く保つ。同期DB処理や重い探索をMainへ載せない。utilityProcessはProcess分離であり、任意Commandを安全に閉じ込めるOS sandboxではない。

使用APIは[Electron utilityProcess](https://www.electronjs.org/docs/latest/api/utility-process)、[Node SQLite](https://nodejs.org/api/sqlite.html)に従う。依存版はv2のlockfileで固定し、Electron同梱Node上でDB・IPC・終了動作を確認して更新する。システムに別途入ったNode / PythonをHub起動の前提にしない。

### 3.3 配置と起動

- v2のSource / package / lockfile / build設定は`v2/`へ独立配置する。Main、Hub、shared契約、Renderer、Capability、Local MCP橋渡しをその配下へ置く。
- 製品Data Rootは`%LOCALAPPDATA%/Nirai-v2/`。Hub DB、Run成果物、ログ、接続情報、HoloのElectron userDataをここへ分けて置く。v1のRuntime / Login保存領域を共有しない。
- 検証用Data Rootは明示指定した別Folderを使う。同じData Rootを複数のHubで開かない。
- Data Rootは実在Folderの実パスへ解決してから排他名とDBパスを決める。junctionや別表記から同じDBを二重に開かない。Electron userDataもそのData Root配下へ設定し、製品・検証間でCookie、localStorage、単一起動の識別を共有しない。
- Mainは単一起動を確保し、app ready後にHubを起動する。HubはData Rootから決まるWindows named pipeを排他的に確保してからDBを開く。確保失敗時は起動を止め、既存Hubを殺したりDBを初期化したりしない。
- Hub内Module用に別Service、WebSocket Server、Broker、Supervisorは作らない。外部Local MCP橋渡しだけが認証付きnamed pipeを使用する（§20）。
- Hubの準備完了と復旧結果を受け取るまでは実行操作を無効にする。Hub切断時はMainの新規送信を止める。既存Hubの終了とpipe解放を確認した後に限り、予期せぬ終了からの限定的な自動再起動を許可する。無限respawnは行わず、再起動後はHub DBの復旧結果を再取得してから操作を再開する。
- Windowの×は「Niraiを終了」として扱い、§10の停止保存を行ってアプリを終了する。Trayは起動中の再表示・明示終了用に使うが、×でWindowだけを隠す動作にはしない。

## 4. 不変条件

1. Task、Turn、Action Run、Request、設定の同じ状態を複数箇所へ二重に正本化しない。
2. Holoの人間向け発言はChatGPT上で生成されたassistant Messageを唯一の本文とし、Nirai用に再生成しない。
3. HoloはAddonとして、`GetTaskContext`からWORLD_RULESとTask Contextを取得して動作する。
4. Taskの完了は`CompleteTask`だけで確定する。
5. `RequestMasterInput`が未解決ならMasterを待つ。それ以外の未完了TaskはResume ONなら次のHolo Turnへ進む。
6. Holo Turn自体はMaster環境への副作用を持たない。File変更・Process等の副作用、安全確認、停止・復旧はAction Runだけで扱う。
7. Provider Conversation / Sessionは送信先と推論Contextの参照であり、Taskの目的・完了・実行権限・復旧状態の正本にしない。
8. Master環境へのNirai管理下の操作はHubのCapabilityとPolicy Gateを通す。外部Toolの直接経路で迂回しない。
9. 結果不明のActionを未実行や成功と解釈せず、同じ副作用を盲目的に再実行しない。
10. Pause / Cancel / Terminal後の古いTurnやActionから新規実行を許可しない。遅延結果は保存するがTaskを勝手に復活させない。

## 5. Capability契約

Registryは明示登録とし、動的Plugin探索を初期必須にしない。Capabilityは以下を提供する。

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

TurnはNiraiからHoloへ一度処理を渡す最小単位。Task、Turn ID、control_epoch、開始・終了時刻を持つ。

Turnは次のいずれかで閉じる。

- ChatGPTのassistant生成が終了した。
- 25分上限、Timeout、Session Error、接続断等で現在Turnを継続できない。
- Pause / Cancelで実行権限を失った。

Turn終了理由からTask状態を増やさない。Taskが未完了なら§8の同じ判定へ戻す。

### 7.2 Action Run

Action RunはCapabilityを一回利用した記録。Holo Turnから要求するFile / Process / その他Tool利用をAction Runとして保存する。

保存項目は、ID、Task、親Turn、Capability / Operation、状態、受付時control_epoch、副作用区分、固定した入力・作業範囲、結果・エラー、実処理参照、開始終了時刻。

状態は`Pending / Running / Completed / Failed / Cancelled / Interrupted`。副作用があり得るOperationだけ`effects=none|applied|partial|unknown`とcleanup確認を持つ。

Terminal Actionを再実行状態へ戻さない。再試行は必要なら新しいAction Runとして行う。結果不明の副作用や未完了cleanupがある間だけ、競合ActionとTask完了を止める。

## 8. Task Engineと完了

EngineはHub内の小さなModule。保存済み状態から次のHolo Turnを開始してよいかを判断する。

### 8.1 Holo Turnの開始

Turnを開始できるのは、TaskがRunningで、別のActive Turnがなく、未解決Master Requestと未確定の危険Actionがなく、Holoが利用可能な場合だけ。

開始理由は次の二つだけ。

- 未処理のMaster入力がある。
- Resume ONでTaskが未完了である。

Master入力、Request回答、明示ResumeはTask Chatの同じMessage列へ保存する。Webへ送るPromptはMCP接続名、Active Turn ID、`GetTaskContext`の取得指示だけとする。

### 8.2 Holo発言の反映

ChatGPT上でActive Turnに対して生成されたassistant Messageを、その本文のままTask Chatへ保存する。Nirai用の回答を別生成しない。

assistant生成終了でTurnを閉じる。Task完了は`CompleteTask`で確定する。

### 8.3 継続判定

Turn終了またはHolo処理の中断後は、常に次の順で判断する。

1. `CompleteTask`済みならTask終了。
2. 未解決`RequestMasterInput`があればMaster待ち。
3. Paused / Cancelled / Failedなら停止。
4. 未確定の危険Actionがあれば安全確認を待つ。
5. それ以外は未完了。Resume ONなら次Turn、OFFなら待機。

通常のHolo発言、25分区切り、Timeout、Session Error、Web切断を別々の復旧フローにしない。いずれも「Taskが未完了なら同じ継続判定へ戻る」で扱う。

### 8.4 完了

Task完了の正本は`CompleteTask`だけ。

`CompleteTask`は未解決Request、Pending / Running Action、未確定effects / cleanup、未処理Master入力、必須完了条件・検証を確認してからTaskをCompletedにする。個別Actionの確定済み失敗履歴だけを理由に完了を拒否しない。

Master UIからの明示完了も同じ安全・整合検査を通す。作業を打ち切る場合はCancelTaskを使う。

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
| 25分 / Timeout / Session Error / Web切断 | 現Turnを閉じ、Task未完了なら§8.3へ戻る |
| Master回答 | Requestを解決して再評価する |
| Hub / Nirai / PC再起動 | 未完了Taskと開始済みActionを復元・照合し、安全が確定するまで新規実行しない |

Resumeは未完了Holo Taskの通常継続である。

古いTurnからのMCP要求はcontrol_epochとActive Turnで拒否する。Actionの副作用が不明な場合だけAdapter側の照合を続け、安全が確定するまで競合実行とTask完了を止める。

## 11. Master Request

承認と質問を同じRequestで扱う。`kind=approval|input`、状態は`Pending / Resolved / Cancelled`。ID、Task、対象Run、revision、質問・理由、Masterに提示する内容、固定した提案操作と入力指紋・作業範囲、回答者・回答・時刻を保存する。

承認は具体的な差分またはCommand・引数・対象へ結び付ける。回答待ちに対象内容が変わったら元承認では実行しない。AI自身はApproveできない。回答はRequest IDを指定し、通常のChat送信でCHECKを消さない。

approvalの提案は、Policy Gateが受け付けたPending action Runの`capability_id / operation / input / workspace_scope`から固定する。別内容の提案や対象Runのない汎用承認を受け付けない。承認回答は`approved: boolean`、質問回答は空でない`text`を要求し、形式が違う回答ではRequestを解決しない。

Approveは当該操作だけを許可する。Rejectはその提案を閉じ、未開始の対象RunをCancelledにする。Task取消や不要になった質問も明示的に閉じる。一つのRequestで無関係なRunまで停止しない。

Master回答はRequestを解決してTaskを再評価する。Runningなら必要なHolo Turnを開始でき、Pausedなら回答だけ保存して再開を待つ。終了したTurnを復活させない。

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
| `local.inspect` | 当該Runの出力末尾・終了結果・実処理状態を限定取得 |
| `local.cancel` | 当該Runの停止要求。停止完了と部分適用は別途記録 |

File操作は正規化した実パスでScopeを確認し、外部へ抜けるリンク・junction・共有書込を拒否する。Hub DB、Credential、実行中の製品版は通常の作業対象から除外する。Nirai Sourceは明示されたTask Scopeに含められる。AIはScopeを変更できない。

変更前の指紋を適用直前に再照合し、退避と適用記録を確保してから書く。複数ファイルを一括で原子的に変更できると仮定しない。中断時は適用済みを記録し、安全に復元できる時だけ戻す。他者の変更があれば上書きせずpartialとして止める。実書込・復元が落ち着くまで占有を解放しない。

Commandはshell文字列連結を標準経路にせず、具体的argvで起動する。初期は対象Projectの既知の検索・テスト・buildを、内容を確認した実行Profileとして登録する。Profileは実行ファイル・引数制約・許可出力先・起動Script等の指紋を持ち、変更時には再検査する。実行したSource / Scriptの版と結果をRunへ残す。

cwdやstagingはOS権限の隔離ではない。Project内Scriptでも任意のファイル・ネットワークを扱えるため、未確認の任意Script、依存導入、公開、破壊的Git操作を無条件で許可しない。実行前に内容と外部影響を確認し、上記承認対象ならRequestを出す。初期は自動で実行可能と判断できないものを明示的に保留する。秘密をCommand引数やログへ流さず、出力量と時間を制限する。

## 13. Conversation / Say / Task Chat

ConversationはMasterとResident、Resident同士に共通の会話基盤。Task Chatは一つのTaskに対応し、Master指示とHoloの人間向け発言を保存する。

MasterのTask入力はNiraiから送る。ChatGPTへ直接入力された文をTask指示の正本にしない。

HoloのTask発言は、Niraiが開始したActive Turnに対してChatGPT上で生成されたassistant Messageをそのまま保存する。GPT用とNirai用の二つの回答を作らず、HoloからTask Chatへ本文を再送させない。

`GetTaskContext`はWORLD_RULES、目的、Scope、完了条件、直近会話、Request、必要なAction結果参照、利用可能Capabilityだけを返す。内部状態や全履歴を渡さない。

## 14. AI同士の連携

Resident同士の人格的な会話は、共通Conversationの参加者を変えて扱う。HoloとSerina等がMaster不在でも会話を継続できることを最終要件とする。専用の会話DBやCollaboration Thread制御は追加しない。

仕事の委譲は、担当Taskから別AI CapabilityをAction Runとして呼び出し、結果をTaskへ戻す。会話と仕事の実行を混同せず、双方とも共通の権限・安全確認に従う。初期自走化に別AIへの委譲は必須ではない。

## 15. Local Memory

Local Memoryは標準搭載する独立Capabilityで、Operationは`remember / recall / forget`。正本はローカルに保存し、検索Index / Embeddingは再生成可能な派生データとする。特定AI Providerがなくても最低限の保存・取得が成立する。

Task / Runの状態を持たず、Archiveを自動的に長期Memoryにしない。HoloやSerina等の別Projectが管理するMemoryは吸収せず、必要なら外部能力として接続する。Memory StoreはHub Storeと別に持ち、検索技術・外部AI利用はその境界内で実装する。初期自走化後に着手する。

## 16. Resident / Persona / Holo

Residentは不変ID、表示名、Role、Persona参照、使用Capability、Model、Avatarを持つ。Persona本文はFileを正本とし、WORLD_RULES本文を複製しない。

HoloはNiraiへ接続するAddonとして扱う。WORLD_RULESとTask固有情報は`GetTaskContext`から取得する。

RoleやPersonaの自然文を実行権限の根拠にしない。未接続Capabilityの利用可能状態・Usageを推測して表示しない。

## 17. Holo Web Adapter

### 17.1 責務

ChatGPT WebはHoloの実行環境、Nirai Task Chatは正式な会話UIとする。WebContentsViewは裏側のAdapterとして送信・生成観測・assistant Message取得だけを担当する。「Holoを開く」はLogin、確認、保守用であり通常操作の必須手順にしない。

Conversation ID / URLはProvider内の送信先ヒントとする。利用不能ならAdapterが新しいConversationを使う。Task ownership、完了、実行権限、復旧状態はHubが管理する。

### 17.2 送信

Turn開始時のWeb Promptは次だけで構成する。

- Nirai-MCP接続名
- Active Turn ID
- `GetTaskContext`を取得してTaskを続行する指示

WORLD_RULES、Master入力、Task履歴、内部状態はWeb Promptへ複製しない。Holoは最初に`GetTaskContext`を取得し、その内容に従って処理する。

### 17.3 assistant Messageの同期

AdapterはActive Turnで新しく生成されたChatGPT assistant Messageの完了を確認し、その本文を変更せずHubへ保存する。Tool実行中の一時的な生成停止は完了とみなさない。

### 17.4 中断

送信前と証明できる短い通信失敗だけAdapter内で有限Retryしてよい。送信後の成否が不明なら同じTurnを盲目的に再送せず、そのTurnを閉じて権限を失効させる。

25分上限、Timeout、Session Error、Web切断、Conversation破損も同じくTurn終了として扱う。Task状態や専用Recovery stateを増やさず、§8.3の継続判定へ戻す。

古いTurnはActive Turn ID / control_epochでNirai-MCPの新規操作権限を失う。過去Turnから遅れて表示された内容や要求を現在Turnへ付け替えない。

## 18. Settingsと有限な待ち

動的設定はHub Storeに集約する。初期値は一つの設定定義に置き、Connector / UIへ別々に埋め込まない。

| 項目 | 初期値と処理 |
|---|---|
| 送信前Retry | 初回込み3回。送信後不明は同じTurnを再送しない |
| Holo Turn上限 | 25分。到達したらTurnを閉じ、Task未完了なら§8.3へ戻る |
| Command実行期限 | 既定5分、通常上限30分。高負荷や延長はPolicyに従う |
| Command出力 | stdout + stderr合計8 MiB。超過時は停止要求と記録 |
| Controlメッセージ | 1 MiB。大きな出力は成果物参照と限定読取を使う |

Login、Draft、Master Request、未確定ActionをRetryで突破しない。これらは条件が解消した時に同じEngine判定を再評価する。

## 19. 保存構造

Hub StoreはData Rootの`hub.sqlite3`一つ。Node Hubだけが読み書きし、Main / UI / Local MCP / Capability Workerは共通APIを使う。

| Table / 記録 | 正本と制約 |
|---|---|
| tasks | §6。Task ID不変、state / revision / control_epoch / resume_enabled |
| holo_turns | §7。TaskごとにActiveは最大一つ。Turn IDとcontrol_epochでHolo権限を限定 |
| runs | §7。Action Runだけを保存。副作用・cleanup・成果物はここへ結び付ける |
| master_requests | §11。対象と提案内容を固定、回答は一度だけ確定 |
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
| Masterの信頼済みUI | CreateTask、UpdateTaskDefinition、PauseTask、ResumeTask、SetTaskResume、CancelTask、ResolveMasterRequest、SendTaskMessage、UpdateResident、UpdateSettings、CompleteTask |
| Active Holo Turn | GetTaskContext、GetRunResult、InvokeCapability、RequestMasterInput、CompleteTask |
| Hub / 登録Adapter | Turn開始・終了、assistant Message観測、Action結果、停止・後始末結果、Capability状態 |

Holoは承認回答を送れない。approval Requestは具体的操作を検査したPolicy Gateが作成する。Task定義変更はMaster操作へ集約する。

人間向け本文は§17.3、Turn終了はAdapter観測を正本とする。

### 20.2 受付と古い要求

共通Envelopeは`protocol_version / command_id / issued_at / type / target / expected_revision / payload`。外部Holo要求にはActive Turnを選ぶ識別子を付ける。

認証後、HubはTaskがRunning、TurnがActive、control_epoch一致、期限内であることを検査して新規Commandを許可する。保存済みcommand_idの同一結果取得は、新規実行権限と分けて返してよい。

Pause / Cancel / Complete、Turn終了、Hub再起動、期限切れ後の古い要求は拒否する。未知の古い要求を新Turnへ付け替えない。

### 20.3 Transport

Mainは信頼済みNirai UIだけからMaster Commandを受ける。外部Web frameへMaster権限を公開しない。

Local MCP橋渡しはv2同梱の小さなNode Moduleとし、認証付きnamed pipeから共通Commandへ変換するだけにする。独立Engine、Queue、Task状態を持たない。

接続Secretは当該Windows利用者だけが読めるData Root内へ保存し、引数・ログ・Webへ渡さない。Holo側の権限は接続認証に加えてActive Turn / Task control_epochで検査する。

## 21. Dashboard

見た目・配置・Resident欄・Task欄・Task Chatの基準は`prototype/`。実データの動作は本書を正本とし、モックの状態更新処理を制御として移植しない。

表示するのは、Task状態、Resume、現在または最近のRunを要約したActivity、Master Request、Capability状態、取得できるUsage / Limit、結果。固定Step / DAGや手動attentionを裏の正本にしない。詳細Logは必要な時だけ開く。

縦長または狭幅ではDashboardを画面下側へ寄せ、高さは概ね58vhを上限として上側にWorld表示領域を残す。Task欄とTask Chatは左右配置を維持し、縦画面だからという理由だけで上下積みにしない。

| UI操作・表示 | 接続契約 |
|---|---|
| ＋ / 最初の指示 | §22の作成と開始。二重クリックは同じcommand_id |
| Pause / 再開 | Task状態操作。Resume設定は変えない |
| Resume ON / OFF | 設定だけ変更。ON時の再評価はHubが行う |
| CHECK | Pending Requestを持つTask件数。RUN / PAUSEとの重複を許す |
| 承認 / 質問への回答 | Request IDと具体的対象を表示して回答。任意Chat送信では解決しない |
| 完了を確定 | Running / PausedのTaskをMasterが明示完了。Hubの安全・整合検査を通し、拒否された条件を表示 |
| Taskを取り消す | 確認後CancelTask。成功完了に変換しない |
| Terminal Task | Completed / Failed / CancelledをTask一覧内で区別。元Taskの再開・内容変更は禁止。Completedの「再開」は旧結果を参照する新Taskを作る |
| 接続切れ | 未保存・未確定を表示し、成功を先行表示しない。再接続時はHubを再取得 |

RUNはRunning Task、PAUSEはPaused Task、COMPLETEはCompleted Taskから計算する。未着手のPausedには「入力待ち」、Running + OFFで応答待ちには「指示待ち」、実処理停止前には「停止処理中」をActivityとして表示できる。これらを追加Task状態にしない。

モックの「完了扱い」は実接続時に上表の「完了を確定」と取消へ分け、未完了Runを強制成功にしない。最初の指示と通常の回答欄は区別し、CHECKがあるだけで通常Messageを承認として解釈しない。

## 22. Task作成

Dashboard上部でResidentを選び、＋で未着手Paused TaskとTask Chatを作る。最初のMaster Message保存、目的初期設定、Running遷移を一つのCommandで確定する。

Holoを使うTaskだけResume設定を持ち、初期値はOFF。最初の指示がないTaskのResumeは拒否する。

作業対象はMasterが設定したWorkspaceまたは明示許可対象から決める。AIに全PCを書込可能な既定Scopeを渡さない。

Task ChatへのMaster追加指示はHubへ保存してHolo Turnの入力にする。Paused中の送信だけではTaskを再開しない。Terminal Taskの続きは旧成果物を参照する新Taskとして作る。

## 23. Terminal表示 / Retention

ARCHIVEタブは設けず、Terminal Taskも同じTask一覧へ統合する。Completedは終了後72時間だけ一覧に表示し、Masterの「閉じる」で即時に表示対象から外せる。Failed / Cancelledは確認のため一覧に残し、「閉じる」で非表示にできる。この非表示はUI上の表示制御でありTask状態やHub保存記録を変更しない。終了から90日経過したTaskと専用の一時結果・Logを整理対象にする。

成果物参照には所有Taskと、一時物・Project本体・共有物・復旧資料の区分を付ける。Project本体、共有成果物、Local Memory、他Taskから参照中の結果、未確定副作用の復旧資料はTask削除に連鎖させない。必要な参照を保全・移し替えてから削除し、Task専用でないConversationも巻き込まない。

未終了実処理や復旧未完了があるTerminal Taskは整理を保留し、理由を表示する。削除済みTask / Run IDの要求は拒否する。Command受付期限を過ぎた新規要求も拒否し、古いCreateTask等の再送で履歴を再生成しない。削除はHubの既知の専用Folderと所有情報から行い、AI指定パスを再帰削除しない。

文書の`archive/`はこの機能とは別。初期計画の退役は同計画の定義に従う。

## 24. 外部能力の追加

新しいAI / Toolは原則Capability Adapterの追加で接続し、EngineやDashboardへProvider名ごとの分岐を増やさない。共通契約に合わない個別機能は、まずCapability固有Operationで表現する。

Python資産を採用する場合は、当該Adapterが入力検証済みのRunをWorkerへ渡し、結果・停止・後始末をHubへ返す。Python側にTask状態、承認、Queue、Hub DBのWriterを追加しない。必要Runtimeの配布と終了管理もそのCapabilityの責務とし、Hub全体の起動要件にしない。

## 25. v1との境界（移行期間のみ）

v1はv2の仕様正本ではないが、失敗例だけでなく有用な機能・知見・実装資産を持つ参照元として扱う。v1由来という理由だけで採用も拒否もしない。候補ごとに現在のv2の目的・不変条件・Capability契約・責務境界へ照らし、次のいずれかとして判断する。

- **Reuse**: 現在の契約と責務へそのまま適合し、第二の正本や旧Lifecycleを持ち込まない。必要最小限の調整で再利用する。
- **Redesign**: 機能、知見、外部仕様への対応は有用だが、状態管理や責務分割がv2へ適合しない。目的だけを残し、v2の契約上で組み直す。
- **Reject**: 現在の目的に不要、またはv2の不変条件・安全境界・単一正本を損なう。互換性や過去実装の存在だけを理由に残さない。

UI / 画像、Persona本文、VRM・環境描画等の純粋資産はReuse候補とする。Provider通信、認証、DOM判定、Usage取得、Memory検索、Resume等の機能や知見も候補に含めてよい。ただしTask状態、承認、復旧判断、Queue等の制御責務を旧構造のまま接続せず、必要ならRedesignしてHubとCapabilityの現在契約へ収める。

以下は既知の失敗構造としてRejectする。ここで拒否するのは機能目的ではなく、v1で採られていた構造そのものである。同じ目的が必要なら、v2の契約に沿ってRedesignする。

- Task Queue / Task Runtime、Workflow / Lease / Heartbeat / watchdogによる第二のTask制御系
- Agent SessionをTask状態の正本とする構造
- Holo Resume専用Queue / Outbox、task owner / tombstone、Conversation ownershipによるTask制御
- ChatGPT StopをTask取消へ結び付ける経路
- Chat Store / Conversation Store / Memory outbox間で同じ仕事の状態を同期する構造
- v1互換のためだけのMigration、Adapter、状態、テスト
- v1のCore / World制御Moduleをv2から直接importし、v2の正本や制御経路として動かすこと

v1のRegression Testを参照する時は、守るべき失敗条件をv2のInvariant / Critical Flow / Boundaryへ翻訳する。当時の修復機構やFixture構造を、理由なく機械的に移植しない。

Local MCPやProvider実装の既存コードを参照する場合も、必要な処理だけをReuseまたはRedesignする。Task状態、承認、Task単位の実行許可・復旧判断はHubへ戻し、Capability内に第二の正本を作らない。Capability内部だけで完結する通信Retry等は、そのCapabilityの責務としてよい。

v1のHolo Local連携はv2経路が成立するまで開発用の足場としてだけ保持する。M8の完走証拠には数えず、v2の実行経路から参照しない。本章は移行完了後に削除する。

## 26. 自分自身の開発と受け入れ条件

### 26.1 実行版と開発対象の分離

通常の開発Taskは許可されたSourceを編集し、候補版を別出力先へbuildする。実行中の配布版を逐次上書きしない。Action RunにはSource指紋、候補build ID、検証結果、実行アプリ版、Schema版を記録する。

候補版は検証用Data Rootで確認し、使用中Niraiの切替はMasterの明示操作に分ける。切替前にTaskとActionを安全停止し、DBと必要資料をBackupする。

### 26.2 初期自走化の必須保証

| ID | 受け入れる保証 |
|---|---|
| AC01 | HubだけがTask / Turn / Action Run / Requestを保存し、重複Eventでも二重開始しない |
| AC02 | Resume OFFでは一つのHolo Turn内で複数Toolを使え、Turn終了後に自動次Turnを作らない |
| AC03 | Resume ONではCompleteTaskまたはMaster待ちまで未完了Taskを継続する。通常発言、25分、Timeout、Session Error、接続復旧を同じ原理で扱う |
| AC04 | Pause / Cancel / Terminal後の古いTurn / Actionを拒否し、遅延結果と部分適用は保存する |
| AC05 | 再起動後もTaskとActionの正本を保ち、未確定副作用を自動再実行しない |
| AC06 | NiraiからChatGPTへ送信し、HoloがNirai-MCPを使い、ChatGPTのassistant Message本文をそのままTask Chatへ反映できる |
| AC07 | AI自己承認・対象差替え・古い承認を拒否し、Request回答後は同じEngine判定へ戻る |
| AC08 | CompleteTaskだけがTask完了を要求でき、未終了Action・未解決Request・未処理指示・必須検証失敗が残る完了を拒否する |
| AC09 | Chat / Web表示 / Reload / Conversation切替でTask状態を二重管理せず、UI再接続でHub正本へ戻る |
| AC10 | Scope外操作とPolicy迂回を拒否し、Nirai Sourceの小変更・検証をv2 Toolだけで実行できる |
| AC11 | 実際のNirai開発Taskをv1制御へ戻らず、Resume ONでCompleteTaskまで自走してDashboardへ全Holo発言と結果を反映できる |
| AC12 | 候補版を隔離検証し、明示切替後もTask履歴と設定を読め、新版上の短いTaskを完走できる |

恒久テストは上記を少数のInvariant / Critical Flow / Boundaryへまとめる。Fake Providerの成功を実ChatGPT接続成立へ読み替えない。

### 26.3 初期自走化後の必須要件

Local Memoryの独立性、Resident同士の共通Conversation、複数TaskとAction Runの資源単位並列、Capability追加性、Persona保全、Retentionによる共有成果物保護を満たす。

## 27. v2全体の完了条件と依存順

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

外部サービスの接続不能、Masterの認証・契約・具体的承認が必要な場合は、停止地点と必要操作を明示する。代替Capabilityの成功を実接続成立へ読み替えず、利用不能な能力を使えるように表示しない。自動Updater、汎用Plugin基盤、高度なSchedulingは本節の出口に不可欠な場合以外は追加しない。
