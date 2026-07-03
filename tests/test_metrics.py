"""4c 計測・二層人格・ドリフト監査の自動アサーションテスト（Ollama不要）"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core import context
from serina.core.config import CoreConfig
from serina.core.reflection_parser import parse_reason_output
from serina.core.runtime import Core
from serina.memory.store import MemoryStore
from serina.tests.test_reflection import (
    DIARY_XML,
    FakeEmbedder,
    FakeSkill,
    _make_distiller,
    _make_pending_session,
)


def _fresh_store() -> MemoryStore:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    return MemoryStore(FakeEmbedder(), db_path=tmp)


def test_metrics_store() -> None:
    store = _fresh_store()
    store.add_metric("master_rating", 4.0, "察しがよかった")
    store.add_metric("master_rating", 2.0, None)
    rows = store.list_metrics("master_rating")
    assert len(rows) == 2
    assert rows[0]["value"] in (2.0, 4.0)
    assert store.list_metrics("unknown_key") == []


def test_injection_order_with_self_image() -> None:
    cfg = CoreConfig()
    out = context.build_system(
        "PERSONA_MARK",
        [{"type": "fact", "content": "STATIC_MARK", "metadata": {}}],
        [{"type": "fact", "content": "REF_MARK", "metadata": {}}],
        cfg,
        open_threads=[{"question": "THREAD_MARK", "context": None}],
        emotion={"narrative_mood": "MOOD_MARK", "shy": False},
        self_image="IMAGE_MARK",
    )
    order = [out.index(m) for m in
             ("PERSONA_MARK", "IMAGE_MARK", "MOOD_MARK", "STATIC_MARK", "REF_MARK", "THREAD_MARK")]
    assert order == sorted(order), f"二層人格の注入順序が崩れている: {order}"
    assert "根本原則に従う" in out, "成長層に憲法優先の但し書きが無い"


def test_callback_parse_and_metric() -> None:
    xml = DIARY_XML  # aurora用
    reason_xml = """<new_facts></new_facts>
<callback_score value="0.8"/>約束の話題を自然に想起できていた
<followup_hit value="1.6"/>面接を尋ねて喜ばれた"""
    r = parse_reason_output(reason_xml)
    assert r.callback_score == {"value": "0.8", "reason": "約束の話題を自然に想起できていた"}

    store = _fresh_store()
    sid = _make_pending_session(store)
    distiller, _ = _make_distiller(store, [reason_xml], [xml])
    report = distiller.distill_session(sid)
    assert report["status"] == "ok"
    cb = store.list_metrics("callback_rate")
    fu = store.list_metrics("followup_hit")
    assert len(cb) == 1 and cb[0]["value"] == 0.8
    assert len(fu) == 1 and fu[0]["value"] == 1.0, "範囲外の採点がクランプされていない"
    assert f"session:{sid}" in cb[0]["note"]


def test_turn_retrieval_recorded() -> None:
    store = _fresh_store()
    store.add_memory(type="fact", content="何かの記憶", importance=0.5, metadata={}, source="s")
    core = Core(store, "p", [FakeSkill()], config=CoreConfig())
    store.create_session("s1")
    core.turn("s1", "こんにちは")
    conn = store._conn()
    try:
        rows = conn.execute("SELECT * FROM turn_retrievals WHERE session_id = 's1'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, "想起IDが記録されていない"


def main() -> None:
    tests = [
        test_metrics_store,
        test_injection_order_with_self_image,
        test_callback_parse_and_metric,
        test_turn_retrieval_recorded,
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
