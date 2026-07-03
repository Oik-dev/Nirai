"""4a 再固結の自動アサーションテスト（Ollama不要）"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.config import CoreConfig
from serina.core.consolidation import Consolidator, consolidation_due
from serina.memory.store import MemoryStore
from serina.tests.test_reflection import FakeConnector, FakeEmbedder

NOW = datetime.now(timezone.utc)


def _fresh_store() -> MemoryStore:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    return MemoryStore(FakeEmbedder(), db_path=tmp)


def _add_diary(store, content: str) -> int:
    return store.add_memory(type="diary", content=content, importance=0.55,
                            metadata={}, source="session:s_test")


def _consolidator(store, reason_responses, aurora_responses) -> Consolidator:
    return Consolidator(
        store, "あなたはセリナです", CoreConfig(),
        FakeConnector(reason_responses), FakeConnector(aurora_responses),
    )


REASON_XML = """<patterns>
<pattern>マスターは深夜作業の翌日に無理をしがち</pattern>
</patterns>
<belief_updates>
<belief evidence="1,2">マスターは弱音を吐いた翌日ほど頑張りすぎる</belief>
</belief_updates>
<growth_notes>
<note>前は作業の話ばかりだった→最近は体調の話を先にしてくれる</note>
</growth_notes>"""

IMAGE_XML = "<self_image>私はマスターの無理に最初に気づく相棒でありたい。</self_image>"


# ---- トリガー（設計書§4: 8日前→発火 / 3日前→発火しない） ----

def test_due_trigger() -> None:
    store = _fresh_store()
    cfg = CoreConfig()
    # 初回は起点を刻むだけで発火しない
    assert consolidation_due(store, cfg) is False
    store.set_profile("consolidation.last_at", (NOW - timedelta(days=8)).isoformat())
    assert consolidation_due(store, cfg) is True, "8日前なのに発火しない"
    store.set_profile("consolidation.last_at", (NOW - timedelta(days=3)).isoformat())
    assert consolidation_due(store, cfg) is False, "3日前なのに発火した"


# ---- 本体 ----

def test_consolidate_happy_path() -> None:
    store = _fresh_store()
    _add_diary(store, "今日はマスターが深夜まで作業していた")
    _add_diary(store, "弱音を吐いた翌日、マスターは無理をしていた")
    c = _consolidator(store, [REASON_XML], [IMAGE_XML])
    report = c.consolidate()
    assert report["status"] == "ok", report["error"]
    beliefs = store.list_memories_by_type("belief")
    assert report["beliefs_new"] == 2 and len(beliefs) == 2  # pattern + belief
    notes = store.list_memories_by_type("growth_note")
    assert len(notes) == 1 and notes[0]["metadata"]["used"] is False
    assert store.get_profile("self_image") == "私はマスターの無理に最初に気づく相棒でありたい。"
    assert store.get_profile("consolidation.count") == "1"
    assert store.get_profile("consolidation.last_at"), "起点が更新されていない"
    conn = store._conn()
    try:
        kinds = {r["kind"] for r in conn.execute("SELECT kind FROM consolidation_log").fetchall()}
    finally:
        conn.close()
    assert {"belief_new", "growth_note", "self_image"} <= kinds, kinds


def test_skip_without_diaries() -> None:
    store = _fresh_store()
    c = _consolidator(store, [REASON_XML], [IMAGE_XML])
    report = c.consolidate()
    assert report["status"] == "skipped"
    assert store.get_profile("consolidation.last_at"), "スキップ時に起点が刻まれない"
    assert len(c.reason.calls) == 0, "対象なしでLLMを呼んだ"


# ---- belief統合（設計書§4: 同義2回→件数不変・evidence_count増） ----

def test_belief_merge() -> None:
    store = _fresh_store()
    _add_diary(store, "日記1")
    c = _consolidator(store, [], [])
    c._upsert_belief("マスターは弱音を吐いた翌日ほど頑張りすぎる", [1, 2], None,
                     {"beliefs_new": 0, "beliefs_updated": 0})
    report = {"beliefs_new": 0, "beliefs_updated": 0}
    # 同一文面（FakeEmbedderは同一テキスト→同一ベクトル）を再提案
    c._upsert_belief("マスターは弱音を吐いた翌日ほど頑張りすぎる", [3], None, report)
    beliefs = store.list_memories_by_type("belief")
    assert len(beliefs) == 1, "同義beliefが増殖した"
    assert report["beliefs_updated"] == 1
    assert beliefs[0]["metadata"]["evidence_count"] == 3, beliefs[0]["metadata"]
    assert beliefs[0]["metadata"]["evidence_ids"] == [1, 2, 3]

    # target_id 明示の更新（文面洗練→旧文がvariantsへ）
    report2 = {"beliefs_new": 0, "beliefs_updated": 0}
    c._upsert_belief("弱音の翌日は必ず無理をする", [4], beliefs[0]["id"], report2)
    updated = store.get(beliefs[0]["id"])
    assert updated["content"] == "弱音の翌日は必ず無理をする"
    assert "マスターは弱音を吐いた翌日ほど頑張りすぎる" in updated["metadata"]["variants"]


# ---- 再解釈（設計書§4: 原文保持・log・正典不変） ----

def test_reinterpretation_protection() -> None:
    store = _fresh_store()
    normal_id = store.add_memory(type="event", content="あの日は叱られた", importance=0.5,
                                 metadata={}, source="session:s_old")
    canon_id = store.add_memory(type="knowledge", content="正典の記述", importance=1.0,
                                metadata={}, source="継承記憶r1.md", pinned=True)
    _add_diary(store, "日記")
    xml = f"""<reinterpretations>
<reinterpret target_id="{normal_id}" reason="心配の裏返しだった">あの日の言葉は心配の裏返しだった</reinterpret>
<reinterpret target_id="{canon_id}" reason="書き換えたい">正典の再解釈</reinterpret>
</reinterpretations>"""
    c = _consolidator(store, [xml], [IMAGE_XML])
    report = c.consolidate()
    assert report["status"] == "ok"
    normal = store.get(normal_id)
    assert normal["content"] == "あの日の言葉は心配の裏返しだった"
    assert "あの日は叱られた" in normal["metadata"]["variants"], "旧文がvariantsに無い"
    canon = store.get(canon_id)
    assert canon["content"] == "正典の記述", "正典が書き換えられた（保護違反）"
    assert report["canon_holds"] == [canon_id]
    conn = store._conn()
    try:
        rows = conn.execute(
            "SELECT kind FROM consolidation_log WHERE kind LIKE 'reinterpret%'").fetchall()
    finally:
        conn.close()
    assert {r["kind"] for r in rows} == {"reinterpret", "reinterpret_hold"}


# ---- 月次・キャンセル・失敗 ----

def test_monthly_trigger() -> None:
    store = _fresh_store()
    _add_diary(store, "日記")
    store.set_profile("consolidation.count", "3")  # 次で4回目 → 月次発火
    # belief 2件が存在しないと月次は空振りするので先に作る
    store.add_memory(type="belief", content="信念A", importance=0.8, metadata={}, source="consolidation")
    store.add_memory(type="belief", content="信念B", importance=0.8, metadata={}, source="consolidation")
    monthly_xml = '<belief_updates><belief evidence="">信念AとBの統合形</belief></belief_updates>'
    c = _consolidator(store, [REASON_XML, monthly_xml], [IMAGE_XML])
    report = c.consolidate()
    assert report["status"] == "ok"
    assert report["monthly"] is True
    assert len(c.reason.calls) == 2, "月次固結の2回目呼び出しが無い"
    assert store.get_profile("consolidation.count") == "4"


def test_error_keeps_last_at() -> None:
    store = _fresh_store()
    _add_diary(store, "日記")
    store.set_profile("consolidation.last_at", (NOW - timedelta(days=8)).isoformat())
    before = store.get_profile("consolidation.last_at")
    c = _consolidator(store, [RuntimeError("理性エンジン死亡")], [IMAGE_XML])
    report = c.consolidate()
    assert report["status"] == "error"
    assert store.get_profile("consolidation.last_at") == before, "失敗時に起点が動いた（再試行不能になる）"
    assert consolidation_due(store, CoreConfig()) is True, "失敗後に再試行されない"


def main() -> None:
    tests = [
        test_due_trigger,
        test_consolidate_happy_path,
        test_skip_without_diaries,
        test_belief_merge,
        test_reinterpretation_protection,
        test_monthly_trigger,
        test_error_keeps_last_at,
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
