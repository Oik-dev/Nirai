"""眠りの人格見直し: 未読の振り返り、1回1文、脳の呼び出しと追記の保護。"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.persona_propose import (
    build_propose_prompt,
    gather_propose_material,
    propose_persona_revision,
    run_persona_growth,
    should_run_persona_propose,
)
from mind.core.chores.persona_revise import persona_sentences
from mind.brains.ollama.serve import Yielded
from mind.core.idea import PERSONA_DIR
from mind.core.memory.page import Page, write_page
from mind.core.persona_assets import load_persona_assets
from mind.core.protection import ChangeLog, GenerationStore
from mind.core.state.persona_propose_state import load_persona_propose_state, save_persona_propose_state

JST = timezone(timedelta(hours=9))
BODY = "長く話したあとの気持ちを振り返ると、自分の言葉が以前より穏やかになってきた。"
REVIEW_OK = json.dumps({"conflicts": [], "duplicates": [], "memory_like": False,
                        "affects_behavior": True, "single_trait": True})


def _diary(memory: Path, day: int, body: str = BODY, *, month: int = 10, page_id: str | None = None) -> Page:
    at = datetime(2026, month, day, 23, 0, tzinfo=JST)
    page = Page(id=page_id or f"reflection-week-2026-{month:02d}-{day:02d}", kind="reflection",
                start=at, end=at, source=(), concepts=(), body=body)
    write_page(memory, page.with_words(title="振り返り", gist="要点", importance=5, written_by="test"))
    return page


def _setup(tmp: Path):
    persona = tmp / "persona"
    shutil.copytree(PERSONA_DIR, persona)
    return tmp / "memory", persona, ChangeLog(tmp / "c.jsonl"), GenerationStore(tmp / "g.jsonl")


def _answer(**payload):
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def _target(persona: Path):
    return next(s for s in persona_sentences(load_persona_assets(persona).blocks)
                if s.block_id == "voice" and "発話" in s.text)


def _change(persona: Path, page: Page, *, quote: str = BODY):
    target = _target(persona)
    return {"revise": True, "target": target.number, "block_id": "voice",
            "after": target.text.replace("発話", "語り"),
            "experiences": [{"page": page.id, "quote": quote}], "reason": "継続した変化"}


def test_should_run_persona_propose_calendar_day() -> None:
    morning = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    assert should_run_persona_propose(now=morning, last_propose_at=None)
    assert not should_run_persona_propose(now=morning.replace(hour=22), last_propose_at=morning)
    assert should_run_persona_propose(now=morning + timedelta(days=1), last_propose_at=morning)


def test_material_uses_distinct_weeks_and_prompt_shows_full_persona(tmp_path: Path) -> None:
    memory, persona, _, _ = _setup(tmp_path)
    for day in (1, 2, 3, 4, 8, 15):
        _diary(memory, day, f"{day}日を振り返った記録")
    material = gather_propose_material(memory, persona_dir=persona)
    assert [p.start.day for p in material.reflections] == [4, 8, 15]
    prompt = build_propose_prompt(material)
    assert "id=reflection-week" in prompt
    assert "core_principles（違う週の経験が3週必要）" in prompt
    assert "identity（変更不可）" in prompt
    assert "記憶と関係の書き留め" in prompt
    assert "1日の日記" not in prompt


def test_no_reflection_means_no_brain_call(tmp_path: Path) -> None:
    memory, persona, changes, generations = _setup(tmp_path)
    assert not run_persona_growth(memory_dir=memory, call_fn=lambda _p: "bad", change_log=changes,
                                  generation_store=generations, persona_dir=persona).asked


def test_no_revision_reported_and_not_asked_again(tmp_path: Path) -> None:
    memory, persona, changes, generations = _setup(tmp_path)
    _diary(memory, 3)
    first = run_persona_growth(memory_dir=memory, call_fn=lambda _p: _answer(revise=False, reason="変化なし"),
                               change_log=changes, generation_store=generations, persona_dir=persona,
                               now=datetime(2026, 10, 4, 9, tzinfo=JST))
    assert first.advance_cooldown and not first.revised
    calls = []
    second = run_persona_growth(memory_dir=memory, call_fn=lambda p: calls.append(p) or "", change_log=changes,
                                generation_store=generations, persona_dir=persona,
                                now=datetime(2026, 10, 5, 9, tzinfo=JST),
                                last_propose_at=datetime(2026, 10, 4, 9, tzinfo=JST))
    assert not second.asked and not calls


def test_valid_revision_uses_one_proposal_and_one_thinking_review(tmp_path: Path) -> None:
    memory, persona, changes, generations = _setup(tmp_path)
    page = _diary(memory, 3)
    brain_calls, review_calls = [], []

    def proposal(prompt):
        brain_calls.append(prompt)
        return _answer(**_change(persona, page))

    def reviewer(prompt):
        review_calls.append(prompt)
        return REVIEW_OK

    result = run_persona_growth(memory_dir=memory, call_fn=proposal, review_fn=reviewer,
                                change_log=changes, generation_store=generations, persona_dir=persona)
    assert result.revised and len(brain_calls) == len(review_calls) == 1
    assert generations.has_persona_block("voice")
    assert "【今回の新しい文】" in review_calls[0]


def test_interrupted_persona_review_does_not_consume_reflection(tmp_path: Path) -> None:
    """照合を会話に譲っても、見送り印・当日済印を残さず同じ振り返りを再試行する。"""
    memory, persona, changes, generations = _setup(tmp_path)
    page = _diary(memory, 3)
    proposal_calls = []

    def proposal(prompt):
        proposal_calls.append(prompt)
        return _answer(**_change(persona, page))

    def interrupted(_prompt):
        raise Yielded()

    with pytest.raises(Yielded):
        run_persona_growth(memory_dir=memory, call_fn=proposal, review_fn=interrupted,
                           change_log=changes, generation_store=generations, persona_dir=persona,
                           now=datetime(2026, 10, 4, 9, tzinfo=JST))
    assert changes.read_all() == []
    assert not generations.has_persona_block("voice")

    retry = run_persona_growth(memory_dir=memory, call_fn=proposal,
                               review_fn=lambda _prompt: REVIEW_OK,
                               change_log=changes, generation_store=generations, persona_dir=persona,
                               now=datetime(2026, 10, 5, 9, tzinfo=JST))
    assert retry.asked and retry.revised and len(proposal_calls) == 2
    assert generations.has_persona_block("voice")
    assert not any(r.action == "persona提案見送り" for r in changes.read_all())


def test_interrupted_persona_proposal_is_not_retried_as_bad_json(tmp_path: Path) -> None:
    memory, persona, changes, generations = _setup(tmp_path)
    _diary(memory, 3)
    calls = []

    def interrupted(prompt):
        calls.append(prompt)
        raise Yielded()

    with pytest.raises(Yielded):
        run_persona_growth(memory_dir=memory, call_fn=interrupted,
                           change_log=changes, generation_store=generations,
                           persona_dir=persona, max_retries=3)
    assert len(calls) == 1
    assert changes.read_all() == []
    retry = run_persona_growth(memory_dir=memory,
                               call_fn=lambda _prompt: _answer(revise=False, reason="改訂なし"),
                               change_log=changes, generation_store=generations, persona_dir=persona)
    assert retry.asked and not retry.revised


def test_invalid_experience_is_logged_and_next_day_does_not_repeat(tmp_path: Path) -> None:
    memory, persona, changes, generations = _setup(tmp_path)
    page = _diary(memory, 3)
    invalid = _change(persona, page, quote="引用が短い")
    first = run_persona_growth(memory_dir=memory, call_fn=lambda _p: _answer(**invalid),
                               review_fn=lambda _p: (_ for _ in ()).throw(AssertionError("確認してはいけない")),
                               change_log=changes, generation_store=generations, persona_dir=persona,
                               now=datetime(2026, 10, 4, 9, tzinfo=JST))
    assert first.advance_cooldown and not first.revised
    assert [r.action for r in changes.read_all()] == ["persona変更不採用", "persona提案見送り"]
    calls = []
    second = run_persona_growth(memory_dir=memory, call_fn=lambda p: calls.append(p) or "", change_log=changes,
                                generation_store=generations, persona_dir=persona,
                                now=datetime(2026, 10, 5, 9, tzinfo=JST),
                                last_propose_at=datetime(2026, 10, 4, 9, tzinfo=JST))
    assert not second.asked and calls == []


def test_bad_json_retries_only_the_proposal_and_does_not_advance(tmp_path: Path) -> None:
    memory, persona, changes, generations = _setup(tmp_path)
    _diary(memory, 3)
    prompts = []
    result = run_persona_growth(memory_dir=memory, call_fn=lambda p: prompts.append(p) or "bad JSON",
                                change_log=changes, generation_store=generations, persona_dir=persona,
                                max_retries=2)
    assert result.asked and not result.advance_cooldown and len(prompts) == 2
    assert "書式" in prompts[1]


def test_later_created_old_month_reflection_is_included(tmp_path: Path) -> None:
    memory, persona, changes, generations = _setup(tmp_path)
    for day in (1, 8, 15, 22):
        _diary(memory, day)
    for day in range(4):
        assert run_persona_growth(memory_dir=memory, call_fn=lambda _p: _answer(revise=False),
                                  change_log=changes, generation_store=generations, persona_dir=persona,
                                  now=datetime(2026, 10, 2 + day, 9, tzinfo=JST)).asked
    old = _diary(memory, 1, "先月にあった変化を振り返った古い記録。", month=9, page_id="reflection-month-2026-09")
    material = gather_propose_material(memory, persona_dir=persona, required_reflection_id=old.id)
    assert any(p.id == old.id for p in material.reflections)
    assert "先月にあった変化" in build_propose_prompt(material)


def test_propose_state_roundtrip_and_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "persona_propose_state.json"
    assert load_persona_propose_state(path) is None
    stamp = datetime(2026, 7, 19, 15, 0, tzinfo=timezone.utc)
    save_persona_propose_state(path, last_propose_at=stamp)
    assert load_persona_propose_state(path) == stamp
    path.write_text("{{broken", encoding="utf-8")
    assert load_persona_propose_state(path) is None
