"""文脈パック工場のテスト。設計書v2 §1.4(三段重ね), §1.5(配置規約)

Phase1範囲: 短期(直近会話)・中期(セッション要約)のみ。長期(記憶DB想起)はPhase2で接続。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.context.pack import build_context_pack
from serina.core_v2.state.session import SessionState, Turn


def test_pack_sections_follow_layout_order() -> None:
    """§1.5: ①人格 ②長期記憶 ③セッション要約 ④直近会話 ⑤絶対ルール再掲 ⑥今回の発言"""
    session = SessionState()
    session.rolling_summary = "今日は朝から天気の話をした"
    session.add_turn(Turn(speaker="master", text="おはよう"))
    session.add_turn(Turn(speaker="serina", text="おはようございます"))

    pack = build_context_pack(
        persona_text="価値観: 誠実であること",
        absolute_rules="機微情報を漏らさない",
        session=session,
        master_utterance="今日は天気がいいね",
    )

    text = pack.render()
    idx_persona = text.index("価値観: 誠実であること")
    idx_long_term = text.index("【想起された長期記憶】")
    idx_summary = text.index("今日は朝から天気の話をした")
    idx_recent = text.index("おはようございます")
    idx_rules_repeat = text.rindex("機微情報を漏らさない")
    idx_utterance = text.index("今日は天気がいいね")

    assert idx_persona < idx_long_term < idx_summary < idx_recent < idx_rules_repeat < idx_utterance


def test_absolute_rules_appear_twice() -> None:
    """§1.5: 絶対ルールは冒頭と末尾に二重掲示する"""
    session = SessionState()
    pack = build_context_pack(
        persona_text="人格",
        absolute_rules="人格が壊れるルール",
        session=session,
        master_utterance="こんにちは",
    )
    text = pack.render()
    assert text.count("人格が壊れるルール") == 2


def test_long_term_memory_is_empty_placeholder_in_phase1() -> None:
    session = SessionState()
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
    )
    assert pack.long_term_memories == []


def main() -> None:
    tests = [
        test_pack_sections_follow_layout_order,
        test_absolute_rules_appear_twice,
        test_long_term_memory_is_empty_placeholder_in_phase1,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
