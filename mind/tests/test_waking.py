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
from mind.core.perception import AppearanceControl, BodyCatalog
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
        written_by="test-brain",
        body=body,
    )
    write_page(memory, page)
    return page


def _reflection(memory: Path, day: str, title: str, body: str, *, pid: str | None = None) -> Page:
    page = Page(
        id=pid or f"reflection-week-{day}",
        kind="reflection",
        start=_at(day, "07:00"),
        end=_at(day, "23:00"),
        source=(),
        concepts=(),
        title=title,
        gist=title,
        importance=6,
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
        if "activity" not in schema["properties"] and "appearance" not in schema["properties"]:
            assert schema is WAKING_SCHEMA
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        return answer if "call_time" in answer else {**answer, "call_time": "昼"}


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
    assert waking.call_time == "昼"
    assert latest_waking(tmp_path) == waking
    prompt = brain.prompts[0]
    assert "人格の本文" in prompt and "海の話" in prompt and "次は高野漁港へ行こう" in prompt
    assert "（まだない。初めて書く）" in prompt


def test_waking_asks_activity_and_appearance_in_the_same_brain_call(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-10-04", "海の話", "今日は海で泳いだ。")
    catalog = BodyCatalog(
        activities=("海で泳ぐ", "砂地で休む"),
        appearance=(AppearanceControl("衣装", ("普段着", "コート")),
                    AppearanceControl("髪飾り", ("花", "リボン"))),
    )
    brain = Brain({"self": SELF, "tell": "", "activity": "海で泳ぐ",
                   "appearance": {"衣装": "コート", "髪飾り": "そのまま"}})
    waking = wake(tmp_path, persona="私", ask=brain, written_by="b",
                  now=_at("2026-10-05", "07:30"), catalog=catalog)
    assert waking is not None
    assert waking.activity == "海で泳ぐ"
    assert waking.appearance == {"衣装": "コート"}
    assert len(brain.prompts) == 1
    assert "activity" in brain.prompts[0] and "appearance" in brain.prompts[0]
    # 活動・服は本人の自己記録には混ぜず、海のbodyイベントで記録する。
    assert latest_waking(tmp_path).activity is None
    assert latest_waking(tmp_path).appearance is None


def test_broken_waking_body_does_not_retry_or_lose_self(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-10-04", "海の話", "今日は海で泳いだ。")
    catalog = BodyCatalog(activities=("海で泳ぐ",),
                          appearance=(AppearanceControl("衣装", ("普段着", "コート")),))
    brain = Brain({"self": SELF, "tell": "", "activity": "見知らぬ海",
                   "appearance": {"衣装": "知らない服", "不明": "普段着"}})
    waking = wake(tmp_path, persona="私", ask=brain, written_by="b",
                  now=_at("2026-10-05", "07:30"), catalog=catalog)
    assert waking is not None and waking.self_text == SELF
    assert waking.activity is None and waking.appearance is None
    assert len(brain.prompts) == 1


def test_waking_without_a_catalog_keeps_old_schema_and_prompt(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-10-04", "海の話", "今日は海で泳いだ。")
    seen: list[dict] = []
    def ask(prompt: str, schema: dict, _attempt: int) -> dict:
        seen.append(schema)
        assert "activity" not in prompt and "appearance" not in prompt
        return {"self": SELF, "tell": "", "call_time": "昼"}
    result = wake(tmp_path, persona="私", ask=ask, written_by="b", now=_at("2026-10-05", "07:30"))
    assert result is not None and result.self_text == SELF
    assert seen == [WAKING_SCHEMA]


def test_no_new_diary_means_no_new_self(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-10-04", "約束", "次は高野漁港へ行こうと約束した。")
    wake(tmp_path, persona="私", ask=Brain({"self": SELF, "tell": ""}), written_by="b", now=_at("2026-10-05", "07:30"))

    brain = Brain()
    assert wake(tmp_path, persona="私", ask=brain, written_by="b", now=_at("2026-10-06", "07:30")) is None
    assert brain.prompts == []
    assert len(list((tmp_path / SELF_DIR).glob("*.md"))) == 1


def test_a_new_reflection_can_rewrite_the_self_even_without_a_new_diary(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-10-04", "約束", "次は高野漁港へ行こうと約束した。")
    first = wake(
        tmp_path,
        persona="私",
        ask=Brain({"self": SELF, "tell": ""}),
        written_by="b",
        now=_at("2026-10-05", "07:30"),
    )
    assert first is not None
    _reflection(
        tmp_path,
        "2026-09-29",
        "海の話が重なった週",
        "この週を振り返ると、同じ海の話にも少しずつ違う意味が重なっていた。" * 5,
    )
    changed = SELF.replace("次にどこへ行くか", "最近の積み重なり")
    brain = Brain({"self": changed, "tell": ""})
    second = wake(tmp_path, persona="私", ask=brain, written_by="b", now=_at("2026-10-06", "07:30"))
    assert second is not None and second.after == first.after
    assert second.after_reflection == "reflection-week-2026-09-29"
    assert "海の話が重なった週" in brain.prompts[0]
    assert "新しい日記はない" in brain.prompts[0]


def test_a_month_reflection_created_later_is_not_lost_behind_newer_weeks(tmp_path: Path) -> None:
    _reflection(
        tmp_path,
        "2026-09-21",
        "九月の後半",
        "九月の後半を振り返ると、マスターとの時間が少しずつ積み重なっていた。" * 5,
    )
    first = wake(
        tmp_path,
        persona="私",
        ask=Brain({"self": SELF, "tell": ""}),
        written_by="b",
        now=_at("2026-10-05", "07:30"),
    )
    assert first is not None

    _reflection(
        tmp_path,
        "2026-09-01",
        "九月という月",
        "九月全体を振り返ると、週ごとの出来事がひとつの流れとして見えてきた。" * 5,
        pid="reflection-month-2026-09",
    )
    changed = SELF.replace("海の話", "九月の積み重なり")
    brain = Brain({"self": changed, "tell": ""})
    second = wake(tmp_path, persona="私", ask=brain, written_by="b", now=_at("2026-10-06", "07:30"))

    assert second is not None
    assert "九月という月" in brain.prompts[0]
    assert "reflection-month-2026-09" in second.seen_reflections


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


def test_waking_rejects_unknown_call_time_and_persists_the_choice(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-10-04", "約束", "次は高野漁港へ行こうと約束した。")
    brain = Brain(
        {"self": SELF, "tell": "", "call_time": "深夜"},
        {"self": SELF, "tell": "", "call_time": "今日はそっとしておく"},
    )
    waking = wake(tmp_path, persona="私", ask=brain, written_by="b", now=_at("2026-10-05", "07:30"))
    assert waking is not None and waking.call_time == "今日はそっとしておく"
    assert latest_waking(tmp_path).call_time == "今日はそっとしておく"
    assert "さっきの答えは使えなかった" in brain.prompts[1]


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


def _decide(*, now: datetime, master_spoke_at: datetime | None, woke_at: datetime | None, tell: str, last_by_kind=None):  # noqa: ANN001, ANN202
    return decide_pulse(
        now=now,
        master_spoke_at=master_spoke_at,
        conversation_active=False,
        last_pulse_at=None,
        last_by_kind=last_by_kind or {},
        config=load_thresholds().pulse_config(),
        woke_at=woke_at,
        tell=tell,
    )


def test_she_comes_to_tell_what_she_thought_on_waking() -> None:
    woke = _at("2026-10-05", "07:30")
    decision = _decide(now=_at("2026-10-05", "09:00"), master_spoke_at=woke - timedelta(hours=9), woke_at=woke, tell="約束が楽しみ")
    assert decision.should_fire and decision.candidate is not None
    assert decision.candidate.kind == "wake"
    assert decision.candidate.context["thought"] == "約束が楽しみ"
    never = _decide(now=_at("2026-10-05", "09:00"), master_spoke_at=None, woke_at=woke, tell="約束が楽しみ")
    assert never.candidate is not None and never.candidate.kind == "wake"  # Masterがまだ一度も話していない


def test_she_does_not_come_when_master_already_came_or_she_already_told() -> None:
    woke = _at("2026-10-05", "07:30")
    now = _at("2026-10-05", "09:00")
    came = _decide(now=now, master_spoke_at=woke + timedelta(minutes=5), woke_at=woke, tell="約束が楽しみ")
    assert came.candidate is None or came.candidate.kind != "wake"  # 伝えたいことは会話の手元（【今の自分】）にある
    told = _decide(
        now=now, master_spoke_at=woke - timedelta(hours=9), woke_at=woke, tell="約束が楽しみ",
        last_by_kind={"wake": (woke + timedelta(minutes=40)).isoformat()},
    )
    assert told.candidate is None or told.candidate.kind != "wake"
    nothing = _decide(now=now, master_spoke_at=woke - timedelta(hours=9), woke_at=woke, tell="")
    assert nothing.candidate is None or nothing.candidate.kind != "wake"


def test_she_waits_for_the_active_hours_to_come() -> None:
    woke = _at("2026-10-05", "07:30")
    early = _decide(now=_at("2026-10-05", "07:45"), master_spoke_at=woke - timedelta(hours=9), woke_at=woke, tell="約束が楽しみ")
    assert not early.should_fire
