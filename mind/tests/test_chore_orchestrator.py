"""蒸留消化の表口（トリガー配線）テスト。設計書 §2.4。

core/chores/orchestrator.py の run_startup_chores(③朝礼)・run_session_end_chores(①終了時)
を検査する。LLM不要（call_fnをスタブ化）。DECISIONS 2026-07-11の未解決事項#1
（消化ロジックを実際に呼ぶ表口が無い）を解消したことの結線検査。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.chore_box import ChoreBox
from mind.core.chores.orchestrator import (
    PERSONA_REVISE_CHORE_KIND,
    build_default_lane_call_fns,
    run_growth_chores,
    run_idle_chore_tick,
    run_idle_digest_chunk,
    run_idle_export_life,
    run_idle_persona_revise_chunk,
    run_session_end_chores,
    run_startup_chores,
)
from mind.core.config import ThresholdsConfig
from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.protection import ChangeLog, GenerationStore
from mind.core.memory.store import MemoryStore
from mind.core.runtime import Core
from mind.core.idea import PERSONA_DIR

class StubBrain:
    def __init__(self, script: dict) -> None:
        self.script = script

    def converse(self, pack) -> dict:  # noqa: ANN001
        return self.script


def _fake_embedder() -> OllamaEmbedder:
    def call_fn(model: str, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0] if "散歩" in text else [0.0, 0.0, 0.0, 1.0]

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    return MemoryStore(
        str(Path(tempfile.mkdtemp()) / "test_memory.db"), embedder=_fake_embedder(), vector_dim=4,
    )


def _fresh_chore_box() -> ChoreBox:
    return ChoreBox(Path(tempfile.mkdtemp()) / "test_chore_box.db")


def _thresholds(fragment_turns: int = 20) -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5, "記憶候補": 0.6},
        mood_guard_max_delta_per_turn=0.1,
        chore_fragment_turns=fragment_turns,
    )


def _reply_brain() -> StubBrain:
    return StubBrain({
        "reply": "うん",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })


def _distillation_call_fn(prompt: str) -> str:
    return json.dumps({
        "candidates": [
            {
                "quote": "最近散歩が好きなんだ",
                "content": "散歩が好きだという話",
                "type": "fact",
                "importance": 0.6,
                "confidence": 0.9,
            }
        ]
    })


def test_run_startup_chores_consumes_leftover_pending_job() -> None:
    """③次回起動時の朝礼: 前回の積み残し(pending)を新しい日の残弾で消化する。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={
        "turns": [
            {"speaker": "master", "text": "最近散歩が好きなんだ"},
            {"speaker": "serina", "text": "いいですね"},
        ]
    })

    summary = run_startup_chores(
        box, memory_store=store, thresholds=_thresholds(), lane_call_fns={"local": _distillation_call_fn},
    )

    assert summary.total_accepted == 1
    assert box.pending(kind="蒸留") == []
    recalled = store.recall("散歩の話題", top_k=1)
    assert recalled[0].content == "散歩が好きだという話"


def test_run_session_end_chores_flushes_and_consumes() -> None:
    """①セッション終了時: end_session()で端数を積んでから、閉じる前に消化する。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(), chore_box=box)
    core.turn("最近散歩が好きなんだ", _reply_brain())

    job_ids, summary = run_session_end_chores(
        core, box, memory_store=store, thresholds=_thresholds(),
        lane_call_fns={"local": _distillation_call_fn},
    )

    assert len(job_ids) == 1  # end_session時の端数flush分
    assert summary.total_accepted == 1
    assert box.pending(kind="蒸留") == []
    assert core.session.turns == []  # Core状態は既にリセット済み
    recalled = store.recall("散歩の話題", top_k=1)
    assert recalled[0].content == "散歩が好きだという話"


def test_run_session_end_chores_also_consumes_older_pending_jobs() -> None:
    """①は今回セッションの端数だけでなく、宿題箱に残る過去の積み残しも合わせて消化する。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={
        "turns": [{"speaker": "master", "text": "前回の積み残し"}]
    })
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(), chore_box=box)
    core.turn("最近散歩が好きなんだ", _reply_brain())

    job_ids, summary = run_session_end_chores(
        core, box, memory_store=store, thresholds=_thresholds(),
        lane_call_fns={"local": _distillation_call_fn},
    )

    assert len(job_ids) == 1  # 新規に積んだのは端数の1件のみ
    assert len(summary.processed) == 2  # だが消化対象は積み残し1件+端数1件=2件


def test_run_idle_digest_chunk_consumes_only_limit_jobs() -> None:
    """②アイドル小分け消化: limit件だけ消化し、残りはpendingのまま残す。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={
        "turns": [{"speaker": "master", "text": "最近散歩が好きなんだ"}]
    })
    box.enqueue("蒸留", lane="local", payload={
        "turns": [{"speaker": "master", "text": "もうひとつの断片"}]
    })

    summary = run_idle_digest_chunk(
        box, memory_store=store, thresholds=_thresholds(),
        lane_call_fns={"local": _distillation_call_fn}, limit=1,
    )

    assert len(summary.processed) == 1
    assert len(box.pending(kind="蒸留")) == 1  # 2件目は次回の機会に持ち越し


def test_run_idle_digest_chunk_noop_when_empty() -> None:
    box = _fresh_chore_box()
    store = _fresh_store()

    summary = run_idle_digest_chunk(
        box, memory_store=store, thresholds=_thresholds(),
        lane_call_fns={"local": _distillation_call_fn}, limit=1,
    )

    assert summary.processed == []


def test_build_default_lane_call_fns_local_only() -> None:
    """§9.3: cloud車線は永久退役。localのみが常に返る。"""
    lane_call_fns = build_default_lane_call_fns()

    assert "local" in lane_call_fns
    assert callable(lane_call_fns["local"])
    assert "cloud" not in lane_call_fns


def test_run_idle_export_life_respects_min_interval() -> None:
    from datetime import datetime, timedelta, timezone

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db = tmp / "m.db"
        # 最小の memories テーブルだけ用意（export_life が読む）
        import sqlite3

        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY, type TEXT, content TEXT, "
            "importance REAL, sensitivity_grade INTEGER, protection_grade TEXT, "
            "cosmetic_version TEXT, created_at TEXT, last_accessed TEXT, access_count INTEGER)"
        )
        conn.commit()
        conn.close()
        life = tmp / "life"
        now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
        weekly_log = tmp / "weekly.json"  # 本物の data/ を汚さない
        assert run_idle_export_life(
            db_path=db, life_dir=life, weekly_log_path=weekly_log, now=now,
        ) is True
        assert run_idle_export_life(
            db_path=db,
            life_dir=life,
            last_export_at=now,
            min_interval_seconds=300,
            now=now + timedelta(seconds=10),
        ) is False


def test_run_idle_persona_revise_chunk_applies_pending_job() -> None:
    import shutil

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        persona_src = PERSONA_DIR
        persona_dir = tmp / "persona"
        shutil.copytree(persona_src, persona_dir)
        from mind.core.persona_assets import load_persona_assets

        before = next(b for b in load_persona_assets(persona_dir).blocks if b.id == "voice")
        n = max(1, len(before.text) // 10)
        new_content = "微調整。" + before.text[n:]
        box = ChoreBox(tmp / "chores.db")
        box.enqueue(
            PERSONA_REVISE_CHORE_KIND,
            lane="local",
            payload={"block_id": "voice", "new_content": new_content, "reason": "テスト"},
        )
        change_log = ChangeLog(tmp / "c.jsonl")
        generation_store = GenerationStore(tmp / "g.jsonl")
        ok = run_idle_persona_revise_chunk(
            box,
            change_log=change_log,
            generation_store=generation_store,
            persona_dir=persona_dir,
            backup_db_fn=lambda *_a, **_k: tmp / "b.db",
        )
        assert ok is True
        assert box.pending(kind=PERSONA_REVISE_CHORE_KIND) == []
        assert (persona_dir / before.file).read_text(encoding="utf-8") == new_content


def test_run_growth_chores_consumes_persona_revise_job() -> None:
    """日界／朝礼用の成長系経路が persona改訂を消化する（配線切れ回帰防止）。"""
    import shutil

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        persona_dir = tmp / "persona"
        shutil.copytree(PERSONA_DIR, persona_dir)
        from mind.core.persona_assets import load_persona_assets

        before = next(b for b in load_persona_assets(persona_dir).blocks if b.id == "voice")
        n = max(1, len(before.text) // 10)
        new_content = "成長経路微調整。" + before.text[n:]
        box = ChoreBox(tmp / "chores.db")
        box.enqueue(
            PERSONA_REVISE_CHORE_KIND,
            lane="local",
            payload={"block_id": "voice", "new_content": new_content, "reason": "成長経路テスト"},
        )
        store = _fresh_store()
        core = Core(
            persona_text="人格",
            absolute_rules="ルール",
            thresholds=_thresholds(),
            chore_box=box,
            memory_store=store,
        )
        change_log = ChangeLog(tmp / "c.jsonl")
        generation_store = GenerationStore(tmp / "g.jsonl")
        # db_path 未指定: バックアップ経路を踏まず改訂本体だけ検証する
        # （実GUIは本物の db_path を渡す。ここは成長経路の配線切れ回帰防止が目的）
        outcome = run_growth_chores(
            core,
            box,
            memory_store=store,
            thresholds=_thresholds(),
            lane_call_fns={"local": lambda _p: ""},
            change_log=change_log,
            generation_store=generation_store,
            persona_dir=persona_dir,
        )
        assert outcome.persona_revise_runs >= 1
        assert box.pending(kind=PERSONA_REVISE_CHORE_KIND) == []
        assert (persona_dir / before.file).read_text(encoding="utf-8") == new_content


def test_run_growth_chores_rebuilds_summaries_from_facts_before_persona_propose() -> None:
    """死んでいたsummaries配線の復活（設計書決定事項5・2026-07-23）。

    facts台帳の好みカテゴリactive factからprefs_summaryが組み立てられ、
    同じ呼び出し内でcore.prefs_summaryへ反映され（persona提案の材料強化）、
    change_logに日本語の変更レポートが残ることを検査する。
    """
    import shutil

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        persona_dir = tmp / "persona"
        shutil.copytree(PERSONA_DIR, persona_dir)
        box = _fresh_chore_box()
        store = _fresh_store()
        store.facts.add_fact(
            subject="マスター",
            predicate="好み",
            object="散歩",
            statement="マスターは散歩が好きになった",
            confidence=0.9,
            episode_ids=[1],
            status="active",
            category="好み",
        )
        core = Core(
            persona_text="人格",
            absolute_rules="ルール",
            thresholds=_thresholds(),
            chore_box=box,
            memory_store=store,
        )
        change_log = ChangeLog(tmp / "c.jsonl")
        summaries_path = tmp / "summaries.json"

        def _propose_call_fn(_prompt: str) -> str:
            return json.dumps(
                {"revise": False, "block_id": None, "new_content": None, "reason": "改訂不要"}
            )

        run_growth_chores(
            core,
            box,
            memory_store=store,
            thresholds=_thresholds(),
            lane_call_fns={"local": _propose_call_fn},
            change_log=change_log,
            persona_dir=persona_dir,
            summaries_path=summaries_path,
        )

        assert "マスターは散歩が好きになった" in core.prefs_summary
        saved = json.loads(summaries_path.read_text(encoding="utf-8"))
        assert "マスターは散歩が好きになった" in saved["prefs_summary"]["content"]
        reports = change_log.read_all()
        assert any(r.action == "要約ブロック更新" for r in reports)


def test_run_idle_persona_revise_chunk_shelve_records_change_report() -> None:
    """関所拒否での棚上げ時も変更レポートが残る（蒸留側と同じ監査一貫性）。"""
    import shutil

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        persona_dir = tmp / "persona"
        shutil.copytree(PERSONA_DIR, persona_dir)
        box = ChoreBox(tmp / "chores.db")
        box.enqueue(
            PERSONA_REVISE_CHORE_KIND,
            lane="local",
            payload={
                "block_id": "voice",
                "new_content": "全面差し替え",
                "mood_contaminated": True,
            },
        )
        change_log = ChangeLog(tmp / "c.jsonl")
        generation_store = GenerationStore(tmp / "g.jsonl")
        ok = run_idle_persona_revise_chunk(
            box,
            change_log=change_log,
            generation_store=generation_store,
            persona_dir=persona_dir,
            backup_db_fn=lambda *_a, **_k: tmp / "b.db",
        )
        assert ok is False
        assert box.pending(kind=PERSONA_REVISE_CHORE_KIND) == []
        reports = change_log.read_all()
        assert len(reports) == 1
        assert reports[0].action == "persona改訂棚上げ"
        # 実ファイルは不変（適用されていない）
        from mind.core.persona_assets import load_persona_assets

        before = next(b for b in load_persona_assets(persona_dir).blocks if b.id == "voice")
        assert before.text != "全面差し替え"


def test_run_idle_chore_tick_export_when_higher_stages_idle() -> None:
    """上位段が空のとき export_life までフォールスルーする。"""
    import sqlite3

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db = tmp / "m.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY, type TEXT, content TEXT, "
            "importance REAL, sensitivity_grade INTEGER, protection_grade TEXT, "
            "cosmetic_version TEXT, created_at TEXT, last_accessed TEXT, access_count INTEGER)"
        )
        conn.commit()
        conn.close()
        box = _fresh_chore_box()
        store = _fresh_store()
        core = Core(
            persona_text="人格",
            absolute_rules="ルール",
            thresholds=_thresholds(),
            chore_box=box,
            memory_store=store,
        )
        change_log = ChangeLog(tmp / "c.jsonl")
        generation_store = GenerationStore(tmp / "g.jsonl")
        outcome = run_idle_chore_tick(
            core,
            box,
            memory_store=store,
            thresholds=_thresholds(),
            lane_call_fns={"local": lambda _p: "要約なし"},
            change_log=change_log,
            generation_store=generation_store,
            db_path=db,
            life_dir=tmp / "life",
            weekly_log_path=tmp / "weekly.json",  # 本物の data/ を汚さない
            export_min_interval_seconds=0,
            last_export_life_at=None,
        )
        assert outcome.kind == "export_life"
        assert outcome.progressed is True
        assert (tmp / "life" / "index.md").exists()


def main() -> None:
    tests = [
        test_run_startup_chores_consumes_leftover_pending_job,
        test_run_session_end_chores_flushes_and_consumes,
        test_run_session_end_chores_also_consumes_older_pending_jobs,
        test_run_idle_digest_chunk_consumes_only_limit_jobs,
        test_run_idle_digest_chunk_noop_when_empty,
        test_build_default_lane_call_fns_local_only,
        test_run_idle_export_life_respects_min_interval,
        test_run_idle_persona_revise_chunk_applies_pending_job,
        test_run_growth_chores_consumes_persona_revise_job,
        test_run_idle_persona_revise_chunk_shelve_records_change_report,
        test_run_idle_chore_tick_export_when_higher_stages_idle,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
