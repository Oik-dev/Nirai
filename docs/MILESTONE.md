# Serina 工程表（MILESTONE）

最終更新: 2026-07-27 ／ 正典: `設計書.md` ／ 索引: `INDEX.md`

> 中断復帰用。**実装済みの設計はすべて正典（`設計書.md`）にあり、本書は「まだ入っていないもの」だけを管理する。**完了した工程の履歴は `archive/DECISIONS.md` と Git を参照。
> 体制: 設計は Claude Code、実装は Sonnet 級が正典を頼りに実施（設計書 §5.5）。

## 現在地（1行）

総合レビュー是正＋Minor持ち越し解消＋日記metadata.target_date恒久解、完了・マージ済み。予定機能Phase1（Task1-1〜1-7）完了・マージ済み（2026-07-27）。次はPhase H（hypothesis保存期間管理）→Phase2（平常値ドリフト）→Phase3（欲求層）。実装計画: `docs/plans/2026-07-26_予定機能_平常値ドリフト_欲求層_実装計画.md`。

## 残作業（優先順）

- **B2適用後の初回本番起動前に控えを取る**: `facts_vec`スキーマが`data/serina_memory.db`へ初めて追加される。`python tools/backup_db.py`を1回実行してから起動すること。
- **Phase H（hypothesis保存期間管理）→ Phase2（平常値ドリフト）→ Phase3（欲求層）**: 実装計画書の「実行順序まとめ」参照。Task 0-2（tombstone_factのembedding削除拡張）は完了済みでPhase Hの前提を満たす。
- （任意）`summaries/blocks.json` と新正本の整合は別タスク
- （任意）既存episodicへ`target_date`をbackfillするツール（タグ無し行はヒューリスティック残置で実害なし）
- （埋め込みモデル差し替え時）`python tools/rebuild_index.py` と `python tools/rebuild_facts_index.py` の両方を実行すること

## 検討中・保留（条件成立まで実装しない。設計書 §5.3 と対）

| 項目 | 着手条件 |
|---|---|
| 二段検索（粗取得→精密再採点） | 記憶数千件超の実測でヒット率/速度が劣化したら |
| グラフ基盤（Graphiti 等） | Fact 台帳（設計書 §4.9）で不足が実測されたら |
| 身体レーン（VoiceLoop / PresencePet・音声・表情・画面知覚・キャラ固有TTS） | テキスト会話が十分と判断できたあとに別計画。D4a / D4b 契約は凍結（設計書 §5.3） |
| 音声系評価指標（初音声秒数・割り込み成功率） | 身体レーン着手時に追加 |
| Presence（アバター常駐） | 身体レーンと同時期 |
| 外相談が弾かれたときの平易化再送 | 必要になったら別途設計 |
| 成長反映の会話live判定 | 構造ゲートの上に Ollama 応答内容判定を載せる必要が出たら |
| summaries/blocks.json と新正本の整合 | 記憶正本入れ直しの完了レビュー後、必要なら別スライス |
| facts台帳の tombstone/superseded 行の物理削除経路 | embedding除去（Task 0-2）後もfacts行数が実測で問題になったら |

## 旧記憶（引っ越し元）

原本: `G:\AI\Serina` ／ 保全コピー: `legacy/`（git管理。正典 継承記憶r1.md・記憶.json・日記×4）

## 環境メモ

- メイン機: RTX2080S 専用8GB＋共有16GB / Win11（Ollama 応答 暖機後おおよそ15〜21秒/ターン）
- サブ機: RTX5070Ti Laptop 12GB ＋ iGPU Radeon610M（上位量子化用）
- Ollama: 導入済（bge-m3 / serina-gemma4-unc。2026-07-25にserina-qwen35-uncから移行）
