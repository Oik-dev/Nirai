"""文脈パック工場のテスト。設計書 §1.4(三段重ね), §1.5(配置規約)

パックは常にローカル向け。思い出したことは、長期記憶が浮かべた文をそのまま載せ、何も浮かばなければ段ごと省く。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.context.pack import build_context_pack
from mind.core.state.session import SessionState, Turn


def test_pack_sections_follow_layout_order() -> None:
    """§1.5: ①人格 ②思い出したこと ④粗要約 ⑤気持ち ⑥絶対ルール ⑦細要約 ⑧発言"""
    session = SessionState()
    session.rolling_summary = "今日は朝から天気の話をした"
    session.fine_summary = "直近は晴れの話で盛り上がった"
    session.add_turn(Turn(speaker="master", text="おはよう"))
    session.add_turn(Turn(speaker="serina", text="おはようございます"))

    pack = build_context_pack(
        persona_text="価値観: 誠実であること",
        absolute_rules="機微情報を漏らさない",
        session=session,
        master_utterance="今日は天気がいいね",
        remembered=["（2025年3月7日、1年7か月前）海の話：約束の海"],
    )

    text = pack.render()
    idx_persona = text.index("価値観: 誠実であること")
    idx_remembered = text.index("【思い出したこと】")
    idx_summary = text.index("今日は朝から天気の話をした")
    idx_feeling = text.index("【今のセリナの心の状態】")
    idx_rules = text.index("【絶対ルール】")
    idx_fine = text.index("直近は晴れの話で盛り上がった")
    idx_utterance = text.index("今日は天気がいいね")

    assert idx_persona < idx_remembered < idx_summary < idx_feeling < idx_rules < idx_fine < idx_utterance


def test_absolute_rules_appear_once() -> None:
    """§1.5: 絶対ルールは⑥に1回のみ（冒頭二重掲示しない）"""
    session = SessionState()
    pack = build_context_pack(
        persona_text="人格",
        absolute_rules="人格が壊れるルール",
        session=session,
        master_utterance="こんにちは",
    )
    text = pack.render()
    assert text.count("人格が壊れるルール") == 1
    assert "【絶対ルール（再掲）】" not in text


def test_remembered_section_is_omitted_when_nothing_came_to_mind() -> None:
    """何も浮かばなかったら黙る：思い出したことの段ごと載せない（「記憶なし」のような文も入れない）。"""
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=SessionState(), master_utterance="やあ",
    )
    assert pack.remembered == ()
    assert "【思い出したこと】" not in pack.render()


def test_remembered_texts_enter_the_pack_as_they_are() -> None:
    """長期記憶が渡した文（いつのことかの添え書きを含む）を、そのまま載せる。"""
    remembered = ["（2025年3月7日、1年7か月前）最初の会話：はじめまして", "（いつのことかは分からない）約束：高野漁港"]
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=SessionState(), master_utterance="やあ",
        remembered=remembered,
    )
    text = pack.render()
    assert all(r in text for r in remembered)


def test_recent_turns_window_keeps_only_latest_n() -> None:
    """§1.5 ⑦: 要約が未到着のあいだは、直近 N ターンの原文だけを暫定で載せる。"""
    session = SessionState()
    for i in range(10):
        session.add_turn(Turn(speaker="master", text=f"発言{i}"))
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="今",
        recent_turns_limit=4,
    )
    assert "発言0" not in pack.fine_summary
    assert "発言5" not in pack.fine_summary
    assert "発言6" in pack.fine_summary
    assert "発言9" in pack.fine_summary


def test_fine_summary_used_in_recent_section_not_raw_turns() -> None:
    """§1.5 ⑦: 直近の会話は fine_summary。原文ダンプは載せない。"""
    session = SessionState()
    session.fine_summary = "マスターが天気の話をしたあと、セリナが返した"
    session.add_turn(Turn(speaker="master", text="おはよう"))
    session.add_turn(Turn(speaker="serina", text="おはようございます"))

    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
    )
    text = pack.render()
    assert "マスターが天気の話をしたあと" in text
    assert "おはようございます" not in text


def test_fine_summary_empty_shows_placeholder() -> None:
    session = SessionState()
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
    )
    text = pack.render()
    assert "【直近の会話】\n（まだ要約なし）" in text


def test_fine_summary_falls_back_to_recent_turns_when_empty() -> None:
    """要約未到着時は直近原文を暫定掲載（接続切れ防止）。"""
    session = SessionState()
    session.add_turn(Turn(speaker="master", text="最初の話題"))
    session.add_turn(Turn(speaker="serina", text="了解です"))
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="次",
    )
    text = pack.render()
    assert "最初の話題" in text
    assert "了解です" in text


def test_rolling_summary_is_passed_through() -> None:
    """要約のクラウド伏せは退役。原文のまま載る。"""
    session = SessionState()
    session.rolling_summary = "APIキー sk-ant-abcdefghijklmnopqrstuvwxyz012345 を話した"
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
    )
    assert "sk-ant-" in pack.rolling_summary


def test_static_head_is_prefix_of_render() -> None:
    """B4: 静的先頭（人格のみ）が render の先頭に固定される。"""
    from mind.core.context.pack import STATIC_HEAD_MARKER, render_static_head

    session = SessionState()
    pack = build_context_pack(
        persona_text="価値観: 誠実",
        absolute_rules="境界",
        session=session,
        master_utterance="こんにちは",
    )
    text = pack.render()
    assert text.startswith(STATIC_HEAD_MARKER)
    head = render_static_head(persona_text="価値観: 誠実")
    assert text.startswith(head)


def test_static_head_is_persona_only() -> None:
    """§1.5 ①: 静的先頭は persona のみ。絶対ルールは含めない。"""
    session = SessionState()
    pack = build_context_pack(
        persona_text="人格本文",
        absolute_rules="境界ルール",
        session=session,
        master_utterance="やあ",
        remembered=["思い出したこと"],
    )
    text = pack.render()
    idx_persona = text.index("人格本文")
    idx_rules = text.index("境界ルール")
    idx_remembered = text.index("【思い出したこと】")
    assert idx_persona < idx_remembered
    assert idx_rules > idx_remembered




def test_feeling_text_appears_before_absolute_rules() -> None:
    """§1.5段⑤: 気持ちは⑥絶対ルールの前。Core が組んだ文がそのまま入り、数は入らない。"""
    session = SessionState()
    session.fine_summary = "直近のやりとり要約"
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
        feeling_text="気持ちの流れ：\n- 10分前：ちょっと照れくさい\n体の感じ：明るい",
    )
    text = pack.render()
    idx_feeling = text.index("【今のセリナの心の状態】")
    idx_rules = text.index("【絶対ルール】")
    idx_fine = text.index("直近のやりとり要約")
    assert idx_feeling < idx_rules < idx_fine
    assert "10分前：ちょっと照れくさい" in text


def test_feeling_default_text_when_not_given() -> None:
    session = SessionState()
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
    )
    assert "未接続" in pack.render()
