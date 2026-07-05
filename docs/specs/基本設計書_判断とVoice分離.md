# 基本設計書 — 判断とVoiceの分離（二段ターン）

**ステータス：C1（配線）実装済み（2026-07-05）。`Decision → Action → Evidence → Voice` の壁と逆流回帰テストを通した（`core/decision.py`／`tests/test_decision_voice.py`）。判断は既存ルール(router)を昇格させた決定論で、気分・人格に非依存。`DecisionResult` は不変で Voice が書き換え不可。自己申告マーカーはロールバック済みで物理的に不在（廃止確定）。検索復帰は C1 完了後の別ステップ（`DecisionResult.action/query` は受け口として温存）。判断エンジンを誰に任せるか（Gemma/Qwen/CPU常駐/Rule Engine の比較・8GB制約下）は C2 で決定。現時点の方針（マスター決定）は決定論最大化（ルール約8割・推論モデル約2割）。**

## 概要

「判断とVoiceの分離」とは、1ターンを **①判断（何をするか）** と **③発話（どう伝えるか）** の2段に割り、**口調（Voice）は③だけに乗せ、価値観・信念・判断原則（Core Values）は①に効かせる**仕組みである。

> 【重要な訂正】人格は「口調」だけに宿るのではない。「何を大切にし、何を信じ、どう判断するか」という人格の核は①（判断）に乗り、「どう伝えるか」という最終表層だけが③（声）に乗る。旧表現「Serinaらしさ（人格・口調）は③だけに乗せる」は、価値観を声へ押し込む誤りだったため撤回する。

- 目的
  - 話し方(Voice)が意思決定(Decision)を上書きする**逆流を止める**
  - 設計書§5「Coreは決めるだけ／Promptは性格だけ／逆流禁止」を、対話ターンの内部でも成立させる
  - 「どのLLMに載せ替えても、同じ価値観で、同じ判断をし、最後だけ口調が変わる」存在へ近づける
- 背景（なぜ必要になったか）
  - WEB検索追加後、事実質問を情緒・哲学へ変換し、本来検索すべき場面で検索せず曖昧に済ませる挙動が観測された
  - 原因は、検索要否の判断とセリナの発話が **同一LLM呼び出し・同一人格プロンプト内で同時生成**される点（現 `web_search` 経路2）。判断と口調が一息で計算されるため、口調の傾き（媚びモード）が判断（検索スキップ）に漏れる
- 制約（守るべき現実）
  - メイン機8GB VRAM。別モデル（Gemma理性エンジン）を毎ターン呼ぶと Aurora⇄判定モデルの載せ替えで激遅（C方式が避けた当の理由）
  - `num_ctx` 8192 の予算内で回す
- 核心アイデア
  - 判断は **理性の器官**（意図判定・確信度・行動選択）に担わせる。**Aurora（RP＝声に特化）は判定に不適**なので使わない
  - 決定論で書ける判断はコード（Core）へ寄せ、モデル判断は「規則で書けない機微」だけに使う＝モデル載せ替え耐性を最大化
  - 判断エンジンの器の置き場（8GB制約下）は未決。増える1回分の待ち時間は、マスター承認済みの「倍待てる」予算で吸収する

## 北極星（層アーキテクチャ全体図）

本設計は下図の一部（Decision を Voice から引き剥がす最小第一手）に過ぎない。全体像を先に固定しておく。

```text
Perception（外界を観測する機能：Search / Browser / Weather / RSS / Stock / Connector）
  ↓
World Context（観測結果として得た、現在世界の一時状態：天気 / ニュース / 検索結果 / API取得）

┌── Decision（判断）への入力 ─────────────────────┐
│  User Input                                       │
│  Relevant Memory（長期記憶からの想起）             │
│  Belief / Self Image（記憶から形成した信念・自己像）│
│  Core Values（価値観・判断原則）                   │
│  World Context（上記の一時状態）                   │
│  Tool Availability（使える道具）                   │
└───────────────────────────────────────────────┘
  ↓
Decision（今回どうするかを決める層。人格そのものではなく、上記入力から行動方針を生成する装置）
  ↓
Plan（複数手順が要るときの計画。初期実装では optional）
  ↓
Action（実行：SearXNG検索 / Weather API / Browser fetch / Memory検索）
  ↓
Evidence / Action Result（行動の結果＝声へ渡す根拠。成功/失敗だけでなく Evidence を必ず含む）
  ↓
Voice Expression（= Voice Style + Conversation State + Emotion。どう伝えるか）
  ↓
Response
```

- **層の心得**：Memory に書くのは World Context をそのままではなく、会話・体験として意味づけられたときだけ。人格は Memory そのものではなく、Memory から形成された Belief を通して成熟する。
- **感情の二分**：判断に影響しうる「判断感情」（urgency / confidence / curiosity / caution）は①へ、表に出る「表現感情」（tone / warmth / softness / humor）は③へ。感情を全部 Voice に押し込めない。
- **新機能追加時の問い**：Web検索・Browser・Weather・Stock・Humanoid 移行など何を足すときも「これはどの層の責務か？」を必ず問う。この問いを守れば人格は揺らぎにくい。

## 責務分離（本設計の背骨）

| 段 | 担当 | 入力 | 出力 | 価値観・信念 | 口調(Voice) |
|---|---|---|---|---|---|
| ①判断 Decision | 決定論規則 ＋ 理性の器官（Auroraではない） | user_input・Core Values・Belief・記憶・World Context・Tool可用性 | 意図／確信度／行動 | **効かせる** | 乗せない |
| ②行動 Action | コード | ①の行動指示 | Evidence / Action Result（下記） | — | — |
| ③発話 Voice | Aurora（人格プロンプト） | ①の判断結果 ＋ ②の Evidence / Action Result | セリナの返答 | （①で確定済み） | **ここだけ乗せる** |

- 北極星の層順は `Memory → Core → Decision → Action → Voice → Output`（逆流禁止）
- 本設計は**最小の第一手**として「Decision を③の一息から引き剥がす」1点に絞る。5層フル純化は後続スコープ

### Decision入力とVoice入力の分離（明記）

- **Decision（①）が受け取るもの**：user_input・Core Values（`prompt/core_values.md`）・Belief/Self Image・Relevant Memory・World Context・Tool可用性・判断感情。
- **Voice（③）が受け取るもの**：①の判断結果（意図/方針）・②の Evidence / Action Result・Voice Style（`prompt/voice_style.md`）・Conversation State・表現感情。
- **配線の現状（B→C フェーズ境界）**：現在は `prompt/persona.md` が丸ごと Voice へ流れており、Core Values も声にしか届いていない。B で価値観・口調・境界を `core_values.md / voice_style.md / boundary.md` の3ファイルへ分割済み。C で `prompt/loader.py` を切り替え、`core_values.md` を①へ、`voice_style.md`＋`boundary.md` を③へ配線し、persona.md を退役させる。それまで persona.md が実効ソース（一時的に内容が重複するのは、このフェーズ境界による意図的なもの）。

## 判断段の設計（①Decision）

- 決定論バックストップ（既存・維持）
  - 現 `_forced_search`（鮮度語 ＋ 質問サイン）は声に依存しないコード側の判断＝正しい層。維持する
  - ただし鮮度もの限定。一般的な事実・知識質問は拾えない → 下記の中立判定役で補完する
- 判定役（新規・理性の器官＝Auroraではない）
  - 低温（`temperature ≈ 0.2`）で「判定だけ」させる。口調・気遣い・演出は不要、事実に基づき判断のみを出力
  - RP特化の Aurora は声の器官であり判定に不適。判定はそれと分離した理性寄りのモデル（既存の理性エンジン系）が担う
  - 出力は構造化タグ（既存リフレクションの `<fact_updates>` 方式を踏襲）でコードがパースする

### 判定役の出力（verdict）

| フィールド | 値の例 | 説明 |
|---|---|---|
| intent | 雑談 / 事実確認 / 依頼 / 感情吐露 | ユーザー意図の種別 |
| factual | yes / no | 事実・知識を問う質問か |
| confidence | high / mid / low | 自分の知識で確信を持って答えられるか |
| action | answer_from_knowledge / search / fetch / admit_unknown / ask_clarify | ③に渡す行動方針 |
| query | 雨 成分 汚れ pH 大気 | action=search/fetch のときの検索語 |

出力例：

```
<verdict>
  <intent>事実確認</intent>
  <factual>yes</factual>
  <confidence>low</confidence>
  <action>search</action>
  <query>雨 成分 汚れ pH 大気</query>
</verdict>
```

## 処理の流れ

新 `turn`（判断→行動→発話）：

1. 決定論バックストップ判定（必須・user_input）
   - 鮮度語 ＋ 質問サイン → `action=search` を確定し、判定役を省略して直行
   - 未発火 → 2へ
2. 中立判定役（バックストップ未発火時）
   - 理性の器官（**Aurora ではない判定モデル**）を **人格なし・低温**で呼び `<verdict>` を生成 → コードがパース
   - ※RP特化の Aurora は声の器官であり判定に不適。ここで Aurora を使う旧記述は残骸につき撤回済み
   - `answer_from_knowledge` → 外部情報なしで③へ
   - `search` / `fetch` → ②で実行し、結果を③へ渡す
   - `admit_unknown` → ③に「憶測せず正直に知らないと伝える」方針を渡す
   - `ask_clarify` → ③に「手がかりを聞き返す」方針を渡す
3. 行動実行（②）
   - 検索（SearXNG）・記憶想起・（1bでFetch）を実行
4. 発話（③Voice）
   - `build_system`（人格 ＋ 記憶 ＋ 感情 ＋ **①②で確定した外部情報／方針**）で Aurora 発話
   - ③は検索の有無を**変えられない**（もう決まっている）
5. 履歴保存・計測（現行踏襲）

## 行動結果（②Action Result / Evidence）

②が③へ渡すのは「検索した」という事実だけではない。**取得した根拠・失敗・タイムアウト・結果0件・信頼度**をすべて構造化して渡す。Voice はこの Evidence を素材に表現するのであり、事実を創作してはならない。

| フィールド | 値の例 | 説明 |
|---|---|---|
| status | success / error / timeout / no_results | 行動の成否 |
| evidence | source / title / snippet / url の並び | 根拠となった出典群 |
| confidence | 0.83 | 結果の信頼度 |
| error | null / 理由 | 失敗時の理由（ログ用・ユーザーには見せない） |

Voice に「検索した」だけを渡さない。0件や失敗も渡し、③はそれを踏まえて「今は取れなかった」と正直に、セリナの声で伝える。

## Voiceの柵（逆流を物理的に封じる）

> **【Voice 憲法条文】** Voice は Decision および Action Result を入力として受け取り、その意味を変えずに自然言語として表現する。Voice は Decision や Action Result を書き換えてはならない。
> - 言い換え：**OK** ／ 自然な口調にする：**OK** ／ 意味の改変：**NG**

- ③のプロンプトに「検索するか否か」の判断語を置かない
  - 現 `SEARCH_CONTRACT` の**自己判断マーカー（〔検索: …〕を自分で出す規約）は廃止**し、①へ一本化する
- ③の責務は「渡された事実を、渡された方針どおり、セリナの声に変換するだけ」に縮小
- 既存の**絵文字除去・絵文字禁止**は Voice 層の純粋な話なので③に残す（`_neutralize_external` ／ スタイル契約の絵文字ルール）

## 既存WEB検索設計からの差分

| 項目 | 現（C方式） | 新（本設計） |
|---|---|---|
| 検索要否の判断 | Auroraが発話の一息で〔検索〕マーカーを出し分け（Voice内でDecision） | ①判定役（決定論＋中立判定）へ移設 |
| 自己申告マーカー | あり（先頭傍受で非表示処理） | **廃止**。③は常に純発話なので先頭傍受も不要 |
| 器官（SearXNGConnector） | — | **不変**。発動部だけ差し替え（WEB検索設計メモ「発動部だけ交換可能」の想定どおり） |

## 文脈予算・性能

- 1ターン ＝ ①判定（理性の器官）＋ ③発話（Aurora）。判定は短い構造化出力なので生成自体は軽い
- 8GB では Aurora（声）でVRAMをほぼ使い切るため、判断エンジンをGPUに同居させられない。**器の置き場は未決**で、次の三択から選ぶ：
  - CPU常駐の小型推論モデル（埋め込み器と同じくCPU実行・GPU載せ替えゼロ）
  - 既存 Gemma 理性エンジンを判断ターンごとに載せ替え（新モデル不要・純度最高・毎回重い）
  - 決定論規則を最大化しモデル判断を最小化（最速・機微は取りこぼす）
- 「倍待てる」枠 ＝ この判定1回分の増分に充当する

## テスト（`test_web_search.py` 方式・モック）

1. 判定役：事実質問→`search`/`answer`、雑談→`answer`、確信低→`search`/`admit`（各3件以上）
2. 決定論バックストップ：既存回帰（鮮度＋質問で強制、誤検知しない）を維持
3. Voiceの柵：③プロンプトに検索判断語が無い／絵文字ゼロ
4. **逆流しないことの回帰テスト（哲学の核）**：同一 user_input で感情ブロック（気分）を変えても `action` が変わらない＝Decisionが人格・口調に非依存
5. E2E「雨って汚いの？」：事実確認判定→（確信low）→search→事実をセリナ声で直答

## 実装チェックリスト

1. Decision モジュール（例 `core/decide.py`）：決定論バックストップ ＋ 中立判定役 ＋ `<verdict>` パーサ
2. 判定役プロンプトを `prompt/` に分離（**persona.md とは別ファイル**・人格隔離・低温）
3. `web_search.py` を「発話専任」に縮小（自己判断マーカー撤去）。検索実行は②から呼ぶ
4. `build_system`：③発話用に確定外部情報／行動方針を受ける口（現行の注入形式を踏襲）
5. `core/config.py`：`decision_temperature` 等を追加
6. 上記テスト ＋ 既存回帰（smoke / smoke_core / test_session / test_web_search / test_router）合格

## サンプル（雨）

- 入力：「雨って汚いの？」
  1. バックストップ：鮮度語なし → 未発火
  2. 判定役：`intent=事実確認 / factual=yes / confidence=low / action=search / query="雨 成分 汚れ pH 大気"`
  3. 行動：SearXNG で取得
  4. 発話：セリナが事実を噛み砕いて**直答**（「厳密には塵や溶け込んだ物質が混じるから完全な純水じゃなくてね…」）＋短く自然な口調
- 追い打ち：「物理的にはどうなんだろう？」
  - 同様に事実確認判定 → search（または1bでfetch）。**口調に流されず判断が独立して回る**

※メモ：
- 12GBサブ機を主戦場化したら、判定役を Gemma 理性エンジンへ差し替え可（別モデルでもスワップ許容）。器官・②③は不変
- 5層フル純化（Memory→Core→Decision→Action→Voice の完全分離）は北極星。本設計は Decision を Voice から外す**最小第一手**
- 判定役も所詮LLM。決定論で書ける判断は規則側へ寄せ、LLMは「規則で書けない機微」だけに使う＝モデル載せ替え耐性を最大化する方針
- DECISIONS への確定登録はマスター承認後に行う
