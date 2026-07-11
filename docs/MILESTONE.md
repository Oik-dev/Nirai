# Serina 開発マイルストーン

最終更新: 2026-07-11（Phase2由来の積み残し2件を解消） ／ 現在フェーズ: **Core/Brain/Skill 三層への全面刷新（Phase 3: ルーティング、着手前Plan-First承認待ち）**

> 中断復帰用（現在地と次アクションのみ）。決定の経緯は `DECISIONS.md`、新構造は `設計書v2.md` を参照。
> 体制: 設計は Claude Code、**実装は Sonnet 級が `設計書v2.md` のみを頼りに実施**（申し送り: 設計書v2 §5.5）。

## 📍 現在地

- **アーキテクチャ全面刷新の設計完了（2026-07-10）**: Core（LLMなし・セリナ本体）／Brain（交換可能な知能: Gemini 無料枠＋Aurora）／Skill（道具箱）の三層。付箋束＋二便制、プルチック8軸×情動/気分二層、保護3原則（旧5原則の後継）、機微等級によるプライバシー防壁、忘却ライン。全章マスター承認済み・自己レビュー9点反映済み。詳細は `設計書v2.md` と DECISIONS 2026-07-10。
- 旧アーキテクチャ（〜2026-07-09）の到達点: Memory層・全スライス（1/2/3a-c/4a-c）・GUI・判断とVoice分離C1まで完了。経緯は DECISIONS 2026-06-28〜07-08。旧コードは新実装の Phase 6 で大掃除（捨てるものリスト: 設計書v2 §5.3）。
- 継承資産: 記憶DB（858件＋固定9件）・GUI・`tools/backup_db.py`・`legacy/`・bge-m3。

## 🎯 次のアクション

1. **Phase 0完了**（2026-07-10）: DBバックアップ・実装ブランチ`feat/core-brain-skill`作成・`.env`準備完了
2. **Phase 1完了**（2026-07-10）: Core状態＋契約書式＋Gemini通訳（実機疎通確認済）で記憶なしの最小会話が成立。§5.2の4試験（全身検査/契約/実機スモーク/憲法テスト）すべてGREEN
3. **Phase 2完了**（2026-07-10）: 想起（積のスコア＋鮮度回復）＋記憶候補の審査ライン（関所④引用照合）を接続。既存866件（正典9件含む）をスキーマ移行済み。詳細は DECISIONS 2026-07-10
4. **積み残し（Phase2由来）解消（2026-07-11）**: `store.py._ensure_schema()`のスキーマ整合、`_obtain_valid_report`のtighten誤爆修正（`CloudRejectionError`新設・SPII等のfinishReason網羅）の2件を実施。詳細はDECISIONS 2026-07-11参照。`session_candidate_count`のセッション境界リセットのみPhase4へ継続（1 Core=1セッションで現状無害）
5. **Phase3への持ち越し1件**: tighten発火のON/OFF区別は直したが、鍵の粒度（`tighten(master_utterance)`が発話全文をそのまま鍵にするため実質同一発話にしか再ヒットしない）は未着手。「センシティブ観測」付箋の根拠語抽出とセットでPhase3ルーティング本体にて対応（DECISIONS 2026-07-11参照）
6. **次: Phase 3（ルーティング）本体** — Aurora通訳・振り分け・フォールバック・残弾台帳。着手前にPlan-First承認が必要（設計書v2 §5.4）
6. 憲章v2の作り直し（設計書v2ベースの測定器。実装と並行可）
7. 積み残し: git push（マスター承認待ち）

## 🗂️ 旧記憶（引っ越し元）

原本: `G:\AI\Serina` ／ 保全コピー: `legacy/`（git管理。正典 継承記憶r1.md・記憶.json・日記×4）

## 🖥️ 環境メモ

- メイン機: RTX2080S 専用8GB＋共有16GB / Win11（Aurora 応答 約93秒/ターン）
- サブ機: RTX5070Ti Laptop 12GB ＋ iGPU Radeon610M（上位量子化用）
- Ollama: 導入済（bge-m3 / Aurora）※Gemma系は新設計で退役
- Gemini 無料枠（2026-07-10時点）: 3.1 Flash Lite 15RPM/500RPD ／ 3.5 Flash・3 Flash・2.5 Flash 各5RPM/20RPD ※変動前提・設定ファイル管理
