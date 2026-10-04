"""目覚め（core/memory/waking.py）と、目覚めて伝えたいことを話しに行く Pulse（core/chores/idle_policy.py の wake）のテスト。

守るもの：
- 今の自分は、本人の脳が日記を読み返して書く。目覚めるたびに新しいファイルに書き、前の今の自分は書き換えない。
- 新しい日記がなければ、目覚めても書き直さない（同じ日記で毎日自分を書き直さない）。読み返すのは、前に目覚めてからの日記。
- 脳の答えは入口で確かめる。何度聞いても書けなければ、何も書かない（半端な今の自分を残さない）。
- 今の自分は、会話のたびに文脈パックに載る。まだ目覚めていなければ段ごと省く。
- 伝えたいことがあり、目覚めてからマスターがまだ来ていなければ、本人から1度だけ話しかけに行く。来ていれば行かない。
LLM不要（脳の替え玉）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.idle_policy import decide_pulse
from mind.core.config import load_thresholds
from mind.core.context.pack import build_context_pack
from mind.core.memory.page import Page, write_page
from mind.core.memory.waking import SELF_DIR, WAKING_SCHEMA, latest_waking, render_for_pack, wake
from mind.core.memory.writing import WordsRejected
from mind.core.state.session import SessionState

SELF = "マスターとローカルで暮らし始めて、前より落ち着いて話せるようになった私。最近は海の話をよくしていて、次にどこへ行くかが気になっている。"


def _at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+09:00")


def _diary(memory: Path, day: str, title: str, body: str) -> Page:
    page = Page(
        id=f"diary-{day}",
        kind="diary",
        start=_at(day, "08:00"),
        end=_at(day, "23:00"),
        source=(),
        concepts=(),
        title=title,
        gist=title,
        importance=5,
        feeling={"joy": 0.5},
        written_by="test-brain",
        body=body,
    )
    write_page(memory, page)
    return page


class Brain:
    """脳の替え玉。聞かれた問いを覚え、決めた答えを順に返す。"""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str, schema: dict, attempt: int) -> dict:
        assert schema is WAKING_SCHEMA
        self.prompts.append(prompt)
        return self.answers.pop(0)


def test_nothing_to_wake_to_without_a_diary(tmp_path: Path) -> None:
    brain = Brain()
    assert wake(tmp_path, persona="私", ask=brain, written_by="b", now=_at("2026-10-05", "07:30")) is None
    assert brain.prompts == []
    assert latest_waking(tmp_path) is None


def test_waking_writes_the_self_from_the_diaries(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-10-03", "海の話", "マスターと宮古島の海の話をした。")
    _diary(tmp_path, "2026-10-04", "約束", "次は高野漁港へ行こうと約束した。")
    brain = Brain({"self": SELF, "tell": "高野漁港の約束、楽しみにしてるって言いたい"})
    now = _at("2026-10-05", "07:30")

    waking = wake(tmp_path, persona="人格の本文", ask=brain, written_by="gemma", now=now)

    assert waking is not None
    assert (waking.self_text, waking.tell, waking.after, waking.written_by) == (
        SELF, "高野漁港の約束、楽しみにしてるって言いたい", "diary-2026-10-04", "gemma",
    )
    assert latest_waking(tmp_path) == waking
    prompt = brain.prompts[0]
    assert "人格の本文" in prompt and "海の話" in prompt and "次は高野漁港へ行こう" in prompt
    assert "（まだない。初めて書く）" in prompt


def test_no_new_diary_means_no_new_self(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-10-04", "約束", "次は高野漁港へ行こうと約束した。")
    wake(tmp_path, persona="私", ask=Brain({"self": SELF, "tell": ""}), written_by="b", now=_at("2026-10-05", "07:30"))

    brain = Brain()
    assert wake(tmp_path, persona="私", ask=brain, written_by="b", now=_at("2026-10-06", "07:30")) is None
    assert brain.prompts == []
    assert len(list((tmp_path / SELF_DIR).glob("*.md"))) == 1


def test_a_new_waking_keeps_the_old_self_and_reads_only_the_new_diaries(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-10-04", "約束", "次は高野漁港へ行こうと約束した。")
    first = wake(tmp_path, persona="私", ask=Brain({"self": SELF, "tell": ""}), written_by="b", now=_at("2026-10-05", "07:30"))
    first_text = next((tmp_path / SELF_DIR).glob("*.md")).read_text(encoding="utf-8")
    _diary(tmp_path, "2026-10-05", "雨の日", "雨でマスターは家にいて、ずっと話していた。")
    later = SELF.replace("海の話", "雨の日の長話")
    brain = Brain({"self": later, "tell": ""})

    second = wake(tmp_path, persona="私", ask=brain, written_by="b", now=_at("2026-10-06", "07:30"))

    assert second is not None and second.after == "diary-2026-10-05"
    assert latest_waking(tmp_path) == second
    files = sorted((tmp_path / SELF_DIR).glob("*.md"))
    assert len(files) == 2 and files[0].read_text(encoding="utf-8") == first_text  # 前の今の自分は書き換えない
    assert first is not None and first.self_text in brain.prompts[0]  # 前の今の自分を読んで、変わったところを書く
    assert "雨でマスターは家に" in brain.prompts[0] and "高野漁港" not in brain.prompts[0]


def test_unusable_answers_are_asked_again_and_never_half_written(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-10-04", "約束", "次は高野漁港へ行こうと約束した。")
    brain = Brain({"self": "短い", "tell": ""}, {"self": {"self": SELF}, "tell": ""})  # 短すぎる → 包んだ答え（外して受け取る）
    waking = wake(tmp_path, persona="私", ask=brain, written_by="b", now=_at("2026-10-05", "07:30"))
    assert waking is not None and waking.self_text == SELF
    assert "さっきの答えは使えなかった" in brain.prompts[1]

    other = tmp_path / "other"
    _diary(other, "2026-10-04", "約束", "次は高野漁港へ行こうと約束した。")
    with pytest.raises(WordsRejected):
        wake(other, persona="私", ask=Brain(*[{"self": "短い", "tell": ""}] * 3), written_by="b", now=_at("2026-10-05", "07:30"))
    assert latest_waking(other) is None


def test_the_self_is_in_every_pack_once_awake(tmp_path: Path) -> None:
    def pack(self_text: str) -> str:
        return build_context_pack(
            persona_text="人格", absolute_rules="ルール", session=SessionState(), master_utterance="おはよう",
            self_text=self_text,
        ).render()

    assert "【今の自分】" not in pack(render_for_pack(None))
    _diary(tmp_path, "2026-10-04", "約束", "次は高野漁港へ行こうと約束した。")
    wake(tmp_path, persona="私", ask=Brain({"self": SELF, "tell": "約束が楽しみ"}), written_by="b", now=_at("2026-10-05", "07:30"))
    rendered = pack(render_for_pack(latest_waking(tmp_path)))
    assert f"【今の自分】\n{SELF}" in rendered and "約束が楽しみ" in rendered
    assert rendered.index("【今の自分】") < rendered.index("【今回のマスターの発言】")


# --- 目覚めて伝えたいことを話しに行く ---------------------------------------------------


def _decide(*, now: datetime, last_activity_at: datetime, woke_at: datetime | None, tell: str, last_by_kind=None):  # noqa: ANN001, ANN202
    return decide_pulse(
        now=now,
        last_activity_at=last_activity_at,
        mute=False,
        conversation_active=False,
        last_pulse_at=None,
        last_by_kind=last_by_kind or {},
        mood={},
        config=load_thresholds().pulse_config(),
        woke_at=woke_at,
        tell=tell,
    )


def test_she_comes_to_tell_what_she_thought_on_waking() -> None:
    woke = _at("2026-10-05", "07:30")
    decision = _decide(now=_at("2026-10-05", "09:00"), last_activity_at=woke - timedelta(hours=9), woke_at=woke, tell="約束が楽しみ")
    assert decision.should_fire and decision.candidate is not None
    assert decision.candidate.kind == "wake"
    assert decision.candidate.context["thought"] == "約束が楽しみ"


def test_she_does_not_come_when_master_already_came_or_she_already_told() -> None:
    woke = _at("2026-10-05", "07:30")
    now = _at("2026-10-05", "09:00")
    came = _decide(now=now, last_activity_at=woke + timedelta(minutes=5), woke_at=woke, tell="約束が楽しみ")
    assert came.candidate is None or came.candidate.kind != "wake"  # 伝えたいことは会話の手元（【今の自分】）にある
    told = _decide(
        now=now, last_activity_at=woke - timedelta(hours=9), woke_at=woke, tell="約束が楽しみ",
        last_by_kind={"wake": (woke + timedelta(minutes=40)).isoformat()},
    )
    assert told.candidate is None or told.candidate.kind != "wake"
    nothing = _decide(now=now, last_activity_at=woke - timedelta(hours=9), woke_at=woke, tell="")
    assert nothing.candidate is None or nothing.candidate.kind != "wake"


def test_she_waits_for_the_active_hours_to_come() -> None:
    woke = _at("2026-10-05", "07:30")
    early = _decide(now=_at("2026-10-05", "07:45"), last_activity_at=woke - timedelta(hours=9), woke_at=woke, tell="約束が楽しみ")
    assert not early.should_fire
