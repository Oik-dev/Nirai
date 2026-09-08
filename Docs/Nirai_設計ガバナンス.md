# Nirai 設計ガバナンス

## 1. 目的

本書は、Niraiの設計・実装・レビューを長期間合理的に維持するための共通ルールを定義する。

既存設計書を守ること自体を目的にしない。最上位の目的は、Masterが求めるNiraiを、合理的・効率的・保守しやすく、安全に長期間成立させることである。

設計と実装は、最低5年間の日常利用で根本作り直しを必要としないことを目標にする。

---

## 2. Source of Truth階層

設計判断が衝突した場合は、次の順序で優先する。

1. **Product Goal / Philosophy**
   - `Nirai_基本設計.md`
   - Niraiとは何か、何を最優先するか
2. **Invariant / Guardrail / Design Governance**
   - 本書
   - Privacy、Identity、Security、Approval、正本所有、設計判断ルール
3. **Active Design / Contract**
   - `詳細設計/01`〜`07`、`09`、`11`、`12`等
   - 現時点で採用している実装方式
4. **Milestone / Acceptance**
   - `詳細設計/08_マイルストーンと受入基準.md`
   - 今何を完成させるか、何をもって完了とするか
5. **Evidence / History / Reference**
   - `*_検証結果.md`
   - 過去Review Finding
   - AITuberKit / AIAvatarKit等の参考カンペ
   - Archive Plan

下位文書は上位文書を上書きしない。

Evidence / History / Referenceは、現行設計を拘束しない。過去にSAFEだったことは「現在も最適である」ことを意味しない。

---

## 3. 設計ラベル

重要な設計判断は、必要に応じて次の性質を区別する。

- **Invariant** — 原則として変更しない。Privacy、Identity、安全境界等
- **Current** — 現在最も合理的と判断して採用している方式
- **Temporary** — 暫定制約。理由と解除条件を必ず持つ
- **Provider Constraint** — 外部Provider仕様に起因する制約
- **Future Candidate** — 未採用の将来候補
- **History** — 過去の実装・判断記録。現行仕様ではない

Temporaryを理由・解除条件なしで永久仕様化しない。

Provider ConstraintをNirai固有Invariantへ昇格させない。

---

## 4. 設計品質の必須観点

設計案は「動く」「安全」だけで採用しない。最低限、次を確認する。

1. **目的適合** — Niraiの存在・関係・長期継続を良くするか
2. **合理性** — 問題に対して過剰でも不足でもないか
3. **効率性** — Token、Brain call、Disk I/O、待ち時間、Master操作、Network、CPU/GPUを無駄に増やさないか
4. **保守性** — 責務分離、局所変更、Test、削除、置換が容易か
5. **運用容易性** — Masterへ不要な儀式、承認、設定、復旧操作を押し付けないか
6. **必要性** — 実測上存在しない問題を先回りで複雑化していないか
7. **既存手段** — Provider、OS、OSS、標準Protocolに正式な解決手段がないか
8. **正本所有** — Identity、Memory、Stateの正本が何か明確か
9. **Lossless** — 要約・Index・Cacheを失っても重要な原情報を再構成できるか
10. **Scaling** — 5年使っても全件scan、全体rewrite、無制限Prompt肥大が常用経路に入らないか
11. **Concurrency** — Global Lockが本当に必要か。Resource単位へ狭められないか
12. **Failure** — Crash、Timeout、Cancel、Provider停止、再起動時の状態が定義されているか
13. **Replaceability** — Provider / Model / Avatar / World交換で不要な破壊が起きないか
14. **Privacy / Security** — Public / Private / Credential / allowed_dir / Approval境界を破らないか
15. **Complexity Budget** — 得られる価値に対して実装・運用複雑性が過大でないか
16. **Evidence** — 採用理由が公式仕様、実測、先行実装等で裏付けられているか

「安全だから全部直列化する」「将来使うかもしれないから先に抽象化する」は、それ単独では採用理由にならない。

---

## 5. Reference-First Gate

### 5.1 原則

大規模実装・新規Subsystem・外部統合・一般的な問題の独自実装へ着手する前に、Webを広く調査する。

目的は、優秀なカンニングペーパー、公式機能、成熟OSS、Reference Implementation、既知の設計Patternを先に発見し、無駄な再発明と試行錯誤を避けることである。

「既存設計書に方式が書いてある」ことを理由に調査を省略しない。設計作成後にProviderやOSS側でより良い正式機能が追加されている可能性も確認する。

### 5.2 必須適用条件

次のいずれかに該当する場合はReference-First Gateを通す。

- 新しいSubsystem / Runtime / Memory方式 / Agent統合を追加する
- Audio / Animation / Input / Retrieval / Storage / Scheduler等の主要方式を新規導入・大幅置換する
- Core / World / Provider / Storage等の複数責務へ跨る変更を行う
- 外部Provider / SDK / Protocolと新規統合する
- 独自Algorithm / Parser / State Machine / Scheduler / Retry機構等を新設する
- 既存方式を大きく置換する
- 一般的な問題で、既存解が存在しそうである
- 大規模か判断に迷う

迷う場合は調査側へ倒す。

### 5.3 調査範囲

最低限、次を横断して確認する。

1. 公式Documentation / SDK / Example / Protocol
2. 成熟OSS / GitHub Repository
3. 同等機能を持つ製品・ProjectのReference Implementation
4. Maintainer Issue / Discussion / Migration Guide
5. 必要に応じて技術記事、Benchmark、実利用報告

検索は1つの候補を見つけて終了しない。少なくとも有力候補を比較し、Nirai要件との適合を判断する。

### 5.4 調査で抽出するもの

- 解決済みの責務分割
- 正式API / Protocol
- Session / State / Retry / Cancellation方式
- Scaling / Performance上の工夫
- Security Boundary
- 既知のFailure Mode
- 実装上の罠
- License / 再利用条件
- Niraiへそのまま持ち込んではいけない前提

Sourceのコピー可否だけを調べるのではなく、「どういう公開APIと責務分割で問題を解いているか」をカンニングする。

### 5.5 実装着手条件

大規模実装へ入る前に、最低限次を設計または作業記録へ残す。

- 調査した公式機能 / 先行実装
- 有力候補の比較
- 採用する考え方
- 採用しない部分と理由
- License上の扱い
- Nirai固有で新規実装が必要な差分

調査せず「全部独自実装」は原則禁止とする。

### 5.6 過去からの教訓

AITuberKit / AIAvatarKit等の先行実装を早期に十分参照していれば、Nirai側で独自に試行錯誤・再設計せずに済んだ領域が複数あった。

今後はこの教訓を個別記憶にせず、Reference-First GateとしてProject Rule化する。

---

## 6. grill-me判断ルール

AIは、判断をMasterへ丸投げしない。一方、Masterの製品意図を勝手に補完して永久仕様化しない。

### Masterへ質問する

次のいずれかに該当する場合、grill-me形式で選択肢・推奨案・Trade-offを提示して確認する。

- Product Goalや体験の優先順位が変わり得る
- Privacy / Identity / Approval等のInvariantへ影響する
- 数年間残る不可逆なデータ構造・互換性方針を決める
- UX上の価値判断が複数あり、技術だけでは正解が決まらない
- 大きなコスト・安全性・利便性Trade-offがある
- 既存のMaster判断と矛盾する可能性がある

### AI側で決めてよい

- 上位Goal / Invariantを変えない局所実装詳細
- 既存標準方式に沿った命名・小さな責務分割
- Test追加、Error handling、明白な重複除去
- 同等挙動の範囲での保守性・可読性改善

質問する場合は、可能な限り次を含める。

- 何を決める必要があるか
- 選択肢
- 各Trade-off
- AIの推奨案と理由

単に「どうしますか？」だけで投げない。

---

## 7. 設計変更ルール

### Invariant / Product Goalと衝突した場合

実装を止め、Masterへ確認する。

### Current Designより明確に良い方式を見つけた場合

旧設計を盲目的に実装しない。

1. Reference-Firstで裏付けを確認する
2. 現行方式との比較を作る
3. Product Goal / Invariantへの影響を確認する
4. 必要ならMasterへgrill-meする
5. 設計書を先に更新する
6. その後実装する

### 設計書に未記載の局所詳細

上位Goal / Invariant / Active Contractを変えない範囲では合理的に決定してよい。

「書いていないことを一切発明しない」を一般ルールにしない。

---

## 8. Historyと現行仕様の分離

Stable / SAFE / Review Finding / Test count / Live Smokeの詳細は、原則としてVerification文書へ置く。

Active Designには「現在どう動くべきか」を書く。

過去経緯が必要な場合だけVerification / Archiveを参照する。

AI_ENTRYはRouterとして短く保ち、Review履歴の保管庫にしない。

---

## 9. Nirai固有の確定Design Principle

2026-09-06時点で次を確定する。

- ResidentはBrain交換後も同じIdentityを維持する
- Provider native Session / ThreadはConversation Contextとして利用できるが、Resident Identity / Nirai Memoryの唯一の正本にしない
- Public / Private Memoryは年単位・数年単位で蓄積する前提にする
- MemoryはRaw Durable Source + Structured Continuity + Derived Retrievalを基本とする
- Masterが1 Residentと会話している間も、安全な範囲で別Residentは生活・Conversationを継続できる
- Global Brain Lockを永久Invariantにしない
- 人間向け発話・TTSの混線はPresentation層で交通整理する
- Taskの標準体験は、Masterが指名Residentへ自然言語で直接依頼する方式とする
- 指名Residentは必要な場合にCouncilを提案・開始できる
- Niraiが通常会話から勝手にFile変更Taskへ昇格しない
- 日常利用5年程度で根本作り直し不要を設計目標とする

---

## 10. 完了条件

新規設計・大規模変更は、Codeが動いただけで完了としない。

最低限、次を確認する。

- Product Goalに適合
- Invariant違反なし
- 合理性・効率性・保守性チェック済み
- 必要なReference-First調査済み
- Current / Temporary / Provider Constraintが明確
- 自動Test / 必要な実機QA済み
- Active Designと実装が一致
- HistoryをCurrent Designへ混入させていない
