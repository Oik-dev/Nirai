# Nirai 敵対レビュー — 2026-09-08

> **Status update:** 本文21件は発見時点のEvidenceとして保持する。2026-09-08中に全21件を修正・正方向回帰へ変換し、その後のIncident Repair追加敵対レビュー6件も修正した。さらに`Adversarial_Review_Followup_2026-09-08.md`のR01-R06で外側cleanup / cancellation / deleted-unsynced Memory / fallback tailまで再検証し、全件修正した。**Currentの最終判定と実測はFollowup Review末尾の「修正後再検証」を正とする。**

対象は D:\Products\Nirai の現在の作業ツリー（未コミット・未追跡の実装を含む）。実装・テストを直接確認し、ルール／設計ガバナンス文書は読まず、サブエージェントも使用していない。

**確定指摘は21件。P1が6件、P2が15件。** P1はデータ消失、並行実行の破壊、または有効な設定で基本機能が動かなくなるため先に直すもの。P2は条件付きの機能不良、整合性欠陥、長期運用上の性能問題。全経路の無欠陥を保証するレビューではない。

既存テストは Core 453件、World 243件がすべて成功し、Worldの型検査も成功した。その状態で追加のPython 15件・TypeScript 2件のオフライン実験も成功した。**追加実験の成功は「不具合を再現できた」の意味であり、正しい動作を証明する回帰テストではない。** 実装は修正していない。

検証はpytestの仮ディレクトリ、フェイクBrain／Adapter、フェイクSocketを使用。実際のAI呼び出し、課金操作、大容量ダウンロード、既存ユーザーデータの削除は行っていない。Cursorの競合は起動待ちの状態を模擬して確認しており、実Providerを並行起動した負荷試験ではない。

## 優先して修正する問題

### F01 — [P1] 削除済みチャットIDを再利用し、別会話の記憶が混ざる

根拠: [chat_store.py:56](D:/Products/Nirai/core/sessions/chat_store.py:56)、[削除処理:146](D:/Products/Nirai/core/sessions/chat_store.py:146)。

当日のセッション番号を「現在残っているindex.jsonの最大値+1」で採番する。最新セッションを削除するとその番号が再利用される。通常の履歴削除はWorld Memoryを残すため、新セッションと旧セッションが同じsession_idを共有する。新セッションの記憶参照に旧会話が混ざり、新セッションをForgetすると旧会話の記憶まで削除対象になる。過去Agentのorigin_chat_session_idも新会話を指し得る。

再現: 旧セッションの発言を記録→チャットのみ削除→同日新規作成→新発言記録。IDが同じになり、World Memoryの同一session_id配下に旧・新2件が存在した。

修正: IDをUUIDにするか、削除されない永続採番器を使う。表示用の日付・連番と永続的な識別子を分離する。既存衝突データは自動で安全に分割できるとは限らない。

### F02 — [P1] 応答生成中の別発言を、読んでいないのに既読にする

根拠: [server.py:4397](D:/Products/Nirai/core/server.py:4397)、[差分取得:4076](D:/Products/Nirai/core/server.py:4076)。同じ方式がWhisper／住民会話にもある。

Native Brainに渡した入力の末尾ではなく、後から保存した自分の返答のentry_idまでmark_seenする。その間にHolo等が公開発言すると、その発言は今回の入力に含まれず、次回の差分にも含まれなくなる。

再現: フェイクNative Adapterの生成中にHolo公開発言を追加。公開履歴には残るが、最初のProvider promptにも次回native差分にも入らなかった。通常UIの連投禁止では防げない。

修正: Providerに実際に渡した入力境界と、自分が生成した出力の識別子を別管理する。割り込んだ発言を未読として残し、自己出力の再注入だけを除外する。

### F03 — [P1] Cursorの並行起動が、起動途中の別タスクを削除する

根拠: [cursor_acp.py:471](D:/Products/Nirai/core/agents/cursor_acp.py:471)、[active登録:487](D:/Products/Nirai/core/agents/cursor_acp.py:487)、[掃除:1313](D:/Products/Nirai/core/agents/cursor_acp.py:1313)。

ACPはstagingと認証ホームを準備した後、create_subprocess_execをawaitし、その後に_activeへ登録する。他タスクがこの間に起動すると、掃除処理が先行タスクを「activeではない古いディレクトリ」と判断する。stagingだけでなく認証ホームの掃除も同じ登録集合を見る。CLI専用経路には事前予約があるがACP経路にはない。

再現: AS-Aの起動待ち相当の状態からAS-Bのstagingを準備すると、AS-Aのstagingが消えた。Managerは最大4セッションを許可するため、並行開始を想定外とは扱えない。

修正: 最初の準備前から終了まで有効な所有権予約を設け、掃除対象の判定にも使う。別Adapterインスタンス／別プロセスが所有する作業も考慮する。

### F04 — [P1] ロールバック失敗時に、復元に必要なバックアップを消す

根拠: [cursor_acp.py:1601](D:/Products/Nirai/core/agents/cursor_acp.py:1601)、[finally:1611](D:/Products/Nirai/core/agents/cursor_acp.py:1611)。

変更適用に失敗し、さらにロールバックにも失敗すると「rollback was incomplete」と報告する。しかしfinallyで.nirai-rollbackを無条件削除する。失敗後の手動復旧に必要な元データを破棄する設計になっている。

再現: 小さな仮ファイルに対し適用・復元の両方へI/O失敗を注入。対象はPARTIALのまま、元データのバックアップディレクトリは消えた。

修正: 復元完了を確認するまでバックアップを保持し、失敗時は復旧マニフェストと場所を保存・表示する。保持した復旧データを上位finallyのホーム掃除で消さないことも必要。

### F05 — [P1] 改行のない正常JSON末尾の後に追記すると、正常イベントも消える

根拠: [store.py:100](D:/Products/Nirai/core/agents/store.py:100)、[末尾修復:133](D:/Products/Nirai/core/agents/store.py:133)。

read_eventsは末尾に改行がなくてもJSONとして完成していれば受け入れる。append_eventは区切り改行を補わず次のJSONを連結する。次のread_eventsは連結した行を壊れた末尾としてtruncateするため、旧正常イベントと新イベントの両方が失われる。

再現: 正常イベント1件の末尾改行だけを除去→2件目をappend→read。ログは0件、0バイトになった。突然終了後の部分書き込みや手作業での末尾変更に対して、修復機構自身が追加の損失を起こす。

修正: 追記前に末尾を検査し、受理したJSONには区切りを補う。不正末尾は退避してから切り詰める。ChatStoreには同種の区切り対策があるため、実装間の不整合でもある。

### F06 — [P1] core.portを変更するとWorldが接続できない

根拠: [CoreConnection.ts:46](D:/Products/Nirai/world/src/renderer/src/runtime/CoreConnection.ts:46)、[App.tsx:597](D:/Products/Nirai/world/src/renderer/src/App.tsx:597)、[launcher.py:23](D:/Products/Nirai/world/launcher.py:23)。

Coreはconfig.core.portで待ち受けるが、Worldはws://127.0.0.1:8765固定。CoreConnectionにurlオプションはあるもののAppから渡されず、Launcher／preloadにもCoreの実アドレスを渡す経路がない。設定値が有効と判定されるのに通常起動が成立しない。

再現: World接続生成を捕捉し、NIRAI_CORE_URLを9876に設定しても8765を選ぶことを確認。Core側が設定ポートを採用することはソースで確認した。実Electronの再起動は実施していない。

修正: Launcherから実際にbindしたCore URLを渡し、preload経由でWorldが使用する。port_overrideを含め、定数の二重管理をなくす。

## 整合性・機能・性能の問題

### F07 — [P2] ロック待ちのキャンセルが、他処理所有のロックを解放する

根拠: [server.py:4445](D:/Products/Nirai/core/server.py:4445)。同形が1209、4706、5090行にもある。

await lock.acquire()がキャンセルされ、取得に成功していなくてもfinallyはlock.locked()だけでreleaseする。lockedは「誰かが保持している」の意味であり、自分が取得した証拠ではない。別の生成処理が排他保護を失う。

再現: 他の処理が保持するロックで生成タスクを待たせてキャンセルしたところ、所有者がreleaseしていないのにロックが解除された。

修正: async withを使うか、acquire成功後だけ立てる所有フラグで解放を制御する。

### F08 — [P2] 親フォルダと子フォルダの同時書き込みを排除しない

根拠: [manager.py:198](D:/Products/Nirai/core/agents/manager.py:198)、[開始時の再判定:408](D:/Products/Nirai/core/agents/manager.py:408)。

ワークスペース競合は正規化文字列の完全一致で判定する。projectを書き込み中でもproject/srcを別作業対象にでき、同じファイルへ同時に書ける。read_only対writeの親子関係も同様。複数allowed_dirsや直接start_sessionで到達する。

再現: projectにrunningスナップショットを置くと、projectは利用不可なのにproject/srcは利用可能と判定された。

修正: 同一パスだけでなく祖先／子孫の範囲重複を判定し、予約中と稼働中の双方に適用する。

### F09 — [P2] ChatとMemoryの二重書き込みに、失敗後の再同期がない

根拠: [server.py:3350](D:/Products/Nirai/core/server.py:3350)、[初期化:198](D:/Products/Nirai/core/server.py:198)。

Chat保存の後でWorld Memoryへ別トランザクションで保存する。後者だけ失敗するとUI履歴には残るのに長期記憶側に欠落する。再起動時に通常会話をreplayするチェックポイント／outboxがない。WhisperもChatとPrivate Memoryを別々に書く。

再現: master_sayでWorld Memory書き込み失敗を注入してCoreを再構築。公開チャットには発言が存在するが、World Memoryの同セッションRawは空のまま。

修正: 権威ある記録への保存と未反映ジョブ作成を同じコミットにし、安定entry_idで冪等な再反映を行う。全DBを無理に一つにする必要はない。

### F10 — [P2] Agent開始失敗が、実行体のないstartingセッションを残す

根拠: [manager.py:462](D:/Products/Nirai/core/agents/manager.py:462)、[続くawait:468](D:/Products/Nirai/core/agents/manager.py:468)。

スナップショット登録後、最初のイベント保存等が失敗すると、_run_sessionタスクを作る前に抜ける。finallyは予約枠だけを戻し、startingスナップショットを終端化しない。実行体はないのにactiveとして数えられ、ワークスペースと同時実行枠が塞がる。

再現: 初期イベント保存失敗を注入。has_active_sessionはtrue、_tasksは空、同ワークスペースは利用不可になった。

修正: 起動フェーズの失敗を一元処理し、Provider開始前の失敗でもfailed/interrupted等へ収束させる。

### F11 — [P2] 応答準備の例外でUIが「応答中」のままになる

根拠: [server.py:4270](D:/Products/Nirai/core/server.py:4270)、[ChatBar.tsx:70](D:/Products/Nirai/world/src/renderer/src/ui/ChatBar.tsx:70)。

_respond_to_masterはactive=true送信後、finallyの外で履歴取得・Memory検索を行う。そこでI/O例外等が起きるとactive=falseが送信されず、完了コールバックはログを出すだけ。ChatBarはactiveRequestIdがある間送信できない。

再現: Memory検索にOSErrorを注入。送信されたresponse_stateはtrueだけだった。停止を押して回復できる場合でも、障害の自動収束としては不十分。

修正: 応答の全準備工程を一つのtry/finallyで囲み、リクエストID付き失敗通知と必ず終端状態を返す。

### F12 — [P2] Forget中のEmbedding完了が、削除済みベクトルを再作成する

根拠: [private_semantic.py:198](D:/Products/Nirai/core/memory/private_semantic.py:198)、[Forget:218](D:/Products/Nirai/core/memory/private.py:218)。

Embeddingはジョブ取得後にawaitする。その間にForgetでRawとベクトルを削除しても、commit_jobはRaw／ジョブ／tombstoneの有効性を再検査せずraw_vecへINSERTする。

再現: pending job取得→forget_entry→同jobをcommit。Rawは0件、ベクトルは1件となった。JOINでRawが要求されるため、これだけで元テキストが検索結果に戻るとは言わない。しかしForgetの派生データ除去契約を破り、孤児ベクトルとKNN候補の汚染を生む。

修正: 同じトランザクション内でRaw存在・ジョブ世代・削除状態を確認し、無効化済みなら破棄する。World側のベクトルcommit/rebuildも同じ観点で揃える。

### F13 — [P2] 外部プロジェクトの成果物をUIから開けない

根拠: [paths.ts:47](D:/Products/Nirai/world/src/main/paths.ts:47)、[外部対象解決:2185](D:/Products/Nirai/core/server.py:2185)。

Coreはtasks.allowed_dirs内の外部プロジェクトを対象にできるが、Electronの成果物パス検証はNirai/runtime/workspace配下以外を必ず拒否する。外部作業が成功しても成果物リンクが失敗する。

再現: resolveAgentWorkspaceFilePathに外部workingDirを渡すと、実在確認前にoutside the Nirai task workspaceで拒否された。

修正: Coreが承認済みタスクに紐付けた作業ルートをMainへ渡して検証する。任意のworkingDirを無条件信用する緩和は避ける。

### F14 — [P2] 同梱holo-mcpが現在のCoreプロトコルと接続不能

根拠: [coreClient.mjs:118](D:/Products/Nirai/holo-mcp/src/coreClient.mjs:118)、[server.py:3166](D:/Products/Nirai/core/server.py:3166)。

MCP側はhello role=holo_adapterを送りholo_adapter_hello_ack/resultを待つ。Coreが扱うのはholo_localとworldであり、holo_adapterは応答せず無視される。秘密を正しく設定してもツール呼び出しがタイムアウトする。

再現: MCPと同じhelloをフェイクSocketから現行Coreへ入力し、ACKが0件であることを確認した。

これは現在のtools/holo-local-client.mjs経路が全滅しているという指摘ではない。古い同梱MCP実装が生きた機能として残っている問題である。

修正: 必要なら現行プロトコルへ移行し認可契約も整合させる。不要なら明示的に廃止して実行可能な入口から外す。役割名だけの置換では認証モデルの差を解消できない。

### F15 — [P2] Holoイベントカーソルに再起動・ページ境界の復旧契約がない

根拠: [events.py:33](D:/Products/Nirai/core/holo/events.py:33)、[結果のカーソル:89](D:/Products/Nirai/core/holo/events.py:89)。

event_idはCore再起動で1へ戻る。旧cursor=100で再接続すると、新しいevent=1があってもタイムアウトする。またlimit=50で60件中50件しか返していないのにlatest_event_id=60を返すため、その値を継続カーソルに使うクライアントは残り10件を飛ばす。返却イベント末尾IDを使えばページ飛ばしは回避できるが、その契約を結果構造が保証していない。256件リングからの脱落も通知しない。

再現: 再起動相当のQueue再作成と、60件／50件ページの両ケースを確認した。

修正: stream epoch、next_cursor、high_watermark、gap/reset情報を分離する。失われた範囲はsnapshot再取得等で明示的に回復する。

### F16 — [P2] Agentイベント追記が累積二乗コストで、Core全体を止める

根拠: [store.py:78](D:/Products/Nirai/core/agents/store.py:78)、[manager.py:797](D:/Products/Nirai/core/agents/manager.py:797)、[agentStore.ts:120](D:/Products/Nirai/world/src/renderer/src/stores/agentStore.ts:120)。

1件追加するたびevents.jsonl全体を読みJSON解析して最大seqを求め、その後fsyncとスナップショット保存を行う。同期I/OがasyncioイベントループとManagerロック内で走る。Frontendも毎回全イベント走査・コピー・ソートを行う。2,000,000文字の本文予算を超えても打ち切り表示イベント自体は増え続ける。

再現: 20件追記で過去ログ読取件数は0,1,…,19の合計190件。累積O(N²)は制御フローで確定している。実運用の遅延時間を測ったベンチマークではない。

修正: 排他管理したnext_seqを保持し復旧時だけtail検査する。イベント索引・ページ取得・上限を設ける。同期永続化を専用writerへ分離し、必要な耐久性を維持する。

### F17 — [P2] Cursor通常作業が依存物まで全コピーし、一般的なプロジェクトで限界に達する

根拠: [cursor_acp.py:314](D:/Products/Nirai/core/agents/cursor_acp.py:314)、[コピー:1274](D:/Products/Nirai/core/agents/cursor_acp.py:1274)、[上限:1404](D:/Products/Nirai/core/agents/cursor_acp.py:1404)。

書き込み作業では.cursor/.gitしか除外せず、node_modules/.venv/build等も毎回ハッシュ・コピー対象になる。20,000ファイルまたは1,000,000,000バイトを超えるとProvider開始前に拒否。上限内でも同期全走査・全コピーでCoreの他処理を止め、終了時にも複数回全ハッシュを行う。read_only経路には依存物除外があり、用途間で性能対策が不一致。

検証: 除外集合・上限・コピー呼び出しを静的に確認。1GBコピー等の負荷実験は実施していない。

修正: 対象ソースの選択、除外、読み取り専用の依存物参照、差分stagingを設計する。事前見積もりを出し、重いファイル処理はイベントループから外す。上限だけを増やして解決しない。

### F18 — [P2] Native会話のサイズ制限が、全履歴を読み込んだ後に働く

根拠: [server.py:4099](D:/Products/Nirai/core/server.py:4099)、[chat_store.py:252](D:/Products/Nirai/core/sessions/chat_store.py:252)。

公開Native context再構築時にpublic_history_after(session_id, None)で全件fetchall／JSON展開し、その後で100件／64,000文字超なら20件に切り直す。入力を制限していても、メモリとCPUの上限にはならない。Resident追加・Provider変更・context破損時など、長期履歴があるほど重くなる。

検証: SQLにLIMITがないことと制限判定の順序を確認。数年分データを生成する負荷実験はしていない。

修正: 初回は最初からbounded tailを取得し、継続差分はLIMIT+1または上限付きストリームで超過を検出する。Whisperの初回bootstrapは既にboundedになっている。

### F19 — [P2] world.voicevox_urlを変更しても音声接続先が変わらない

根拠: [config.py:220](D:/Products/Nirai/core/config.py:220)、[voicevoxIpc.ts:29](D:/Products/Nirai/world/src/main/ipc/voicevoxIpc.ts:29)、[launcher.py:25](D:/Products/Nirai/world/launcher.py:25)。

設定ファイルでは必須として読むが、利用側は環境変数NIRAI_VOICEVOX_URLか127.0.0.1:50021しか見ない。Launcherは設定値を渡さない。ポートを変更したVOICEVOXや別ホストを設定しても反映されない。

修正: 検証済み設定値をMainへ伝達し、環境変数との優先順位を一本化する。到達性検査と合成が同じ確定URLを使うようにする。

### F20 — [P2] FPS／省電力設定は検証されるだけで、実行には使われない

根拠: [config.py:162](D:/Products/Nirai/core/config.py:162)、[SceneRuntime.ts:289](D:/Products/Nirai/world/src/renderer/src/runtime/SceneRuntime.ts:289)、[RenderLoop.ts:3](D:/Products/Nirai/world/src/renderer/src/runtime/RenderLoop.ts:3)。

world.fpsは設定として読むがRenderLoopへ渡されず、上限は既定72。ecomode.resume_delay_secも設定の読み込み・検証以外に消費箇所がない。設定を下げてもレンダリング負荷低減に反映されない。実際のFPSはディスプレイ／負荷にも依存するため、常に72FPSになるという意味ではない。

修正: FPS上限・バックグラウンド時停止／低頻度化・復帰遅延の実装に設定を接続する。未対応なら有効な運用設定として扱わない。

### F21 — [P2] CLI出力を時間制限だけで収集し、メモリ使用量を制限しない

根拠: [process_manager.py:86](D:/Products/Nirai/core/brains/process_manager.py:86)。

stdout/stderrの両方をcommunicateで丸ごと保持する。Providerが大量のログを吐けば、タイムアウト前にCoreがメモリを使い切れる。Agentイベント予算はこの受信バッファには効かず、出力文字列化でさらに複製される。Cursor通常会話／CLI経路などに共通する。

検証: 上限なしの収集経路を確認。OOMを起こす実験はしていない。

修正: バイト予算付きで逐次読み取り、閾値超過時はProviderを停止して明確なエラーにする。必要な診断ログは上限付きファイルまたはリングへ退避する。

## 構造面の改善余地（上の21件とは別）

- **状態の持ち方が分散しすぎている。** CoreServerは約5,200行で、接続、通常応答、Holo、Agent、Task Queue、Memoryの状態と保存順序を一つで扱う。上記の既読境界・ghost session・準備例外の漏れは、処理ごとに似たライフサイクルを手作業で複製した結果でもある。大改修から始めず、所有権・保存境界・終端処理をまず小さな共通部品にする。
- **境界をまたぐテストが薄い。** 既存696件は成功しても、Core設定→Launcher→preload→World、MCP client→Core、保存→削除→再作成、別処理のロック→キャンセルを結ぶ契約が守られていなかった。単なる件数増加ではなく、障害注入・並行インターリーブ・状態遷移の不変条件を追加する。
- **長期運用の保持上限が弱い。** Agent sessions、Frontendのイベント配列、チャット表示済み履歴、会話ロックのMap等に保存寿命／削除時の収束を定義する。データを勝手に消すのでなく、アーカイブと索引／表示上限を分ける。
- **補助ファイルの失敗を本体の失敗へ昇格させすぎる。** World MemoryはRawコミット後に互換Episodeへ追記する。この派生ファイルで失敗すると処理全体がエラーになり、同一entry_idの再試行はRawが既存のため派生修復を行わず戻る。派生物の再構築ジョブを本体コミットから分離するべき。これは追加の障害注入実験をしていない改善指摘。
- **「read_only」の強度がProviderごとに異なる。** Cursor Resident CLIはWindowsでask modeを使い、OS sandboxを付けない。コード上のラベルだけでCodexのsandboxと同じ隔離と扱えない。ここでは実Providerの制限回避／漏えいを再現していないため、確定した漏えい脆弱性とは報告しない。

## 修正の順序

1. F01–F05の識別子・既読・作業所有権・復旧データ保存を先に直し、正常動作を期待する回帰テストへ変換する。
2. F07–F12のキャンセル／保存失敗／Forget競合を収束させる。失敗しても「稼働中」「既読」「保存済み」と嘘をつかない状態モデルにする。
3. F06、F13–F15、F19–F20の部品間契約を接続テストで固定する。
4. F16–F18、F21の全件走査・コピー・受信バッファを上限付きにし、小規模な実測を加える。

## 再現ファイルと実行記録

- [Python再現実験](D:/Products/Nirai/.tools/adversarial_review_20260908/test_review_probes.py): 15件成功、1.98秒。
- [Frontend再現実験](D:/Products/Nirai/.tools/adversarial_review_20260908/frontend.test.ts): 2件成功。
- 既存Core: 453 passed、87.13秒。既存World: 39 files／243 tests passed。World typecheck: exit 0。

作業ディレクトリD:\Products\Niraiから、以下で再実行できる。

```powershell
.venv\Scripts\python.exe -m pytest .tools/adversarial_review_20260908/test_review_probes.py -q --tb=short
world\node_modules\.bin\vitest.cmd run --root . .tools/adversarial_review_20260908/frontend.test.ts
```

レビューで追加したのは本レポートと上記2ファイルのみ。既存の大量の未コミット変更をレビュー前から確認しており、それらを本レビューの変更とは扱っていない。テスト実行に伴うキャッシュ等は別途更新され得る。

なお、実Provider接続、Electronの実画面、GPU実測、長時間連続運転、実ネットワーク障害、電源断は未検証。疑わしい箇所をすべて確定バグと数えてはいない。

---

## 2026-09-08 修正後再検証

### 判定

**SAFE — 本レビューのP1/P2 21件は全件修正・正方向回帰へ変換済み。DNA / UE4.27 Feasibilityへ進む前のCore / World基盤Blocking Findingは、本監査範囲では残っていない。**

このSAFEは「全経路の無欠陥」を意味しない。特に実Provider長時間運転、GPU、実ネットワーク障害、物理電源断は別の運用Evidenceで継続確認する。また独立Cursorによる修正後再レビューは縮小Bundleでも82秒timeoutし、`SAFE / NEEDS FIX`判定を返す前に終了したため、外部SAFE証拠には数えていない。

### 旧再現Probeの反転確認

発見用Python Probeは「欠陥が存在するとPASS」の逆向きTestである。修正後再実行では**14 failed / 1 passed**へ反転した。14件のFAILは、旧欠陥条件が成立しなくなった期待どおりの結果。

残る1 PASSはF04の旧Probeで、元の`.nirai-rollback`が消えることだけを欠陥判定している。Current実装はrollback不完了時に元データと`recovery.json`を`runtime/cursor_recovery/REC-*`へ退避してから通常一時領域を片付けるため、復旧データ消失という元Findingは成立しない。正方向回帰`test_cursor_incomplete_rollback_preserves_recovery_backup`で保持を固定した。

F14の旧ProbeはCurrent Coreが`holo_adapter`を4004拒否するため旧Fake Socketの`close()`不足でAttributeErrorになった。これは「無応答でtimeoutする」という元Findingの再現ではない。Current回帰ではretired roleを明示拒否し、旧`holo-mcp`を製品入口から撤去している。

Frontend旧Probeは**1 failed / 1 passed**。F13側はCurrent APIがAgent Session Snapshotの`working_dir`を正本とする方式へ変わり、さらにrealpathでjunction / symlink脱出を拒否するため旧再現前提が成立しない。F06側の旧Probeは`CoreConnection`単体の既定URLが8765であることだけを見るため現在もPASSするが、製品起動経路は`Core bound_port → StandardWorldLauncher.core_url → NIRAI_CORE_URL → preload → App → CoreConnection`へ接続済み。`test_run_builds_default_world_launcher_from_bound_core_and_config`でbound port 9876がWorld Launcherへ渡ることを固定している。

### 修正後に追加で見つけて閉じたFailure Path

- Cursor staging / credential homeはthread-safe runtime ownershipで同一Core内の起動途中Sessionを保護し、別Core Process由来の若いDirectoryは6時間未満ならstale扱いしない。初期化途中失敗でもownershipを解放する
- 外部Project成果物はSession Snapshotの許可working directoryに加え、`realpath`でも同じ実体Root配下であることを確認し、junction / symlink経由の脱出を拒否する
- Chat JSONLのfsync直後、SQLite index / Memory outbox作成前にCoreが落ちても、起動時に全Chat raw tailを先にreindexしてからoutbox replayするためMemoryへ収束する
- World Memory ForgetはSession tombstoneとRaw / derived削除を同一SQLite transactionで確定し、Chat / outboxが残ったcrash境界でも復活しない。遅延して戻るStructured / Vector JobもRaw + Job再検証後に破棄する
- Agent Eventは耐久JSONLへ全履歴を保持する一方、World再接続SnapshotはDiskから最新500件だけをbounded tail読取する。通常snapshotで全Event logをmaterializeせず、Frontendも同じ500件上限で保持する

### F16–F21の長期運用対策確認

- F16: event appendは全履歴再scanをやめ、tail seq復旧 + `asyncio.to_thread`永続化。Snapshotも最新500件bounded tail
- F17: Cursor writable stagingは`.venv` / `node_modules` / build / cache等の依存・生成Treeを除外し、重いsnapshot/copy処理をevent loop外へ移動
- F18: Native public bootstrapは最初からbounded recent tail、継続deltaは上限付き取得
- F19/F20: Core設定のVOICEVOX URL / FPS / ecomode resume delayをStandardWorldLauncher経由でWorldへ一元伝達
- F21: ProcessManagerはstdout + stderr合算byte budgetを逐次監視し、超過Providerを停止してbounded errorへ収束

### 最終実測

2026-09-08修正後の最終回帰：

- Core pytest：**482 passed**。単一全件実行は90秒Tool上限で89%時点timeoutしたため、全Test Fileを二分割して**282 passed + 200 passed、Failure 0**で完走
- World Vitest：**39 files / 245 tests passed**
- TypeScript typecheck：**成功**
- Electron production build：**成功**
- Doctor：**fatal=0 / warnings=2**
  - Cursor optional Provider：OK
  - Gemini optional Provider：OK
  - Codex optional Provider：launcher/runtime未構成のためWARN、Core起動には非Fatal
  - Claude：Current acceptanceで無効のためWARN、Core起動には非Fatal
- `git diff --check`：**成功**。stderrは既存working-copyのLF→CRLF warningのみ

追加の敵対回帰`core/tests/test_adversarial_regressions.py`には、既読境界、lock ownership、response_state終端、retired Holo role、Cursor並行prepare / rollback recovery、workspace親子競合、Private / World Forget競合、Agent event O(N²)再発、Chat ID再利用、Native bounded bootstrap、Memory outbox crash recovery、Holo event epoch / page / gap、Agent Snapshot bounded tail等を正方向で固定している。

### 残る非Blocking事項

本レビューとは別に、Phase 1既知の継続観測は残る。Codex Live SmokeはProvider利用枠 / runtime条件が整った時点で再確認、Gemini Cloud Vector quota / 長時間運用は実運用Evidenceを継続取得する。World Observation / Natural Idle / Brain生活ティックはWorld Replacement後のM3工程、DNA / UE4.27は次工程のFeasibility対象であり、本修正で「実装済み」とは扱わない。

---

## Incident Repair追加敵対レビュー後再検証

### 判定

**SAFE — 追加敵対レビューで報告された6件は全件再現確認後に修正し、正方向回帰へ固定した。さらに修正実装そのものを再監査し、派生する競合・負荷・Privacy境界も追加で補強した。Current監査範囲でDNA着手を止めるBlocking Findingは残っていない。**

このSAFEは全経路の無欠陥を保証するものではない。実Provider長時間運転、Electron実画面、GPU、実ネットワーク障害、物理電源断は別の運用Evidenceで継続確認する。

### IR-F01 — [P2] Holo会話が`running`のまま永久停止

Resident `talk`で公開Chat Sessionが失われている場合、旧実装はConversationを`running`へ保存した後にSession不在を検出して例外終了し、実行Taskなしの`running`を残した。

修正後は公開Chat Sessionを`start_turn`前に検証する。失敗時はConversation stateを変更せず、Task / public-session bindingも作らない。欠陥を再現する回帰は修正前FAIL、修正後PASSへ反転した。

### IR-F02 — [P1] Codex並行起動が別Sessionの認証Homeを削除

Codex App ServerにもCursorと同じruntime ownershipを導入した。Session IDはisolated Home prepareより前にclaimし、process / final cleanup完了まで保持する。stale cleanupはowned Homeを除外し、別Core Process由来のDirectoryも6時間未満ならstale扱いしない。prepare途中Failureでもclaimは必ずreleaseする。

### IR-F03 — [P1] 不完全rollbackのRecovery退避失敗で原本Backupを消失

RecoveryはRollback原本を直接`move`しない。`.nirai-rollback`を保持したまま`recovery tempへcopy → recovery.json作成 → atomic rename`の順に公開する。export失敗時は原本を削除せず、Errorへ残存Pathを明記する。成功時だけ通常cleanupへ進む。

この修正により、copy途中・manifest書込・atomic publishのいずれでFailureしても、復旧用原本をRecovery処理自身が消費しない。

### IR-F04 — [P1] 空の引用付きGemini KeyをHealthだけAvailable判定

`GEMINI_API_KEY=""`または`''`はquote除去後に空なら`None`へ正規化する。Doctor、Dive Health、`_provider_is_available("gemini")`は同じProduct parser結果を見るため、空KeyをAvailableとして会話 / Memory経路へ流さない。

### IR-F05 — [P2] Codex Home cleanupがevent loopを停止

Codex isolated Homeのprepare / cleanup、spawn失敗時cleanup、Conversation credential cleanupを`asyncio.to_thread`へ移した。Windowsの短時間File lock retryが発生してもCore event loop全体を停止させない。

追加監査ではProvider Conversation context破棄にも同じ同期cleanup経路が残っていたため、これもoff-loop化した。ただし単純なfire-and-forgetでは次Turnが旧Context cleanup完了前に始まるため、Conversation IDごとにcleanup Taskを追跡し、**同じConversationの次sendだけ**前回cleanup完了をawaitする。Core全体は継続し、当該Conversationの因果だけ直列化する。

### IR-F06 — [P2] Incident SQLite競合時にERRORが消失

Incident DBをWAL化し、busy timeoutをboundedにする。SQLiteへ保存できないERRORは`runtime/incidents-fallback.jsonl`という単一bounded journalへfsync保存し、次回Dive HealthでSQLiteへreplayする。

ERROR嵐の直後にHealth replay自体が長時間処理へ化けないよう、1 checkpointで最大32件だけ処理する。残りがあればfallbackを保持し、Healthを`attention`のまま次回へ送る。DB lockを実際に保持した障害注入Testで、ERRORがsilent lossせずfallbackからSQLiteへ収束することを確認した。

### 追加監査で閉じた派生Failure Path

- Memory OutboxのJSONだけが壊れた場合はindexed Chat entryからpayloadを自己修復してMemory replayする
- Outboxの`scope / resident / payload`を派生値として扱い、indexed Chatの`kind / from / to`から再導出する。Private WhisperのOutbox scopeだけを`private → world`へ改変してもPublic World Memoryへ昇格できない
- indexed Chat側まで壊れて自動修復不能な場合はCoreを落とさずIncidentへ昇格する
- 同一未解決Incidentは再発しても最高severityを保持する
- Incident fingerprintはprovider / operation / scope等の安定した故障軸を区別し、Session ID / PID等の揮発値では行を増殖させない
- Dive時Memory Outbox replayは最大32件、Incident fallback replayも最大32件にboundedする
- Codex PATHに壊れたlauncherがある場合でも正常なDesktop managed Runtimeへfallbackする

### 最終実測

2026-09-08 Incident Repair追加敵対レビュー修正後：

- Core pytest：**509 passed**
  - 全Test Fileを90秒Tool上限回避のため二分割し、**307 passed + 202 passed / Failure 0**
- World Vitest：**39 files / 245 tests passed**
- TypeScript typecheck：**成功**
- Electron production build：**成功**
- Doctor：**fatal=0 / warnings=1**
  - Cursor：OK
  - Codex：OK
  - Gemini：OK
  - Claude：Current acceptanceで無効のためWARN
- `git diff --check`：**成功**。stderrは既存working-copyのLF→CRLF warningのみ

### Current残余境界

- 実Provider長時間連続運転 / network churn / quota変動は自動回帰では代替できないため継続観測する
- Electron実画面と物理電源断は別の実機QA対象
- DNA / UE4.27は次工程のFeasibilityであり、本レビューはその成立性を証明していない
- 通常Resident / Agent RuntimeによるNirai本体self-buildは引き続きM5+境界で、Current Incident Repairは`Core自動Recovery → Incident → 次回Holo Diveで修復`を正規運用とする
