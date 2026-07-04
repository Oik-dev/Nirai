# 基本設計書 — WEB検索（自律検索）

**ステータス：設計（実装未着手）。Phase 1a（自律スニペット検索）→ 1b（深掘り時のFetch）の2段で実装する。プロアクティブ経路（Perception層のWorld Context積み）は本書のスコープ外。**

## 概要

セリナが「自分の知識の限界」を察して、明示指示なしに自律的にWEB検索し、その結果を人格で語り直す機構。External Services 接続設計（`基本設計書_ExternalServices接続.md`）の型に乗り、器官（Connector）は将来の Perception 層プロアクティブ経路でもそのまま再利用する。

- 目的
  - 学習カットオフ以降の情報・セリナが知らない事実を、ユーザーが「調べて」と言わなくても補完する
  - 検索の器官（Search / Fetch）を先に確立し、後続のRSS等プロアクティブ知覚の共有基盤とする
- 前提
  - Skill→Connector→注入→Aurora発話の型はスライス2で完成済み
  - 検索結果は memories に直接書かない（発話は history に残り、fact化は既存の蒸留に委ねる。正典保護の書き込み経路を増やさない）
  - 検索基盤は **SearXNG（自前ホスト・メタ検索）**。器官は他人（API業者）を経由しない
- スコープ外
  - プロアクティブ経路（トリガー→検索→World Context保存）。Cognition層と一緒に後日
  - 意味的要約（スニペット/本文の構造抽出まで。3行要約はしない＝下流バイアス回避）

## 発動設計（自律検索の中核）

「セリナが自律的に調べる」を、モデルスワップを起こさず（メイン機8GB VRAM 制約）実現する。**Aurora自身が検索要否を判断**する C 方式を採り、決定論オーバーライドで backstop する。

### 2マーカー（Auroraが出し分ける）

Aurora への system 契約に「知らない/自信が無い/最新情報が要る時は、答えず下記マーカーだけを返せ」を仕込む。

| マーカー | 意味 | 動作 |
|---|---|---|
| `〔検索: キーワード〕` | 軽い確認・最新の概況 | スニペット検索（上位5件） |
| `〔精読: キーワード〕` | 深掘り・詳細 | 検索 → 上位1本の本文をFetch |

- マーカーは**返答の丸ごと**（先頭トークンから、他の文言を混ぜない）で出させる。ストリーミング傍受（後述）のため。
- キーワードの質は Aurora 任せ（自律の一部）。人格プロンプトで「良いクエリを書く」よう規定する。

### 鮮度・深掘りオーバーライド（決定論・backstop）

LLMは「自分が知らないこと」を正しく知れない（カットオフ以降ほど自信満々に作話する）。本人の自己申告だけに委ねず、機械的に強制する層を重ねる。

- **鮮度語**（最新 / 今 / 現在 / 今日 / 今年 / 価格 / 株価 / ニュース / 天気 / 為替 等）を検知 → Auroraに聞くまでもなく **強制「検索」**。news カテゴリで引く。
- **深掘り語**（詳しく / 深掘り / 具体的に / もっと / どういう仕組み 等）を検知 → **強制「精読」**。

### クエリ源

| 発動経路 | SearXNGに投げる語 |
|---|---|
| Aurora自己発動 | マーカー内の Aurora が蒸留したキーワード |
| 決定論オーバーライド | ユーザー入力を軽整形した文（フィラー除去）。SearXNGは自然文クエリを許容 |

### フォールバック（無言死・生スタックトレース禁止）

- Connector が `status="error"`（SearXNG停止・タイムアウト等） → 外部情報**なし**で Aurora を呼び、「今ネットが見えない」旨を人格で正直に言わせる
- **精読の本文抽出が空振り**（JS重量級・ペイウォールで trafilatura が失敗） → **スニペットに自動フォールバック**（「本文までは読めなかったけど、掴んだ範囲で言うと〜」）
- SearXNG 未起動でも起動時チェックはしない（対話開始を遅らせない）。呼び出し時に検知して上記へ

## アーキテクチャ上の要点（C方式ゆえの構造）

1. **検索能力を持つSkillは会話のキャッチオール本体**。マーカー検出は「生成後」に判明するため、`can_handle` で事前ルートされる脇役にはできない。`ChatSkill` を置き換える形の `WebSearchSkill`（catch-all, `can_handle=True`）とし、`create_core` は `[DistillIntentSkill(), WebSearchSkill(...)]` とする。
2. **ストリーミング先頭傍受**でマーカーを捌く。通常ターンの逐次表示を殺さないため、`on_token` をラップし、ストリーム先頭の数文字だけ覗く：
   - 先頭が `〔検索` / `〔精読` → **表示せず**バッファに溜め、キーワードを取り出して検索/Fetch経路へ
   - それ以外 → バッファ済み先頭を吐き出して以降そのまま流す（通常発話）
3. 決定論オーバーライドは `run()` 冒頭でユーザー入力を判定し、該当時は第1パスを省いて**先に検索→注入→1パス発話**（Auroraがデータ込みで即答）。

## 処理の流れ

`WebSearchSkill.run(ctx)`：

1. **オーバーライド判定**（ユーザー入力）
   - 深掘り語 → mode=精読・強制／鮮度語 → mode=検索・強制／どちらも無ければ mode=None
2. **強制経路**（mode≠None）
   - `query = 整形(ctx.user_input)` → Connector で取得 → `ctx.system_prompt` 末尾に注入 → Aurora を stream 発話（1パス）
3. **自己判断経路**（mode=None）
   - 先頭傍受付きで Aurora を第1パス発話
   - マーカー無し → その発話をそのまま返す（1パス・逐次表示済み）
   - マーカー有り → キーワード＋mode を取り出し、Connector で取得 → 注入 → Aurora を第2パス stream 発話（マーカーは破棄）
4. **注入形式**：system 末尾に `## 外部情報（web_search）— <キーワード>` ブロック。上限は ES 契約の2,000字（精読時は本文用に拡張、下記）
5. **失敗時**：前節フォールバック

## データ構造

### 器官（Connector・薄い）

既存 `ServiceResult`（ES設計）をそのまま戻り値に使う。

| Connector | シグネチャ | 責務 |
|---|---|---|
| `SearXNGConnector` | `query(request: str, *, news: bool = False) -> ServiceResult` | `GET {url}/search?q=&format=json&language=ja`（news時 `categories=news&time_range=month`）。上位N件を `- タイトル / 抜粋 / URL` に整形（≈1,200字）。通信・整形のみ |
| `FetchConnector` | `fetch(url: str) -> ServiceResult` | 指定URLをGET → trafilatura で本文抽出（構造抽出のみ・要約しない）。上限≈3,000字で先頭切り詰め。通信・抽出のみ |

- 精読フロー：`SearXNGConnector.query` で上位1件のURLを得る → `FetchConnector.fetch(url)` で本文 → 注入
- URL・キーは環境変数（`SERINA_SEARXNG_URL` 既定 `http://localhost:8888`）。ハードコード禁止

### config 追加（`core/config.py`）

| 項目 | 既定 | 説明 |
|---|---|---|
| `searxng_url` | env or `http://localhost:8888` | SearXNG エンドポイント |
| `search_timeout` | 30.0 | Connector タイムアウト（秒） |
| `search_top_n` | 5 | スニペット取得件数 |
| `search_snippet_char_cap` | 2000 | スニペット注入上限（ES契約準拠） |
| `fetch_body_char_cap` | 3000 | 精読の本文注入上限（num_ctx 8192 予算内） |
| `fetch_top_k` | 1 | 精読で本文取得する記事数 |

### 文脈予算（制約・明記）

num_ctx 8192（日本語で全体 ≈5,000〜6,000字）を人格＋記憶＋履歴＋外部情報で共有する。精読は**本文1本・上限3,000字**に絞る（2本入れると溢れて品質が落ちる）。深掘りターンだけ一時的に `memory_k`/`history_n` を絞るツマミは、実データで溢れが観測されてから（要チューニング項目）。

## インフラ：SearXNG 構築（Serinaリポジトリ外・別運用）

- **Docker Compose** で `127.0.0.1:8888` にバインド（8080は競合回避）
- `settings.yml` で **JSON出力を有効化**：`search.formats: [html, json]`（無いと `format=json` が 403 で死ぬ最大の罠）
- 成果物：`infra/searxng/docker-compose.yml` ＋ `settings.yml` ＋ 起動手順書（`docs/` に1本）
- 秘匿値なし（自前ホスト・ローカルのみ）。将来外部公開する場合の認証は YAGNI

## 実装フェーズ

- **Phase 1a（自律スニペット検索）**：SearXNG構築＋`SearXNGConnector`＋`WebSearchSkill`（自己判断＋鮮度オーバーライド＋先頭傍受）＋config＋配線。1aだけで単体価値が出る（精読未完でも動く）
- **Phase 1b（深掘りFetch）**：`FetchConnector`（trafilatura）＋精読マーカー/深掘りオーバーライド＋本文注入＋空振りフォールバック

## テスト（`tests/test_router.py` 方式・SearXNG/Fetchはモック）

1. マーカー検出：先頭 `〔検索:kw〕`/`〔精読:kw〕` を正しく抽出、非マーカー発話はそのまま通す（各3件以上）
2. 鮮度/深掘りオーバーライド：検知3件以上＋誤検知3件以上（「昨日の話**調べた**？」＝記憶の話は拾わない 等）
3. スニペット整形：JSON→注入ブロックの整形と2,000字切り詰め
4. 精読フォールバック：本文抽出が空 → スニペットに降格して発話、例外を漏らさない
5. Connectorエラー：`status="error"` → セリナが人格で謝る・無言死しない
6. ストリーミング先頭傍受：マーカー時にマーカー文字列が `on_token` へ流れない（表示汚染ゼロ）

## 実装チェックリスト

1. `infra/searxng/`（compose＋settings.yml＋手順書）
2. `connectors/search.py`（SearXNGConnector）／1bで `connectors/fetch.py`（FetchConnector）
3. `skills/web_search.py`（WebSearchSkill＝catch-all、先頭傍受・オーバーライド・2パス）
4. `core/config.py` に前掲項目を追加
5. `create_core` を `[DistillIntentSkill(), WebSearchSkill(...)]` へ（ChatSkillを置換）
6. Aurora の system 契約にマーカー規約を追記（会話スタイル契約の末尾に同居）
7. 上記テスト＋既存回帰（smoke/smoke_core/test_session）合格
8. 依存追加：trafilatura（1b時、requirements.txt 記録）

## サンプル（SpaceX）

- 入力：「SpaceXの最新ニュースってどんな感じ？」
  - 鮮度語「最新」検知 → 強制「検索」（news） → `SearXNGConnector.query("SpaceX 最新ニュース", news=True)`
  - 上位5件（Starship試験・Starlink投入・NASA HLS…）を注入 → セリナが見出しダイジェストで発話
- 追い打ち：「そのStarshipの試験飛行、詳しく知りたい」
  - 深掘り語「詳しく」検知 → 強制「精読」 → 検索1位の記事を `FetchConnector.fetch(url)` で本文抽出 → 注入 → セリナが腰を据えて回答
  - 本文抽出が空振りなら「本文までは読めなかったけど〜」とスニペット範囲で正直に

※メモ：
- 誤爆/取りこぼしはオーバーライド語彙と人格プロンプトで運用チューニング。実害が出たらLLM判定に格上げ（ES設計のYAGNI方針）
- 12GBサブ機を主戦場化したら、発動を別判定モデル（A案）に差し替え可（器官は共通なので発動部だけ交換）
- 履歴が要るFeed・複数サービス同時照会・プロアクティブ経路は Perception 層設計で別途
