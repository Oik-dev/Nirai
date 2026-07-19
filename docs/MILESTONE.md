# Serina 工程表（MILESTONE）

最終更新: 2026-07-19（白紙実装 GO） ／ 正典: `設計書.md` ／ 索引: `INDEX.md`

> 中断復帰用。この1枚で「どこまで終わり、次に何をするか」が分かる。決定の経緯は `archive/DECISIONS.md` または Git を参照。
> 体制: 設計は Claude Code、**実装は Sonnet 級が正典＋GO／合意台帳を頼りに実施**（申し送り: 設計書 §5.5）。

## 工程表（設計書 §5.4 準拠）

| Phase | 内容 | 状態 | 完了日 / 備考 |
|---|---|---|---|
| 0 | DBバックアップ・ブランチ`feat/core-brain-skill`・`.env`準備 | ✅ 完了 | 2026-07-10 |
| 1 | 歩く骨格（Core状態＋契約書式＋Gemini通訳で最小会話） | ✅ 完了 | 2026-07-10。§5.2の4試験すべてGREEN |
| 2 | 記憶接続（想起＋審査ライン、866件スキーマ移行） | ✅ 完了 | 2026-07-10 |
| 3 | ルーティング（振り分け・フォールバック・残弾台帳） | 🔶 本体実装済み | Qwen単一化後も残課題は下記 |
| 4 | 裏方便（宿題箱・蒸留・日記・既存記憶の機微査定） | ✅ 完了 | 2026-07-12 |
| 5 | 道具箱 | 🔶 GO範囲完了 | 2026-07-19。Geminiアドバイザー（`skills/gemini_advisor/`）レビュー済。StockAI等は非範囲 |
| 6 | 大掃除（旧コード削除・docs刷新・ゼロベース命名） | ✅ 完了 | 2026-07-12 |
| 白紙GO | 合意台帳§3系＋OSS（B10除く）＋文書同期 | ✅ 完了 | 2026-07-19。Wave 0〜7＋最終completion-review通過（Critical 0）。採否: `specs/2026-07-19_白紙実装GO_採否と着手指示.md`（計画原本は `archive/` へ退役） |

## 次のアクション（優先順）

1. **idle ウォーターフォール本配線** — 未接続は次の3関数（いずれも実装・テスト済みだが本番から呼ばれていない。`run_idle_chore_tick`（`core/chores/orchestrator.py`）の優先順チェーンへ組み込む）:
   - `write_fact_from_distillation_candidate`（`core/chores/distillation.py`）— 蒸留→Fact 書き込み
   - `revise_persona_block`（`core/chores/persona_revise.py`）— persona 可変ブロックの自律改訂（実行前 `tools/backup_db.py` 必須・設計書 §4.3）
   - `run_idle_export_life`（`core/chores/orchestrator.py`）— DB→life/ 一方向出力
   **着手前に必ず**: tombstone除外はtable-scan含め対応済み（2026-07-19）だが、新規読み経路を足す際は同様の除外を確認
2. **GUI の Pulse 表示接続** — サーバ側は完成（見回り発火・`/api/pulse/pending`・`/api/pulse/mute`）。残りは **フロント `app/web/app.js` のポーリング＋表示のみ**
3. **eval 10指標の実測運用**（ハーネス `tests/eval_suite.py` は空回し確認済み。実Ollamaで基準値を取る）
4. **やらない（本GO）** — B10二段検索、C3身体レーン、会話BrainとしてのGemini復活、StockAI等の未指定道具。push は明示指示時のみ

### 白紙GO Wave進捗

| Wave | 内容 | 状態 |
|---|---|---|
| 0 | 文書同期（設計書v2・憲章縮小） | ✅ 完了 2026-07-19 |
| 1 | 土台（persona分割・store二口・冪等・rebuild・backup・pack先頭） | ✅ 完了 2026-07-19 |
| 2 | Fact・指示忘却・保護改訂 | ✅ 実装済 2026-07-19（最終レビュー済 2026-07-19） |
| 3 | 想起強化（Planner・think・記憶ツール・explainable） | ✅ 実装済 2026-07-19（最終レビュー済 2026-07-19） |
| 4 | 要約・life/・persona自律改訂 | ✅ 実装済 2026-07-19（最終レビュー済 2026-07-19） |
| 5 | 会話優先・checkpoint | ✅ 実装済 2026-07-19（最終レビュー済 2026-07-19） |
| 6 | Pulse・人格の刃・評価10指標 | ✅ 実装済 2026-07-19（最終レビュー済 2026-07-19） |
| 7 | Geminiアドバイザー（無人格Skill） | ✅ 実装済 2026-07-19（最終レビュー済 2026-07-19） |

### Phase 3 の残メモ（白紙GO外・残置）

- センシティブ観測の発火は Qwen 第2発注対象外（心の動き・マスター観測のみ）。ラチェット汎化は弱い
- 緩和方向（`平気`）のマスター承認導線は未実装
- 付箋カタログのうち「センシティブ観測」以外は、体系だったプロンプト説明が薄い

## 到達点の要約

- **アーキテクチャ（Core/Brain/Skill）**: Phase 0〜4・6 完了。文脈パックの直近窓＋rolling_summary（§1.4）実装済み
- **Brain**: Qwen3.5-35B-A3B-Uncensored 単一（`serina-qwen35-unc`）。会話用 Gemini/Aurora 退役（tag `aurora-final`）
- **GO（2026-07-19）**: Wave 0〜7 実装・commit済（ユニット 343 GREEN）。最終completion-review通過: architecture-reviewer 全条文PASS（B-1逆流は是正済）／serina-code-reviewer Critical 0・Important 1（tombstone table-scan漏れ→即時修正＋回帰テスト）
- **Gemini**: 会話 Brain ではなく `skills/gemini_advisor/`（相談クエリのみ・キー無しでも会話継続）
- **人格**: `prompt/persona/`＋`manifest.toml`。可変自律改訂・刃明文あり。旧 `persona.md`＋`boundary.md` は結合一致用に併存
- **正典**: ルーティング反転・同一性事後監査制・§4.8／§5.6 反映済。憲章は保護・機微・発火条件中心
- **継承資産**: 記憶DB・GUI・`tools/backup_db.py`・`legacy/`・bge-m3
- **2026-07-19 健全化**: WAL/busy_timeout、GUI watchdog GPU モック、運用docs 同期

## 旧記憶（引っ越し元）

原本: `G:\AI\Serina` ／ 保全コピー: `legacy/`（git管理。正典 継承記憶r1.md・記憶.json・日記×4）

## 環境メモ

- メイン機: RTX2080S 専用8GB＋共有16GB / Win11（Qwen 応答 暖機後おおよそ15〜21秒/ターン）
- サブ機: RTX5070Ti Laptop 12GB ＋ iGPU Radeon610M（上位量子化用）
- Ollama: 導入済（bge-m3 / serina-qwen35-unc）
