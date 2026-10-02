# AI作業の補足

## 目的

Serinaは長期記憶を持つ個人用パートナーAIである。記憶データは本人性に関わる保護対象として扱う。

## 保護

- Serinaの魂（`D:\Products\Residents\Serina`。人格・記憶DB・状態）は、通常の実装作業で変更しない。心がどの魂を使うかは環境変数 `NIRAI_SOUL` で決まる（`core/soul.py`）。
- 統合、削除、書き換えの前には、内容・影響・戻す方法を日本語で報告する。
- 記憶データを大きく変える前には `tools/backup_db.py` で復元用の控えを取る。

## 必要なときだけ読む文書

- 全体の索引：`docs/INDEX.md`
- 設計：`docs/設計書.md`
- 現在地：`docs/MILESTONE.md`

## 確認

Pythonは心専用の `.venv`（`requirements.txt`）。通常の確認は `.venv\Scripts\python -m pytest tests/ -q`。テストは使い捨ての魂で動き、本物の魂には触れない。記憶の想起に関わる変更だけ、必要に応じて、`NIRAI_SOUL` に魂を指定して `.venv\Scripts\python tests/smoke_bge_m3_recall.py` も行う。
