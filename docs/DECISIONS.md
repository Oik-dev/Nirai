# Serina 決定ログ

確定した設計判断の記録（新しい順）。現在地は `MILESTONE.md`、詳細設計は `specs/` を参照。

---

## 2026-07-11 Phase4継続: 蒸留消化ロジックを実装・記憶候補の即時DB書き込み（裏口）を廃止

- **背景**: MILESTONEの次アクション「蒸留ジョブの消化ロジック」着手前に、既知の設計不整合（`_process_turn`が「記憶候補」付箋を即時便のうちに`review_candidate`で直接DB審査していた。§4.1本来は即時便候補→宿題箱→裏方便精査→関所→DB）の扱いをマスターに確認した。
- **マスター確認結果**: 「蒸留は記憶候補（DBに書き込む候補）の唯一の生成源」を採用。advisorレビューで、これは§4.1「記憶DBに書き込めるのはこのライン一本だけ。裏口は存在させない」を文字通り実現する解釈であり、`review_candidate`（関所④引用照合・重複チェック・上限）自体は再利用可能な単一コンポーネントで、変えるべきは「どこから呼ぶか」（会話中→裏方便の消化）だけ、という整理で着手した。
- **実装**:
  1. `core_v2/runtime.py`の`_process_turn`から、即時便の「記憶候補」付箋を`review_candidate`で直接DB審査していたブロックを削除（裏口を閉じた）。`Core.session_candidate_count`は現時点でこれを増やす経路が無くなったため実質デコード待ち状態（下記「未解決」参照）。
  2. `core_v2/chores/distillation.py`（新規）: 宿題箱の`kind="蒸留"`ジョブを消化する`consume_pending_distillation_jobs()`。ジョブのlane別に注入されたcall_fn（Aurora/Geminiの生テキスト呼び出し）へ会話断片を発注し、JSON形式で記憶候補（引用つき）を受け取り、蒸留ジョブに積まれた断片自体から`SessionState`を再構成して`review_candidate`（既存の関所④コンポーネントをそのまま再利用）に通す。合格分のみDBへ書き込む。
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
- **根拠の所在**: `core_v2/runtime.py`（`_process_turn`）、`core_v2/chores/distillation.py`（新規）、`tests/test_chore_distillation.py`（新規）、`tests/test_core_memory_integration.py`。

---

## 2026-07-11 Phase4着手: 宿題箱（ChoreBox）プリミティブ＋Core.end_session()

- **成果**: 設計書v2 §2.4「裏方便の駆動方式（宿題箱・機会駆動）」のうち、宿題箱そのもの（enqueue/pending/mark_done・永続化）と、Coreのセッション境界処理（`end_session`）を新規実装。`core_v2/chores/chore_box.py`（新規）・`core_v2/runtime.py`（`Core.__init__`に`chore_box`引数追加、`_process_turn`に断片enqueue、`end_session`新設）。これにより**Phase3から繰り越していた`session_candidate_count`のセッション境界リセット未対応**（2026-07-11「Phase2由来の積み残し2件」#3）を解消。
- **enqueueのタイミングを設計に合わせて修正した経緯**: 初版実装では`end_session`が呼ばれた時点でセッション全ターンを一括で宿題箱へ積んでいたが、advisorレビューで§2.4本文（「会話中: Coreが蒸留の宿題（細切れ断片）を宿題箱に積む」＝enqueueは会話中／消化は①セッション終了時②アイドル時③朝礼、の2つは別物）との齟齬を指摘された。この実装だと強制終了で`end_session`が走らない場合、メモリ上のSessionStateがまるごと失われ、③朝礼での回収（§2.4「宿題は消えず③で回収される」）が効かない——設計が守ろうとしているクラッシュ耐性そのものを破る実装だった。**修正**: enqueueは`_process_turn`側に移し、蒸留の断片バッファ（`_pending_fragment`）が器（`chore_fragment_turns`設定値・既定20ターン）を満たすたびに即座に宿題箱へ積む。`end_session`は端数（partial fragment）のflushと、`session_candidate_count`・`SessionState`のリセットのみを担う。
- **宿題箱の永続先**: `serina_memory.db`（長期記憶DB）とは別ファイル`data/chore_box.db`とした。根拠は§2.6の状態目録で「宿題箱」が「長期記憶DB」と別掲されていること。`data/*.db`は既存の`.gitignore`対象のため、生きているDBとは別に`tools/backup_db.py`のバックアップ対象にする必要はない（CLAUDE.mdの「破壊的操作の前にbackup_db.py」は長期記憶DBが対象）。
- **蒸留の車線振り分け（クラウド／ローカル）は今回スコープ外**: §2.4「裏方便の二車線」は断片ごとにCoreの個人情報フィルタが振り分ける設計だが、その断片単位のフィルタは未実装（現状のセンシティブ判定は発話単位のみ）。今回は`decide_brain`同様「グレーは安全側」に倣い、全断片を`lane="local"`固定で積んでいる。車線振り分け・実際の消化ロジック（蒸留LLM発注）は次のスライスで対応。
- **記憶候補審査ラインとの不整合（既知・未着手）**: `core_v2/runtime.py`の`_process_turn`は現状、「記憶候補」付箋を即時便のうちに`review_candidate`で審査しDBへ直接書き込んでいる。§4.1の設計上のパイプラインは「即時便の粗い候補→宿題箱→裏方便で精査→関所→DB」であり、宿題箱を経由しない今の実装は設計と厳密には一致しない（advisorレビューで指摘済み）。ただし現状動いている経路を今回崩すのはスコープが違うため据え置き。記憶候補フローを宿題箱経由に作り直すことがPhase4後続スライスの候補になりうる。
- **テスト**: `tests/test_chore_box.py`（新規・enqueue順序／kind絞り込み／limit／mark_done／プロセス再起動を模した再オープンでの永続確認）、`tests/test_core_session_boundary.py`（新規・session_candidate_countリセット／SessionStateリセット／chore_box未設定時のno-op安全性／会話中の断片flush／end_session時の端数flush）。既存`core_v2`系統合テスト（test_core_full_body/test_core_memory_integration/test_core_routing_integration）・`test_thresholds_config`・`tests/smoke.py`・`tests/test_session.py`全green（回帰なし）。
- **設定値追加**: `config/thresholds.toml`に`[chores] fragment_turns = 20`（`ThresholdsConfig.chore_fragment_turns`）。
- **根拠の所在**: `core_v2/chores/chore_box.py`、`core_v2/runtime.py`（`_process_turn`, `_flush_full_chore_fragments`, `_enqueue_chore_fragment`, `end_session`）、`core_v2/config.py`、`config/thresholds.toml`、`tests/test_chore_box.py`、`tests/test_core_session_boundary.py`。

---

## 2026-07-11 Phase3残課題: センシティブ観測付箋をGemini/Auroraアダプタから実発火させる

- **成果**: 前回実装したCore側受け皿（`_apply_sensitivity_observation`）を実際に生かすため、`brains/gemini/adapter.py`の`build_prompt()`と`brains/aurora/adapter.py`の`build_extraction_prompt()`（2回目・付箋抽出発注）に`SENSITIVITY_OBSERVATION_INSTRUCTION`を追加。「話題がクラウドに不向き／実は平気だったと感じたらセンシティブ観測付箋を書け」＋`direction`/`keywords`のJSON例を提示。keywordsは「話題そのものを特定する語のみ（『話』『相談』等の一般語は選ばない）」と明記——ラチェットが部分一致のため誤爆源になるのはBrainが選ぶ根拠語の質そのもの、という点を指示文の本体にした。
- **Aurora側の非対称**: 判定はstage1（自由会話）ではなくstage2（付箋抽出の別発注）に置いた。stage1は既存設計どおり書式強制なしのまま維持し、二段方式の内部に閉じる形（条文Aに抵触しない）。
- **これで解決した範囲・していない範囲（正確な理解のために明記）**: 今回追加したのは「Brainが成功ターンで自発的にセンシティブ観測付箋を書く」経路の実配線のみ。`core_v2/runtime.py`の`_obtain_valid_report`内、`CloudRejectionError`（クラウドの安全フィルタ拒否）時に発話全文をそのまま`tighten()`の鍵にしている箇所は**設計として意図的に据え置いたまま**（この時点ではBrain呼び出し自体が拒否されており報告書が存在しないため、付箋経由の根拠語は原理的に使えない。全文一致は再ヒット率が低い代わりに誤爆もしない高精度フロアとして維持）。つまり「元々の問題が解決した」のではなく、「拒否経路の全文フロアは意図的に残置。キーワード汎化は“成功ターン経由”という別経路が今回初めて生きた」が正確な理解。
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
- **成果**: 付箋カタログ6種のうち唯一未配線だった「センシティブ観測」（設計書v2 §2.2, §3.3.1）のCore側消費ロジックを実装。`core_v2/runtime.py`の`_process_turn`が`accepted_fusen`を走査し、`kind=="センシティブ観測"`の付箋を`_apply_sensitivity_observation`へ渡す。`content={"direction": "不向き"|"平気", "keywords": [...]}`。`direction=="不向き"`のときのみ`keywords`各語を`routing_rules.tighten()`（逆止弁の厳格化方向・自動反映）。`direction=="平気"`（緩和方向）はマスター承認が必須（§3.3.1）のため自動適用しない——ただしaccepted_fusenとして受理済み記録は残るため無言破棄ではない（承認導線自体は今回スコープ外）。
- **advisorレビューで却下した案**: Core側で正規表現による日本語根拠語の自動抽出は**採用しなかった**。理由: (1) 設計は根拠語の出所をBrain（LLM）と明記しており(§2.2「誰が書く: Brain」)、Core側抽出は設計が求めていない。(2) トークナイザ無しの正規表現抽出は「話題」等の常用語まで拾い、`is_sensitive`が部分一致である以上、日常発話の大半が誤ってAurora行き(93秒/ターン)になる。(3) ラチェットは厳格化方向のみ自動・緩和はマスター承認必須の片方向のため、誤爆した鍵は張り付いて手動でしか剥がせない。(4) ラチェットは二次防壁に過ぎず（一次防壁=`decide_brain`の内容ベース判定、2026-07-11既出）、再現率より適合率を優先すべき局面だった。
- **`core_v2/runtime.py:132`（CloudRejectionError時に発話全文をそのまま鍵にしている箇所）は意図的に未変更**。この時点ではBrain呼び出し自体が拒否されており報告書が存在しないため、根拠語をBrainに書かせる通常経路（今回実装した付箋経路）が使えない。全文一致は再ヒット率が低い代わりに誤爆もしない「意図的な高精度フロア」として温存し、汎化は通常成功時にBrainが自発的に書くセンシティブ観測付箋に委ねる設計とした。コード上にもこの理由をコメントで明記済み。
- **既知の未対応（スコープ外・将来対応）**: 現時点でどのBrainアダプタ（Gemini/Aurora）もセンシティブ観測付箋を実際には出力しない。プロンプト側の対応（Brainに根拠語つきで書かせる指示）は今回のCore側配線とは別作業として残る。緩和方向(`平気`)のマスター承認導線（承認キュー等）も未実装。
- **テスト**: `tests/test_core_routing_integration.py`に`test_sensitivity_observation_fusen_tightens_by_keyword_not_whole_utterance`（根拠語が鍵になり別発話でも再ヒットし、無関係発話は巻き込まないことを検証）・`test_sensitivity_observation_fusen_loosen_direction_is_not_auto_applied`（緩和方向が自動適用されないことを検証）を追加。既存`test_cloud_rejection_falls_back_to_aurora_same_turn_and_tightens_rule`は無変更のまま green（132を変えていないため）。全165件green。
- **根拠の所在**: `core_v2/runtime.py`（`_process_turn`, `_apply_sensitivity_observation`, `_obtain_valid_report`のコメント）、`tests/test_core_routing_integration.py`。

## 2026-07-11 Phase2由来の積み残し2件を解消（tighten誤爆・スキーマ整合）

- **#1 tightenの誤爆修正**: `Core._obtain_valid_report`が「呼び出し例外」「契約書式違反」を問わずcloud失敗全てで`routing_rules.tighten()`していた問題（2026-07-10繰り越し）を解消。型付き例外`CloudRejectionError`（`brains/contract/schema.py`）を新設し、Gemini通訳が応答JSONの`promptFeedback.blockReason`（値問わず存在すれば拒否）／候補ゼロ／`finishReason`が「話題そのものが安全/ポリシー上拒否された」ことを示す種別（`SAFETY`/`PROHIBITED_CONTENT`/`SPII`/`BLOCKLIST`。Gemini API公式リファレンスで裏取り済み。**`SAFETY`限定だと個人情報ブロックの`SPII`——このシステムの防御対象そのもの——を取りこぼすため列挙**。同じ列挙候補の`LANGUAGE`（対応言語外という能力的限界）・`RECITATION`（引用/著作権由来の停止）は話題の機微性と無関係なため意図的に除外——ラチェットは「話題の機微性」だけを学習する設計のため）を検知したときのみ送出するよう変更（`brains/gemini/adapter.py`の`_extract_reply_text`に純関数として分離）。`_obtain_valid_report`は`CloudRejectionError`のときだけtighten、通信エラー（`requests.exceptions.RequestException`等、意図的にラップせず伝播）と契約書式違反はフェイルオーバーのみでラチェットは研がない（設計書v2 §3.5どおり）。
  - **着手前の前提確認**: 一次防壁である`decide_brain`（`core_v2/routing/decision.py`）の§3.2②「内容ベースでセンシティブ→ローカル確定」が実装済みであることを確認済み。ラチェット（tighten）は二次的な学習層に過ぎないため、tighten対象を狭めても一次防壁が別に効いておりプライバシー的な穴は開かない。
  - **既知の未解決（Phase3本体へ繰り越し）**: `tighten(master_utterance)`は鍵として発話全文をそのまま`_sensitive_keywords`に加えるため、`is_sensitive`の部分一致判定上は「一字一句同じ発話が再度来たときしかヒットしない」に等しく、**現状では「次回から同種の話題は最初からローカルへ」（§3.5の意図）を実質達成できていない**。根拠語（トピックの核となる単語）への圧縮は設計上「センシティブ観測」付箋（Brainが根拠語つきで報告）経由で行う想定であり、これはPhase3ルーティング本体の仕事のためスコープ外とした。tighten発火のON/OFF区別（今回の修正対象）と、鍵の粒度（根拠語化・未着手）は別問題である点に注意。
  - **テスト**: `tests/test_core_routing_integration.py`に`test_communication_error_falls_back_but_does_not_tighten_rule`・`test_contract_format_violation_does_not_tighten_rule`を追加。既存の`test_cloud_rejection_...`は`raise_cls=CloudRejectionError`を明示するよう更新（旧実装は素の`RuntimeError`でも誤ってtightenしていたため、それを再現できないテストに直した）。`tests/test_gemini_adapter.py`に`_extract_reply_text`の缶詰データ検証（`SAFETY`だけでなく`SPII`も含む）を追加。
- **#2 スキーマ整合**: `core_v2/memory/store.py`の`_ensure_schema()`のCREATE TABLE文が実DB物理スキーマ（`pinned`/`source`/`parent_id`/`metadata`列・`FOREIGN KEY(parent_id)`）より縮小していた問題を解消。実DBの現行スキーマ（`tools/migrate_memory_schema.py`適用後）と一字一句一致するよう書き換え。`IF NOT EXISTS`のため実DBへの影響はないが、テスト用新規DB作成時に物理スキーマの乖離が起きなくなった。`tools/migrate_memory_schema.py`は一回性のALTER移行ツールであり役割が異なるため単一ソース化はせず現状維持。
- **#3 `session_candidate_count`のセッション境界リセット**: 現状1 Core＝1セッションで無害なため今回は着手せず、MILESTONEどおりPhase4に残す（advisor助言: 先食いすると Phase4 の面を潰すだけ）。
- **根拠の所在**: `brains/contract/schema.py`（`CloudRejectionError`）、`brains/gemini/adapter.py`（`_extract_reply_text`, `CONTENT_BLOCK_FINISH_REASONS`）、`core_v2/runtime.py`（`_obtain_valid_report`）、`core_v2/memory/store.py`（`_ensure_schema`）、`tests/test_core_routing_integration.py`・`tests/test_gemini_adapter.py`・`tests/test_memory_store.py`（全GREEN確認済み）。

---

## 2026-07-10 Phase 3（ルーティング）進行中の繰り越し1件

- **内容**: `Core._obtain_valid_report`は、cloud Brainの失敗が「呼び出し例外（通信エラー・弾切れ含む）」でも「契約書式違反」でも、区別なく`routing_rules.tighten()`を呼ぶ。設計書v2 §3.5は本来「クラウドの拒否（安全フィルタ）」だけを振り分けルール学習の対象とし、「通信エラー・弾切れ」は同ターン代打のみでラチェットは研がない、と別扱いにしている。
- **現状維持の理由**: 区別するには通訳（adapter）が型付き例外（例: `CloudRejectionError` vs 単純な通信エラー）を返す必要があるが、現在は素の`Exception`のみ。誤ってtightenする方向は「グレーは安全側」（設計書v2 第0章 確定判断#2）と同じ向きのズレであり、実害は限定的（プライバシーの門が余分に閉まるだけで、開く方向には動かない）。
- **今後の対応**: Gemini/Aurora通訳に型付き例外を持たせ、`_obtain_valid_report`でクラウド拒否のみtighten対象に絞る。Phase3の後続タスクまたはPhase5（道具箱・仕上げ）で対応。
- **根拠の所在**: `core_v2/runtime.py`の`_obtain_valid_report`。

## 2026-07-10 Phase 2（記憶接続）完了

- **成果**: 想起（関連度×新しさ×重要度の**積**＋保護等級A/Sのキーワードトリガー想起）と記憶候補の審査ライン（関所④引用照合・重複チェック・1セッション上限）を接続。保護3原則（透明性・可逆性・同一性）を実装。Core本体に配線し、毎ターン想起→pack反映、記憶候補付箋→審査→DB書き込みが動作。既存866件（正典9件含む）へスキーマ移行済み。
- **設計適合の是正2件**（Advisorレビューで発見・修正）:
  1. 想起スコアが当初「加重和」で実装されていたのを、§4.4が明記する「かけ算」（`relevance * recency * importance`、素の積）に修正。積は「無関係な記憶は重要度が高くても沈む」ANDゲート特性を持つ、設計が意図した挙動。
  2. `recall()`が想起のたび鮮度回復（`last_accessed`更新・`access_count`加算, §4.1）していなかったのを追加。積にrecencyを使う以上、回復がないと継承866件が新規記憶に対して指数的に沈み二度と浮上しなくなるため。`nearest_relevance()`（重複チェック専用）は想起ではないため回復させない。
- **実DB移行**: `tools/backup_db.py`でバックアップ後、`tools/migrate_memory_schema.py`を適用。正典9件（`pinned=1`）→保護等級S、非正典857件→保護等級B（全件、マスター判断）、全866件が機微等級2で開始。`memory_vec`充足866/866・1024次元（bge-m3と一致）を確認、ダミーベクトルによるKNN機構疎通も確認済み。
- **繰り越し事項（Phase2完了時点）**:
  1. ~~実bge-m3 recall smoke未実施~~ → **2026-07-10中に解消**。マスターがOllamaを起動、`tests/smoke_bge_m3_recall.py`で実DB866件への意味的想起を確認（「マスターとの約束」→誓いの記憶、「セリナの性格について」→名前・性格の記憶が適切にヒット）。
  2. `core_v2/memory/store.py`の`_ensure_schema()`は実DBの物理スキーマ（pinned・source・parent_id・metadata列を含む）より縮小したCREATE TABLE文を持つ。実DBに対しては`IF NOT EXISTS`によりno-opだが、Phase3で実DB以外（テスト用新規DB等）を本番相当に使う前にスキーマを整合させること。
  3. `Core.session_candidate_count`（1セッションあたりの記憶化件数上限のカウンタ）はセッション境界でのリセット処理を持たない。現状1 Core＝1セッションのため無害だが、セッション回転を扱うPhase4で対応が必要。
- **根拠の所在**: `core_v2/memory/`（embedder.py, store.py, protection.py）, `core_v2/intake/memory_review.py`, `core_v2/context/pack.py`, `core_v2/runtime.py`, `tools/migrate_memory_schema.py`, 対応する`tests/test_*.py`一式（16ファイル全GREEN）。

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
- **根拠の所在**: `core_v2/`, `brains/`配下の実装一式、`tests/test_contract.py` `test_thresholds_config.py` `test_emotion_state.py` `test_relationship_session_state.py` `test_intake_gate.py` `test_context_pack.py` `test_gemini_adapter.py` `test_core_full_body.py` `test_env_loader.py` `test_constitution.py`、実機確認 `tests/smoke_gemini.py`。

## 2026-07-10 Phase 1実装: 新実装は `core_v2/` に仮払い（暫定処置）

- **背景**: 設計書v2 §5.1 の目標ディレクトリ構成（`core/state/`・`core/context/`・`core/intake/` 等）は、既存の旧実装（`core/config.py`・`core/context.py`・`core/session.py`・`core/runtime.py` 等、フラット配置）と同一ディレクトリ名で衝突する。特に `core/context.py`（旧・ファイル）と `core/context/`（新・ディレクトリ）は同名衝突し、Python の import 解決が壊れる。設計書は「旧コードは Phase 6 まで削除しない・参照もしない」とのみ定め、この物理衝突には触れていなかったため、マスターに確認して決定。
- **決定**: Phase 1〜5 の新実装は `core_v2/`（および将来必要なら `skills_v2/` 等）に仕払える。旧コードとは物理的に一切衝突しない。Phase 6 の大掃除（設計書v2 §5.3）で旧 `core/` 等を削除したのち、`core_v2/` → `core/` にリネームして正式名に揃える。
- **影響**: 設計書v2 §5.1 のディレクトリ構成表そのものは変更しない（最終形は変わらない）。Phase 6 の作業に「`core_v2/` → `core/` リネーム」が1ステップ追加される。
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
