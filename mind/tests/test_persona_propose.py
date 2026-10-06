"""眠りのあとの人格の見直し（core/chores/persona_propose.py。設計書 §4.3）のテスト。

守るもの：材料は記憶のページの日記だけ（気分の流れは入れない）。1日1回まで。直すと決めたら関所（固定ブロック不可・
20%まで・前の文を控える）を通して書き換える。直さない・直せないときも、無言で終わらせず変更レポートを残す。
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.persona_propose import (
    ProposeMaterial,
    build_propose_prompt,
    gather_propose_material,
    propose_persona_revision,
    run_persona_growth,
    should_run_persona_propose,
)
from mind.core.idea import PERSONA_DIR
from mind.core.memory.page import Page, write_page
from mind.core.persona_assets import load_persona_assets
from mind.core.protection import ChangeLog, GenerationStore
from mind.core.state.persona_propose_state import load_persona_propose_state, save_persona_propose_state

JST = timezone(timedelta(hours=9))


def _diary(memory: Path, day: int, body: str) -> None:
    at = datetime(2026, 10, day, 23, 0, tzinfo=JST)
    page = Page(
        id=f"reflection-week-2026-10-{day:02d}",
        kind="reflection",
        start=at,
        end=at,
        source=(),
        concepts=(),
        body=body,
    )
    write_page(memory, page.with_words(title="振り返り", gist="要点", importance=5, written_by="test"))


def _setup(tmp: Path) -> tuple[Path, Path, ChangeLog, GenerationStore]:
    persona = tmp / "persona"
    shutil.copytree(PERSONA_DIR, persona)
    return tmp / "memory", persona, ChangeLog(tmp / "c.jsonl"), GenerationStore(tmp / "g.jsonl")


def _answer(**payload) -> str:  # noqa: ANN003
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def test_should_run_persona_propose_calendar_day() -> None:
    morning = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    assert should_run_persona_propose(now=morning, last_propose_at=None) is True
    assert should_run_persona_propose(now=morning.replace(hour=22), last_propose_at=morning) is False
    assert should_run_persona_propose(now=morning + timedelta(days=1), last_propose_at=morning) is True


def test_material_is_the_latest_diary_pages(tmp_path: Path) -> None:
    memory, persona, _, _ = _setup(tmp_path)
    for day in (1, 2, 3, 4):
        _diary(memory, day, f"{day}日の日記")
    material = gather_propose_material(memory, persona_dir=persona, reflection_limit=3)
    assert [d.body for d in material.reflections] == ["2日の日記", "3日の日記", "4日の日記"]
    prompt = build_propose_prompt(material)
    assert "4日の日記" in prompt and "1日の日記" not in prompt
    assert "気分" not in prompt.split("【直近の振り返り】")[1].split("あなたは")[0]


def test_no_diary_means_no_question(tmp_path: Path) -> None:
    memory, persona, change_log, generations = _setup(tmp_path)
    outcome = run_persona_growth(
        memory_dir=memory, call_fn=lambda _p: _answer(revise=True), change_log=change_log,
        generation_store=generations, persona_dir=persona,
    )
    assert outcome.asked is False and outcome.advance_cooldown is False


def test_no_revision_is_reported(tmp_path: Path) -> None:
    memory, persona, change_log, generations = _setup(tmp_path)
    _diary(memory, 3, "いつもどおりの一日")
    outcome = run_persona_growth(
        memory_dir=memory,
        call_fn=lambda _p: _answer(revise=False, block_id=None, new_content=None, reason="変化なし"),
        change_log=change_log, generation_store=generations, persona_dir=persona,
    )
    assert outcome.asked and outcome.revise is False and outcome.advance_cooldown
    assert any(r.action == "persona提案見送り" for r in change_log.read_all())


def test_revision_goes_through_the_gate_and_is_written(tmp_path: Path) -> None:
    memory, persona, change_log, generations = _setup(tmp_path)
    _diary(memory, 3, "マスターとの口調が少し柔らかくなった気がする。")
    voice = next(b for b in load_persona_assets(persona).blocks if b.id == "voice")
    new_content = "微調整。" + voice.text[max(1, len(voice.text) // 20):]
    outcome = run_persona_growth(
        memory_dir=memory,
        call_fn=lambda _p: _answer(revise=True, block_id="voice", new_content=new_content, reason="口調の持続的な柔らかさ"),
        change_log=change_log, generation_store=generations, persona_dir=persona,
    )
    assert outcome.revised and outcome.block_id == "voice"
    assert (persona / voice.file).read_text(encoding="utf-8") == new_content
    assert generations.has_persona_block("voice")


def test_revision_rejected_by_the_gate_is_reported(tmp_path: Path) -> None:
    """固定ブロックは提案の段階で弾かれ、リトライを使い切ったら不採用として残る。"""
    memory, persona, change_log, generations = _setup(tmp_path)
    _diary(memory, 3, "日記")
    outcome = run_persona_growth(
        memory_dir=memory,
        call_fn=lambda _p: _answer(revise=True, block_id="core_principles", new_content="改変", reason="?"),
        change_log=change_log, generation_store=generations, persona_dir=persona, max_retries=2,
    )
    assert outcome.revised is False and outcome.failure_reason is not None
    assert any(r.action == "persona提案不採用" for r in change_log.read_all())


def test_llm_failure_does_not_advance(tmp_path: Path) -> None:
    memory, persona, change_log, generations = _setup(tmp_path)
    _diary(memory, 3, "日記")
    outcome = run_persona_growth(
        memory_dir=memory, call_fn=lambda _p: "これはJSONではない", change_log=change_log,
        generation_store=generations, persona_dir=persona, max_retries=2,
    )
    assert outcome.asked and outcome.failure_reason is not None and outcome.advance_cooldown is False


def test_propose_persona_revision_retries_when_change_ratio_too_large() -> None:
    """改訂幅が上限を超えたら即失敗にせず、小さい差分での再提案をリトライで促す。"""
    original = "天真爛漫で無邪気。論理と直観に優れる二面性を持つ。" * 5
    material = ProposeMaterial(reflections=[], mutable_blocks={"personality": original, "voice": "", "love": ""})
    calls: list[str] = []

    def _call(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return _answer(revise=True, block_id="personality", new_content="全く別の性格描写。", reason="大幅")
        return _answer(revise=True, block_id="personality", new_content="少しだけ" + original[len(original) // 20:], reason="軽微")

    revise, block_id, _new, _reason, failure = propose_persona_revision(material, call_fn=_call, max_retries=3)
    assert failure is None and revise is True and block_id == "personality"
    assert len(calls) == 2 and "上限" in calls[1]


def test_propose_persona_revision_fails_after_retries_exhausted_on_change_ratio() -> None:
    original = "天真爛漫で無邪気。論理と直観に優れる二面性を持つ。" * 5
    material = ProposeMaterial(reflections=[], mutable_blocks={"personality": original, "voice": "", "love": ""})
    *_rest, failure = propose_persona_revision(
        material,
        call_fn=lambda _p: _answer(revise=True, block_id="personality", new_content="毎回総入れ替え。", reason="大幅"),
        max_retries=2,
    )
    assert failure is not None and "改訂幅" in failure


def test_persona_propose_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "persona_propose_state.json"
    assert load_persona_propose_state(path) is None
    stamp = datetime(2026, 7, 19, 15, 0, tzinfo=timezone.utc)
    save_persona_propose_state(path, last_propose_at=stamp)
    assert load_persona_propose_state(path) == stamp


def test_load_persona_propose_state_corrupt_file_returns_none(tmp_path: Path) -> None:
    """状態ファイル破損で起動を止めない（未記録扱いで続行）。"""
    broken = tmp_path / "persona_propose_state.json"
    broken.write_text("{{{壊れたJSON", encoding="utf-8")
    assert load_persona_propose_state(broken) is None


def test_no_new_reflection_since_the_last_look_means_no_question(tmp_path: Path) -> None:
    """前回の見直しのあとに新しい振り返りがなければ聞かない。"""
    memory, persona, change_log, generations = _setup(tmp_path)
    _diary(memory, 3, "前の日記")
    first = run_persona_growth(
        memory_dir=memory,
        call_fn=lambda _p: _answer(revise=False, block_id=None, new_content=None, reason="変化なし"),
        change_log=change_log,
        generation_store=generations,
        persona_dir=persona,
        now=datetime(2026, 10, 4, 9, 0, tzinfo=JST),
        last_propose_at=None,
    )
    assert first.advance_cooldown
    asked = []
    outcome = run_persona_growth(
        memory_dir=memory, call_fn=lambda p: asked.append(p) or "", change_log=change_log,
        generation_store=generations, persona_dir=persona,
        now=datetime(2026, 10, 5, 9, 0, tzinfo=JST),
        last_propose_at=datetime(2026, 10, 4, 8, 0, tzinfo=JST),
    )
    assert outcome.asked is False and asked == []


def test_new_reflection_after_the_last_look_is_used(tmp_path: Path) -> None:
    memory, persona, change_log, generations = _setup(tmp_path)
    _diary(memory, 3, "前の振り返り")
    first = run_persona_growth(
        memory_dir=memory,
        call_fn=lambda _p: _answer(revise=False, block_id=None, new_content=None, reason="変化なし"),
        change_log=change_log,
        generation_store=generations,
        persona_dir=persona,
        now=datetime(2026, 10, 4, 9, 0, tzinfo=JST),
    )
    assert first.advance_cooldown
    _diary(memory, 10, "新しい振り返り")
    asked = []
    second = run_persona_growth(
        memory_dir=memory,
        call_fn=lambda p: asked.append(p) or _answer(revise=False, block_id=None, new_content=None, reason="変化なし"),
        change_log=change_log,
        generation_store=generations,
        persona_dir=persona,
        now=datetime(2026, 10, 11, 9, 0, tzinfo=JST),
        last_propose_at=datetime(2026, 10, 4, 9, 0, tzinfo=JST),
    )
    assert second.asked and asked
