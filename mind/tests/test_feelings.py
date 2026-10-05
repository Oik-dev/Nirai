"""気持ち（core/feeling/）のテスト。LLM不要。設計書 §2.3。

守るもの（S2 の出口：気持ちが続き、時間で落ち着き、久しぶりだとうれしそう）：
- 出来事で動いた芯は、時間がたつと平常へ戻る。速い層は数時間、遅い層（気分）は次の日にも少し残る。
- つながりは会えない時間で減り、話すと満ちる。久しぶりに会えたときほど、同じ「近づいた」でもうれしい。
- 動く量は、端までの残りの割合。同じ向きが続いても、端に張り付かない。逆向きの出来事なら戻る。
- 夜は高ぶりの平常が下がって眠くなり、昼は上がる。
- 状態のファイルはない。気持ちの記録の最後の行から、いつでも同じ今の気持ちが計算し直せる。
- 文脈パックの⑤には、本人の言葉と素朴な体の感じだけが載る。数は載らない。古いマスターの様子は載らない。
- ページの芯の数はピーク・エンド（いちばん動いたときと終わりの平均）。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import load_thresholds
from mind.core.feeling.appraisal import Appraisal
from mind.core.feeling.body import Body, react, rhythm, sense, settle
from mind.core.feeling.feelings import Felt, Feelings, ago, flow_lines, peak_end
from mind.core.lifelog import FeelingLog

P = load_thresholds().feeling
JST = timezone(timedelta(hours=9))
NOON = datetime(2026, 10, 6, 14, 0, tzinfo=JST)
SRC = ["lifelog/conversation/2026-10-06.jsonl#1-2"]


def _appraisal(valence: str = "うれしい", arousal: str = "少し動いた", distance: str = "近づいた", **kw) -> Appraisal:
    return Appraisal(feeling=kw.get("feeling", "うれしい"), valence=valence, arousal=arousal, distance=distance,
                     master_state=kw.get("master_state", ""))


@pytest.fixture
def feelings(tmp_path: Path) -> Feelings:
    return Feelings(FeelingLog(tmp_path / "feeling"), P)


# --- 体の芯 ---------------------------------------------------------------------


def test_a_moved_heart_settles_back_with_time() -> None:
    start = Body(connection=1.0)
    moved = react(start, NOON, valence="とてもうれしい", arousal="大きく動いた", distance="変わらない", params=P)
    base, peak = sense(start, NOON, P), sense(moved, NOON, P)
    assert peak.valence > base.valence and peak.arousal > base.arousal
    later = lambda hours: sense(settle(moved, hours * 3600, P), NOON + timedelta(hours=hours), P)  # noqa: E731
    # 速い層は数時間で戻り、気分（遅い層）の余韻は次の日にも少し残る
    assert later(2).valence < peak.valence
    assert later(8).valence - sense(Body(connection=settle(moved, 8 * 3600, P).connection), NOON + timedelta(hours=8), P).valence < 0.1
    next_day = settle(moved, 24 * 3600, P)
    assert 0 < next_day.slow_valence < moved.slow_valence and abs(next_day.fast_valence) < 0.001


def test_long_absence_lowers_connection_and_reunion_is_happier() -> None:
    """同じ「近づいた」でも、会えなかった時間が長いほど、満ちる量も快の増え方も大きい（特例の文を書かずに出る）。"""
    after_chat = Body(connection=0.9)
    gains = []
    for hours in (1, 24, 24 * 7):
        apart = settle(after_chat, hours * 3600, P)
        at = NOON + timedelta(hours=hours)
        met = react(apart, at, valence="うれしい", arousal="少し動いた", distance="近づいた", params=P)
        gains.append((met.connection - apart.connection, sense(met, at, P).valence - sense(apart, at, P).valence))
    assert settle(after_chat, 7 * 86400, P).connection < settle(after_chat, 86400, P).connection < 0.9
    assert gains[0][0] < gains[1][0] < gains[2][0]
    assert gains[0][1] < gains[1][1] < gains[2][1]


def test_moves_are_a_share_of_the_room_left_so_nothing_sticks_at_the_edge() -> None:
    body, at = Body(connection=1.0), NOON
    for _ in range(30):
        body = react(body, at, valence="とてもうれしい", arousal="大きく動いた", distance="近づいた", params=P)
    top = sense(body, at, P)
    assert top.valence < 1.0 and top.arousal < 1.0  # 端に張り付かない
    hurt = react(body, at, valence="とても嫌", arousal="大きく動いた", distance="離れた", params=P)
    assert sense(hurt, at, P).valence < top.valence - 0.4  # 逆向きの出来事なら、ちゃんと戻る
    assert hurt.connection < body.connection


def test_unpleasant_and_drifting_apart_feel_bad() -> None:
    body = Body(connection=0.8)
    hurt = react(body, NOON, valence="嫌", arousal="少し動いた", distance="離れた", params=P)
    assert sense(hurt, NOON, P).valence < sense(body, NOON, P).valence
    assert hurt.connection < body.connection


def test_the_day_rhythm_lowers_arousal_at_night() -> None:
    night = NOON.replace(hour=2)
    assert rhythm(NOON, P) > rhythm(night, P)
    assert sense(Body(), NOON, P).arousal > sense(Body(), night, P).arousal


# --- 気持ちの記録から今の気持ち --------------------------------------------------------


def test_without_records_she_is_at_her_usual(feelings: Feelings) -> None:
    assert feelings.body(NOON) == Body(connection=P.initial_connection)
    assert feelings.body_words(NOON) == "ふだんどおり"


def test_the_present_is_recomputed_from_the_last_record(tmp_path: Path, feelings: Feelings) -> None:
    """状態のファイルはない。同じ記録を別の Feelings（再起動）で読んでも、同じ今の気持ちになる。"""
    feelings.feel(_appraisal("とてもうれしい", "大きく動いた"), source=SRC, at=NOON)
    later = NOON + timedelta(hours=3)
    again = Feelings(FeelingLog(tmp_path / "feeling"), P)
    assert again.sense(later) == feelings.sense(later)
    assert not list((tmp_path).glob("*.json"))


def test_a_turn_without_an_appraisal_still_counts_as_meeting(feelings: Feelings) -> None:
    before = feelings.sense(NOON)
    row = feelings.feel(None, source=SRC, at=NOON)
    assert row["evaluation"] is None and row["feeling"] == "" and row["kind"] == "turn"
    after = feelings.sense(NOON)
    assert after.connection > before.connection
    assert after.arousal == pytest.approx(before.arousal)  # 揺れは聞けていないので動かさない


def test_she_becomes_lonely_after_a_day_apart(feelings: Feelings) -> None:
    for minutes in range(0, 30, 10):
        feelings.feel(_appraisal(), source=SRC, at=NOON + timedelta(minutes=minutes))
    assert not feelings.lonely(NOON + timedelta(hours=1))
    assert feelings.lonely(NOON + timedelta(days=1, hours=6))
    assert "人恋しい" in feelings.body_words(NOON + timedelta(days=1, hours=6))


def test_body_words_are_plain_senses_not_emotion_names(feelings: Feelings) -> None:
    feelings.feel(_appraisal("とてもうれしい", "大きく動いた"), source=SRC, at=NOON)
    assert set(feelings.body_words(NOON).split("・")) == {"明るい", "高ぶっている"}
    late = datetime(2026, 10, 7, 1, 30, tzinfo=JST)
    assert "眠い" in feelings.body_words(late)


def test_pack_section_has_her_words_and_master_state_but_no_numbers(feelings: Feelings) -> None:
    words = ["おはよう、うれしい", "ちょっと照れた", "話せてよかった", "また明日も話したい"]
    for n, w in enumerate(words):
        feelings.feel(_appraisal(feeling=w, master_state="眠そう" if n == 3 else ""), source=SRC, at=NOON + timedelta(minutes=10 * n))
    now = NOON + timedelta(minutes=40)
    text = feelings.for_pack(now)
    assert "おはよう、うれしい" not in text  # 直近の3つまで
    assert text.index("ちょっと照れた") < text.index("また明日も話したい")  # 古い順
    assert "- 30分前：ちょっと照れた" in text and "- 10分前：また明日も話したい" in text
    assert "体の感じ：" in text
    assert "マスターの様子（10分前）：眠そう" in text
    assert not re.search(r"\d\.\d", text)  # 数は載せない
    stale = feelings.for_pack(NOON + timedelta(hours=7, minutes=30))
    assert "マスターの様子" not in stale and "7時間前：また明日も話したい" in stale


def test_ago_speaks_in_minutes_hours_and_days() -> None:
    assert [ago(s) for s in (10, 125, 3 * 3600 + 5, 2 * 86400)] == ["たった今", "2分前", "3時間前", "2日前"]


# --- 眠りの材料 -------------------------------------------------------------------


def _felt(minute: int, valence: float, arousal: float, stir: float, words: str = "") -> Felt:
    from mind.core.feeling.body import Sense

    return Felt(at=NOON + timedelta(minutes=minute), words=words, master_state="",
                sense=Sense(valence=valence, arousal=arousal, connection=0.8, stir=stir), source=())


def test_peak_end_averages_the_most_moved_moment_and_the_end() -> None:
    felts = [_felt(0, 0.2, 0.4, 0.1), _felt(5, 0.8, 0.9, 0.9), _felt(10, 0.3, 0.5, 0.2)]
    assert peak_end(felts) == {"valence": 0.55, "arousal": 0.7}
    assert peak_end([]) is None


def test_flow_lines_keep_first_and_last_when_thinning() -> None:
    felts = [_felt(n, 0.1, 0.4, 0.1, words=f"気持ち{n}") for n in range(20)] + [_felt(30, 0.1, 0.4, 0.1)]
    lines = flow_lines(felts, 5).splitlines()
    assert len(lines) == 5 and lines[0] == "14:00 気持ち0" and lines[-1] == "14:19 気持ち19"


def test_during_finds_the_feelings_of_that_conversation(feelings: Feelings) -> None:
    feelings.feel(_appraisal(feeling="海の話"), source=["lifelog/conversation/2026-10-06.jsonl#1-2"], at=NOON)
    feelings.feel(_appraisal(feeling="仕事の話"), source=["lifelog/conversation/2026-10-06.jsonl#3-4"], at=NOON + timedelta(minutes=1))
    assert [f.words for f in feelings.during({("2026-10-06", 4)})] == ["仕事の話"]
    assert [f.words for f in feelings.during({("2026-10-06", 1), ("2026-10-06", 3)})] == ["海の話", "仕事の話"]
