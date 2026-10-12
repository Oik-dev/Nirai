"""人格1文変更の保護契約。すべて使い捨てイデアで検証する。"""

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

from mind.core.chores.persona_revise import numbered_persona, persona_sentences, revise_persona_block
from mind.core.idea import PERSONA_DIR
from mind.core.memory.page import Page, write_page
from mind.core.persona_assets import load_persona_assets
from mind.core.protection import ChangeLog, GenerationStore

JST = timezone(timedelta(hours=9))
BODY = "一緒に海辺で会話した日々を振り返ると、以前より自然な言葉が増えてきた。"
APPROVED = {"conflicts": [], "duplicates": [], "memory_like": False, "affects_behavior": True, "single_trait": True}


def setup(tmp: Path):
    persona = tmp / "persona"
    shutil.copytree(PERSONA_DIR, persona)
    return tmp / "memory", persona, ChangeLog(tmp / "changes.jsonl"), GenerationStore(tmp / "generations.jsonl")


def reflection(memory: Path, day: int, *, body: str = BODY):
    at = datetime(2026, 10, day, 10, 0, tzinfo=JST)
    page = Page(id=f"reflection-week-2026-10-{day}", kind="reflection", start=at, end=at,
                source=(), concepts=(), body="").with_words(title="振り返り", gist="海辺", importance=5,
                                                       written_by="test", body=body)
    path = write_page(memory, page)
    return page, path


def sentence(persona: Path, block_id: str, contains: str):
    return next(s for s in persona_sentences(load_persona_assets(persona).blocks)
                if s.block_id == block_id and contains in s.text)


def proposal(target, after: str, pages: list[Page], quote: str = BODY):
    return {"target": target, "after": after,
            "experiences": [{"page": p.id, "quote": quote} for p in pages]}


def apply(memory, persona, log, generations, change, pages, *, answer=APPROVED, window=8192, reviewer=None):
    return revise_persona_block(
        change, materials=pages, memory_dir=memory, review_fn=reviewer or (lambda _p: json.dumps(answer)),
        context_window=window, reason="経験からの変化", change_log=log,
        generation_store=generations, persona_dir=persona,
    )


def test_full_persona_is_numbered_and_includes_unchangeable_blocks(tmp_path: Path) -> None:
    _, persona, _, _ = setup(tmp_path)
    blocks = load_persona_assets(persona).blocks
    prompt = numbered_persona(blocks)
    for block in blocks:
        assert f"### {block.id}" in prompt
    assert "### identity（変更不可）" in prompt
    assert "### core_principles（違う週の経験が3週必要）" in prompt
    numbers = [s.number for s in persona_sentences(blocks)]
    assert numbers == list(range(1, len(numbers) + 1))


def test_review_brain_is_allowed_to_think_without_persona(monkeypatch) -> None:
    from mind.brains.ollama.adapter import OllamaAdapter

    brain = OllamaAdapter()
    received = []

    def fake_call(prompt: str, *, think: bool = False, on_token=None) -> str:
        received.append((prompt, think, on_token))
        return "{}"

    monkeypatch.setattr(brain, "_default_chat_call", fake_call)
    assert brain.raw_thinking_call("人格なしの照合") == "{}"
    assert received == [("人格なしの照合", True, None)]


def test_identity_boundary_and_undeclared_block_never_change(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1)
    for block_id in ("identity", "boundary"):
        current = sentence(persona, block_id, "・" if block_id == "identity" else "禁止")
        assert not apply(memory, persona, log, generations, proposal(current.number, "違う性質。", [page]), [page])
    assert len([r for r in log.read_all() if r.action == "persona変更不採用"]) == 2
    assert not generations.has_persona_block("identity")
    assert not generations.has_persona_block("boundary")


def test_core_principles_needs_three_different_weeks(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    pages = [reflection(memory, day)[0] for day in (1, 2, 8, 15)]
    current = sentence(persona, "core_principles", "行動を選択")
    after = current.text.replace("選択", "決定")
    assert not apply(memory, persona, log, generations, proposal(current.number, after, pages[:2]), pages[:2])
    assert "違う週" in log.read_all()[-1].reason
    assert apply(memory, persona, log, generations, proposal(current.number, after, pages[::2] + pages[-1:]), pages)
    assert generations.has_persona_block("core_principles")


def test_missing_material_deleted_page_invalid_quote_are_rejected(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, path = reflection(memory, 1)
    target = sentence(persona, "voice", "発話")
    changed = target.text.replace("発話", "語り")
    no_material = proposal(target.number, changed, [page])
    assert not apply(memory, persona, log, generations, no_material, [])
    assert not apply(memory, persona, log, generations, proposal(target.number, changed, [page], "本文に存在しない長い引用"), [page])
    assert not apply(memory, persona, log, generations, proposal(target.number, changed, [page], "短い"), [page])
    path.unlink()
    assert not apply(memory, persona, log, generations, no_material, [page])
    assert len(log.read_all()) == 4 and not generations.has_persona_block("voice")


def test_quote_width_and_whitespace_normalize(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1, body="振り返れば　半角ABCと全角１２３が少しずつ馴染んできた。")
    current = sentence(persona, "voice", "発話")
    quote = "振り返れば 半角ＡＢＣと全角123が少しずつ馴染んできた。"
    assert apply(memory, persona, log, generations, proposal(current.number, current.text.replace("発話", "語り"), [page], quote), [page])


@pytest.mark.parametrize("review", [
    {**APPROVED, "conflicts": [2]}, {**APPROVED, "duplicates": [3]},
    {**APPROVED, "memory_like": True}, {**APPROVED, "affects_behavior": False},
    {**APPROVED, "single_trait": False}, {"broken": True},
])
def test_contradiction_redundancy_memory_like_or_broken_review_never_applies(tmp_path: Path, review: dict) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1)
    current = sentence(persona, "voice", "発話")
    path = persona / next(b.file for b in load_persona_assets(persona).blocks if b.id == "voice")
    before = path.read_bytes()
    calls = []

    def reviewer(prompt):
        calls.append(prompt)
        return json.dumps(review)

    assert not apply(memory, persona, log, generations,
                     proposal(current.number, current.text.replace("発話", "語り"), [page]), [page], reviewer=reviewer)
    assert len(calls) == 1
    assert "【今回の新しい文】" in calls[0]
    assert current.text not in calls[0]
    assert path.read_bytes() == before
    assert "照らし合わせ" in log.read_all()[-1].reason
    assert log.read_all()[-1].changes[0]["review"] == review
    assert not generations.has_persona_block("voice")


def test_budget_rejects_growth_and_reviewer_is_not_called(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1)
    calls = []
    change = {**proposal(None, "自分の好みにとても強い傾向が生まれた。", [page]), "block_id": "voice"}
    assert not apply(memory, persona, log, generations, change, [page], window=100,
                     reviewer=lambda p: calls.append(p) or json.dumps(APPROVED))
    assert calls == [] and "天井" in log.read_all()[-1].reason


def test_invalid_target_multi_sentence_or_missing_experience_rejects(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1)
    current = sentence(persona, "voice", "発話")
    for change in (proposal(99999, "新しい文。", [page]),
                   proposal(current.number, "二文ある。もう一文ある。", [page]),
                   proposal(current.number, "新しい文。", [])):
        assert not apply(memory, persona, log, generations, change, [page])
    assert len(log.read_all()) == 3


def test_single_sentence_keeps_other_bytes_and_provenance_and_self_once(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1)
    block = next(b for b in load_persona_assets(persona).blocks if b.id == "voice")
    target = sentence(persona, "voice", "発話")
    after = target.text.replace("発話", "語り")
    original = block.text
    calls = []

    def reviewer(prompt):
        calls.append(prompt)
        assert target.text not in prompt
        assert after in prompt
        return json.dumps(APPROVED)

    assert apply(memory, persona, log, generations, proposal(target.number, after, [page]), [page], reviewer=reviewer)
    assert len(calls) == 1
    assert (persona / block.file).read_text(encoding="utf-8") == original[:target.start] + after + original[target.end:]
    stored = json.loads((tmp_path / "generations.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert stored["content"] == original
    assert stored["changes"][0]["before"] == target.text
    assert stored["changes"][0]["after"] == after
    assert stored["changes"][0]["experiences"][0]["page"] == page.id
    assert stored["changes"][0]["review"] == APPROVED
    assert log.read_all()[-1].changes == stored["changes"]
    rows = [json.loads(line) for p in (tmp_path / "lifelog" / "recall").glob("*.jsonl")
            for line in p.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["intent"] == "self" and rows[0]["page"] == page.id


def test_deletion_does_not_call_review_and_append_is_at_end(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1)
    target = sentence(persona, "voice", "発話")
    calls = []
    assert apply(memory, persona, log, generations, proposal(target.number, "", [page]), [page],
                 reviewer=lambda prompt: calls.append(prompt) or "bad")
    assert calls == []
    new_sentence = "以前より、自然に語りかけることを大切にしている。"
    assert apply(memory, persona, log, generations,
                 {**proposal(None, new_sentence, [page]), "block_id": "voice"}, [page])
    block = next(b for b in load_persona_assets(persona).blocks if b.id == "voice")
    assert block.text.endswith(new_sentence)


def test_interrupted_before_swap_keeps_original(tmp_path: Path, monkeypatch) -> None:
    from mind.core.chores import persona_revise

    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1)
    block = next(b for b in load_persona_assets(persona).blocks if b.id == "voice")
    target = sentence(persona, "voice", "発話")
    before = (persona / block.file).read_bytes()

    def interrupted(_src, _dst):
        raise KeyboardInterrupt("停止")

    monkeypatch.setattr(persona_revise.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        apply(memory, persona, log, generations, proposal(target.number, target.text.replace("発話", "語り"), [page]), [page])
    assert (persona / block.file).read_bytes() == before
    assert log.read_all() == []
