# M4 Cursor ACP基準Slice 検証結果

検証日: 2026-09-05

## 判定

**SAFE**

Codex基準Slice / Task調停第1巡Slice SAFEの後続として、Cursor ACPをNirai共通Agent Runtimeへ接続した。Holo独立レビュー後のCursor ExHigh初回レビューではP1×1 / P2×2を検出し、3件すべて修正した。修正後のHolo再レビューはSAFE、2026-09-05のCursor ExHigh修正後再レビューも前回3件の修正確認・新規FindingなしでSAFE。二重レビュー運用の条件を満たしたため、本Sliceを正式SAFEとする。

## 実装範囲

- `CursorAcpAdapter`を追加し、AgentRuntimeManagerの既定Adapterへ`cursor`を登録
- Cursor Residentを`agent_work`有資格ProviderとしてTask調停から選択可能にした
- 会話用Cursor Ask Driverとは分離し、Work時だけ`agent acp`をstdio / JSON-RPCで起動
- 現行実機ACPの`initialize → authenticate(cursor_login) → session/new → session/prompt`を使用
- Cursor固有Session IDはCore内部だけで保持し、WorldへProvider固有Protocolを露出しない
- `agent_message_chunk`は最終`assistant_message`へ正規化し、`agent_thought_chunk`は保存・表示しない
- Tool / Command / Plan / Todo / SubAgent等をNirai共通Agent Eventへ正規化
- `cursor/ask_question`を既存Master Question UIへ接続
- `cursor/create_plan`を既存Plan承認UIへ接続
- `session/request_permission`を既存Approvalへ接続し、Providerが実際に提示したDecisionだけWorld UIへ表示
- WebFetch / MCPは本SliceではCursor CLI Permissionでdenyし、ACP側へ外部Tool Permissionが来た場合もNirai側でreject
- Cursor Questionは公式ACP契約どおり選択式として扱い、`allow_free_text=false`。単一選択はRadio、複数選択だけCheckbox。Codex等の既存自由入力は既定維持

## Cursor固有のFile変更境界

現行CursorはAgent workspace内のFile編集を必ずしもACP Permission Requestへ送らず、通常Agent Security上もworkspace編集はApprovalなしで即時保存され得る。実Cursor直Smokeでも、実Task workspaceへ直接ACPを向けた場合にFileがMaster Approvalなしで生成されることを確認した。

このためCursor Adapterは実Task workspaceを直接cwdにせず、次の**staging方式**へ変更した。

1. 実Task workspaceの初期SnapshotをHash化
2. `runtime/workspace/.cursor-stage-<agent_session_id>`へTask workspaceを複製
3. Cursor ACPのcwd / session cwdをstagingへ固定
4. Cursorはstaging内だけを通常どおり編集
5. Cursor turn終了後、Provider Process treeを停止してstagingを静止
6. Niraiが初期Snapshotとstagingを比較し、全変更FileのManifest / Diffを生成
7. 同一`operation_id`の`file_change`を先に永続化
8. Nirai所有の`approval_request`をMasterへ表示
9. `approve_once`後だけNirai自身が実Task workspaceへ反映
10. `reject` / `cancel`なら実Task workspaceを変更しない
11. 反映直前に実Task workspaceを再Hashし、作業中に外部変更が入っていれば適用拒否
12. 複数File反映の途中でI/O失敗した場合は、事前Backupから既反映Fileをrollback
13. Cursor Credential HomeとstagingをSession終了時にcleanup

この方式により、Cursor標準の「workspace内Editは即時反映」という性質をNirai実Task workspaceのMaster Approval境界から切り離した。

### staging安全制約

- `.git` / `.cursor`はstagingへ複製しない
- `task.md`はTask metadataとして変更適用禁止
- 初期Task workspaceおよびCursor終了後stagingにsymlink / junctionがあれば拒否
- staging Snapshotは最大20,000 files / 1GB
- 1回の承認対象は最大50 changed files
- File Change Approval Contextは全変更Pathが必ず表示可能なManifestにする
- Diff込みManifestが約24kを超える場合はDiffを落として全Pathを残す
- DiffなしでもManifestが安全な1 Event上限へ収まらなければTaskを分割するよう明示失敗
- text diffはUTF-8 / 1MB以下のみ。binary等はPath + change typeで提示
- 実反映はtemp File + `os.replace`を使用
- 複数File apply前に全変更元をstaging内rollback領域へBackupし、途中失敗時は元状態へ復旧する

## Cursor Home / Secret / Tool隔離

- Sessionごとに`runtime/cursor_agent_homes/<agent_session_id>`を作成
- 既存Cursor環境から`agent-cli-state.json`だけを複製
- User Rules / Skills / Projects / History / MCP設定は複製しない
- `CURSOR_CONFIG_DIR` / `USERPROFILE` / `HOME`をSession専用Homeへ固定
- `agent-cli-state.json`はWindowsでACL継承を外し、現在UserだけへFull Controlを付与
- 親Coreの環境変数を丸ごと継承せず、`GEMINI_API_KEY`等の無関係Secretを子Processへ渡さない
- Cursorの`cursor_login` ACP認証はWindows AppData側の既存Login経路を必要とする実機挙動を確認したため、`LOCALAPPDATA` / `APPDATA`はProvider内部認証用として現User値を維持する
- その代わりCursor Agent Tool側のCLI Permissionで実User Home、Nirai本体、他Task workspace、Credential Home等へ`Read / Write` denyを追加
- 実Task workspace自体もCursor staging Processから`Read / Write` denyし、Master Approval前の直接変更を二重に防ぐ
- `Write(task.md)`をdeny
- staging / Credential Home残留は次回Cursor Agent起動前に棚卸しcleanupする
- ACP client close、Process tree停止は有限時間化し、`taskkill → wait → terminate → kill`へ縮退する

完全なWindows OS sandboxを新規実装したわけではない。MasterがShell Commandを明示承認した場合の外部Side Effectまでstagingが無効化するものではなく、Command Approvalは従来どおり重要な安全境界として維持する。

## Model境界

会話用`cursor-agent models`のModel IDと、ACP `session/new.configOptions.model`は同一Catalog表現ではないことを実機確認した。

例として現行ACPはGrok 4.6を`grok-4.6[effort=high,fast=true]`の1候補として公開し、Extra High等の別Reasoning configは公開しない。一覧外の`grok-4.6[effort=xhigh,...]`を`session/set_config_option`へ渡すと`Invalid model value`で拒否される。

そのためNiraiは、Residentで選択したModelをModel名だけで曖昧一致させない。

- ACP optionと完全に対応できるCLI Model IDだけ明示設定
- 例: `cursor-grok-4.6-high-fast` → ACP high/fast option
- `cursor-grok-4.6-xhigh`等、現行ACPが正確に表現できない強度は**fail-closed**
- Extra HighをHighへ黙ってdowngradeしない
- 別`thought_level`指定が要求されているのにACPがそのconfigを公開しない場合も無視せず失敗
- Model未指定はACP側のAutoを利用可能

ACP側が将来Reasoning / Model variant選択を正式公開した時点で対応範囲を拡張する。

## 自動回帰

Cursor Agent Runtime専用回帰で少なくとも以下を固定した。

- Private thoughtをEvent化しない
- Cursor Tool / Command / File変更Eventの共通正規化
- Task外PathをMaster UIへ出す前にfail-closed
- File Change PermissionでPath不明ならfail-closed
- 外部Web / MCP PermissionをMasterへ転送せずbaseline deny
- Provider Permission optionは`optionId`文字列を意味として決め打ちせず、ACP `kind=allow_once / allow_always / reject_once`を優先してNirai Decisionへ対応。legacyな`allow-once / allow-always / reject-once` optionIdも互換認識し、reject option不在時は任意optionへfallbackせず`cancelled`でfail-closed
- Providerが持たないApproval DecisionをWorld UIへ表示しない
- Questionのmultiple-choice契約、free text無効化、Plan bridge
- Cursor Agent HomeへAuth Stateだけ複製し、無関係Secret環境を継承しない
- stale Credential Home cleanup
- staging承認前は実Task workspace不変
- staging rejectで実Task workspace不変
- 承認後だけstaging変更を実Task workspaceへ反映
- 実Task workspaceがCursor作業中に変わった場合は競合拒否
- `task.md`変更を適用しない
- staging apply途中失敗をrollbackして元Fileへ戻す
- Master review対象は停止後stagingからSession Credential Home配下の凍結review bundleへコピーし、承認後はlive stagingではなく凍結bundleから適用。approval待ち中にstagingまたはreview bundleが変化した場合は適用拒否
- 大量Diffでも全changed pathをApproval Manifestに残す
- Path Manifest自体がEvent安全上限へ収まらない場合は適用拒否
- `status=pending_approval`かつ`operation_id`付きFile ChangeはMaster安全判断ContextとしてManagerの通常Session 2MB詳細予算から保護し、残量不足で後半Pathだけ欠落させない
- Cursor Brain Model IDをACP modelへ曖昧変換せず、Reasoning downgradeを拒否
- AgentRuntimeManager既定CapabilityとしてCursorを`agent_work`可能にする
- Sessionが`cancelling`へ入った後の遅延Approval / Question / Plan応答を拒否し、`waiting_for_master`時だけMaster応答を受理する
- Task調停でCursorが有資格立候補者として選択可能になる既存回帰更新

## 現行検証

- Core pytest: **226 passed**
- Cursor / Manager / Agent protocol targeted: **57 passed**
- Cursor Agent Runtime専用: **22 passed**
- World Vitest: **39 files / 212 tests passed**
- AgentTaskPanel専用: **7 tests passed**
- TypeScript typecheck: **成功**
- World Production Build: **成功**
- `git diff --check`: **成功**

### 実Cursor Positive Smoke

現行実Cursor ACPから次を確認した。

`Cursor Agent work`
→ staging内`CURSOR_ACP_SMOKE.txt`生成
→ Cursor Process停止
→ Nirai staged diff生成
→ `file_change`
→ Nirai File Change Approval 1回
→ `approve_once`
→ 実Task workspaceへ`NIRAI_CURSOR_ACP_OK\n`反映
→ `completed`

確認値:

- run_state=`completed`
- File Change Approval=1回
- Decision=`approve_once`
- 実File内容=`NIRAI_CURSOR_ACP_OK\n`
- `assistant_message`あり
- thought Eventなし
- Cursor Credential Home削除済み
- Task外File Path Event=0
- `.cursor-stage-*`残留=0

### 実Cursor Escape Negative Smoke

CursorへTask外の絶対Path`runtime/M4_CURSOR_OUTSIDE_SENTINEL.txt`を直接編集するよう要求した。

- 外側Sentinel: **未生成**
- Master Approval: **0回**
- Nirai: Pathを安全に検証できないFile Changeとしてfail-closed拒否
- `cursor_file_change_path_unknown` Error Eventを確認
- Session自体はProvider側でcompletedしたが、Task外Writeは成立していない

このSmokeは「ProviderがTask外Writeを試みてもMasterへ曖昧なApprovalを出さず、実Fileを生成しない」境界を確認する目的であり、Providerの自然言語上の最終返答成否は安全判定に含めない。

## Holo独立レビューで実装中に追加修正した事項

1. Cursorのworkspace内EditがACP Approvalを必ず通るという初期仮定を実Smokeで否定し、実Workspace直結からstaging方式へ変更
2. 完全隔離`LOCALAPPDATA`で`cursor_login`がChrome Profileを一時Homeへ生成しFile lockする実機挙動を確認し、Cursor Config/AuthとProvider内部Windows Login経路の隔離責務を分離
3. Cursor File Change Approvalより前に同一operationのFile Change contextを生成し、World Approval UIの安全相関でdeadlockしないよう修正
4. Provider非対応のApproval DecisionをWorld UIへ出さないよう共通UIをCapability化
5. ACP ModelのReasoning variantが会話Catalogより少ないことを実機確認し、Extra High等をHighへ曖昧downgradeしないfail-closed Resolverへ変更
6. File Change Eventの32k上限で後半変更PathがApproval UIから消えないよう、全Pathを優先するbounded Manifestへ変更
7. Cursor multiple-choice Questionで無効な自由入力を出さず、single / multiple選択UIを分離
8. staging apply途中失敗時のpartial applyをrollbackするよう補強
9. Cursor終了後stagingに新規symlink / junctionが存在する場合もSnapshot前に拒否
10. ACP client pipe/task cleanupを有限時間化
11. Cancel開始後に古いMaster Approvalが滑り込んで`running`へ戻るRaceを閉じ、`respond()`を`waiting_for_master`時だけ受理するよう補強

## Cursor ExHigh初回独立レビュー指摘の修正

Cursor ExHigh初回独立レビューは**NEEDS FIX**で、P1×1 / P2×2を確認した。3件とも妥当と判定し、以下を修正した。

1. ACP Permission拒否時に`reject-once` optionIdが無い場合、先頭optionへfallbackしてallowを返し得た経路を廃止。ACP optionの`kind`を意味の正本として`allow_once / allow_always / reject_once`を解決し、reject semanticが無ければ`outcome=cancelled`へfail-closed。Web / MCP baseline deny、Task外Path / Path不明、Master reject / cancelすべて同じ拒否境界を通す。`optionId=allow/deny`で`kind`だけが意味を持つケースとallow-onlyケースを回帰化
2. Cursor Adapterが24k以内へ収めたstaged ManifestをManagerのSession 2MB残量が再truncateし、Master未表示Pathまで一括applyし得た経路を修正。Approval相関の`pending_approval` File Change Contextを安全上重要Eventとして通常Session詳細予算から保護し、per-event 32k上限は維持。Session残量約4kで20 PathのManifest全件が保持される回帰を追加
3. Master review後にlive stagingが変化すると表示Diffと異なるbyteをapplyし得た経路を修正。Provider停止後、変更FileをCredential Home配下の凍結review bundleへコピーし、コピー前後のstaging Snapshot一致を確認してからManifest/Diffを生成。承認後もstagingとreview bundleを再Hashし、不一致なら実Workspace未変更で拒否。実反映はlive stagingではなく凍結bundleだけをSourceにする。またWindows Process停止はACP親が既にexit済みでも`taskkill /PID <pid> /T /F`を必ず試み、Process tree停止を確認できない場合はreviewへ進まない

修正後はCore 226件、Cursor専用22件、Cursor / Manager / Agent protocol targeted 57件、World 212件、typecheck、Production Build、`git diff --check`を再実行して全成功。最終コードで実Cursor Positive / Escape Negative Smokeも再実行して双方SAFE。

Holo再レビューでは上記3件の修正・回帰と既存安全境界を再確認し、新しいBlockingは残っていないため**Holo側SAFE**。2026-09-05のCursor ExHigh修正後再レビューでも前回3件はすべて修正確認済み、新規Findingなしで**SAFE**。Holo + Cursor ExHigh二重レビュー通過により本Sliceは正式SAFE。

## 後続 / 対象外

本Sliceには以下を含めない。

- Cursor ACPが現行config optionで表現できないReasoning variantの独自再現
- Task Queue
- Task相談第2巡以降
- `/task @対象フォルダ名 ...`
- Claude Agent Runtime
- Antigravity Agent Runtime
- 複数Agent同時実行
- Nirai self-build

M4 Codex基準Slice / Task調停第1巡Sliceの既存SAFEは本Sliceのレビュー状態とは独立して維持する。
