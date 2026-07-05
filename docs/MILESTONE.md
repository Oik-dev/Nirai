# Serina 開発マイルストーン

最終更新: 2026-07-05（判断とVoiceの分離 C1配線を実装） ／ 現在フェーズ: **判断とVoiceの分離（C1配線完了・次はC2で判断エンジンを選定）**

> 中断復帰用（現在地と次アクションのみ）。決定の経緯は `DECISIONS.md`、構造原則は `設計書.md`、スライス詳細は `specs/` を参照。
> 体制: **Claude Code で設計・実装を完結**。トークン制限時のみ Cursor で続行。

## 📍 現在地

- Memory層 実装**完了**（DB/store/検索/埋め込み/移行/スモーク）。旧記憶の移行v2完了（858件・固定9件・正典は原文無傷）。
- Core層 **スライス1完了**（最小の司令塔: 人格注入→記憶検索→Aurora応答→履歴保存）。
- **3a 実装完了**（sessions/archived_history、SessionManager、maintenance、自動テスト）。
- スライス3 **設計完了**（3a/3b/3cに分割、レビュー済み）／スライス4 **設計完了**（記憶の成長6項目）。
- git 管理開始・DB日次バックアップ導入（2026-07-03）。
- **全スライス完了**: 1（Core最小）／3a・3b・3c（セッション・二役蒸留・減衰と感情）／4a・4b・4c（再固結・行動化・計器盤）／2（ルーティング・時刻注入・蒸留インテント）。各スライスは二段階または統合レビュー済み・テスト7スイート全合格。詳細は DECISIONS 2026-07-03〜04。
- 想起チューニング済（num_ctx 8192・規律プロンプト・旧記憶851件キーワード付与）＋会話スタイル契約（詩化・台本・作話対策、実プローブ検証済）。
- **運用フェーズ第1弾完了（2026-07-04）**: ストリーミング表示／External Services 接続設計書／GUI基本形（`Serina.bat` 起動）。詳細は DECISIONS 同日。
- **WEB検索 Phase 1a を退避・ロールバック（2026-07-05）**: C方式（Auroraが発話の一息で検索要否を判断）が設計思想（Coreは決めるだけ／二役=声と判断の分離）に反し、Voice→Decisionの逆流でセリナの人格が変質したため、本線から退避。会話本体は ChatSkill へ戻し WEB検索前の状態へ復帰。実装一式は `shelf/web-search-c-method` ブランチに全保全（器官・infra含む・再設計時に再利用）。経緯は DECISIONS 同日。
- **人格アーキテクチャ整理 B＋C1 完了（2026-07-05）**: B=persona.md を core_values/voice_style/boundary へ物理分割・設計書4点を訂正（北極星図・憲法条文・Aurora判定残骸除去）。C1=`Decision→Action→Evidence→Voice` の配線を TDD で実装（`core/decision.py`・不変な `DecisionResult`・逆流回帰テスト green）。判断は決定論で気分・人格に非依存。検索は非復帰・受け口(action/query)のみ温存。詳細は DECISIONS 同日。

## 🎯 次のアクション

1. **C2: 判断エンジンの選定**（8GB制約下）: Rule Engine／CPU常駐小型／Gemma載せ替え／Qwen の比較。現時点方針は決定論最大化（ルール約8割・推論モデル約2割）＝判断をLLMに丸投げせず、曖昧な依頼のみ推論モデルに回す。C1の逆流回帰テストを土台に、判断先を差し替えても壁が保たれることを担保する
2. **配線切替（persona.md 退役）**: `prompt/loader.py` を core_values（→判断）／voice_style＋boundary（→発話）読みへ切替。現在は persona.md が実効ソースのため、この切替で分割を有効化する
3. **検索復帰ステップ**: 温存した `DecisionResult.action/query` の受け口へ、退避ブランチ（`shelf/web-search-c-method`）の器官を Action として再接続。生きた逆流検証を行う
4. 積み残し: git push（マスター承認待ち）／smoke系のOllama実機E2E／GUIチューニング／ツマミ調整（κ・τ・照れ隠し・p_growth）

## 🗂️ 旧記憶（引っ越し元）

原本: `G:\AI\Serina` ／ 保全コピー: `legacy/`（git管理。正典 継承記憶r1.md・記憶.json・日記×4）

## 🖥️ 環境メモ

- メイン機: RTX2080S 専用8GB＋共有16GB / Win11（応答 約93秒/ターン）
- サブ機: RTX5070Ti Laptop 12GB ＋ iGPU Radeon610M（上位量子化用）
- Ollama: 導入済（bge-m3 / Aurora / 理性エンジン Gemma4-12B heretic i1-IQ4_XS 6.64GB）
