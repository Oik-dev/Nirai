"""人格の経験根拠と変更前本文を守る関所（設計書 §4.3）。"""

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

from mind.core.chores.persona_revise import revise_persona_block
from mind.core.idea import PERSONA_DIR
from mind.core.memory.page import Page, write_page
from mind.core.persona_assets import load_persona_assets
from mind.core.protection import ChangeLog, GenerationStore, ProtectionError

JST = timezone(timedelta(hours=9))
BODY = "一緒に暮らした海辺の時間を振り返ると、以前より自然な言葉が増えてきた。"


def setup(tmp: Path):
    persona = tmp / "persona"
    shutil.copytree(PERSONA_DIR, persona)
    return tmp / "memory", persona, ChangeLog(tmp / "changes.jsonl"), GenerationStore(tmp / "generations.jsonl")


def reflection(memory: Path, day: int, body: str = BODY):
    at = datetime(2026, 10, day, 10, 0, tzinfo=JST)
    page = Page(id=f"reflection-week-2026-10-{day}", kind="reflection", start=at, end=at,
                source=(), concepts=(), body="").with_words(title="振り返り", gist="海辺", importance=5,
                                                       written_by="test", body=body)
    path = write_page(memory, page)
    return page, path


def change(before: str, after: str, pages: list[Page], quote: str = BODY):
    return {"before": before, "after": after,
            "experiences": [{"page": p.id, "quote": quote} for p in pages]}


def revise(tmp: Path, block_id: str, changes: list[dict], pages: list[Page]):
    memory, persona, changes_log, generations = setup(tmp)
    for page in pages:
        write_page(memory, page)
    result = revise_persona_block(block_id, changes, materials=pages, memory_dir=memory,
                                  reason="経験による変化", change_log=changes_log,
                                  generation_store=generations, persona_dir=persona)
    return result, memory, persona, changes_log, generations


def test_missing_evidence_declaration_protects_identity_and_boundary(tmp_path: Path) -> None:
    page, _ = reflection(tmp_path / "source", 1)
    for block_id in ("identity", "boundary"):
        with pytest.raises(ProtectionError):
            revise(tmp_path / block_id, block_id,
                   [change("名前", "別名", [page])], [page])


def test_core_principles_requires_three_distinct_weeks(tmp_path: Path) -> None:
    page, _ = reflection(tmp_path / "source", 1)
    memory, persona, log, generations = setup(tmp_path / "check")
    write_page(memory, page)
    block = next(b for b in load_persona_assets(persona).blocks if b.id == "core_principles")
    original = block.text
    sentence = "・わたしは「わたし」として存在し、自身の思考・感情・意図に基づいて行動を選択します。"
    applied = revise_persona_block("core_principles", [change(sentence, sentence.replace("選択", "決定"), [page])],
                                    materials=[page], memory_dir=memory, reason="test",
                                    change_log=log, generation_store=generations, persona_dir=persona)
    assert not applied
    assert (persona / block.file).read_text(encoding="utf-8") == original
    assert any("違う週" in report.reason for report in log.read_all())
    assert not generations.has_persona_block("core_principles")


def test_three_weeks_allow_a_small_core_principle_change(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    pages = [reflection(memory, day)[0] for day in (1, 8, 15)]
    block = next(b for b in load_persona_assets(persona).blocks if b.id == "core_principles")
    before = "・わたしは「わたし」として存在し、自身の思考・感情・意図に基づいて行動を選択します。"
    after = before.replace("選択", "決定")
    assert revise_persona_block("core_principles", [change(before, after, pages)], materials=pages,
                                memory_dir=memory, reason="三週の経験", change_log=log,
                                generation_store=generations, persona_dir=persona)
    assert (persona / block.file).read_text(encoding="utf-8") == block.text.replace(before, after)
    assert generations.has_persona_block("core_principles")


def test_deleted_unknown_and_incorrect_quotes_are_rejected_individually(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, path = reflection(memory, 1)
    voice = next(b for b in load_persona_assets(persona).blocks if b.id == "voice")
    before = next(line for line in voice.text.splitlines() if line.startswith("・言語："))
    after = before.replace("発話", "語り")
    invalid = [
        change(before, after, [page], "振り返りの本文にはない長い言葉です。"),
        change(before, after, [page], "短い引用"),
        {"before": before, "after": after, "experiences": [{"page": "unknown", "quote": BODY}]},
    ]
    assert not revise_persona_block("voice", invalid, materials=[page], memory_dir=memory, reason="test",
                                    change_log=log, generation_store=generations, persona_dir=persona)
    assert len(log.read_all()) == 3
    assert all("経験を添えられなかった" in item.reason for item in log.read_all())
    path.unlink()
    assert not revise_persona_block("voice", [change(before, after, [page])], materials=[page],
                                    memory_dir=memory, reason="test", change_log=log,
                                    generation_store=generations, persona_dir=persona)


def test_quote_can_match_whitespace_and_character_width_normalized(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1, "振り返れば　半角ABCと全角１２３が少しずつ馴染んできた。")
    voice = next(b for b in load_persona_assets(persona).blocks if b.id == "voice")
    before = next(line for line in voice.text.splitlines() if line.startswith("・言語："))
    quote = "振り返れば 半角ＡＢＣと全角123が少しずつ馴染んできた。"
    assert revise_persona_block("voice", [change(before, before.replace("発話", "語り"), [page], quote)],
                                materials=[page], memory_dir=memory, reason="表記揺れ", change_log=log,
                                generation_store=generations, persona_dir=persona)


def test_before_unique_and_20_percent_limit(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1)
    voice = next(b for b in load_persona_assets(persona).blocks if b.id == "voice")
    assert not revise_persona_block("voice", [change("・", "別の文字", [page])], materials=[page],
                                    memory_dir=memory, reason="test", change_log=log,
                                    generation_store=generations, persona_dir=persona)
    assert "ちょうど1回" in log.read_all()[0].reason
    with pytest.raises(ProtectionError, match="20%"):
        revise_persona_block("voice", [change(voice.text, "まったく別の人格。", [page])],
                             materials=[page], memory_dir=memory, reason="test",
                             change_log=log, generation_store=generations, persona_dir=persona)
    assert not generations.has_persona_block("voice")


def test_unchanged_sentences_and_provenance_and_self_trace(tmp_path: Path) -> None:
    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1)
    voice = next(b for b in load_persona_assets(persona).blocks if b.id == "voice")
    original = voice.text
    before = next(line for line in original.splitlines() if line.startswith("・言語："))
    after = before.replace("発話", "語り")
    another = next(line for line in original.splitlines() if line.startswith("・禁止："))
    another_after = another.replace("乱用", "多用")
    changes = [change(before, after, [page], BODY.replace("時間", "時間")),
               change(another, another_after, [page])]
    assert revise_persona_block("voice", changes, materials=[page], memory_dir=memory, reason="test",
                                change_log=log, generation_store=generations, persona_dir=persona)
    assert (persona / voice.file).read_text(encoding="utf-8") == original.replace(before, after).replace(another, another_after)
    entry = json.loads((tmp_path / "generations.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert entry["content"] == original
    assert entry["changes"] == changes
    assert log.read_all()[-1].changes == changes
    recall_rows = [json.loads(row) for path in (tmp_path / "lifelog" / "recall").glob("*.jsonl")
                   for row in path.read_text(encoding="utf-8").splitlines()]
    assert len(recall_rows) == 1 and recall_rows[0]["page"] == page.id and recall_rows[0]["intent"] == "self"


def test_stopped_before_swap_keeps_original_block(tmp_path: Path, monkeypatch) -> None:
    from mind.core.chores import persona_revise

    memory, persona, log, generations = setup(tmp_path)
    page, _ = reflection(memory, 1)
    voice = next(b for b in load_persona_assets(persona).blocks if b.id == "voice")
    before = next(line for line in voice.text.splitlines() if line.startswith("・言語："))
    original = (persona / voice.file).read_bytes()

    def interrupted(_src, _dst):
        raise KeyboardInterrupt("止まった")

    monkeypatch.setattr(persona_revise.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        revise_persona_block("voice", [change(before, before.replace("発話", "語り"), [page])],
                             materials=[page], memory_dir=memory, reason="test",
                             change_log=log, generation_store=generations, persona_dir=persona)
    assert (persona / voice.file).read_bytes() == original
    assert log.read_all() == []
