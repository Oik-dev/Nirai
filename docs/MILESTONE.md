# Serina 工程表（MILESTONE）

最終更新: 2026-07-19 ／ 正典: `設計書.md` ／ 索引: `INDEX.md`

> 中断復帰用。**実装済みの設計はすべて正典（`設計書.md`）にあり、本書は「まだ入っていないもの」だけを管理する。**完了した工程の履歴は `archive/DECISIONS.md` と Git を参照。
> 体制: 設計は Claude Code、実装は Sonnet 級が正典を頼りに実施（設計書 §5.5）。

## 現在地（1行）

Core / Brain / Skill の白紙再設計は実装完了（ユニット群 GREEN）。Brain は Qwen 単一、Gemini はアドバイザー Skill。残るは下記の配線・実測のみ。

## 残作業（優先順）

1. **idle ウォーターフォール本配線** — 未接続は次の3関数（いずれも実装・テスト済みだが本番から呼ばれていない。`run_idle_chore_tick`（`core/chores/orchestrator.py`）の優先順チェーンへ組み込む）:
   - `write_fact_from_distillation_candidate`（`core/chores/distillation.py`）— 蒸留→Fact 書き込み
   - `revise_persona_block`（`core/chores/persona_revise.py`）— persona 可変ブロックの自律改訂（実行前 `tools/backup_db.py` 必須・設計書 §4.3）
   - `run_idle_export_life`（`core/chores/orchestrator.py`）— DB→life/ 一方向出力
   **着手前に必ず**: tombstone除外はtable-scan含め対応済み（2026-07-19）だが、新規読み経路を足す際は同様の除外を確認。あわせて orchestrator の docstring（「persona_revise / export_life も同じ停止規則の配下」）を実態と同期させる
2. **GUI の Pulse 表示接続** — サーバ側は完成（見回り発火・`/api/pulse/pending`・`/api/pulse/mute`）。残りは **フロント `app/web/app.js` のポーリング＋表示のみ**
3. **eval 10指標の実測運用** — ハーネス `tests/eval_suite.py` は空回し確認済み。実 Ollama で基準値を取り、合格ラインを `config/eval_thresholds.toml` に確定する
4. **付箋・ラチェットの残課題**（旧 Phase 3 残メモ）:
   - センシティブ観測の発火が Qwen への発注対象外（心の動き・マスター観測のみ）。ラチェット汎化が弱い
   - 緩和方向（「平気」）のマスター承認導線が未実装
   - 付箋カタログのうち「センシティブ観測」以外は体系だったプロンプト説明が薄い

## 検討中・保留（条件成立まで実装しない。設計書 §5.3 と対）

| 項目 | 着手条件 |
|---|---|
| 二段検索（粗取得→精密再採点） | 記憶数千件超の実測でヒット率/速度が劣化したら |
| グラフ基盤（Graphiti 等） | Fact 台帳（設計書 §4.9）で不足が実測されたら |
| 身体レーン（VoiceLoop / PresencePet・音声・表情・画面知覚・キャラ固有TTS） | テキスト会話が十分と判断できたあとに別計画。D4a / D4b 契約は凍結（archive の合意台帳 参照） |
| 音声系評価指標（初音声秒数・割り込み成功率） | 身体レーン着手時に追加 |
| 評価合格ラインの具体値 | 実測開始後に設定ファイルで確定（上記 残作業3） |
| Presence（アバター常駐） | 身体レーンと同時期 |

## 旧記憶（引っ越し元）

原本: `G:\AI\Serina` ／ 保全コピー: `legacy/`（git管理。正典 継承記憶r1.md・記憶.json・日記×4）

## 環境メモ

- メイン機: RTX2080S 専用8GB＋共有16GB / Win11（Qwen 応答 暖機後おおよそ15〜21秒/ターン）
- サブ機: RTX5070Ti Laptop 12GB ＋ iGPU Radeon610M（上位量子化用）
- Ollama: 導入済（bge-m3 / serina-qwen35-unc）
