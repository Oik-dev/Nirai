# AI作業の補足

## 目的

Serinaは長期記憶を持つ個人用パートナーAIである。記憶データは本人性に関わる保護対象として扱う。

## 保護

- Serinaのイデア（`D:\Products\Residents\Serina`。人格・記憶DB・状態）は、通常の実装作業で変更しない。精神がどのイデアを使うかは環境変数 `NIRAI_IDEA` で決まる（`core/idea.py`）。
- 統合、削除、書き換えの前には、内容・影響・戻す方法を日本語で報告する。
- 記憶データを大きく変える前には `tools/backup_db.py` で復元用の控えを取る。

## 必要なときだけ読む文書

- 全体の索引：`docs/INDEX.md`
- 設計：`docs/設計書.md`
- 現在地：`docs/MILESTONE.md`

## 確認

Pythonは精神（mind）専用の `.venv`（`requirements.txt`）。通常の確認は `.venv\Scripts\python -m pytest tests/ -q`。テストは使い捨てのイデアで動き、本物のイデアには触れない。記憶の想起に関わる変更だけ、必要に応じて、`NIRAI_IDEA` にイデアを指定して `.venv\Scripts\python tests/smoke_bge_m3_recall.py` も行う。記憶の良し悪しは、Serinaの記憶テスト（`python -m mind.memory_test --idea <イデア> [--memory episodic]`。`docs/plans/長期記憶の作り直し.md` §11）で測る。作り直した記憶（`core/memory/` の page・recall など。同 §15）は、M4までは写しのイデアだけで動かす。問題はSerinaの人生からできている。Claudeが中身を読むのは、このテストを作り・監査するときと、最初の記憶づくりの整理のときだけ（同 §13）。
