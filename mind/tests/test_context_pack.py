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
    """§1.5: ①人格 ②思い出したこと ④粗要約 ⑤感情 ⑥絶対ルール ⑦細要約 ⑧発言"""
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
    idx_emotion = text.index("【今のセリナの心の状態】")
    idx_rules = text.index("【絶対ルール】")
    idx_fine = text.index("直近は晴れの話で盛り上がった")
    idx_utterance = text.index("今日は天気がいいね")

    assert idx_persona < idx_remembered < idx_summary < idx_emotion < idx_rules < idx_fine < idx_utterance


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


def test_local_turns_are_kept_in_recent_turns_text_compat() -> None:
    """recent_turns_text は互換のため残置（render ⑦ では使わない）。"""
    session = SessionState()
    session.add_turn(Turn(speaker="master", text="俺の住所教えるね", location="local"))

    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="そうだね",
    )

    assert "俺の住所教えるね" in pack.recent_turns_text


def test_recent_turns_window_keeps_only_latest_n() -> None:
    """§1.4: recent_turns_text 互換窓（pack.render ⑦ とは別経路）。"""
    session = SessionState()
    for i in range(10):
        session.add_turn(Turn(speaker="master", text=f"発言{i}"))
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="今",
        recent_turns_limit=4,
    )
    assert "発言0" not in pack.recent_turns_text
    assert "発言5" not in pack.recent_turns_text
    assert "発言6" in pack.recent_turns_text
    assert "発言9" in pack.recent_turns_text


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


def test_emotion_state_appears_before_absolute_rules() -> None:
    """§1.5段⑤: 感情状態は⑥絶対ルールの前"""
    from mind.core.state.emotion import EmotionState

    session = SessionState()
    session.fine_summary = "直近のやりとり要約"
    emotion = EmotionState()
    emotion.apply_affect_delta({"怒り": 0.8})

    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
        emotion=emotion,
    )
    text = pack.render()
    idx_emotion = text.index("【今のセリナの心の状態】")
    idx_rules = text.index("【絶対ルール】")
    idx_fine = text.index("直近のやりとり要約")
    assert idx_emotion < idx_rules < idx_fine
    assert "怒り" in pack.emotion_state_text


def test_emotion_state_default_text_when_emotion_not_given() -> None:
    session = SessionState()
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
    )
    assert "未接続" in pack.emotion_state_text


def test_emotion_state_no_movement_without_trailing_note() -> None:
    from mind.core.state.emotion import EmotionState

    session = SessionState()
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
        emotion=EmotionState(),
    )
    assert "穏やか" in pack.emotion_state_text
    assert "滲ませる" not in pack.emotion_state_text
    assert "※" not in pack.emotion_state_text


def test_render_emotion_for_pack_quantizes_max_affect_without_numbers() -> None:
    """生数値は渡さず、最大情動強度を文言バケット化する"""
    from mind.core.config import ThresholdsConfig
    from mind.core.context.emotion_render import render_emotion_for_pack
    from mind.core.state.emotion import EmotionState

    thresholds = ThresholdsConfig(fusen_confidence={}, mood_guard_max_delta_per_turn=0.1)
    emotion = EmotionState()
    emotion.apply_affect_delta({"怒り": 0.9, "悲しみ": 0.5, "喜び": 0.05})

    text = render_emotion_for_pack(emotion, thresholds)
    assert "0." not in text, "生数値をそのまま渡してはならない"
    assert "怒り" in text
    assert "悲しみ" not in text, "情動は最大軸のみ"
    assert "喜び" not in text, "しきい値未満の軸は言及しない"
    assert "滲ませる" not in text


def test_render_emotion_for_pack_shows_mood_dyad() -> None:
    """気分の喜び+信頼から一次ダイアド「愛情」が自然文に出る"""
    from mind.core.config import ThresholdsConfig
    from mind.core.context.emotion_render import render_emotion_for_pack
    from mind.core.state.emotion import EmotionState

    thresholds = ThresholdsConfig(
        fusen_confidence={},
        mood_guard_max_delta_per_turn=0.1,
        emotion_dyad_min=0.4,
    )
    emotion = EmotionState()
    emotion.apply_mood_delta({"喜び": 0.6, "信頼": 0.55}, max_delta_per_turn=0.6)

    text = render_emotion_for_pack(emotion, thresholds)
    assert "愛情" in text
    assert "底流" in text


def test_render_emotion_phrase_stable_within_same_bucket() -> None:
    """同一バケット中は言い回しを据え置く"""
    from mind.core.config import ThresholdsConfig
    from mind.core.context.emotion_render import EmotionRenderCache, render_emotion_for_pack
    from mind.core.state.emotion import EmotionState

    thresholds = ThresholdsConfig(fusen_confidence={}, mood_guard_max_delta_per_turn=0.1)
    cache = EmotionRenderCache()
    emotion = EmotionState()
    emotion.apply_affect_delta({"怒り": 0.45})

    first = render_emotion_for_pack(emotion, thresholds, cache=cache)
    emotion.apply_affect_delta({"怒り": -0.02})
    second = render_emotion_for_pack(emotion, thresholds, cache=cache)
    assert first == second


def test_render_emotion_phrase_changes_when_axis_changes_same_bucket() -> None:
    """同一バケットでも軸が変わったら言い回し（軸名）を更新する"""
    from mind.core.config import ThresholdsConfig
    from mind.core.context.emotion_render import EmotionRenderCache, render_emotion_for_pack
    from mind.core.state.emotion import EmotionState

    thresholds = ThresholdsConfig(fusen_confidence={}, mood_guard_max_delta_per_turn=0.1)
    cache = EmotionRenderCache()
    emotion = EmotionState()
    emotion.affect["怒り"] = 0.45
    first = render_emotion_for_pack(emotion, thresholds, cache=cache)
    emotion.affect["怒り"] = 0.0
    emotion.affect["悲しみ"] = 0.45
    second = render_emotion_for_pack(emotion, thresholds, cache=cache)
    assert "怒り" in first
    assert "悲しみ" in second
    assert first != second


def test_render_emotion_phrase_changes_when_bucket_changes() -> None:
    """バケットが変わったときだけ言い回しを引き直す"""
    from mind.core.config import ThresholdsConfig
    from mind.core.context.emotion_render import EmotionRenderCache, render_emotion_for_pack
    from mind.core.state.emotion import EmotionState

    thresholds = ThresholdsConfig(fusen_confidence={}, mood_guard_max_delta_per_turn=0.1)
    cache = EmotionRenderCache()
    emotion = EmotionState()
    emotion.apply_affect_delta({"怒り": 0.25})

    low = render_emotion_for_pack(emotion, thresholds, cache=cache)
    emotion.affect["怒り"] = 0.85
    high = render_emotion_for_pack(emotion, thresholds, cache=cache)
    assert low != high


def test_render_emotion_phrase_uses_real_randomness_not_hash() -> None:
    """2026-07-26 A7: hash()はプロセス内で決定的なため候補が死んでいた。
    random.seedを固定し、同一バケット・異なる軸で異なる語が選ばれうることを確認する
    （乱数依存のため『候補集合に含まれること』をassertする）。"""
    import random

    from mind.core.context.emotion_render import (
        AFFECT_BUCKET_VARIANTS,
        EmotionRenderCache,
        _affect_bucket,
        _pick_affect_phrase,
    )

    bucket = _affect_bucket(0.45)
    variants = AFFECT_BUCKET_VARIANTS[bucket]
    seen: set[str] = set()
    random.seed(0)
    for i in range(50):
        cache = EmotionRenderCache()  # 毎回新規キャッシュ→必ず引き直す
        phrase = _pick_affect_phrase(bucket, "怒り", cache)
        base = phrase.split("（")[0]
        assert base in variants
        seen.add(base)
    assert len(seen) > 1, "50回引いて候補が1種類しか出ないのは乱数が効いていない疑い"


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


# --- 2026-07-26 B1: マスターの様子（⑤末尾） --------------------------------


def test_pack_appends_master_observation_when_fresh() -> None:
    from datetime import datetime, timezone

    from mind.core.config import ThresholdsConfig
    from mind.core.state.relationship import RelationshipState

    session = SessionState()
    relationship = RelationshipState()
    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    relationship.current_turn_at = now
    relationship.observe(master_mood="機嫌が良さそう")

    pack = build_context_pack(
        persona_text="人格",
        absolute_rules="境界",
        session=session,
        master_utterance="やあ",
        relationship=relationship,
        thresholds=ThresholdsConfig(fusen_confidence={}, mood_guard_max_delta_per_turn=0.1),
        now=now,
    )
    text = pack.render()
    assert "マスターの様子: 機嫌が良さそう" in text
    # ⑤ブロック内（絶対ルールより前）に載る。新しい段は増やさない。
    assert text.index("マスターの様子") < text.index("【絶対ルール】")


def test_pack_omits_master_observation_when_absent() -> None:
    session = SessionState()
    pack = build_context_pack(
        persona_text="人格",
        absolute_rules="境界",
        session=session,
        master_utterance="やあ",
    )
    text = pack.render()
    assert "マスターの様子" not in text


def test_pack_omits_stale_master_observation() -> None:
    """2026-07-26 B1: 鮮度切れの観測はパックへ出さない（architecture-reviewer指摘）。"""
    from datetime import datetime, timedelta, timezone

    from mind.core.config import ThresholdsConfig
    from mind.core.state.relationship import RelationshipState

    session = SessionState()
    relationship = RelationshipState()
    observed_at = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    relationship.current_turn_at = observed_at
    relationship.observe(master_mood="疲れてそう")

    pack = build_context_pack(
        persona_text="人格",
        absolute_rules="境界",
        session=session,
        master_utterance="やあ",
        relationship=relationship,
        thresholds=ThresholdsConfig(fusen_confidence={}, mood_guard_max_delta_per_turn=0.1),
        now=observed_at + timedelta(days=3),
    )
    text = pack.render()
    assert "マスターの様子" not in text
    assert "疲れてそう" not in text


# --- Phase 3 Task 3-5: 欲求のパック表出 --------------------------------------


def test_desire_not_in_pack_when_gate_closed() -> None:
    """抑制門が閉じている間は欲求情報を一切載せない。"""
    from mind.core.config import ThresholdsConfig
    from mind.core.context.emotion_render import DESIRE_HIGH_LINE, DESIRE_TINT_PHRASE, render_emotion_for_pack
    from mind.core.state.desire import DesireState
    from mind.core.state.emotion import EmotionState

    thresholds = ThresholdsConfig(fusen_confidence={}, mood_guard_max_delta_per_turn=0.1)
    emotion = EmotionState()
    emotion.mood["喜び"] = 0.5
    emotion.affect["嫌悪"] = 0.7  # 門閉じ
    desire = DesireState()
    desire.level = 0.9
    text = render_emotion_for_pack(emotion, thresholds, desire=desire)
    assert DESIRE_HIGH_LINE not in text
    assert DESIRE_TINT_PHRASE not in text


def test_desire_tints_mood_when_level_below_threshold() -> None:
    """level < 0.6 は心情文への色添えのみ（独立文なし）。"""
    from mind.core.config import ThresholdsConfig
    from mind.core.context.emotion_render import DESIRE_HIGH_LINE, DESIRE_TINT_PHRASE, render_emotion_for_pack
    from mind.core.state.desire import DesireState
    from mind.core.state.emotion import EmotionState

    thresholds = ThresholdsConfig(fusen_confidence={}, mood_guard_max_delta_per_turn=0.1)
    emotion = EmotionState()
    emotion.mood["喜び"] = 0.5
    desire = DesireState()
    desire.level = 0.4
    text = render_emotion_for_pack(emotion, thresholds, desire=desire)
    assert DESIRE_TINT_PHRASE in text
    assert DESIRE_HIGH_LINE not in text
    # 2026-07-30是正: 恒真アサーション(text.count=="\n"==自身)を実チェックへ差し替え。
    # 「独立文を新設しない」の実体は、tintが単独行ではなく心情文と同じ行に
    # 連結されていること（マスター確認済み・2026-07-30: 現状のナレーション統一でよい）。
    lines = text.split("\n")
    tint_line = next(line for line in lines if DESIRE_TINT_PHRASE in line)
    assert tint_line != DESIRE_TINT_PHRASE, "色添えは既存の心情文と同じ行に連結されるべき（独立行にしない）"


def test_desire_independent_line_when_level_high() -> None:
    """level >= 0.6 は独立した一文。"""
    from mind.core.config import ThresholdsConfig
    from mind.core.context.emotion_render import DESIRE_HIGH_LINE, render_emotion_for_pack
    from mind.core.state.desire import DesireState
    from mind.core.state.emotion import EmotionState

    thresholds = ThresholdsConfig(fusen_confidence={}, mood_guard_max_delta_per_turn=0.1)
    emotion = EmotionState()
    emotion.mood["喜び"] = 0.5
    desire = DesireState()
    desire.level = 0.6
    text = render_emotion_for_pack(emotion, thresholds, desire=desire)
    assert DESIRE_HIGH_LINE in text
    assert DESIRE_HIGH_LINE in text.split("\n")


def test_desire_pack_text_has_no_concrete_word_examples_or_rules() -> None:
    """生成文言に具体ワード例・解釈規則の指示文字列が含まれない。"""
    from mind.core.config import ThresholdsConfig
    from mind.core.context.emotion_render import render_emotion_for_pack
    from mind.core.state.desire import DesireState
    from mind.core.state.emotion import EmotionState

    thresholds = ThresholdsConfig(fusen_confidence={}, mood_guard_max_delta_per_turn=0.1)
    emotion = EmotionState()
    emotion.mood["喜び"] = 0.5
    desire = DesireState()
    desire.level = 0.9
    text = render_emotion_for_pack(emotion, thresholds, desire=desire)
    forbidden = ("手を繋ぐ", "抱きしめる", "親密な意味で受け取れ", "という言葉を")
    for word in forbidden:
        assert word not in text
