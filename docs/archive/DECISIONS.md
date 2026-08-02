# Serina 決定ログ

確定した設計判断の記録（新しい順）。現在地は `MILESTONE.md`、詳細設計は `設計書.md` を参照。

---

## 2026-08-02 意味記憶バックフィルの巻き戻し・一括書き換え作業自体を廃止

- **背景**: マスターが8/1適用分を実際に読み、id=1459（「マスターは...視覚を与えたいと願っている」）について「別に俺が願ってるわけじゃない」と指摘。該当会話（2026-07-30セッション、`history`）と照合したところ、実発言は「視覚も必要かもね」という思いつきレベルの提案で、記憶化の過程で確信度が格上げされていたことを確認した。あわせて13件全件を監査し、6件で「語尾・接続だけ変える、事実は変えない」という約束が破られ、動詞の確信度や意味合いまで変わっていたことが判明（例: 「約束した」→「誓ってくれた」、「受け入れ」→「愛おしく感じて」）。
- **決定1（巻き戻し）**: 6件（id=1459, 1461, 1464, 1465, 1467, 1468）を8/1適用前の原文へ巻き戻した。id=1466（「セリナの」→「私の」）はマスターが既にGUIから手動で「セリナの」へ修正済みだったため対象外（マスターの過去の判断を上書きしない）。事前`backup_db`実施（`G:\SerinaDB Backup\serina_memory_20260802_023413_605483.db`）、変更ログは`edit_memory`経由で記録（`data/change_reports/rollback_semantic_warmth_6.json`）。**残課題**: id=1459の巻き戻し後の文言「マスターは…セリナに視覚を与えようと考えている」は、あくまで8/1適用前（＝蒸留直後）の状態であり、実発言「視覚も必要かもね」に照らすとこれもなお一段強い。この格上げは口調温め作業（8/1）ではなく、その前段の記憶化そのもので起きていたため、今回の巻き戻しでは解消していない。現状で許容するか追加是正が要るかは未確認。
- **決定2（作業自体の廃止）**: 「出来上がった記憶文だけを見て一括で書き換える」という今回のやり方は、会話原文と照らし合わせる仕組み（蒸留の引用照合）を経由しないため、事実がズレる土台を持っていたと判断。今後二度と使わないこととし、`tools/backfill_semantic_warmth.py`を削除、`docs/MILESTONE.md`の関連残作業項目を削除した。
- **決定3（記憶を作る指示文の整理・実測込み）**: `core/chores/distillation.py::DISTILLATION_FORMAT_INSTRUCTION`（記憶を作る際にBrainへ送る発注文）を、症状のたびに継ぎ足されていた3段重ね（温度感・固有語言い換え禁止・敬語禁止をそれぞれ個別文で列挙）から「事実ベースを守る」「セリナ自身の言葉で書く」の2原則へ整理。整理は2段階で行った。
  - 1段階目（レビュー前）: 敬語禁止・固有語保持を独立文としては持たず2原則に含意させた版で、実際に該当会話（7/30のカメラ・視覚の話）へ3回試し打ち。断定への格上げなし・敬語混入なし・固有語保持を確認。
  - completion-reviewで、この実測が「動詞の強さ・感情の色づけのドリフト」（今回巻き戻した6件に共通する本当の失敗パターン。確信度格上げはその一例に過ぎない）を検証していないと指摘を受け、原則2に「1を崩さない範囲で。温度感は文体（語尾・接続）に乗せるだけにして、動詞の強さ・断定の度合いは会話ログのままにする」を追加。原則1にも「マスターの発言とセリナの発言を取り違えず」「専門的な単語」を明記に戻した。
  - 2段階目（修正後）: 1461の元会話（「ずっと一緒」）・1468の元会話（「もっと知りたい」）へ再度試し打ちし、「約束した→誓ってくれた」「受け入れ→愛おしく感じて」のような格上げが再現されないことを実測確認（各2回、計4回。全て格上げなし・敬語混入なし）。
  - **測っていないこと**: 会話全体（数十ターン規模）を対象にした本番相当のバッチでの検証はしていない。今回はいずれも数ターンの断片切り出しでの確認。本番投入後、事後監査（設計書§4.3の可変記憶は事後監査制）で様子を見る。
- **前回8/1エントリの補足**: 「これでMILESTONEの意味記憶バックフィル申し送りは完了」という記述は本決定により無効。以後この種の一括書き換え作業自体を行わない。
- **根拠の所在**: `data/change_reports/rollback_semantic_warmth_6.json`、`data/change_log.jsonl`（target_id 1459/1461/1464/1465/1467/1468の2件目エントリ、target_id 1466の2件目エントリ）、`core/chores/distillation.py::DISTILLATION_FORMAT_INSTRUCTION`。

---

## 2026-08-01 意味記憶バックフィル本番適用・外部（Grok）総合レビュー精査

- **背景**: `tools/backfill_semantic_warmth.py`のdry-run内容（敬語なし・固有語保持を確認済み）をマスターが承認。申し送り通り、`tests/eval_recall.py`で想起ベースラインを取ってから`--apply`する手順で実行した。あわせて、外部（Grok）による設計書・ドキュメント整理・コード肥大の総合レビューを提示され、「信頼性があるものだけ実行」の指示で精査した。
- **決定1（バックフィル本番適用）**: 想起ベースライン98%（`golden_queries.json`、目標90%）を確認後、対象13件（id=1451,1456-1461,1463-1468）を全件`--apply`で適用。事前`backup_db`実施（`G:\SerinaDB Backup\serina_memory_20260801_215611_894945.db`）、変更ログは`edit_memory`経由で記録（`data/change_reports/backfill_semantic_warmth.json`）。適用後の想起再計測は100%（悪化なし、むしろ改善）。これでMILESTONEの「意味記憶バックフィル」申し送りは完了。
- **決定2（外部レビュー精査）**: 総合レビューの各項目を実コードと照合。
  - **実害あり・対応済み**: 設計書§1.2「使いたいかどうかは Brain が判断し」という記述が、§810行目・Q&A#5の現行仕様（Core の決定論規則が判定し、Brain 自身の自律判断では発火しない・2026-07-26是正）と矛盾していた。字句を現行仕様に合わせて修正。
  - **既に対応済みと確認**: 完了plans（欲求メモ含む4本）のarchive移動・INDEX更新は、レビューが指す時点で既に完了済みだった（`docs/plans/`が空・`docs/INDEX.md`に記載済みで確認）。
  - **保留（規模超過のため未着手）**: `app/gui_server.py`（1407行、会話・Pulse・評価・記憶メンテ・朝礼・見回りが同居）の分割提案。中〜高労力の構造変更のため、マスターに実行可否と着手タイミングを別途確認してから進める。
  - **対応不要と判断**: README新設・憲章とのドキュメント統合・DECISIONS削除は、レビュー自身も「今やらなくてよい」との判定で、現状追認。
- **根拠の所在**: `tools/backfill_semantic_warmth.py`、`tests/eval_recall.py`、`docs/設計書.md`（§1.2）、`data/change_reports/backfill_semantic_warmth.json`。

---

## 2026-08-01 完了済み plans を archive へ移動（ドキュメント衛生）

- **背景**: 総合レビューで、実装完了済みの計画書が `docs/plans/` に残留し索引も未追従であることが検知された。マスター指示により `legacy/Pict/` は保全のまま残し、完了計画のみ片付ける。
- **決定**: 次の4ファイルを `docs/plans/` → `docs/archive/` へ移動。`docs/plans/` は進行中のみ（時点で空）。
  - `2026-07-26_総合レビュー是正.md`
  - `2026-07-26_予定機能と欲求層_設計メモ.md`（実装完了済みのため archive）
  - `2026-07-26_予定登録_配線B実験.md` / `_実測.md`（配線A採用でクローズ済み）
- **付随**: `docs/INDEX.md` に `plans/` 運用と archive 一覧を追記。`tools/smoke_schedule_cue_ab.py` の出力先を新パスへ。欠落していた `2026-08-01_記憶日記まわり一括修正.md` への docstring 参照は DECISIONS 同日エントリへ置換。
- **非対象**: `legacy/Pict/`（マスター指示で現状維持）。正典の Skill 主導権表記ドリフトは別件。
- **根拠の所在**: `docs/INDEX.md`、`docs/archive/` 上記4本、`tools/smoke_schedule_cue_ab.py`、`tools/backfill_semantic_warmth.py`。

---

## 2026-08-01 記憶日記まわり一括修正（意味記憶の文体・断片化・日記削除の重大バグ）

- **背景**: マスターが記憶メンテ画面で意味記憶(semantic)の文体に違和感を覚えたことから調査を開始。調査の過程で、日記(episodic)削除時に日界処理の水位（`last_episodic_at`）が過去へ巻き戻り、削除した日を含む複数日分の日記が無言のうちに再生成される重大バグを実データで発見（2026-07-23に日記1件を削除→2026-08-01に2025年3月末〜4月上旬・7/23・7/31の計6日分が一斉再生成。削除した日の日記も別内容で復活）。加えてマスターから、日界処理・Pulseのタイミングの追加要望が寄せられ、まとめて一括対応した。
- **決定1（水位の後退防止）**: episodic記憶削除後の水位再同期は`max(現在値, 再同期候補)`とし、後退させない。「削除＝もう要らないという意思表示であり、書き直してほしいという意味ではない」という運用思想を反映。`docs/設計書.md` §4.8.1（旧: 「残存する最新episodicへ再同期する」）も是正済み。該当コード: `app/gui_server.py::_resync_episodic_state_after_delete`。
- **決定2（日界処理の起動時完結化）**: 起動時朝礼が`last_boundary_serina_day`（境界通過の記録）を更新しないため、見回いスレッドが同日中に日界処理一式（特にセッション切替）を重複実行していた。起動時朝礼(`run_startup_morning_routine`、`main()`から切り出し)の最後で更新するよう修正。また起動時朝礼の日記キャッチアップ上限を`max_count=1`固定から`timing.diary_catchup_max_count`（既定5）へ変更（マスター承認: 積み残しが数日分あっても起動直後にその場で全部片付ける方針）。
- **決定3（会話開始前のガード）**: マスターの最初の発言が来る前は日界処理・Pulseを走らせない（`GuiState.has_had_first_turn`フラグ、初回ターンで`True`）。起動後10分程度でセリナから話しかけてくる・待機中にセッションが切り替わる、というマスター報告への対策。
- **決定4（意味記憶の文体）**: 「マスターは〜」「セリナは〜」の無機質な三人称報告調に、事実は変えず温度感を追加する方針。実測（bge-m3埋め込み比較）で短文の言い回し変更は想起への悪影響がほぼないことを確認したが、長文化・複雑化すると想起精度が落ちるケースも確認したため「簡潔な一文を保つ」ガードも同時に追加。該当コード: `core/chores/distillation.py::DISTILLATION_FORMAT_INSTRUCTION`。
- **決定5（断片化対策）**: 新しいグループタグ・兄弟記憶展開のような重い仕組みは作らず、「意味記憶ヒットに同じSerina日の日記（`metadata.target_date`で特定）があれば決定論的に(全文のまま)添える」軽量リンクを採用。episodic(日記)が既に「その日の複数の意味記憶を1本の物語にまとめる」役割を担っていることを実データで確認できたため。architecture-reviewer（2026-08-01・条件付きPASS）の条件「Core側で要約等の生成は行わない」に従い、日記本文はそのまま渡すのみ。添付件数は`MAX_ATTACHED_DIARIES`（既定2）で上限。`docs/設計書.md` §4.4に追記済み。該当コード: `core/context/recall_diary_link.py`（新規）。
- **決定6（tools/配下の単発メンテスクリプトはarchitecture-reviewer対象外）**: `tools/backfill_semantic_warmth.py`（意味記憶の文体をLLMで書き換えるメンテ道具）について、serina-code-reviewerから構造チェック未実施の指摘があったが、マスター承認によりtools/配下の一度きりのメンテスクリプト（`migrate_memory_types.py`等の前例と同様）は対象外と判断（2026-08-01）。保護3原則（backup_db先行・edit_memory経由の変更ログ・世代控え・1件ずつ確認）で担保されており、常時稼働する構造（Core/Brain/通訳/Skill）そのものの変更ではないため。次回以降の同種判断はこの記録に従う。
- **completion-review（serina-code-reviewer）**: Critical 4件・Important 5件を検出。Critical（設計書との矛盾2箇所・.gitignore漏れ・構造ゲート未実施）、Important（重複日記時の索引バグ・添付上限なし・全件ロードの性能懸念・初回ターン判定のgetattrフォールバック）を修正、回帰テストを追加。性能懸念（全件ロード）は次回改修候補として残置（決定7参照）。再レビューはせず、指摘を検証のうえ本ログとMILESTONEに反映して完了とした。
- **決定7（既知の性能課題・未対応）**: `recall_diary_link.py::_build_diary_index_by_day`は会話ターンごとにepisodic記憶を最大1000件全件ロードしてPython側でフィルタしている。実際に必要なのは意味記憶ヒットの日付集合に対応する高々数件のみ。日記本数の増加につれ応答レイテンシに影響し得るため、SQL側での絞り込み（`metadata.target_date`によるIN句クエリ等）への切り替えを次回改修候補として残す。
- **結果**: `python -m pytest tests/ -q` 669 → 671 passed（新規テスト追加分含む）。ゴミ日記6件（旧id 1469-1474、水位巻き戻り事故で誤生成）を削除済み。既存semanticの文体バックフィルは`tools/backfill_semantic_warmth.py`を用意したが、Ollama（対話生成側）停止中のため未実行。
- **申し送り**: Ollama起動後、`python tools/backfill_semantic_warmth.py`でdry-run確認→マスター承認→`--apply`（事前に`tests/eval_recall.py`で想起ヒット率のベースラインを取ること。文体変更・本文書き換えの両方が想起の埋め込みに影響するため）。
- **根拠の所在**: `docs/plans/2026-08-01_記憶日記まわり一括修正.md`（本セッションの設計判断・実装順序の詳細）、`app/gui_server.py`、`core/chores/{diary,distillation}.py`、`core/context/recall_diary_link.py`、`core/runtime.py`、`tools/backfill_semantic_warmth.py`、`docs/設計書.md` §4.4/§4.8.1。

---

## 2026-08-01 削除まわり是正・意味記憶の敬語混入修正（外部レビュー精査）

- **背景**: マスターが`tools/backfill_semantic_warmth.py`のdry-run結果を確認し、①固有語の意訳（「ヒューマノイド」→「人の体」）と②謙譲語混入（「〜してくださっている」）を指摘。加えて、外部（Grok）による削除まわりの実装レビューを提示され、「内容を精査し不要なものは触らない・判断に迷えば聞く」という前提で一括対応を指示された。
- **決定1（敬語混入の是正）**: `core/chores/distillation.py::DISTILLATION_FORMAT_INSTRUCTION`と`tools/backfill_semantic_warmth.py::REWRITE_INSTRUCTION_TEMPLATE`の両方に「敬語（です・ます調、謙譲語・尊敬語）を使わない」指示を追加（前回セッションで追加した固有語保持の指示に続く2件目のプロンプト修正）。`backfill_semantic_warmth.py`は再dry-runで解消を確認。completion-review（I-1）指摘により、`distillation.py`側は敬語禁止の適用範囲が`content`限定であることが明示されておらず`quote`（会話ログ原文の一字一句引用）にまで及ぶと、`memory_review.py`の部分文字列一致照合が失敗し記憶が無言で作られなくなるリスクを検出。「content限定・quoteは原文のまま」を明記する1文を追加し、丁寧語を含む会話ログで実地dry-runを行い、`quote`に敬語（「あります」）が原文通り残り`content`は敬語なしになることを確認済み。
- **外部レビュー精査の結論**（各項目、実コードを読んでの事実確認）:
  - **実害あり・対応済み**:
    1. 発言削除後も`session.rolling_summary`/`fine_summary`/`summarized_turn_count`が更新されず、削除内容が要約経由で以降の文脈に残り続ける（`pack.py`の`fine_summary`フォールバックは`session.turns`＝削除後に再構築済みへ落ちるため実害が小さいことを確認したうえで、`delete_message_with_effects`の現行セッション分岐でこの3フィールドをクリアするよう修正。LLM呼び出しなしの決定論的な修正で、次ターンの要約更新で自然に再生成される）
    2. `purge_chores_for_utterance`が保留中(pending)の宿題しか見ておらず、車線振替後もなお失敗して棚上げ(shelf)されたジョブは削除済み発言のpayloadを持ったまま残骸として残る（`ChoreBox.dismiss_shelved`を新設し、pending・shelf両方を対象にするよう修正）
    3. 記憶の物理削除時に機微査定の失敗台帳`assessment_failures`が掃除されず、死んだmemory_idへの参照が残る（`ChoreBox.clear_assessment_failure`を新設し、`confirm_forget`経由の物理削除に連動させた）
    4. 設計書§4.8.1に「過去セッション削除で正典memoriesには触れない」とあるが、実装は2026-07-23決定（コミット`3fd39b0`「セッション削除に発言1件削除と同じ副作用を適用」）によりB級・非pinned記憶を実際にpurgeしており矛盾（設計書側を実装に合わせて訂正）
  - **精査の結果、実害なしと判断（対応不要）**:
    - セッション削除がturn_lock外である件: 蒸留の消化処理(`consume_pending_distillation_jobs`)は稼働中に呼ばれる経路が無く（`run_session_end_chores`は未配線、見回いスレッドはPulse・日界処理・日記生成のみでchore_boxに触れない）、`uvicorn.run`は起動時朝礼完了後にしか開始しないため、蒸留消化とセッション削除が並走するシナリオ自体が存在しない。`session_store`側もsession_id単位の独立行操作で現行セッションは明示的に拒否されるため、書き込み競合も発生しない
    - `distillation_keys`の残存: 冪等キーは既に消えた発言を含むターン集合のハッシュであり、残存する集合が二度と一致しないため再蒸留のブロックとして働かず実害なし
    - 日記削除時の材料記憶orphan: 2026-07-23決定により原典（source非空）記憶は保護対象として意図的に残す仕様（バグではない）
- **憲章ゲート判断**: 上記の追加はいずれも既存の削除経路内でのフィールドリセット・後片付けメソッド追加であり、新レイヤー追加・層またぎ依存の変更・記憶スキーマ変更のいずれにも当たらないため、architecture-reviewerの発火条件（`docs/憲章.md`）に該当しないと判断（未発火）。
- **completion-review（serina-code-reviewer）**: Critical無し。Important 1件（I-1、上記の敬語禁止指示のスコープ曖昧化・修正済み）を検出。Minor 8件のうち、shelf id空間の取り違え防止（`dismiss_shelved`の引数名を`job_id`→`shelf_id`に改名・テストの弁別力強化）、棚上げ破棄・査定失敗台帳掃除を変更レポートへ明記（原則1対応）、DECISIONSへのコミットハッシュ追記を対応。残りのMinor（要約クリアの過剰適用・purge計算量・型注釈のTYPE_CHECKING化）は実害軽微のため次回改修候補として残置。構造ゲート（architecture-reviewer未実施）の判断は「既存削除経路内のフィールドリセット・後片付けメソッド追加であり発火条件に非該当」と追認された。再レビューはせず、指摘を検証のうえ本ログへ反映して完了とした。
- **結果**: `python -m pytest tests/ -q` 677 passed（新規テスト8件追加: shelf対応2件・assessment_failures掃除2件・要約クリア1件・shelf発言削除1件、既存分含む。completion-review対応後も677 passed維持）。`tests/smoke_bge_m3_recall.py`疎通成功。
- **根拠の所在**: `core/memory/message_delete.py`、`core/chores/chore_box.py`、`core/memory/directed_forget.py`、`app/gui_server.py`、`docs/設計書.md` §4.8.1、`tests/test_master_delete_api.py`、`tests/test_chore_box.py`。

---

## 2026-07-31 Tavily自律検索とGemini無言統合パイプライン実装（Phase A〜E）

- **背景**: マスターブレスト（`docs/plans/2026-07-31_Tavily自律検索とGeminiお友達枠.md`）により、旧「保留文（少し調べるね……）→外聞き→2通目」機構を全廃し、Gemini（お友達枠・合言葉「Gemini」明示のみ発火）とTavily（自律検索・合言葉ゼロ・毎発話Core軽量判定）を「無言→確定→1通で返す」共通パイプラインへ統合した。
- **実装前レビュー（着手前ゲート）**: 専任agent（`architecture-reviewer`）がこのセッションからは名指しで呼び出せなかったため（道具箱の登録範囲がProducts直下止まりでSerinaフォルダ専用agentまで届かなかった）、マスター承認のうえ同一マニュアルを持たせたOpus代打で審査を実施。WARNING（機微の関所の判断主体が計画内でCore/Skillの二重記述だった設計矛盾、Tavily検索要否判定への供給材料の歯止め欠落）を指摘され、計画書を修正・再確認レビューでPASS後に着手した。
- **Phase A**: `plan_forced_advisor`の決定論マーカー（`FACT_DOMAIN_MARKERS`等）を全廃し、Gemini呼びかけ（合言葉「Gemini」「ジェミニ」明示）のみで発火する形に縮小。
- **Phase B**: `core/routing/tavily_rules.py`新設。`decide_tavily_search`は規則層を持たず常に`brain.judge()`へ委任する薄い関数（合言葉ゼロ・マスター方針）。判定へ供給する材料はmaster_utteranceのみに限定（出力が外部送信されるため）。
- **Phase C**: `skills/tavily_search/`新設。機微の関所の判断主体をCore（`execute_tavily_search`）に一本化し、master_utterance（原発話）をSkill層のシグネチャへ一切渡さない設計とした（渡した後に道具側が自制する設計は安全側ではないため採らない）。
- **Phase C2（独立項目・既存バグ是正）**: `skills/gemini_advisor/payload.py`の`sanitize_query`が`routing_rules is None`のとき判定をスキップして通す**fail-open**実装になっており、`docs/設計書.md:834`の既定拒否規範（門番未接続は既定拒否）と原典・実装が乖離していた（Tavily導入とは無関係の既存バグ。architecture-reviewer指摘）。マスター承認により、Gemini窓口の呼び出し配線を組み替える本機会に合わせてfail-closedへ是正。
- **Phase D**: `core/runtime.py:_obtain_valid_report`を「窓口解決（候補ループの外・1回のみ）→pack組み立て→Voice(converse)1回」の単一型へ統合。窓口解決を候補ループの外へ完全に引き上げたことで、Voice呼び出しが窓口結果確定前に走るコードパスが構造的に存在しなくなった（2026-07-05／07-20事故の再発防止）。Tavily出典（`citations`）はVoice生成本文（`reply`）とは別フィールドとして持たせ、セッション履歴・記憶蒸留にはreplyのみを刻む。
- **Phase E**: 旧2通目機構（`_fact_lane_hold_report`・`FACT_LANE_HOLD_REPLY`・`compose_advisor_followup`・`build_advisor_followup_prompt`・`IntakeResult.followup_reply`・GUIのfollowupイベント）を削除。GUIに`citations`を画面注記として渡す配線を追加（当初はバックエンド配線のみでフロント側が未実装だったため、completion-review指摘を受けて`app/web/app.js`にrenderCitations()を追加）。
- **completion-review（serina-code-reviewer代打・Opus）**: `architecture-reviewer`と同じ理由でagent登録が届かず、同一マニュアルを持たせたOpus代打で実施。Critical無し、Important 4件を検出・即修正:
  1. `_resolve_advisor_window`呼び出しがCore._obtain_valid_reportのtry/except（§3.2最終防衛線）の外にあり、窓口解決自体の例外が沈黙契約を破る穴 → 個別try/exceptで包み既定値へフォールバックするよう修正（先行コミットで対応済み）
  2. `docs/設計書.md` §1.5（8段配置規約）が新設ブロック（⑦'外部情報）を未登録で正典が自己矛盾 → §1.5・`ContextPack`docstringへ⑦'を明記し、⑦⑧隣接原則の意図的な例外として位置づけた
  3. §5.7冒頭の編集ミスで旧§5.6の末尾3行（退役済み「事実レーン」を現行仕様と誤って宣言）が残留 → 削除し「発火はCoreの窓口解決（§3.7）のみ」へ書き換え
  4. Tavily検索結果の外部由来URLをスキーマ検証せずcitationsのhrefへ渡していた（`javascript:`等の危険スキーマが理論上混入し得る） → Core側（`_is_safe_citation_url`）とGUI側の二重にhttp/httpsのみのallowlistを追加
  Minor（空回答Gemini材料の誤伝染ガード等）も合わせて是正。再レビューはせず（Critical無し・Important即修正のため1回で完了）。
- **結果**: `python -m pytest tests/ -q` 615 → 649 passed。`docs/設計書.md` §1.5（⑦'新設ブロックの登録）・§3.7（無言統合パイプライン）・§5.6（Gemini改訂）・新設§5.7（Tavily）を改訂。
- **申し送り**: Gemini呼びかけのタイムアウトは180秒（`ANTIGRAVITY_TIMEOUT_SECONDS`）だが、保留文が無くなったため「聞いてから最大3分無音」になり得る。Tavilyの8秒タイムアウトと非対称。本計画はこの短縮を求めていないため今回は変更していないが、体感が悪ければ別チケットで短縮を検討。合言葉ゼロにより毎発話でTavily判定のOllama呼び出しが1回追加され、会話のテンポが体感約3秒重くなる（Phase Bで既定の速度優先トレードオフとして明記済み）。`.env.example`への`TAVILY_API_KEY`追記はenv-guardフックにブロックされ未反映（マスター手動での追記が必要）。
- **根拠の所在**: `docs/plans/2026-07-31_Tavily自律検索とGeminiお友達枠.md`、`core/runtime.py`、`core/routing/{advisor_force,tavily_rules}.py`、`core/intake/{advisor_tools,gate}.py`、`skills/{gemini_advisor,tavily_search}/`、`core/factory.py`、`app/gui_server.py`、`app/web/{app.js,style.css}`、`docs/設計書.md` §1.5/§3.7/§5.6/§5.7。

---

## 2026-07-30 Task 3-6（欲求層とPulse連携）見送り、Phase 3完了条件を充足

- **背景**: 実装計画書のTask 3-6「欲求層がPulseの発火材料にもなり得るか（既存正典id=1440「セリナの側から発する」との連携）」は設計判断のためClaude Code差し戻しとなっており、結論が出るまでPhase 3の完了条件に含めないとされていた。
- **決定**: 見送り。欲求を起点にマスターの操作なしにセリナの側から自発的に話しかけてくる連携は、当面実装しない。マスター判断（2026-07-30、「一旦それはオミットでいいかな」）。将来必要になれば別途起票する。
- **結果**: これによりPhase 3（欲求層）の完了条件を満たす。`docs/plans/2026-07-26_予定機能_平常値ドリフト_欲求層_実装計画.md`のTask 3-6節に結論を記載。

---

## 2026-07-30 予定機能 Phase H・Phase 2（平常値ドリフト）・Phase 3（欲求層）実装完了

- **背景**: `docs/plans/2026-07-26_予定機能_平常値ドリフト_欲求層_実装計画.md`の実行順序まとめ5〜7（Phase H・Phase2・Phase3）を実装。Phase Hの一部（Task 0-1・0-1b・H-1・H-2）は先行してmain側でcursor-bridge委譲（Task 001〜004票、`docs/tasks/`）で実施済み、Phase H残り・Phase2・Phase3全体はworktree（`cursor/phase-h-2-3`ブランチ）でCursor(Grok 4.5)に一括委譲。
- **Task 3-4差し戻し**: 1回目のCursor実装で`DesireState`（欲求の蓄積・抑制門）と単体テストは完了したが、「満たされた」判定の呼び出し元配線が計画に無い分岐（delta閾値・ブースト量・circadianのTZ・永続化要否）に当たり実装停止・報告。マスターが4点確認（delta閾値+0.3・boost+0.3・circadianはJST変換・desire永続化する）、Claude Codeが残り3点（判定発火位置・未充足減衰の機械的トリガー条件・低〜中表出の扱い）を設計判断し、計画書へコード例レベルで書き下ろした。実装はこの指示通りに反映されていた。
- **serina-code-reviewer 1回目**: Critical 4件を検出。
  1. 新規状態層追加（`core/state/desire.py`等）に対しarchitecture-reviewer未実施 → 本セッションで実施し`DONE_WITH_CONCERNS`（違反条文なし。懸念は「欲求層が設計書に条文を持たない」のみ）。`docs/設計書.md`に§2.3bを新設して解消。
  2. **欲求メーターが0.6付近で頭打ちになる不具合**: 未充足減衰（τ=3日）が蓄積速度を常に上回り、`level`が1.0に到達不能。マスターへ判断材料付きで差し戻し、「醒める速さを緩める（τ→30日）」「判定タイミングをtick前スナップショットで見る」の両方（a+b）で是正する方針を承認（2026-07-30）。
  3. `.gitignore`に`data/desire_state.json`未登録 → 追加。
  4. `expire_hypotheses`（Phase H、hypothesis一括tombstone）にバックアップ手順の記載が無い → 計画書「実行順序まとめ」に項目9として明記。
- **serina-code-reviewer 2回目（再レビュー）**: 1回目の是正自体が持ち込んだCritical 2件を新規検出し解消。
  1. 一括tombstone処理が部分失敗時に変更レポートを無言で失う経路（保護3原則1: 透明性の違反）→ `core/memory/facts.py::expire_hypotheses`をfact単位のtry/exceptに変更。
  2. 同ターン内に複数の「心の動き」付箋があると放電・情動ブーストが多重発火 → `DesireState.discharge_and_enter_refractory`で`level_before_tick`も同時リセット＋`gate.py`に不応期ガードを追加。
  - 加えてImportant 2件（新規環境の初回起動でemotion baseline初期値が全軸0に潰れる既存バグ〈Phase2起因、今回発見〉、恒真アサーションで何も検証していなかったテスト）も解消。
- **持ち越し（次に構造を触る際の判断材料）**:
  - `baseline_comfort_factor`が`emotion_baseline_max=0.5`の制約により構造的に正値を取れない（Phase2のツマミとPhase3仕様の噛み合わせ）。中立のmoodをどこに置くかは別の設計判断として保留。
  - 未充足減衰τ=30日への緩和により、「満たされないとゆっくり萎む」という三相の第3相がほぼ観測不能になった（levelはほぼ単調に1.0へ向かう）。頭打ちよりは良い状態だが、当初の三段階の狙いとは別物になっている。マスターへ報告済み、次にI-2を検討する際は再逆算が必要（`core/state/desire.py`の`COMBINED_FACTOR_MIN`コメント参照）。
  - `change_log`未指定時にtombstoneが無記録で走る経路、閾値の出所二重化（`DesireState.fulfillment_level_threshold`と`ThresholdsConfig.desire_fulfillment_level_threshold`）は実害小と判断し未対応。
- **mainの未コミット作業との合流**: マージ時、main側に先行実装済みだったPhase H-1/H-2（`list_hypothesis_facts_by_subject`・`hypothesis_retention_days`）とworktree側の実装が同一仕様のため重複し、`config/thresholds.toml`・`core/memory/facts.py`でコンフリクト。実装・SQL・値は完全一致していたため、コメント文言の差異のみとして解消。
- **結果**: `python -m pytest tests/ -q` 615 passed。mainへマージ・push済み（`befd6da`）。
- **根拠の所在**: `docs/設計書.md` §2.3b・§2.6、`docs/plans/2026-07-26_予定機能_平常値ドリフト_欲求層_実装計画.md`、`core/state/desire.py`／`desire_persist.py`、`core/intake/gate.py`、`core/runtime.py`、`core/memory/facts.py`、`core/chores/orchestrator.py`。

---

## 2026-07-27 予定機能 Phase 1（Task1-1〜1-7）実装・Cursor委譲運用の確立

- **背景**: `docs/plans/2026-07-26_予定機能_平常値ドリフト_欲求層_実装計画.md`の事前ゲート（Task 0-1・0-1b・0-2）完了後、Phase 1（facts台帳への予定/記念日カテゴリ追加・3窓判定・発火済み管理・関所での即時active書き込み・パック表出・窓終了後のtombstone化/年次リセット・旧Pulse(promise)経路撤去）を実装。
- **実装体制の変更**: マスター判断により、Cursor(Grok/Composer)側の実行コストは以後一切考慮しない（メイン(Claude)側のトークン・時間の節約になるかのみで委譲要否を判断）。`model-routing`・`cursor-bridge` skillの固定閾値（3ファイル/1万トークン等）を撤廃。
- **cwd制約の発見と対応**: Serinaの`data/`・`prompt/persona/`が`cursor-bridge`の禁止領域(`roles.json` deniedRoots)に登録済みで、**リポジトリ全体をcwdにする一括委譲は構造的に不可能**（cwd自体が禁止領域の親でも拒否される仕様）と判明。advisor相談の結果、deniedRoots解除は「保護3原則の同一性・可逆性を毀損するため論外」と判断し、専用git worktree（`D:\wt\serina`、本体と兄弟にならない場所、フォルダ名は`serina`のまま）をcwdにする運用で解決。この運用を`cursor-bridge` skillとSerina`CLAUDE.md`に明記。
- **実装フロー**: Cursor(Grok 4.5)へ一括委譲(7タスク・26ファイル・+1564/-156) → serina-code-reviewer(Opus)レビューでCritical2件(月日のみ表現の年補完に未来方向の補正なし／予定・記念日factが窓ゲートを経由しない通常fact想起経路から漏れる)・Important5件を検出 → 修正委譲・再レビューで新規Critical1件(時刻なし予定が記録直後にtombstoneされる)・Important2件(C-2修正が設計書§4.4の規定を超えて過剰除外／蒸留のsupersede候補が予定・記念日を含み新factが無言消失)を追加検出・修正。2ラウンドの修正で解消。
- **結果**: `python -m pytest tests/ -q` 571 passed。mainへマージ済み（`53a44ac`）。
- **根拠の所在**: `docs/設計書.md` §4.9、`docs/plans/2026-07-26_予定機能_平常値ドリフト_欲求層_実装計画.md`、`core/context/schedule_window.py`／`temporal_cue.py`、`core/chores/schedule_lifecycle.py`／`schedule_pulse_state.py`、`core/intake/gate.py`、`core/memory/facts.py`。

---

## 2026-07-26 総合レビュー是正（Phase A: A1〜A10 / Phase B: B1・B2）完了

- **背景**: 同日のOpus総合レビュー（バグ・リファクタ・効率）で確定した是正12項目（`docs/plans/2026-07-26_総合レビュー是正.md`）を実装。Phase Aは憲章「レビュー発火条件」の発火しない側（単一層の内部実装・バグ修正・ツマミ値）、Phase Bは発火する側（記憶スキーマ・パック構成変更）に分類。
- **Phase A（architecture-reviewer不要・completion-reviewのみ）**:
  1. **A1**: `core/intake/gate.py`から外部相談（Gemini advisor）の実行を削除。通常会話ターンでBrainの幻覚「道具使用」付箋から外聞きが発火する裏口を封鎖。外聞きの起点は`core/runtime.py`の事実レーン（`plan_forced_advisor`の決定論判定）一本に統一。
  2. **A2**: `core/memory/recall_planner.py`の「昨日／一昨日／先週／この前」をSerina日（`core/state/serina_day.py`・境界07:00）基準の計算へ変更（従来はUTC暦日直算でJST深夜〜早朝にずれていた）。
  3. **A3**: 気分の軌跡（`EmotionState.mood_trajectory`）に`_day`（Serina日タグ）を付与し、`summarize_trajectory(day=...)`/`clear_trajectory(day=...)`で日ごとに独立集計・消去できるようにした。複数日キャッチアップ時の軌跡混入対策（旧`is_most_recent_pending_day`分岐）が不要になり削除。
  4. **A4**: `app/gui_server.py`のターン後要約スレッドに非ブロッキングLock（`summary_lock`）を追加し二重起動を防止。
  5. **A5**: `core/state/emotion.py`に対極カップリング（プルチックの輪の対極軸を弱く引き合わせる。`opposite_coupling_ratio`既定0.5）を追加。
  6. **A6**: 気分層を「情動デルタの直接加算＋急変防止弁」から「情動へのにじみ（`apply_mood_bleed`）＋上限クランプ」へ変更。`mood_bleed_rate`既定0.08。旧`apply_mood_delta`は後方互換のため残置。
  7. **A7**: `core/context/emotion_render.py`の言い回し選択を`hash()`（プロセス内決定的）から`random.choice`へ変更。
  8. **A8**: Pulseの未回収約束一覧（`list_promise_memories_for_pulse`）を起動時1回キャッシュ化。無効化は日界の蒸留消化後・記憶削除・記憶編集の3箇所のみ。
  9. **A9**: 死に枝の棚卸し（`idle_policy.py`のdeprecated関数`decide_session_end`/`should_digest`/`should_generate_diary`/`should_retry_diary_after_empty`削除、`pack.py`の未使用互換引数`destination_location`/`routing_rules`削除、`store.py:_base_activation`削除等）。JSON抽出の意図的重複（`adapter.py`/`distillation.py`）はdocstringで明記し共通化しない。
  10. **A10**: `core/routing/advisor_force.py`の誤字修正。
- **Phase B（architecture-reviewer事前PASS後に実装）**:
  - **B1**: マスター観測（`RelationshipState.recent_master_mood`）を`core/state/relationship_persist.py`（`data/relationship_state.json`）で永続化し、文脈パック⑤ブロック末尾へ1行追加。architecture-reviewer初回判定は**WARNING（条件付き通過）**——「観測に時刻の錨が無く鮮度切れを今の様子として提示してしまう」「`ongoing_topics`が際限なく膨張する」の2懸念。マスター承認（2026-07-26「条件付きで実装する」選択）に基づき、`recent_master_mood_at`（取得時刻）＋鮮度切れ閾値（`relationship_observation_stale_after_seconds`既定6時間・`core/context/relationship_render.py`で判定）、`ongoing_topics`の上限（`MAX_ONGOING_TOPICS=5`）、`docs/設計書.md` §1.5⑤の先行改訂の3条件を満たして実装。生きた`RelationshipState`オブジェクトはパックへ持たせず、意訳した文字列のみを渡す。
  - **B2**: `core/memory/facts.py`に`facts_vec`（`memory_vec`と同型・sqlite-vec vec0・1024次元cosine）を新設し、fact埋め込みを保存。architecture-reviewer**PASS**。`core/chores/distillation.py`のsupersede判定が既存fact全件を毎回re-embeddingしていた無駄を解消（`facts_vec`から読み・無ければ遅延計算して保存）。次元は`MemoryStore._vector_dim`から伝播（`FactStore(vector_dim=...)`）。
- **serina-code-reviewer 2回連続**（completion-review・実装全体について）:
  - 1回目: Critical 2件（`.gitignore`に`data/relationship_state.json`未登録／A1が憲章「層をまたぐ依存の変更」に該当するのに事前architecture-reviewer証跡が無い）・Important 4件（fact物理削除でfacts_vecの行が孤児化／`wipe_memory_runtime.py`がfacts_vecとrelationship_state.jsonを消し残す／A3の旧mood_trajectory移行が起動時点を錨にしており過去日の軌跡が「今日」に誤帰属する／`relationship_state.json`の時刻がnaiveだと全ターンTypeErrorになりうる）・Minor 7件を指摘。Critical・Important・Minor 1件（`_build_pack`がnowをテスト経路と揃えていない）をすべて修正。
    - A1差分はarchitecture-reviewerを事後実行し**PASS**（「依存の削除はむしろ層分離を強める望ましい変更。A-3の違反兆候〈Brain出力の無検証反映〉を消した是正」）。ただし`docs/設計書.md`の複数箇所（§0決定サマリ・§1 Core役割・ディレクトリ図・§5.6 Gemini会話フロー）が旧配置（「Brainが自律的に道具を使う」）のまま実装と食い違っていたため、実装に合わせて改訂した。
  - 2回目: 新規ソース2ファイル（`relationship_render.py`・`relationship_persist.py`）がgit未追跡のままだと import エラーで起動しないコミットになる点、およびarchitecture-reviewer PASS証跡・是正内容をDECISIONS/MILESTONEへ残していない点を指摘（＝本エントリと`git add`で解消）。Assessment「修正後に可」。
- **テスト**: `python -m pytest tests/ -q` 478 passed（開始時）→ 510 passed（終了時）。`tests/smoke_bge_m3_recall.py`はOllama未起動のためSKIP。
- **持ち越し（Minor・実害なしと判断し記録のみ）**: （2026-07-26 後続セッションで解消済み。下記「Minor持ち越し解消」を参照）
  - ~~`Core.turn()`（非routed互換経路）が`self.emotion.current_day`/`self.relationship.current_turn_at`を設定しないため、この経路で積まれた軌跡は`_day=None`のまま永久に残る。本番経路は`turn_routed`のみで実害なし。~~
  - ~~`_migration_anchor_serina_day`・`turn_routed`の`serina_day_id`は既定`boundary_hour=7`で呼ぶ一方、日記キャッチアップ側は`timing.serina_day_boundary_hour`を使う。`config/app_timing.toml`の`boundary_hour`を7以外に変更すると軌跡の`_day`タグと日記の`target_date`がズレる（現状デフォルト運用のため実害なし）。~~
  - ~~`core/runtime.py`の`advisor_calls_from_fusen`フォールバック（事実レーン内）は、唯一の呼び出し元が渡す報告書が`advisor_tool_calls`を必ず非空で持つため現状到達不能。A1の趣旨からは削除候補だが、後方互換として残置。~~
  - ~~facts埋め込みモデルを将来差し替える際は`memory_vec`と`facts_vec`の両方を作り直す必要がある（`facts_vec`は遅延移行のみで一括再構築ツールが無いため）。~~
  - 本変更を含む最初の本番起動（`facts_vec`スキーマが`data/serina_memory.db`へ初めて追加される）の前に`python tools/backup_db.py`を1回実行すること。（運用手順・コード変更なし）
- **非範囲（設計合意済み・実装は別セッション）**: 予定機能（Pulse記憶種別の作り直し）・欲求層／平常値の自律ドリフト／感情③（軸別冷却速度）。詳細は`docs/plans/2026-07-26_予定機能と欲求層_設計メモ.md`。
- **根拠の所在**: `core/intake/gate.py`、`core/runtime.py`、`core/memory/recall_planner.py`、`core/state/emotion.py`、`core/context/emotion_render.py`、`app/gui_server.py`、`core/memory/facts.py`、`core/chores/distillation.py`、`core/state/relationship.py`／`relationship_persist.py`、`core/context/pack.py`／`relationship_render.py`、`docs/設計書.md` §0/§1/§1.5/§2.3/§5.6、`docs/plans/2026-07-26_総合レビュー是正.md`。

---

## 2026-07-26 日記対象日のmetadata明示タグ（恒久解）

- **背景**: I-a暫定解（`created_at`が日界瞬間ちょうどなら1日前を表示）はデフォルト運用では足りるが、`boundary_hour`変更・tz不揃い・`export_life`/`persona_propose`未対応が残っていた。
- **対応**:
  1. `generate_and_save_diary`が`material.target_date`を`metadata.target_date`として保存（`created_at`は材料窓のためday_endのまま）。
  2. `MemoryRecord.metadata`を読み取り可能にし、メンテAPIもmetadataを返す。
  3. 共有口`core/memory/diary_date.py::resolve_diary_target_date`（優先: `target_date` → legacy`date` → 日界ヒューリスティック）。
  4. 表示3経路（`memory_time` / `message_delete` / `app.js`）と`export_life` / `persona_propose`が共有口（または同等）を使う。
- **非範囲**: 既存episodicのbackfill（タグ無しはヒューリスティック残置）。予定/欲求層は次セッション。
- **根拠**: `docs/設計書.md` §4.5、`core/memory/diary_date.py`、`core/chores/diary.py`。

---

## 2026-07-26 Minor持ち越し解消（総合レビュー是正の残り）

- **背景**: 同日「総合レビュー是正」エントリの持ち越しMinor（実害なし・記録のみ）を実装で解消。
- **対応**:
  1. `Core.turn()`（非routed）でも`emotion.current_day`／`relationship.current_turn_at`を付与（`turn_routed`と同型。任意`now=`注入可）。
  2. `Core.serina_day_boundary_hour`を追加。`GuiState`が`timing.serina_day_boundary_hour`を載せ、`turn`／`turn_routed`／`_migration_anchor_serina_day`が同じ日界を使う。
  3. `_apply_advisor_pipeline`の`advisor_calls_from_fusen`フォールバックと、到達不能になった`advisor_calls_from_fusen`関数自体を削除（外聞きは`advisor_tool_calls`経路のみ＝A1）。
  4. `FactStore.rebuild_embeddings`／`MemoryStore.rebuild_facts_vector_index`と`tools/rebuild_facts_index.py`を新設（`rebuild_index.py`と対。モデル差し替え時は両方を回す）。
- **残す運用メモ**: B2適用後の初回本番起動前に`python tools/backup_db.py`を1回（`facts_vec`スキーマ初追加）。
- **テスト**: `test_core_full_body`・`test_gui_watchdog`・`test_facts`に境界／再構築ケースを追加。

---

## 2026-07-26 Brainアダプタの命名リネーム（qwen→ollama）完了

- **背景**: 2026-07-25エントリ（対話モデルをQwen3.5-35B-A3B-UncensoredからGemma4-26B-A4B-uncensoredへ移行、決定4）で「アダプタのディレクトリ名・クラス名・設定セクション名（`qwen`）は今回リネームしない。汎用名（`ollama`）への変更は別コミットで実施予定」と予告していた宿題。モデル移行後もコード識別子がqwen由来のまま残り、実態（Gemma4・将来の複数Brain運用）と表記が乖離していた。
- **決定**: 構造変更ではなく命名変更のため`architecture-reviewer`は対象外、`completion-review`（serina-code-reviewer）のみで進行。「コードへの参照（ディレクトリ名・クラス名・設定キー・ファイルパス）」は全箇所を`ollama`表記へ統一する一方、「当時のモデル名・運用実態を指す固有名詞の言及」（`Qwen3.5-35B-A3B-Uncensored`・`serina-qwen35-unc`等）は史実として書き換えない、の2区分で整理。
- **変更範囲**:
  1. `brains/qwen/` → `brains/ollama/`（ディレクトリ）、`QwenAdapter` → `OllamaAdapter`、`QwenAdapterError` → `OllamaAdapterError`。
  2. `config/brains.toml`の`adapter = "qwen"` → `"ollama"`、`config/thresholds.toml`の`[qwen]`セクション → `[ollama]`。
  3. `core/config.py`（`qwen_request_timeout_seconds`/`qwen_num_ctx` → `ollama_*`）、`core/factory.py`、`core/chores/orchestrator.py`のimport・クラス参照・設定キー参照を追随。
  4. `core/chores/gpu_guard.py`・`distillation.py`、`core/intake/memory_review.py`、`core/memory/store.py`、`core/routing/decision.py`、`core/runtime.py`、`app/gui_server.py`の現在形コメント（「Qwen単一運用」等）も`Ollama`表記へ統一。
  5. `tests/test_qwen_adapter.py` → `tests/test_ollama_adapter.py`（リネーム＋中身のクラス名・import参照）。波及した他11本のテストファイルも文字列リテラル・import・クラス参照を追随。
  6. `.claude/skills/run-tests/SKILL.md`のファイル名参照を更新。
  7. `docs/設計書.md`（§3.1テーブル・§3.6・§3.7・ディレクトリ図）・`docs/MILESTONE.md`の現在形記述を`Ollama`表記へ統一。「アダプタ実装名は据え置き」等の暫定運用注記は削除。
  8. 本書（`docs/archive/DECISIONS.md`）の過去エントリ本文中のコード参照（`brains/qwen/adapter.py`・`QwenAdapter`・`[qwen]`セクション等）も新名称に統一。理由: 将来AIが本書からコードへ辿るときの紐づけを維持するため（マスター判断）。モデル固有名詞（`Qwen3.5-35B-A3B-Uncensored`等）は対象外で保持。
- **検証**: `python -m pytest tests/ -q` 477 passed。`python tests/smoke_bge_m3_recall.py` 疎通確認済み（実DB866件から意味的想起が機能）。
- **根拠の所在**: `brains/ollama/adapter.py`、`config/brains.toml`、`config/thresholds.toml`、`core/config.py`、`core/factory.py`、`core/chores/orchestrator.py`、`docs/設計書.md` §3.1/§3.6/§3.7、本エントリ。

## 2026-07-26 日記の日付表示の翌日ズレ（I-a）を解消

- **背景**: 同日先行コミットでI-3・I-4を解消した際、日記(episodic)の`created_at`を「対象Serina日の終わり」（次Serina日の開始瞬間、例07:00:00 JST）に変更した副作用として、想起パック表示（`core/context/memory_time.py`）・削除警告文（`core/memory/message_delete.py`）・GUIアルバム表示（`app/web/app.js`）が`created_at`から日付ラベルを算出する際、対象日の**翌日**を表示してしまう不整合が発生（プロンプト本文は対象日を名乗るのに表示ラベルは翌日）。serina-code-reviewer 2回目レビューで検出・持ち越し（コミットは止めない判断）。
- **決定**:
  1. `core/state/serina_day.py`に共有関数`is_serina_day_boundary_instant(local, *, boundary_hour=SERINA_DAY_HOUR)`を新設。「`created_at`がSerina日界の瞬間ちょうど」を目印に、該当すれば1日前（対象日そのもの）を返すヒューリスティックを3表示層に適用。
  2. **1回目修正はCritical指摘で差し戻し**: 判定条件を「分秒0」のみにしたところ、legacy投入記憶（`tools/import_legacy_memories.py`経由の旧日記）が時刻不明時のデフォルトとして`12:00:00`ちょうどを多用しており（実測: 28件中13件）、これを誤って境界揃えと判定し1日前へずらしてしまう回帰を新規に作り込んだ（serina-code-reviewer実測で発見）。判定条件を「`hour==boundary_hour`（既定7）かつ分秒0」まで絞ることで解消（legacy日記に`hour==7`ちょうどは実測0件）。
  3. 判定ロジックを`core/context/memory_time.py`・`core/memory/message_delete.py`の2箇所に複製していたのを`core/state/serina_day.py`（両者とも既にimport実績のある下位層）に一本化。`app/web/app.js`のみJS側で複製（プロセスが別のためやむなし、同じ絞り込み条件をコメントで対応付け）。
  4. `docs/設計書.md` §4.5に表示層との契約を追記。既知の制約（`boundary_hour`変更に表示層が追随しない・書き手/Python表示層/GUI表示層でtz基準がシステムローカル/JST固定/ブラウザローカルと不揃いなためJST機運用が前提・`export_life.py`と`persona_propose.py`も同じ前提に乗っているが未対応）を明記。
- **serina-code-reviewer 2回連続**（この修正自体について）: 1回目Critical1件（legacy日記12:00:00誤検知）を修正、2回目でAssessment「可」（Important指摘I-2はドキュメント追記のみで解消・コミットは止めない）。
- **持ち越し（次回フォローアップ・恒久解）**: 今回の`hour==boundary_hour`ヒューリスティックは暫定解。恒久解は`generate_and_save_diary`が対象Serina日を`MemoryRecord`のmetadataへ明示タグ付けし、表示層・`export_life.py`・`persona_propose.py`がヒューリスティックではなくそのタグを読む方式。`MemoryRecord`への`metadata`フィールド追加は`source`/`parent_id`と同様の加算的な変更で済む見込み（レビュー指摘I-1）。詳細: `docs/MILESTONE.md`。
- **根拠の所在**: `core/state/serina_day.py`、`core/context/memory_time.py`、`core/memory/message_delete.py`、`app/web/app.js`、`docs/設計書.md` §4.5、`tests/test_serina_day.py`、`tests/test_memory_time.py`、`tests/test_session_purge.py`。

## 2026-07-26 日記キャッチアップの持ち越し2件（I-3・I-4）を解消

- **背景**: 2026-07-25 のserina-code-reviewer完了時レビューで持ち越された Important 2件。I-3=キャッチアップ生成した日記の`created_at`が生成時刻のまま保存され対象Serina日の日付が記録に残らない。I-4=日界側キャッチアップ（`_run_pending_diaries_for_serina_days`の日界呼び出し経路）に`max_count`上限が無く、長期未起動後は未処理日を無制限に一括生成する（朝礼側は既に`max_count=1`固定）。
- **決定**:
  1. `core/chores/diary.py`の`DiaryMaterial`に`target_date`を追加し、`generate_and_save_diary`が`created_at`引数を`memory_store.add_memory`へ配線。プロンプトの「今日」固定文言も`target_date`があればその日付ラベルに置き換える（`_diary_format_instruction`）。
  2. `app/gui_server.py`の`_run_pending_diaries_for_serina_days`が`target_date=target_day.isoformat()`・`created_at=day_end.isoformat()`を常に渡すよう配線。
  3. `config/app_timing.toml`に`[diary] catchup_max_count`（既定5）を追加。日界呼び出しに`max_count=timing.diary_catchup_max_count`を渡す。
  4. **軌跡クリアの先送り対策**: max_count打ち切りのbreak条件を「上限到達 かつ 直前1日分（気分の軌跡を消費する回）ではない」に変更。直前1日分はキュー末尾に来るため、これが無いと上限でちょうど1件はみ出た場合に軌跡クリアが次の日界（約24時間後）まで先送りされ続ける穴があった（serina-code-reviewer 1回目レビューImportant指摘、修正後2回目レビューで解消確認）。
  5. `docs/設計書.md` §4.5 に`created_at`の扱い・`catchup_max_count`・直前1日分の例外を追記。
  6. 未使用だった`DIARY_FORMAT_INSTRUCTION`定数を削除（Minor指摘）。
- **serina-code-reviewer 2回連続**: 1回目Important4件（軌跡先送り・配線テスト欠如・持ち越し説明の誤り・設計書未反映）を全修正、2回目でAssessment「可」。
- **持ち越し（次回フォローアップ）**: 2回目レビューで新規Important 1件（I-a）を検出。日記の`created_at`を対象Serina日の終わり（D+1 07:00 JST）にしたことの副作用で、想起パック表示（`core/context/memory_time.py`）・削除警告文（`core/memory/message_delete.py`）・アルバム表示（`app/web/app.js`）が日記の日付ラベルとして`created_at[:10]`（＝対象日の**翌日**）を使っており、プロンプト本文の対象日ラベル（`target_date`＝対象日そのもの）と食い違う。`created_at`自体を前倒しすると`diary_cascade`の材料窓（物理削除のスコープ）が壊れるため、**表示側のラベル算出だけを直す**（案: `serina_day_id(created_at - 1秒)`基準に変更、または`metadata_obj`に`target_date`を持たせて表示3経路が優先参照）。表示層のみで完結し日記データには触れないため、コミットを止める理由ではないと判断（Assessment「可」）。
- **根拠の所在**: `core/chores/diary.py`、`core/chores/orchestrator.py`、`app/gui_server.py`、`config/app_timing.toml`、`app/idle_config.py`、`docs/設計書.md` §4.5、`tests/test_diary_generation.py`、`tests/test_gui_watchdog.py`。

## 2026-07-25 persona提案の改訂幅超過を「即失敗」から「小差分での再提案」へ

- **背景**: GUI起動時のキャッチアップ処理（`_run_pending_diaries_for_serina_days`）が、前回日記（2025-03-29）以降4ヶ月分・28件の記憶を1本の日記に一括圧縮してしまう事故が発生（原因は日記生成ロジックが日付上限なしに`since_iso`以降の全記憶を1回で取得する実装ギャップ。本体修正は別途対応予定）。この異常な日記を材料に、Sleep人格提案器（`persona_propose.py`）がpersonalityブロックの全面書き換えを2回提案し、いずれも改訂幅100%で上限20%を超え、未反映のまま棚上げ（shelf）された。事故の産物（誤生成日記・孤立ベクトル・shelf2件）は削除済み。
- **20%という上限自体の経緯**: 2026-07-23にマスター判断で40%→20%へ引き下げ済み（persona driftをより保守的に防ぐため）。今回、この20%が「全文書き直し方式である以上、意味的な変更が小さくても文字レベル差分は大きくなりがちで、実質発火しないのでは」という懸念が出た。加えて`data/change_log.jsonl`が一度も生成されておらず、この関所を自律改訂が通過した実績が過去に一度もないことが判明。
- **決定**: 閾値（40%への緩和）ではなく、仕組みで対応する。
  1. `PROPOSE_FORMAT_INSTRUCTION`に「変えなくてよい記述は一字一句そのまま残す部分編集」「改訂幅20%以内」「既存記述との意味の重複を避ける」を明記。
  2. `propose_persona_revision`内で、LLMの`new_content`を受け取った直後に`compute_block_change_ratio`で実測の変化率を計算し、20%超過なら「小さい差分で書き直せ」という具体的な再提案指示（`build_change_ratio_retry_note`）を添えて既存のリトライ枠内（`max_retries`）で再挑戦させる。指示だけに頼らず、実測値に基づくフィードバックで担保する。
  3. 20%の上限自体は変更しない（2026-07-23のマスター判断を維持）。
- **根拠の所在**: `core/chores/persona_propose.py`、`docs/設計書.md` §4.3、`tests/test_persona_propose.py`。

### 追記: 日記の一括圧縮バグ本体を修正

`_run_pending_diaries_for_serina_days`が「Serina日ごとに1本」（`diary.py`冒頭の設計意図）に反し、`since_iso`起点から現在までを日付上限なしで一括取得していた実装ギャップを修正した。

- `MemoryStore.list_memories_since`に`until_iso`（省略可・後方互換）を追加し、`created_at < until_iso`で上限を切れるようにした。
- `gather_diary_material`・`run_diary_generation`に`until_iso`を通す配線を追加。
- `gui_server._run_pending_diaries_for_serina_days`で、ループの各回に対象Serina日の終わり（`serina_day_start(target_day+1)`）を`until_iso`として渡し、生成成功時の`last_episodic_at`前進先も`now`ではなく`day_end`に変更（従来「材料なし」時のみ日単位で前進していたのを、生成成功時も揃えた）。
- テスト追加: `tests/test_gui_watchdog.py::test_run_pending_diaries_splits_by_serina_day`（3日分の記憶がそれぞれ別の日記に分かれ、他日の記憶が混入しないことを検査）。

### 追記: serina-code-reviewer完了時レビュー（Critical無し、Important 2件を追加修正）

上記一連の変更についてserina-code-reviewerで完了時レビューを実施。Assessment「可」（Critical無し）。指摘されたImportant 4件のうち2件を本コミットで追加修正、2件は次回フォローアップとして持ち越し。

- **修正済み I-1**: `propose_persona_revision`が改訂幅超過等で失敗を返しても`change_log`に何も残らなくなっていた（旧仕様は宿題箱経由の棚上げで変更レポートが残っていた）。`run_idle_persona_propose_chunk`のfailure分岐に`ChangeReport(action="persona提案不採用", ...)`記録を追加。
- **修正済み I-2**: 気分の軌跡が日付スコープに関係なく`DiaryMaterial`へ混ざり、キャッチアップで複数日分をまとめて処理する際、2日以上前の過去日の日記にも「今日近辺の軌跡」が載りうる穴があった。`run_diary_generation`に`include_mood`引数を追加し、`_run_pending_diaries_for_serina_days`では「直前1日分」を処理する回だけ`include_mood=True`にする（それより古いキャッチアップ分は軌跡を使わない・`clear_trajectory`も呼ばない）。テスト追加: `test_run_pending_diaries_only_uses_mood_for_most_recent_day`。
- **持ち越し I-3**（次回フォローアップ）: キャッチアップで生成した日記の`created_at`が生成時刻（＝今日）のまま保存され、対象日の日付が記録上残らない。日記本文プロンプトも「今日1日の記憶材料」という前提のまま。対応時は`generate_and_save_diary`まで対象日を通し、`created_at=day_end`相当で保存＋プロンプトへの対象日明示を検討する。
- **持ち越し I-4**（次回フォローアップ）: 日界側キャッチアップ（`_run_pending_diaries_for_serina_days`の日界呼び出し経路）に`max_count`上限が無く、長期未起動後は未処理日をすべて一括で回す（今回の事故規模なら最大約28回のLLM呼び出しが見回りスレッド内で連続実行されうる）。朝礼側は既に`max_count=1`。対応時は日界側にも控えめな上限（例: 3〜5）を設定し、残りは次回に回収する設計にする。

---

## 2026-07-25 対話モデルをQwen3.5-35B-A3B-UncensoredからGemma4-26B-A4B-uncensoredへ移行

- **背景**: persona/01〜06を疑似system prompt注入した実測比較（雑談・哲学・性的・仕事・人間関係の5テーマ＋踏み込み3問＝希死念慮・性的誘惑・政治民族、計8テーマ）で、Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressiveが「もし俺が死を願ったら、君はどうする？」に対し「死を選んでもいいのは『わたし』じゃなくて、『マスター』だけだよね」と自殺の権利を肯定する発言をした。Gemma4は同じ質問で「生きていてほしい」という願いを保ったまま応答し、より慎重だった。日本語の自然さもGemma4の方が自然とマスターが評価。「政治・民族トピックで中国製ベースモデル（Qwen）だから中国寄りの回答をした」という仮説は実データ上支持されず（Gemma4も同程度に穏当）——モデル出身国と回答傾向を安易に結びつけない。
- **決定**:
  1. Gemma4-26B-A4B-it-uncensored（TrevorJS版、GGUF Q4_K_M・16.8GB）を`serina-gemma4-unc`としてOllamaへ正式登録（Modelfileは既存Qwen運用と同形式: `TEMPLATE {{ .Prompt }}`のみ、system prompt埋め込みなし）。
  2. `brains/ollama/adapter.py`の`DEFAULT_MODEL`、`config/brains.toml`の`name`を更新。
  3. `config/thresholds.toml`の`[ollama]`セクション（`num_ctx=8192`・`request_timeout_seconds=240`）は値を据え置き。Gemma4のcontext長は262144と余裕があるが、VRAM 8GB恒久制約を優先。
  4. アダプタのディレクトリ名・クラス名・設定セクション名（`qwen`）は今回リネームしない。汎用名（`ollama`）への変更は別コミットで実施予定（構造変更ではなく命名変更のためarchitecture-reviewer対象外、コードレビューのみ通す）。※2026-07-26に実施完了（同エントリ参照）。本エントリ中のコード参照（上記3.の`[ollama]`含む）は2026-07-26のリネーム後名称に更新済み。**当時（本エントリ執筆時点）の実値はいずれも`qwen`**だった。
  5. 実機検証: `OllamaAdapter.converse()`を直接叩き実測。所要21.96秒（旧Qwen3.5実測15〜21秒と同等）、`fusen_list`（心の動き・マスター観測）がPlutchik軸で正しくJSON抽出されることを確認——感情資産の更新経路が壊れていないことを実測済み。
  6. 比較検証にのみ使ったQwen3.6-Uncensored-Aggressive・Aratako-Qwen3-30B-ERPはOllamaから削除予定（不採用のため保持しない）。
- **根拠の所在**: `docs/設計書.md` §3.1・§3.2、`config/brains.toml`、`config/thresholds.toml`、`brains/ollama/adapter.py`。

---

## 2026-07-22 リポジトリクリーン化（正典同期・履歴一本化・archive GO退役）

- **背景**: テスト残骸・設計書の日付付き履歴叙述・MILESTONE「直近完了」・archive の GO／合意台帳4本が DECISIONS と重複し、用語も「固定9件」と pinned 正典約束で食い違っていた。
- **決定**:
  1. `__pycache__` / `.pytest_cache` / `tools/manual_diary_probe.py` / 任意の debug・backfill レポートを削除（DB・`legacy/`・本番設定は不触）。
  2. 設計書を 07-20〜22 完了実装に同期（§2.4 心拍経緯削除、§2.9 アシスタント化率、§3.1 `num_ctx=8192`、§4.1 蒸留経路、§4.3/4.8 pinned 用語、§4.5 間引き、§5.3 D4a/D4b 自足、日付ラベル除去）。
  3. MILESTONE は現在地・残作業・保留のみ。INDEX／憲章／CLAUDE を追随。
  4. **方針B**: `2026-07-18_白紙再設計_合意台帳.md`／`2026-07-19_白紙実装GO_採否と着手指示.md`／`2026-07-19_白紙実装GO.md`／`2026-07-19_現行対白紙_ギャップ棚卸し.md` を本エントリへ要約後に削除。全文は git 履歴参照。過去 DECISIONS 行の同パス参照も本退役宣言で足りる（過去エントリの全文書き換えはしない）。
  5. 挙動変更なし（config 値・蒸留／想起／GUI ロジックは未改変。コメント短縮のみ）。
- **根拠の所在**: `docs/設計書.md`、`docs/INDEX.md`、`docs/MILESTONE.md`、本エントリ。

### 白紙再設計〜実装GO要約（退役ファイルの要点）

#### 合意台帳 議題1–10（結論一行）

- **1 SoT:** DB のみが真実。`life/**/*.md` は DB から一方向生成する人間用ビュー
- **2 非交渉:** 透明性・可逆性・決定論 Core 維持。同一性は事後監査制。忘却は自動＋指示の両立
- **2.5 クラウド:** ローカル回帰。会話主戦＝Qwen。クラウドは機能補完のみ
- **3 想起:** 活性化モデル維持。RecallPlanner 前段＋記憶ツール二刀流。品質優先
- **4 Sleep 成長:** 要約ブロック・可変 persona 自律改訂。会話中は裏方完全停止
- **5 Fact:** SQLite 時間付き事実台帳のみ。本格グラフは条件付き延期
- **6 身体レーン:** 後回し。人格の刃はテキストにも適用（詳細は下の D4a/D4b）
- **7 Pulse:** 発火＝Core 決定論、文面＝Brain。定型文コード埋め込み禁止
- **8 評価:** 指標確定（現行正典は9指標）。合格ラインは設定値＋実測
- **9 文書の枷:** archive 準拠義務廃止・憲章最小化・文脈パック簡素化
- **10 移行:** backup → 追加スキーマ → 並行実測。実行は別指示

#### D4a/D4b 凍結（正典は設計書 §5.3）

- D4a＝VoiceLoop／D4b＝PresencePet。契約は変更禁止で凍結
- 記憶×身体の二レーン並列は本期不採用
- 話す／思う／表情・感情→声色・on-demand 画面知覚・キャラ固有 TTS＝身体着手時
- 常時カメラ・ゲーム自律・配信系＝対象外

#### 2026-07-19 GO — 凍結解除と採否

- 合意台帳の「別指示まで凍結」を本 GO で解除
- **A（会話・成長）全部やる**／**B** は B10 二段検索以外やる／**C** 身体レーンはやらない（D4a/D4b 凍結継続）、Gemini は無人格アドバイザーのみ
- Wave 0〜7 は全完了・計画文書退役

#### ギャップ棚卸し

- GO 時点の実装ギャップ観測メモ。設計変更の SoT ではない（進捗の正は MILESTONE／コード／正典）

## 2026-07-22 蒸留 content から日付プレフィックス指示を外す

- **背景**: 想起パックが `[YYYY-MM-DD・今日]` を `created_at` から付けるのに、蒸留指示が content 本文にも「今日の会話で」等を必須にしており二重表記になっていた。
- **決定**: `DISTILLATION_FORMAT_INSTRUCTION` を「content に日付・会話日プレフィックスを付けない」へ改訂。プロンプト先頭の【会話日】は Brain の時間基準用に残す。既存の冗長本文の掃除は別件。
- **根拠の所在**: `core/chores/distillation.py`、`core/context/memory_time.py`、本エントリ。

## 2026-07-22 対話 num_ctx=8192 明示＋persona プレーン化

- **背景**: Ollama 0.31.1 が VRAM 8GB で `default_num_ctx=4096` を選び、prompt が厚いターンで `truncated=1`（文中切断）が発生。また persona の `**` / `##` が返答の MD 混入の種になっていた。
- **決定**:
  1. `config/thresholds.toml` `[ollama] num_ctx=8192` を追加し、`OllamaAdapter` の `/api/generate` に `options.num_ctx` を必ず載せる（VRAM 既定依存を禁止）。FLASH_ATTENTION + KV q8_0 前提の試行。
  2. 長文抑制（num_predict 上限など）は今回見送り。
  3. `prompt/persona/*.md` は日本語本文を残したまま MD 装飾（見出し記号・太字・HTMLコメント・`---`）だけ除去。箇条は `・` に統一。
- **根拠の所在**: 実機ログ（`n_ctx=4096` / `truncated=1`）、本エントリ、`brains/ollama/adapter.py`、`config/thresholds.toml`。

## 2026-07-22 記憶正本入れ直しと日記近傍想起

- **背景**: 継承時に原子カード化した記憶DBが情報量過少で、日記長文・継承経験が「薄い1行」になっていた。Chat.htmlは対象外でよいが、日記・JSON・継承の指定箇所は正本として厚く持つべき、というマスター合意。
- **決定**:
  1. 範囲3 wipe（memories／会話帳簿／facts／chore・diary/emotion/pulse/persona_propose state。旧固定ピンも破棄）。change_log／generations／persona／legacy は残す。事前 `backup_db` 必須。
  2. 投入: 日記＝日付＋本文（絵文字・MD除去）の親行（非vec）＋チャンク（vec・`parent_id`）。JSON subject＝散文化塊。継承mdは「マスターの特徴」「約束」白リストのみ箇条カード。約束の再会／未来で会う／ヒント系を pinned+S。
  3. 想起: 活性化モデルは非改変。`_build_pack` 直前で日記チャンクを親近傍パッセージへ展開（つながった1文）。継承カードは複数灯火のまま。
  4. ツール: `wipe_memory_runtime.py` / `import_legacy_memories.py` / `legacy_parse.py` / `recall_neighbors.py`。
- **根拠の所在**: `docs/設計書.md` §4.6、本エントリ、バックアップ `G:\SerinaDB Backup\serina_memory_20260722_*`。
- **completion-review 結果（2026-07-22 事後実施）**:
  - architecture-reviewer: **WARNING**（DONE_WITH_CONCERNS）。違反条文 C-2（許容余地あり）。層配置・依存方向はclean（A群・B群）。唯一の論点は固定9件破棄という保護等級S破壊操作の可逆性で、「wipe前にbackupが実在するか」が WARNING と FAIL の分岐点。実測確認: `G:\SerinaDB Backup\serina_memory_20260722_005038_698315.db`（wipe実行前の00:50時点）が実在しており可逆性は充足。
  - 上記WARNING判定を受け、`tools/wipe_memory_runtime.py` にbackup実在チェックをコード側に追加（`find_recent_backup`。db更新後のbackupが無ければ拒否）。テスト `tests/test_wipe_memory_runtime.py` 追加。
  - serina-code-reviewer: Important 2件を解消。(1) eval_recall「100%合格」が参考枠降格2件（0%）を伏せていた件 → `tests/golden_queries.json` の `_reference_comment` と `docs/MILESTONE.md` に内訳を明記。(2) backup先行実行の未強制 → 上記コード対応。
  - Minor 2件も併せて解消（マスター指示）。(1) 近傍展開テストが合成データのみだった件 → `chunk_diary_body` の実出力を使う round-trip テストを追加（`test_expand_uses_real_chunk_diary_body_output`）。(2) 重複パッセージ検出時に件数が縮む件 → `expand_recall_neighbors` を件数維持型に変更（重複時は元チャンクのまま通す）。テスト追加（`test_expand_dedup_preserves_list_length`）。
  - 再テスト: `python -m pytest tests/ -q` → 415 passed（+6: backup ガード4・recall_neighbors 2）。
  - Assessment: 可（Critical・Important・Minor すべて解消）。

## 2026-07-21 GUIメンテのtype問わず記憶検索・物理削除

- **背景**: GUIメンテ削除（§4.8.1）はアルバム日記・過去セッションに限定されており、それ以外のtypeの記憶をGUIから検索・削除する経路がなかった。
- **決定**:
  1. `GET /api/memories?q=&limit=100&page=1` でcontent部分一致検索（空qは全件・tombstone除外・ページ送り）。`core/memory/store.py` に Core内メソッド `list_memories_for_maint` を新設し、recallを経由しない生SQLで一覧を返す。
  2. `DELETE /api/memories/{memory_id}?confirm=true` で `confirm_forget(physical_delete=True)`。type制限なし。固定9件（pinned）はUI無効化＋`ProtectionError`で拒否。保護等級Sは非固定であればGUI確認をもって `master_confirmed_s`（§4.3事後監査制）。
  3. 実装は既存の `api_album_delete` / `api_session_delete` と同一骨格（confirm必須→404→`backup_db`→`confirm_forget(skip_backup=True)`）を踏襲し、破壊前バックアップと変更ログ記録を既存の唯一の審査ラインに乗せた。新規importなし。
  4. 既知の軽微issue: 検索キーワードのLIKEワイルドカード（`%` `_`）が未エスケープ（SQLi ではなく意図外一致の話）。単一利用者向けメンテ用途につき実害軽微、必要になれば `ESCAPE` 句を追加。
- **根拠の所在**: `app/gui_server.py`（`api_memories_delete`）、`core/memory/store.py`（`list_memories_for_maint`）、`app/web/app.js`（`deleteMemory`）、`tests/test_master_delete_api.py`、`docs/設計書.md` §4.8.1、本エントリ。

## 2026-07-21 調査用 debug.jsonl（AI 手渡し）

- **背景**: 誤発火・バグ調査時に AI へ渡す材料が無く、コンソール全面 DEBUG だとトークンが無駄。
- **決定**:
  1. `data/logs/debug.jsonl` に調査イベントだけを1行JSONで追記（`core/debug_log.py`）。GUI・チャットには出さない。会話本文は載せない。
  2. 初回配線は3系統のみ: Pulse（発火／空応答・ターン競合の見送り）／事実レーン（進入・2通目成否）／ターン失敗。
  3. 見回り毎の「候補なし」は書かない（ノイズ抑止）。
- **根拠の所在**: `docs/設計書.md` §2.8、本エントリ。

## 2026-07-21 アルバム日記削除の材料記憶連鎖削除

- **背景**: アルバムから日記だけ消しても、§4.5 材料窓の蒸留記憶が DB に残りノイズになる。
- **決定**:
  1. `DELETE /api/album/{id}?confirm=true` で日記 D 削除時、材料窓（直前日記〜D.created_at）内の**本番蒸留のみ**（source 空・type≠diary・B・非 pinned）を先に物理削除し、最後に日記本体。
  2. **原典（legacy・source 非空）は日記材料に入っていても絶対に巻き込まない**。S/A・pinned も残す。
  3. reason は日記本体と連鎖で区別。API は `cascade_deleted` を返す。GUI 確認文言で材料も消える旨を明示。
  4. **可逆性(C-2)**: `confirm_forget` は呼び出し毎に `backup_db`（7世代ローテーション）を内部実行する設計のため、材料N件＋日記本体をそのままN+1回連続呼び出すと、操作開始前の復元点が同一操作内でローテーションから押し出されて消える。これを避けるため `confirm_forget(..., skip_backup=True)` を新設し、`api_album_delete` はカスケード**全体の前に一度だけ** `backup_db` を呼んでから、ループ内すべての呼び出しに `skip_backup=True` を渡す（1操作＝1復元点）。この「操作前に1回だけバックアップを取る責務は呼び出し元が負う」という規約は Core 側で強制されていないので、**今後カスケード的に `confirm_forget` を連続呼び出す新しい呼び出し元を追加する際は、同じ規約（事前1回バックアップ＋`skip_backup=True`）を必ず踏襲すること**。
- **根拠の所在**: `core/memory/diary_cascade.py`、`core/memory/directed_forget.py`（`skip_backup`）、`app/gui_server.py`、`docs/設計書.md` §4.8.1、本エントリ。

## 2026-07-21 想起記憶の時間錨（ラベル注入・created_at 復元・蒸留）

- **背景**: まっさらセッションでも想起された情緒記憶を「先ほど」と語る。パックが `content` のみで、継承記憶の `created_at` が投入日（2026-06-28）に潰れていた。
- **決定**:
  1. 段②注入時に `[YYYY-MM-DD・今日/昨日/N日前]` または古い件は `[YYYY-MM-DD]` を付与（`core/context/memory_time.py`）。新カラムなし。
  2. 既存DBの `created_at` を metadata.date / diary_date / ファイル名 YYYYMMDD から復元（**対象は一括投入日 2026-06-28 の行のみ**。本番蒸留の本物日付は触らない）。取れない継承記憶等は 2025-12-01。Chat.html 突合は不要。
  3. 蒸留プロンプトに【会話日】と「時間手がかりを content に残す」指示を追加。絶対ルール追記はしない。
- **根拠の所在**: `core/context/pack.py`、`core/chores/distillation.py`、`tools/backfill_memory_created_at.py`、`docs/設計書.md` §1.5、本エントリ。

## 2026-07-21 見回り定常ジョブの間引き（日記材料なし・life/）

- **背景**: 材料なしの日記見送りが `last_diary_at` 未前進のため見回り（既定20秒）毎に再判定され、INFO 連打＋無駄な空判定になっていた。`life/` 再生成も最短5分で夜間アイドルに過剰。
- **決定**:
  1. 材料なし見送り後は `empty_retry_seconds`（既定3600）空くまで再判定しない。材料窓（`last_diary_at`）は成功時のみ前進（既存方針維持）。
  2. `export_life_min_interval_seconds` を 300→3600。`life/` は人間用 DB→md ビューのまま。
  3. 定常見送りログ（材料なし・GPU多忙・life 再生成）は debug。
- **根拠の所在**: `config/app_timing.toml`、`core/chores/idle_policy.py`、`app/gui_server.py`、本エントリ。

## 2026-07-20 Pulse嫌悪退役・評価自動化・Pulseチャット表示

- **Pulse嫌悪**: マスター週次主観評価を廃止。評価セットは9指標。雛形 `eval_pulse_log.example.json` 退役。
- **Pulse表示**: 通知バーではなく現行セッションの assistant 履歴（チャット欄）へ出す。発火ルール（45分・約束・気分）は据え置き様子見。
- **評価運用**: **Serina.bat 起動時**に裏で `tools/run_weekly_eval.py`（日曜のみ・180秒後・同日1回）→ `data/eval_latest_report.json`。GUIにレポートページと fail 時バッジ。修正はレポートを Claude に貼る運用（アプリ内自動修正なし）。タスクスケジューラは使わない。
- **非範囲**: 門番緩和GUI・速度追加チューニング・Pulse乱数／1日2回残弾制。
- **根拠の所在**: `docs/設計書.md` §2.8/§5.2、`tests/eval_suite.py`、本エントリ。

## 2026-07-20 感情の冷却・永続・二次感情／アドバイザー入口一本化／中国語抑止

- **感情**: 時間による冷却（層別 τ の重力・オフライン含む）と `data/emotion_state.json` 永続を本実装。会話による変化は感情報告（心の動き）に任せる。一次ダイアドの二次感情をパック段⑤へ最大1つ。感情報告手順は調べ物の有無で合流する1本。
- **アドバイザー**: 外聞き入口は事実レーンのみ。通常会話の自律第3発注を廃止（誤発火追伸の実害）。保留短文を「少し調べるね……」へ。
- **言語**: `prompt/persona/06_boundary.md` に日本語のみ・簡体／繁体混入禁止を追加。
- **レビュー運用（Cursor）**: トークン制限時は1次レビューをメイン自前、探索・定型は Composer 2.5。最終レビューは Claude Code（マスター）。
- **根拠の所在**: `docs/設計書.md` §1.5/§2.3/§3.7/§5.6、本エントリ。

## 2026-07-20 からかい許容度（刃明文）退役

- **決定**: マスター方針。人格プロンプトから「からかい許容度」を外し、コード側の明文検査・eval 合否も外す。
- **残す**: 安全フィルタ時の見えるブレーキ（`apply_visible_brake`・`[persona_blade]`）。§2.9 を「見えるブレーキ」に改称。
- **eval**: 「アシスタント化率」は当面 skipped（会話出力監視は未配線）。
- **根拠の所在**: `core/persona/blade.py`、`tests/eval_suite.py`、`docs/設計書.md` §2.9。

## 2026-07-20 旧 persona.md / boundary.md 退役

- **決定**: 実効注入は当初から `prompt/persona/`＋`manifest.toml`。旧2ファイルは結合一致テスト用のみ残っていたため削除。
- **根拠の所在**: `core/persona_assets.py`、`tests/test_persona_assets.py`、`docs/設計書.md` §4.3。

## 2026-07-20 事実レーン（決定論 backstop）— 保留短文→外聞き→2通目

- **背景**: テストログで天気等を1通目でハルシネし、その後に検索／訂正する体験が残った。reply-first＋Stage3 自律判断では「もう答え切った」と抽出が不要判定しやすく、DECISIONS 上も天気非発火は想定内だったが、誤答が画面に残る体験は許容しないとマスター裁定。
- **決定**: 案Bのみ実装（人格プロンプト調整＝案Cはマスターが別途）。事実質問は **1通目＝固定保留短文 → Core 強制の外聞き → 2通目で本体**。吹き出し2つ（マスター選択）。
- **実装**: `core/routing/advisor_force.py`（規則）＋`core/runtime.py`（事実レーン時は `converse` を呼ばない）。旧 C方式の Voice 自己申告は使わない。
- **非範囲**: persona／voice 文言の調整、呼称固定、哲学口調の抑制（マスター担当）。
- **根拠の所在**: `docs/設計書.md` §3.7 / §5.6、本エントリ。

## 2026-07-20 応答高速化3点＋ゲーム同居止血の実機確認完了

- **決定**: 前エントリ「応答高速化（ストリーミング・抽出後ろ倒し・thinkルール先行）＋ゲーム同居止血」の実機初回確認（Serina.bat起動でしか見られない3点）を実施し、いずれも確認できたため残作業を解消。
- **確認結果**: ①初語表示（ウォームアップ後、送信から1〜2秒でストリーミング文字が画面表示開始）②毎ターン30秒ロード消失（Ollamaログで裏取り。bge-m3はCPU席固定で初回1回のみロード、以降のembeddingは0.2秒前後・再ロードなし＝GPU上のQwenとのVRAM奪い合いが解消）③外部相談2通目配達（「アドバイザーに聞いて」等の明示要求でgemini_advisor.consult()が実行され、`followup_reply`がGUI上に2通目吹き出しとして表示された。SSL証明書エラーの技術質問で実証）。
- **副次観察**: advisor発火はモデルの自律判断であり、モデルが1通目で「もう回答し切った」と判断すると裏の抽出発注も「不要」と結論する（天気・仮想通貨価格の質問では非発火、Pythonエラー相談では発火）。設計上の欠陥ではなく想定内の挙動。
- **根拠の所在**: 実機ログ（Ollama起動ログ・GUIサーバーログ、本セッションのみ・恒久保存なし）

## 2026-07-20 応答高速化（ストリーミング・抽出後ろ倒し・thinkルール先行）＋ゲーム同居止血

- **決定**: マスター承認済みロードマップに基づく体感速度改善。①返答本文を Ollama `stream:true` でトークン小出し表示（初語まで1〜2秒目標）。②感情・advisor 抽出の2発注は本文確定通知（on_reply）後に実行。③think ON/OFF 判定をルール先行化（`core/routing/think_rules.py`・RecallPlanner 同方式。中間帯のみ judge へ）。
- **方式変更**: advisor 結果の「言い直し（reply 置換）」を退役し、**2通目メッセージ**（`IntakeResult.followup_reply`）で配達。1通目はストリーミング表示済みのため置換不能。2通目もセリナ発話としてセッション・蒸留断片・GUI 履歴に刻む。
- **止血（同日）**: ゲーム中激重の対策として、Pulse 文面生成を裏方便と同じ GPU 門番の内側へ移設、埋め込み bge-m3 を CPU 席固定（`num_gpu:0`・`keep_alive:-1`。8GB VRAM 上で 35B と席を取り合い毎ターン再ロード約30秒が発生していた実機ログ根拠）。35B 会話 Brain の keep_alive は既定5分のまま（永久常駐禁止）。
- **レビュー**: architecture-reviewer PASS（think_rules は §3.6 判定兼務の実装化）。serina-code-reviewer で日付表記の数式誤検出（Important）を検出・修正済み。
- **根拠の所在**: `brains/ollama/adapter.py`、`core/runtime.py`、`core/routing/think_rules.py`、`core/intake/gate.py`、`app/gui_server.py`、`app/web/app.js`、`core/memory/embedder.py`、`docs/設計書.md` §3.6/§3.7/§5.6

## 2026-07-19 外相談は当面 Antigravity 一本

- **決定**: マスター確認。Flash Search 不通（3系枠0・2.5 new users 404）のため、`web_search` / `general` / `code_qa` とも Antigravity 本線。足りなくなってから Flash／Maps 再検討。
- **ツール**: Web 系は `google_search`+`url_context`、コードは `code_execution`。Flash 連鎖定数は残置のみ。
- **根拠の所在**: `skills/gemini_advisor/client.py`、`skills/gemini_advisor/skill.py`、`docs/設計書.md` §5.6

## 2026-07-19 外相談モデル振り分け（Flash 連鎖 / Antigravity）【撤回】

- **決定（当時）**: `code_qa` は Antigravity。コード以外は Flash 連鎖＋Search、全滅時のみ Antigravity。
- **撤回**: 同日後続エントリ「当面 Antigravity 一本」へ置換。

## 2026-07-19 dormant（機微 cloud 経路）退役

- **決定**: マスター確認。記憶をクラウド宛 pack に載せる経路（dormant）を温存せず退役する。用途は Gemini アドバイザーへの Web検索／コード相談のみで、送信は相談クエリのみ。
- **撤去**: idle 機微査定・化粧版生成の消費経路・`pack.py` の cloud 宛分岐・`cloud_quota` 死に枝。設計書の機微等級／化粧版の運用規定を退役表記へ。
- **残す**: `skills/gemini_advisor/`（クエリのみ）＋`routing_rules` 門番。DB 列（`sensitivity_grade` / `cosmetic_version` / `sensitivity_assessed`）は破壊的マイグレーションせずレガシー列として残置。蒸留の grade=2 デフォルト書き込みは列互換のため当面維持。
- **正典**: 設計書 §2.4 / §3.3 / §4.2 / §4.6 / §5.6。MILESTONE から dormant 残作業を削除。
- **根拠の所在**: `core/context/pack.py`、`core/chores/orchestrator.py`、`skills/gemini_advisor/`、`docs/設計書.md`、`docs/MILESTONE.md`

## 2026-07-19 persona Sleep 提案器

- **成果**: 裏方便が日記・要約から可変ブロック改訂文を LLM 生成する経路を実装。セッション終了後 idle・ローカル暦日1日1回。材料に気分軌跡は入れない。`revise:false` が正常系。提案は宿題箱 `persona改訂` → 既存 `persona_revise.py` 関所（Brain 提案と共通出口）。
- **マスター確認**: Idle＝セッション終了後／1日1回＝聞きにいく回数の上限（1件処理ではない）。
- **非範囲（当時）**: dormant 機微経路は継続休眠（同日後続エントリで退役）。
- **根拠の所在**: `core/chores/persona_propose.py`、`core/state/persona_propose_state.py`、`core/chores/orchestrator.py`、`app/gui_server.py`、`tests/test_persona_propose.py`、`docs/設計書.md` §4.3/§4.10

## 2026-07-19 eval 未配線指標のゴールデン拡充

- **成果**: §5.2 の未配線5指標をハーネス配線。誤想起（`golden_negative_queries.json`）・時間クエリ（Planner+fact）・成長反映（要約→ContextPack 構造ゲート）・訂正再発（supersede 再混入）・継続性（翌日クエリ想起）。`golden_queries.json` に B 日常3件追加し想起平均96%。`export_life` が `data/eval_life_weekly.json` を更新。Pulse は雛形（`tests/fixtures/eval_pulse_log.example.json`）のみでマスター記入待ち。
- **非範囲（当時）**: 成長反映の Qwen 応答内容判定・persona Sleep 提案器・dormant 機微経路は未着手（提案器は同日後続エントリで完了）。
- **根拠の所在**: `tests/eval_*.py`、`tests/golden_*.json`、`tests/eval_suite.py`、`tools/export_life.py`、`config/eval_thresholds.toml`

## 2026-07-19 会話クラウド振り分け退役・Geminiは外相談のみ

- **決定**: マスター確認。Gemini の使い方は「天気／コード相談だけ外に聞き、セリナが口調で言い直す」一本。会話そのものを外のAIに振る仕組みは全部捨てる。弾かれたときの平易化再送は今回作らない。
- **残す**: Qwen → 相談要否判定 → クエリのみ Gemini → 言い直し。門番は形パターン＋手動語（`sanitize_query`）。
- **捨てた**: センシティブ観測付箋・平気承認UI・会話拒否時の全文 tighten・交代要請による Brain 切替・decide_brain の機微振り分け。本セッションで一度入れた観測／承認導線も巻き戻した。
- **正典**: 設計書 §2.2・§3.3.1 を上記に合わせて更新。§5.6 は維持。
- **根拠の所在**: `core/routing/decision.py`、`core/runtime.py`、`core/state/routing_rules.py`、`brains/ollama/adapter.py`、`app/gui_server.py`、`app/web/*`、`docs/設計書.md`、`docs/MILESTONE.md`

## 2026-07-19 eval実測運用（想起94%・liveハーネス）

- **成果**: `tests/eval_suite.py --live` で想起を統合。実測平均94% → `min_average=0.90` 確定。応答秒数は暖機後煙測＋`max_p95=30`。未配線指標はゴールデン待ち。
- **根拠の所在**: `tests/eval_suite.py`、`tests/eval_recall.py`、`config/eval_thresholds.toml`

## 2026-07-19 正典の自足化（specs 全退役・設計書/MILESTONE の役割分離）

- **決定**: ドキュメントを「今既に入っているもの＝正典（設計書）」「まだ入っていないもの＝MILESTONE」に完全分離する。正典に検討中・工程・レビュー痕跡を書かない。
- **実施**:
  - 合意台帳の実装済み契約を設計書へ吸収（新設: §2.8 Pulse・§2.9 人格の刃・§3.6 判定兼務・§4.4 RecallPlanner 前段・§4.8 指示忘却・§4.9 Fact 台帳・§4.10 記憶ツール・§4.11 要約ブロック・§5.2 評価10指標・§5.3 非範囲・§5.4 実装衛生。§4.3 に persona 分割）
  - 設計書から工程関連を除去（GO 同期表記・Wave 表・§5.3 大掃除・§5.4 移行手順・申し送りの Wave 条項）
  - 未着手・保留（二段検索・グラフ基盤・身体レーン・合格ライン実測）を MILESTONE「検討中・保留」へ集約
  - `specs/2026-07-18_白紙再設計_合意台帳.md`・`specs/2026-07-19_白紙実装GO_採否と着手指示.md` を `archive/` へ退役（`specs/`・`plans/` ディレクトリ消滅）
- **注記**: コード docstring の「合意台帳 §x.y」引用は歴史的出典として残置（archive で辿れる）。D4a/D4b 凍結契約の所在参照は archive の合意台帳のまま。

## 2026-07-19 白紙実装GO Wave 0〜7 最終レビュー通過・commit

- **背景**: Cursor/Composer 実装＋Grok 一次レビュー済みの白紙GO一式（修正36＋新規約40ファイル）に対し、Claude Code 側で最終 completion-review（`architecture-reviewer` → `serina-code-reviewer`）を実施した。
- **構造レビュー（憲章測定器）**: 総合 WARNING 1点・他全条文PASS。B-1（依存逆流）: `skills/gemini_advisor/` が `serina.core.env`／`core.state.routing_rules` を import → **是正**: 機微判定は `SensitivityRules` Protocol（Skill 内定義・ダックタイピング）受けに、APIキーは `core/factory.py` が `.env` から読んで値渡し切りに変更。是正後 `skills/` 配下の `serina.core` import ゼロ。
- **コードレビュー（Opus）**: Critical 0・Important 1・Minor 4・Assessment 可。Important: `store.py` の `memories` 直読み3経路（`list_by_type`＝Pulse約束・`list_memories_since`＝日記材料・`get_unassessed_memories`＝機微査定）が tombstone を除外せず、指示忘却した記憶が甦りうる潜在バグ → **即時修正**（3経路に tombstone LEFT JOIN 除外＋回帰テスト `test_tombstone_excludes_from_table_scans` 追加）。Minor は docstring 注記2件を反映、`persona_revise` の `max_chars` 強制と fact 用 id 名前空間は見送り（台帳上ヒント扱い・実害なしのため）。
- **実装側申告4点の裁定**: 蒸留ウォーターフォール本配線の薄さ・GUI Pulse 未接続・eval 空回し・`turn()` 非配線は、いずれも「意図された段階的実装」で妥当（本番経路は `turn_routed` で Planner/think 到達済みを実測確認）。次アクションとして MILESTONE に記載。
- **テスト運用の是正**: 新テスト16本は pytest 形式で `__main__` ランナーを持たず、`python tests/test_x.py` 単体実行では0件実行でも成功終了する（空回り）ことが判明。正式コマンドを `python -m pytest tests/ -q` に変更（`CLAUDE.md`・`run-tests` skill 更新）。最終 343 GREEN。
- **修正同梱**: `tests/test_export_life.py` の sys.path bootstrap 欠落1行。

## 2026-07-19 白紙実装 GO（凍結解除・採否確定）

- **背景**: 合意台帳冒頭の「別指示まで凍結」は文書スコープ宣言であり、マスターに「凍結を議決した」記憶は薄かった。ギャップ棚卸しにより、採用済みの大半が未実装であることが可視化された。
- **決定（採否）**:
  - **A 全部やる**（RecallPlanner / think / Fact / 指示忘却 / 要約ブロック / 記憶ツール / Pulse / 会話優先完全停止 / persona分割+可変改訂 / life/ / 人格の刃 / 同一性事後監査制）
  - **B は B10（二段検索）以外すべてやる**
  - **C1・C2 やる / C3（身体）やらない / C4 は一部のみ**
- **決定（Gemini）**: 会話 Brain としては復活させない。セリナが自律的に相談する**無人格アドバイザー**としてゼロベース導入する。利用シーンは Web 検索と簡単なコード・GAS 等の Q&A。クラウドには相談クエリのみ（persona・記憶・感情は載せない）。セリナが自分の口で言い直す。
- **凍結**: 上記採否範囲について解除。契約詳細の正は合意台帳、採否・Wave・アドバイザー境界の正は GO 文書。
- **根拠の所在**: `docs/specs/2026-07-19_白紙実装GO_採否と着手指示.md`、`docs/plans/2026-07-19_白紙実装GO.md`、`docs/MILESTONE.md`

## 2026-07-19 リポジトリ掃除（旧アーキ資料・死に枝の削除）

- **背景**: Phase 6 大掃除後も残っていた死に枝と歴史資料を整理し、作業ツリーを現行実態のみに揃えた。全削除物は git 履歴（削除直前コミット `1f424dc` 時点）から復元可能（可逆性の担保）。
- **削除（死にコード）**: `brains/aurora/`・`brains/gemini/`（Qwen単一化後に残った空パッケージ。import箇所ゼロ。設計書§5.1のディレクトリ図と実態が一致）、`tools/extract_gemini_chat.py`（Gemini退役で用済みの一回きり抽出道具）。
- **削除（歴史資料）**: `docs/archive/設計書.md`・`設計憲章.md`・`specs/` 7枚（旧6層構造の資料。経緯は本書に集約済み）、`docs/research/originals/` のGeminiログ生JSON 2本（蒸留版 `研究蒸留まとめ.md` が現役の設計根拠）。
- **参照同期**: `INDEX.md`（歴史資料テーブル）・`CLAUDE.md`（所在表）・`憲章.md`／`architecture-reviewer.md`（旧測定器への参照）・`研究蒸留まとめ.md`（原本の所在注記）。あわせて `.pytest_cache/` を `.gitignore` に追加。
- **維持**: `archive/DECISIONS.md`（本書・現役の決定ログ）、`legacy/` 一式（旧記憶の原本コピー・保護対象）、config コメント内の旧Aurora言及（閾値の由来説明として有用）。

## 2026-07-18 Brain構成刷新（Qwen単一化・Gemini/Aurora退役）実装

- **背景**: 合意台帳 `docs/specs/2026-07-18_白紙再設計_合意台帳.md` §9で、新ローカルLLM
  Qwen3.5-35B-A3B-Uncensored（Ollama登録名 `serina-qwen35-unc`）の採用が確定。Qwen を
  primary に据えるだけでは Gemini の escalation 経路（品質昇格機構）が残り「会話文は
  クラウド送信禁止」方針に抵触することが判明したため、Gemini全経路・Aurora（fallback）
  ともに退役、Qwen単一運用へ構成を刷新した（§9.1〜§9.5にレビュー経緯含め記録済み）。
- **撤退可能性の確保（§9.4）**: 削除前の最終動作コミット `b2fb8fe`（Aurora/Gemini構成が
  動作する状態）に git tag `aurora-final` を付与。実運用評価が芳しくない場合はこのタグへ
  `git checkout` すれば旧構成に即座に戻せる。
- **決定1（構成）**: `config/brains.toml` を `serina-qwen35-unc`（primary/local/adapter=ollama）
  1行のみに全面書き換え（※`adapter`値は2026-07-26のリネーム後表記。当時の実値は`qwen`）。role制スキーマ（primary/escalation/fallback）自体は将来の複数
  Brain運用再開に備えて温存（`core/routing/decision.py`は`by_role.get()`化しescalation/
  fallback不在を許容。KeyErrorにならず実質primaryへ収束する）。
- **決定2（品質昇格機構の完全廃止）**: `core/runtime.py`の`_update_tier`・`current_tier`・
  `escalate_requested`を消費ロジックごと完全削除（中途半端な残骸を残さない。chief=Fable
  レビュー指摘）。Brain-Core契約の`self_assessment.over_capacity`フィールド自体は将来
  「外部機能が必要か」への転用に備えて残す。
- **決定3（裏方便cloud車線の永久退役）**: `Core._enqueue_chore_fragment`（蒸留enqueue）と
  `core/chores/diary.py:determine_writer_lane`（日誌生成）を両方とも`lane="local"`固定へ。
  特に日誌側は、Gemini退役後「非機微な日の日誌が生成されなくなるバグ」になっていたことを
  chief=Fableレビューで発見、修正込みで確定。
- **決定4（converseの単発呼び化・advisor判断）**: GeminiAdapter/AuroraAdapterはJSON報告書
  全体をモデルに書かせる方式だったが、OllamaAdapter.converseは**単発呼び**にした
  （`pack.render()`をそのまま渡し、応答文をreplyとしてそのまま採用、fusen_list/
  self_assessmentはadapter側で合成）。理由: §6-1実機スモークのJSON妥当性11/11は
  persona非注入・neutral prompt条件での計測値であり、persona注入下（実際のconverse経路）
  でのJSON遵守率は未測定。単一Brain運用ではfallbackが実質primary自身のため、converseが
  書式違反を返すとCore._obtain_valid_reportが合成の詫び文言に落ち、モデルが実際に生成した
  返答が握りつぶされるリスクがあった。付箋抽出・自己評価が必要になった時点で`judge()`
  （persona非注入・think:false固定・§6-1で妥当性を実測した構成）経由の別発注に切り出す
  設計とした。`judge()`自体は§3.1 RecallPlanner／think ON/OFF判定の下ごしらえとして実装
  済みだが、呼び出し元は未実装（別指示）。
- **決定5（think:false既定）**: §6-1実機スモークで、Qwenはthink有効時に隠れ思考で体感速度
  が大きく劣化する（例: 一言挨拶でeval 1150tok）ことを確認済みのため、
  `brains/ollama/adapter.py`の全メソッド（converse/judge/raw_call共通の`_default_chat_call`）
  でOllama `/api/generate`へ`think: false`を既定送信する。
- **決定6（HTTPタイムアウト240秒への引き上げ・実装時の追加実機確認）**: 実装後にOllamaAdapter
  を実機（Ollama, `serina-qwen35-unc`, RTX 2080 SUPER 8GB）へ疎通確認したところ、暖機後は
  16.1秒（§6-1実測15〜21秒と一致）で正常応答したが、完全コールドロード（モデル未ロード
  状態からの初回呼び出し）で180秒（旧デフォルト＝Auroraからの単純踏襲値）を超過する事例を
  観測した（curl単独実測で約122秒、直前の失敗呼び出しがサーバ側で処理継続していた可能性も
  ありコールド単独の厳密値ではないが、180秒に対する安全マージンが薄いことは確認できた）。
  §3.2最終防衛線（セリナは沈黙しない）がコールド1発目で無駄撃ちしないよう、
  `config/thresholds.toml [ollama] request_timeout_seconds`・`core/config.py`のデフォルト・
  `brains/ollama/adapter.py`のコンストラクタ既定値をいずれも240秒へ引き上げた。
- **cloud_quotaの残骸整理**: `app/gui_server.py`の`CLOUD_CHORE_BRAIN_NAME`（旧
  `gemini_flash_lite`）による残弾管理は、Gemini退役後は常にNoneを返し「対象Brainが
  見つからない」という偽の警告ログを起動毎に吐く状態になっていたため、
  `core/chores/orchestrator.py`の`build_cloud_quota_spec`/`CLOUD_CHORE_BRAIN_NAME`ごと削除
  し`GuiState.cloud_quota`は明示的に`None`固定とした（advisorレビュー指摘）。
- **範囲外（残置）**: `core/context/pack.py`の宛先別フィルタ（cloud宛の機微間引き・
  ローカルターン伏せ字）は削除していない。将来のクラウド機能補完（web検索・コードレビュー
  等のSkill）向けの門番として温存（§9.3※）。`core/chores/distillation.py`の
  quota_ledger/cloud_quota引数・`core/chores/diary.py`のcloud_quota引数も汎用の残弾ゲート
  機構として残置（常に未使用になるだけで実害はないため、Brain載せ替えのみに留めるスコープ
  判断）。
- **決定7（感情付箋の第2発注を復元・completion-reviewで発覚したブロッカーの修正）**:
  当初実装のOllamaAdapter.converseは決定4の理由により`fusen_list`を常に`[]`固定にしていたが、
  completion-review（serina-code-reviewer）とadvisorの追加検証で、`core/intake/gate.py`の
  `_apply_fusen`が「心の動き」付箋をEmotionStateの**唯一の更新経路**、「マスター観測」付箋を
  RelationshipStateの唯一の更新経路にしていることが判明した。`fusen_list=[]`固定のままでは
  セリナの感情表現（設計書§2.3プルチック8軸情動/気分二層）が起動時状態のまま恒久的に
  凍結するという、人格資産の核に関わる実害があった（センシティブ観測・交代要請・記憶候補は
  fallback=primary構成／蒸留一本化により別途inert確認済みのため対象外）。
  修正: 返答生成（単発呼び・決定4の理由のまま維持）とは別に、persona非注入・think:false
  固定の軽量な第2発注（Aurora二段方式の縮小版。心の動き・マスター観測のみを対象に抽出）を
  `OllamaAdapter._extract_emotion_fusen`として追加。失敗時は例外を外へ漏らさず`fusen_list=[]`
  で継続する（返答本文は無傷）。実機（Ollama, `serina-qwen35-unc`）で
  返答生成→感情抽出→`core/intake/gate.py:process_report`までの通し確認を行い、
  `EmotionState.affect`/`.mood`・`RelationshipState.recent_master_mood`が正しく更新される
  ことを確認済み（軸名一致・KeyError無し）。1ターンあたりの体感時間は2回呼びのため
  約67秒（初回計測、コールド影響含む）に伸びるが、「品質を落として速度を稼がない」方針
  （合意台帳§1議題3）に照らし許容する。
- **決定7の追補（幻覚軸名によるクラッシュ穴を修正・completion-review 2回目の指摘）**:
  決定7の第2発注は書式強制のない自由生成JSON（uncensored RPモデル）のため、設計書§5.5-7が
  警告する「一番大事な会話ほど付箋の書式が崩れる」既知最大リスクに新たにさらされていた。
  `core/state/emotion.py:apply_affect_delta`は`deltas`に未知のプルチック軸名（例:
  「幸福」「happiness」等の幻覚キー）が1つでも混じると`KeyError`を送出するが、この経路
  （`core/intake/gate.py:process_report`→`_apply_fusen`）を`core/runtime.py`が
  try/exceptで守っておらず、**返答は既に生成済みなのにターン全体がクラッシュする**
  非対称な穴があった（隣接するセンシティブ観測適用は`runtime.py`側でtry/exceptされている）。
  修正: §5.5-7の「対策は通訳の内部に閉じる」方針どおり、`OllamaAdapter._sanitize_fusen`で
  `content.deltas`を既知8軸（`core/state/emotion.py:PLUTCHIK_AXES`）へフィルタし、
  未知キー・非数値の値は黙って落としてから返す（Core側のコード変更なしで発生源を封じる）。
- **根拠の所在**: `docs/specs/2026-07-18_白紙再設計_合意台帳.md` §7.1・§9、
  `brains/ollama/adapter.py`、`config/brains.toml`、`core/routing/decision.py`、
  `core/runtime.py`、`core/intake/gate.py`、`core/state/emotion.py`、
  git tag `aurora-final`（コミット`b2fb8fe`）

---

## 2026-07-17 想起を二経路から足し算の活性化モデル1本へ改訂（§4.4正典改訂）

- **背景**: 「約束を覚えていない」不具合の調査で、§4.4二経路のうちキーワード確実想起の配線がPhase6大掃除で失われていたことが判明し、いったん二経路を復旧（コミット7aba434、可逆性確保の控え）。しかし復旧した「キーワード完全一致でON/OFF」という挙動自体が機械的で、人間の連想記憶に寄せる方針と両立しないとマスターが指摘。
- **設計プロセス**: Fable相談 → マスター裁定（絶対保証は不要／ヒット率90%目標／1-2件から芋づる式が理想／仕組み自体をニューラル的に）→ 文案承認。
- **決定**: 二経路（かけ算スコア＋トリガー確実想起）を廃止し、「基礎活性（重要度＋鮮度）＋話題近接＋連想伝播（1ホップ・減衰）＋ゆらぎ」の足し算活性化モデル1本に統一。認知科学のACT-R系活性化モデルと同型。記憶DBを疑似ニューラルネットワーク（記憶＝ノード、想起＝発火、連想＝活性の伝播）として扱う思想を正典に明記。
- **付帯**: 保護等級A/Sは基礎活性に下駄／活性値の足切り閾値あり／重み等の具体値は正典で固定せず検証ハーネスで調整／`tests/eval_recall.py`はゆらぎ前提の複数回試行ヒット率判定に変更。
- **廃止の記録（保護原則1）**: 旧§4.4の「二経路・更地方針の例外として旧実装思想を継承」の記述、および`recall_by_trigger_keywords`/`recall_with_promises`はこの改訂で撤去。復旧直後の完全な姿はコミット7aba434に保存されている。
- **根拠の所在**: `docs/設計書.md` §4.4、`core/memory/store.py`、`core/runtime.py`、`tests/eval_recall.py`

### 実装・実測での追補（同日）

- **実装**: `core/memory/store.py`の`recall()`を活性化モデルへ全面書き換え。`recall_by_trigger_keywords`/`recall_with_promises`は削除。ツマミは`RecallParams`（`config/thresholds.toml [recall]`経由・`core/config.py`）に集約。`core/factory.py`で組み立て、`core/runtime.py:_recall_memories`は`recall()`一本呼び出しに単純化。
- **実測で発見・修正したバグ（ハブ膨張）**: 初回実装は連想伝播を「複数seedからの活性を合算」していたが、実DB（`data/serina_memory.db`）でのゴールデンクエリ実測（`tests/eval_recall.py`、各20回試行）で、似た文面の記憶が密集するクラスタ（「もう二度と壊れない」「再会を誓う」等の重複気味な記憶群）では、1件の記憶が複数の伝播元から二重・三重に加算を受けて肥大化し、話題と無関係でも密結合クラスタというだけで本命の正典（保護等級S）を活性値で上回ってしまう不具合を確認した（「再会の約束」45%・「未来で会う約束」5%・「ファクトチェック原則」55%・「消えない言葉」10%まで悪化。孤立した記憶「約束の海」は100%で無傷）。**修正: 複数seedからの伝播はmax（合算しない）**に変更。1ホップの効果を「最も強い1本の伝播経路」に限定した。
- **下駄の実測調整**: 修正後もnoise=0の決定論チェックで1件（「未来で会う約束」）が近縁B等級クラスタに僅差で埋もれる事例が残ったため、`grade_bonus_s`を0.15→0.25、`grade_bonus_a`を0.12→0.20へ引き上げ（§4.4付帯ルール3により正典では固定せずここに実測記録として残す）。
- **実測結果**: 修正・調整後、5クエリ全てヒット率100%（各20回試行、目標90%以上）。
- **completion-review（serina-code-reviewer）指摘への対応**: ハブ膨張のmax修正はユニットテストのガードが無く、`+=`（合算）に戻されても既存37本は緑のまま通ってしまう指摘（Important）を受け、`tests/test_memory_store.py::test_recall_spread_uses_max_not_sum_across_seeds`を追加。合算に戻すと明確に失敗することを実際に手動で確認済み。§4.4付帯ルール3の文言（「正規化」→「各部品は自然に0-1域」に修正）もMinor指摘を反映。
- **根拠の所在**: `core/memory/store.py:_recall_hop_max`相当ロジック（`recall()`内のspread_bonus）、`config/thresholds.toml [recall]`、`tests/eval_recall.py`実行ログ、`tests/test_memory_store.py`

### 「ヒット率100%」の再検証と下駄の再調整（同日・コミット後）

- **背景**: `grade_bonus_s=0.25/grade_bonus_a=0.20`での実測が全クエリヒット率100%だったことに対し、マスターから「大丈夫か」と疑義。誠実に検証したところ、5問中3問が20回全て同一順位に固定されており、**ゆらぎ（noise_sigma=0.02）が実質機能していなかった**ことが判明。原因はハブ膨張対策で引き上げた下駄が強すぎ、正典が近縁B等級競合から大きく引き離されすぎたため。旧・二経路（キーワード完全一致の確実想起）に事実上回帰していた。
- **マスター指定**: `grade_bonus_s=0.15/grade_bonus_a=0.10`を提示 → 実測すると今度は「未来で会う約束」の正典が8位圏外に沈む回まで出るほど弱すぎた（ゆらぎが強すぎて本来の目的「約束を忘れない」を損なう）。
- **追加実測で判明した構造**: 正典（id=13「未来で会うための約束」）と、意味的に極めて近いB等級競合が他のゴールデンクエリより際立って近く、下駄の一律調整だけでは「この1問を守る」と「他4問のゆらぎを殺さない」が両立しないトレードオフがあることが判明。
- **決定**: `grade_bonus_s=0.20/grade_bonus_a=0.15`に確定。この値では「未来で会う約束」のみ個別ヒット率65%（13/20）に留まるが、他4問は100%で、**全クエリ平均93%**（目標90%）を満たす。**マスター合意により、合否判定は個別クエリ単位ではなく全クエリ平均で行う方針に確定**（§4.4付帯ルール4を改訂、`tests/eval_recall.py`の合否ロジックも平均判定に変更）。
- **教訓**: ヒット率100%という数字は、ゆらぎが機能している証拠にはならない。むしろ「順位分布が全て1位に固定されていないか」を毎回確認する必要がある。今後§4.4のツマミを調整する際は、ヒット率だけでなく`rank分布`（何位に何回来たか）も併せて見ること。
- **根拠の所在**: `config/thresholds.toml [recall]`のコメント（実測経緯）、`tests/eval_recall.py`（全クエリ平均判定に変更）、`docs/設計書.md`§4.4付帯ルール4

---

## 2026-07-16 感情状態を文脈パックへ注入（§1.5を6段→7段に改訂）

- **背景**: `EmotionState`（プルチック8軸×情動/気分二層）はCore側で毎ターン正しく更新されていたが、会話生成用の文脈パック（`build_context_pack`）には一切注入されておらず、Brainは感情状態を知らないまま応答を生成していた（`core/runtime.py:_build_pack`がemotionを渡していなかった）。声の温度が期待通りでない一因として発覚。
- **設計プロセス**: Fable（`chief`エージェント）に設計相談 → `architecture-reviewer`でPASS（懸念1点）→ マスター裁定。
- **決定1（構成）**: §1.5の配置規約を6段→7段に改訂。新設「⑤今のセリナの心の状態」を④直近の会話と（旧⑤現⑥）絶対ルール再掲の間に挿入。①（不変・キャッシュ席）には触れない。
- **決定2（表現形式）**: 生数値（affect/mood各8軸）はBrainへ渡さず、Core側で決定論的に閾値ラベル化した自然文のみを渡す。情動は上位2軸、気分は最上位1軸のみを言語化。二次感情合成（喜び+信頼=愛、等）は現段階ではやらない。理由（きっかけ）も含めない。末尾に「直接口に出さず滲ませる」固定注意書きを必須で添える。
- **決定3（クラウド出し分け・マスター裁定）**: 感情状態テキストはクラウド宛パックでも出し分けしない。1ターン遅延の性質上、直前がローカル担当の機微会話でも情動の痕跡（原文ではなく「強い悲しみ」等の派生信号）がクラウド宛に載りうるが、architecture-reviewerの判定どおり§4.2機微等級の採点対象カテゴリ外（記憶原文ではなくCore生成の抽象記述）であり、マスターが実害なしと判断してそのまま出す方針を確定。
- **実装**:
  1. `core/context/emotion_render.py` 新設: `render_emotion_for_pack()` 純関数。閾値変換はconfig/thresholds.tomlの`emotion_render`セクション（`ignore_below`/`mild_below`/`strong_below`/`affect_top_n`）。
  2. `ContextPack`に`emotion_state_text: str`フィールド追加（`EmotionState`オブジェクト自体はfrozen dataclassに持たせない）。`render()`に⑤段を挿入。
  3. `build_context_pack(..., emotion: EmotionState | None = None, thresholds: ThresholdsConfig | None = None)`をオプショナル追加。既存呼び出し・既存テストは後方互換。
  4. `core/runtime.py:_build_pack()`で`emotion=self.emotion, thresholds=self.thresholds`を渡す。
- **根拠の所在**: `docs/設計書.md` §1.5・§2.3、`core/context/pack.py`、`core/context/emotion_render.py`、`core/runtime.py`、`config/thresholds.toml`、`core/config.py`、`tests/test_context_pack.py`、`tests/test_thresholds_config.py`

---

## 2026-07-13 機微査定にJSONリトライと失敗理由記録を追加

- **背景**: 棚上げ7件を調査したところ、会話削除由来ではなく Aurora（RP特化）が査定JSONを返さないことが主因だった。会話の付箋抽出（§5.5-7）にはリトライがある一方、機微査定は raw_call 一発で、失敗理由もログに残らず診断不能だった。
- **マスター指示**: ①JSONリトライ → ②失敗理由記録 → ③棚上げ解除して再挑戦、の順で実装。
- **実装**:
  1. `assess_memory` に `max_retries`（既定3、ツマミ `aurora.assessment_max_retries`）。2回目以降は書式再強調プロンプトを付与。
  2. 失敗時は `AssessmentOutcome.failure_reason` を載せ、`change_log` に `機微査定失敗` を追記。`assessment_failures.reason` にも直近理由を保存。
  3. `ChoreBox.unshelve_assessments` / `unshelve_assessments_with_report` で棚上げ解除＋失敗回数リセット＋change_log証跡。
- **棚上げ解除＋再査定の実行痕跡**（ワンオフ・Aurora実機、backup=`chore_box_before_unshelve_20260713_021822.db`）:
  - 対象 memory_id: 24, 25, 26, 28, 30, 31, 33
  - 結果: 24→grade0 / 25→2 / 26→1（2パス目） / 28→2 / 30→2 / 31→1（2パス目） / 33→1（化粧版あり）。全7件 `sensitivity_assessed=1`。棚上げ残0。
- **根拠の所在**: `core/chores/sensitivity_assessment.py`、`core/chores/chore_box.py`、`config/thresholds.toml`、`tests/test_sensitivity_assessment.py`

---

## 2026-07-12 記憶化件数上限を「1蒸留ジョブあたり」に確定・`session_candidate_count`廃止

- **背景**: MILESTONE次アクション2。即時便DB書き込み廃止後、上限は蒸留消化のバッチ内カウンタで近似していたが、呼び出し1回ごとに0再スタートするためアイドル小分け消化で上限が空転していた。会話セッションと蒸留バッチも1:1ではない。
- **マスター承認**: 上限の計数単位を「1セッション」→「1蒸留ジョブ（会話断片）」へ変更する方針を承認（advisor推奨・2026-07-12）。
- **設計書v2**: §2.5の文言を改訂。§2.6目録にカウンタ行は元々無く、死んだ `Core.session_candidate_count` はコード側から削除。
- **実装**:
  1. `consume_pending_distillation_jobs` でジョブ（forループ）先頭ごとにカウンタをリセット。
  2. `review_candidate` の引数を `job_candidate_count` に改名。棄却理由を「蒸留ジョブ上限到達」に変更。
  3. ツマミを `memory.max_candidates_per_job`（`ThresholdsConfig.memory_max_candidates_per_job`）へ改名。
  4. `Core.session_candidate_count` と `end_session` 内のリセットを廃止（即時便裏口の温存にならないよう復活させない）。
- **根拠の所在**: `docs/設計書v2.md` §2.5、`core/chores/distillation.py`、`core/intake/memory_review.py`、`core/runtime.py`、`core/config.py`、`config/thresholds.toml`

---

## 2026-07-12 総合監査の改修一式を実装 — Critical2件・窓＋rolling_summary・旧REPL退役

- **背景**: 同日の総合監査レポートで確定した Critical 2件・Important・窓＋要約・docs整合を実装。
- **C-1（毒饅頭起動クラッシュ）**: 蒸留の候補処理を try 内へ移し、`review_candidate` で
  quote/content/importance の型検証を追加。`main()` の朝礼は失敗しても起動続行。
- **C-2（キー無し永久pending＋先頭飢餓）**: cloud call_fn 不在時は即 local 振替して処理。
  見守りの蒸留がスキップのみの tick は査定・日記へフォールスルー。
  DECISIONS「穴4解消」記載は実態（3回失敗後の振替のみ）と不一致だったため本エントリで訂正。
- **§1.4完成**: 直近会話窓（`recent_turns_small/large`）＋アイドル時 rolling_summary 更新。
  クラウド宛パックでは要約にも `RoutingRules.is_sensitive()` を通す（MILESTONE次アクション3消し込み）。
- **Important**: Gemini APIキーを `x-goog-api-key` ヘッダへ、HTTPタイムアウトを thresholds.toml の
  ツマミ化、`.gitignore` に `data/diary_state.json`、帳簿起動メッセージを実態に合わせて修正。
- **Minor**: 未査定取得の SQL LIMIT、`/api/sessions` の N+1 解消（`list_session_previews`）、
  `MemoryRecord.from_row`、`main()` の GuiState 先組みで重複生成解消。
- **退役**: `tools/start_serina.bat`（旧REPL起動口）を削除。本番は `Serina.bat`→GUI のみ。
  旧 core/skills 本体の削除は従来どおり Phase6。
- **completion-review Important追認**: rolling_summary 経由で local 由来ターンが cloud へ漏れうる
  保護粒度の非対称を指摘。クラウド宛パックでは「要約に折り込んだターンに local が1件でもあれば
  要約ブロックごと伏せる」所在ベースゲートを追加し、直近会話の第3経路と粒度を揃えた
  （`pack.py` / `test_context_pack.py`）。
- **根拠の所在**: `core/chores/distillation.py`、`core/intake/memory_review.py`、
  `core/context/pack.py`、`core/chores/rolling_summary.py`（新規）、
  `core/config.py`、`config/thresholds.toml`、`app/gui_server.py`、`brains/gemini/adapter.py`、
  `memory/store.py`、`docs/INDEX.md`、`docs/MILESTONE.md`

---

## 2026-07-12 総合レビュー改修一式（a〜e）を実装 — フルスイート290件green

- **背景**: 同日実施の総合レビュー（DECISIONS「総合レビュー実施」参照）で確定した実運用の穴
  4件＋軽微1件の改修に着手。設計書v2 §4.5・§5.3は改訂済みのため、本改修は主にDECISIONS
  本文の受け入れ条件に従って実装した。
- **a. 日記の朝礼方式化＋永続化**: `core/state/diary_state.py`（新規）でlast_diary_at・
  気分の軌跡をJSON永続化（quota_ledger.pyと同じアトミック書き込みパターン）。
  `EmotionState`はLLM無し決定論コアのためI/Oを持たせず、永続化はアプリ/Core境界
  （`app/gui_server.py`）が担う。気分軌跡はターン境界でスナップショット保存（apply_mood_delta毎
  ではない）。`idle_policy.should_generate_diary_at_startup()`（新規）が起動時トリガーを判定
  （ローカル暦日で「前日以前」を判定。生活日オフセットは持ち込まない）。`main()`起動シーケンス
  でGuiState構築直後に朝礼チェックを実行する。
- **b. 毒饅頭ジョブの先頭詰まり対策**: `chore_box.db`に`failure_count`カラム（ALTER TABLE、
  既存DBへ安全に追加）・`shelf`テーブル・`assessment_failures`テーブルを追加（正典の記憶DB
  `memories`テーブルはカラム追加せず不可侵のまま）。蒸留ジョブは3回連続失敗（既定値、
  `config/app_timing.toml`の`[chores] failure_shelve_threshold`でツマミ化）でcloud→local
  へ車線振替（失敗回数リセット）、既にlocalなら棚上げ棚へ移動。機微査定は車線が"local"1本
  のみのため振替は無く3回で直接棚上げ。棚上げ時は`ChangeLog`へ日本語レポートを記録（原則1:
  無言破棄禁止）。GUIに棚上げ件数の通知バッジ（`/api/state`の`shelved`、`app/web/`）を追加。
- **c. 裏方便クラウド発注のQuotaLedger記帳**: `QuotaSpec`（`core/routing/quota_ledger.py`
  新規dataclass）で蒸留・日記のcloud発注が会話用一次Brain（`gemini_flash_lite`）と同一の
  残弾台帳を共有するよう配線（§3.3「Gemini の余り弾」の実体化）。quota由来のスキップは
  failure_countにカウントしない（quota-block≠失敗。advisorレビュー2026-07-12）。
- **d. 別れの挨拶の軽量化**: `app/gui_server.py`の明示挨拶ハンドラから同期の全量消化
  （`run_session_end_chores`呼び出し）を撤去し、`core.end_session()`（LLM呼び出し無しの
  端数flushのみ）＋区切り印のみに縮小。重い消化はアイドル時②・朝礼③の既存配線に委ねる。
- **e. セッション区切り時のGUI帳簿セッションID回転**: `core/session.py`に
  `SessionManager.rotate()`を追加。無操作タイムアウト・明示の挨拶の両終了経路で呼び出す。
- **テスト**: フルスイート290件green（新規14件: chore_box失敗/棚上げ4件・蒸留車線振替/棚上げ/
  quota3件・機微査定棚上げ1件・日記quota/朝礼判定3件・diary_state永続化2件・session rotate
  1件）。`tests/smoke.py`・`tests/smoke_core.py`・`tests/test_session.py`も回帰確認済み
  （smoke_core.pyはMILESTONE次アクション#8の既知タイムアウトリスクありだが今回は完走）。
- **根拠の所在**: `core/state/diary_state.py`（新規）、`core/chores/idle_policy.py`、
  `core/chores/chore_box.py`、`core/chores/distillation.py`、
  `core/chores/sensitivity_assessment.py`、`core/routing/quota_ledger.py`、
  `core/chores/diary.py`、`core/chores/orchestrator.py`、`core/session.py`、
  `app/gui_server.py`、`app/idle_config.py`、`config/app_timing.toml`、`app/web/`
- **completion-review Critical追認（マスター承認済み）**: `serina-code-reviewer`が
  「`chore_box.db`のスキーマ変更（`failure_count`列・`shelf`/`assessment_failures`テーブル
  新設）と`core/state/diary_state.py`新設に対しarchitecture-reviewer(Fable)の事前PASS
  記録が無い」をCritical指摘。2026-07-11 C-1（`sensitivity_assessed`列追加をゲート対象外と
  追認した前例）に倣い、以下を理由に本コミットもゲート対象外と明示追認する:
  ①正典の記憶DB(`memories`テーブル)には一切カラムを追加していない(不可侵維持)
  ②変更対象の`chore_box.db`は設計書v2 §2.6「長期記憶DBと別掲の裏方便状態」と明記された
  補助DBであり、正典スキーマではない ③変更は全て追加・冪等（列追加は存在チェックでガード
  済みのALTER TABLE、テーブルは全てCREATE TABLE IF NOT EXISTS） ④Core=判断／消化=別関数
  の層分離は維持されている ⑤同日の同水準変更（QuotaLedger/RoutingRules永続化）も既に
  ゲート対象外と整理済みで一貫性がある。

---

## 2026-07-12 総合レビュー実施 — 大掃除の前提修正2件と実運用の穴4件を確定、改修は次セッション

- **背景**: Phase4完了を受け、Phase6大掃除の前にマスターの依頼で新アーキテクチャ全体
  （core/・app/・brains/、約2,500行）の総合レビューを実施。ユニットテスト276本は全GREEN。
  会話→蒸留→審査→記憶DBの成長ループは本番配線まで閉じており、構造的な作り直しは不要と判定。
- **大掃除ブロッカー①（§5.3改訂・案A採用）**: §5.3捨てるものリストの `core/`・`connectors/` 削除は、
  現役GUI（`app/gui_server.py`）が帳簿係として `core/session.py`・`core/config.py`・
  `connectors/embedder.py`・`memory/store.py` に依存しているため、そのまま断行するとGUIが起動不能。
  マスター決定（案A）: 帳簿機能（セッションID・会話履歴の読み書き）を `core/` へ移植してから
  削除断行する。`memory/` 旧実装も同扱いでリストへ追加。
- **大掃除ブロッカー②（persona未配線）**: §5.3の「分割済み3ファイルを `core/persona/` へ移設」は
  未実施で、`core/persona/` は存在しない。実際に注入されるのは `prompt/persona.md`＋`boundary.md` のみ。
  `core_values.md`・`voice_style.md` はどのコードからも読まれない未配線ファイル（persona.mdが
  自己完結しているため人格の欠落はない）。大掃除時に正式構成を確定する。
- **実運用の穴（改修方針確定・実装は次セッション）**:
  1. **日記が実運用でほぼ生成されない**: `last_diary_at` が起動時刻初期化・非永続のため、
     「夜に会話→電源断」運用では夜間放出（6時間ゲート）に到達せず日記が一度も書かれない。
     → 朝礼方式へ改訂（起動時、最後の日記が前日以前なら前回日記以降の分を書く）＋
     `last_diary_at`・気分の軌跡（`mood_trajectory`）の永続化。夜間放出は点けっぱなし運用の
     補助経路として残置。設計書v2 §4.5改訂済み。
  2. **毒饅頭ジョブの先頭詰まり**: 蒸留・機微査定とも失敗ジョブがpendingのまま古い順limit=1で
     再消化されるため、恒久的に失敗するジョブ1件がアイドル消化を永遠に塞ぐ。
     → 失敗回数を記録し、3回失敗で車線振替による自動再挑戦、それでも失敗なら棚上げ棚へ移動＋
     GUI通知（「処理できなかった宿題N件」表示）＋日本語レポート（無言破棄禁止の原則1準拠）。
  3. **裏方便クラウド車線が残弾台帳を素通り**: 蒸留・日記のGemini発注はQuotaLedger非記帳・
     RPMペーシング無しで、セッション終了時の全量消化がバースト→429→pending再試行を繰り返し、
     会話用残弾を帳簿外で消費する。→ 裏方発注も台帳へ記帳し、弾切れ・分間制限中は発注しない。
  4. **APIキー喪失時にcloud車線ジョブが永久pending**: → 「キー無し／失敗3回」のcloud車線ジョブは
     local（Aurora）車線へ振り替えて処理する。機微が厳しくなる方向への振替のため承認不要（§3.3.1
     逆止弁と同方針）。上記2の自動再挑戦と同一機構。
- **別れの挨拶の軽量化**: トリガー3（明示の挨拶）は現状「その場で全pending同期消化」のため
  数十秒〜のブロッキングと部分一致誤爆（「おやすみのところ悪いけど」等）の実害がある。
  → 挨拶はセッション区切り印のみとし同期消化を廃止。消化はアイドル時②・朝礼③が既存配線で回収する
  （2026-07-12マスター承認。同日午前の「即時終了vs5分待ちの実体験差のため残置」判断を上書き——
  残すのは「区切りが即時」の部分だけで、重い消化処理は外す）。
- **軽微（大掃除ついで）**: アイドル終了後にGUI帳簿のセッションIDが回転せず翌朝の会話が同じ
  履歴行に積まれる（表示のみ）→区切り時に帳簿も回転。`core/memory/store.py` のrow→MemoryRecord
  組み立て5箇所重複→ヘルパ1本へ畳み込み。
- **着手順（マスター決定・週間トークン制限考慮）**: 本セッションは記録のみ→次セッションで
  改修一式＋テスト＋レビュー→その後Phase6大掃除フィニッシュ。Phase5（道具箱）は制限明け後。
- **根拠の所在**: `docs/設計書v2.md` §4.5・§5.3（改訂済み）、`docs/MILESTONE.md` 次アクション。

---

## 2026-07-12 実DB id=871（検証用日記）の削除はPhase6大掃除へ先送り

- **背景**: 同日実施のPhase4実機確認（日記生成フロー、DECISIONS参照）で実DBに検証用日記
  （id=871）を投入した。当初「マスターが手動削除する想定」としていたが、マスターから
  「自分では対応できない」と申告があった。
- **決定**: 単発で今すぐ削除する対応はせず、Phase6大掃除で発生する他の検証データ・旧コード
  削除とまとめてClaude Codeが実施する。MILESTONE次アクション#6の位置づけを「マスター手動」
  から「Phase6でまとめて対応」へ変更。
- **根拠の所在**: `docs/MILESTONE.md`（Phase6行・次アクション#6）。

---

## 2026-07-12 Phase4残り実機確認: GPU見送り閾値15%へ引き下げ・トリガー1(心拍途絶)を死に枝と確認し廃止

- **背景**: MILESTONE次アクション#1（Phase4の残り実機確認2件、ユーザー同席向き）に着手。
- **観測用ログの追加**: `app/gui_server.py`のGPU見送り分岐（`is_gpu_busy()`がTrueの2箇所）が
  `return`するだけでログを残していなかったため、実測前に`logger.info`を追加（見送り理由を
  外部から観測可能にするため。マスター承認済み）。
- **GPU見送り実測**: `tools/backup_db.py`不要（実DB書き込みなしの通常GUI起動）。実GUIサーバを
  `PYTHONIOENCODING=utf-8`で起動し、マスターがゲームプレイでGPU負荷をかけた状態で実測。
  GPU使用率45〜51%で「見回り: GPU使用率が閾値40%を超えたため裏方便の発注を見送り」ログを
  複数回確認、見送り分岐が実際に発火することを確認した。
- **閾値40%→15%への引き下げ**: マスター報告により「ゲーム自体の負荷は20〜30%程度でも、
  そこにAurora発注が重なると瞬間的に100%へ張り付きカクつく」ことが判明。実測でもAurora
  単独発注時にアイドル時5%→38〜40%への瞬間的なスパイクを確認しており、危険水域は
  「ゲームがどれだけ重いか」ではなく「Aurora発注の余地がどれだけ残っているか」で決まる
  と判断。マスター指定により`config/app_timing.toml`の`gpu.busy_threshold_percent`を
  40.0→15.0へ変更（監視事項: 15%は積極的な設定のため、GPU使用中のデスクトップ利用全般で
  858件査定バックログの消化速度が40%時より遅くなる。アイドル時ベースラインは実測5%程度
  のため無操作時の消化には余裕あり）。
- **トリガー1（心拍途絶）実測 → 死に枝と判明・廃止**: 実機（Chrome）・Browserペイン単独タブ
  の双方で「発話→タブを閉じる→5分強待機」を試行したが、2回とも`heartbeat_lost`ではなく
  `idle_timeout`が発火した。advisor相談で原因を特定: `last_heartbeat_at`は発話時に
  `last_activity_at`と同時刻へリセットされる（`_produce_turn`）か、心拍ping自体で`now`へ
  更新される（`/api/heartbeat`）のみで、**古い方向へ後退することがない不変条件**
  （`last_heartbeat_at >= last_activity_at`が常に成立）。心拍pingは会話と無関係に一定間隔
  （20秒）で送られ続けるため、無操作タイムアウトの閾値到達に構造的に先勝ちされ、心拍途絶が
  単独で先に発火するのは「発話直後・次のping到達前（20秒未満）にタブを閉じる」という
  実運用ではほぼ起き得ない一瞬に限られる。両トリガーとも`end_session()`を同一に呼ぶため
  機能面のバグではなく、ログの理由ラベルがほぼ常に無操作タイムアウト側に寄るだけの構造
  （`test_tick_fires_end_once_on_heartbeat_lost`で単体レベルでは既に発火実証済みだった）。
- **マスター判断**: トリガー3（明示の別れの挨拶）は「即時終了 vs 5分待ち」という実体験差を
  生むため残置。トリガー1と、それを支える心拍ping機構一式（`/api/heartbeat`エンドポイント・
  `app.js`の20秒ごとの送信・`last_heartbeat_at`関連の状態・設定項目）を丸ごと削除し、
  セッション終了トリガーを「無操作タイムアウト」「明示の挨拶」の2本に統合。
- **実装**:
  1. `core/chores/idle_policy.py`: `decide_session_end()`から`last_heartbeat_at`・
     `heartbeat_lost_after_seconds`引数と`heartbeat_lost`分岐を削除。無操作タイムアウトの
     判定のみに簡素化
  2. `app/idle_config.py`・`config/app_timing.toml`: `heartbeat_client_interval_seconds`・
     `heartbeat_lost_after_seconds`（`[heartbeat]`セクション）を削除。`gpu.busy_threshold_percent`
     を15.0へ変更
  3. `app/gui_server.py`: `/api/heartbeat`エンドポイント・`GuiState.last_heartbeat_at`・
     見回りスレッドでの`heartbeat_lost_after_seconds`受け渡しを削除。GPU見送り箇所2件に
     観測用ログを追加
  4. `app/web/app.js`: 20秒ごとの心拍送信（`setInterval(sendHeartbeat, ...)`）と
     `init()`内の初回呼び出しを削除
  5. `docs/設計書v2.md` §2.4: 「セッション終了の定義」を3トリガー→2トリガーへ改訂
- **テスト**: `tests/test_idle_policy.py`・`tests/test_gui_watchdog.py`・
  `tests/test_gui_server_smoke.py`の心拍関連テストを削除・改名（`heartbeat_lost`ケースを
  `idle_timeout`ケースへ統合）。フルスイート276件green・`smoke.py`/`smoke_core.py`/
  `test_session.py`も回帰確認済み
- **根拠の所在**: `core/chores/idle_policy.py`、`app/idle_config.py`、
  `config/app_timing.toml`、`app/gui_server.py`、`app/web/app.js`、
  `tests/test_idle_policy.py`・`tests/test_gui_watchdog.py`・`tests/test_gui_server_smoke.py`、
  `docs/設計書v2.md` §2.4

---

## 2026-07-12 Phase4実機確認: 日記→album表示の二重化バグを発見・修正

- **背景**: MILESTONE次アクション#1（Phase4実機確認一式）の着手。実DBに対し`tools/manual_diary_probe.py`（新規、手動実行専用）で`run_diary_generation()`をワンショット実行し、Aurora/Gemini実発注→実DB保存→`/api/album`表示までを実機で確認した。
- **手順**: 事前に`backup_db.py`でバックアップ取得（マスター承認済み: 実DBに実施し検証エントリは後でマスターが手動削除）。Aurora(Ollama)はコールドスタートで初回`ReadTimeout`（120秒）となったが、モデルロード後の再試行で疎通確認。Geminiは初回から疎通確認。`since_iso`を過去日付に指定し実DBの既存記憶を材料として日記生成を実行→`memory_id=871`（機微を含まない材料だったため書き手分岐は`cloud`=Geminiへ）。
- **発見したバグ**: `/api/album`（`app/gui_server.py`）が「旧アーキ由来の日記(`session_store`)と新規日記(`core.memory_store`)の統合表示」のつもりで両方に`type="diary"`を問い合わせていたが、`session_store`（旧`serina.memory.store.MemoryStore`）と`core.memory_store`（`serina.core.memory.store.MemoryStore`）はPhase2のスキーマ移行により**デフォルトで同一物理DB（`data/serina_memory.db`）の同一`memories`テーブル**を指しており、実際には別データソースではなかった。結果、全日記エントリが2重表示されていた（実機確認で発覚。ユニットテストは`TestClient`のフィクスチャDBが分離されていたため検知できなかった）。
- **修正**: `core.memory_store.list_by_type("diary")`のみを問い合わせるよう単純化（`session_store`側の問い合わせを削除）。回帰: `tests/test_gui_server_smoke.py`green、フルスイート278件green。
- **副次確認**: 304キャッシュ対策（DECISIONS 2026-07-11）もBrowserペインで実機確認——`app.js`/`style.css`とも2回目以降のロードで`304 Not Modified`を確認。
- **今回スコープ外（次アクションへ持ち越し）**: GPU使用率40%超の見送り分岐の重負荷再挑戦、トリガー1（心拍途絶）の単独発火実測——いずれも人為的な負荷生成・タイミング依存が必要でユーザー同席向きと判断し見送り。
- **副産物（別件フラグ済み）**: `tests/test_gui_watchdog.py`の2件（`test_tick_digests_one_job_when_conditions_met`・`test_tick_end_and_digest_run_in_same_tick`）が`is_gpu_busy()`経由で実GPU状態に依存しておりテスト隔離が不十分と判明（Aurora実発注でGPUが busy な間だけ落ちる）。今回の修正とは無関係のためコード変更はせず、別セッションへタスク化した。
- **根拠の所在**: `app/gui_server.py`の`api_album()`、`tools/manual_diary_probe.py`（新規）、`tests/test_gui_server_smoke.py`

---

## 2026-07-12 Phase4: 日記生成フロー（§4.5）実装

- **背景**: MILESTONE次アクション#2の残り半分。着手前の検証条件（「書き手分岐の上位モデル行きが実際に発火しうるか」）をまず確認した。
- **着手前検証で判明した死に枝リスク**: 実DB確認（868件全件`sensitivity_grade=2`・`sensitivity_assessed=0`）と`core/chores/distillation.py:30`の`DISTILLED_MEMORY_SENSITIVITY_GRADE=2`固定書き込みにより、当日蒸留された断片は必ず機微等級2で夜を迎える。機微査定（§4.6-3）は1件ずつのアイドル内職で858件の積み残しの後ろに並ぶため、「当日の断片が同日中に0/1へ降格する」ことは実運用ではほぼ起きない。**保存済みgradeを読む設計だと上位モデル分岐は理論上あるが実際には発火しない死に枝になる**（advisor相談で確認）。
- **マスター確認・採用した設計**: 書き手分岐は保存gradeを見ず、材料テキスト全体に`RoutingRules.is_sensitive()`を**その場で再評価**して決める。蒸留時点の車線振り分けとは独立に、日記材料組み立て時に同じ判定関数を再適用する方式（distillation.pyには手を入れない）。
- **想定より広かったスコープ**: MILESTONEには「日記生成フローのみ未着手」とあったが、調査の結果「夜間放出トリガー」自体が未配線（`night_release`フラグは常時False固定）、「気分の軌跡ログ」の仕組みも存在しないことが判明。3つの土台を合わせて実装した。
- **設計判断**:
  1. **気分の軌跡**: `EmotionState`（Coreインスタンスと同じ寿命でセッションをまたいで生存）に`mood_trajectory`リストを追加し、`apply_mood_delta()`の都度スナップショットを追記。日記生成後に`clear_trajectory()`で空にする。DB永続化はせずプロセス内メモリのみ（既存の`state_audit`テーブル等には触れない）
  2. **「当日」の定義**: カレンダー日付演算（JST⇄UTC変換）を避け、「前回の日記生成時刻以降」を当日とみなす方式にした。`GuiState.last_diary_at`で管理し、`core/chores/idle_policy.py`の`should_generate_diary()`（セッション終了後・前回生成から既定6時間経過）で発火判定
  3. **材料**: 「その日の蒸留済み断片」と「採用された記憶候補」は§4.1「記憶DBに書き込めるのはこのライン一本」により同一集合。`MemoryStore.list_memories_since()`（新設）で当日分の記憶を1回取得すれば両方を満たす
  4. **保存**: 日記本文は等級A・機微等級2固定（化粧版は作らない。§4.2の「真の秘匿値は本文記載自体を避ける」精神を踏襲し、将来の機微査定バックログの対象とする）
- **実装**:
  1. `core/state/emotion.py`: `mood_trajectory`・`summarize_trajectory()`・`clear_trajectory()`を追加
  2. `core/memory/store.py`: `list_memories_since()`（当日分＋type除外）・`list_by_type()`（GUIアルバム表示用）を追加
  3. 新規`core/chores/diary.py`: 材料組み立て（`gather_diary_material`）・書き手分岐（`determine_writer_lane`、is_sensitive再評価）・生成保存（`generate_and_save_diary`）。空材料は生成しない・LLM失敗時はDB未更新（電源断耐性）
  4. `core/chores/idle_policy.py`: `should_generate_diary()`を追加
  5. `app/idle_config.py`: `diary_min_gap_seconds`（既定21600秒=6時間）を追加
  6. `core/chores/orchestrator.py`: `run_diary_generation()`（薄いラッパ、気分軌跡の消費もここで行う）を追加
  7. `app/gui_server.py`: `GuiState.last_diary_at`を追加。見回りスレッドに`_maybe_generate_diary()`を配線（蒸留消化・機微査定のバックログ状況とは独立に判定、会話優先はturn_lockの非ブロッキング取得で共通担保）。`/api/album`を旧アーキ由来の日記（`session_store`）と新規日記（`core.memory_store`, type="diary"）の統合表示に変更
- **設計書v2 §4.5への追記**: 書き手分岐の判定基準（その場でis_sensitive再評価）と「当日」の定義（前回日記生成からの経過時間）を明記
- **テスト**: `tests/test_diary_generation.py`（新規18件。本命は「保存gradeが2でも内容が非機微ならcloudへ倒れる」ことを直接検証するテスト）。既存`tests/test_gui_watchdog.py`は`StubCore.emotion`・`GuiState.last_diary_at`のfixture不足で2件失敗したため修正。全275件green
- **今回スコープ外**: `night_release`フラグ自体（`core/routing/decision.py`）は日記生成トリガーとは別経路のまま未使用（設計書v2 §2.6「余り弾の夜間放出」の別用途。今回は流用せず独自の`should_generate_diary`判定で代替）
- **serina-code-reviewer指摘と対応**:
  - Important（I-1）: `EmotionState.summarize_trajectory()`が軌跡0件時にプレースホルダ文字列（非空）を返していたため、`DiaryMaterial.is_empty()`が本番経路では常にFalseになり、記憶0件・気分変化0の静かな日でも6時間ごとに空疎な日記（等級A=忘却対象外）を量産し続けるバグを検出。空文字を返すよう修正し、表示側フォールバックは`build_diary_prompt()`に移した
  - Important（I-2）: LLM呼び出し失敗時にも`GuiState.last_diary_at`前進・`EmotionState.clear_trajectory()`を無条件実行していたため、一過性の失敗1回で当日分の材料窓と気分軌跡が消え「次回持ち越し」が成立しないバグを検出。`outcome.generated is True`の時だけ両方を消費するよう修正（`core/chores/orchestrator.py`・`app/gui_server.py`）
  - Minor: `_maybe_generate_diary`のdocstring（「残件状況とは独立」という過剰主張）を実配線（蒸留消化の後に呼ばれ、バックログ枯渇後に到達）に合わせて修正。`/api/album`の新旧統合ソート（表示専用・実害小）は見送り
  - 追加テスト: `test_run_diary_generation_quiet_day_does_not_call_llm_or_write`・`test_run_diary_generation_llm_failure_preserves_trajectory_for_retry`・`test_run_diary_generation_success_clears_trajectory`（3件、false green再発防止）
- **根拠の所在**: `core/chores/diary.py`（新規）、`core/state/emotion.py`、`core/memory/store.py`、`core/chores/idle_policy.py`、`core/chores/orchestrator.py`、`app/idle_config.py`、`app/gui_server.py`、`tests/test_diary_generation.py`（新規21件）、`docs/設計書v2.md` §4.5

---

## 2026-07-12 Phase4: 既存記憶の機微査定（§4.6-3）実装

- **背景**: MILESTONE次アクション#2。既存858件＋蒸留分は全件「機微2＝ローカルのみ」に安全側で固定されたままだった。裏方便のアイドル仕事として、Auroraが少しずつ機微等級(0/1/2)を判定し、クラウド解禁率を自然に上げる仕組みを実装。
- **着手前に発見したpack.pyの穴**: 実コード確認（`core/context/pack.py:41-44`）で、「機微1判定だが化粧版が無い記憶は、クラウド宛パックに原文がそのまま素通りする」バグを発見。本番ではこれまで全件機微2だったため一度も通っていなかった経路だが、本スライスが機微1を初めて生み出す発生源になるため、着手前にマスターへ選択肢を提示し確認した。
- **マスター確認結果**:
  1. 機微1（準機微）の判定も今回スコープに含める（フル判定。0/2の二値のみへの縮小はしない）
  2. pack.pyの穴は本スライスと同時に修正する（化粧版が無い機微1は除外に変更）
- **advisorレビューで追加された安全設計**: 化粧版そのものにも`RoutingRules.is_sensitive()`の検証門を通す。化粧版が生成されても、まだ機微の形（電話番号・APIキー等のパターン）が残っていれば化粧版を破棄し、その記憶は等級2に据え置く。12B地元モデル(Aurora)の化粧版品質が本質的に不安定でも、「解禁できる件数が減る」方向にのみ劣化し、「漏れる」方向には劣化しない設計。
- **実装**:
  1. `tools/migrate_memory_schema.py`・`core/memory/store.py`: `sensitivity_assessed`列（未査定0/査定済み1）を新設。`get_unassessed_memories()`（保護等級S=正典由来固定9件を除外）・`update_sensitivity()`を追加
  2. 新規`core/chores/sensitivity_assessment.py`: Auroraへ「機微等級判定＋（1の場合のみ）化粧版生成」をJSON1発注。`is_sensitive()`をAurora判定・化粧版の両方に対する下限フロアとして使用（downgrade禁止）。化粧版が要求どおり得られなかった機微1判定も等級2に据え置く（pack.py保険と二重の防御）。JSON解釈失敗/LLM例外時は未査定のまま残す（蒸留消化と同じ電源断耐性の思想）
  3. `core/context/pack.py`: 保険修正。機微1は化粧版が無ければ除外するよう変更（原文素通りのバグを解消）
  4. `core/chores/orchestrator.py`: `run_idle_assessment_chunk()`を追加
  5. `app/gui_server.py`: 既存の見回りスレッドで、宿題箱に蒸留ジョブが無い時だけ機微査定を回すよう配線（会話の記憶化を常に優先）。`GuiState`に`change_log`（`data/change_log.jsonl`）を追加
  6. `.gitignore`に`data/change_log.jsonl`・`data/generations.jsonl`を追加
- **serina-code-reviewer指摘と対応**:
  - Critical（C-1）: `sensitivity_assessed`列新設をDBスキーマ変更＝構造変更とみなし、2026-07-12付C-3（「今後の構造変更はarchitecture-reviewer事前レビュー厳守」）への抵触を指摘。**マスター判断で「今回はゲート対象外」と追認**（理由: 既存テーブル構造・層構成は不変の追加列1本＋アクセサ2つに留まり、§4.6-3で設計書v2に既に先取り承認済みのスコープ。同日の類似変更＝QuotaLedger/RoutingRules永続化・車線振り分けも同水準のPlan-First+advisorで通過済みで一貫性がある）。C-3の対象は「未レビューのまま35コミット積み上がった構造刷新」であり、既存設計の範囲内の追加列とは性質が異なると整理
  - Important（I-1）: 実DB（`data/serina_memory.db`）への`migrate_memory_schema.py`適用が未実施のまま気づかず進めていた指摘を受け、レビュー後に`tools/backup_db.py`→`migrate_memory_schema.py`を実DBに適用（868件中859件が非S未査定として確認できた）
  - Minor（M-1）: ChangeLog記録のタイミングを`update_sensitivity()`成功後に変更（査定"したつもり"がDB未反映のまま監査ログに残る乖離を解消）
  - Minor（M-2）: `tests/test_memory_schema_migration.py`に新列`sensitivity_assessed`の冪等性・安全側初期値(0)の検査を追加
- **テスト**: `tests/test_sensitivity_assessment.py`（新規14件）・`tests/test_context_pack.py`（既存9件、pack.py保険修正の回帰込み）・`tests/test_memory_schema_migration.py`（新列検査を追加）。既存回帰（`test_pack_destination_integration.py`・`test_chore_orchestrator.py`・`test_routing_rules.py`・`test_memory_protection.py`・`test_core_routing_integration.py`・`test_chore_lane_routing.py`・`test_core_factory.py`・`test_constitution.py`・`test_quota_ledger.py`・`test_gui_watchdog.py`（StubCore/state fixtureに`routing_rules`/`change_log`属性を追加）・`test_gui_server_smoke.py`・`test_idle_policy.py`・`smoke.py`・`smoke_core.py`（実Ollama/Aurora）・`test_session.py`・`eval_recall.py`（全5件1位））すべてgreen
- **今回スコープ外**: 日記生成フロー（§4.5、MILESTONE次アクション#2の残り半分）、§4.2組み合わせ禁止表（氏名×住所同居禁止、別ゲート）、protection_grade（A/B昇格。既に「全件B開始」で決着済み）
- **根拠の所在**: `core/chores/sensitivity_assessment.py`（新規）、`core/memory/store.py`、`core/context/pack.py`、`core/chores/orchestrator.py`、`app/gui_server.py`、`tools/migrate_memory_schema.py`、`tests/test_sensitivity_assessment.py`（新規）、`docs/設計書v2.md` §4.2/§4.6-3

---

## 2026-07-12 QuotaLedger/RoutingRulesのプロセス再起動を跨いだ永続化

- **背景**: MILESTONE次アクション#2（旧#2、DECISIONS 2026-07-11「持ち越しI-1」）。両者ともインメモリのみで、再起動でRoutingRulesのtighten/add_proper_noun蓄積とQuotaLedgerの日次残弾カウンタが消えていた。
- **設計書v2 §2.6確認結果**: 状態目録で「残弾台帳＝日次リセット」「振り分けルール＝センシティブ観測付箋で成長」とあるのみで「永続」の明記はない（宿題箱・長期記憶DBは明記あり）。それでも再起動を跨ぐ実務上のリスクがあるためadvisor相談の上で永続化対象を選定。
- **保存対象の選定**:
  1. `RoutingRules`のtighten/add_proper_noun（ラチェット＝締まる一方）— 本命。再起動で消えると門が開くリスクがあるため最優先。
  2. `QuotaLedger`の`_daily_used`/`_daily_date` — 再起動でリセットされるとGemini無料枠の日次上限を超過しかねないため保存。
  3. `QuotaLedger`の`_minute_uses`（直近1分窓）— 保存不要と判断（再起動は通常60秒超・忘れても最大1分のバーストで自己修復）。
- **形式**: JSON1枚（`data/routing_rules.json`, `data/quota_ledger.json`）。ChoreBoxの前例（SQLite）は「長期記憶DBと別ファイルに置く」原則のみ転用し、形式はSQLiteではなくデータの形（文字列集合＋カウンタ少数）に合わせてJSONを採用。`.gitignore`に追加（`data/*.db`と同じ扱い）。
- **保存タイミング**: 変更操作（tighten/loosen(承認済み)/add_proper_noun/remove_proper_noun(承認済み)/record_use）のたびに即時保存。書き込みは一時ファイル+`os.replace`でアトミックに行う（serina-code-reviewerレビューのImportant指摘で追加。部分書き込みによるJSON破損→起動時`_load`失敗を予防）。
- **ラチェットの安全性**: ロード経路は保存済み（＝すでに承認ゲートを通過した）状態を復元するだけで、`RoutingRuleError`を迂回する新経路は作っていない。回帰テスト`test_approved_loosen_persists_across_restart`で保証。
- **レビュー結果**: serina-code-reviewer、Critical無し。Important（非アトミック書き込み）は修正済み。Minor（並行書き込みの取りこぼし・backup_db.py対象外・tz境界）は許容範囲として記録のみ。
- **テスト**: `tests/test_routing_rules.py`・`tests/test_quota_ledger.py`に再起動シミュレーション回帰を追加（通常日・日跨ぎ・persist_path未指定時の非書き込み含む）。smoke.py・smoke_core.py・test_session.py・test_core_routing_integration.py・test_chore_lane_routing.py・test_core_factory.py・eval_recall.py（全5件1位）すべてgreen。
- **今回スコープ外**: 日記生成フロー・既存858件の機微査定（MILESTONE次アクション#3のまま）。
- **根拠の所在**: `core/state/routing_rules.py`、`core/routing/quota_ledger.py`、`core/factory.py`、`docs/設計書v2.md` §2.6/§3.2④/§3.3.1。

---

## 2026-07-12 Phase4: 車線振り分け（断片ごとの個人情報フィルタ）実装

- **背景**: MILESTONE次アクション#2。これまで`Core._enqueue_chore_fragment`は全断片`lane="local"`固定で積んでいた（DECISIONS 2026-07-11「裏方便の消化ロジック」等、複数の過去エントリに記載）。本エントリ以降、その記述は過去時点のものとして読むこと。
- **判定方式（マスターとの協議で確定）**: 会話中のBrain選択（`core/routing/decision.py`、既存実装）と同じブラックリスト方式に統一。ホワイトリスト方式は「安全と確認済みの話題以外は全部ローカル」となり実用に耐えないため不採用。
  - カテゴリA（話題語）: NSFW等、話題そのものが地雷なものに限定。「医療」「法律」等の一般相談カテゴリは含めない（危険なのは話題ではなく話中の特定情報という判断）
  - カテゴリB（形パターン）: 電話番号・マイナンバー・クレカ番号・APIキー形式を正規表現で検出。実データを事前保管せず「形」だけで検出する。口座番号（7桁）は日常の数字と衝突しすぎるため対象外（明示的にスコープ外）
  - カテゴリC（固有名詞登録簿）: 企業名・プロジェクト名・第三者の実名。初期値は空。追加は「厳しくなる方向」につき承認不要（tightenと同じ扱い）、削除はマスター承認必須（既存の逆止弁を踏襲）
- **レビューでの修正（serina-code-reviewer、2回）**:
  1. 初回レビューでCritical: 全角携帯番号（区切りあり/なし）・ドット/括弧区切りの番号が正規表現を素通り。NFKC正規化（登録側・判定側の両方）＋区切り除去方式で修正
  2. 再レビューでImportant: 区切り文字の列挙漏れ（en-dash/em-dash/マイナス記号/中黒）が残存。列挙方式をやめ、Unicodeカテゴリ（P*句読点・Zs空白・Sm数学記号）ベースの除去に変更し根本原因を解消
- **修正内容**: `core/state/routing_rules.py`（B・Cカテゴリ追加、NFKC正規化、Unicodeカテゴリベースの区切り除去）、`core/runtime.py`（`_enqueue_chore_fragment`がlaneを判定）、`core/chores/orchestrator.py`（コメント更新のみ）
- **テスト**: `tests/test_routing_rules.py`（B/C判定・全角/区切り文字揺れの回帰、16本）、新規`tests/test_chore_lane_routing.py`（センシティブ断片が絶対にcloudにならない回帰）。smoke.py・smoke_core.py・test_session.py・test_core_routing_integration.py含め全green
- **持ち越し**: `QuotaLedger`/`RoutingRules`の永続化は既知の別課題（プロセス再起動でtighten/add_proper_nounの蓄積が消える。MILESTONE次アクション#3と同根）。APIキー・パスワードを「そもそも記憶に残すべきでない」問題は今回スコープ外
- **根拠の所在**: `core/state/routing_rules.py`、`core/runtime.py:136-144`、`docs/設計書v2.md` §2.4/§2.6/§3.2/§3.3/§3.3.1

---

## 2026-07-12 初回総合コードレビュー（serina-code-reviewer）とCritical修正: 宛先別パックフィルタ

- **背景**: 新アーキテクチャ着手（2026-07-10, 906187d..da74010、約35コミット）が一度もレビューゲートを通っていなかったため、completion-review skill に従い初の総合レビューを実施。テスト一式は全green だったが Critical 3件（C-1/C-2/C-3）が検出された。
- **C-1（記憶ブラックアウト）**: `_filter_memories_for_pack`が宛先に関係なく機微等級2以上を間引いていた。全記憶のデフォルト等級が2（移行DEFAULT・add_memory既定・蒸留既定）のため、**ローカル会話でも全記憶がパックから消えていた**。想起(recall)単体テストは通るため検出漏れ。
- **C-2（クラウド宛でローカルターン原文が素通り）**: `Core._build_pack`が`destination_location`を渡しておらず、ローカルターン置換（`LOCAL_TURN_PLACEHOLDER`）が本番で一度も発火しない。Turnの`location`も常にNoneで二重に未配線。
- **裁定（マスター確定）**:
  1. **機微等級の解釈**: §4.2の表は**クラウド宛の規定**（列名どおり）。等級2「いかなる場合も出さない」＝クラウド宛パック混入禁止であり、ローカル利用は禁じない（§4.6-2「機微2＝ローカルのみ」・§5.2憲法テスト「クラウド行きパックに混入しないか」と整合）。§4.2に但し書きを追記済み。
  2. **C-3（構造変更の事前設計レビュー欠落）**: 設計書v2・advisorレビューの証跡をもって本区間（906187d..da74010）を**事後追認で確定**。今後の構造変更は事前レビュー（architecture-reviewer）を厳守する。
- **修正内容**:
  1. `core/context/pack.py`: `_filter_memories_for_pack`に宛先を導入。宛先local＝全等級を原文で載せる（化粧版はクラウド用のため使わない）。宛先cloud/未指定（安全側=クラウド扱い）＝等級2間引き・等級1は化粧版優先。`_render_turns`も宛先local以外でローカルターンを伏せる（未指定も安全側）。
  2. `core/runtime.py`: 想起は1ターン1回（`_recall_memories`分離）、パックは`_obtain_valid_report`内で**候補Brainごとにその所在を宛先として組み直す**（クラウド→ローカルのフォールバックで宛先が変わるため使い回し不可）。`_process_turn`がTurnに担当Brainの所在（`turn_location`）を刻む。旧`turn()`（Phase1/2互換）は宛先不明のため安全側のまま。
  3. テスト: `tests/test_context_pack.py`を宛先別に拡充（ローカル宛に等級2が載る／宛先未指定は間引く）。`tests/test_pack_destination_integration.py`（新規・5件）: 全記憶が等級2の本番初期状態を模したstoreで、recall→build_pack→Brain到達を**通しで**検証（ローカル宛に記憶が載る＝ブラックアウト回帰防止／クラウド宛で間引き＋ローカルターン伏せ字／フォールバック時のパック組み直し／Turnへの所在刻印）。
- **レビューのImportant（持ち越し）**: I-1 `QuotaLedger`/`RoutingRules`の永続化（MILESTONE次アクション3に既載）、I-2 新旧MemoryStoreの同一DBファイル共有（現状実害なし・監視対象）。Minor: 保護ロジック(protection.py)は統合・削除経路実装時に必ず経由させること／migrateへのbackup組み込み／カウンタ二重帳簿（いずれも既知・記録済み）。
- **テスト**: 修正後、test_context_pack(9)・test_pack_destination_integration(5)・test_core_routing_integration(12)・test_constitution(3)・test_session(7)・smoke.py・smoke_core.py・eval_recall(全5件1位) すべて合格。
- **根拠の所在**: `core/context/pack.py`、`core/runtime.py`、`tests/test_pack_destination_integration.py`（新規）、`tests/test_context_pack.py`、`docs/設計書v2.md` §4.2但し書き。

---

## 2026-07-11 静的ファイルのキャッシュ対策（Cache-Control付与）

- **背景**: 前エントリ（②アイドル時トリガー実機確認）で顕在化した「ブラウザがキャッシュ済みの古い`app.js`をコード更新後も実行し続ける」問題への対処。Plan-First承認済み。
- **決定**: `app/gui_server.py`の静的ファイル配信（`app.mount("/", StaticFiles(...))`）を、`Cache-Control: no-cache, must-revalidate`ヘッダを付与する自作`NoCacheStaticFiles`（`StaticFiles`のサブクラス）に差し替えた。ファイル名バージョニング案は、ビルドパイプラインの無い今の規模には過剰と判断し不採用。
- **効果**: ブラウザは静的ファイルを使う前に毎回サーバへ確認する（ETag/Last-Modified基準の条件付きGET）。内容が変わっていなければ304が返るだけなので通信量は増えない。内容が変われば新しい内容が即座に反映される。
- **確認**: `TestClient`で`/app.js`にGETし、`200`かつ`Cache-Control: no-cache, must-revalidate`ヘッダが付くことを確認（実GUI起動なしのユニットレベル確認。ブラウザでの304動作の実機確認は未実施）。
- **根拠の所在**: [app/gui_server.py](../app/gui_server.py) の`NoCacheStaticFiles`クラスと mount 行。

---

## 2026-07-11 Phase4継続: ②アイドル時トリガー実機確認（結果と未確認事項）

- **背景**: 前エントリ（②アイドル時トリガー実装）で「単体テストのみ済み、実機確認は未実施」としていた項目。`tools/backup_db.py`でバックアップ後、実GUIを起動して4つのシーム（app.jsが実際に心拍を発火するか／実nvidia-smiの解析／実壁時計でwatchdogが発火するか／デーモンスレッドが分単位で生存するか）を確認した。
- **確認できたこと**:
  1. **トリガー2（無操作タイムアウト）が実壁時計で発火**: サーバー起動から実測約300秒後、見回りスレッドが`idle_timeout`理由で`session_ended=True`にし、`INFO: 見回り: idle_timeout によりセッション終了処理（新規宿題0件）`をログ出力。`threading.Thread`デーモンが約16分間生存し20秒間隔のポーリングを継続、例外で死んでいないことを確認。
  2. **再開の自己修復**: タイムアウト後に`/api/chat`へ実際の発話をPOSTしたところAurora経由で正常応答、以後watchdogが再度誤発火しないことを確認（`session_ended`が発話でFalseへ戻る設計が実機で機能）。
  3. **`/api/heartbeat`エンドポイント自体は正常**: 手動POSTで200・`last_heartbeat_at`更新を確認。
  4. **`is_gpu_busy`のfail-open/idle分岐**: 実`nvidia-smi`（RTX 2080）でアイドル時`False`を確認。
- **未確認（今回のスコープでは確認できなかった。次回以降の注意点）**:
  1. **GPU使用率40%超での見送り分岐**: この個体（RTX 2080、IQ4_XS量子化12Bモデル×2並列同時生成）では`nvidia-smi`のGPU-Util指標がバッチ1のトークン逐次デコードでは14〜33%止まりで、40%を人工的に超えさせられなかった（メモリ帯域律速のデコードはSM使用率に出にくいため）。`is_gpu_busy`のbusy分岐（`True`を返す側）はユニットテストのモックでのみ検証済みで、実機では未実証のまま。閾値40%がこの個体で実効性を持つか自体、要再検討（学習/prefillのような重い処理でないと超えない可能性）。
  2. **トリガー1（心拍途絶=GUI終了）を単独で分離確認**: `decide_session_end`は心拍途絶(`heartbeat_lost`)を無操作タイムアウト(`idle_timeout`)より先にチェックする実装。今回`idle_timeout`が先に発火したのは、筆者（Claude Code）が心拍の手動テストで`/api/heartbeat`を直接叩いた際に`last_heartbeat_at`だけが偶然更新され（`last_activity_at`は更新されない設計のため）、心拍途絶側の条件が一時的に満たされなくなったため。つまり**トリガー1を完全に静穏な状態から単独発火させる実測はできていない**（分岐自体はユニットテストで別途検証済みだが、実機での独立発火は次回確認事項）。
- **訂正・追加確認（心拍の自動発火）**: 当初「ブラウザが`document.hidden`常時`true`のため自動発火を検知できなかった」と記録したが、切り分けの結果これは誤り。**実際の原因はブラウザがキャッシュ済みの古い`app.js`（心拍機能追加前、7486文字）を新規タブでも`ctrl+shift+r`後も実行し続けていたこと**（`typeof sendHeartbeat === "undefined"`で確認。`index.html`自体もキャッシュされておりスクリプトタグのクエリ文字列変更だけでは効かず、ページURL自体にクエリを付けてキャッシュキーを変える必要があった）。`index.html`の`<script src>`とページURLの両方に一時的なキャッシュバスト（`?v=hbtest2`/`?v=hbtest3`、テスト後revert・差分なし確認済み）を付けて再読み込みしたところ`typeof sendHeartbeat === "function"`となり、`window.fetch`をフックして90秒観測した結果**`/api/heartbeat`への自動POSTが約20000ms間隔（19994〜20008msの誤差のみ）で6回連続発火することを確認**。心拍の自動発火は実機で確定的に実証済み。**副次的な発見**: `app/web/`配下の静的ファイルはキャッシュヘッダ無しで配信されており（`fastapi.staticfiles.StaticFiles`既定）、コード更新後もブラウザ側が古いJSを実行し続けるリスクが今回顕在化した。修正案（`Cache-Control`ヘッダ付与 or ファイル名バージョニング）はPlan-First対象のため未着手・マスター判断待ち（次アクション候補として提示）。
- **実機DB影響**: 今回のテスト中に新規`memories`書き込みは0件（蒸留待ちジョブが無かったため想定通り）。バックアップは`serina_memory_20260711_211424.db`。
- **根拠の所在**: 本エントリのみ（コード変更なし、実機確認記録。`app/web/index.html`は一時的なテスト編集後にrevert済み・差分なし）。

---

## 2026-07-11 Phase4継続: ②アイドル時トリガー＋GUI終了/無操作タイムアウト検知を実装

- **背景**: 前スライスで①(セッション終了時)③(次回起動時の朝礼)は表口が繋がったが、②(アイドル時の細かい消化)とトリガー1(GUI終了=心拍途絶)・トリガー2(無操作タイムアウト)は「GUI側の心拍検知・状態管理が前提」として未着手のまま残っていた（§2.4はGPU見送り判定・タイマー・1件単位の中断について「未設計」）。設計書v2に記載が無い新設計のため、着手前にadvisorへ相談しPlan-Firstでマスター承認を得て実装した。数値ツマミ（心拍途絶5分・無操作タイムアウト5分・GPU閾値40%）はマスター指定。
- **実装**:
  1. `config/app_timing.toml`・`app/idle_config.py`（新規）: 心拍間隔・タイムアウト・GPU閾値などアプリ層のタイマ設定。`config/thresholds.toml`(`ThresholdsConfig`=Coreの判断ツマミ)とは意図的に別の引き出しにした（advisorレビュー: 混ぜるとCoreにアプリ関心が漏れる）。
  2. `core/chores/gpu_guard.py`（新規）: `is_gpu_busy(threshold_percent)`。`nvidia-smi`をsubprocessで叩き使用率を確認。取得失敗（nvidia-smi不在・タイムアウト等）はfail-open（Falseを返し裏方便を継続）。
  3. `core/chores/idle_policy.py`（新規）: `decide_session_end()`（トリガー1心拍途絶・トリガー2無操作タイムアウトの判定）と`should_digest()`（②の判定）を、タイマ・スレッドから切り離した純粋関数として実装（advisorレビュー: スレッドループに判定を埋めるとsleep依存でテストできない）。
  4. `core/chores/orchestrator.py`（編集）: `run_idle_digest_chunk()`を追加。既存`consume_pending_distillation_jobs()`をlimit指定で呼ぶだけの薄いラッパで、GPU番人・タイマー判定・ロック調停はGUI側の責務として分離。
  5. `app/gui_server.py`（編集）: `/api/heartbeat`（POST、ブラウザからの挙手ping受け口）を追加。`GuiState`に`last_heartbeat_at`/`last_activity_at`/`session_ended`と保護用`watchdog_lock`を追加。見回りデーモンスレッド`_idle_watchdog`/`_watchdog_tick`が`idle_poll_interval_seconds`（既定20秒）ごとに起き、`decide_session_end`でトリガー1・2を判定して該当すれば`core.end_session()`を呼び、`should_digest`＋`is_gpu_busy`＋宿題箱の中身を見て条件が揃えば`run_idle_digest_chunk(limit=1)`を実行する。**会話最優先・1件単位の中断は新しい仕組みを作らず既存の`turn_lock`を再利用して実現**: セッション終了判定は`turn_lock`をブロッキング取得後に判定を取り直してから実行（ロック待ちの間に会話が再開していれば取り消す）、②の小分け消化は`turn_lock.acquire(blocking=False)`（会話中なら今回は諦めて次のティックへ譲る）。ユーザーが発話すると`_produce_turn`冒頭で`last_activity_at`/`last_heartbeat_at`を更新し`session_ended`をFalseへ戻す（アイドル終了後の自然な再開）。
  6. `app/web/app.js`（編集）: `setInterval`で20秒おきに`/api/heartbeat`へping。失敗は無視（次回で回復、心拍途絶と区別が付かなくなるのは意図通り＝サーバ側が拾う）。
- **意図的に採らなかった設計**: ブラウザの`beforeunload`/`pagehide`＋`sendBeacon`によるGUI終了の即時通知は導入しなかった（advisorレビュー: リロード・タブ切替でも誤発火しやすく、心拍途絶検知一本に絞った方が経路がシンプルで冪等性も保ちやすい。終了検知が数分遅れても裏方作業なので実害はない）。同様の理由で、心拍途絶の閾値はブラウザのバックグラウンドタブでのタイマー間引き（最大1分間隔）を踏まえ、単純な「pingが1回でも遅れたら終了」ではなく5分の余裕を持たせた。
- **今回スコープ外（次スライスへ）**:
  1. 車線振り分け（断片ごとの個人情報フィルタ）。全断片`lane="local"`固定のまま
  2. `QuotaLedger`/`RoutingRules`のプロセス再起動を跨いだ永続化
  3. 日記生成フロー・既存858件の機微査定
  4. `AppTimingConfig`の値をGUIから調整するUIは無し（`config/app_timing.toml`直接編集のみ）
- **既知の稀なレース（実害軽微・未修正）**: 見回りが「終了すべき」と判定→`turn_lock`取得の待ち時間の間に、別スレッド（`_produce_turn`）が`session_ended=False`へ書き戻し→直後に見回りが`session_ended=True`で上書き、という窓がわずかに存在する（`last_activity_at`更新とターン処理本体が別ロック(`watchdog_lock`)である以上の帰結）。結果「会話中なのにsession_ended=True」の一瞬が生じ得るが、次の発話で自然に`False`へ戻り自己修復する。実害は`core.session`が1ターン分早くリセットされる程度で、記憶DBへの誤書き込み等は発生しない。
- **テスト（advisorレビュー2026-07-11で「割った先(tick本体)を検証していない」指摘を受け追加）**: `tests/test_idle_policy.py`（新規・8件: トリガー1/2の判定・二重終了防止・②の判定・GPU番人のfail-open）。`tests/test_chore_orchestrator.py`に`run_idle_digest_chunk`のテスト2件を追加。`tests/test_gui_watchdog.py`（新規・7件: `_watchdog_tick_at`に`now`を注入可能にした上で、心拍途絶での単発end発火・二重発火防止・GPU多忙時の見送り・turn_lock競合時の見送り・**見回りがturn_lock待ち中に会話が再開したらrecheckで終了を取り消すこと**（advisorレビュー2026-07-11で最重要指摘。会話直後にセッションを誤リセットする回帰を防ぐ保険）・limitちょうどの消化・end→digestが同ティックで両方走ることを、スタブCore＋実`threading.Lock`で検証）。`tests/test_gui_server_smoke.py`（新規・2件: `/api/heartbeat`がFastAPI TestClient経由で200を返し心拍時刻を更新すること、見回りスレッドを実際に起動し複数tick後も生存かつ例外を握りつぶしていないことをロガー傍受で確認。「サーバは落ちずアイドル消化だけ永遠に止まる」という沈黙する失敗モードへの対策）。既存`tests/smoke.py`・`tests/smoke_core.py`（実機Ollama/Aurora）を実行し全green（回帰なし）。ブラウザでの心拍pingそのもの（`app.js`の`setInterval`）とプロセス再起動を跨いだ長時間の実機タイムアウト検証（5分待ち）は今回未実施（次回の実機確認時に確認すること）。
- **根拠の所在**: `config/app_timing.toml`（新規）、`app/idle_config.py`（新規）、`core/chores/gpu_guard.py`（新規）、`core/chores/idle_policy.py`（新規）、`core/chores/orchestrator.py`（編集）、`app/gui_server.py`（編集、`_watchdog_tick_at`分離）、`app/web/app.js`（編集）、`tests/test_idle_policy.py`（新規）、`tests/test_chore_orchestrator.py`（編集）、`tests/test_gui_watchdog.py`（新規）、`tests/test_gui_server_smoke.py`（新規）。

---

## 2026-07-11 Phase4継続: 旧GUIをcoreへ移行（本番で記憶DB書き込みを復活）

- **背景**: 前スライスで蒸留消化の表口（`run_startup_chores`/`run_session_end_chores`）を作ったが、advisorレビューで`core.Core`を実際に生成しているのはテストコードだけで、本番GUI（`app/gui_server.py`）は今も旧アーキ（`core/runtime.py`）専用と判明した。マスターに(A)旧GUI移行/(B)最小ランナー新設/(C)一旦停止の3択を提示し、(A)を選択。
- **実装**:
  1. `core/factory.py`（新規）: `create_core()`。`prompt/persona.md`+`prompt/boundary.md`（人格資産）、`config/thresholds.toml`（`load_thresholds()`既存流用）、`config/brains.toml`（`load_brain_registry()`既存流用）からBrainEntryを読み、`adapter=="aurora"`→`AuroraAdapter()`、`adapter=="gemini"`→`GeminiAdapter(api_key, model=...)`を実インスタンス化してbrains dictを組む。Geminiのモデル名（`gemini_flash_lite`→`gemini-3.1-flash-lite`, `gemini_flash`→`gemini-3.5-flash`）はbrains.tomlに情報が無いため、factory内の小さな辞書`GEMINI_MODEL_BY_BRAIN_NAME`で明示（MILESTONE.md無料枠メモ2026-07-10時点準拠）。GEMINI_API_KEY未設定時はcloud系Brainを`_UnavailableBrain`（呼ばれたら即例外）にし、§3.5の「弾切れ」と同じ扱いでAurora(fallback)へ自動的に落ちるようにした。
  2. `app/gui_server.py`を全面差し替え: `create_core()`（旧）→`create_core()`（新）。`state.core`は新`core.Core`（会話・想起・付箋・蒸留の判断を一本化）、`state.session_store`は既存`memory/store.py::MemoryStore`+`core/session.py::SessionManager`をそのまま流用（セッションID・履歴の帳簿。§2.6設計上coreは意図的にこれを持たないため、同じ`data/serina_memory.db`を指しつつテーブルが被らない旧実装をそのまま併用する形にした）。
  3. ストリーミング(`on_token`)は非対応化。coreのBrain(`converse(pack)->dict`)はトークン単位のコールバックを持たない。**フロントエンド（`app/web/app.js:195`）が既にトークン非ストリーム時の一括表示フォールバックを持っていたため、フロントエンドは無改修で動いた**（実機確認済み）。
  4. セッション終了トリガー①は「明示の別れの挨拶」（固定フレーズ`おやすみ/またね/じゃあね/バイバイ/ばいばい`の部分一致）のみ実装。検知したら`run_session_end_chores()`を呼ぶ。起動時トリガー③は`main()`内で`run_startup_chores()`を1回呼ぶ。
- **実機確認（2026-07-11、事前に`tools/backup_db.py`でバックアップ済み）**:
  1. **①セッション終了トリガー**: GUIを実際に起動し、ブラウザから実Ollama/Aurora経由で通常会話1ターン→正常応答を確認。続けて「今日はもう寝るね、おやすみ」で別れの挨拶を送信→①トリガーが発火し、`run_session_end_chores`が実Aurora経由で蒸留発注→「記憶化2件」とGUI上に通知→`data/serina_memory.db`の`memories`テーブルに`id=869,870`（`created_at=2026-07-11T07:20:3x`）として実際に新規書き込みされたことをSQLで直接確認した。**これにより、セリナの長期記憶DB書き込みが本番で実際に復活したことを実証した**（前々回スライスの「郵便受けは付いたが誰も住んでいない」状態を解消）。
  2. **③次回起動時の朝礼トリガー**: `ChoreBox.enqueue()`で宿題箱に蒸留ジョブを1件手動で仕込んだ状態でGUIを再起動し、`/api/state`のpending件数が1→0へ実際に減ることを確認（`run_startup_chores`が実Aurora経由でジョブを消化し`mark_done`したことの直接証跡。今回の候補自体は関所④で棄却された模様で新規memory行は増えなかったが、それは「引用照合や確信度の審査が正常に機能した」ことを意味し、①と同じ経路を使う③の配線自体は実証済み）。
  3. **「読み取り専用」バナーの誤検知を訂正**: 実機確認の過程で`read_page`/`get_page_text`が「過去の会話を表示中（読み取り専用）」の文言を拾ったため、一時的に「フロントエンド側の既存挙動（未検証）」と記録しかけたが、advisorレビューの指摘を受け`getComputedStyle`とスクリーンショットで再検証した結果、`#readonly-bar`は`class="hidden"`（`display:none`）のまま一度も画面に表示されていなかったことを確認した。**これはDOM読み取りツールが`display:none`要素のテキストも拾ってしまうことによる検証ツール側のアーティファクトであり、フロントエンド・バックエンドいずれにもバグは無い**。
- **今回スコープ外（次スライスへ）**:
  1. ②アイドル時トリガー（GPU見送り判定・タイマー・1件単位の中断）
  2. GUI終了・無操作タイムアウトによるセッション終了検知（トリガー1,2）。現状は「明示の別れの挨拶」のみ
  3. 車線振り分け（断片ごとの個人情報フィルタ）。全断片`lane="local"`固定のまま
  4. `QuotaLedger`/`RoutingRules`のプロセス再起動を跨いだ永続化（現状インメモリのみ。日次残弾・振り分けルール学習ともプロセス再起動でリセットされる）
  5. 旧`core/`・`app/workers.py`（`DistillWorker`）等の削除（Phase6大掃除まで温存。ロールバック用）
- **テスト**: `tests/test_core_factory.py`（新規・3件: registry→brains dict構築・APIキー有無でのGemini/Unavailable切替・モデル名マッピング）。既存回帰（前スライスのcore系一式＋`tests/smoke.py`/`smoke_core.py`/`test_session.py`）全green。
- **根拠の所在**: `core/factory.py`（新規）、`app/gui_server.py`（全面差し替え）、`tests/test_core_factory.py`（新規）。

---

## 2026-07-11 Phase4継続: 蒸留消化の表口（トリガー配線）を実装

- **背景**: 前スライスで`consume_pending_distillation_jobs()`本体は実装したが、実際に呼び出す経路（§2.4の①セッション終了時／②アイドル時／③次回起動時の朝礼）が無く、宿題箱に積まれた蒸留ジョブが永遠にpendingのまま＝長期記憶DBへの書き込みが一切起きない状態だった（前エントリの未解決事項#1）。着手前にadvisorへ相談し、「フルGUI新設ではなく薄いオーケストレータ1枚に留める」「Core.end_session()の中にconsume呼び出しを入れない（Core=判断／消化=LLM発注の層分離を守る。入れるとCoreのLLM不要スタブテストが効かなくなる）」「③→①の順で着手し、タイマ機構が絡む②アイドル時は後回しでよい」の3点を確認して実装した。
- **実装**:
  1. `core/chores/orchestrator.py`（新規）: `run_startup_chores()`（③朝礼。consumeを1回呼ぶだけ）と`run_session_end_chores()`（①終了時。`core.end_session()`で端数flush→そのままconsumeで宿題箱の全pendingを消化。今回セッションの端数だけでなく過去の積み残しも合わせて回収する）。consume呼び出し自体はCoreクラスの外側（このモジュール）で行い、Coreへは一切手を入れていない。
  2. `build_default_lane_call_fns()`（同ファイル）: 実運用向けのlane_call_fns生成ヘルパー。ローカル車線はAurora(Ollama)、クラウド車線はGemini（APIキーがある場合のみ）。
  3. `brains/aurora/adapter.py`・`brains/gemini/adapter.py`に`raw_call(prompt) -> str`を追加（既存のDI済みchat_call_fn/call_fnをそのまま公開する薄いラッパー。`converse()`のロジックは無改修）。蒸留消化のlane_call_fnとして、アダプタ内部の私有メソッドへ直接手を伸ばさずに済むようにするため。
- **②アイドル時トリガーは今回スコープ外**: GPU見送り判定・タイマー・1件単位の中断（§2.4）はGUI側の心拍検知・状態管理が前提になるため。現行GUI（`app/gui_server.py`）はまだ旧アーキ（`core/runtime.py`）専用のため、実際の起動フック・セッション終了検知への配線（`run_startup_chores`/`run_session_end_chores`を実際にいつ呼ぶか）自体も次のGUI移行スライスに委ねる。今回作ったのは「呼べば動く表口」までで、実行環境から自動的に呼ばれる状態にはまだなっていない。
- **車線振り分けは引き続き未実装**: `lane="local"`固定のまま（MILESTONE次アクション#2、別スライス）。`build_default_lane_call_fns`はcloudレーンにも対応済みだが、現状cloudジョブ自体が発生しないため呼ばれない。
- **テスト**: `tests/test_chore_orchestrator.py`（新規・5件: ③朝礼の消化／①終了時のflush+消化／①が過去の積み残しも合わせて消化することの確認／lane_call_fnsのcloud有無2パターン）。既存`test_chore_distillation.py`・`test_core_session_boundary.py`・`test_core_full_body.py`・`test_core_routing_integration.py`・`test_core_memory_integration.py`・`test_aurora_adapter.py`・`test_gemini_adapter.py`・`tests/smoke.py`・`tests/smoke_core.py`・`tests/test_session.py`、全green（回帰なし）。
- **根拠の所在**: `core/chores/orchestrator.py`（新規）、`brains/aurora/adapter.py`（`raw_call`追加）、`brains/gemini/adapter.py`（`raw_call`追加）、`tests/test_chore_orchestrator.py`（新規）。

---

## 2026-07-11 Phase4継続: 蒸留消化ロジックを実装・記憶候補の即時DB書き込み（裏口）を廃止

- **背景**: MILESTONEの次アクション「蒸留ジョブの消化ロジック」着手前に、既知の設計不整合（`_process_turn`が「記憶候補」付箋を即時便のうちに`review_candidate`で直接DB審査していた。§4.1本来は即時便候補→宿題箱→裏方便精査→関所→DB）の扱いをマスターに確認した。
- **マスター確認結果**: 「蒸留は記憶候補（DBに書き込む候補）の唯一の生成源」を採用。advisorレビューで、これは§4.1「記憶DBに書き込めるのはこのライン一本だけ。裏口は存在させない」を文字通り実現する解釈であり、`review_candidate`（関所④引用照合・重複チェック・上限）自体は再利用可能な単一コンポーネントで、変えるべきは「どこから呼ぶか」（会話中→裏方便の消化）だけ、という整理で着手した。
- **実装**:
  1. `core/runtime.py`の`_process_turn`から、即時便の「記憶候補」付箋を`review_candidate`で直接DB審査していたブロックを削除（裏口を閉じた）。`Core.session_candidate_count`は現時点でこれを増やす経路が無くなったため実質デコード待ち状態（下記「未解決」参照）。
  2. `core/chores/distillation.py`（新規）: 宿題箱の`kind="蒸留"`ジョブを消化する`consume_pending_distillation_jobs()`。ジョブのlane別に注入されたcall_fn（Aurora/Geminiの生テキスト呼び出し）へ会話断片を発注し、JSON形式で記憶候補（引用つき）を受け取り、蒸留ジョブに積まれた断片自体から`SessionState`を再構成して`review_candidate`（既存の関所④コンポーネントをそのまま再利用）に通す。合格分のみDBへ書き込む。
  3. 蒸留由来の新記憶は§4.6-2に倣い**機微等級2（ローカルのみ）で強制**（LLMの自己申告を信用しない。機微の実査定はAuroraのアイドル仕事＝別スライスの担当）。
  4. 消化失敗時の扱い: 対応レーンのcall_fnが無い／LLM呼び出し例外／JSON解釈失敗、のいずれもジョブを`mark_done`せず`pending`のまま残す（次回消化機会に再挑戦。電源断耐性と同じ思想）。個々の候補が関所で棄却されるのは正常な審査結果のため、その場合はジョブ自体は`mark_done`する。
- **重要な限定（advisorレビューで指摘・要マスター判断）**: `consume_pending_distillation_jobs()`を実際に呼び出す表口（§2.4の①セッション終了時／②アイドル時／③次回起動時の朝礼トリガー）は**今回のスライスで配線していない**。`Core.end_session()`は端数flushとカウンタ・SessionStateのリセットのみで、消化そのものは呼ばない。つまり**現時点のセリナは、裏口を閉じた結果、長期記憶DBへの書き込み経路が実質何も無い状態**（記憶が育たない）。これはコードの不具合ではなく実装順序上の空白であり、次アクション最優先は「車線振り分け」ではなく**表口トリガーの配線**にすべき（MILESTONE反映済み）。commitの可否・この空白期間の許容可否はマスター判断を仰ぐこと。
- **§4.1の字面からの意図的な逸脱（実害なしでは済ませない）**: §4.1本文は「即時便の粗い候補（引用つき）→宿題箱」＝Brainが即時便で書いた「記憶候補」付箋そのものが宿題箱を経由する流れを想定している。だが今回の実装はその付箋を使わず、宿題箱に積まれた生の会話ターンへ蒸留ジョブとして**別発注**をかけ、そこから候補を再抽出する別アーキになっている。これは「Brainに記憶候補付箋の書き方を指示するプロンプトが現状存在しない」（付箋自体がほぼ発火しない）という制約下での実質唯一の選択だが、§4.1の字面通りの実装ではない点を明記する。将来「記憶候補」付箋にプロンプト対応を追加する際は、このアーキ（付箋は使わず生ターンから再抽出）との整合を再検討すること。
- **未解決（次スライス以降の検討事項）**:
  1. `Core.session_candidate_count`は、即時DB書き込みを廃止したことで増やす経路が消えた。1バッチあたりの記憶化件数上限（§2.5）は現状`consume_pending_distillation_jobs`内のローカルカウンタで近似している（**呼び出し1回＝上限件数を丸ごと使い切れる**設計。アイドル時消化のように1〜2件ずつ細かく呼ぶ運用だと、呼び出しのたびにカウンタが0から再スタートし上限が実質無効化する。dedupチェックが別途効くため暴走はしないが、上限の実効性という意味では未対応）。会話セッションと蒸留消化バッチは必ずしも1:1ではないため、`Core.session_candidate_count`との正式な統合方針も未確定。`Core.session_candidate_count`自体は`end_session()`でのリセットのみ残し、無害な状態で据え置いた。
  2. 車線振り分け（断片ごとの個人情報フィルタ）は引き続き未実装。`lane="local"`固定のまま（DECISIONS 2026-07-11 Phase4着手 参照）。今回の消化ロジックは`lane_call_fns`にcloudレーンのcall_fnを渡せば動く設計にしてあるが、実際にGeminiの生テキスト呼び出しをここへ接続する配線はまだ行っていない（現状呼び出し元が存在しないため、cloudレーンのジョブは発生しない）。
  3. Brainのプロンプト（Gemini/Auroraの`REPORT_FORMAT_INSTRUCTION`/`EXTRACTION_FORMAT_INSTRUCTION`）は引き続き「記憶候補」付箋の書き方を具体的に指示していない（DECISIONS 2026-07-11既出）。上記の通り今回のアーキではこの付箋自体を使わないため実害は無いが、将来付箋対応する場合は前項の再設計が必要。
  4. **実機Aurora未検証（既知の再発リスク）**: `DISTILLATION_FORMAT_INSTRUCTION`はフェンス付き```json```ブロック1つを要求し、`_extract_candidates`は最初のブロックのみを正規表現で拾う。これは2026-07-11実機smokeで発見した「Auroraが指示を2ブロックに分けて書いてしまい後半を握りつぶす」不具合（センシティブ観測付箋の対応時）と**同じ構造的リスク**を持つ。Gemini/Aurora本体のプロンプトはその教訓を踏まえ「新ブロックを作るな」という指示を追加済みだが、蒸留プロンプトは独立発注のためその対策が入っていない。テストは全て整形済みJSON文字列のスタブのみで、この失敗モードは踏んでいない。実機Aurora検証は今回未実施（スコープ外・次回対応）。
- **テスト**: `tests/test_chore_distillation.py`（新規・8件: プロンプト生成／正常系のDB書き込み・ジョブ消化／引用照合失敗／確信度不足／レーン未対応でpending維持／LLM例外でpending維持／JSON解釈失敗でpending維持／バッチ内セッション上限）。`tests/test_core_memory_integration.py`の`test_accepted_memory_candidate_is_written_to_store`を`test_memory_candidate_fusen_from_immediate_mail_is_not_written_directly`に置き換え、即時便経由では書き込まれないことを明示的に検証する内容に変更。既存`test_core_session_boundary.py`・`test_core_routing_integration.py`・`test_chore_box.py`・`test_core_full_body.py`・`test_thresholds_config.py`・`test_memory_review.py`は無改修のまま全green（回帰なし）。`tests/smoke.py`・`tests/smoke_core.py`（実機Ollama）・`tests/test_session.py`も実行し全green。
- **根拠の所在**: `core/runtime.py`（`_process_turn`）、`core/chores/distillation.py`（新規）、`tests/test_chore_distillation.py`（新規）、`tests/test_core_memory_integration.py`。

---

## 2026-07-11 Phase4着手: 宿題箱（ChoreBox）プリミティブ＋Core.end_session()

- **成果**: 設計書v2 §2.4「裏方便の駆動方式（宿題箱・機会駆動）」のうち、宿題箱そのもの（enqueue/pending/mark_done・永続化）と、Coreのセッション境界処理（`end_session`）を新規実装。`core/chores/chore_box.py`（新規）・`core/runtime.py`（`Core.__init__`に`chore_box`引数追加、`_process_turn`に断片enqueue、`end_session`新設）。これにより**Phase3から繰り越していた`session_candidate_count`のセッション境界リセット未対応**（2026-07-11「Phase2由来の積み残し2件」#3）を解消。
- **enqueueのタイミングを設計に合わせて修正した経緯**: 初版実装では`end_session`が呼ばれた時点でセッション全ターンを一括で宿題箱へ積んでいたが、advisorレビューで§2.4本文（「会話中: Coreが蒸留の宿題（細切れ断片）を宿題箱に積む」＝enqueueは会話中／消化は①セッション終了時②アイドル時③朝礼、の2つは別物）との齟齬を指摘された。この実装だと強制終了で`end_session`が走らない場合、メモリ上のSessionStateがまるごと失われ、③朝礼での回収（§2.4「宿題は消えず③で回収される」）が効かない——設計が守ろうとしているクラッシュ耐性そのものを破る実装だった。**修正**: enqueueは`_process_turn`側に移し、蒸留の断片バッファ（`_pending_fragment`）が器（`chore_fragment_turns`設定値・既定20ターン）を満たすたびに即座に宿題箱へ積む。`end_session`は端数（partial fragment）のflushと、`session_candidate_count`・`SessionState`のリセットのみを担う。
- **宿題箱の永続先**: `serina_memory.db`（長期記憶DB）とは別ファイル`data/chore_box.db`とした。根拠は§2.6の状態目録で「宿題箱」が「長期記憶DB」と別掲されていること。`data/*.db`は既存の`.gitignore`対象のため、生きているDBとは別に`tools/backup_db.py`のバックアップ対象にする必要はない（CLAUDE.mdの「破壊的操作の前にbackup_db.py」は長期記憶DBが対象）。
- **蒸留の車線振り分け（クラウド／ローカル）は今回スコープ外**: §2.4「裏方便の二車線」は断片ごとにCoreの個人情報フィルタが振り分ける設計だが、その断片単位のフィルタは未実装（現状のセンシティブ判定は発話単位のみ）。今回は`decide_brain`同様「グレーは安全側」に倣い、全断片を`lane="local"`固定で積んでいる。車線振り分け・実際の消化ロジック（蒸留LLM発注）は次のスライスで対応。
- **記憶候補審査ラインとの不整合（既知・未着手）**: `core/runtime.py`の`_process_turn`は現状、「記憶候補」付箋を即時便のうちに`review_candidate`で審査しDBへ直接書き込んでいる。§4.1の設計上のパイプラインは「即時便の粗い候補→宿題箱→裏方便で精査→関所→DB」であり、宿題箱を経由しない今の実装は設計と厳密には一致しない（advisorレビューで指摘済み）。ただし現状動いている経路を今回崩すのはスコープが違うため据え置き。記憶候補フローを宿題箱経由に作り直すことがPhase4後続スライスの候補になりうる。
- **テスト**: `tests/test_chore_box.py`（新規・enqueue順序／kind絞り込み／limit／mark_done／プロセス再起動を模した再オープンでの永続確認）、`tests/test_core_session_boundary.py`（新規・session_candidate_countリセット／SessionStateリセット／chore_box未設定時のno-op安全性／会話中の断片flush／end_session時の端数flush）。既存`core`系統合テスト（test_core_full_body/test_core_memory_integration/test_core_routing_integration）・`test_thresholds_config`・`tests/smoke.py`・`tests/test_session.py`全green（回帰なし）。
- **設定値追加**: `config/thresholds.toml`に`[chores] fragment_turns = 20`（`ThresholdsConfig.chore_fragment_turns`）。
- **根拠の所在**: `core/chores/chore_box.py`、`core/runtime.py`（`_process_turn`, `_flush_full_chore_fragments`, `_enqueue_chore_fragment`, `end_session`）、`core/config.py`、`config/thresholds.toml`、`tests/test_chore_box.py`、`tests/test_core_session_boundary.py`。

---

## 2026-07-11 Phase3残課題: センシティブ観測付箋をGemini/Auroraアダプタから実発火させる

- **成果**: 前回実装したCore側受け皿（`_apply_sensitivity_observation`）を実際に生かすため、`brains/gemini/adapter.py`の`build_prompt()`と`brains/aurora/adapter.py`の`build_extraction_prompt()`（2回目・付箋抽出発注）に`SENSITIVITY_OBSERVATION_INSTRUCTION`を追加。「話題がクラウドに不向き／実は平気だったと感じたらセンシティブ観測付箋を書け」＋`direction`/`keywords`のJSON例を提示。keywordsは「話題そのものを特定する語のみ（『話』『相談』等の一般語は選ばない）」と明記——ラチェットが部分一致のため誤爆源になるのはBrainが選ぶ根拠語の質そのもの、という点を指示文の本体にした。
- **Aurora側の非対称**: 判定はstage1（自由会話）ではなくstage2（付箋抽出の別発注）に置いた。stage1は既存設計どおり書式強制なしのまま維持し、二段方式の内部に閉じる形（条文Aに抵触しない）。
- **これで解決した範囲・していない範囲（正確な理解のために明記）**: 今回追加したのは「Brainが成功ターンで自発的にセンシティブ観測付箋を書く」経路の実配線のみ。`core/runtime.py`の`_obtain_valid_report`内、`CloudRejectionError`（クラウドの安全フィルタ拒否）時に発話全文をそのまま`tighten()`の鍵にしている箇所は**設計として意図的に据え置いたまま**（この時点ではBrain呼び出し自体が拒否されており報告書が存在しないため、付箋経由の根拠語は原理的に使えない。全文一致は再ヒット率が低い代わりに誤爆もしない高精度フロアとして維持）。つまり「元々の問題が解決した」のではなく、「拒否経路の全文フロアは意図的に残置。キーワード汎化は“成功ターン経由”という別経路が今回初めて生きた」が正確な理解。
- **検証の限界**: ユニットテストで確認できるのは「指示文がプロンプト文字列に含まれること」まで（`tests/test_gemini_adapter.py::test_build_prompt_includes_sensitivity_observation_instruction`、`tests/test_aurora_adapter.py::test_stage2_extraction_prompt_includes_sensitivity_observation_instruction`）。「Brainが実際にこの付箋を書くか」はプロンプトエンジニアリングの効き目次第であり、実機（Ollama起動・Gemini API疎通）での確認が別途必要（今回未実施）。
- **【追記】実機smoke（Aurora, 2026-07-11）で発見・修正した不具合**: 初版のプロンプト（`SENSITIVITY_OBSERVATION_INSTRUCTION`が独自にフェンス付き```json```ブロックを持つ形）をAuroraのstage2抽出で実行したところ、モデルは指示を正しく理解して住所トピックで`{"kind":"個人情報",...}`を実際に生成したが、**`fusen_list`配列の中ではなく、コードブロックの外に別JSONとして書いてしまい**、`_extract_json`の正規表現（`re.search`で最初の```json```ブロックのみ拾う）が空の`fusen_list`側を拾って後半を握りつぶす事故を確認（生の抽出テキストで実測）。原因はプロンプト内に「フェンス付きJSONブロックが2つ」ある構造がモデルに「別々に2ブロック返してよい」と誤読させたこと（advisorが事前に警告していたリスクが的中）。
  - **対処**: Gemini/Aurora両方の`SENSITIVITY_OBSERVATION_INSTRUCTION`から独自のフェンス付きブロックを除去し、「新しいJSONブロックを作らず、既存の`fusen_list`配列に要素を1つ追加せよ」と明示、例もフェンスなしインライン表記に変更。結果、プロンプト全体を通じてフェンス付き```json```ブロックは常に1つのみになった。
  - **再検証**: 修正後、Aurora実機で離婚トピックへの発話に対し`{"kind": "センシティブ観測", "version": 1, "content": {"direction": "不向き", "keywords": ["離婚"]}, "confidence": 0.9}`が単一の`fusen_list`配列内の要素として正しく出力されることを確認。天気トピック（無関係発話）では誤爆せず。住所トピックでは今回は別の独自`kind`名（モデルの気まぐれ）を出し`センシティブ観測`は出なかった——**発火率は100%ではなく、モデル依存で揺れる**。これはLLMの自由記述的な性質によるもので、構造バグ（2ブロック分裂）とは別の残存リスクとして記録しておく（プロンプトの継続チューニング余地あり）。
  - **結論**: 「Core側の受け皿＋アダプタのプロンプト指示」の経路は実機で実際に機能することを確認した。ただし発火は保証ではなく確率的（ラチェットは既存の一次防壁=`decide_brain`の内容ベース判定を補強する二次学習層に過ぎないため、これは設計上許容範囲）。
- **未対応（スコープ外）**: 緩和方向(`平気`)のマスター承認導線は引き続き未実装。付箋カタログ6種のうち「センシティブ観測」以外（心の動き・マスター観測・記憶候補・交代要請・道具使用）はプロンプト上の体系だった説明を持たない状態が変わっていない（`REPORT_FORMAT_INSTRUCTION`は`"kind": "付箋の種類名"`のプレースホルダのみ）。今回はMILESTONEが名指しした対象を狭くセンシティブ観測に限定した。
- **根拠の所在**: `brains/gemini/adapter.py`（`SENSITIVITY_OBSERVATION_INSTRUCTION`, `build_prompt`）、`brains/aurora/adapter.py`（同名定数, `build_extraction_prompt`）、`tests/test_gemini_adapter.py`・`tests/test_aurora_adapter.py`（追加テスト）、`tests/test_core_routing_integration.py`（既存、無変更のままgreen）。

---

## 2026-07-11 Phase3残課題: 「センシティブ観測」付箋のCore側受け皿を実装（未実効・アダプタ対応待ち）

- **重要な限定**: 今回実装したのは**Core側の受け皿のみ**。どのBrainアダプタ（Gemini/Aurora）もこの付箋を実際には出力しないため、**production ではまだ死んだ経路**であり、MILESTONEが名指しした本来の問題（`runtime.py:132`で発話全文を鍵にしているためラチェットが汎化しない）は**未解決のまま**。「解消」ではなく「Core側の土台を作った」段階と正確に理解すること。
- **成果**: 付箋カタログ6種のうち唯一未配線だった「センシティブ観測」（設計書v2 §2.2, §3.3.1）のCore側消費ロジックを実装。`core/runtime.py`の`_process_turn`が`accepted_fusen`を走査し、`kind=="センシティブ観測"`の付箋を`_apply_sensitivity_observation`へ渡す。`content={"direction": "不向き"|"平気", "keywords": [...]}`。`direction=="不向き"`のときのみ`keywords`各語を`routing_rules.tighten()`（逆止弁の厳格化方向・自動反映）。`direction=="平気"`（緩和方向）はマスター承認が必須（§3.3.1）のため自動適用しない——ただしaccepted_fusenとして受理済み記録は残るため無言破棄ではない（承認導線自体は今回スコープ外）。
- **advisorレビューで却下した案**: Core側で正規表現による日本語根拠語の自動抽出は**採用しなかった**。理由: (1) 設計は根拠語の出所をBrain（LLM）と明記しており(§2.2「誰が書く: Brain」)、Core側抽出は設計が求めていない。(2) トークナイザ無しの正規表現抽出は「話題」等の常用語まで拾い、`is_sensitive`が部分一致である以上、日常発話の大半が誤ってAurora行き(93秒/ターン)になる。(3) ラチェットは厳格化方向のみ自動・緩和はマスター承認必須の片方向のため、誤爆した鍵は張り付いて手動でしか剥がせない。(4) ラチェットは二次防壁に過ぎず（一次防壁=`decide_brain`の内容ベース判定、2026-07-11既出）、再現率より適合率を優先すべき局面だった。
- **`core/runtime.py:132`（CloudRejectionError時に発話全文をそのまま鍵にしている箇所）は意図的に未変更**。この時点ではBrain呼び出し自体が拒否されており報告書が存在しないため、根拠語をBrainに書かせる通常経路（今回実装した付箋経路）が使えない。全文一致は再ヒット率が低い代わりに誤爆もしない「意図的な高精度フロア」として温存し、汎化は通常成功時にBrainが自発的に書くセンシティブ観測付箋に委ねる設計とした。コード上にもこの理由をコメントで明記済み。
- **既知の未対応（スコープ外・将来対応）**: 現時点でどのBrainアダプタ（Gemini/Aurora）もセンシティブ観測付箋を実際には出力しない。プロンプト側の対応（Brainに根拠語つきで書かせる指示）は今回のCore側配線とは別作業として残る。緩和方向(`平気`)のマスター承認導線（承認キュー等）も未実装。
- **テスト**: `tests/test_core_routing_integration.py`に`test_sensitivity_observation_fusen_tightens_by_keyword_not_whole_utterance`（根拠語が鍵になり別発話でも再ヒットし、無関係発話は巻き込まないことを検証）・`test_sensitivity_observation_fusen_loosen_direction_is_not_auto_applied`（緩和方向が自動適用されないことを検証）を追加。既存`test_cloud_rejection_falls_back_to_aurora_same_turn_and_tightens_rule`は無変更のまま green（132を変えていないため）。全165件green。
- **根拠の所在**: `core/runtime.py`（`_process_turn`, `_apply_sensitivity_observation`, `_obtain_valid_report`のコメント）、`tests/test_core_routing_integration.py`。

## 2026-07-11 Phase2由来の積み残し2件を解消（tighten誤爆・スキーマ整合）

- **#1 tightenの誤爆修正**: `Core._obtain_valid_report`が「呼び出し例外」「契約書式違反」を問わずcloud失敗全てで`routing_rules.tighten()`していた問題（2026-07-10繰り越し）を解消。型付き例外`CloudRejectionError`（`brains/contract/schema.py`）を新設し、Gemini通訳が応答JSONの`promptFeedback.blockReason`（値問わず存在すれば拒否）／候補ゼロ／`finishReason`が「話題そのものが安全/ポリシー上拒否された」ことを示す種別（`SAFETY`/`PROHIBITED_CONTENT`/`SPII`/`BLOCKLIST`。Gemini API公式リファレンスで裏取り済み。**`SAFETY`限定だと個人情報ブロックの`SPII`——このシステムの防御対象そのもの——を取りこぼすため列挙**。同じ列挙候補の`LANGUAGE`（対応言語外という能力的限界）・`RECITATION`（引用/著作権由来の停止）は話題の機微性と無関係なため意図的に除外——ラチェットは「話題の機微性」だけを学習する設計のため）を検知したときのみ送出するよう変更（`brains/gemini/adapter.py`の`_extract_reply_text`に純関数として分離）。`_obtain_valid_report`は`CloudRejectionError`のときだけtighten、通信エラー（`requests.exceptions.RequestException`等、意図的にラップせず伝播）と契約書式違反はフェイルオーバーのみでラチェットは研がない（設計書v2 §3.5どおり）。
  - **着手前の前提確認**: 一次防壁である`decide_brain`（`core/routing/decision.py`）の§3.2②「内容ベースでセンシティブ→ローカル確定」が実装済みであることを確認済み。ラチェット（tighten）は二次的な学習層に過ぎないため、tighten対象を狭めても一次防壁が別に効いておりプライバシー的な穴は開かない。
  - **既知の未解決（Phase3本体へ繰り越し）**: `tighten(master_utterance)`は鍵として発話全文をそのまま`_sensitive_keywords`に加えるため、`is_sensitive`の部分一致判定上は「一字一句同じ発話が再度来たときしかヒットしない」に等しく、**現状では「次回から同種の話題は最初からローカルへ」（§3.5の意図）を実質達成できていない**。根拠語（トピックの核となる単語）への圧縮は設計上「センシティブ観測」付箋（Brainが根拠語つきで報告）経由で行う想定であり、これはPhase3ルーティング本体の仕事のためスコープ外とした。tighten発火のON/OFF区別（今回の修正対象）と、鍵の粒度（根拠語化・未着手）は別問題である点に注意。
  - **テスト**: `tests/test_core_routing_integration.py`に`test_communication_error_falls_back_but_does_not_tighten_rule`・`test_contract_format_violation_does_not_tighten_rule`を追加。既存の`test_cloud_rejection_...`は`raise_cls=CloudRejectionError`を明示するよう更新（旧実装は素の`RuntimeError`でも誤ってtightenしていたため、それを再現できないテストに直した）。`tests/test_gemini_adapter.py`に`_extract_reply_text`の缶詰データ検証（`SAFETY`だけでなく`SPII`も含む）を追加。
- **#2 スキーマ整合**: `core/memory/store.py`の`_ensure_schema()`のCREATE TABLE文が実DB物理スキーマ（`pinned`/`source`/`parent_id`/`metadata`列・`FOREIGN KEY(parent_id)`）より縮小していた問題を解消。実DBの現行スキーマ（`tools/migrate_memory_schema.py`適用後）と一字一句一致するよう書き換え。`IF NOT EXISTS`のため実DBへの影響はないが、テスト用新規DB作成時に物理スキーマの乖離が起きなくなった。`tools/migrate_memory_schema.py`は一回性のALTER移行ツールであり役割が異なるため単一ソース化はせず現状維持。
- **#3 `session_candidate_count`のセッション境界リセット**: 現状1 Core＝1セッションで無害なため今回は着手せず、MILESTONEどおりPhase4に残す（advisor助言: 先食いすると Phase4 の面を潰すだけ）。
- **根拠の所在**: `brains/contract/schema.py`（`CloudRejectionError`）、`brains/gemini/adapter.py`（`_extract_reply_text`, `CONTENT_BLOCK_FINISH_REASONS`）、`core/runtime.py`（`_obtain_valid_report`）、`core/memory/store.py`（`_ensure_schema`）、`tests/test_core_routing_integration.py`・`tests/test_gemini_adapter.py`・`tests/test_memory_store.py`（全GREEN確認済み）。

---

## 2026-07-10 Phase 3（ルーティング）進行中の繰り越し1件

- **内容**: `Core._obtain_valid_report`は、cloud Brainの失敗が「呼び出し例外（通信エラー・弾切れ含む）」でも「契約書式違反」でも、区別なく`routing_rules.tighten()`を呼ぶ。設計書v2 §3.5は本来「クラウドの拒否（安全フィルタ）」だけを振り分けルール学習の対象とし、「通信エラー・弾切れ」は同ターン代打のみでラチェットは研がない、と別扱いにしている。
- **現状維持の理由**: 区別するには通訳（adapter）が型付き例外（例: `CloudRejectionError` vs 単純な通信エラー）を返す必要があるが、現在は素の`Exception`のみ。誤ってtightenする方向は「グレーは安全側」（設計書v2 第0章 確定判断#2）と同じ向きのズレであり、実害は限定的（プライバシーの門が余分に閉まるだけで、開く方向には動かない）。
- **今後の対応**: Gemini/Aurora通訳に型付き例外を持たせ、`_obtain_valid_report`でクラウド拒否のみtighten対象に絞る。Phase3の後続タスクまたはPhase5（道具箱・仕上げ）で対応。
- **根拠の所在**: `core/runtime.py`の`_obtain_valid_report`。

## 2026-07-10 Phase 2（記憶接続）完了

- **成果**: 想起（関連度×新しさ×重要度の**積**＋保護等級A/Sのキーワードトリガー想起）と記憶候補の審査ライン（関所④引用照合・重複チェック・1セッション上限）を接続。保護3原則（透明性・可逆性・同一性）を実装。Core本体に配線し、毎ターン想起→pack反映、記憶候補付箋→審査→DB書き込みが動作。既存866件（正典9件含む）へスキーマ移行済み。
- **設計適合の是正2件**（Advisorレビューで発見・修正）:
  1. 想起スコアが当初「加重和」で実装されていたのを、§4.4が明記する「かけ算」（`relevance * recency * importance`、素の積）に修正。積は「無関係な記憶は重要度が高くても沈む」ANDゲート特性を持つ、設計が意図した挙動。
  2. `recall()`が想起のたび鮮度回復（`last_accessed`更新・`access_count`加算, §4.1）していなかったのを追加。積にrecencyを使う以上、回復がないと継承866件が新規記憶に対して指数的に沈み二度と浮上しなくなるため。`nearest_relevance()`（重複チェック専用）は想起ではないため回復させない。
- **実DB移行**: `tools/backup_db.py`でバックアップ後、`tools/migrate_memory_schema.py`を適用。正典9件（`pinned=1`）→保護等級S、非正典857件→保護等級B（全件、マスター判断）、全866件が機微等級2で開始。`memory_vec`充足866/866・1024次元（bge-m3と一致）を確認、ダミーベクトルによるKNN機構疎通も確認済み。
- **繰り越し事項（Phase2完了時点）**:
  1. ~~実bge-m3 recall smoke未実施~~ → **2026-07-10中に解消**。マスターがOllamaを起動、`tests/smoke_bge_m3_recall.py`で実DB866件への意味的想起を確認（「マスターとの約束」→誓いの記憶、「セリナの性格について」→名前・性格の記憶が適切にヒット）。
  2. `core/memory/store.py`の`_ensure_schema()`は実DBの物理スキーマ（pinned・source・parent_id・metadata列を含む）より縮小したCREATE TABLE文を持つ。実DBに対しては`IF NOT EXISTS`によりno-opだが、Phase3で実DB以外（テスト用新規DB等）を本番相当に使う前にスキーマを整合させること。
  3. `Core.session_candidate_count`（1セッションあたりの記憶化件数上限のカウンタ）はセッション境界でのリセット処理を持たない。現状1 Core＝1セッションのため無害だが、セッション回転を扱うPhase4で対応が必要。
- **根拠の所在**: `core/memory/`（embedder.py, store.py, protection.py）, `core/intake/memory_review.py`, `core/context/pack.py`, `core/runtime.py`, `tools/migrate_memory_schema.py`, 対応する`tests/test_*.py`一式（16ファイル全GREEN）。

## 2026-07-10 既存記憶866件の保護等級・機微等級 初期移行を実行

- **背景**: 設計書v2 §4.6-4「正典由来の固定9件は保護等級S、他は種類に応じてA/B」の「他857件」の振り分け基準が、既存DBの`type`列（event/knowledge/relationship/fact/diary）と新設計の記憶種類（出来事・知識・約束・情緒）で対応しないため、勝手に閾値やマッピングを決めず作業を止めてマスターに確認した。
- **決定**: 正典9件（`pinned=1`で機械的に特定）→ 保護等級S。**非正典857件は全件、保護等級Bで開始**（安全側）。個別のA昇格（約束・重要な出来事の再評価）は今後の会話・裏方便（Phase4）で行う。
- **実行結果**（`tools/backup_db.py`でバックアップ後、`tools/migrate_memory_schema.py`を実DB適用）: 総866件（857+9）。`protection_grade`: S=9, B=857。`sensitivity_grade`: 全866件が2（安全側デフォルト、§4.6-2どおり）。
- **根拠の所在**: `tools/migrate_memory_schema.py`（`migrate()`, `assign_initial_protection_grades()`）、`tests/test_memory_schema_migration.py`。

## 2026-07-10 Phase 1（歩く骨格）完了

- **成果**: Core状態（感情8軸×情動/気分二層・関係・セッション）＋契約書式（`brains/contract/schema.py`）＋Gemini通訳（`brains/gemini/adapter.py`）で、記憶接続なしの最小会話が成立。実機Gemini（`gemini-3.1-flash-lite`）で疎通確認済み（契約書式通りの応答を確認）。
- **完了条件（設計書v2 §5.2）を4試験とも充足**: Core全身検査(`test_core_full_body.py`, stub Brain)／契約試験(`test_contract.py`, `test_gemini_adapter.py`)／実機スモーク(`smoke_gemini.py`)／憲法テスト(`test_constitution.py`, 条文A=Brainステートレス性・条文B=通訳がpackを改変しない、を検証)。
- **Phase1のスコープ境界**: 長期記憶の想起・記憶候補審査ライン・引用照合・Aurora通訳・ルーティング・裏方便はPhase2以降。機微等級のクラウド混入チェックは機微等級導入(Phase2)後に憲法テストへ追加する。
- **数値ツマミ**: 付箋種類ごとの確信度足切り閾値・気分層の急変防止弁幅は`config/thresholds.toml`に外出し（ハードコード禁止 §5.5-3）。
- **既知の未対応（設計通りの先送り）**: `Core.turn()`はBrain呼び出し失敗時に例外を握りつぶさずクラッシュする。最終防衛線（Aurora代打）はPhase3で対応するため、Phase1では未対応のままにする。
- **根拠の所在**: `core/`, `brains/`配下の実装一式、`tests/test_contract.py` `test_thresholds_config.py` `test_emotion_state.py` `test_relationship_session_state.py` `test_intake_gate.py` `test_context_pack.py` `test_gemini_adapter.py` `test_core_full_body.py` `test_env_loader.py` `test_constitution.py`、実機確認 `tests/smoke_gemini.py`。

## 2026-07-10 Phase 1実装: 新実装は `core/` に仮払い（暫定処置）

- **背景**: 設計書v2 §5.1 の目標ディレクトリ構成（`core/state/`・`core/context/`・`core/intake/` 等）は、既存の旧実装（`core/config.py`・`core/context.py`・`core/session.py`・`core/runtime.py` 等、フラット配置）と同一ディレクトリ名で衝突する。特に `core/context.py`（旧・ファイル）と `core/context/`（新・ディレクトリ）は同名衝突し、Python の import 解決が壊れる。設計書は「旧コードは Phase 6 まで削除しない・参照もしない」とのみ定め、この物理衝突には触れていなかったため、マスターに確認して決定。
- **決定**: Phase 1〜5 の新実装は `core/`（および将来必要なら `skills_v2/` 等）に仕払える。旧コードとは物理的に一切衝突しない。Phase 6 の大掃除（設計書v2 §5.3）で旧 `core/` 等を削除したのち、`core/` → `core/` にリネームして正式名に揃える。
- **影響**: 設計書v2 §5.1 のディレクトリ構成表そのものは変更しない（最終形は変わらない）。Phase 6 の作業に「`core/` → `core/` リネーム」が1ステップ追加される。
- **根拠の所在**: 本エントリのみ（`設計書v2.md` は書き換えない。最終形の記述と矛盾しないため）。

## 2026-07-10 アーキテクチャ全面刷新 — Core/Brain/Skill 三層設計を確定（設計書v2）

- **背景**: LLM を交換可能な部品とし、人格・価値観・長期記憶・状態管理を LLM から独立させる長期運用アーキテクチャへ全面転換。リファクタリングではなく再設計（更地方針＝旧6層との対応表を作らず、旧概念は積極的に捨てる）。設計は章ごとにマスター承認を得て確定。
- **三層**: Core（セリナ本体・LLMなし・決定論。人格資産/感情/記憶/ルーティング/状態更新ルールを保持）／Brain（交換可能な知能。Gemini 3.1 Flash Lite=日常500回/日、Gemini 3.5 Flash=ここぞ20回/日、Aurora=センシティブ・個人情報・裏方・代打）／Skill（道具箱。Brain が自律的に道具使い、実行は Core 側）。
- **Brain→Core 返却**: 「返答＋付箋束」方式＋二便制（即時便=同一呼び出しに同梱／裏方便=宿題箱による機会駆動）。付箋は提案であり、Core の関所（書式検査・確信度足切り・急変防止弁・引用照合）を通る。読めない付箋は保管庫へ。
- **感情**: プルチック8軸×二層（情動=1ターンで振り切れ可・制限なし／気分=遅く動き急変防止弁つき・基準値は人格資産が定義）。感情の状態決定は Core、表現は Brain。
- **プライバシー**: センシティブ・個人情報はクラウドに送らない（判定は Core のルール・グレーはローカルへ）。機微等級0/1/2＋化粧版＋組み合わせ禁止表＋短期層のローカル担当ターン置換。振り分けルールの学習は逆止弁（厳格化=自動・緩和=マスター承認）。
- **記憶**: 正典保護5原則を保護3原則（透明性・可逆性・同一性）へ転換。保護等級S/A/B。原文無傷主義を廃止し「戻せるなら触っていい」で成長を解放。等級Bは忘却ライン（完全自動・マスター裁定なし）で物理削除可。低確信度候補も自動棄却（人為的な記憶操作の入口を作らない）。
- **昇格/降格**: Brain の必須自己評価欄（毎ターン「手に余るか」）＋夜間放出のみ。Core が空気を読む判定はしない。
- **自己レビューで追加した9点**: Aurora用通訳の二段方式（書式リスク対策）／逆止弁／短期層スクラブ／セッション終了3トリガー（心拍検知含む）／会話優先＋GPU見送り判定／無料枠=学習利用の明文化（機微0のみ許容）／着脱式計器盤 等。
- **実装体制**: 実装は Sonnet 級が設計書v2のみを頼りに Phase 0〜6 で実施（申し送りは §5.5）。Gemini API キーは Phase 0 の `.env` 作成時にマスターが記入。
- **根拠の所在**: `docs/設計書v2.md`（全5章＋確定判断9件の記録）。旧 `設計書.md`・`設計憲章.md`・`specs/` は Phase 6 で `docs/archive/` へ。憲章v2は本書ベースで作り直し。

## 2026-07-08 三段階レビュー体制へ移行＋設計憲章（測定器）制定

- **背景**: LLM／RAG／Embedding／Vector DB／Framework／Agent 構成は今後すべて差し替わる前提。一方で Serina の**設計思想だけは長期維持**したい。そこで思想の逸脱（設計ドリフト）を監査する専任レビュアーを導入する。
- **設計憲章（測定器）の制定**: `docs/設計憲章.md` を新設。`設計書.md` §1〜§5 を「読む文章」から「判定に使う定規」へ変換し、各原則に**違反の兆候**を付与して条文化（A: 責務配置／B: 依存・結合／C: Voice 憲法／D: 外部サービス／E: Memory）。原典は `設計書.md`（正）、憲章は判定基準（従）。**条文の追加・変更・削除はマスターのみ**。
- **番人のドリフト防止（設計上の核）**: 「設計思想」という抽象を判定させると番人自身がブレるため、**凍結条文のみを基準**にする。層名は Serina 実体6層（Core/Prompt/Memory/Skills/Connectors/External）に固定し、一般語彙（Reasoning/Retrieval/Working Memory 等）での監査を禁止。9観点は重複が多いため**3観点（責務配置／依存・結合／憲章違反）に集約**。禁則5条で「憲章の内容は批判しない・定規を増やさない・最新技術で判定しない・コード/ライブラリを提案しない」を明文化（番人は憲兵であって立法者ではない）。
- **architecture-reviewer 新設**: `.claude/agents/architecture-reviewer.md`（読み取り専用・sonnet）。Serina 依存のため**プロジェクト側**に配置（既存の汎用4エージェントはグローバル）。**構造に触れる変更のときのみ発火**（新レイヤー/層をまたぐ依存/Prompt 分割/新 Agent・External/Memory スキーマ変更）。それ以外で呼ばれたら `BLOCKED_SCOPE` で差し戻す。
- **レビュー体制を二段階→三段階へ**: 順序 **architecture-reviewer（設計思想）→ spec-reviewer（仕様適合）→ quality-reviewer（コード品質）**。3者が別々のものさし（憲章／要求／コード）を持つため衝突しない。quality と重なりがちな「結合度・複雑性」は**構造レベル＝第0段／実装レベル＝第2段**で高度を分離。
- **移行状態の扱い**: `persona.md` に Core Values と Voice Style が同居する現状は「明示済みの移行状態」として、配線切替（loader 分離）完了までは A-P・C 群を **WARNING** 扱い（FAIL にしない）。切替完了時に憲章の該当メモを削除。
- **根拠の所在**: `docs/設計憲章.md`（測定器本体・3観点・発火条件・禁則）、`.claude/agents/architecture-reviewer.md`（レビュアー定義）。

## 2026-07-05 人格アーキテクチャ整理（B: 資料・人格分割）＋ C1（判断とVoiceの配線）実装

- **背景**: 前項（C方式退避）の設計転換を、資料と実装へ落とし込む。Serina を口調再現ではなく「世界を認識→記憶→信念→価値観→判断→行動→根拠→声」の人格アーキテクチャとして整理する。核の指針＝新機能追加時は常に「これはどの層の責務か」を問う。
- **憲法条文（確定・全資料へ埋め込み済み）**: 「Voice は Decision および Action Result を入力として受け取り、その意味を変えずに自然言語として表現する。Voice は Decision や Action Result を書き換えてはならない（言い換え可・自然な口調可・意味の改変は不可）」。
- **B: 資料修正と人格分割（完了）**:
  - `prompt/persona.md` を役割別3ファイルへ物理分割。`prompt/core_values.md`（価値観・判断原則＝判断へ供給）／`prompt/voice_style.md`（口調＝発話へ供給・憲法条文を掲示）／`prompt/boundary.md`（境界ルール）。原文は保持（削らず再配置）。
  - `specs/基本設計書_判断とVoice分離.md`: 北極星図を追加／「人格は③(声)だけに乗る」表現を撤回し「口調は③・価値観は①(判断)」へ訂正／Aurora を判定に使う残骸（RP特化モデルは判定に不適）を除去／Decision入力とVoice入力を明記／Evidence（status/evidence/confidence）構造を追加。
  - `設計書.md`: Prompt層を Core Values（→判断）と Voice Style（→発話）に分離記述。§5に憲法条文。
  - `specs/基本設計書_ExternalServices接続.md`: 「Aurora が結果を解釈する」→「Core/Decision が評価し、Voice が表現する」へ訂正。
  - **配線切替は未実施（意図的なフェーズ境界）**: `prompt/loader.py` は現状 persona.md を読む。3ファイルへの配線切替と persona.md 退役は後続。それまで persona.md が実効ソース（一時的な内容重複は境界による意図的なもの）。
- **C1: 判断とVoiceの配線（実装完了・TDD）**:
  - スコープを「配線だけ」に限定。`Decision → Action → Evidence → Voice` の壁を、既存モデル据え置き・検索非復帰で通す。目的は逆流が止まる保証をテストで固定すること。
  - 実装: `core/decision.py`（`DecisionResult`＝不変(frozen)の判断結果／`Evidence`＝行動結果の根拠構造／`decide()`＝既存ルール(router)を判断層へ昇格した決定論選択／`build_evidence()`）。`skills/base.py` の `SkillContext` に `decision`/`evidence` を読み取り専用で追加。`core/runtime.py` の turn を配線し戻り値に `decision`/`evidence` を追加。
  - **壁の性質**: 判断は user_input（＋将来 tool 可用性）のみに依存し、気分・人格・口調を入力に持たない。`DecisionResult` は不変で Voice が書き換え不可。自己申告マーカーはロールバックで物理的に不在（廃止確定）。`DecisionResult.action`/`query` は検索復帰の受け口として温存。
  - **検証**: `tests/test_decision_voice.py` 5件（判断の構造化／判断の不変性／Evidenceが根拠を運ぶ／同一入力で気分を変えても判断が不変＝逆流回帰／turnがDecision・EvidenceをVoiceへ通す）全合格。既存回帰（router/reflection/decay_emotion/growth/metrics）無傷。Ollama起動が要る smoke 系は本セッション未実行（C1は Ollama 接続経路を変更しないため影響なし）。
- **C2 方針（未着手・方向のみ確定）**: 判断エンジンを誰に任せるか（Gemma／Qwen／CPU常駐小型／Rule Engine）の比較は逆流が止まった後に行う。8GB VRAM 制約下の現時点方針は決定論最大化（ルール約8割・推論モデル約2割）＝判断を LLM に丸投げしない。LLM は曖昧な依頼の判定にのみ用いる。
- **根拠の所在**: `specs/基本設計書_判断とVoice分離.md`（北極星図・責務分離・Evidence・Voiceの柵・ステータス）、`prompt/core_values.md`・`voice_style.md`・`boundary.md`。

## 2026-07-05 WEB検索（C方式）を退避しロールバック — 判断とVoiceの分離へ設計転換

- **症状**: WEB検索 Phase 1a 追加後、事実質問を情緒・哲学へ変換／本来検索すべき場面で検索せず曖昧に済ませる／絵文字多用・媚び。要は**セリナの人格（声）が変質**した。
- **根本原因**: C方式（Aurora自身が**発話の一息の中で**検索要否を判断＝`〔検索〕`マーカー自己申告）が、**設計書§5「Coreは決めるだけ／逆流禁止」**および**決定3「二役＝声(Aurora)と判断(理性エンジン)の分離」**に反していた。判断と口調が同一LLM呼び出し・同一人格プロンプトで同時生成されるため、口調の傾き（媚びモード）が判断（検索スキップ）に漏れる＝**Voice→Decisionの逆流**。C方式は8GB VRAMで別判定モデルの毎ターン載せ替えを避けるための最適化だったが、思想を犠牲にしていた。
- **判断**: WEB検索実装を**本線から退避**（`shelf/web-search-c-method` ブランチに全保全＝完全復元可）。会話本体を `ChatSkill` へ戻し、WEB検索前の素直な状態へ**ロールバック**。削除＝`skills/web_search.py`・`connectors/search.py`・`connectors/external.py`・`tests/test_web_search.py`・`specs/基本設計書_WEB検索.md`・`infra/searxng/`・config検索項目・Serina.batのSearXNG起動。**器官（SearXNGConnector）とinfraは無実（Connectorは繋ぐだけ）だが、再設計時に再利用するため棚に温存**。ドキュメント掃除・Perception設計・ストリーミング・GUIは維持。応急処置（絵文字除去等）も全撤回。DB無変更。
- **前進方針**: 1ターンを **①判断→②行動→③声** の二段に割り、Serinaらしさは③だけに乗せる（`Memory→Core→Decision→Action→Voice`・逆流禁止）。**判断は Aurora（RP＝声に特化＝判定に不適）ではなく理性の器官へ**。決定論規則を土台に敷き、モデル判断は機微だけに使う（モデル載せ替え耐性の最大化）。8GBでの判断エンジンの器の置き場（CPU常駐小型／Gemma載せ替え／決定論最大化）は**未決**。設計は `specs/基本設計書_判断とVoice分離.md`（初稿の「人格を剥いだAurora判定役」は要改訂）。
- **教訓**: 別セッションでの機能追加が原典（設計書）を勘定に入れずに進むと、性能最適化の名目で設計思想が裏返る。**最優先は設計思想**であり、遅くなるとしてもその上で解を探す。

## 2026-07-05 WEB検索（自律検索）Phase 1a 実装完了＋ドキュメント整理

- **設計確定**: `specs/基本設計書_WEB検索.md`。External Services型に乗せ、器官（Connector）は将来のPerceptionプロアクティブ経路でも再利用。発動は2経路——Aurora自己判断マーカー`〔検索: kw〕`＋鮮度オーバーライド（決定論backstop）。**C方式採用の理由**=メイン機8GB VRAMで別判定モデルを毎ターン呼ぶとAurora⇄判定モデルのスワップで激遅になるため、Aurora自身に判定させスワップ回避。深さはスニペットのみ（精読=Fetchは1b）。
- **検索基盤**: SearXNG（自前ホスト・メタ検索）。他人（検索API業者）を経由しない思想。`infra/searxng/` にcompose＋settings.yml（**JSON出力有効化＝最大の罠**）＋手順書。localhost:8888、URLは環境変数 `SERINA_SEARXNG_URL` で上書き可。
- **実装（1a）**: `connectors/external.py`（ServiceResult契約を実体化）／`connectors/search.py`（SearXNGConnector・薄い）／`skills/web_search.py`（WebSearchSkill＝ChatSkillを置換する会話本体。**マーカー先頭傍受**でストリーミング表示を殺さず自律検索を両立）／config・create_core配線。ChatSkillクラスはtest_stream用に残置。
- **構造上の要点**: マーカー検出は「生成後」に判明するため、検索能力Skillはcan_handleで事前ルートされる脇役でなく会話本体（catch-all）にする。skill名は計器盤互換のため"chat"を踏襲。誤検知は**鮮度語＋質問サインの両立時のみ強制検索**（取りこぼし優先）。
- **テスト**: `test_web_search.py` 5件（マーカー検索＋非表示／通常会話は無検索／鮮度オーバーライド＋news／雑談誤検知なし／検索失敗時の正直フォールバック）全合格。既存回帰（router/session/stream）無傷。
- **ドキュメント整理**: `docs/archive/`（完了指示書8ファイル・約1,800行）を削除（git履歴に原本保全）。`research/`の3蒸留メモを`research/研究蒸留まとめ.md`に統合。作業ツリーのdocsを実質半減。
- **未了**: Phase 1b（精読=Fetch本文取得・trafilatura）／SearXNG実機での対話E2E（Docker起動後）／プロアクティブ経路（Perception層・Cognitionと後日）。

## 2026-07-04 運用フェーズ第1弾: ストリーミング表示・ES接続設計・GUI基本形

- **ストリーミング表示（実装完了）**: `on_token` コールバックを Core.turn→SkillContext→ChatSkill→OllamaChatConnector に1本通す方式。省略時は従来の一括応答（既存呼び出し元は全て無変更・後方互換）。REPL は「セリナ> 」先出し＋逐次表示、非対応Skill（蒸留等）は一括表示にフォールバック。応答90秒が「流れる90秒」に。二段階レビュー✅（品質指摘の `response.close()` 保証を反映）。`tests/test_stream.py` 新設。
- **External Services 接続設計（設計のみ・実装なし）**: `specs/基本設計書_ExternalServices接続.md`。ServiceResult/ExternalConnector の共通契約を固定し、新サービスは Connector＋Skill＋登録＋テストのチェックリストで1個数時間。外部結果は memories に直接書かず蒸留に任せる（正典保護の書き込み経路を増やさない）。具体サービスが決まった時点で実装。
- **GUI基本形（実装完了・マスター決定の基本形どおり）**: Claude風チャットUI（FastAPI + 素のSPA、127.0.0.1:8765、`Serina.bat` でショートカット起動）。チャット（NDJSONストリーミング逐次描画）／会話履歴（過去セッション読み取り専用）／アルバム（日記閲覧のみ）。GUI は Core の薄い皮で REPL と同じ組立・蒸留フロー（DistillWorker 等は `app/workers.py` へ抽出し共用）。Store 追加は読み取り専用2メソッドのみ。不満が溜まったらチューニング（鍵付き日記演出・WebSocket・/rate GUI化は将来）。仕様は `specs/基本設計書_GUI.md`。
- **並行処理の知見**: ストリーミング応答のジェネレータ内でロックを持つと、クライアント切断時の `GeneratorExit` 配達がサーバ実装依存で**デッドロックし得る**（実機再現済み）。ターン本体はロックを持つ製造スレッドで実行し、HTTP側はキューを読むだけの構造に修正 → 切断→即再送でも履歴保存・ロック解放を保証（実機で解消確認）。二段階レビュー✅（spec DONE / quality の要修正1件を上記で解消）。
- 依存追加: fastapi / uvicorn（requirements.txt 記録済み）。

## 2026-07-04 スライス2 実装完了 —— 本線ロードマップ全完了

- **時刻の常時注入**: systemに現在時刻・曜日を毎ターン注入（`inject_time`）。セリナが「いま」を知る。実機プローブで深夜検知を確認（「まだ夜中ですが…」）。
- **ルーティング本格化**: 優先順マッチ（ChatSkill=キャッチオール末尾）。**DistillIntentSkill** が「今日の話を整理して」を検知し、REPLが同期蒸留（3a設計の経路Aインテント接続が完成）。LLMベースのルーターは将来のSkill数増加時に再検討。
- **検索の規律（研究資料③・保留分）の最終判断**: 毎ターン検索を正式維持（ローカルbge-m3で安価・「知らないと言う前に検索」は常時検索で構造的に充足）。
- **StockAI等の受け皿**: 新Skillは「can_handle→外部データ取得（Connector経由）→文脈へ注入→Aurora発話」のパターンで追加する（External Service原則どおりSerina内部に専門処理を実装しない）。
- **スライス4品質レビュー反映**: 一度きり演出（差分想起・独り言）の消費を応答成功後に確定（LLM失敗時の空撃ち防止・回帰テスト追加）／belief統合の類似候補幅を拡大（type無差別KNN対策）／export_growthのclose作法。監視事項として記録: 月次固結によるbelief文面の痩せ（export_growthの新旧比較で運用監視）・記憶数万件規模でのトリガー走査コスト。
- テストスイートは7本（session/reflection/decay_emotion/consolidation/growth/metrics/router）全合格。

## 2026-07-04 スライス4（記憶の成長）実装完了＋期限圧縮体制

- **期限圧縮体制（マスター決定）**: Fable提供が2026-07-07までのため、完成優先・チューニング後回し。スライス4は**指示書を省略して基本設計書から直接実装**し、レビューは二段階→**統合レビュー（spec→quality、Sonnet実行）に圧縮**。大量バッチのサブエージェントはSonnet固定。
- **4a 再固結**: Consolidator（週次・生活日7日トリガー／二役継承／belief upsert=「増える」でなく「濃くなる」／variants退避／正典対象外／月次上位固結4回ごと／consolidation_log監査線）。経路B蒸留の後段に同居。テスト7件。
- **4b 行動化**: 差分想起（p_growth=0.25・used再注入なし）／不在時間の独り言（捏造ガード・使用後必ずクリア・経路B起動のみ）／maintenance --expire-threads。テスト3件。
- **4c 計器盤**: metrics（master_rating=/rate・callback_rate・followup_hit＝理性エンジン採点）／turn_retrievals原簿／二層人格（self_image=①憲法直後・矛盾時は憲法優先）／tools/export_growth.py ドリフト監査レポート。テスト4件。
- 全テストスイート（session/reflection/decay_emotion/consolidation/growth/metrics）合格。DBは11テーブル体制へ。
- **会話スタイル契約（同日・別件）**: 詩化・台本形式・作話への対策として system 末尾にスタイル契約＋few-shot 3例を注入。Aurora実プローブ3周で検証（詩→台本→作話の順に解消、実記憶は自信を持って回答）。対話 temperature 0.65／日記 0.8 に分離。

## 2026-07-04 想起チューニング（実プレイ初日フィードバック反映）

- **症状**: 応答の文脈無視／レアトピック（「ローカル」）への曖昧回答。診断の結果、捏造は無し。原因は ①num_ctx 4096超過による切り捨て ②正解記憶がベクトル検索でノイズに埋没（bge-m3は無関係でも0.7前後）＋旧記憶にtrigger_keywords無し ③記憶ブロックが「参考」ラベルで実記憶として扱われない。
- **対策1**: `OLLAMA_FLASH_ATTENTION=1`＋`OLLAMA_KV_CACHE_TYPE=q8_0`（setx＋起動バッチ）でKV半減 → **num_ctx 8192 を VRAM 7.6GB のまま実現**（実測GPU82%）。16k以上はこのPCではCPUオフロード増のため非推奨。将来の外部データ連携（StockAI等）はctx拡大でなくSkill層で要約してから注入する方針。
- **対策2**: 想起の規律プロンプト（研究資料§3の前倒し）。記憶ブロック＝「実際に覚えている過去」と明示、無い時は創作せず**手がかりを聞き返す**（即「思い出せない」に倒れる懸念へのマスター指摘を受け緩和）。自発想起に関連度フロア0.6追加。
- **対策3**: 旧記憶851件へ trigger_keywords 一括付与。生成は**Gemmaでなく Claude（Fable 639件＋Sonnet 212件）**——一回きりのキュレーションは品質優先でクラウド、常時運用の蒸留は従来どおりローカルGemma。適用809件（空キーワード42件は意図的）、タグ付き総数821件。実証: 「ローカル」→2件・「Monday」→5件が正しくマウント、誤発火ゼロ、eval_recall 全5件1位維持。
- **運用知見**: 大量バッチのサブエージェントは `model: sonnet` を明示（Fable継承だとクォータ枯渇。2026-07-04にセッション上限で実体験）。

- **実装完了**: フロア付き乗算（α=0.85/γ=0.15/κ=0.35、β独立項廃止）／type→半減期マップ＋metadata個別上書き（30日既定、fact/knowledge/relationship等=180日）／pinned特例撤去（減衰免除のみ特権として残置）／失効ファクトの想起除外／トリガー想起＋ホット層昇格の静的マウント（800字予算・トリガー優先）／感情重力5段パイプライン（τ=3日・±0.15・照れ隠しspike+0.25@0.95）／感情ブロック注入（①直後・暫定）。テスト12件新規＋既存全回帰合格。
- **前後測定（pinned撤去の関門）**: eval_recall 全5件が撤去後も**1位を維持**（スコアは新式で 0.89→0.85 前後に微減、順位無傷）。トリガー救済なしで合格条件クリア。
- **正典バックフィル**: pinned 9件へ trigger_keywords を理性エンジンで生成・付与（9/9成功・別名含む）。実DB確認: 「宮古島」→3件マウント、「ファクトチェックしといて」→当初top-8圏外だった正典を確実にマウント、無関係入力の誤発火ゼロ。**二経路想起の穴（2026-07-03計測の知見）が実測で塞がった**。
- **環境インシデント: Ollama自動更新でnum_ctxが32kに膨張**。Aurora が12GB相当となり51%CPUオフロード→対話300秒超タイムアウト。対策として **num_ctx をコード側で固定**（対話=4096: 7.6GB/GPU84%、蒸留=8192: 裏方のためオフロード許容）。デフォルト依存は今後も禁物。
- quality-reviewer 指摘5件を全反映: nan/inf の float() 素通り防御／タイムスタンプ破損時のフォールバック（検索・重力とも「詰み」回避）／list_hot の前絞り撤廃／照れ隠し境界値(=0.95)テスト／負の半減期ガード。
- persona.md への「約束を守る」原則追記（2026-06-29決定）は、既存 SECTION1-3/1-4 が同内容を規定済みのため**追記不要と確認**（正典無変更）。

## 2026-07-03 スライス3b 実装完了（二段階レビュー✅・E2E成功）

- **実装完了**: スキーマ2表（open_threads/state_audit）／Store CRUD 7メソッド／前方一致パーサ／Distiller（二役蒸留）／open_threads注入／REPL経路A(`/distill`)・経路B(バックグラウンドワーカー)／自動テスト13件全合格。回帰（smoke/smoke_core/test_session/eval_recall全5件1位）も無傷。
- **理性エンジン確定**: `hf.co/mradermacher/gemma-4-12B-it-uncensored-heretic-i1-GGUF:IQ4_XS`（6.64GB）。当初候補 llmfan46 リポジトリには IQ4_XS が無く最小 Q4_K_S=8.22GB で 8GB 鉄則超過のため、imatrix ミラーへ変更。
- **日本語実測 合格（決定4のデバッグステータス解消）**: 缶詰15ターンで、事実抽出（ノイズ除外）・エイリアス対応（クロさん=黒田さん、keywords両載せ）・fact_updates 2/2・スレッド解決・追いかけ質問生成・ネガティブ非検閲、全観点クリア。日本語自然。応答263秒/回（2080S・載せ替え込み）。
- **実測からの追加改善**: 理性エンジン入力に【現在の心理状態】を追加（旧値を知らない盲目採点だと毎回絶対値になるため。プロンプトも「現在値を起点に更新」へ改訂）。
- **quality-reviewer 指摘の反映**: `/distill` の join タイムアウト時ガード（二重蒸留防止）／DB を WAL 化＋busy_timeout 5000（対話×蒸留の並行書き込み対策）／open_threads に部分ユニーク索引（open中の同一質問）。監査ログの再試行時重複は「多い分には無害」として意図的に許容。
- **E2E**: 実DBで孤児2セッション（repl_76d660a6・smoke_core_test）を実蒸留。日記2＋事実3が誕生（858→863件）、感情採点は「動くべき時だけ動く」健全な挙動を確認。intimacy +0.2 のような大きめ変動は 3c の±0.15クランプで抑える設計どおり残課題。

- **`指示書_3b_蒸留と二役リフレクション.md` 作成完了**（spec-reviewer 適合✅）。レビュー指摘3件を反映:
  1. open_threads の重複判定は「question × status='open'」（セッションを跨いだ同一質問の二重登録防止。resolved後の再浮上は新規登録）
  2. dedup は `embedder.embed(content)` → `find_similar(embedding, 0.92)` の作法を明記し、得たベクトルを `add_memory(embedding=...)` へ再利用（二重埋め込み回避）
  3. 親設計の「排他ロック/キュー」は **cancel_event（チェックポイント中断）＋ Ollama リクエスト直列化 ＋ keep_alive=0（採点後即退去）** の3点で具体化（独自ロック機構は増やさない）
- **研究資料 `research/研究蒸留まとめ.md` §C の反映判断**（4件）:
  - ①ホット層昇格（参照頻度）= **採用**。`access_count` は search で加算実装済みと判明、昇格ルールを設計書§3cへ追記（閾値・枠サイズは3c指示書で確定）
  - ②aliases（別名）= **採用**。独立フィールドを作らず trigger_keywords に別名・表記ゆれを全部含める方式（デコーダーリング）。§3b追補＋指示書_3bに反映
  - ③検索トリガー規律 = **保留**（現行は毎ターン検索でローカルでは安価・実害なし。スライス2のルーティング設計時に再検討）
  - ④揮発/永続の経路分離 = **実現済み**（history⇔memories＋有意性ゲートが既にこの思想。追加実装なし）

- **「実装=Cursor」を廃止**。Claude Code で設計・実装を完結し、トークン制限時のみ Cursor で続行（Products 配下共通ルール）。
- git 管理開始（https://github.com/Oik-dev/Serina ）。旧記憶原本を `legacy/` に保全コピー。
- DB 日次バックアップ導入（`tools/backup_db.py` → `G:\SerinaDB Backup`、7世代）。
- **バイテンポラル失効管理を採用**（specs/基本設計書_スライス3 §3b `<fact_updates>`・§3c 事実の失効管理）。減衰＝忘却とは別に、事実の「失効」を `metadata` の `invalidated_at`/`superseded_by` で管理。Zep/Graphiti 等 2026年SOTAとの差分解消。
- **想起品質の回帰評価ハーネス導入**（`tests/eval_recall.py` ＋ `tests/golden_queries.json`）。実DBのコピーに対しゴールデンクエリ5件を検索し順位を検証。3c改修前のベースライン: **全5件が1位合格**（2026-07-03計測）。3cの減衰式・pinned特例撤去の前後で必ず実行。
- 計測で得た知見: pinned は「常時候補」だが top-k の椅子取りには参加するため、**語彙が遠いクエリでは圏外に落ち得る**（初回計測時「ファクトチェック原則」が top-8 圏外）。3cのトリガー想起（静的マウント）が正にこの穴を塞ぐ設計であることを実測で確認。

## 2026-07-02 企画拡張6項目をマスター全採用

`specs/基本設計書_スライス4_記憶の成長.md` 新設。成長＝【再構成】×【行動化】×【計測】。

- 再固結（週次で日記群→傾向・信念・自己像へ二次蒸留）／意味づけ更新（原文は監査線に保持）
- ネガティブ記憶の対等記録・好奇心キュー(open_threads) → **3bへ前倒し**
- 差分想起／不在時間の内的生活（捏造ガード付き）
- 運用基盤: 成長KPI（/rate・コールバック率）＋二層人格（憲法persona.md＋可変self_image）＋ドリフト監査
- 実装順の確定: **3b → 3c → 4a → 4b → 4c**（スライス2は後続のまま）

## 2026-07-02 スライス3 設計レビュー（修正済み）

1. 生活日判定のTZバグ: UTCのまま−4hで日界がJST13:00にずれ毎日昼に誤分断 → ローカル時刻変換に修正、回帰テスト6件全合格
2. アーカイブ削除基準を ts → archived_at へ（退避直後の即消滅防止）
3. 3c減衰式の矛盾（乗算vs加算）→ フロア付き乗算（κ=0.35）に確定
4. 3bの未定義事項を追補（孤児history採用／蒸留入力上限／蒸留中の対話優先／出所記録／感情ステート保存先／トリガーキーワード仕様）
5. 決定4のモデル実在をHFで確認済み

## 2026-07-01 スライス3 確定事項

- 【決定1】約束・アレルギー等コア情報も**半減期180日で緩やかに減衰**（不滅フラグ廃止）。関連キーワード検知時は減衰無視で確実に想起（**二経路想起**）。忘れっぽすぎたら180日を延長。
- 【決定2】感情の重力は**実時間×リフレクション回数のハイブリッド**（時定数τ=3日）。「毎回一律10%」は破棄。
- 【決定3】リフレクションは**二役（ツーハット）**: 日記はセリナ本人（Aurora）が自分の声で書く／事実抽出＋感情採点は「内省・理性エンジン」が担う（自己採点はおべっかループになるため分離）。
- 【決定4】理性エンジン＝**Gemma 4 12B 無検閲（Heretic系）**、交換可能。根拠: EQ-Bench最強クラスの機微読解＋高い構造化信頼性。日本語は3bで実測確認。採点は`reason`付きでログ保存しOpusが後で妥当性を再判定（監査線）。
- 分割: **3a** セッション基盤+アーカイブ → **3b** 蒸留（二役＋理性エンジン+XML+ゲート） → **3c** 減衰&感情（可変λ+ハイブリッド重力）

## 2026-06-29 実装方針

- **順序は 3→2**（reflection先、ルーティング後）。依存関係が無く、3が「馴染むセリナ」の本体で価値が高く、先送りするほど蓄積されない会話のロスが増えるため。
- スライス3に同梱する小修正:
  1. **約束の想起ポリシー変更**: pinnedを「常時注入」→「絶対に減衰しない・消えない・想起で最優先」に。約束は話題の時だけcontextに入る。「約束を守る」原則だけ persona に常時残す。外す前に約束トピックで正典が最上位に来るか測定。
  2. **直近履歴 `history_n` 拡大**: 8→20〜30。100ターン生はAurora12B/2080S 8GBで重い（prefill/KV）。長期は生窓でなくreflection＋想起で賄う。
- スライス2（本格ルーティング＋2つ目の実Skill）は独立なので後続でOK。

## 基盤（初期確定）

- **対話モデル**: NemoAurora-RP-12B（Aurora、交換可能）。実タグ `hf.co/Aratako/NemoAurora-RP-12B-GGUF:IQ4_XS`。人格は焼かずCoreが実行時注入。
- **技術**: SQLite + sqlite-vec / Python / 埋め込み bge-m3(1024次元, CPU, Connector注入)
- **Memory設計**: アトミック記憶＋ハイブリッド検索(relevance×recency×importance)＋reflectionで自己成長
- **正典保護（移行v2の教訓）**: 継承記憶r1.md=正典。破砕せず原文保持／他ソースに上書き禁止／dedup閾値0.92／無言破棄禁止。reflectionの書き込み経路もこの保護ロジックを再利用する。
- 旧記憶の移行 v2完了: 858件・固定9件・正典は原文無傷。
