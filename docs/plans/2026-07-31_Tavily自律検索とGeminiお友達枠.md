# Tavily自律検索とGeminiお友達枠（2026-07-31）

マスター合意済み方針（本セッションのブレスト）の実装計画。実装は別セッションで行う。

**改訂履歴**: 初版は Gemini レーンを「保留文 → 相談 → 2通目」の既存踏襲で計画したが、
マスターより「少し調べるね」の保留文自体が不要、答えの中で「Geminiさんが教えてくれたよ〜」等の
自由文で触れる形にしたいと指示があり、Gemini レーンも Tavily レーンと同じ「無言 → 確定 → 1通で返す」型へ
統合する形に全面改訂した。さらに、この過程で architecture-reviewer が
「Gemini の機微判定が原典（設計書 §5.6 既定拒否規範）と乖離した fail-open 実装になっている」
既存バグを発見し、マスターの承認を得て Phase C2 として同時是正することにした。

## 前提

- ベースライン: `main` 先頭、`python -m pytest tests/ -q` = **615 passed**（実測済み）
- 各 Phase 完了ごとにテストを回し、615 件以上が green であることを確認する
- `.env` に `TAVILY_API_KEY` 追加済み（マスター手動、`.env` は git 管理外）
- DB へ破壊的操作を行う項目はない
- 本計画は憲章「レビュー発火条件」の**発火する**側（Skill の契約・登録構造の変更、通訳の追加）に該当する。
  実装着手前に **architecture-reviewer を1回通し、PASS 後に着手する**。

## 背景・目的

現行の外聞き（advisor）は「決定論キーワード一致 → 固定保留文「少し調べるね……」→ 外部相談 → 2通目
（GUI が `followup` イベントで追加の吹き出しとして表示）」の一本道（事実レーン）で、
Gemini（Antigravity）のみを窓口にしている。マスター方針転換により：

1. 決定論キーワード判定（`FACT_DOMAIN_MARKERS` 等）は全廃する
2. Gemini は合言葉「Gemini」で呼ぶ「お友達」枠として残す。ただし**保留文・2通目の型も廃止**し、
   無言で相談 → 結果確定後に**1通で**答える。答えの中で「Geminiに聞いた」旨をセリナが
   **自由な言葉で**触れてよい（例:「Geminiお姉ちゃんに聞いてきたよ」）
3. 新たに Tavily（検索特化 API）を「裏方の道具」として導入する。合言葉なし、セリナの判断（Core の軽量判定）
   で毎発話ごとに要否を決める。無言で検索 → 結果確定後に1通で答える。有益な情報が拾えたときだけ
   本文に自然に混ぜ、出典を本文末尾に小さく添える（ClaudeやGPTの検索体感）

## 設計の骨子（共通パイプライン＋2つの窓口）

初版では「Gemini＝2通型」「Tavily＝1通型」で型自体が違ったが、本改訂で**型を1本に統合**する。
違うのは発火条件・機微の関所の性質・生成時の言及の自由度の3点のみ。

| レーン | 窓口 | 発火 | 相談/検索が確定した後の言及 |
|---|---|---|---|
| A（お友達） | Gemini（Antigravity、`skills/gemini_advisor/`） | 合言葉「Gemini」明示のみ | Voice が **自由文**で「Geminiに聞いた」体裁を語ってよい |
| B（自律検索） | Tavily（新規 `skills/tavily_search/`） | 毎発話、Core の軽量判定（`brain.judge()`） | Voice は検索結果の自然文（title/snippet）を語らない。出典 URL は Core が末尾へ機械的に追記 |

共通パイプライン（両レーンとも）:

```
判定（Core、Voiceとは別のOllama呼び出し）
  → 発火なし: 通常会話（従来どおり）
  → 発火あり: 窓口（Gemini/Tavily）を呼び、機微の関所を通し、結果を確定させる
    → 確定した結果（またはヒット無し／拒否／タイムアウト）を pack に埋め込む
    → pack 確定後に初めて Voice（Brain.converse）を1回だけ呼び、1通で返す
```

出典表現が2レーンで非対称なのは矛盾ではなく、**材料の性質が違うため**（Phase D で詳述）。
Gemini が返すのは既に Gemini 自身が生成した「回答」であり、Voice がそれを要約して語るのは
従来の `compose_advisor_followup` と同じ仕事（Voice の生成物として問題ない）。
Tavily が返すのは検索結果の断片（snippet）であり、Voice がそれを自分の言葉のように語ると
出典のねつ造・幻覚と区別がつかなくなる。だから URL のみ機械的に付す。

### 拘束条件（両レーン共通・2026-07-05／07-20 事故の再発防止）

1. 発火要否の判定は Voice 生成呼び出し（`converse`）とは**別の** Ollama 呼び出しで行う。
   persona は注入しない。既存 `_decide_deep_thinking`（`core/runtime.py:528`）と同じパターン。
   Gemini レーンの判定は決定論（合言葉一致）のままでよい。Tavily レーンは `brain.judge()`。
2. 判定の出力は固定語彙のみ（Tavily: `{"needs_search": bool, "query": str}`）。自由文の判断理由は持たせない。
3. 判定結果は **Core が parse する**。Voice には「判定してよい／覆してよい」余地を与えない。
4. Voice の生成呼び出しは、**窓口の結果（相談結果・検索結果・タイムアウト・拒否のいずれか）が
   確定した後にのみ**行う。Voice が先に本文を生成してから結果を継ぎ足す経路を作らない
   （2026-07-20 事故の再発防止）。→ 受け入れテストとして「窓口の結果が確定する前に
   `Brain.converse` が呼ばれていないこと」をアサートする単体テストを Phase D に含める。
5. **窓口に失敗・タイムアウト・拒否した場合、Voice はその窓口を使った体で話さない。**
   Gemini なら「Geminiに聞いた」と言わない。Tavily なら検索した体で話さない・出典を付けない。
   これは「保留文を出さない」ことの裏返しの安全装置であり、これが無いと
   「聞いてもいないのに `Geminiが教えてくれたよ` と語る」新しい形のハルシネ後出しになる
   （2026-07-20 事故の再発、別の衣装）。

## 憲章適合の要点（`docs/憲章.md`）

- A-1: Core は文章生成をしない。
  - Tavily の出典行は「Core 所有の定型テンプレート＋ URL 文字列のみ」で組み立てる。
    Tavily が返す `title` / `snippet` 等の自然文は本文へ差し込まない。
  - Gemini の「〜に聞いた」という言及は **Voice 自身の生成物**であり、Core が書くのではない
    （Core は「言及してよい」という指示を pack に載せるだけ）。
  - 「近所のお姉さん」というキャラ付けの指示文は **`prompt/persona/`（人格資産・保護3原則の対象）
    には置かない**。Core が動的に組み立てる指示欄（Tavily の「検索結果があれば触れてよい」と
    同じ置き場所）に置く。実装が persona ファイルを黙って編集しない、という歯止めを明記する。
- A-5 / A-6: Skill（Tavily・Gemini とも）は状態・判断・記憶を持たない。採否は Core。
  Tavily も Gemini と同様、会話 Brain（primary/escalation）には登録しない。
- B-2: 記憶DBへ直接触れるのは Core のみ。Tavily Skill は記憶DBに触れない。
- **C-4（重要・当初計画で欠落・architecture-reviewer 1〜5回目 FAIL/WARNING 指摘への対応）**:
  機微等級2はクラウド宛パック／Skill ペイロードに載せない。宛先未確定もクラウド扱いでフィルタする。
  - **原典との乖離の発見（reviewer 5回目指摘）**: `docs/設計書.md:834` は「門番未接続（routing_rules
    を渡さない呼び出し）は既定拒否。門番を通さない直呼びで相談クエリが素通しになる経路を許さない」と
    既に明記している。一方、実装 `skills/gemini_advisor/payload.py:57`（`sanitize_query`）は
    `routing_rules is None` のとき判定そのものをスキップして通す **fail-open** になっており、
    原典の規範に反した状態がずっと存在していた（Tavily 導入とは無関係の既存バグ）。
  - **マスター判断（本改訂で反映）**: 今回 Gemini 窓口の呼び出し配線を統合パイプラインへ
    組み替えるタイミングに合わせ、**この乖離も併せて是正する**（Phase C2）。
    Tavily・Gemini とも `routing_rules` 未接続・機微判定不能時は送信しない **fail-closed** に統一し、
    原典の既定拒否規範に実装を合わせる。
  - Tavily は合言葉ゼロで全発話が対象になるため独立に fail-closed で新設し（Phase C）、
    判定（Brain）が生成した言い換えクエリだけでなく**マスターの原発話**も検査対象にする。
    Gemini は既存の `sanitize_query` を是正して fail-closed にする（Phase C2）。
  - **共通パイプライン内での位置づけ**: Phase D で1本化するのはあくまで「判定 → 窓口呼び出し →
    確定 → Voice 生成」という**制御フローの型**であり、機微の関所の**実装（Tavily 用モジュール／
    Gemini の `payload.py`）は窓口ごとに別個**のままとする。ただし**倒れ方（未接続・判定不能は
    送らない）は両窓口で統一**する。関所の実装コード自体を1つの共通関数へ統合する案は
    **引き続きスコープ外**とし、実施する場合は既存 Skill の送信契約変更として独立に
    architecture-reviewer を通す。

## 実装順序

Phase A → B → C → C2 → D → E。Phase B は Phase A のあとに着手
（`plan_forced_advisor` 撤去後の跡地に置くため）。Phase C2 は Phase D で Gemini 窓口の呼び出し
配線を移設する前に、関所自体の是正を先に済ませておく。

---

## Phase A: 合言葉の全廃と Gemini トリガーへの一本化

**対象**: `core/routing/advisor_force.py`, `tests/test_advisor_force.py`,
`tests/test_core_routing_integration.py`, `tests/test_gemini_advisor.py`

**変更内容**:
1. `FACT_DOMAIN_MARKERS` / `FRESHNESS_MARKERS` / `QUESTION_MARKERS` / `EXPLICIT_SEARCH_MARKERS` /
   `CODE_MARKERS` とそれらを使う分岐（`plan_forced_advisor` 内の該当ロジック）を削除する。
2. `EXPLICIT_ADVISOR_MARKERS` を Gemini 専用の呼びかけ語に絞る（例: `("Gemini", "ジェミニ")`。
   表記ゆれの採否は実装判断でよい）。
3. `plan_forced_advisor` は「Gemini 呼びかけがあれば `ForcedAdvisorPlan(tool=..., ...)` を返し、
   なければ None」のみに縮小する。コード相談分岐（`_looks_like_code` 併用）は維持してよい
   （Gemini 呼びかけ時の tool 振り分けとしてそのまま使える）。

**契約**: `plan_forced_advisor` は Gemini レーンの発火判定としてのみ残る（決定論・合言葉一致）。
Gemini レーン自体の型（保留文・2通目の扱い）は Phase E で撤去する。本 Phase では発火条件のみ変更。

**テスト更新**: `tests/test_advisor_force.py` の天気・鮮度・質問マーカー系のテストケースは削除、
「Gemini」明示のみで発火し、それ以外の雑談・事実ドメイン発話では発火しないことを確認するテストに置き換える。

---

## Phase B: Tavily 検索要否の軽量判定

**対象**: `core/routing/tavily_rules.py`（新規）, `core/runtime.py`

**変更内容**:
1. `core/routing/think_rules.py` の `plan_think` / `_decide_deep_thinking`
   （`core/runtime.py:528`）と同型のパターンで、判定専用関数を新設する。
   ただし規則層は持たず（マスター方針: 合言葉ゼロ）、**常に judge へ委任**する薄い関数でよい：
   ```
   def decide_tavily_search(master_utterance: str, brain: Brain) -> TavilySearchDecision
   ```
2. 判定プロンプト `SEARCH_JUDGE_INSTRUCTION` を新設し、出力契約を
   `{"needs_search": bool, "query": str}` に固定する（`THINK_JUDGE_INSTRUCTION` 同様、
   `brain.judge()` 経由。persona 注入なし）。
3. `judge` が呼べない／例外／契約違反時は `needs_search=False`（安全側・速度優先。既存の
   `_decide_deep_thinking` の「曖昧は false」踏襲）。

**契約**: この判定は Voice の生成呼び出しと完全に独立した Ollama 呼び出しである。
Voice（`converse`）のプロンプトには検索要否判断の余地を与えない。

**テスト更新**: 新規 `tests/test_tavily_rules.py`。judge のモックで
`needs_search=True/False` それぞれの経路、judge 例外時に False へフォールバックすることを確認する。

**マスターへの申し送り**（実装前に一度提示、既に一度触れているが数値は改めて明記）:
合言葉ゼロにより、この判定呼び出しは「おはよう」を含む**全発話**で走る。Ollama 実測で温まった状態
約3秒/発話。会話のテンポに影響するため、体感が重いと感じたら「明らかに短い相槌のみ判定を
スキップする」フィルタ（安全側＝判定する方向にのみ倒す。旧マーカーのような「発火させる」規則の
逆流にはしない）を Phase B 完了後の別チケットとして追加できる。今回のスコープには含めない。

---

## Phase C: Tavily Skill 新設

**対象**: `skills/tavily_search/`（新規ディレクトリ）, `core/intake/advisor_tools.py`

**変更内容**:
1. `skills/gemini_advisor/skill.py` と同型で `skills/tavily_search/skill.py` を作る。
   `TavilySearchSkill`: `enabled` プロパティ（API キー有無）、
   `search(query: str, *, routing_rules: SensitivityRules | None) -> TavilyResult | None`
   （失敗・無効時は None、例外を握って会話継続を優先。`GeminiAdvisorSkill.consult` と同じ防御方針）。
   `TavilyResult` は `{answer: str | None, results: list[{title, url, snippet}]}` 程度の最小構造。
2. **機微の関所（C-4・architecture-reviewer 1〜3回目指摘への対応・最重要）**:
   `skills/gemini_advisor/payload.py` の `sanitize_query` / `build_payload` と**関所を置く位置
   （送信直前・Skill 側）だけ同型**の関所を Tavily 側に独立して新設する。
   **注意1（fail-open の踏襲禁止）**: Tavily 側は **`routing_rules` が None または未接続のとき
   送信しない（fail-closed。機微判定不能＝送らない、と定義する）**。Gemini から継承する性質ではなく、
   Tavily レーンで新たに立てる独立の性質である。なお Gemini 側の `sanitize_query` も
   同じ fail-closed へ是正する（Phase C2）。両窓口を同時に安全側へ揃える。
   **注意2（検査対象は原発話＋送信クエリの両方）**: 関所が検査する文字列は
   Phase B の判定（Brain＝人格を持たない判定 Ollama 呼び出し）が生成した再構成クエリ `query` **だけ**
   にしない。Brain 由来の言い換えで機微語が失われる（例: 固有名詞を一般語へ言い換える）と、
   関所が発火しないまま原発話の機微情報がクラウドへ出る経路が成立してしまう
   （`core/state/routing_rules.py` の `is_sensitive()` は渡された単一文字列への部分一致・正規表現
   判定のみで、渡されなかった文字列は判定できない）。
   `search()` / `execute_tavily_search` は **マスターの原発話（`master_utterance`）と
   送信クエリ（`query`）の両方**を受け取り、**いずれかが機微に触れれば送信しない**契約とする。
   「外へ出してよいか」の最終判断は Core が原発話に対して下す責務であり、
   Phase B の判定出力（言い換えクエリ）はあくまで提案（A-3）に過ぎない。
   **責務の所在（実装時の一本化）**: 判断は Core、実行は Skill、という分担で統一する。
   Skill 側の関所コードは「Core の判断を受けて実行を止める」従属表現として書き、
   Skill 自身が独立に「送るか送らないか」を決める二重の決定主体を作らない
   （A-5: Skill は状態・判断・記憶を持たない、に対応）。
   機微判定で拒否された場合、`search()` は None を返し理由を `last_failure_reason` に残す
   （送信は行わない）。
3. `core/intake/advisor_tools.py` に Tavily 専用の実行関数 `execute_tavily_search`（既存の
   `execute_advisor_tool_calls` とは並列。Gemini 側の既存経路には触れない）を追加する。
   ここでも `routing_rules` を Core から Skill へ渡す。
   無言単発検索用に短い専用タイムアウト定数（例: `DEFAULT_TAVILY_TIMEOUT_SECONDS = 8.0`）を新設する
   （保留文がない構成で長い無音待ちは不可）。
4. API キーは Core 側ファクトリ（Gemini と同じ場所）が `.env` の `TAVILY_API_KEY` から読み、
   Skill に渡す。Skill 自身は `.env` を読まない（A-5 に準拠、Gemini 踏襲）。

**契約**: Tavily Skill は状態・記憶を持たない。採否判断（呼ぶかどうか）は Phase B の判定結果と
機微の関所の両方を通過した場合のみ。Skill 自身は「通されたら実行するだけ」。
機微で拒否された場合は Phase D の「ヒット無し」経路（検索せず・出典なし・Voice は検索した体で
話さない）へそのまま合流させる。新しい分岐は増やさない。

**テスト更新**: 新規 `tests/test_tavily_search.py`。`skills/gemini_advisor` 系テストと同型で、
キー無し時に無効・呼び出し例外時に None・成功時の結果構造に加え、**`routing_rules=None` で
既定拒否になること**と**機微判定で拒否されたクエリが実際に送信されないこと**を確認する
（Gemini 側の同型テストと対で書く）。さらに、**言い換え後のクエリは無害だが原発話
（`master_utterance`）側に機微語が含まれるケースでも拒否されること**を確認するテストを追加する
（Phase C 注意2の受け入れ基準）。

---

## Phase C2: Gemini 機微関所の是正（原典との乖離の解消・マスター承認済み）

`docs/設計書.md:834` は「門番未接続（routing_rules を渡さない呼び出し）は既定拒否」と定めているが、
`skills/gemini_advisor/payload.py` の `sanitize_query` は `routing_rules is None` のとき判定を
スキップして通す fail-open の実挙動になっており、原典と実装が乖離している
（Tavily 導入とは無関係の既存バグ。architecture-reviewer 5回目指摘）。
マスター承認により、Gemini 窓口の呼び出し配線を Phase D で組み替えるこの機会に是正する。

**対象**: `skills/gemini_advisor/payload.py`, `tests/test_gemini_advisor.py`, `tests/test_constitution.py`

**変更内容**:
1. `sanitize_query()` を以下のように変更する（`routing_rules is None` の早期スキップを廃止）：
   ```python
   def sanitize_query(query: str, *, routing_rules: SensitivityRules | None = None) -> str | None:
       cleaned = query.strip()
       if not cleaned:
           return None
       if routing_rules is None or routing_rules.is_sensitive(cleaned):
           return None
       return cleaned
   ```
2. `GeminiAdvisorSkill.consult()`（`skills/gemini_advisor/skill.py`）側の
   「`routing_rules is None` なら送信拒否」という既存の早期リターンはそのまま残す
   （二重の安全網として問題ない。実際にこの経路が主に機能しているため、`sanitize_query` 単体を
   直しても呼び出し側の挙動自体は変わらない。直す目的は「関所の実装コード自体の契約」を
   将来の別の呼び出し元に対しても安全側にすること）。
3. `tests/test_constitution.py` の `test_Skillペイロードに記憶と人格を載せない`（99行目）は
   `build_payload("明日の東京の天気", category="web_search")` を `routing_rules` なしで呼び、
   `payload is not None` を期待している。fail-closed 化により `routing_rules=None` では
   常に `None` が返るようになるため、機微でないダミーの `routing_rules`（例:
   `RoutingRules()` の素の実装）を明示的に渡す形へ書き換える（テストの主眼＝人格・記憶が
   ペイロードに含まれないこと、は変えない）。

**契約**: `sanitize_query` / `build_payload` は、`routing_rules` が渡されない・機微判定不能な
すべての呼び出し経路で `None` を返す（fail-closed）。Tavily 側（Phase C）と挙動が対称になる。

**テスト更新**: 新規に `sanitize_query(query, routing_rules=None)` が `None` を返すことを確認する
単体テストを追加する（`tests/test_gemini_advisor.py` に配置。既存の
`test_consult_refuses_when_routing_rules_missing`（274行目、`consult()` レベルの確認）とは
別に、`payload.py` レベルでも同じ契約を確認する）。

---

## Phase D: 無言統合パイプライン（Gemini・Tavily 共通）

**対象**: `core/runtime.py`（`_obtain_valid_report` / `_fact_lane_hold_report` /
`_apply_advisor_pipeline` 系）, `core/context/`（`build_context_pack` 呼び出し箇所、
`_build_pack` 周辺 `core/runtime.py:602`）

**変更内容**:
1. `_obtain_valid_report` の分岐を以下の1本の型に統合する（Gemini・Tavily とも同じ型を通す）：
   - Gemini 呼びかけ検出（Phase A の `plan_forced_advisor`）→ Gemini 窓口を呼ぶ
   - 呼びかけなし → `decide_tavily_search`（Phase B）→ `needs_search=True` なら Tavily 窓口を呼ぶ
   - どちらも該当なし → 従来どおりの通常会話（`_call_brain_converse` を素の pack で1回）
2. 窓口を呼ぶ場合、**この時点で**結果を確定させる（保留文は出さない）。
   Tavily は `master_utterance` と `query` の両方を機微の関所へ通す（Phase C 注意2）。
   Gemini は Phase C2 で fail-closed に是正済みの `routing_rules` 配線をそのまま使う。
   **注意3（配線の引っ越し漏れ防止・architecture-reviewer 4回目指摘）**: 本 Phase で
   `_apply_advisor_pipeline`（`core/runtime.py:436` 付近、`routing_rules=self.routing_rules` を
   `skill.consult()` へ渡している箇所）を統合パイプラインへ解体・移設する。Phase C2 是正後は
   `routing_rules` が渡らなければ Gemini 側も送信を拒否するため、移設ミスがあれば
   「Gemini に呼びかけても常に無反応（拒否）になる」という**目に見える形で壊れる**
   （是正前は無言で関所が消えるサイレント故障だったが、是正後は自己申告する形になる）。
   念のため「Gemini 窓口についても、統合パイプライン内で `routing_rules` が実際に
   `skill.consult()` へ渡っていること」を移設後の回帰テストとして明示的に置く。
3. 確定した結果（成功／ヒット無し／機微拒否／タイムアウト）を `_build_pack` 経由で pack に差し込む。
   既存の `schedule_fact_line`（`core/runtime.py:617` 付近、「時間付き事実」を1件差し込む仕組み）と
   同型で、Gemini 用・Tavily 用それぞれの材料を差し込む口を追加する。
4. pack 確定後に初めて `_call_brain_converse`（Voice 本体生成、1回のみ）を呼ぶ。
   **Voice の生成呼び出しが窓口の結果確定前に走るコードパスを作らない**（拘束条件4）。
5. Core が組み立てる指示欄（pack 内、`prompt/persona/` ではない場所）に、窓口ごとに異なる指示を積む：
   - Gemini 材料がある場合: 「Geminiに相談して返ってきた答え。近所のお姉さんに聞いてきたような
     体裁で、自分の言葉で自然に伝えてよい」（キャラ付けの指示文はここに置く。A-1 参照）
   - Tavily 材料がある場合: 「検索結果があれば自然に触れてよいが、URL や見出し文をそのまま書かない。
     出典は Core が末尾に付与する」
   - どちらも材料が無い場合（呼んでいない／拒否／タイムアウト）: 「聞いていない・調べていないことを
     聞いた・調べた体で話さない」（拘束条件5）
6. **出典行は「セリナの発話」か「画面の注記」かを本計画で確定する（architecture-reviewer
   4回目指摘・A-1）**: **画面の注記として扱う。セッション履歴・記憶書き込み経路には積まない。**
   具体的には、報告書（raw_report）に **Voice の生成した本文とは別のフィールド**
   （例: `citations: list[{url: str}]`）として Tavily の URL リストを持たせる。
   `Turn(speaker="serina", ...)` としてセッションへ刻む文字列・記憶蒸留の材料になる文字列は
   **Voice が生成した本文（`reply`）のみ**とし、Core が組み立てた出典行はそこに混ぜない。
   出典は GUI 側が `reply` の下に付加的に表示する（画面上は末尾に見えるが、記憶に残る発話
   本体には含まれない）。これにより「Core が書いた文字列がセリナ自身の言葉として人生記録に残る」
   事態を避ける（A-1・記憶原則の整理）。出典行は依然として **Core 所有の定型テンプレート＋
   URL 文字列のみ**で構成し、`title` / `snippet` 等の自然文は載せない。
   Gemini 材料の場合は `citations` を使わない（「〜に聞いた」という言及は Voice の本文
   （＝記憶に残ってよい発話そのもの）に既に自然文として含まれている）。

**契約（受け入れ基準）**:
- 単体テスト: `Brain.converse`（Voice 生成）呼び出しが、Gemini／Tavily いずれの窓口についても、
  結果が確定する前に一度も呼ばれていないことをモックでアサートする（拘束条件4の機械的証明）。
- 単体テスト: 窓口が失敗・拒否・タイムアウトしたとき、Voice 出力（モック）が「聞いた／調べた」と
  述べていても、その報告書には該当窓口の実行済みフラグが立っていないことを確認する構造にする
  （拘束条件5。自由文の内容そのものは検査できないため、材料が無いことをテストで保証する）。
- 単体テスト: Tavily 材料があるときの `citations` が、Core が組み立てた URL 文字列と一致し、
  `reply`（Voice の生成した本文）そのものには URL が含まれないことを確認する。
- 単体テスト: セッション履歴・記憶蒸留の材料として渡る文字列が `reply` のみであり、
  `citations` の内容がそこに混入していないことを確認する（Phase D-6 の記憶原則整理の証明）。
- 単体テスト: Gemini 窓口が統合パイプライン内でも `routing_rules` を実際に受け取っていることを
  確認する回帰テスト（Phase D-2 注意3）。
- 単体テスト: 両窓口ともタイムアウト／ヒット無し／拒否時に、通常会話と同じ形で報告書が返ることを確認する。

**非範囲**: レーン間の統合UI表示（GUI側の見せ方調整）は本 Phase では吹き出し1個に統一する
（Phase E で `followup` イベント経路を扱う）以外の演出調整は含めない。

---

## Phase E: 旧2通目機構の退役

Phase A〜D により、Gemini レーンも「無言 → 確定 → 1通」に統合されたため、
旧「保留文 → 2通目」機構が不要になる。放置すると死んだコード経路が残るため、明示的に退役させる。

**対象**: `core/runtime.py`（`_fact_lane_hold_report`, `FACT_LANE_HOLD_REPLY`,
`_apply_advisor_pipeline` の2通目生成部分）, `brains/ollama/adapter.py`
（`compose_advisor_followup`, `build_advisor_followup_prompt`）, `core/intake/gate.py`,
`app/gui_server.py`（378-381行、`followup_reply` を検出して `followup` イベントを発火する経路）,
`tests/test_gui_produce_turn.py`, `tests/test_gemini_advisor.py`, `tests/test_core_routing_integration.py`,
`tests/test_ollama_adapter.py`

**変更内容**:
1. `core/routing/advisor_force.py` の `FACT_LANE_HOLD_REPLY` と `core/runtime.py` の
   `_fact_lane_hold_report` を削除する。
2. `_apply_advisor_pipeline` から「`compose_advisor_followup` を呼んで `followup_reply` を
   報告書に載せる」ロジックを削除する（Phase D の統合パイプラインに置き換わるため）。
3. `brains/ollama/adapter.py` の `compose_advisor_followup` / `build_advisor_followup_prompt` は
   呼び出し元が無くなるため削除する（未使用コードを残さない）。
4. `app/gui_server.py` の `followup` イベント発火経路（378-381行）は、報告書に
   `followup_reply` が載らなくなるため自然に到達しなくなる。コード自体は
   「他の用途で `followup_reply` を使う経路が将来できても壊れないための後方互換」として
   **残すか削除するかは実装判断**とするが、削除する場合はテスト（`tests/test_gui_produce_turn.py`）
   の該当ケースも合わせて削除する。
5. `app/gui_server.py` に、Phase D-6 で新設した報告書の `citations` フィールドを画面へ渡す配線を
   追加する。`reply`（記憶に残る発話本体）とは別経路で GUI へ届け、画面上は `reply` の下に
   小さく表示する（見た目・演出の細部は実装判断。本計画が定めるのは「`citations` は記憶書き込み
   経路に混ぜない」という契約のみ）。
6. `docs/設計書.md` の事実レーン節（§3.7 / §5.6）を、本改訂後の型（無言統合パイプライン）に
   合わせて書き直す。

**契約**: 1ターンにつき Voice の生成（`converse`）は1回のみ。GUI に届く吹き出しは1個のみ
（`reply` 一本。出典は `citations` として付加的に届く。`followup` イベントは通常経路では
発火しなくなる）。

**テスト更新**: `tests/test_gemini_advisor.py` / `tests/test_core_routing_integration.py` の
2通目（followup）を前提にしたテストケースを、1通完結を前提にした形へ書き換える。

---

## 実装後

- `python -m pytest tests/ -q` で 615 件以上 green を確認
- `completion-review` skill → `serina-code-reviewer`（Opus固定）を直列1回
- `docs/archive/DECISIONS.md` に本変更の意思決定エントリを追記する。
  Tavily/Gemini 統合パイプライン化に加え、**Phase C2（Gemini 機微関所の fail-open 是正）は
  独立した項目として明記する**（原典と実装が乖離していた既存バグの発見と是正、マスター承認済み）。
- `docs/設計書.md` の該当節（事実レーン §3.7 / §5.6）を新設計に合わせて改訂
