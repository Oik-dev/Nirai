# Nirai 敵対レビュー再検証 — 2026-09-08

> **Status update:** 本文R01-R06は発見時点のEvidenceとして保持する。2026-09-08同日に全6件を実コードで再現確認後に修正し、正方向回帰8件へ固定した。Current判定と実測は末尾の「修正後再検証」を正とする。

対象は `D:\Products\Nirai` の作業ツリー。HEADは `543c3ee0743f57624b6659e1a6e47fa47ae24d68` だが、多数の未コミット変更と新規ファイルを含む現在の実装をレビューした。前回報告書は上書きしていない。製品コードの修正、実Providerへの問い合わせ、実ユーザーデータの変更は行っていない。

**今回確定した指摘は6件（P1: 2件、P2: 4件）。8つの隔離実験で再現した。** P1はファイル破損・復旧不能・作業の衝突につながるため優先修正、P2は条件付きで機能・安全上の契約を損なう問題を示す。

| ID | 優先度 | 問題 | 実験結果 |
|---|---|---|---|
| R01 | P1 | Cursorの復旧用コピーを、最終後片付けが削除する | 実行全体を通した失敗注入で、元ファイルが破損しバックアップも消失 |
| R02 | P1 | Cursorのキャンセル完了後も承認済み変更の書き込みが続く | キャンセルが戻り作業領域の所有登録が消えた後、ファイルが変更された |
| R03 | P2 | 未同期チャットの削除が他のチャットの記憶再同期まで停止させる | 別セッションを含む未同期2件を取得できず、再起動相当でも停止 |
| R04 | P2 | Codex起動準備・プロセス起動待ちのキャンセルで認証コピーが残る | 2種類のキャンセル位置で偽のauth.jsonが残留 |
| R05 | P2 | Cursor ACPの準備中キャンセルが作業コピーと所有登録を残す | コルーチン終了後にコピーが完成し、所有登録とフォルダが残留 |
| R06 | P2 | 障害記録の途中書き込みが、後続記録の取り込みを止める | 正常な後続エラーも取り込めず、再試行しても同じ位置で失敗 |

## R01 — 復旧コピーを残す処理の外側で、それを削除している

**場所:** [cursor_acp.py:1693](D:/Products/Nirai/core/agents/cursor_acp.py:1693)、同ファイル873–876、1980–1983、2022–2027。ACP側も688–691で同じ後片付けを行う。復旧用ディレクトリ作成の別の取りこぼしは1673–1677。

実ファイルへの変更に失敗し、元に戻す処理も失敗すると、`_apply_staged_changes` は `.nirai-rollback` を `runtime/cursor_recovery` に退避する。退避に失敗した場合は `preserve_rollback_root=True` として、その場に原本を残した旨のエラーを返す。

しかし、その原本は `cursor_home/.nirai-staged-review/.nirai-rollback` に置かれている。呼び出し元の `finally` は成否に関係なく `cursor_home` 全体を削除するため、原本も消える。現在の既存回帰テストは `_apply_staged_changes` から戻った直後までしか確認しておらず、その後の削除を見ていない。

さらに `recovery_root.mkdir(...)` は退避失敗を捕捉する `try` の外にあり、そこで失敗すると `preserve_rollback_root` がFalseのまま内側の後片付けでも原本を削除する。

**再現:** 実Cursorを使わず、CLI結果だけを偽装して `_run_exact_cli` 全体を実行。`a.txt=ORIGINAL` を `CHANGED` へ変更する承認を与え、適用と巻き戻しにファイルI/O障害を注入する。復旧先コピー失敗と復旧先ディレクトリ作成失敗の2条件を検証した。いずれも終了後は `a.txt=PARTIAL`、復旧コピーなしとなった。

**影響:** 障害が重なったとき、元データの最後のコピーが失われる。「原本を残した」とするエラーの案内も実行終了時点では事実と異なる。

**修正方針:** 復旧データの保存責任を実行全体で共有する。復旧データは認証ホームの通常削除対象から独立させるか、退避失敗を最終後片付けへ伝え、認証素材だけを消してバックアップを保持する。復旧先の作成から公開までを同じ失敗保護区間に含める。回帰は必ず実行全体の終了後に原本の存在と内容を確認する。

**プローブ:** `test_cursor_full_turn_cleanup_deletes_last_recovery_copy`（2条件）。

## R02 — キャンセルは適用用の別スレッドを止めず、終了後に書き込む

**場所:** [cursor_acp.py:1285](D:/Products/Nirai/core/agents/cursor_acp.py:1285)、同ファイル865–882、`core/agents/manager.py:753–766`。

`_review_and_apply_staged_changes` は、実ファイルへの適用を `asyncio.to_thread` で別スレッドへ渡す。待っている側をキャンセルしても、既に走っているスレッドは停止しない。その終了を待つ保護がないまま呼び出し元の後片付けへ進み、レビュー資料やバックアップを含むホームを削除し、所有登録も解除する。ManagerはキャンセルされたProviderタスクを正常なキャンセルとして扱い、セッションを終了状態へ進める。

**再現:** 偽CLIが `a.txt` の変更案を作り、承認後、適用用のファイルコピー途中で処理を待機させる。その間にキャンセル。キャンセルが戻り、ホーム削除・所有登録解除を確認した時点では `a.txt=ORIGINAL`。コピーを再開すると、その後に `a.txt=CHANGED` になった。

**影響:** 「キャンセル済み」を見てマスターが編集を再開したり、次の仕事を始めたりしても、古い仕事が後から書き込む。複数ファイルの途中なら、後片付けが適用元や復旧用コピーを消すことによる部分適用・復旧失敗も起こり得る。承認なしで変更が始まる問題ではなく、承認済み変更をキャンセルした後の終了保証の問題である。

**修正方針:** 適用開始後は、変更または巻き戻しが確定するまで適用タスクを保持し、キャンセル要求が来ても完了を待つ。書き込みが終了するまで作業領域を解放せず、レビュー資料・バックアップも削除しない。画面上はこの間「停止処理中」とする。

**プローブ:** `test_cursor_cancel_returns_before_approved_write_finishes`。

## R03 — チャット削除後の孤立した未同期行が、再同期全体を塞ぐ

**場所:** [chat_store.py:160](D:/Products/Nirai/core/sessions/chat_store.py:160)、同ファイル552–613、`core/server.py:303–307,3755–3766`。

Chatは保存されたがMemory側への書き込みに失敗した場合、`memory_outbox` に再同期用の行が残る。通常のチャット削除はJSONLと `chat_entries` を消す一方、この未同期行を残す。後の `pending_memory_sync` は `chat_entries` を正本として必須参照するため、削除済みチャットの行に遭遇すると `memory outbox payload has no indexed source entry` を投げる。

この取得処理は行単位の失敗を返す方式ではなく、一覧全体を返す前に失敗する。Coreもそこで再同期ループを終了するため、同じ取得範囲の正常な他セッションまで再同期されない。通常の削除経路には未同期の有無を確認する処理がない。

**再現:** セッションAへ保存後、Memoryへ同期せずAを削除。続いてセッションBへ未同期の発言を追加する。未同期数は2だが一覧取得は例外となる。ChatStoreを作り直してRaw再照合を行っても同じ例外が続く。

**影響:** 障害後の自動回復が永続的に止まり、別の会話・Whisperの未同期分まで滞留する。普通のチャット削除で長期記憶を維持するという契約にも反する。新規発言の通常の直接同期が成功する場合まで、すべて止まるという意味ではない。

**修正方針:** 通常削除の前に未同期記憶を確実に保存するか、再同期に必要な正本を削除対象から独立して保持する。明示的なForgetと通常削除を区別する。読めない行は隔離・Incident化し、別の正常な行の再同期を妨げない。未同期行を一律削除するだけでは、記憶を残す契約を満たさない。

**プローブ:** `test_deleted_unsynced_chat_poison_blocks_other_sessions`。

## R04 — Codexの起動途中キャンセルで認証ファイルが取り残される

**場所:** [codex_app_server.py:421](D:/Products/Nirai/core/agents/codex_app_server.py:421)、同ファイル427–440。実際の認証コピーは653–665、古いホームの整理は707–735。

準備用の別スレッドを待っている間にキャンセルすると、外側は所有登録を解除して戻るが、準備スレッドは続行できる。そこで後から作られる認証ホームの受け取り先と後片付けがなくなる。また、準備完了後の `_spawn` 待機中のキャンセルは、後片付けを行う `except Exception` では捕捉されず、外側の登録解除だけを通過する。

**再現:** ①準備を待機させ、キャンセルが戻った後に偽 `auth.json` を作成する。②偽認証ホームを準備して、偽 `_spawn` を待機させてからキャンセルする。どちらも終了後に偽認証コピーが残り、所有登録は空だった。本物の認証ファイルは使用していない。

**影響:** 実行終了時に破棄する設計のログイン情報が `runtime/codex_agent_homes` へ残留する。通常ホームの古いものを消す処理は6時間の経過と後続prepareを条件にするため、即時解消はされない。認証情報が実際に第三者へ流出したことを示す実験ではない。

**修正方針:** 準備スレッドの終了を待ってリソースを受け取り、キャンセルを含む全終了経路でホームまたは認証素材を削除する。所有登録の解除はその後に行う。プロセス起動待ちも同じリソース管理区間へ含める。

**プローブ:** `test_codex_cancel_during_prepare_leaves_late_auth_copy`、`test_codex_cancel_during_spawn_leaves_prepared_auth`。

## R05 — Cursor ACP準備中のキャンセルが作業コピーを残す

**場所:** [cursor_acp.py:340](D:/Products/Nirai/core/agents/cursor_acp.py:340)、同ファイル349–352、1335–1369。

ACP経路で作業コピーを作る別スレッドを待つ箇所は `except Exception` のみで囲まれ、キャンセルはここを通過する。その後にある通常の実行終了処理にも到達しない。コピー処理は継続し、`_preparing_ids` と所有登録が残る。

**再現:** 実際の `_prepare_staging_workspace` の実行開始を待たせ、ACPの `run` をキャンセルしてから準備を再開した。実行タスクは終了しているが、コピー先ディレクトリと両方の所有登録が残った。

**影響:** コピーに時間がかかる仕事を途中で止める操作で、作業データが取り残される。所有登録に残っているため、同じCoreプロセスの通常の古い作業領域整理でも保護され続ける。認証コピーを確認したR04とは異なり、このプローブは認証準備より前の作業コピー残留を対象にする。

**修正方針:** キャンセルを含む外側の終了処理を準備開始前から有効にし、準備スレッドの終了を待って作業コピーを削除する。削除完了後に準備・所有登録を解放する。ACPとCLI双方で同じライフサイクルを保証する。

**プローブ:** `test_cursor_cancel_during_prepare_leaks_claim_and_stage`。

## R06 — 障害記録の末尾破損で後続Incidentも取り込めない

**場所:** [incidents.py:312](D:/Products/Nirai/core/incidents.py:312)、同ファイル326、363–400。

SQLiteへ障害を記録できない場合の代替JSONLは、追記前に末尾が完全な行かを確認しない。書き込み途中の停止などで改行なしのJSON断片が残ると、次の正常なエラーをその断片へ直接連結する。取り込み側は不正な行で例外を出し、切り離した再生ファイルをそのまま次回へ持ち越す。

**再現:** 代替記録に途中で切れたJSONを書き、その後は製品の `append_fallback` で正常な障害を追加した。`replay_fallback` は2回とも同じ不正JSONで停止し、未解決Incident数は0、代替記録は残留した。

**影響:** 障害記録を守るための代替経路が、保存障害から自動復旧できない。Dive Healthは残留を検知できても、正常な後続エラーの修理情報をSQLiteへ移せない。以後の新しい代替記録も、先に切り離した破損ファイルがある間は取り込み順番が回ってこない。

**修正方針:** 追記前に不完全な末尾を隔離し、新しい正常行と連結させない。再生時は壊れた行を明示的に退避・報告し、正常な行の処理と進捗保存を続けられるようにする。破損データを黙って捨てる方式にはしない。

**プローブ:** `test_incident_partial_tail_permanently_blocks_following_valid_error`。

## 検証と限界

再現コードは `.tools/adversarial_review_followup_20260908/test_review_probes.py`。この8テストは**欠陥が成立すると成功する確認用テスト**であり、修正済みを示すものではない。すべてpytestの一時ディレクトリを使用し、実Providerを起動していない。キャンセル位置とファイルI/O失敗を意図的に固定し、稀な失敗順序を再現した。

```powershell
cd D:\Products\Nirai
.\.venv\Scripts\python.exe -m pytest .tools/adversarial_review_followup_20260908/test_review_probes.py -q --tb=short
```

- 今回の欠陥確認: **8 passed in 7.85s**。
- Core既存回帰（保存・Incident・前回指摘関連）: **83 passed in 52.58s**。
- Core既存回帰（Agent・会話継続関連）: **104 passed in 12.24s**。
- World既存回帰: **39 files / 245 tests passed**。
- その他のCore既存回帰: **322 passed in 44.73s**。上記と重複なしで、Core既存テストは合計 **509 passed**。

Pythonは制限内で既存インタープリターを起動できなかったため、許可された制限外実行で同じチェックアウトの `.venv\Scripts\python.exe` を使用した。依存ライブラリのダウンロードやインストールはしていない。

静的確認はCoreの保存・再同期・会話継続、Agent管理・Cursor/Codex実行、Memory/Forget、Worldの通信・履歴表示・外部ファイル参照を対象とした。確定指摘は上の再現で裏付けられたものに絞った。実Providerの挙動、実OSによる強制終了や電源断、GPU/Electron画面、数年分データの実長期運転、全入力・全経路の網羅を保証するものではない。

前回の21件や追加6件の「修正済み」という文書だけで安全とは判定していない。特にR01は、関数内の保存確認だけでは外側の後片付けまで含む安全性を保証できない例となっている。

## 対象ファイルの識別

レビュー時点のSHA-256:

| ファイル | SHA-256 |
|---|---|
| core/agents/cursor_acp.py | F1F34FE098DEB86378B26B0D7C81D84D357A0AC66FE190C13A645FA9DDC2337A |
| core/agents/codex_app_server.py | A27C6B697FC1D8563014F404EE191A733DB0F9B211FCF99D8037D5348DAA096A |
| core/sessions/chat_store.py | C55EA1D4EBF5C3B384A2289A71A3863B390452BFB963C2B3BB9014212C8F6C43 |
| core/incidents.py | 30DCE5C583829D406EDE198FCCE029CEEC4593B93964F67B4B706CD768C99391 |

---

## 2026-09-08 修正後再検証

### 判定

**SAFE — R01-R06は全件修正済み。発見用8 Probeは8/8がFAILへ反転し、新しい正方向回帰8件は8/8 PASS。Core / World全回帰、typecheck、production build、Doctor、diff-checkまで完走した。**

このSAFEは実Provider長時間運転、物理電源断、Electron実画面、GPU、実ネットワーク障害を保証するものではない。本文の限界は引き続き有効。

### R01 — Cursor Recoveryを実行Homeの外へ分離

Rollback原本を`cursor_home/.nirai-staged-review`配下へ作る方式を廃止し、実Workspaceへ最初の書込みを行う前に`runtime/cursor_recovery/.RB-*`へ作る。Recovery Root自体を作れない場合はWorkspaceを一切変更せずfail-fastする。

Rollback不完了時は`.RB-*`内へ`recovery.json`をflush + fsyncし、同一Recovery Root内で`REC-*`へatomic renameする。公開処理に失敗した場合も`.RB-*`は通常Cursor Home cleanupから独立しているため、外側`finally`後まで原本が残る。正方向回帰は`_run_exact_cli`全体の終了後にHome削除とBackup残存を同時確認する。

### R02 — Cancel完了と承認済みWriteの終了を一致

承認後の`_apply_staged_changes`は専用Taskとして`asyncio.shield`し、待機側がcancelされてもworker threadのapply / rollback確定までSession ownership、review bundle、Recovery Dataを保持する。Apply成功後にcancelを返す場合は、File Changeをcompletedへ進めてから`CancelledError`を返す。Apply / rollback自体が失敗した場合はRecovery Errorをcancelより優先する。

これによりManagerがSessionをcancelledとして解放した後に古いworkerが実Workspaceを書き換える経路を閉じた。

### R03 — 削除Chatの未同期Memory Sourceを同期完了まで保持

通常Chat削除では、`memory_outbox`が参照中の`chat_entries`だけを非表示のReplay Sourceとして一時保持する。他の同期済みindex行とJSONL / Sidebar indexは従来どおり削除する。

Memory replay成功後の`mark_memory_synced`で、元Chat JSONLが既に存在しない場合は保持していたSource indexも削除する。これにより「Chat表示は削除するが長期Memoryは残す」契約を維持しつつ、削除済みSessionの孤立Outboxが他Sessionの再同期をpoisonしない。

### R04 — Codex prepare / spawn cancelでもCredentialを回収

Codex Home prepareの`to_thread`をTaskとしてshieldし、cancel時もworker終了を待ってlate-created Homeを受け取る。通常Task Homeは全削除、Conversation Homeは認証素材だけを削除し、その後にruntime ownershipをreleaseする。

Home準備後の`_spawn`待機も`BaseException`を含む同じResource区間へ入れ、spawn cancelで`auth.json`を残さない。

### R05 — Cursor ACP / CLI prepare cancelでもStageとClaimを回収

Cursor staging prepareを共通のcancellation-safe helperへ集約した。`to_thread` workerが走り始めた後のcancelではworker終了を待ち、late-created staging directoryを削除した後に`_preparing_ids`とruntime ownershipを解放する。ACPとexact CLIの両経路で同じLifecycleを使う。

### R06 — Incident fallbackのtruncated tailを隔離して後続を進める

Fallback append前に末尾を確認する。改行が無いが完全なJSON objectなら区切り改行だけ補う。不完全tailなら`incidents-fallback-quarantine.jsonl`という単一bounded quarantineへraw bytesをhexで保存して元journalから切り離し、`incident_fallback_corrupt_record` markerを正常行として追加してから新しいERRORを追記する。

Replay中に既存のinvalid JSON / non-object行へ遭遇した場合も同じquarantineへ保存し、corruption Incidentを作成して正常な後続行のReplayを継続する。quarantine I/O失敗は`IncidentStoreError`へ統一し、Dive Healthの診断境界をすり抜けない。

### 修正後Probe / Regression

発見用`.tools/adversarial_review_followup_20260908/test_review_probes.py`は「欠陥が成立するとPASS」の逆向きProbeである。修正後は**8 failed / 0 passed**となり、R01-R06の旧欠陥条件はすべて崩れた。

新規`core/tests/test_adversarial_followup_regressions.py`は正しいCurrent契約を8件で固定する。

- Cursor full-turn cleanup後もRecovery原本が残る
- Recovery Root作成不能なら実Workspaceへ書く前に失敗
- approved write中のcancelはwrite確定までResourceを解放しない
- 削除Chatの未同期Memory Sourceが他SessionをBlockingしない
- Codex prepare cancel後にlate auth copyを残さない
- Codex spawn cancel後にprepared authを残さない
- Cursor prepare cancel後にStage / Claimを残さない
- Incident truncated fallbackをquarantineし、後続ERRORをSQLiteへReplayする

### 最終実測

- Core pytest：**517 passed**
  - 90秒Tool上限回避のため全Test Fileを**315 passed + 202 passed / Failure 0**で完走
- World Vitest：**39 files / 245 tests passed**
- TypeScript typecheck：**成功**
- Electron production build：**成功**
- Doctor：**fatal=0 / warnings=1**
  - Cursor：OK
  - Codex：OK
  - Gemini：OK
  - Claude：Current acceptanceで無効のためWARN
- `git diff --check`：**成功**。stderrは既存working-copyのLF→CRLF warningのみ
