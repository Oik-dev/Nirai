# External Services 接続設計書

**ステータス：設計のみ（実装なし）。具体的なサービスが決まった時点で、本書に従い1サービスあたり数時間で実装する。**

## 概要

External Services とは、Serina 本体の外に置く専門AI・専門機能群（株分析・Web検索・コード解析・自動化など）のこと。

- 目的
  - 設計原則「専門機能は Serina 内部に実装しない」（設計書.md §5）の受け皿を定義する
  - どんなサービスが来ても同じ型で接続できる「共通契約」を先に固定し、将来の実装を数時間作業にする
- 前提
  - 接続パターンの土台（Skill → Connector → 注入 → Aurora 発話）はスライス2で完成済み
  - Serina は External Service を**呼び出すだけ**。取得結果の**評価・判断は Core/Decision** が担い、**表現（人格での発話）は Voice（Aurora）** が担う。Aurora は表現の器官であり、結果を"解釈"して判断（検索要否・信頼度・行動方針）を変えることはしない
  - 依存方向は `Core → Skills → Connectors → External` の一方向（逆流禁止）

## 基本ルール

- 責務分離
  - Skill（例：`StockSkill`）
    - インテント検知（`can_handle`）と、結果を Aurora に渡すまでの段取りだけを持つ
    - 外部APIを直接叩かない（Connector 経由のみ）
  - Connector（例：`StockAIConnector`）
    - 外部サービスとの通信・認証・タイムアウトだけを持つ
    - ビジネスロジック・判断処理を持たない（`OllamaChatConnector` と同格の薄さ）
  - External Service 本体
    - Serina リポジトリの外で開発・運用する。Serina 側は契約（後述の Result 型）だけ知る
- 発話の原則
  - 外部サービスの生出力をそのままユーザーに見せない
  - 必ず Voice（Aurora）に「素材（Evidence / Action Result）」として注入し、セリナの人格で語り直して返す
    - 例：株分析の結果 JSON → 「マスター、今日の◯◯だけど…」という一人称の発話
  - **Voice 憲法条文**：Voice は Decision および Action Result を入力として受け取り、その意味を変えずに自然言語として表現する。Voice は Decision や Action Result を書き換えてはならない（言い換え OK／自然な口調 OK／**意味の改変 NG**）。外部情報の数値・事実は改変せず、口調だけを乗せる
- 記憶の原則
  - 外部サービスの結果を直接 memories に書き込まない
  - 発話は通常どおり history に残し、fact 化するかどうかは既存の蒸留（reflection）に任せる
    - 正典保護5原則の書き込み経路を新設しないため

## 処理の流れ

1. インテント検知（Core → Router）
   - 各 External Skill は `can_handle(user_input)` でキーワード/正規表現マッチ
   - Router は優先順マッチ（スライス2実装済み）。External Skill 群は `DistillIntentSkill` の後・`ChatSkill`（キャッチオール）の前に並べる
2. 外部実行（Skill → Connector）
   - Skill が Connector を呼び、`ServiceResult` を受け取る
   - タイムアウトは Connector 生成時に指定（既定 30 秒。対話をブロックしすぎない）
3. 結果の注入（Skill 内）
   - `ServiceResult.payload` を system プロンプト末尾に「## 外部情報」ブロックとして追記
   - 注入文字数上限：2,000 文字（超過分は Connector 側で要約 or 先頭切り詰め）
4. Aurora 発話（Skill → ChatConnector）
   - 通常の `chat()` を呼ぶ。`ctx.on_token` をそのまま転送し、ストリーミング表示にも対応する
5. 失敗時（例外処理）
   - Connector が `status="error"` を返した場合
     - 外部情報なしで Aurora を呼び、「今その情報が取れない」旨をセリナの人格で正直に言わせる
     - 無言死・生スタックトレース表示は禁止
   - External Service 未起動の場合
     - 起動時チェックはしない（対話開始を遅らせない）。呼び出し時に検知して上記フォールバック

## データ構造（共通契約）

### ServiceResult（Connector の戻り値）

| フィールド名 | データ型 | 説明 |
|------|------|------|
| status | string | "ok" / "error" |
| payload | string | Aurora に注入する本文（整形済みテキスト。上限2,000文字） |
| source | string | 出所ラベル（例："StockAI", "web_search"）。注入ブロックの見出しに使う |
| error | string \| None | status="error" 時の理由（ログ用。ユーザーには見せない） |

### ExternalConnector（Protocol）

| メソッド | シグネチャ | 説明 |
|------|------|------|
| query | `query(request: str) -> ServiceResult` | 自然文または整形済みリクエストを渡し、結果を受け取る |

- 実装置き場
  - Connector: `connectors/<service>.py`
  - Skill: `skills/<service>.py`
  - 設定（URL・APIキー）: `core/config.py` に追記し、キーは環境変数から読む（ハードコード禁止）

## 新サービス追加チェックリスト

1. Connector 実装（`query()` 1メソッド、通信とタイムアウトのみ）
2. Skill 実装（`can_handle` のパターン定義＋注入→Aurora 発話）
3. `create_core()` の skills リストに登録（ChatSkill より前）
4. ルーティングテスト追加（検知パターン・誤検知パターン各3件以上、`tests/test_router.py` 方式）
5. フォールバックテスト（サービス停止時にセリナが人格で謝れること）

## サンプル（将来イメージ：StockAI）

- 入力例：「今日の日経どうだった？」
  - `StockSkill.can_handle` → True
  - `StockAIConnector.query("日経平均 本日")` → `ServiceResult(status="ok", payload="日経平均 39,120円 +1.2% …", source="StockAI")`
  - system 末尾に `## 外部情報（StockAI）` として注入 → Aurora が人格で発話（ストリーミング表示）

※メモ：
- 認証が必要なサービスが来た時点で、APIキー管理（環境変数 or `.env`）の共通ヘルパーを検討する
- 複数サービスの同時照会（並列 query）は最初のサービスが安定してから。現時点では1ターン1サービスで十分
- インテント検知を正規表現から小型LLM判定に格上げする案は、誤検知が実害になってから（YAGNI）
