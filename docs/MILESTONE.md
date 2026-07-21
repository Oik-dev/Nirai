# Serina 工程表（MILESTONE）

最終更新: 2026-07-22 ／ 正典: `設計書.md` ／ 索引: `INDEX.md`

> 中断復帰用。**実装済みの設計はすべて正典（`設計書.md`）にあり、本書は「まだ入っていないもの」だけを管理する。**完了した工程の履歴は `archive/DECISIONS.md` と Git を参照。
> 体制: 設計は Claude Code、実装は Sonnet 級が正典を頼りに実施（設計書 §5.5）。

## 現在地（1行）

記憶正本入れ直し（日記親＋チャンク／JSON／継承抜粋）と日記近傍想起まで実装・本番投入済み。completion-review 待ち。

## 残作業（優先順）

- completion-review（`serina-code-reviewer`）— 本スライスの最終ゲート
- （任意）`summaries/blocks.json` と新正本の整合は別タスク

## 直近完了（2026-07-22）

- **記憶正本入れ直し**: 範囲3 wipe（旧固定ピン含む）＋ legacy 日記／JSON／継承白リスト投入。日記は親（非vec）＋チャンク。約束系を pinned+S 再設置（8件）
- **日記近傍想起**: `expand_recall_neighbors` を `_build_pack` 直前に差し込み（recall 活性化本体は非改変）
- **メンテ**: `tools/wipe_memory_runtime.py` / `tools/import_legacy_memories.py` / `core/memory/legacy_parse.py`
- 設計書 §4.6 更新。pytest ユニット群通過。eval_recall は golden を新正本に合わせて更新

## 直近完了（2026-07-21）

- **GUIメンテ 記憶検索・物理削除（type問わず）**: `GET/DELETE /api/memories` を新設。content部分一致検索（tombstone除外・ページ送り）＋物理削除。既存の`api_album_delete`/`api_session_delete`と同一骨格（confirm必須→backup→confirm_forget）を踏襲、固定9件はUI無効化、保護等級SはGUI確認で対応。completion-review Assessment「可」（Critical/Important なし、Minor1件＝LIKEワイルドカード未エスケープ・実害軽微）。詳細は DECISIONS 参照

## 直近完了（2026-07-20）

- **評価自動化**: Pulse嫌悪退役（9指標）／アシスタント化率のパターン照合／`tools/run_weekly_eval.py`（**Serina.bat起動時**・日曜のみ・3分後・同日1回）／GUI評価レポート＋failバッジ＋Claude用コピー
- **Pulseチャット表示**: 通知バー廃止。現行セッションの assistant 履歴へ載せる。muteはサイドバー。発火ルールは据え置き
- **応答高速化3点**: 返答本文のトークン小出し表示（Ollama `stream:true`）／感情・advisor 抽出の2発注を本文確定後の裏へ／think ON/OFF 判定のルール先行化（`core/routing/think_rules.py`）。advisor 結果は言い直し（置換）を退役し2通目メッセージ（`followup_reply`）で配達。設計書 §3.7 新設
- **ゲーム同居止血**: Pulse 文面生成を GPU 門番の内側へ・埋め込み bge-m3 を CPU 席固定（35B との VRAM 席取り合いで毎ターン再ロード約30秒が発生していた）
- **実機確認**: 上記3点＋2通目配達をSerina.bat起動で確認済み（詳細はDECISIONS参照）

## 直近完了（2026-07-19）

- **dormant 退役**: cloud 宛記憶パック／化粧版／idle 機微査定をコード・正典から撤去。Gemini はクエリのみ外聞き（advisor）を維持
- **persona Sleep 自律改訂の提案器**: セッション終了後 idle・ローカル暦日1日1回。材料は直近日記＋要約ブロック（気分軌跡なし）。JSON で可変1ブロックを提案→宿題箱→既存関所適用。`core/chores/persona_propose.py`
- **eval 未配線指標のゴールデン拡充**: 誤想起／時間クエリ／成長反映（構造ゲート）／訂正再発／継続性を配線。`golden_queries.json` に B 日常3件追加（平均96%）。`export_life` → `life/` ＋週次ログ。Pulse は雛形のみ（マスター記入待ち）
- 実行: `python tests/eval_suite.py`（決定論指標）／`python tests/eval_suite.py --live`（Ollama+実DB）

## 検討中・保留（条件成立まで実装しない。設計書 §5.3 と対）

| 項目 | 着手条件 |
|---|---|
| 二段検索（粗取得→精密再採点） | 記憶数千件超の実測でヒット率/速度が劣化したら |
| グラフ基盤（Graphiti 等） | Fact 台帳（設計書 §4.9）で不足が実測されたら |
| 身体レーン（VoiceLoop / PresencePet・音声・表情・画面知覚・キャラ固有TTS） | テキスト会話が十分と判断できたあとに別計画。D4a / D4b 契約は凍結（archive の合意台帳 参照） |
| 音声系評価指標（初音声秒数・割り込み成功率） | 身体レーン着手時に追加 |
| Presence（アバター常駐） | 身体レーンと同時期 |
| 外相談が弾かれたときの平易化再送 | 必要になったら別途設計 |
| 成長反映の会話live判定 | 構造ゲートの上に Qwen 応答内容判定を載せる必要が出たら |
| summaries/blocks.json と新正本の整合 | 記憶正本入れ直しの完了レビュー後、必要なら別スライス |

## 旧記憶（引っ越し元）

原本: `G:\AI\Serina` ／ 保全コピー: `legacy/`（git管理。正典 継承記憶r1.md・記憶.json・日記×4）

## 環境メモ

- メイン機: RTX2080S 専用8GB＋共有16GB / Win11（Qwen 応答 暖機後おおよそ15〜21秒/ターン）
- サブ機: RTX5070Ti Laptop 12GB ＋ iGPU Radeon610M（上位量子化用）
- Ollama: 導入済（bge-m3 / serina-qwen35-unc）
