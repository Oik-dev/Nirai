# M4 Task調停第2巡・Queue・対象Folder Slice 検証結果

検証日: 2026-09-05

## 判定

**SAFE / Task相談第2巡以降・永続FIFO Queue・`/task @対象フォルダ名`を受入完了**

M4 Codex基準Slice / Task調停第1巡Slice / Cursor ACP基準Slice / Antigravity基準Sliceの既存SAFEを維持したまま、M4に残っていたTask調停第2巡以降、Task Queue、対象Folder指定を実装した。複数回のCursor / Luna独立レビューで検出された追加consult上限、`runtime`内部target境界、named target消失TOCTOUを修正し、2026-09-05の最終再レビューで新規FindingなしのSAFEを確認した。本Sliceを正式SAFEとし、M4全体も残件なしでSAFEとする。

## 実装範囲

### Task相談第2巡以降

- consult応答へ`needs_followup: boolean`を追加し、文章の意味をCoreが推測して「意見が割れた」と判定しない
- 第1巡は従来どおり全参加Residentが順次consultする
- `needs_followup=true`は、これまでの相談に具体的な未解決の意見対立が残り、担当決定前に追加発言が必要な場合だけ返す契約とする
- 単なる情報不足、不安、別案の存在だけでは`needs_followup=true`にしない
- 第1巡で誰かが`needs_followup=true`なら第2巡へ進み、以降は全員がfalseとなった巡で終了する
- 第2巡以降の追加consultは合計最大8ターン。合意判定は全参加Residentが発言した完全な1巡だけで行い、残りTurn数で次巡を完了できない場合は部分巡を開始しない。上限到達時も未解決なら担当を決めずfail-closedする
- 各追加発言にも、それまでの全相談履歴・巡数・実効`volunteer`・`needs_followup`を渡す
- 担当候補は最後の発言時点の実効`volunteer`を正とし、途中で立候補を撤回したResidentは候補から外す
- 複数候補が最後まで残った場合は、最初に有資格立候補した順を維持して決定する
- `agent_work`資格は従来どおりCoreがProvider / Modelで再検証し、Brain自己申告だけでは担当資格を与えない

### 永続Task Queue

- Queue正本を`runtime/task_queue.json`へ追加
- Queueは`active` pre-Agent Task 1件 + `pending` FIFOで保存する
- Queue Fileはtemp write + replaceで原子的に更新する
- 待機上限は32件。超過時は新規Taskをfail-closedする
- Task本文のQueue保存上限は32,000文字。World request受理時にも同じ上限を先行検証し、超過入力でQueue Storeを故障状態へ遷移させない。超過内容をQueue Fileへ部分保存しない
- Store自身もpending 32件上限とpersisted File 8 MiB上限を再検証し、壊れた外部状態から上限を迂回できない
- 先行Taskが相談中またはAgent作業中なら、新しいTaskはbusy拒否せず`queued`としてFIFOへ積む
- `task_update phase=queued`へ`queue_position`を付与し、World再接続時も待機状態を再通知する
- 先行Taskがterminalへ到達したら次のQueue先頭だけを自動起動し、Agent Sessionの同時実行1件という既存安全境界は維持する
- Core停止時はQueue dispatcherを先に停止し、停止中に新しいTaskを開始しない
- Queue待機中も元Chat Sessionの削除 / World Memory Forget、Resident削除 / Brain変更を拒否する

### Queue Crash Recovery

- consultation開始前から当該Taskを`active`として永続化し、最初のawait前のTask Flow予約とDurable Queue状態を両立する
- Core再起動時に`active`だったpre-Agent Taskはpending先頭へ戻し、後続pendingより先に再相談する
- 再起動時には古いWorld request IDを復元せず、Task IDを正本として再開する
- Queue状態のPath・origin Chat Session・target整合を再検証してから復元する
- Queue stateが破損・不正・安全境界外の場合はQueueを推測復旧せず、新規Task受付もfail-closedする。巨大File、不正UTF-8、JSON破損、Path解決異常も復旧境界内で明示エラーへ畳む
- Agent Session作成後、Queue側`active`を消す前にCoreが落ちるCrash窓に備え、再起動時に同じ`task_id`のDurable Agent Sessionが存在すればQueue recordを再実行せず除去する
- これにより「Agentへ昇格済みTaskを再相談・二重実行する」経路を閉じる

### `/task @対象フォルダ名`

- World Chat入力で`/task @ProjectA 依頼文`を解析し、Coreへ`task_request.target="ProjectA"`として送る
- 対象名は`tasks.allowed_dirs`に登録された**外部Root**のbasenameとcase-insensitive一致するものだけを許可する。Nirai内部状態Root `runtime` とその配下は`tasks.allowed_dirs`へ明示登録されていてもnamed target候補から除外する。通常Agent cwdとして`runtime`配下で許可するのは当該Task自身の`runtime/workspace/<task_id>`だけで、Agent Session / Chat Session / Queue正本 / 他Task workspace等へProviderを向けない。日本語等のUnicode Folder名も、実在する外部allowed root basenameとの一致であれば許可する
- 任意Path・相対Path・`..`、`/`、`\\`を入力から解決しない
- 対象名が未知・不正・同名Rootが複数で曖昧、または設定後に対象Folder自体が消えている場合はQueue投入時・Queue復旧時・Task Flow開始時・相談終了後のProvider起動直前に再検証してfail-closedし、削除済みProjectを自動再作成しない
- 既存のProtected Root検証を通すため、Nirai本体`core` / `world`の直接改修禁止は維持する
- 指定なし`/task 依頼文`は従来どおり`runtime/workspace/<task_id>`を作業cwdにする
- named target時もTask metadata正本は`runtime/workspace/<task_id>/task.md`へ置き、実Project直下へNirai管理用`task.md`を混入させない。metadata Rootは`tasks.allowed_dirs`の並び順に依存しない
- Agent Runtimeへ渡す実cwdとTask metadata directoryを分離し、Managerへ直接別metadata directoryを渡す経路も拒否する

## World / Protocol

- `TaskUpdatePayload.phase`へ`queued`を追加
- `queue_position` / `target`を任意Fieldとして追加
- Protocol Parserが`queued` Task Updateを受理し、`working_dir / queue_position / target`の型と`queue_position >= 1`をruntime検証する
- Provider Model単位CapabilityもProtocol Parserで構造検証し、壊れたModel Capability Payloadを型安全として通さない
- ChatBarは`parseTaskCommand()`で通常会話・通常Task・named target Task・入力不正を分離する
- `/task @ProjectA`だけで依頼文が無い場合はWorld側で送信せず入力エラーにする

## 自動回帰

今回の専用回帰で少なくとも以下を固定した。

- consult schemaが`needs_followup`を必須booleanとして扱う
- 第1巡に未解決対立が無ければ追加consultしない
- 明示的な未解決対立だけ第2巡へ進む
- 第2巡で立候補を撤回したResidentを担当候補から外す
- 第2巡で新たに立候補したResidentを候補にできる
- 後続巡のBrain呼び出しに失敗したResidentは、過去巡の`volunteer=true`を残さず最新立候補をfalseへ倒す
- 第2巡以降は最大8追加ターン。3人で残り2ターンしかない場合のように次巡を完走できないときは部分巡を開始せず、未解決のまま担当決定しない
- 9人など1巡自体が8ターンを超える場合は、第1巡後に未解決でも部分的な第2巡を開始せずfail-closedする
- 上限まで完全巡を実行しても未解決ならTask Flowは`failed`となりAgent Sessionを開始しない
- allowed root basenameのcase-insensitive named target解決とUnicode Folder名の許可
- 未知target / `../` / Path separator / 同名Root曖昧 / 削除済みtargetを拒否し、`runtime` / `runtime/workspace` / `runtime/agent_sessions` / `runtime/chat_sessions`等のNirai内部状態はallowed_dirsへ明示登録してもnamed targetとして拒否
- 通常Agent cwdへ`runtime`内部Pathや他Task workspaceを直接渡す経路も拒否し、自Task自身の`runtime/workspace/<task_id>`だけを例外として許可
- named targetの相談開始後に対象Projectを削除した場合、担当決定後・Provider起動直前の再検証でfailedとなり、Agent Sessionを開始せずFolderも再生成しない
- named targetの実cwdとTask metadata directoryを分離し、Project直下へ`task.md`を作らない
- Queue active + pending FIFOの永続round-trip
- Queue duplicate task IDを拒否
- Queue本文32,000文字超をrequest入口とStore双方で拒否し、超過requestでQueue Storeをsticky failureへしない
- Queue Store自身がpending 32件超・8 MiB超・不正UTF-8・親Directory作成失敗を明示エラーへ畳む
- 壊れたQueue Fileを勝手に上書きしない
- consultation開始前の2件目TaskをQueueへ入れる
- consultation終了後にQueue先頭を自動開始する
- Crash時のactive pre-Agent Taskをpending先頭へ復旧する
- 復旧時に古いWorld request IDを再利用しない
- Agent Sessionへ昇格済みtask_idをQueueから除外して二重実行しない
- named target Taskを実WebSocket Protocol経由でProject cwdへ接続する
- unknown named targetを実WebSocket Protocol経由で拒否する
- World Parserが`queued` Task Updateを受理し、新規optional Fieldの不正型・不正queue positionを拒否する
- Model単位Agent Capabilityの不完全PayloadをWorld Parserが拒否する
- World Chat入力が`/task @ProjectA 依頼文`を構造化する

## Holo自前レビュー（2026-09-05）

外部レビュー前に、Crash / persistence / safety / protocol contractを中心に差分を再レビューし、以下を追加修正した。

1. `task.md` metadata先が`tasks.allowed_dirs[0]`へ暗黙依存していたため、設定順によって外部Projectへ混入し得る境界を修正。metadata先を`runtime/workspace/<task_id>`へ固定し、Manager直接呼び出しでも外部metadata指定を拒否
2. 内部既定Root `runtime/workspace`またはその配下をnamed targetへ選べ、他Task workspaceを横断するcwdになり得たため、内部Task workspace subtree全体をnamed target候補から除外
3. Queue待機中またはCore再起動前にnamed Projectが削除された場合、実行・復旧時にFolderを再作成し得る経路を修正。外部targetはenqueue時だけでなくdispatch / restore時にも実在Directoryを再検証し、消失時はfail-closed
4. 第1巡で立候補したResidentが第2巡でBrain失敗した場合に古い`volunteer=true`が残る問題を修正。失敗した最新巡では候補から外す
5. 32,000文字超TaskがStore validation errorを起こしてQueueをsticky failureへし得たため、request入口で先行拒否
6. Queue pending上限をServerだけでなくStore load/saveにも実装し、persisted File 8 MiB上限、不正UTF-8 / JSON破損、Directory作成失敗を明示エラー化
7. World `task_update` parserが追加optional Fieldを型検証していなかったため、`working_dir / queue_position / target`を検証し、不正queue positionを拒否
8. Model単位Agent Capabilityのruntime guardが未実装だったため、不完全Capability PayloadをWorld型として受理しないよう修正
9. 上位正本`Nirai_基本設計.md`に旧「Say / WhisperからTask」記述が残っていたため、現行`/task`明示入口・Queue・named target契約へ同期

上記修正後、Holo自前レビューでは新しいBlocking Findingを残していない。独立したCursor / Lunaレビューを正式SAFE判定前の外部Gateとする。

## Cursor / Luna独立レビュー対応（2026-09-05）

Cursor / LunaからNEEDS FIXが返ったため、指摘を現行Working Treeへ再照合した。

- Cursor P2「追加8ターン上限が巡途中で切れ、未発言者を無視して合意扱いする」は**有効な新規Finding**として修正した。追加巡を開始する前に残りTurn数で全参加Resident分を完走できるか確認し、部分巡を開始しない。完全巡終了時も未解決かつ上限到達なら担当決定せずfail-closedする。3人で第4巡が2人だけになるケース、9人で第2巡自体を完走できないケース、通常Task FlowでfailedとなりAgent Sessionを開始しないケースを回帰追加した
- Cursor P2「Queue待機中にnamed targetが消えるとdispatchが再生成する」は、レビュー取得前のHolo最終修正で既に解消済みだった。現行`_start_task_flow()`は`target_name`を`_run_task_flow()`へ渡し、実行開始時に`named_working_dir()`で実在性を再検証する。専用回帰`test_queued_named_target_deleted_before_dispatch_fails_without_recreating_project`も存在するため追加修正不要
- Luna P2「消えたtarget Folder再生成」も同じ現行修正により既解消。`named_working_dir()`自体も`target.is_dir()`を要求し、Queue restoreでも再検証する
- Luna P2「Core 270/271件の文書不一致」はレビュー対象Snapshotが古く、現行文書はレビュー受領時点で既に281件へ更新済みだった。今回の追加回帰後は最新実測284件へ再同期する

したがって今回新たにコード修正したBlocking対象はCursorの追加consult P2のみ。named target系2件と件数ずれ1件は現行Treeでは再現しない旧Snapshot指摘として閉じる。本Sliceは修正後のCursor / Luna再レビュー待ちを維持する。

## 第2回 Cursor / Luna独立レビュー対応（2026-09-05）

修正後再レビューで前回Findingは閉じた一方、LunaからP1×1、CursorからP2×1が新たに検出されたため、両方を有効Findingとして修正した。

- Luna P1「`tasks.allowed_dirs`へ`runtime`や`runtime/agent_sessions`を登録するとnamed targetへ選べる」: `AgentWorkspacePolicy`へNirai内部状態Root `runtime`を予約境界として導入し、その配下をnamed target候補から全面除外した。さらに通常Agent cwd resolverでも`runtime`配下は当該Task自身の`runtime/workspace/<task_id>`だけを許可し、Agent Session / Chat Session / Queue正本 / 他Task workspace等への直接cwd指定を拒否する。Cursorの内部stagingは通常Task cwdと混同せず、`runtime/workspace/.cursor-stage-<session>`直下だけを許す専用内部経路へ分離した
- Cursor P2「相談開始後にnamed targetが消えると`start_session()`で再生成される」: 担当Resident決定後・Provider起動直前に`target_name`から`named_working_dir()`を再実行し、相談前に確定したcwdとの一致と実在性を再確認する。相談中にProjectを削除した回帰ではTaskはfailed、Agent Session未作成、Folder未再生成を固定した

これによりnamed targetの実在確認はenqueue / Queue restore / dispatch後Task Flow開始 / 相談終了後Provider起動直前の複数境界で行う。今回もM4全体は自己SAFEへ上げず、Cursor / Luna修正後再レビュー待ちを維持する。

## 第3回 Luna独立レビュー対応（2026-09-05）

Luna再レビューで、Serverの最終`named_working_dir()`確認後からManagerのcwd解決までの短いTOCTOU窓がP1として検出された。Server確認後に外部Projectが削除された場合、旧`resolve_working_dir()`は明示cwdにも`mkdir(parents=True, exist_ok=True)`を行うため、Manager自身が削除済みProjectを再生成してAgent Sessionを開始できた。

このFindingは有効として、cwd resolverの生成責務を変更した。

- 対象未指定時だけ`runtime/workspace/<task_id>`を生成してよい
- 明示cwdは既存Directoryであることを必須とし、Manager / Adapterの再検証時に消失していてもmkdirしない
- Server最終確認後、Manager境界へ入る直前にnamed Projectを削除する固定TOCTOU回帰を追加し、Project未再生成・Agent Session未作成・Task failedを確認する
- Manager単体でも、明示外部cwdが消失済みならFolderを再生成せず`AgentSafetyError`で拒否する回帰を追加する
- Cursor / AntigravityがProvider開始時に明示cwdを再検証する既存経路も、同じnon-creating resolverを通るため、後段再検証で外部Projectを復活させない
- 追加の自己点検で、Provider開始後にもCursor staged applyの`_atomic_copy_file()`とAntigravity local writeの`_atomic_write_text()`が`parent.mkdir(parents=True)`を持ち、Master承認後に外部Project Rootが消えた場合は同じ受入条件を再び破り得ることを確認した。共通`AgentWorkspacePolicy.prepare_write_path()`へ寄せ、**既存Task workspace Root自体は絶対に生成せず**、必要な子DirectoryだけをRootから1段ずつ`parents=False`相当で作る。Rootが途中で消えれば即fail-closedする
- CursorはMaster承認callback中に外部Project Rootを削除してstaged createを反映させる回帰、AntigravityもApproval中にRootを削除してlocal writeを反映させる回帰を追加し、いずれもRoot未再生成を固定した

この修正後、最終独立再レビューで新規FindingなしのSAFEを確認した。

## 最終独立再レビュー（2026-09-05）

前回P1だった「Serverの最終named target確認後に外部Projectが消えるとManagerが再生成できる」競合は解消済みと確認された。明示cwdは既存Directoryだけを受理し、対象未指定の自Task workspace以外をManagerが生成しない。Server最終確認後の固定TOCTOU回帰、Manager単体の消失cwd拒否回帰、Provider開始後のCursor staged apply / Antigravity local writeのRoot未再生成境界も確認され、`runtime`内部状態のnamed target禁止も維持されている。

最終判定は**SAFE / 新規Findingなし**。独立レビュー実測はCore pytest 291 passed、TypeScript typecheck成功、`git diff --check`成功。World側は今回の最終修正で変更していないため既存39 files / 219 tests passedを維持し、Production Buildも既存成功記録を維持する。これを外部Gate通過として、本SliceおよびM4全体を正式SAFEとする。

## 現行検証

- Core pytest: **291 passed**
- Queue / Safety / AgentRuntimeManager / Agent Server Protocol targeted: **77 passed**
- Safety / Manager / Server / Cursor / Antigravity target-disappearance boundary targeted: **120 passed**
- World Vitest: **39 files / 219 tests passed**
- ChatInput + ProtocolParser + AgentTaskPanel targeted: **3 files / 37 tests passed**
- TypeScript typecheck: **成功**
- World Production Build: **成功**

既存のCodex / Cursor ACP / Antigravity実Provider Live Smokeは各Sliceの検証記録を正とする。本SliceはProvider AdapterそのものではなくTask orchestration / Queue persistence / target routingの変更であり、今回の自動回帰は既存Agent Runtime Manager / Protocol回帰を含めている。

## 最終状態

本Sliceの修正後再レビューは完了し、新規Blocking Findingはない。Task調停第2巡以降、永続FIFO Queue、named target、安全なmetadata分離、target消失時のfail-closed、World `queued`表示と`/task @folder`入力契約まで受入済みとする。

**Slice判定: SAFE**

**M4全体判定: SAFE**
