# Nirai 詳細設計 03：Brainドライバ（頭脳）

正本は [Nirai_基本設計.md](../Nirai_基本設計.md)。行動コマンド語彙の正は [01_通信プロトコル.md](01_通信プロトコル.md)。

## 概要

Brainドライバとは、住人の思考を外部AI（定額サブスクのCLI等）に問いかける差し替え可能な部品である。

- 前提
  - 全ドライバは共通の窓口を実装する：**コンテキストを渡すと、発言と行動が返る**
  - 全Brain呼び出しは`invocation_id`を持ち、会話中の呼び出しはキャンセル可能にする
  - 呼び出しは常に同時1件（02の直列原則）
  - 従量課金APIは使わない。CLIの認証はMasterが事前に済ませてある前提（ドライバはログイン処理をしない）
  - CLIの引数仕様は実装時の実物を正とする。**実装の最初に実物の`--help`で確認し、差異があれば本書を更新する**

## 共通インターフェース

```
think(invocation_id, mode, resident, context) → BrainResponse
cancel(invocation_id) → CancelResult
```

| 引数 | 説明 |
|------|------|
| mode | "talk"（会話）/ "tick"（生活）/ "consult"（タスク相談）/ "work"（タスク実行） |
| resident | 住人情報（persona、config） |
| context | モードごとの入力（下記） |

`cancel(invocation_id)`は対象のBrain呼び出しと、その呼び出しが起動した子プロセス群を可能な範囲で終了させる。会話UIの停止ボタンは、現在のMaster発話から開始された全Residentの会話用invocationをキャンセルする。実行中Taskのwork invocationは対象外とする。

Windowsでは親CLIだけを終了して子プロセスを残さないよう、Brainプロセス管理層でプロセスツリー単位の停止手段を持つ。具体方式は既存ライブラリまたはWindows標準機構を利用し、独自プロセス管理基盤を作らない。

## プロンプト構成

Brainに渡すプロンプトは次の順で連結した1つのテキストとする。

1. **共通ヘッダ**（全モード共通・固定文）
   - あなたはNiraiという箱庭世界の住人であること
   - 世界の恒常的な説明（水面から光が届く海中3D空間。Residentはその世界の中で身体を持って生活している。Masterは画面のこちら側にいる隣人）
   - この固定説明は「Niraiがどういう世界か」を示す設定であり、「今この瞬間に何が見えているか」の観測事実ではない
   - 応答は必ず後述のJSON形式1個のみで返すこと（前後に説明文を書かない）
2. **人格**：`residents\<名前>\persona.md`の全文
3. **記憶**（06の選別規則に従う）
   - Say / resident_chat / tick：M3以降はWorld MemoryのRetriever結果＋必要な直近公開履歴。M1〜M2はRetriever未実装なので現在セッションと蓄積済み公開履歴だけを使う。Private Memoryは渡さない
   - Whisper：公開Contextに加え、宛先Resident本人のPrivate Memory `context.md`＋直近Whisper履歴を渡す。M3以降は必要に応じWorld Memory Retriever結果も加える
   - 各CLI自身のセッション再開・Memory機能は補助に留め、Nirai Memoryの正本にはしない
4. **現在のWorld Observation**（M3以降。01・02参照）
   - `captured_at`と観測可否
   - 自分の現在Locationと行動状態
   - 近くにいるResident、そのおおまかな距離・状態
   - Masterが現在FocusしているResident
   - 現在時刻・時間帯
   - 実際のWorld Presentationから意味化された環境状態（光、水面、視界、Caustics等）
   - 必要なら直近のWorld Event
   - World未接続・Snapshot未取得・Snapshotが古すぎる場合は「現在の観測なし」と明示し、固定世界説明や過去Memoryから現在状態を推測させない
5. **モード別の指示と入力**
   - talk：セッション履歴（直近20発言）＋「Master（または相手）に返事をする。話すことがなければpass」
   - tick：選べる行動一覧（02参照）＋「今なにをするか1つ選ぶ」
   - consult：タスク内容＋これまでの相談履歴＋「意見と、立候補するかどうかを返す」
   - work：タスク指示（07の形式）。このモードのみJSON応答ではなく実作業（下記タスクモード参照）

### 固定世界説明と現在観測の区別

Brainは、共通ヘッダに書かれた恒常的な世界設定と、World Observationに入った現在事実を区別する。

- 「Niraiには水面から光が届く」は固定世界説明として言ってよい
- 「今は水面からの光が強い」は現在Observationに`light=bright`等の根拠がある場合だけ現在事実として扱う
- 「Codexが近くにいる」「Masterがこちらを見ている」等もObservationに根拠がある場合だけ断定する
- Observationが無い場合でも会話自体は継続するが、現在見えているものを創作して埋めない
- World ObservationはPrivate Memoryではない。Whisper中にも宛先Residentへ渡してよいが、Whisper本文をObservationへ逆流させない

これによりResidentは設定文を演じるだけでなく、実際のWorld状態に反応して発言・行動できるようにする。

## 応答JSON形式（talk / tick / consult 共通）

| フィールド | 型 | 必須 | 説明 |
|-----------|----|------|------|
| say | string | — | 発言。無言なら空文字 |
| actions | array | — | 行動コマンドの配列（01の語彙）。例：`[{"command":"move","args":{"location":"light_area"}}]` |
| talk_to | object | — | tickのみ。`{"name":"相手","text":"最初のひとこと"}` |
| to_master | string | — | tickのみ。Masterへのひとこと |
| volunteer | bool | — | consultのみ。立候補するか |
| pass | bool | — | 発言も行動もしない場合true |

- 応答例（tick）：

```json
{"say": "光の筋が今日は長いな", "actions": [{"command":"move","args":{"location":"light_area"}}, {"command":"afk","args":{}}], "pass": false}
```

## 応答のパース規則

1. CLIの出力から最初の`{`〜対応する最後の`}`を取り出してJSONパースする
2. 失敗したら同じプロンプトで1回だけリトライする
3. リトライも失敗したら
   - talk：発言なし（02のエラー方針）
   - tick：何もしない扱い（予算は消費しない）
4. `actions`に未知のコマンドが混ざっていたら、そのコマンドだけ捨てて残りを実行（WARNログ）
5. `say`が200文字を超える場合：全文は現在のchat_sessions履歴と会話UIへ、Worldの吹き出しには先頭60文字＋「…」を送る

## Brain固有Memoryの扱い

Claude Code / Codex / Cursor等が独自に持つセッション履歴、設定ファイル、Memory相当機能は、CLIを使いやすくする補助として利用してよい。ただし以下を守る。

- Residentの正式な過去はNirai側のWorld Memory / Private Memoryを正本とする
- Brain交換時に失われる情報をNiraiの人格・関係・会話継続の必須情報にしない
- CLI固有MemoryへWhisper内容を永続保存する前提にしない。秘匿境界はNirai Coreが管理する
- 同じ公開会話をResident別・CLI別に重複保存して正本化しない

## Brain Provider Adapter

設定UIはProviderごとの差を直接持たず、Brain Provider Adapterから状態と設定方式を取得する。

各Adapterは少なくとも次を提供する。

- `available`：CLI / Runtimeが利用可能か
- `connected`：Niraiから利用できる状態か
- `configuration_mode`：`connect` / `api` / `local`等
- `connect/configure`：Providerに応じた連携または設定
- `disconnect`：そのResidentとの割当解除。外部CLIのアンインストールや全体ログアウトは行わない
- `think`
- `cancel`
- `health_check`

初期の設定UIに表示するProviderは **Codex / Claude / Cursor / Gemini / Local LLM** とする。Providerが利用不能でもUI全体を壊さず、そのProviderだけ利用不可表示にする。

### 利用枠 / Quota表示（将来拡張）

右Resident設定Sidebarでは、割り当て中Brain Providerの残り利用枠を確認できるようにする。Providerごとに上限の単位や期間が異なるため、UI上の共通概念は「トークン残量」ではなく**利用枠**とする。

Provider Adapterは、取得可能な場合に次の共通Snapshotへ正規化する。

```text
used_percent       # 使用済み割合。取得不能ならnull
remaining_percent  # 残り割合。取得不能ならnull
reset_at           # 次回Reset時刻。取得不能ならnull
period_label       # current session / weekly / all models 等の表示用期間名
updated_at         # 最終取得時刻
stale              # 古いCache値か
```

- 右SidebarではResidentのAI名の近くに利用枠とReset目安を表示する。
- Provider側が複数期間のQuotaを返す場合は、1値へ無理に統合せず期間ごとに表示できる構造を許容する。
- 取得不能・未対応・Provider仕様変更時は推測値を作らず「取得不可」とする。Quota取得失敗で会話やTaskを停止しない。
- Quota取得はBrain呼び出しとは分離し、表示更新のためにAI推論を実行しない。
- 認証情報そのものをNirai独自形式へ複製保存しない。既存CLI / Providerの認証状態を必要最小限の範囲で参照する。
- 実装時はProvider固有のUsage取得をAdapter内へ閉じ込め、World UIへProvider固有API・認証処理を漏らさない。

参考実装として `llmquota`（https://github.com/0xNyk/llmquota）を調査対象にする。Claude / Codex / Cursor等のQuota取得方式、共通表示への正規化、JSON出力の考え方を参考にしてよい。ただしNiraiはWindows専用であり、`llmquota`そのものへの恒久依存は前提にしない。実装着手時に最新Sourceと各Providerの現行仕様を再確認し、必要な方式だけをNiraiのProvider Adapterへ取り込む。

本項は将来拡張の設計メモであり、M1-08以降の現在Taskへ先回り実装しない。

## 各ドライバ

### claude-code

- 起動コマンド（会話・生活・相談）
  - `claude -p "<プロンプト>" --output-format json`
  - ツール使用は不要なので、許可ツールを空にするオプションを付ける（実装時に確認）
- タイムアウト：120秒。超えたらプロセスを止めて失敗扱い

### codex

- 起動コマンド（会話・生活・相談）
  - `codex exec "<プロンプト>"`
  - 読み書きを伴わないモード（サンドボックス読み取り専用）を指定する（実装時に確認）
- タイムアウト：120秒

### cursor

- 起動コマンド（会話・生活・相談）
  - `cursor-agent -p "<プロンプト>"`（モデルはGrok系をCursor側の設定で選択）
- タイムアウト：120秒
- v1ではタスク実行（workモード）の担当にはしない（会話・相談のみ）

### gemini

- Gemini CLIを利用する
- CLIの正確な非対話実行・モデル指定・キャンセル方法は実装時の`--help`と公式仕様を正とする
- 初期は会話・相談用途から対応し、work対応は必要になった時に追加する

### local-llm

- Ollama等のローカルRuntimeへ接続するProvider
- 初期UIには選択肢を用意するが、対応Runtimeは実装時に1種類へ絞り、複数Runtimeを先回り実装しない
- APIキーを前提にせず、ローカル接続設定をProvider Adapterが扱う

## タスクモード（work）

タスク実行時のみ、BrainはJSON応答ではなく実作業を行う（詳細フローは07）。

- 共通の枠
  - 作業ディレクトリ：`runtime\workspace\<タスクID>\` をCLIのworkdirに指定する
  - プロンプトに必ず含める固定文：
    - 作業はこのディレクトリの中だけで行うこと
    - Git操作・外部送信・システム設定変更・秘密情報の取り扱いは禁止
    - 完了したら成果物の一覧と要約を`result.md`に書くこと
  - 上限：60分または40ターン。超えたらプロセスを止めて失敗扱い
- claude-code
  - `claude -p "<タスク指示>" --max-turns 40` ＋ 許可ツールをファイル読み書きと安全なコマンドに限定するオプション（実装時に確認）
- codex
  - `codex exec "<タスク指示>"` ＋ 書き込み先を作業ディレクトリに限定するサンドボックス指定（実装時に確認）

## 失敗と再試行の規定（まとめ）

| 事象 | 扱い |
|------|------|
| タイムアウト | 失敗。リトライしない（時間がもったいない） |
| Masterによる会話キャンセル | 対象invocationを停止し、未実行の同一応答キューも破棄する。失敗扱いにはしない |
| JSONパース失敗 | 同一プロンプトで1回リトライ |
| CLIが存在しない・認証切れ | その住人を「留守」にし、会話UIへ通知（`remove_resident`） |
| タスクの上限超過 | 失敗としてMasterへ報告（07参照） |

※メモ：
- serina / chatgpt-mcp は拡張として後日追加する（07の拡張フックで登録）。Local LLMは初期Provider一覧に含め、対応Runtimeは1種類から始める
- CLIの応答にかかる実時間（体感）はM1実装時に計測し、タイムアウト値を見直す
