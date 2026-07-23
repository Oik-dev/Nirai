"""Sleep 人格提案器（§4.3 / §4.10）のテスト。"""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.chore_box import ChoreBox
from serina.core.chores.persona_propose import (
    ProposeMaterial,
    build_propose_prompt,
    gather_propose_material,
    run_idle_persona_propose_chunk,
    should_run_persona_propose,
)
from serina.core.chores.persona_revise import PERSONA_REVISE_CHORE_KIND
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.protection import ChangeLog
from serina.core.memory.store import MemoryStore
from serina.core.state.persona_propose_state import (
    load_persona_propose_state,
    save_persona_propose_state,
)


def _fake_embedder() -> OllamaEmbedder:
    def call_fn(model: str, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store(tmp: Path) -> MemoryStore:
    return MemoryStore(tmp / "memory.db", embedder=_fake_embedder(), vector_dim=4)


def _seed_diary(store: MemoryStore, content: str = "今日は長く話した一日だった。") -> None:
    store.add_memory(
        content=content,
        type="episodic",
        importance=0.8,
        sensitivity_grade=2,
        protection_grade="A",
    )


def test_should_run_persona_propose_calendar_day() -> None:
    # システムローカル暦日基準（日記朝礼と同じ）。固定UTCだとTZで日跨ぎがずれる
    morning = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    evening = morning.replace(hour=22)
    next_day = morning + timedelta(days=1)
    assert should_run_persona_propose(now=morning, last_propose_at=None) is True
    assert should_run_persona_propose(now=evening, last_propose_at=morning) is False
    assert should_run_persona_propose(now=next_day, last_propose_at=morning) is True


def test_build_propose_prompt_excludes_mood_words_section() -> None:
    material = ProposeMaterial(
        diaries=[],
        prefs_summary="紅茶が好き",
        relation_summary="最近は穏やか",
        mutable_blocks={"personality": "天真爛漫", "voice": "からかう", "love": "独占欲"},
    )
    prompt = build_propose_prompt(material)
    assert "気分の軌跡" not in prompt
    assert "【好みの要約】" in prompt
    assert "personality" in prompt


def test_run_idle_persona_propose_noop_without_material() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        box = ChoreBox(tmp / "chores.db")
        store = _fresh_store(tmp)
        change_log = ChangeLog(tmp / "c.jsonl")
        outcome = run_idle_persona_propose_chunk(
            box,
            memory_store=store,
            call_fn=lambda _p: '{"revise": true}',
            change_log=change_log,
            prefs_summary="",
            relation_summary="",
            persona_dir=ROOT / "prompt" / "persona",
        )
        assert outcome.asked is False
        assert outcome.advance_cooldown is False
        assert box.pending(kind=PERSONA_REVISE_CHORE_KIND) == []


def test_run_idle_persona_propose_revise_false_advances_cooldown() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        box = ChoreBox(tmp / "chores.db")
        store = _fresh_store(tmp)
        _seed_diary(store)
        change_log = ChangeLog(tmp / "c.jsonl")
        calls: list[str] = []

        def _call(prompt: str) -> str:
            calls.append(prompt)
            return '```json\n{"revise": false, "block_id": null, "new_content": null, "reason": "変化なし"}\n```'

        outcome = run_idle_persona_propose_chunk(
            box,
            memory_store=store,
            call_fn=_call,
            change_log=change_log,
            prefs_summary="好きな飲み物は紅茶",
            persona_dir=ROOT / "prompt" / "persona",
        )
        assert outcome.asked is True
        assert outcome.revise is False
        assert outcome.advance_cooldown is True
        assert outcome.enqueued is False
        assert len(calls) == 1
        reports = change_log.read_all()
        assert any(r.action == "persona提案見送り" for r in reports)


def test_run_idle_persona_propose_enqueues_revision() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        persona_dir = tmp / "persona"
        shutil.copytree(ROOT / "prompt" / "persona", persona_dir)
        box = ChoreBox(tmp / "chores.db")
        store = _fresh_store(tmp)
        _seed_diary(store, "マスターとの口調が少し柔らかくなった気がする。")
        change_log = ChangeLog(tmp / "c.jsonl")
        from serina.core.persona_assets import load_persona_assets

        before = next(b for b in load_persona_assets(persona_dir).blocks if b.id == "voice")
        n = max(1, len(before.text) // 20)
        new_content = "微調整。" + before.text[n:]

        def _call(_prompt: str) -> str:
            payload = {
                "revise": True,
                "block_id": "voice",
                "new_content": new_content,
                "reason": "口調の持続的な柔らかさ",
            }
            import json

            return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

        outcome = run_idle_persona_propose_chunk(
            box,
            memory_store=store,
            call_fn=_call,
            change_log=change_log,
            relation_summary="関係は安定",
            persona_dir=persona_dir,
        )
        assert outcome.enqueued is True
        assert outcome.block_id == "voice"
        jobs = box.pending(kind=PERSONA_REVISE_CHORE_KIND)
        assert len(jobs) == 1
        assert jobs[0].payload["mood_contaminated"] is False
        assert jobs[0].payload["source"] == "sleep_propose"


def test_run_idle_persona_propose_llm_failure_does_not_advance() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        box = ChoreBox(tmp / "chores.db")
        store = _fresh_store(tmp)
        _seed_diary(store)
        change_log = ChangeLog(tmp / "c.jsonl")
        outcome = run_idle_persona_propose_chunk(
            box,
            memory_store=store,
            call_fn=lambda _p: "これはJSONではない",
            change_log=change_log,
            prefs_summary="材料あり",
            persona_dir=ROOT / "prompt" / "persona",
            max_retries=2,
        )
        assert outcome.asked is True
        assert outcome.failure_reason is not None
        assert outcome.advance_cooldown is False


def test_persona_propose_state_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "persona_propose_state.json"
        assert load_persona_propose_state(path) is None
        stamp = datetime(2026, 7, 19, 15, 0, tzinfo=timezone.utc)
        save_persona_propose_state(path, last_propose_at=stamp)
        loaded = load_persona_propose_state(path)
        assert loaded == stamp


def test_gather_propose_material_reads_diaries() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store = _fresh_store(tmp)
        _seed_diary(store, "日記A")
        material = gather_propose_material(
            store,
            prefs_summary="好み",
            persona_dir=ROOT / "prompt" / "persona",
            diary_limit=3,
        )
        assert not material.is_empty()
        assert material.diaries[0].content == "日記A"


def main() -> None:
    tests = [
        test_should_run_persona_propose_calendar_day,
        test_build_propose_prompt_excludes_mood_words_section,
        test_run_idle_persona_propose_noop_without_material,
        test_run_idle_persona_propose_revise_false_advances_cooldown,
        test_run_idle_persona_propose_enqueues_revision,
        test_run_idle_persona_propose_llm_failure_does_not_advance,
        test_persona_propose_state_roundtrip,
        test_gather_propose_material_reads_diaries,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [ERROR] {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        raise SystemExit(1)
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()


def test_load_persona_propose_state_corrupt_file_returns_none() -> None:
    """状態ファイル破損で起動を止めない（未記録扱いで続行）。"""
    import tempfile
    from pathlib import Path

    from serina.core.state.persona_propose_state import load_persona_propose_state

    with tempfile.TemporaryDirectory() as tmpdir:
        broken = Path(tmpdir) / "persona_propose_state.json"
        broken.write_text("{{{壊れたJSON", encoding="utf-8")
        assert load_persona_propose_state(broken) is None
