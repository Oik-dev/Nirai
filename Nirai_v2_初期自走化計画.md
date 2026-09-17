# Nirai v2 初期自走化計画

> 本書はNirai v2を、Holoがv2だけで開発Taskを完遂できる状態まで立ち上げるための期間限定実装計画である。
> 仕様の正本は`Nirai_v2_基本設計書.md`とし、本書は実装順序だけを定義する。

## 目的

最初の到達点を以下に限定する。

> DashboardからHoloへTaskを渡し、Holoがv2のControl APIとToolを使って作業し、途中で止まってもAuto Resumeで復帰し、v2だけで完了まで自走できる。

この到達点までは、Memory、Serina、Cursor、Codex等の拡張を優先しない。

## 実装順序

### M1. Hub最小核

v2の状態正本を成立させる。

- Hub Store
- Resident（初期はHoloのみ）
- Conversation / Task Chat
- Task
- Run
- Task単位のResume ON / OFF
- Master Request
- 最小Capability Registry

旧Task / Workflow / Agent Sessionを正本として利用しない。

### M2. UI配線

完成済み`prototype/`を実データへ接続する。

- Task作成
- Task一覧 / Activity表示
- Task Chat
- Pause / 再開
- Resume ON / OFF
- CHECK
- 完了 / Archive表示

UI都合でBackend概念を追加しない。

### M3. v2 Control API

Holoがv2を直接読んで操作できる最小入口を作る。

最低限、以下を可能にする。

- Taskの取得
- Taskの現在情報取得
- Runの開始 / 結果記録
- Master Request作成
- Task完了要求
- v2が公開するTool利用

Holoの作業経路から旧Workflow APIへの依存を外す。

### M4. Holo Connector

ChatGPT Web固有の接続責務だけを実装する。

- 対象Conversation識別
- `ready / busy / blocked / unavailable`観測
- Prompt送信
- 生成中判定
- Master Draft保護
- Login / Reload / 一時切断への復旧
- `delivery_id`による重複送信防止

Task状態、Resume設定、Task完了判定、独立したAuto Resume QueueをHolo Connectorへ持たせない。

### M5. Holoで1 Task完走

Auto Resumeなしで一本道を成立させる。

```text
Dashboard
  -> v2 Task
  -> Holo
  -> v2 Control API / Tool
  -> Run結果
  -> Task Completed
  -> Dashboard反映
```

この時点で、通常ケースのTask実行に旧Workflow制御を必要としないことを確認する。

### M6. Auto Resume

Hub側のTask状態とHolo Connectorの観測状態を接続する。

以下を満たした時だけHoloへ続行を送る。

- Taskが`Running`
- Resumeが`ON`
- Taskが未完了
- 次の進行にHoloが必要
- Holo Connectorが`ready`

再送本文へTask内容を複製せず、原則`Task ID + delivery_id`だけを渡す。HoloはHubから現在情報を取得して続行する。

### M7. 停止・異常・Restart Recovery

自走に必要な境界だけを確認する。

- Holo生成中
- Master Draftあり
- 別Conversation表示中
- ChatGPT Reload
- Login / Web一時不調
- Nirai再起動
- Holo応答途中の切断
- Run失敗
- Master Request待ち
- Resume OFF
- Pause / 再開
- 同一Deliveryの二重送信防止

未完了TaskはNirai再起動後にResume設定を保持したまま`Paused`で復元し、勝手に再開しない。

### M8. v2 Self-host Cutover

HoloへNirai v2自身の開発Taskを実際に渡し、以下だけで完遂できることを確認する。

- v2 Dashboard / Conversation
- v2 Task / Run
- v2 Control API
- v2 Holo Connector
- v2 Auto Resume
- v2 Master Request

旧NiraiのWorkflow、Auto Resume、Conversation ownership、Holo Task ownershipをTask継続のために利用しない。

これを満たした時点を、v2初期自走化の完了とする。

## この期間は後回しにするもの

初期自走化へ直接必要でないものはM8後へ送る。

- Local Memory本実装
- Serina本接続
- Resident同士の自律会話本実装
- Cursor / Codex
- Web / Image等の追加Capability
- 複数Task並列実行の本実装
- 高度なResource Scheduling
- その他の拡張機能

設計書で必須と定義された機能を廃止する意味ではなく、初期自走化の実装順序から外すだけである。

## v1の扱い

M8完了まではv1を開発用の足場として残す。

v2が自走できることを確認する前に、旧Holo連携や旧制御経路を先に削除しない。

M8完了後に、v2から不要になったv1依存を別作業として退役させる。

## 本書の退役

M1〜M8がすべて完了したら、本書は役目を終える。

その時点で以下を行う。

1. 本書を`archive/`配下へ移し、Archive扱いにする。
2. `README.md`と`AI_ENTRY.md`から現行計画としての参照を削除する。
3. 以後の設計・実装判断では本書を正本として使用しない。
4. 完了後の開発は`WORLD_RULES.md`と`Nirai_v2_基本設計書.md`を基準に行う。

Archive後の本書は、初期自走化の範囲を確認するためだけに残す。
