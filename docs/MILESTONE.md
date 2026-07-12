# Serina 工程表（MILESTONE）

最終更新: 2026-07-12（憲章v2作成・architecture-reviewer復元。次はPhase6大掃除。DECISIONS 2026-07-12参照） ／ 正典: `設計書v2.md` ／ 索引: `INDEX.md`

> 中断復帰用。この1枚で「どこまで終わり、次に何をするか」が分かる。決定の経緯は `DECISIONS.md` を参照。
> 体制: 設計は Claude Code、**実装は Sonnet 級が `設計書v2.md` のみを頼りに実施**（申し送り: 設計書v2 §5.5）。

## 工程表（設計書v2 §5.4 準拠）

| Phase | 内容 | 状態 | 完了日 / 備考 |
|---|---|---|---|
| 0 | DBバックアップ・ブランチ`feat/core-brain-skill`・`.env`準備 | ✅ 完了 | 2026-07-10 |
| 1 | 歩く骨格（Core状態＋契約書式＋Gemini通訳で最小会話） | ✅ 完了 | 2026-07-10。§5.2の4試験すべてGREEN |
| 2 | 記憶接続（想起＋審査ライン、866件スキーマ移行） | ✅ 完了 | 2026-07-10。積み残し2件も07-11解消（DECISIONS参照） |
| 3 | ルーティング（Aurora通訳・振り分け・フォールバック・残弾台帳） | 🔶 本体実装済み | main配線済み。**残課題は縮小したが未解消**（下記） |
| 4 | 裏方便（宿題箱・蒸留・日記・既存記憶の機微査定） | ✅ 完了 | 2026-07-12。総合監査改修（毒饅頭・朝礼日記・QuotaLedger・窓＋要約）まで含む |
| 5 | 道具箱（道具説明書書式・検索・StockAI等の接続） | ⬜ 未着手 | |
| 6 | 大掃除（旧コード削除断行・docs刷新・完成） | ⬜ 未着手 | 捨てるものリスト: 設計書v2 §5.3。実DB`data/serina_memory.db`のid=871（検証用日記）を含む検証データ一式の削除もここでまとめて行う |

## 🎯 次のアクション（優先順）

1. **Phase 6: 大掃除**（§5.3改訂済み: GUI帳簿係の`core_v2/`移植→旧コード削除断行・persona正式構成の確定・id=871含む検証データ削除・docs刷新。`MemoryRecord.from_row`は監査改修で前倒し済み）
2. **Phase 5: 道具箱**（設計書v2 §5.4準拠。週間トークン制限明け後に着手）
3. **（別件フラグ済み・任意）`tests/test_gui_watchdog.py`のGPU実状態依存を解消**（`is_gpu_busy()`をモック化。task_80267462）
4. **（任意・既知の弱点）`tests/smoke_core.py`（旧アーキ）が`num_predict`未指定のため長文生成で300秒タイムアウトしうる**（旧アーキはPhase6で退役予定のため優先度低）

### 直近で消し込んだもの
- ~~`Core.session_candidate_count`の役割整理~~ → 2026-07-12: 上限を1蒸留ジョブ単位に確定しカウンタ廃止（DECISIONS参照）
- ~~憲章v2の作り直し~~ → 2026-07-12: `docs/憲章v2.md` 新設・`.claude/agents/architecture-reviewer.md` 復元（測定器参照を憲章v2へ）

## 📍 到達点の要約

- **新アーキテクチャ（Core/Brain/Skill 三層）**: Phase 0〜4 完了、Phase 3 本体実装済み。文脈パックの直近窓＋rolling_summary（§1.4）も実装済み
- **旧アーキテクチャ（〜2026-07-09）**: Memory層・全スライス・GUI・判断とVoice分離C1まで完了して退役。資料は `archive/`、旧コードは Phase 6 で大掃除。旧REPL入口`tools/start_serina.bat`は2026-07-12退役済み
- **継承資産**: 記憶DB（866件・正典9件含む）・GUI・`tools/backup_db.py`・`legacy/`・bge-m3

## 🗂️ 旧記憶（引っ越し元）

原本: `G:\AI\Serina` ／ 保全コピー: `legacy/`（git管理。正典 継承記憶r1.md・記憶.json・日記×4）

## 🖥️ 環境メモ

- メイン機: RTX2080S 専用8GB＋共有16GB / Win11（Aurora 応答 約93秒/ターン）
- サブ機: RTX5070Ti Laptop 12GB ＋ iGPU Radeon610M（上位量子化用）
- Ollama: 導入済（bge-m3 / Aurora）※Gemma系は新設計で退役
- Gemini 無料枠（2026-07-10時点）: 3.1 Flash Lite 15RPM/500RPD ／ 3.5 Flash・3 Flash・2.5 Flash 各5RPM/20RPD ※変動前提・設定ファイル管理
