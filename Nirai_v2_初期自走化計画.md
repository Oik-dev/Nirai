# Nirai v2 初期自走化計画

> 本書は、Holoがv2だけでNirai自身の開発Taskを完遂できる状態までの期間限定建設計画である。動作仕様は`Nirai_v2_基本設計書.md`を唯一の正本とし、本書は実装範囲・順序・各段階の出口を定める。AC番号は基本設計§26.2を参照する。

## 到達点

DashboardからHoloへ渡した仕事を、v2のTask / Run、Control API、ローカルTool、Holo Connector、Auto Resume、Master Requestで完遂する。

物理構成は基本設計§3のElectron＋TypeScript Hub子Processに従う。共通契約と実装を`v2/`へ置き、v1の起動処理や状態正本を初期構成へ組み込まない。

UIモックはM2から実Hubへ配線する。安全境界は各段階で作り、M7は実接続での統合確認に使う。前段階の契約が成立しないまま、後段階の機能で埋め合わせない。

## M1. Hub最小核

基本設計§3〜11・§18〜20を対象に、以下を一つの起動可能な骨格として作る。

- Electron Mainから一つのHub utilityProcessを起動し、終了・接続断を扱う。
- v2専用Data Root、単一起動、SQLite Schema / transaction / Backup、初期Migration。
- Task、Run、Request、Resident（Holo）、Conversation / Message、設定、Command受付記録。
- 共通Command handler、呼出権限、実行許可の世代、入力指紋、配送情報、失敗・副作用の記録。
- 最小RegistryとEngine。実行能力は小さな代替Capabilityで検証する。
- UIへSnapshotと変更通知を返す非同期経路。

**出口:** AC01・AC04・AC05・AC07・AC08のHub内境界を代替Capabilityで確認する。同じ開始要求、古い応答、停止後の遅延結果、未完了TaskのPaused復元を保存し直しても矛盾させない。同じData Rootの二重起動を拒否し、DB障害を空DBで隠さない。

外部IOをtransaction内で待たないこと、Main / RendererをDB処理で止めないことも確認する。実ChatGPTとローカル書込の成立はこの段階の合格に含めない。

## M2. UI配線

基本設計§13・§21・§22に従い、`prototype/`の見た目・配置・レスポンシブ構造を実Hubへ接続する。外観の全面再設計はしない。

- ＋での未着手Task作成、最初の指示、タイトル、担当Resident、Task選択。
- Task / Activity / Chat、Pause / 再開、Resume ON / OFF。
- CHECKの対象Requestと具体的な承認・質問への回答。
- 検査付き完了確定、取消、Terminal区分、Archive表示。
- 接続切れ・受付未確定・操作拒否理由・再接続時の再取得。

モックの模擬応答・状態書換は表示用Projectionと共通Commandへ置換する。共通Commandの別実装をUI側へ作らない。

**出口:** 実Hubと代替Capabilityを使い、AC01・AC09を画面操作で確認する。二重クリック、Reload、CHECK中の通常Chat、実行中の完了要求、未着手Taskの再開、接続断を扱える。UIを配線済みとして、次段階から同じ画面で実処理を確認する。

## M3. v2 Control APIとローカルTool

基本設計§5・§12・§17.4・§20を対象に、Holo用Local MCP橋渡しと最小Toolを実装する。

- named pipe接続、接続Secret、応答限定Token、Command ID、対象・世代・期限検査。
- Context取得、報告、Tool要求、質問、応答終了・完了要求を既存handlerへ接続。
- File読取・検索、変更前照合付きpatch、有限なCommand実行、結果取得・停止。
- 変更退避・部分適用・Process識別・接続断時の停止、結果と検証対象の版の保存。
- 初期Projectのテスト・build実行Profileと共通Policy。Master承認が必要な具体的操作の表示。

**出口:** 許可した小さな検証用Workspaceで読取→編集→Command→結果保存を通し、AC04・AC07・AC10を実ファイルとProcessで確認する。Scope外、古い応答、AI自己承認、提案差替え、二重要求、停止中の書込を拒否または正しく照合できる。

この段階はLocal Toolの到達性まで。実ChatGPTからこの橋渡しを使えることはM4で確認する。

## M4. Holo Connectorと実接続

基本設計§17に従い、Main内のWeb表示・安全な一通送信とHubのRunを接続する。

- 専用Conversationの作成・確定・Taskとの対応保存。
- Login、対象会話、生成中、Draft、blocked理由の観測。
- 保存してから送信、delivery_id照合、結果不明の扱い、有限な通信Retry。
- 応答限定Tokenの受領、Hub Context取得、構造化した応答結果。
- Reload / 再接続後の対応取得と、旧会話からの要求拒否。

v2専用のTool構成を用意し、Hubを通らない既存File / Shell Toolを自走経路に混ぜない。Holo用の初回導入指示へ旧Workflow操作を入れない。

**出口:** 実Electron / ChatGPTで、Dashboardの新規Task→専用会話作成→AcceptResponse→Task取得→小さな読取Tool→終了報告→Dashboard反映を通す。別会話とDraftを保護し、AC06の正常経路を確認する。

専用会話作成、認証したTool到達、結果返却のいずれかが成立しなければ、接続境界を修正してから進む。代替Capabilityの成功で実接続の合格にしない。

## M5. Resume OFFで1 Task完走

基本設計§6〜8・§26の一度の応答を成立させる。

**出口:** 実Holoへ小さな開発Taskを渡し、一度の応答内でFile読取→変更→実検証の複数Tool Runを行い、CompleteTaskで終了する。Task ChatとDashboardへ成果物・検証結果を反映する。AC02・AC08・AC10を満たし、旧Core / Workflowによる制御を使わない。

完了条件に届かず応答が終了した場合も、Resume OFFでは自動送信しないことを確認する。Masterの追加指示からは明示的に続行できる。

## M6. Auto Resume

基本設計§8〜10・§17に従い、保存状態に基づく次応答の呼び起こしを有効にする。

**出口:** 実Holoが残作業を記録して一度の応答を終えた後、Running + Resume ONで次の応答から続行し、完了まで到達する。AC03・AC06を確認する。ON切替、Capability復旧、資源解放でも再評価され、重複Eventで応答Runが増えない。

Tool / Request / 不明配送の待機中には新しい応答を自動生成しない。Resume OFF、Paused、Terminalでは自動送信されない。Master回答による明示続行をAuto Resumeと混同しない。

## M7. 停止・異常・再起動の統合確認

各段階で実装済みの契約を、AC04〜AC09として実接続を含めて確認する。

| 境界 | 確認する地点 |
|---|---|
| Holo送信 | 保存後・クリック前、クリック後・確認前、受領後・終了報告前 |
| Task制御 | 生成中Pause / Cancel、OFF切替、古い応答からのTool、Terminalへの遅延結果 |
| Web | Draft、別会話、生成Stop、Reload、Login、一時切断 |
| Master Request | 未回答、却下、Paused中回答、再起動、承認後の入力変更 |
| ローカル操作 | patch適用中、復元中、Process実行中、停止後の子Process残存 |
| Hub / Main | 正常終了、Hub強制終了、Main切断、同じData Rootへの再起動 |
| 完了 | 自分自身の応答終了、必須検証失敗、未処理新指示、結果不明の残存 |

意図的な障害注入は検証用Data Root / Workspaceに限定し、稼働中v1や実Projectの成果物を壊さない。PC再起動を実施する場合は承認条件に従う。

**出口:** 未完了TaskはPausedで復元され、Master再開後に保存結果から続けられる。不明操作を自動で再実行せず、誤宛先・二重副作用・未検証完了がない。Fake DOM等で確認した条件と実接続で確認した条件、未検証の範囲を分けて示す。

M7は全ての異常を毎回繰り返す恒久手順にはしない。守るべき契約は少数の自動テストへまとめ、外部接続は必要な統合点で確認する。

## M8. v2 Self-host Cutover

基本設計§26.1の分離を守り、実際の小さなNirai v2開発TaskをMasterから受けて完遂する。検証だけのダミー修正で製品開発の完了証拠を代用しない。

1. v2のUIから依頼を受け、v2 ToolでSourceを読み、変更し、必要な検証を行う。
2. 途中でHoloの応答を一区切りさせ、Auto Resumeで次の応答が作業を引き継ぐ。
3. 候補版をbuildし、検証用Data Rootで起動確認する。
4. 成果物の版・Run結果・残課題を保存し、HubでTask完了、Dashboardで結果を確認する。
5. Masterの明示操作で候補版へ切り替え、Task履歴・設定を確認し、新版上で短いTaskを完走させる。

**出口:** AC11・AC12を満たす。v1 Core / Workflow / Outboxとの接続を持たない検証構成で成立し、v1制御を呼ばなかったことを起動構成と実行記録で確認する。稼働中v1の停止・削除を合格の前提にしない。

証拠は対象Taskの通常の結果として、変更対象、Source / build ID、実検証Run、Auto Resume地点、必要だったMaster操作、v1制御不使用、残る制限を短く保存する。

## この期間に先行させないもの

Memory本実装、Serina本接続、Cursor / Codex、Resident同士の自律会話、Web / Image等の追加Capability、複数Taskの本格並列化、高度なResource Scheduling、自動Updater、汎用Plugin基盤はM8後へ送る。

90日Retentionの自動清掃は後続でよいが、所有関係・共有成果物・復旧資料の保護はM1から保存契約へ入れる。後続機能を後回しにすることは、基本設計の最終要件を廃止する意味ではない。

## v1の扱い

初期自走化が成立するまでは、必要なv1の開発用経路を保持する。資産の採用判定は基本設計§25に従う。M8後の不要v1経路の退役・削除・データ移行は別作業として扱う。

## 本書の退役

M1〜M8の出口が全て成立した時点で本書を`archive/`へ移す。同時にREADMEとAI_ENTRYの現行計画参照を外し、その後の設計・実装はWORLD_RULESと基本設計を基準にする。

退役した計画は現行仕様の根拠にしない。実装時に確定した恒久仕様が本書だけに残っていれば、退役前に基本設計へ統合し、重複した契約を残さない。
