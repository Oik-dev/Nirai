# Perception 層（知覚）設計書

**ステータス：設計のみ（実装なし）。Active 型・World Context 土台までが本書のスコープ。認知・話題生成は含まない。**

## 概要

Perception 層とは、Serina が外部世界を知覚するための基盤である。人間でいう視覚・聴覚・感覚器官に相当する。

- 目的
  - 外部世界から情報を取得し、後段（将来の Cognition 層）が使える正規化状態にすること
  - プロアクティブ（セリナ自身の都合で「見に行く」）な知覚の受け皿を用意すること
- スコープ外（本層の責務ではない）
  - 情報の重要度判断 / 話題選択 / 記憶との関連付け / 会話生成 → すべて将来の Cognition 層の責務
  - 意味的な要約（後述、構造抽出まではやるが意味圧縮はしない）
- 大前提（重複回避の設計判断）
  - **新しい器官を作らない。** 外界から取得する器官は既存の Connector 層（設計書.md §「繋ぐだけ」）をそのまま再利用する
  - **新しい返却型を発明しない。** 既存 `ServiceResult`（ExternalServices接続.md）を拡張して `PerceptionResult` とする
  - **本層で唯一の新規実体は World Context ストア**のみ。器官・契約は既存資産の再配置で賄う

### 6層アーキテクチャ上の位置づけ

```
Perception（知覚・本書）
   ↓  取得した外界情報を World Context に積む
Cognition（認知・将来）── World Context を咀嚼して話題化
   ↓
Planning（会話計画・将来）
   ↓
Expression（発話・既存 Aurora）

  ┌─ 縦の主流線とは別に、全層が参照する共有ストア ─┐
  Memory（記憶・既存 serina_memory.db）  ※パイプラインの通過点ではない
  Emotion（感情・既存）                  ※同上・計画に色を付け記憶に重みを書き戻す
```

- Memory / Emotion は「通過する層」ではなく全層が読み書きする**共有状態**として扱う（縦一列に固定しない）
- Perception と Memory の関係は §「正典保護との境界」で厳密に規定する

## 基本ルール

- 器官は既存 Connector を再利用する（新設禁止）
  - StockAI / Search
    - 既存 Connector をそのまま流用（ExternalServices接続.md で定義済み）
  - Weather / Browser
    - Connector を1個ずつ追加するだけ（`connectors/<service>.py`、通信とタイムアウトのみ）
  - 器官（Connector）＝少数・安定。器官は無闇に増やさない
- 器官と「情報源（Feed）」を混ぜない
  - Perception Primitive（器官）
    - Search / Fetch(Browser) / HTTP-API / Weather など汎用の取得手段
  - Feed / Source（具体的な源）
    - 「StockAI レポート」「特定 RSS」など。Primitive の上に**設定として乗る**具体的な源
    - 例：StockAI は「HTTP-API Primitive を使った Feed 設定の一例」であって、器官そのものではない
- 責務境界：知覚は「綺麗な生信号」まで
  - やってよい（知覚）
    - ボイラープレート除去 / 本文抽出 / 文字コード正規化 / 構造化
  - やってはいけない（認知の越境）
    - 意味的要約 / 重要文の選別 / トーン判定
      - 例：Web ページから本文テキストを抜くのは可。その本文を3行に要約するのは不可（下流にバイアスがかかり、記憶層が欲しかった情報も消えるため）
- 引き金（トリガー）と器官は別レイヤー
  - Phase0 の引き金は「手動呼び出し」または「スケジューラのスタブ」のみ
  - 「いつ・何を知覚するか」の自律判断は Cognition の責務であり、本層では実装しない（YAGNI）
- 既存のリアクティブ経路と混線させない
  - 既存 External Services 経路：ユーザー発話 → Skill(`can_handle`) → Connector → Aurora に注入 → 即発話
  - Perception 経路：トリガー → Perception → Connector → **World Context に保存**（その場では発話しない）
  - 両者は同じ Connector を共有するが、**消費者と結果の行き先が異なる**別経路である

## 処理の流れ

Perception 取得フロー
1. トリガー（Phase0 は手動 or スケジューラのスタブ）
   - 「どの Feed を取得するか」を指定して起動する
2. 器官呼び出し（Perception → 既存 Connector）
   - Perception が対象 Feed に紐づく Connector の `query()` を呼ぶ
   - タイムアウトは Connector 生成時に指定（既定 30 秒。対話をブロックしない方針を踏襲）
3. 正規化（構造抽出のみ）
   - Connector が返した `ServiceResult` を土台に、構造抽出だけ行い `PerceptionResult` を組み立てる
   - 要約は行わない（意味圧縮は Cognition の責務）
4. World Context へ保存（upsert ＋ TTL 付与）
   - `PerceptionResult` を World Context ストアに書き込む
   - 同一 Feed の既存レコードがあれば新しい方で更新（履歴が要る Feed は将来 append 方式に拡張）
5. 失敗時（例外処理）
   - Connector が `status="error"` を返した場合
     - 例外を投げず、`status="unavailable"` の `PerceptionResult` を World Context に保存する
     - 「今は見えなかった」も情報として認知層が推論に使える（QA原則「分からなければ正直に報告」の実装版）
   - 無言死・生スタックトレースの露出は禁止
6. 消費（将来の Cognition）
   - Cognition が World Context を参照して話題化・記憶関連付けを行う
   - **本書のスコープ外**（土台を用意するのみ）

## データ構造

### PerceptionResult（ServiceResult の拡張）

既存 `ServiceResult` の4フィールドを継承し、知覚に必要な項目を追加する。

| フィールド名 | データ型 | 由来 | 説明 |
|------|------|------|------|
| status | string | 既存 | "ok" / "error" / **"unavailable"**（partial は将来） |
| payload | string | 既存 | 本文（整形済みテキスト） |
| source | string | 既存 | 出所ラベル（例："weather", "StockAI"） |
| error | string \| None | 既存 | status 異常時の理由（ログ用・非表示） |
| source_type | string | 追加 | 器官の種別（"search" / "web" / "weather" / "feed"） |
| source_id | string | 追加 | Feed インスタンス識別子（同種器官の区別用） |
| mode | string | 追加 | "active" 固定（"passive" は将来枠。フィールドだけ先置き） |
| content_normalized | string | 追加 | 構造抽出済み本文（要約は含めない） |
| observed_at | datetime | 追加 | 取得した時刻 |
| freshness | datetime \| None | 追加 | 情報自体の as-of（取得時刻とは別。例：天気予報の対象日時） |
| reliability | string | 追加 | ソース信頼度 tier（"high" / "mid" / "low"）。QA原則の足切り用 |
| provenance | string \| None | 追加 | 引用元 URL 等（将来の記憶帰属・出典表示に使う） |
| ttl_seconds | int | 追加 | この情報の有効寿命（秒）。World Context の失効判定に使う |
| cost_meta | JSON \| None | 追加 | 課金・レート・レイテンシ等のメタ（将来のバジェット制御用） |

- `observed_at` と `freshness` を分ける理由
  - 例：朝9時に「明日の天気」を取得した場合、observed_at=今朝9時 / freshness=明日。認知が「いつの情報か」を正しく扱えるようにする

### World Context ストア（本層で唯一の新規実体）

外界の揮発情報を置く短命ストア。正典（`serina_memory.db` の memories）とは**物理的に別テーブル**とする。

| フィールド名 | データ型 | 説明 |
|------|------|------|
| id | int | ユニークID（PK） |
| source_type | string | 器官種別（PerceptionResult より） |
| source_id | string | Feed 識別子 |
| mode | string | "active"（将来 "passive"） |
| status | string | "ok" / "error" / "unavailable" |
| payload | string | 整形済み本文 |
| content_normalized | string | 構造抽出済み本文 |
| observed_at | datetime | 取得時刻 |
| freshness | datetime \| None | 情報の as-of |
| reliability | string | 信頼度 tier |
| provenance | string \| None | 出典 |
| expires_at | datetime | 失効時刻（observed_at + ttl_seconds） |
| cost_meta | JSON \| None | メタ情報 |
| created_at | datetime | レコード作成時刻 |

- index: (source_type, expires_at)
- 失効処理
  - 読み出し時に `expires_at < now` のレコードは返さない
  - 定期パージ（スケジューラ）で失効レコードを物理削除
- 置き場
  - Phase0 は既存 `serina_memory.db` 内の**別テーブル**として同居可（正典テーブルには触れない）
  - 将来、揮発ストアを別ファイルに分離したくなった時のため、アクセスは専用 Connector/リポジトリ関数に閉じる

## 正典保護との境界（違反厳禁）

- 外界の知覚情報を `memories`（正典）に直接書き込まない
  - 天気・ニュース・株価は「揮発的な世界状態」であって「セリナの人生」ではない
  - すべて World Context 止まり。正典テーブルには一切書かない
- 記憶への昇格経路
  - 外界情報がセリナの記憶になるのは「体験として意味づけられた」場合のみ
  - その経路は**新設しない**。既存どおり発話が history に残り、fact 化は既存の蒸留（reflection）に委ねる（正典保護5原則の書き込み経路を増やさない）

## 実装チェックリスト（新 Feed 追加時）

1. Connector 実装 or 再利用（`query()` 1メソッド、通信とタイムアウトのみ）
2. Perception 側で Feed 登録（Connector ＋ 正規化方針 ＋ reliability tier ＋ ttl_seconds を束ねる）
3. 正規化関数（構造抽出のみ。要約を書かないことをレビューで確認）
4. World Context への保存・失効テスト（TTL 経過後に読み出されないこと）
5. フォールバックテスト（Connector エラー時に status="unavailable" が保存され、例外が漏れないこと）
6. 正典非汚染テスト（Perception 経路が memories を1件も書き換えないこと）

## 完了条件（Phase0）

- セリナが手動 or スケジューラ経由で以下を取得し、World Context に積める状態になること
  - 検索できる / Web ページを読める / StockAI を参照できる / 天気を取得できる
- 取得失敗が status="unavailable" として安全に記録されること
- World Context が正典（memories）を一切汚染しないこと
- 認知・話題生成は行わない（Cognition 層は次フェーズ）

## サンプル（将来イメージ：Weather Feed）

- トリガー：スケジューラが毎朝 `weather_daily` Feed を起動
  - `WeatherConnector.query("東京 明日")` → `ServiceResult(status="ok", payload="明日 東京 晴れ 最高28℃ …", source="weather")`
  - Perception が正規化し `PerceptionResult(source_type="weather", freshness=明日, reliability="high", ttl_seconds=21600)` を組成
  - World Context に upsert（expires_at=6時間後）
  - 発話はしない。将来 Cognition が朝の会話で「明日晴れるらしいよ」と使う素材になる

※メモ：
- Passive（Discord 着信・RSS 更新等の受動知覚）は現時点で対象外。`mode` フィールドだけ先置きし、追加時にスキーマ移行不要とする
- Browser の本文抽出ライブラリ選定（trafilatura 等）は Feed が具体化した時点で決める
- 定時取得のバジェット/レート制御（cost_meta 活用）は、定時 Feed が増えて実害が出てから（YAGNI）
- 履歴が必要な Feed（時系列で貯めたい株価等）は、upsert ではなく append ＋ 世代管理へ拡張する
- World Context の別ファイル物理分離は、揮発データが正典 DB を肥大化させ始めてから検討する
