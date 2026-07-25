# Serina 工程表（MILESTONE）

最終更新: 2026-07-26 ／ 正典: `設計書.md` ／ 索引: `INDEX.md`

> 中断復帰用。**実装済みの設計はすべて正典（`設計書.md`）にあり、本書は「まだ入っていないもの」だけを管理する。**完了した工程の履歴は `archive/DECISIONS.md` と Git を参照。
> 体制: 設計は Claude Code、実装は Sonnet 級が正典を頼りに実施（設計書 §5.5）。

## 現在地（1行）

日記キャッチアップの持ち越し2件（I-3・I-4）を解消・completion-review完了（Assessment可、2回目で決着）。

## 残作業（優先順）

- **I-a（次回フォローアップ）**: 日記の日付ラベルが対象日の翌日になる不整合。`created_at`を対象Serina日の終わり（D+1 07:00 JST）にした副作用で、想起パック表示（`core/context/memory_time.py`）・削除警告文（`core/memory/message_delete.py`）・アルバム表示（`app/web/app.js`）が`created_at[:10]`（＝翌日）をラベルに使う一方、プロンプト本文の対象日（`target_date`）は当日のまま。表示側のラベル算出のみ直す（`created_at`自体の前倒しは`diary_cascade`の材料窓を壊すため不可）。詳細: `archive/DECISIONS.md` 2026-07-26
- （任意）`summaries/blocks.json` と新正本の整合は別タスク

## 検討中・保留（条件成立まで実装しない。設計書 §5.3 と対）

| 項目 | 着手条件 |
|---|---|
| 二段検索（粗取得→精密再採点） | 記憶数千件超の実測でヒット率/速度が劣化したら |
| グラフ基盤（Graphiti 等） | Fact 台帳（設計書 §4.9）で不足が実測されたら |
| 身体レーン（VoiceLoop / PresencePet・音声・表情・画面知覚・キャラ固有TTS） | テキスト会話が十分と判断できたあとに別計画。D4a / D4b 契約は凍結（設計書 §5.3） |
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
- Ollama: 導入済（bge-m3 / serina-gemma4-unc。2026-07-25にserina-qwen35-uncから移行）
