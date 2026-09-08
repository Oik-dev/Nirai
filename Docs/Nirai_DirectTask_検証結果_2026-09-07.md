# Nirai Direct Task 検証結果 2026-09-07

## 判定

**Direct Task Protocol / Focused Resident Shortcut Slice：SAFE**

**自然文だけからTask intentを自動判定するUX：未実装**

Masterが担当Residentを明示したTaskでは、旧M4の全Resident Council / volunteer選出を通らず、そのResidentへ直接Agent Workを割り当てる経路を製品実装した。

## Current Contract

`task_request`は任意の`resident`を受け取る。

- `resident`あり：Direct Task
- `resident`なし：既存M4 Council互換経路

World Chatでは、ResidentをFocusした状態で`/task ...`を送ると、そのFocus Resident名を`task_request.resident`へ含める。

Direct Taskでは：

1. 指名Residentが現在enabledか確認する
2. Brainが存在し、Holo Addonではなく、`agent_work` Capabilityを持つか確認する
3. 条件を満たさない場合、別Residentへ自動移管しない
4. Council / volunteer Brain callを行わない
5. 指名ResidentのProvider Agent Runtimeを直接開始する
6. `task_update.assignment_policy = direct`、`assigned_resident`を返す

## Queue / Recovery

`QueuedTaskRecord`へoptional `resident_name`を追加した。

- Queue保存・読込でDirect assigneeを保持
- Core restart時もDirect assigneeを維持
- 復旧時にResidentが無効・削除・Agent非対応ならTask Queueをfail-closedする
- queued updateにも`assigned_resident` / `assignment_policy=direct`を保持
- 旧Queue recordの`resident_name`欠落は`None`として読み、Legacy Council経路を維持する

Queue全体1件/FIFOはまだM4 Temporary制約であり、このSliceでは変更していない。

## Safety

Direct Taskは「指名されたResidentができなければ他へ回す」を行わない。

これにより、MasterがCursorへ依頼したTaskをNiraiが勝手にCodexへ渡す等の暗黙Delegationを防ぐ。

Provider / working dir / allowed_dirs / Approval等の既存M4安全境界はそのまま利用する。

## UX境界

今回、通常Whisper本文をNiraiが勝手にTaskへ昇格する機能は追加していない。

理由：

- 「これどう思う？」等の相談と「これ直して」の実作業を文字列heuristicだけで誤判定すると、会話からFile変更へ勝手に進む危険がある
- 07のActive Designでも、実作業意図が曖昧ならFile変更前に確認することを要求している

Currentの明示ShortcutはFocus Resident + `/task`。最終的な「自然言語で直接依頼」は、明示Task affordanceまたは安全なintent/confirmation UXを追加してから完成扱いにする。

## Verification

追加・更新回帰：

- Focus/指定Resident Direct TaskはCouncil Brain call 0で担当開始
- `assignment_policy=direct`
- case-insensitive Resident解決後、canonical名を保存
- unknown ResidentはQueue / Agent Session作成前に拒否
- Queue StoreでDirect Residentをround-trip
- Core restartでDirect assigneeを復元
- stale / missing Direct ResidentはQueue復旧をfail-closed
- resident未指定の既存Council経路は回帰維持

最終実測：

- Core pytest：**385 passed / 33.71s**
- World Vitest：**39 files / 219 tests passed**
- World TypeScript typecheck：成功
- World Production Build：成功
- `git diff --check`：exit 0（LF→CRLF warningのみ）

## 残件

1. Focus + `/task`を最終日常入口にせず、自然なDirect Task UXを成立させる
2. Councilを担当Residentから必要時だけ開始するDelegated Task UX
3. Global Agent Session 1件 / FIFOをResource-based Concurrencyへ移行
4. Core crash後のProvider Work resume / rerun / abandon UX
