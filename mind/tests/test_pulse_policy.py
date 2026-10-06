"""Pulse 発火判定の決定論テスト（設計書 §2.8）。

守るもの：
- 本人から話しかけに行くわけは3つ。目覚めて伝えたいこと、約束や予定の日が来たこと（その日）、人恋しさ（つながり）の順。
  「無操作45分」や「気分が閾値外」では行かない。
- その日に行くのは、今日まだマスターが来ておらず、本人も今日まだ話しかけていないときに1度だけ。
- 人恋しくても、来てよい時間帯・深夜・mute・会話中・間隔の安全柵は同じにかかる。
- 起動してからマスターがまだ来ていなくても、人恋しければ行く（つながりは気持ちの記録から分かる）。
LLM不要。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.idle_policy import (
    PulseConfig,
    decide_pulse,
    is_late_night,
    should_suppress_pulse,
)

CFG = PulseConfig(
    active_hour_start=8,
    active_hour_end=22,
    same_kind_gap_seconds=10800,
    min_interval_seconds=3600,
    late_night_start=23,
    late_night_end=7,
)


def _local_at(hour: int, minute: int = 0) -> datetime:
    """ローカル暦の指定時刻（TZ 依存テストを安定化）。"""
    base = datetime.now().astimezone()
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


NOW = _local_at(14)


def _ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


def _decide(**overrides):  # noqa: ANN003, ANN202
    kwargs = dict(
        now=NOW, mute=False, conversation_active=False, last_pulse_at=None, last_by_kind={}, config=CFG,
        lonely=False, master_spoke_at=_ago(30 * 3600),
    )
    return decide_pulse(**{**kwargs, **overrides})


def test_suppress_when_mute() -> None:
    reason = should_suppress_pulse(
        now=NOW, mute=True, conversation_active=False,
        last_pulse_at=None, config=CFG,
    )
    assert reason == "mute"


def test_suppress_late_night() -> None:
    midnight = _local_at(2)
    assert is_late_night(now=midnight, late_night_start=23, late_night_end=7)
    reason = should_suppress_pulse(
        now=midnight, mute=False, conversation_active=False,
        last_pulse_at=None, config=CFG,
    )
    assert reason == "late_night"


def test_suppress_during_conversation() -> None:
    reason = should_suppress_pulse(
        now=NOW, mute=False, conversation_active=True,
        last_pulse_at=None, config=CFG,
    )
    assert reason == "conversation_active"


def test_lonely_goes_to_see_master() -> None:
    d = _decide(lonely=True)
    assert d.should_fire is True
    assert d.candidate is not None and d.candidate.kind == "connection"
    assert d.candidate.context["since_master_spoke"] == "1日"  # 文面の材料：どれだけ会っていないか


def test_not_lonely_stays_quiet_however_long_idle() -> None:
    """無操作がどれだけ続いても、人恋しくなければ行かない（「45分の無操作」の規則はない）。"""
    assert _decide(lonely=False, master_spoke_at=_ago(5 * 3600)).should_fire is False


def test_lonely_without_master_since_startup_still_goes() -> None:
    d = _decide(lonely=True, master_spoke_at=None)
    assert d.should_fire is True
    assert "since_master_spoke" not in d.candidate.context


def test_lonely_respects_safety_rails() -> None:
    assert _decide(lonely=True, conversation_active=True).should_fire is False
    assert _decide(lonely=True, mute=True).should_fire is False
    assert _decide(lonely=True, now=_local_at(22, 30)).should_fire is False  # 来てよい時間帯の外
    assert _decide(lonely=True, last_pulse_at=_ago(1800).isoformat()).should_fire is False  # 最短間隔
    assert _decide(lonely=True, last_by_kind={"connection": _ago(7200).isoformat()}).should_fire is False  # 同種3時間
    assert _decide(lonely=True, last_by_kind={"connection": _ago(11000).isoformat()}).should_fire is True


def test_waking_thought_comes_before_loneliness() -> None:
    woke = _ago(3600)
    d = _decide(lonely=True, woke_at=woke, tell="夢の話をしたい", master_spoke_at=_ago(40 * 3600))
    assert d.candidate.kind == "wake"
    assert d.candidate.context["thought"] == "夢の話をしたい"
    told = {"wake": _ago(600).isoformat()}
    assert _decide(lonely=True, woke_at=woke, tell="夢の話をしたい", last_by_kind=told).candidate.kind == "connection"


def _today_at(hour: int) -> datetime:
    return NOW.replace(hour=hour, minute=0)


def test_the_day_of_a_promise_she_comes_once_if_master_has_not() -> None:
    due = ("週末に海の話をする約束",)
    decision = _decide(due_today=due)
    assert decision.should_fire and decision.candidate.kind == "day"
    assert decision.candidate.context == {"reason": "the_day", "today": ["週末に海の話をする約束"]}

    assert not _decide(due_today=due, master_spoke_at=_today_at(9)).should_fire  # 今日もうマスターが来た
    assert not _decide(due_today=due, last_pulse_at=_today_at(9).isoformat()).should_fire  # 今日もう話しかけた
    assert not _decide(due_today=()).should_fire  # その日でなければ行かない（人恋しくもない）
    assert not _decide(due_today=due, now=_local_at(23, 30)).should_fire  # 深夜は行かない


def test_the_day_comes_after_a_waking_thought_and_before_loneliness() -> None:
    woke = _today_at(7)
    first = _decide(due_today=("約束",), lonely=True, woke_at=woke, tell="約束の日だねって言いたい")
    assert first.candidate.kind == "wake"
    assert _decide(due_today=("約束",), lonely=True).candidate.kind == "day"
