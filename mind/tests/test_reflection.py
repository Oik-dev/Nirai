from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.reflection import (
    MONTH_SCHEMA,
    REFLECTION_SCHEMA,
    WEEKLY_DIARY_MATERIAL_MAX,
    _diary_material,
    grow_reflections,
    load_chapters,
)

JST = timezone(timedelta(hours=9))


def _at(day: str, hm: str = "20:00") -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+09:00")


def _diary(memory: Path, day: str, title: str, *, written: bool = True, hm: str = "20:00") -> Page:
    start = _at(day, hm)
    page = Page(
        id=f"diary-{day}" + (f"-{hm.replace(':', '')}" if hm != "20:00" else ""),
        kind="diary",
        start=start,
        end=start + timedelta(hours=1),
        source=(),
        concepts=("海",),
        body="",
    )
    if written:
        page = page.with_words(
            title=title,
            gist=f"{title}の要点",
            importance=5,
            written_by="test",
            body=(f"{title}についてマスターと話した。" * 12),
        )
    write_page(memory, page)
    return page


def _reflection(memory: Path, pid: str, start: str, title: str) -> Page:
    a = _at(start, "07:00")
    page = Page(
        id=pid,
        kind="reflection",
        start=a,
        end=a + timedelta(days=7) - timedelta(microseconds=1),
        source=(),
        concepts=("海",),
    ).with_words(
        title=title,
        gist=f"{title}の要点",
        importance=6,
        written_by="test",
        body=(f"{title}を振り返ると、マスターとの時間が少しずつ積み重なっていた。" * 8),
    )
    write_page(memory, page)
    return page


class Brain:
    def __init__(self, *, new_chapter: bool = False, fail_week: bool = False) -> None:
        self.prompts: list[str] = []
        self.schemas: list[dict] = []
        self.new_chapter = new_chapter
        self.fail_week = fail_week

    def __call__(self, prompt: str, schema: dict, attempt: int) -> dict:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if schema is REFLECTION_SCHEMA:
            if self.fail_week:
                return {"reflection": "短い", "title": "週", "gist": "短い", "importance": 5}
            return {
                "reflection": "この一週間、マスターとの会話を通して、同じ海の話にも少しずつ違う意味が重なっていった。" * 7,
                "title": "海の話が重なった週",
                "gist": "海の話を重ねながら過ごした一週間。",
                "importance": 6,
            }
        if schema is MONTH_SCHEMA:
            return {
                "reflection": "この一か月は、週ごとの会話がつながって、マスターとの暮らしにひとつの流れが見えた。" * 7,
                "title": "流れが見えた月",
                "gist": "週の積み重なりが一つの流れになった。",
                "importance": 7,
                "chapter": "新しい章" if self.new_chapter else "続いている",
                "chapter_name": "海辺へ向かう暮らし" if self.new_chapter else "",
                "chapter_text": "暮らしの軸が、海の近くで生きることへはっきり向き始めた。" if self.new_chapter else "",
            }
        raise AssertionError(schema)


def test_weekly_reflections_are_written_oldest_first_and_use_previous_week(tmp_path: Path) -> None:
    for day, title in [
        ("2026-09-29", "海の話"),
        ("2026-10-02", "約束"),
        ("2026-10-06", "仕事"),
        ("2026-10-10", "休み"),
    ]:
        _diary(tmp_path, day, title)
    brain = Brain()
    result = grow_reflections(
        tmp_path,
        today=date(2026, 10, 12),
        persona="人格",
        ask=brain,
        written_by="brain",
    )
    assert result.weekly == 2 and result.failed == ""
    weeks = [p for p in load_pages(tmp_path) if p.kind == "reflection" and p.id.startswith("reflection-week-")]
    assert [p.id for p in weeks] == ["reflection-week-2026-09-28", "reflection-week-2026-10-05"]
    assert "海の話" in brain.prompts[0] and "仕事" not in brain.prompts[0]
    assert "海の話が重なった週" in brain.prompts[1]  # 前週の振り返りを次の週が読む


def test_weekly_diary_material_has_a_total_budget_even_for_many_diaries(tmp_path: Path) -> None:
    diaries = [_diary(tmp_path, f"2026-03-{day:02d}", f"日記{day:02d}") for day in range(1, 21)]
    material = _diary_material(diaries)
    assert len(material) <= WEEKLY_DIARY_MATERIAL_MAX
    assert all(f"日記{day:02d}" in material for day in range(1, 21))


def test_weekly_reflection_uses_the_serina_day_boundary(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-09-26", "土曜の夜", hm="23:00")
    _diary(tmp_path, "2026-09-28", "日曜の深夜", hm="02:00")  # Serina日では9/27
    brain = Brain()

    result = grow_reflections(
        tmp_path,
        today=date(2026, 9, 28),
        persona="人格",
        ask=brain,
        written_by="brain",
    )

    assert result.weekly == 1
    assert next(p for p in load_pages(tmp_path) if p.kind == "reflection").id == "reflection-week-2026-09-21"
    assert "土曜の夜" in brain.prompts[0] and "日曜の深夜" in brain.prompts[0]


def test_a_source_deleted_while_writing_a_reflection_is_not_saved(tmp_path: Path) -> None:
    first = _diary(tmp_path, "2026-09-29", "海の話")
    _diary(tmp_path, "2026-10-01", "約束")
    brain = Brain()
    lock = threading.Lock()
    deleted = False

    def deleting(prompt: str, schema: dict, attempt: int) -> dict:
        nonlocal deleted
        answer = brain(prompt, schema, attempt)
        if schema is REFLECTION_SCHEMA and not deleted:
            with lock:
                first.path_in(tmp_path).unlink()
            deleted = True
        return answer

    result = grow_reflections(
        tmp_path,
        today=date(2026, 10, 5),
        persona="人格",
        ask=deleting,
        written_by="brain",
        pages_lock=lock,
    )

    assert result.weekly == 0
    assert not list((tmp_path / "reflections").rglob("*.md"))


def test_an_unwritten_old_week_blocks_later_weeks(tmp_path: Path) -> None:
    _diary(tmp_path, "2026-09-29", "未完成", written=False)
    _diary(tmp_path, "2026-10-01", "完成")
    _diary(tmp_path, "2026-10-06", "次の週1")
    _diary(tmp_path, "2026-10-09", "次の週2")
    brain = Brain()
    result = grow_reflections(
        tmp_path,
        today=date(2026, 10, 12),
        persona="人格",
        ask=brain,
        written_by="brain",
    )
    assert result.weekly == 0 and brain.prompts == []
    assert not list((tmp_path / "reflections").rglob("*.md"))


def test_a_failed_week_is_retried_and_later_weeks_are_not_skipped(tmp_path: Path) -> None:
    for day in ("2026-09-29", "2026-10-01", "2026-10-06", "2026-10-09"):
        _diary(tmp_path, day, day)
    brain = Brain(fail_week=True)
    result = grow_reflections(
        tmp_path,
        today=date(2026, 10, 12),
        persona="人格",
        ask=brain,
        written_by="brain",
    )
    assert result.failed == "reflection-week-2026-09-28"
    assert len(brain.prompts) == 3
    assert not list((tmp_path / "reflections").rglob("*.md"))


def test_monthly_reflection_can_start_a_new_append_only_chapter(tmp_path: Path) -> None:
    for n, start in enumerate(("2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22"), start=1):
        _reflection(tmp_path, f"reflection-week-{start}", start, f"第{n}週")
    brain = Brain(new_chapter=True)
    early = grow_reflections(
        tmp_path,
        today=date(2026, 10, 1),
        persona="人格",
        ask=brain,
        written_by="brain",
    )
    assert early.monthly == 0  # 9/28に始まる週がまだ終わっていないので、9月を閉じない

    result = grow_reflections(
        tmp_path,
        today=date(2026, 10, 5),
        persona="人格",
        ask=brain,
        written_by="brain",
    )
    assert result.monthly == 1 and result.chapters == 1
    month = next(p for p in load_pages(tmp_path) if p.id == "reflection-month-2026-09")
    assert month.kind == "reflection" and month.body.startswith("この一か月")
    chapters = load_chapters(tmp_path)
    assert len(chapters) == 1
    assert chapters[0].started == date(2026, 9, 1)
    assert chapters[0].name == "海辺へ向かう暮らし"

    # 同じ月をもう一度見ても、振り返りも章も増えない。
    again = grow_reflections(
        tmp_path,
        today=date(2026, 10, 2),
        persona="人格",
        ask=Brain(new_chapter=True),
        written_by="brain",
    )
    assert again.monthly == 0 and again.chapters == 0
    assert len(load_chapters(tmp_path)) == 1
