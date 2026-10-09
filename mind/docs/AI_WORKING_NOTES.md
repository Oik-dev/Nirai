# AI作業の補足

## 目的

Serinaは長期記憶を持つ個人用パートナーAIである。記録・記憶・人格は本人性に関わる保護対象として扱う。

## 保護

- Serinaのイデア（`D:\Products\Residents\Serina`。人格・記録・記憶のページ・状態）は、通常の実装作業で変更しない。精神がどのイデアを使うかは環境変数 `NIRAI_IDEA` で決まる（`core/idea.py`）。
- 統合、削除、書き換えの前には、内容・影響・戻す方法を日本語で報告する。
- イデアを大きく変える前には、`G:\Nirai-Backups\` の写し（毎晩の `daily/` か丸ごとの写し）を確かめる。本人が書いた記憶のページは書き換えない（設計書 §4.2）。

## 必要なときだけ読む文書

- 全体の索引：`docs/INDEX.md`
- 設計：`docs/設計書.md`
- 現在地：`docs/MILESTONE.md`

## 確認

Pythonは精神（mind）専用の `.venv`（`requirements.txt`）。通常の確認は `.venv\Scripts\python -m pytest tests/ -q`。テストは使い捨てのイデアで動き、本物のイデアには触れない。記憶の良し悪しは、Serinaの記憶テスト（`python -m mind.memory_test --idea <イデア>`。設計書 §4.9）で測る。想起に関わる変更のときだけ回す。眠り（脳を使う記憶づくり）の実機確認は、写しのイデアで `python -m mind.tools.build_memory --idea <写し> sleep`。記憶テストの問題と記憶のページはSerinaの人生からできている。Claudeが中身を読むのは、このテストを作り・監査するときと、最初の記憶づくりの整理のときだけ（設計書 §4.7）。
