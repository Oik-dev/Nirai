from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from mind.core.idea import Idea
from mind.core.lifelog import RecallLog
from mind.core.memory.growth import (
    CONCEPT_SCHEMA,
    LATER_SCHEMA,
    grow_concepts,
    reconsolidate,
    replay,
)
from mind.core.memory.index import load_aliases
from mind.core.memory.memory import Memory
from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.recall import Cue
from mind.core.memory.relation import Relation

JST = timezone(timedelta(hours=9))


def _at(day: str, hm: str = "20:00") -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+09:00")


def _idea(tmp_path: Path) -> Idea:
    root = tmp_path / "idea"
    root.mkdir()
    (root / "identity.toml").write_text('name = "Test"\n', encoding="utf-8")
    return Idea.open(root)


def _page(
    pid: str,
    day: str,
    concept: str,
    *,
    hm: str = "20:00",
    importance: int = 5,
    arousal: float = 0.4,
) -> Page:
    return Page(
        id=pid,
        kind="episode",
        start=_at(day, hm),
        end=_at(day, "01:10" if hm == "01:00" else "20:10"),
        source=(),
        concepts=(concept,),
        structured_by="test",
        affect={"valence": 0.1, "arousal": arousal},
    ).with_words(
        title=f"{concept}の話",
        gist=f"{concept}について話した。",
        importance=importance,
        written_by="test-brain",
        body=f"わたしは{concept}のことを覚えている。",
    )


def test_concepts_are_grown_by_append_and_aliases_reach_recall(tmp_path: Path) -> None:
    idea = _idea(tmp_path)
    old = _page("old", "2026-10-01", "高野漁港")
    new = _page("new", "2026-10-02", "約束の海")
    write_page(idea.memory, old)
    write_page(idea.memory, new)
    calls = []

    def ask(_prompt: str, schema: dict, attempt: int) -> dict:
        assert schema is CONCEPT_SCHEMA
        calls.append(attempt)
        return {"pairs": [{"a": "約束の海", "b": "高野漁港"}]}

    assert grow_concepts(idea.memory, [old, new], persona="人格", ask=ask) == 2
    assert calls == [0]
    text = (idea.memory / "concepts.toml").read_text(encoding="utf-8")
    assert text.count("[[growth]]") == 2 and 'on = "2026-10-01"' in text and 'on = "2026-10-02"' in text
    aliases = load_aliases(idea.memory)
    assert aliases["約束の海"] == aliases["高野漁港"] == "高野漁港"

    memory = Memory(idea, embed=lambda text: [1.0 if "高野漁港" in text or "約束の海" in text else 0.0, 0.2], embed_model="fake")
    memory.rebuild_index(progress=lambda _m: None)
    remembered = memory.recall(Cue("約束の海、覚えてる？", (), _at("2026-10-03")))
    assert remembered and remembered[0].page_id in {"old", "new"}
    # 同じ日をもう一度見ても、脳へ聞かず追記もしない。
    assert grow_concepts(idea.memory, load_pages(idea.memory), persona="人格", ask=ask) == 0
    assert calls == [0]


def test_concept_growth_uses_the_serina_day_boundary(tmp_path: Path) -> None:
    idea = _idea(tmp_path)
    before_dawn = _page("before-dawn", "2026-10-06", "夜明け前", hm="01:00")
    write_page(idea.memory, before_dawn)

    calls = []

    def ask(_prompt: str, schema: dict, attempt: int) -> dict:
        assert schema is CONCEPT_SCHEMA
        calls.append(attempt)
        return {"pairs": []}

    assert grow_concepts(idea.memory, load_pages(idea.memory), persona="人格", ask=ask) == 1
    first = (idea.memory / "concepts.toml").read_text(encoding="utf-8")
    assert 'on = "2026-10-05"' in first and 'on = "2026-10-06"' not in first

    evening = _page("evening", "2026-10-06", "約束の海")
    write_page(idea.memory, evening)
    assert grow_concepts(idea.memory, load_pages(idea.memory), persona="人格", ask=ask) == 1
    second = (idea.memory / "concepts.toml").read_text(encoding="utf-8")
    assert 'on = "2026-10-06"' in second
    assert calls == [0]


def test_concept_growth_rejects_names_that_were_not_shown(tmp_path: Path) -> None:
    idea = _idea(tmp_path)
    pages = [_page("a", "2026-10-01", "海"), _page("b", "2026-10-02", "浜辺")]
    attempts = []

    def ask(_prompt: str, schema: dict, attempt: int) -> dict:
        assert schema is CONCEPT_SCHEMA
        attempts.append(attempt)
        if attempt == 0:
            return {"pairs": [{"a": "浜辺", "b": "知らない名前"}]}
        return {"pairs": []}

    grow_concepts(idea.memory, pages, persona="人格", ask=ask)
    assert attempts == [0, 1]


def test_concept_growth_never_jumps_over_an_unwritten_day(tmp_path: Path) -> None:
    idea = _idea(tmp_path)
    first = _page("first", "2026-10-01", "海")
    blocked = Page(id="blocked", kind="episode", start=_at("2026-10-02"), end=_at("2026-10-02", "20:10"), source=(), concepts=("浜",))
    later = _page("later", "2026-10-03", "岬")
    calls = []

    def ask(_prompt: str, schema: dict, attempt: int) -> dict:
        calls.append((schema, attempt))
        return {"pairs": []}

    assert grow_concepts(idea.memory, [first, blocked, later], persona="人格", ask=ask) == 1
    text = (idea.memory / "concepts.toml").read_text(encoding="utf-8")
    assert 'on = "2026-10-01"' in text and 'on = "2026-10-03"' not in text
    assert calls == []


def test_reconsolidation_only_appends_later_and_vivid_recall_shows_it(tmp_path: Path) -> None:
    idea = _idea(tmp_path)
    page = _page("old", "2026-08-01", "海")
    write_page(idea.memory, page)
    log = RecallLog(idea.recall)
    today = date(2026, 10, 6)
    log.append(ts=_at("2026-10-05", "14:00"), page=page.id, activation=2.0, vivid=True, intent=False)
    before_body = page.body

    def ask(_prompt: str, schema: dict, _attempt: int) -> dict:
        assert schema is LATER_SCHEMA
        return {"later": "今は、あの日の約束の意味が前より少し分かる。"}

    changed = reconsolidate(
        idea.memory,
        log,
        day=today,
        persona="人格",
        ask=ask,
        waking=None,
        relation=Relation(person="マスター"),
    )
    assert changed == 1
    saved = load_pages(idea.memory)[0]
    assert saved.body == before_body
    assert saved.later == ({"on": "2026-10-06", "text": "今は、あの日の約束の意味が前より少し分かる。"},)
    # 同じページは同じ日に二度書かない。
    assert reconsolidate(
        idea.memory, log, day=today, persona="人格", ask=ask, waking=None, relation=Relation(person="マスター")
    ) == 0

    memory = Memory(idea, embed=lambda _text: [1.0, 0.2], embed_model="fake")
    memory.rebuild_index(progress=lambda _m: None)
    remembered = memory.recall(Cue("海のこと、覚えてる？", (), _at("2026-10-07")))
    assert remembered and "今思うと（2026-10-06）" in remembered[0].text


def test_reconsolidation_is_limited_to_two_pages_per_sleep_day(tmp_path: Path) -> None:
    idea = _idea(tmp_path)
    log = RecallLog(idea.recall)
    today = date(2026, 10, 6)
    for n in range(5):
        page = _page(f"old-{n}", "2026-08-01", f"海{n}")
        write_page(idea.memory, page)
        log.append(
            ts=_at("2026-10-05", f"14:0{n}"),
            page=page.id,
            activation=float(5 - n),
            vivid=True,
            intent=False,
        )

    def ask(_prompt: str, schema: dict, _attempt: int) -> dict:
        assert schema is LATER_SCHEMA
        return {"later": "今は少し違って見える。"}

    args = dict(
        memory_dir=idea.memory,
        recall_log=log,
        day=today,
        persona="人格",
        ask=ask,
        waking=None,
        relation=Relation(person="マスター"),
    )
    assert reconsolidate(**args) == 2
    assert reconsolidate(**args) == 0
    assert sum(
        1 for page in load_pages(idea.memory) for item in page.later if item.get("on") == "2026-10-06"
    ) == 2


def test_replay_is_a_recall_trace_and_does_not_repeat_that_day(tmp_path: Path) -> None:
    idea = _idea(tmp_path)
    pages = [
        _page("high", "2026-10-04", "海", importance=9, arousal=0.9),
        _page("mid", "2026-10-03", "仕事", importance=6, arousal=0.5),
        _page("low", "2026-10-02", "昼寝", importance=2, arousal=0.1),
    ]
    for page in pages:
        write_page(idea.memory, page)
    log = RecallLog(idea.recall)
    today = date(2026, 10, 6)
    log.append(ts=_at("2026-10-05", "12:00"), page="mid", activation=1.0, vivid=False, intent=False)

    assert replay(idea.memory, log, day=today) == 2
    rows = log.entries_on(today)
    replayed = [row["page"] for row in rows if row.get("intent") == "replay"]
    assert replayed == ["high", "low"] and "mid" not in replayed
    assert replay(idea.memory, log, day=today) == 0
    times = log.times()
    assert len(times["high"]) == 1 and len(times["low"]) == 1 and len(times["mid"]) == 1


def test_replay_resumes_if_only_one_trace_was_written_before_interruption(tmp_path: Path) -> None:
    idea = _idea(tmp_path)
    for page in [
        _page("high", "2026-10-04", "海", importance=9, arousal=0.9),
        _page("mid", "2026-10-03", "仕事", importance=6, arousal=0.5),
    ]:
        write_page(idea.memory, page)
    log = RecallLog(idea.recall)
    today = date(2026, 10, 6)
    log.append(ts=_at("2026-10-06", "06:55"), page="high", activation=0.0, vivid=False, intent="replay")

    assert replay(idea.memory, log, day=today) == 1
    replayed = [row["page"] for row in log.entries_on(today) if row.get("intent") == "replay"]
    assert replayed == ["high", "mid"]
