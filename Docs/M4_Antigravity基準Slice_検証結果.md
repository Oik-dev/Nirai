# M4 Antigravity基準Slice 検証結果

検証日: 2026-09-05

## 判定

**SAFE / 2026-09-05 MasterがInteraction Create応答喪失時の未知stored Interaction即時削除不能をGemini Provider由来の残余制約として受容**

M4 Codex基準Slice / Task調停第1巡Slice / Cursor ACP基準Sliceの既存SAFEを維持したまま、Gemini会話Driverとは独立したWork用`AntigravityAgentAdapter`をNirai共通Agent Runtimeへ追加した。

Holo側レビューと自動回帰、World回帰、Production Build、実Antigravity Positive Smokeまでは完了している。2026-09-05のCursor ExHigh初回独立レビューはNEEDS FIXで、Managerの一般Event string cap 12,000文字とAntigravityのDiff review上限24,000文字の不一致により、12,001〜24,000文字のDiffがpersist時だけclipされ、Master未確認の末尾を`approve_once`後に適用できるP1を1件検出した。

P1は、`pending_approval`かつ`operation_id`を持つ`file_change`を完全なreview contextとして扱い、Managerの32,000文字Event総量内では個別12,000文字capを適用せず完全保存するよう修正した。完全保存できないpayloadはpersist / broadcast前にfail-closedし、承認待ちを開かない。15k〜20k級Diffを実Manager経由で通し、persisted DiffとAdapter生成Diffの完全一致、および`approve_once`後のFile全文一致を固定する回帰を追加した。Grok修正確認ではこのP1は解消済みと判定された。

続くLunaレビューでは、World Approval UIが明示的な空・不正`options`を「制限なし」と解釈するP2と、Initial Interaction Create応答喪失時のremote resource回収P2を検出した。Approval UIは`undefined`の旧Payloadだけを後方互換とし、明示空配列・非配列・不正要素混入をfail-closedするよう修正した。remote resource回収は公式API契約を再確認し、EnvironmentをInteractionsと同時に暗黙作成せずEnvironments APIで先行作成し、Session固有markerによるCreateEnvironment応答喪失復旧を追加した。Interaction Create応答喪失時も既知Environmentを削除対象にできる。一方、現行公式Interactions APIにはCreate冪等キーとInteraction一覧取得APIがなく、未知Interaction IDのstored recordをNiraiだけで100%復旧・即時DELETEすることは保証不能であるため、この残余制約を明文化し正式SAFEにはしていない。

その後のM4総合レビューでは追加P2として、(1) Provider一覧のGemini `agent_work`表示がModel条件と不一致、(2) pre-Agent Taskの停止・失敗時に`consulting`通知が幽霊状態で残り得る、(3) 上記Interaction応答喪失制約の継続確認、(4) 検証記録の実測値と独立レビュー環境で確認できた範囲の混同、を指摘された。(1)はProvider既定ModelのCapabilityを`_provider_can_agent_work()`で判定し、Gemini Modelごとの実効Capabilityも返すよう修正した。(2)はTask Flow Cancel時に必ず`cancelled` terminal updateへ置換し、相談Brain失敗時のWorld警告送信失敗をTask本体へ伝播させないよう修正した。(4)は独立レビュー時の確認値と修正後Treeの実測を分離して記録する。

さらにLuna再レビューでCursorの`artifact` Capabilityが実装と不一致と判明したため、Cursor Adapterへ実装済みCapabilityを明示し、staging中に意図的に抑止しているArtifactは`false`とした。Managerの未宣言Adapter fallbackも「全Capabilityあり」から空集合へ変更し、今後のAdapter追加でも未宣言機能を推測で表示しないfail-closed契約へ統一した。Cursor再レビューのEnvironment List指摘については、Google公式のAPI Referenceが`id` / `page_size`、Managed AgentsガイドのREST例が`environment_id` / `pageSize`と相互に食い違っていることを確認したため、実Smokeで成立済みの`page_size` requestは維持しつつ、response IDは`id`と`environment_id`の両方を受理する互換境界へ修正した。paginationを含む両schema混在回帰を追加した。

## 実装範囲

- `AntigravityAgentAdapter`を追加し、`AgentRuntimeManager`既定AdapterへGemini Work Runtimeとして登録
- 会話用`GeminiDriver`とは別経路とし、通常`gemini-*` Modelは会話可能でも`agent_work`対象外
- `antigravity-*` Modelを選択したGemini ResidentだけTask担当資格を持つ
- Google Gemini Interactions APIのManaged Agentをbackground / stateful Sessionとして利用
- Google remote environmentは`network=disabled`
- remote Toolは`code_execution`とNirai所有Custom Functionだけを明示し、基準Sliceでは`google_search` / `url_context` / MCPを渡さない
- Google remote filesystemはscratch環境とし、ローカルTask workspaceと同一Filesystemとして扱わない
- Core権限のlocal shell / subprocessはAntigravity Adapterから起動しない
- remote `code_execution_call / code_execution_result`をNirai共通`command_execution` Eventへ正規化し、`execution_scope=remote_sandbox`を付与
- private thoughtはNirai Eventへ保存・表示しない
- ローカルTask workspace操作はNirai Custom Function Bridgeへ限定
  - `nirai_list_files`
  - `nirai_read_text_file`
  - `nirai_write_text_file`
  - `nirai_edit_text_file`
  - `nirai_delete_file`
  - `nirai_ask_master`
  - `nirai_submit_plan`
- File Write / Edit / DeleteはNirai側でDiff / Path / Hashを生成し、既存Master File Change Approvalへ接続
- Question / Planは既存Nirai Master UIへ接続
- Provider継続Turnでは`previous_interaction_id + environment_id`を使用しつつ、Interaction単位の`tools` / `system_instruction`を毎Turn再指定
- `incomplete`は同一environmentで最大8回まで継続
- background実行の`store=true`で保持され、IDを取得できたInteractionをSession終了時に明示DELETE
- remote environmentはEnvironments APIで先行作成し、Create応答喪失時はSession固有markerからList / GetでIDを復旧する。Google公式文書間のschema差異に備え、Environment resource IDは`id` / `environment_id`の両方を受理し、`next_page_token` paginationも処理する。Interaction開始前にEnvironment IDを確定し、Session終了時またはInteraction Create応答喪失時も明示DELETEを試行
- Interaction Create応答そのものを失ってID不明になったstored Interaction recordは、現行公式APIに冪等Create / List Interactionsが無いためNiraiだけで完全回収を保証できない。Sessionは失敗扱いとし、未知recordを完全cleanup済みとは記録しない
- cleanup失敗は成功扱いにしない

## Antigravity安全境界

### remote command

remote `code_execution`はGoogle側sandboxだけで実行する。Nirai local Task workspaceへ直接書き込む権限は持たせない。

- `network=disabled`
- `execution_scope=remote_sandbox`
- Event上のcwdは`Google Antigravity remote sandbox`
- Command本文 / 出力はbounded
- Core権限のlocal command bridgeは実装しない

### local read / list

- PathはTask `working_dir`相対だけ
- 絶対Path、`..`によるescape、Task外解決を拒否
- symlink / junction等で解決先がTask workspace外ならblocked扱いまたは拒否
- Text ReadはUTF-8かつ512 KiB以下
- Listは件数と出力文字量をbounded

### local write / edit / delete

- `task.md`は変更禁止
- Master Approval前に実Workspaceへ変更しない
- File Change提案は同一`operation_id`で`file_change pending_approval`と`approval_request`を相関
- File内容のFingerprintをApproval前に固定
- Approval待ち中にFile内容が変われば適用拒否
- Approval待ち中にcanonical Path topologyが変われば適用拒否
- `AgentWorkspacePolicy.assert_write_path`を適用直前にも再確認
- 既存Fileが非UTF-8または512 KiB超で旧内容を完全レビューできない場合、`nirai_write_text_file`による上書きをfail-closed
- DiffがMaster review上限を超える場合は変更を小分けするようfail-closed
- `approve_once`だけ適用し、`reject`は未適用、`cancel`はAgent Session停止
- temp File + `os.replace`でText Writeを反映

### Provider暴走 / 予算

- 1 TurnのNirai Function Call最大32件
- 1 SessionのNirai Function Call最大128件
- 1 SessionのInteraction最大128件
- `incomplete`連続継続最大8回
- Session全体の既存Agent Runtime timeoutは維持
- remote cancel / environment cleanup / Interaction cleanupは有限・明示失敗境界を持つ

## レビュー状態

Cursor ExHigh初回独立レビューは2026-09-05に完了し、P1を1件検出してNEEDS FIXとなった。Findingは「Masterに見えないFile Diffを`approve_once`で適用する」で、`ANTIGRAVITY_DIFF_LIMIT=24,000`以内のDiffでもManagerの`_EVENT_STRING_LIMIT=12,000`によりpersisted `file_change`だけがclipされる境界不一致だった。

修正後は、Approval相関の`file_change pending_approval`を完全保存できる場合だけpersist / broadcastし、一般Eventの個別string capから除外する一方、32,000文字のEvent総量上限は維持する。完全保存できない場合は`AgentRuntimeManagerError`で停止し、後続`approval_request`へ進まない。このP1はGrok修正確認で解消済み。

Luna再レビューの追加P2については、Approval `options`のfail-closed化は実装修正済み。続く修正後再レビューでは、Cursor `artifact` Capability明示・未宣言Capability fail-closed・Environment Listの`id` / `environment_id`両schema互換とpaginationを含む今回差分は**SAFE**と判定され、Cursor ACP SliceもSAFE維持が確認された。remote response-lossはEnvironment回収まで修正確認済みだが、未知Interaction IDのstored record完全削除は現行Provider API契約上保証不能な残余制約として残るため、Antigravity Slice自体は正式SAFEにはしていない。

## 自動回帰

Antigravity専用 / Manager回帰で少なくとも以下を固定した。

- Environments APIで`network=disabled` EnvironmentをInteraction前に先行作成し、Initial Interactionへ既知Environment IDを渡す
- CreateEnvironment応答喪失時にSession固有inline markerからList / GetでEnvironment IDを復旧し、終了時にDELETEする
- Initial Interaction Create応答喪失時も既知EnvironmentをDELETE対象にしてSessionを失敗扱いにする。未知Interaction IDのrecord削除不能はProvider残余制約として明示する
- Toolはremote `code_execution` + Nirai Custom Functionだけ
- `google_search`非搭載
- thought非Event化
- remote code executionのrunning / completed Event正規化
- code call / resultが別pollでも同じCommand情報を保持
- environment IDが後から付いてもProvider Sessionへ保存
- `incomplete`が同一environment / previous interactionで継続
- 継続TurnでもNirai tools / system instructionを再注入
- terminal responseにenvironment IDが無ければfail-closed
- Master Approval前にFile未反映
- `approve_once`後だけFile反映
- rejectで実Workspace不変
- Approval待ち中の外部File変更を競合拒否
- `task.md`変更禁止
- Task外Path拒否
- binary既存Fileの不完全Review overwrite拒否
- Read / ListはMaster Approval不要かつTask workspace内限定
- remote commandはMaster local command Approvalを要求せずremote sandbox Eventのみ
- 未定義`nirai_run_command` Custom Functionはfail-closedし、local processを起動しない
- Question option / free text mappingと上限
- Plan revise契約
- Plan cancelでAgent Session cancel
- 過大Diff拒否
- 1 Turn 33件のFunction Callを拒否
- cleanup失敗を成功扱いにしない
- 通常Gemini Modelは`agent_work=false`
- Provider一覧のGemini既定Modelも`agent_work=false`とし、取得済みGemini Modelごとに実効Capabilityを返す
- `antigravity-*` Modelだけ`agent_work=true`
- Gemini Agent Capabilityを`approval / question / plan / file_diff / command_result`へ限定
- Cursor Capabilityは`approval / question / plan / todo / subagent / file_diff / command_result`へ限定し、staging中に意図的に抑止する`artifact`をfalseにする。未宣言AdapterのCapabilityはManagerで空集合としてfail-closed
- pre-Agent Task Cancel時は保存済み`consulting`を`cancelled` terminal updateへ置換し、再接続時に幽霊状態を残さない
- 相談Brain失敗時のWorld警告送信失敗はTask Flow本体へ伝播させない
- 実Managerを通したFile Approval round-trip
- World Approval UIは`options`未指定の旧Payloadだけを後方互換とし、明示空配列・非配列・不正要素混入をfail-closedする
- 12,000文字を超え24,000文字以下のFile Diffを実Manager経由でpersistし、Adapter生成Diffとの完全一致と末尾行保持、`approve_once`後のFile全文一致を固定
- 32,000文字Event総量へ完全保存できない`file_change pending_approval`はEventを1件もpersistせずfail-closedする回帰を固定
- Event broadcast中の高速ResponseをProvider wait前でも失わない共通Manager回帰
- Cursor Provider表示で`artifact=false`が最終Payloadまで維持される回帰
- Capability未宣言AdapterはManagerで空集合となる回帰
- Environment Listが`environment_id` / `id`両schemaを混在して返しても`next_page_token`を辿って全IDを復旧できる回帰

## 現行検証

独立レビューと修正後検証を混同しない。

- Luna M4総合レビュー時のWorking Tree: Core pytest **254 passed**。Luna環境ではWorld Vitest / Production Buildは`spawn EPERM`により独立未確認
- 最新修正後再レビュー: targeted **90 passed**、Core pytest **259 passed**、TypeScript typecheck **成功**、`git diff --check` **成功**。同レビュー環境ではWorld Vitest / Production BuildはNode worker / esbuildの`spawn EPERM`により独立再現できず、コード失敗としては扱われていない
- Holo最新修正後Working Tree: Core pytest **259 passed**
- 最新targeted（Agent Server Protocol + Antigravity + Cursor + AgentRuntimeManager）: **90 passed**
- Antigravity + AgentRuntimeManager targeted（前段確認値）: **44 passed**
- Holo修正後World Vitest: **39 files / 212 tests passed**
- AgentTaskPanel専用: **7 tests passed**
- TypeScript typecheck: **成功**
- Holo修正後World Production Build: **成功**
- `git diff --check`: **成功**（LF→CRLF warningのみ、whitespace errorなし）

## 実Antigravity Positive Smoke

最終cleanup変更後のTreeから実Google Antigravityへ1 Taskを実行した。

```text
Environments APIでnetwork disabled Environmentを先行作成
→ 既知Environment IDをInitial Interactionへ指定
→ Antigravity Agent work
→ remote code_execution
→ REMOTE_ANTIGRAVITY_OK
→ nirai_write_text_file(ANTIGRAVITY_SMOKE.txt)
→ Nirai file_change pending_approval
→ Nirai approval_request
→ Master approve_once
→ local Task workspaceへ NIRAI_ANTIGRAVITY_OK\n 反映
→ completed
→ remote environment cleanup
→ stored Interaction cleanup
```

確認値:

- status=`SAFE`
- run_state=`completed`
- 実File内容=`NIRAI_ANTIGRAVITY_OK\n`
- File Change Approval=**1回**
- Decision=`approve_once`
- unexpected Master request=**0**
- remote command Event=**2件**（running / completed）
- remote command scope=`remote_sandbox`のみ
- Task外Path Event=**0**
- Session終了cleanup APIはエラーなし

実Smokeはremote Commandとlocal File Bridgeを同一Task内で実際に通しており、Environments API先行作成、既知Environment IDでのInitial Interaction、Function Result継続Turn、tools / system instruction再注入、Master Approval、local反映、terminal cleanupを現行Google APIが受理することを確認した。なお初回SmokeではInteractions API Reference上に見える`labels`を試したところ、実Gemini APIが「Gemini Enterprise Agent Platform限定」とHTTP 400で拒否したため即時撤去し、二回目SmokeでSAFEを確認した。

## 最終判定

前回P1と、その後のLunaレビュー／M4総合レビューでNirai側に見つかった修正可能なP2は現行Working Treeへ反映済み。最新修正後再レビューでは、Approval `options`、GeminiのModel依存`agent_work`表示、Cursorの`artifact=false`、未宣言Capability fail-closed、pre-Agent Task terminal整理、Environment Listの`id` / `environment_id`両schema互換を含む今回のCapability / Environment修正差分は**SAFE**と判定された。Cursor ACP SliceもSAFE維持。remote response-lossはEnvironments API先行作成・marker復旧・既知Environment cleanupまで修正確認済みで、未知Interaction IDのstored record即時削除不能だけがProvider残余制約として残った。

2026-09-05、MasterはInteraction Create成功後に応答だけ完全喪失してID不明となったstored InteractionをNiraiから即時DELETEできない可能性について、現行Gemini APIに一覧取得・Create冪等キーが無いProvider由来の残余制約として正式に受容した。Nirai側で実装可能なEnvironment先行作成・marker復旧・既知Environment cleanup・既知Interaction cleanup・失敗扱いは完了しているため、この残余制約はNirai実装のBlockingとしない。最新修正後再レビューでCapability / Environment差分はSAFE、Cursor ACP Slice SAFE維持も確認済みであることから、Antigravity基準Sliceを正式に**SAFE**とする。M4全体は相談第2巡以降、Queue、`/task @対象フォルダ名`が未完了のためNEEDS FIXを維持する。

重点確認対象:

- Interactions API lifecycle / stateful continuation
- remote `code_execution`とlocal Function Bridgeの分離
- network disabled / 外部Tool非搭載
- File Approval相関とManager pending lifecycle
- 高速Master Response / Cancel / Timeout / Core stop race
- Path escape / symlink / junction / TOCTOU
- existing binary / large File fail-closed
- remote environment / Interaction cleanup
- Event / Session payload上限
- Model-aware `agent_work`
- Codex / Cursor / Task調停既存SAFEへの回帰

残余Provider制約はMaster受容済み。今後Google APIが一覧取得・冪等Create等を提供した場合のみ、未知Interaction回収を追加改善候補として再評価する。現時点では本SliceのBlockingではない。

## 後続 / 対象外

本基準Sliceには以下を含めない。

- Task相談第2巡以降
- Task Queue
- `/task @対象フォルダ名 ...`
- Claude Agent Runtime（追加有料依存を避けるMaster判断で延期）
- AntigravityへのGoogle Search / URL Context / MCP開放
- AntigravityからのCore権限local shell
- 複数Agent同時実行
- Nirai self-build

M4 Codex基準Slice / Task調停第1巡Slice / Cursor ACP基準Sliceの既存SAFEは本Sliceのレビュー状態とは独立して維持する。
