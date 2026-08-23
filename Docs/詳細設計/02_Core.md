# Nirai 詳細設計 02：Core（調停役）

正本は [Nirai_基本設計.md](../Nirai_基本設計.md)。メッセージ形式は [01_通信プロトコル.md](01_通信プロトコル.md)。

## 概要

Coreとは、Niraiの裏で動く常駐サービスであり、頭脳呼び出し・会話の交通整理・記憶・省エネ・拡張ロードを担う調停役である。

- 前提
  - Python 3.11以上、単一プロセス（asyncioで並行処理）
  - **Brain呼び出しは常に同時1件まで**（直列）。理由：サブスク枠とPC負荷の保護。順番待ちはキューで管理する
  - Worldがいなくても動く（演出が見えないだけ）。会話ログはファイルに残る

## 内部構成（モジュール分割の指定）

| モジュール | 役割 |
|-----------|------|
| server | WebSocketサーバー、World子プロセスの起動・監視 |
| registry | 住人の読み込み（residents\走査）と状態保持 |
| sessions | セッション管理（会話の調停） |
| ticker | 生活ティックのスケジューラ |
| budget | 行動予算の管理 |
| brains | Brainドライバ群（03参照） |
| memory | World Memory / Private Memoryの読み書きとRetriever（06参照） |
| tasks | タスクの相談・実行（07参照） |
| ecomode | Worldの表示状態に応じた省エネ制御 |
| extensions | 拡張ロード（07参照） |

## 起動フロー

1. config.tomlを読む（無い・壊れている場合はエラーを表示して終了）
2. residents\配下からresidents.enabledに載っている住人を読み込む（06の形式）
   - 読み込みに失敗した住人はスキップし、WARNログ＋会話UI通知（その住人は「留守」）
3. runtime\state.jsonがあれば復元（住人のLocation、当日の予算消費、最終ティック時刻）
   - 日付が変わっていたら予算消費をリセット
4. extensions\を走査して拡張をロード（07参照）
5. WebSocketサーバー起動 → Worldを子プロセス起動
6. ティックのスケジュールを開始

## セッション管理

### セッションとは

ひとまとまりの会話。参加者・種別・発言履歴を持つ。**同時にアクティブなセッションは1つまで**。新しい会話のきっかけが起きたときに別セッションが動いていたら、先入れ先出しのキューに積む。ただしMaster発のセッション（say / whisper / task）はキューの先頭に割り込む。

### セッション種別

| 種別 | きっかけ | 参加者 | 終了条件 |
|------|---------|--------|---------|
| master_talk | master_say / master_whisper | say＝全住人、whisper＝宛先のみ | 最後の発言から10分無応答、またはMasterの新しい話題 |
| resident_chat | 住人のティック行動`talk_to` | 発起人と相手（2人） | 両者パス、または6ターン（3往復）で打ち切り |
| task_consult | task_request | 全住人 | 担当決定、または8ターンで打ち切り（07参照） |

### Master向けチャットセッション

UI上のチャットセッションは`runtime\chat_sessions\`で管理する。

- `chat_session_create`：新しいSession IDを発行して選択する。Temporary Contextだけを新しくする
- `chat_session_select`：過去セッションを選択し、その続きとして会話できる
- `chat_session_delete`：UI履歴だけを削除する。World Memory Episodeは残す
- `world_memory_forget_session`：対応EpisodeだけをWorld Memoryから削除・Retriever対象外にする。UI履歴は残す
- 左Sidebar用一覧は`index.json`から返し、タイトル生成のためだけにBrainを呼ばない

### master_talk（Sayの場合）のフロー

1. World UIから`master_say(text, request_id)`を受信
   - `request_id`をMaster発話1回の処理単位として登録する
   - 現在選択中のチャットセッションファイルへ発話を記録し、Worldへ`chat_append`
   - Worldへ`response_state {active:true, request_id}`を返す
2. 選択中セッションのTemporary Contextへ追加する
3. 全住人について順番に（登録順・直列）：
   a. Brainに「会話モード」で問いかける（03のプロンプト構成。セッション履歴＋World Memoryから必要な公開記憶を渡す。Private MemoryはSayでは渡さない）
   b. 応答の`say`が空でなければ：Worldへ同じ`request_id`付きの`bubble`＋`action`（例：faceでMaster側を向く、talk、expression）、現在セッションファイルへ`request_id`付きで記録、Worldへ`chat_append`
   c. 応答が`pass`なら発言なし（その住人は今回黙っていた扱い。ログはDEBUGのみ）
4. 全対象Residentの処理が終わったらWorldへ`response_state {active:false, request_id}`を返す
5. Masterが続けて発言したら、新しい`request_id`で2へ戻る（履歴が積み上がる）
6. 公開会話の一区切り時：Sayと公開Resident発話だけからEpisodeを作りWorld Memoryへ保存する（06参照）。Whisperは入力から除外し、Residentごとに同じ要約を複製しない

- Whisperの場合の差分
  - 3を宛先の住人1人だけに行う
  - **秘匿規則（厳守）**：Whisperの内容は、宛先以外の住人のBrain入力・World Memory・公開AI Contextへ一切含めない。MasterのUI用チャットセッション履歴には残る
  - 終了時は宛先ResidentのPrivate MemoryへWhisperログを保存し、継続状態`context.md`を必要な範囲だけ更新する

### resident_chat のフロー

1. 住人Aのティック行動が`talk_to(B)`だった場合、セッションを開始する
2. Worldへ演出指示：Aに`move`（BのいるLocationへ）→ 双方に`face`
3. ターン制で交互にBrainを呼ぶ（A→B→A→…、各自最大3発言）
   - 各発言はWorldへ`bubble`し、会話セッション履歴へ`resident_chat`として記録する
4. 両者が`pass`、または6ターンで終了
5. 会話を1つの公開EpisodeとしてWorld Memoryへ保存し、双方に`stand`を送る。Residentごとの同一コピーは作らない

## 生活ティック

### スケジュール

- 住人ごとに間隔を持つ（residents\<名前>\config.tomlの`tick_interval_min`、既定30）
- 実際の間隔 = 設定値 × (0.8〜1.2のランダム)。住人同士が同時に動かないよう自然にずれる
- 次の条件のときはスキップして次回へ：省エネ中／セッション参加中／タスク実行中／当日の予算切れ

### ティック1回のフロー

1. budgetを確認。残0なら「予算切れ演出」（`stand / afk / sleep`からスクリプトで選ぶ。Brainは呼ばない）
2. コンテキストを組み立てる（現在時刻・時間帯・自分のLocation・他住人のLocationと状態・World Memoryから取得した関連記憶。03・06参照）。Private Memoryは生活ティックへ渡さない
3. Brainに「生活モード」で問いかける（予算1消費）
4. 応答の行動をWorldへ送る。`say`があれば公開発話としてbubble表示し、必要なWorld Eventまたは会話履歴へ記録する
5. 応答の行動が`talk_to`ならresident_chatセッションをキューに積む

### ティックで選べる行動（Brainへの選択肢として提示する）

- 行動一覧
  - 01の行動コマンド語彙のいずれか（move / wander / stand / afk / work / sleep など）
  - `talk_to(住人名)` — 話しかけたい相手と最初のひとことを添える
  - `to_master(text)` — Masterへの個別発話。**1日2回まで**。Private MemoryへWhisperとして記録し、World Memoryには入れない
  - 何もしない

## Master発話のキャンセル

`cancel_response {request_id}`を受信した場合：

1. `request_id`に紐づく会話用`invocation_id`を全てキャンセル対象にする
2. 実行中Brainは03の`cancel(invocation_id)`でプロセスツリー単位の停止を試みる
3. 同じ`request_id`に対して未開始のResident応答キューを破棄する
4. Worldへ`response_state {active:false, request_id}`を返し、その発話に対応するTTS停止を指示する
5. workモードのTask、生活ティック、別`request_id`由来の処理は停止しない

`session_id`だけでCancel対象を決めてはならない。同じUI Sessionで複数のMaster発話が存在するため、必ず`request_id`で区別する。

キャンセルはユーザー操作による正常終了として扱い、Brain失敗やTask失敗には数えない。

## 行動予算

### ルール

- 住人ごとに1日のBrain呼び出し回数の予算を持つ（residents側config、既定は下表）
- 消費の区分
  - 生活ティック：予算を消費する
  - Masterとの会話（master_talk）：**予算を消費しない**（Masterと話せなくなる事態を作らない）。ただし1日の呼び出し実績は記録し、50回/日を超えたら会話UIにWARN通知
  - resident_chat：発言1回につき予算1
  - タスク：予算と別枠（タスク自体の上限で守る。07参照）
- 日付が変わったらリセット

### 既定値と計算例

| 頭脳 | 生活ティック予算/日 |
|------|-------------------|
| claude-code | 8 |
| codex | 8 |
| cursor | 12 |

- 例：cursor住人（ティック間隔30分）の一日
  - 稼働16時間 ÷ 0.5時間 = ティック機会 約32回
  - 予算12なので、12回はBrainが考えて行動し、残り約20回はスクリプト演出（散歩・座る）になる

## 省エネモード

Worldは表示状態・フォーカス状態・最小化状態を`display_state`でCoreへ通知する。

1. 判定
   - Worldが非表示、または最小化されている → 省エネ
   - 判定材料が無い（World未接続など）場合は省エネにしない（世界を止めない側に倒す）
2. 省エネに入ったら
   - Worldへ`pause`（描画FPSと環境Effectの更新頻度を下げる）
   - ティックを停止（セッション・タスクの進行中のものは完了まで流し、新規を止める）
3. 表示が戻ったら`ecomode.resume_delay_sec`待って`resume`、ティック再開
4. 会話UIの`pause_toggle`で手動切り替えも可（手動時は自動判定より手動が優先）
5. 復帰時、停止していた時間帯をWorld EventとしてWorld Memoryへ1件記録する（Brainは呼ばない）

Windowsのデスクトップ階層をCoreが監視することはしない。

## 状態保存（state.json）

| フィールド | 型 | 説明 |
|-----------|----|------|
| date | string | 予算消費の対象日 |
| residents | object | 住人名→`{location, budget_used, calls_today, last_tick}` |
| session_seq / task_seq | int | ID連番 |

- 保存タイミング：終了時、および5分ごとの自動保存
- 壊れていた場合：初期状態で開始し、WARNログ（会話ログ・記憶は別ファイルなので失われない）

## エラー方針

- Brain呼び出し失敗（03の規定でリトライ後も失敗）
  - 会話中：その住人は「……（考えごとをしている）」という定型bubbleを出し、セッションはその住人抜きで続行
  - ティック：何もしなかった扱い。予算は消費しない
- Worldのクラッシュ：00の生存関係の通り再起動を試みる
- 未捕捉例外：ログに全文を残し、Core自体は落とさない（当該処理だけ中断）
