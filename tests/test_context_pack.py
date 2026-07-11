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
from serina.core_v2.memory.store import MemoryRecord
from serina.core_v2.state.session import SessionState, Turn


def _memory(content: str, grade: int, cosmetic: str | None = None) -> MemoryRecord:
    return MemoryRecord(
        id=1,
        type="fact",
        content=content,
        importance=0.5,
        sensitivity_grade=grade,
        protection_grade="B",
        cosmetic_version=cosmetic,
        created_at="2026-01-01T00:00:00+00:00",
        last_accessed="2026-01-01T00:00:00+00:00",
    )


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


def test_long_term_memory_is_empty_placeholder_when_no_recall_given() -> None:
    session = SessionState()
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
    )
    assert pack.long_term_memories == []


def test_sensitivity_grade_2_never_enters_cloud_pack() -> None:
    """§4.2: 機微等級2はクラウド宛パックに絶対に載せない。§3.3個人情報フィルタ（Core専権）"""
    session = SessionState()
    recalled = [
        _memory("公開可能な好物の話", grade=0),
        _memory("本名フルセットと口座番号", grade=2),
    ]
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
        recalled_memories=recalled, destination_location="cloud",
    )
    assert any("公開可能な好物の話" in m for m in pack.long_term_memories)
    assert not any("本名フルセット" in m for m in pack.long_term_memories)


def test_sensitivity_grade_2_dropped_when_destination_unknown() -> None:
    """宛先未指定（None）は安全側＝クラウド扱いで間引く"""
    session = SessionState()
    recalled = [_memory("本名フルセットと口座番号", grade=2)]
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
        recalled_memories=recalled,
    )
    assert pack.long_term_memories == []


def test_all_grades_enter_local_pack_with_original_content() -> None:
    """§4.6-2: 機微2＝ローカルのみ＝ローカル宛パックには全等級を原文で載せる（記憶ブラックアウト回帰防止）"""
    session = SessionState()
    recalled = [
        _memory("公開可能な好物の話", grade=0),
        _memory("自宅は横浜市○○区△△1-2-3", grade=1, cosmetic="自宅は横浜市"),
        _memory("本名フルセットと口座番号", grade=2),
    ]
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
        recalled_memories=recalled, destination_location="local",
    )
    assert any("公開可能な好物の話" in m for m in pack.long_term_memories)
    assert any("△△1-2-3" in m for m in pack.long_term_memories), "ローカルでは化粧版でなく原文を使う"
    assert any("本名フルセット" in m for m in pack.long_term_memories), "等級2もローカルでは載る"


def test_local_turns_are_scrubbed_when_destination_is_cloud() -> None:
    """§3.3第3経路: クラウド行きpackでは過去のローカル担当ターンの原文をプレースホルダに置換"""
    from serina.core_v2.state.session import Turn

    session = SessionState()
    session.add_turn(Turn(speaker="master", text="俺の住所教えるね", location="local"))
    session.add_turn(Turn(speaker="serina", text="横浜市○○区△△1-2-3だね", location="local"))
    session.add_turn(Turn(speaker="master", text="今日はいい天気だね", location="cloud"))

    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="そうだね",
        destination_location="cloud",
    )

    assert "横浜市" not in pack.recent_turns_text
    assert "俺の住所教えるね" not in pack.recent_turns_text
    assert "（ローカルで交わした会話）" in pack.recent_turns_text
    assert "今日はいい天気だね" in pack.recent_turns_text


def test_local_turns_are_not_scrubbed_when_destination_is_local() -> None:
    from serina.core_v2.state.session import Turn

    session = SessionState()
    session.add_turn(Turn(speaker="master", text="俺の住所教えるね", location="local"))

    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="そうだね",
        destination_location="local",
    )

    assert "俺の住所教えるね" in pack.recent_turns_text


def test_sensitivity_grade_1_prefers_cosmetic_version_when_available() -> None:
    """§4.2: 化粧版がある場合はクラウド用言い換え版を優先する"""
    session = SessionState()
    recalled = [_memory("自宅は横浜市○○区△△1-2-3", grade=1, cosmetic="自宅は横浜市")]
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
        recalled_memories=recalled,
    )
    assert any("自宅は横浜市" == m for m in pack.long_term_memories)
    assert not any("△△1-2-3" in m for m in pack.long_term_memories)


def main() -> None:
    tests = [
        test_pack_sections_follow_layout_order,
        test_absolute_rules_appear_twice,
        test_long_term_memory_is_empty_placeholder_when_no_recall_given,
        test_sensitivity_grade_2_never_enters_cloud_pack,
        test_sensitivity_grade_2_dropped_when_destination_unknown,
        test_all_grades_enter_local_pack_with_original_content,
        test_local_turns_are_scrubbed_when_destination_is_cloud,
        test_local_turns_are_not_scrubbed_when_destination_is_local,
        test_sensitivity_grade_1_prefers_cosmetic_version_when_available,
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
