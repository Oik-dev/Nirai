"""3c 可変減衰・二経路想起・感情重力の自動アサーションテスト（Ollama不要）"""

from __future__ import annotations

import math
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core import context
from serina.core.config import CoreConfig
from serina.core.reflection import Distiller
from serina.core.runtime import Core
from serina.memory.store import MemoryStore
from serina.tests.test_reflection import FakeConnector, FakeEmbedder, FakeSkill
from serina.tools.backfill_keywords import run_backfill

NOW = datetime.now(timezone.utc)


def _fresh_store() -> MemoryStore:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    return MemoryStore(FakeEmbedder(), db_path=tmp)


def _mem(type: str = "diary", days_ago: float = 0.0, pinned: bool = False, meta=None) -> dict:
    return {
        "type": type,
        "pinned": pinned,
        "metadata": meta or {},
        "last_accessed": (NOW - timedelta(days=days_ago)).isoformat(),
    }


def _set_mem_stats(store, memory_id: int, access_count: int, days_ago: float) -> None:
    conn = store._conn()
    try:
        conn.execute(
            "UPDATE memories SET access_count = ?, last_accessed = ? WHERE id = ?",
            (access_count, (NOW - timedelta(days=days_ago)).isoformat(), memory_id),
        )
        conn.commit()
    finally:
        conn.close()


def _distiller(store, state: dict, mood=None) -> tuple[Distiller, SimpleNamespace, dict]:
    d = Distiller(store, "p", CoreConfig(), FakeConnector([]), FakeConnector([]))
    result = SimpleNamespace(state=state, narrative_mood=mood)
    report = {"state_changes": [], "state_dropped": []}
    return d, result, report


# ---- 減衰 ----

def test_decay_halving() -> None:
    store = _fresh_store()
    assert abs(store._decay_score(_mem("diary", 30.0), NOW) - 0.5) < 1e-6, "H=30でΔt=30が半減しない"
    assert abs(store._decay_score(_mem("diary", 0.0), NOW) - 1.0) < 1e-6
    assert abs(store._decay_score(_mem("fact", 180.0), NOW) - 0.5) < 1e-6, "H=180でΔt=180が半減しない"


def test_decay_floor() -> None:
    store = _fresh_store()
    cfg = store.search_config
    floor_score = store._hybrid_score(relevance=1.0, decay=0.0, importance=1.0)
    expected = cfg.alpha * cfg.kappa + cfg.gamma
    assert abs(floor_score - expected) < 1e-9, f"フロア値が想定外: {floor_score}"
    assert floor_score > 0.4, "完全に沈んだ記憶が浮上できない"


def test_pinned_no_decay() -> None:
    store = _fresh_store()
    assert store._decay_score(_mem("diary", 365.0, pinned=True), NOW) == 1.0


def test_metadata_half_life_override() -> None:
    store = _fresh_store()
    d = store._decay_score(_mem("diary", 3.0, meta={"half_life_days": 3}), NOW)
    assert abs(d - 0.5) < 1e-6, f"metadata上書きが効いていない: {d}"


# ---- 二経路想起 ----

def test_invalidated_excluded() -> None:
    store = _fresh_store()
    text = "マスターは大阪に住んでいる"
    mid = store.add_memory(
        type="fact", content=text, importance=0.7,
        metadata={"trigger_keywords": ["大阪", "住まい"]}, source="s",
    )
    assert store.search(text, k=5), "前提: 失効前は検索に出る"
    assert store.list_triggered("大阪の話"), "前提: 失効前はトリガーに出る"
    meta = store.get(mid)["metadata"]
    meta["invalidated_at"] = "2026-07-03T00:00:00+00:00"
    store.update(mid, metadata=meta)
    assert not any(m["id"] == mid for m in store.search(text, k=5)), "失効が自発想起に出る"
    assert not any(m["id"] == mid for m in store.list_triggered("大阪の話")), "失効がトリガーに出る"
    assert not any(m["id"] == mid for m in store.list_hot_memories(0, 9999, 10)), "失効がホット層に出る"


def test_trigger_mount() -> None:
    store = _fresh_store()
    store.add_memory(
        type="knowledge", content="宮古島の高野漁港近くのビーチで再会する約束",
        importance=1.0, metadata={"trigger_keywords": ["宮古島", "約束の海"]},
        source="継承記憶r1.md", pinned=True,
    )
    cfg = CoreConfig()
    core = Core(store, "PERSONA", [FakeSkill()], config=cfg)
    store.create_session("s_trig")
    core.turn("s_trig", "宮古島の話をしようか")
    system = FakeSkill.last_ctx.system_prompt
    assert "約束・最優先" in system and "高野漁港" in system, "トリガー想起が発火していない"

    core.turn("s_trig", "全然関係ないラーメンの話")
    # 自発想起（関連記憶ブロック）には出てよい。静的マウントブロックが出ないことを確認
    assert "約束・最優先" not in FakeSkill.last_ctx.system_prompt, "キーワード無しで静的マウントされた"

    # 800字打ち切り: 巨大な記憶の後続は載らない
    long_mem = {"type": "fact", "content": "あ" * 900, "metadata": {}}
    small_mem = {"type": "fact", "content": "小さい記憶", "metadata": {}}
    out = context.build_system("P", [long_mem, small_mem], [], cfg)
    assert "小さい記憶" not in out, "capを超えて注入された"


def test_hot_promotion() -> None:
    store = _fresh_store()
    hot_id = store.add_memory(type="fact", content="よく参照される記憶", importance=0.5,
                              metadata={}, source="s")
    cold_id = store.add_memory(type="fact", content="あまり参照されない記憶", importance=0.5,
                               metadata={}, source="s")
    stale_id = store.add_memory(type="fact", content="昔よく参照された記憶", importance=0.5,
                                metadata={}, source="s")
    _set_mem_stats(store, hot_id, access_count=5, days_ago=1)
    _set_mem_stats(store, cold_id, access_count=4, days_ago=1)
    _set_mem_stats(store, stale_id, access_count=10, days_ago=30)
    ids = {m["id"] for m in store.list_hot_memories(min_access=5, recent_days=14, limit=3)}
    assert hot_id in ids, "昇格条件を満たすのに昇格しない"
    assert cold_id not in ids, "参照不足なのに昇格した"
    assert stale_id not in ids, "参照が古いのに昇格した（降格が効いていない）"


# ---- 感情重力 ----

def test_gravity_pipeline() -> None:
    store = _fresh_store()
    store.set_profile("emotion.intimacy", "0.5")
    store.set_profile("emotion.last_state_update", NOW.isoformat())
    d, result, report = _distiller(
        store, {"intimacy": {"value": "0.9", "reason": "極端な提案"}})
    d._apply_state("s", result, report)
    got = float(store.get_profile("emotion.intimacy"))
    assert abs(got - 0.65) < 0.01, f"0.5→提案0.9はクランプで0.65のはず: {got}"


def test_gravity_time_only() -> None:
    store = _fresh_store()
    store.set_profile("emotion.intimacy", "0.5")
    store.set_profile(
        "emotion.last_state_update", (NOW - timedelta(days=3)).isoformat())
    d, result, report = _distiller(store, {})
    d._apply_state("s", result, report)
    got = float(store.get_profile("emotion.intimacy"))
    expected = 0.5 + (0.4 - 0.5) * (1 - math.exp(-1))  # τ=3, Δt=3 → 約63%回帰
    assert abs(got - expected) < 0.01, f"時の力の回帰が想定外: {got} (期待 {expected:.4f})"


def test_shy_spike() -> None:
    store = _fresh_store()
    store.set_profile("emotion.intimacy", "0.9")
    store.set_profile("emotion.tension", "0.3")
    store.set_profile("emotion.last_state_update", NOW.isoformat())
    d, result, report = _distiller(
        store, {"intimacy": {"value": "1.0", "reason": "愛の告白"}})
    d._apply_state("s", result, report)
    assert float(store.get_profile("emotion.intimacy")) == 1.0
    tension = float(store.get_profile("emotion.tension"))
    assert abs(tension - 0.55) < 0.01, f"照れ隠しspikeが効いていない: {tension}"

    # 境界値: 最終intimacyがちょうど0.95（>=判定）でも発動する
    store2 = _fresh_store()
    store2.set_profile("emotion.intimacy", "0.8")
    store2.set_profile("emotion.tension", "0.3")
    # Δt=0を厳密に作る（実行までの数msの重力混入を防ぐため僅かに未来に置く→max(Δt,0)=0）
    store2.set_profile(
        "emotion.last_state_update", (NOW + timedelta(minutes=5)).isoformat())
    d2, result2, report2 = _distiller(
        store2, {"intimacy": {"value": "0.95", "reason": "境界"}})
    d2._apply_state("s", result2, report2)
    assert abs(float(store2.get_profile("emotion.intimacy")) - 0.95) < 1e-9
    tension2 = float(store2.get_profile("emotion.tension"))
    assert abs(tension2 - 0.55) < 0.01, f"閾値ちょうどで照れ隠しが発動しない: {tension2}"

    # nan/inf の提案は破棄される（float()素通り対策）
    store3 = _fresh_store()
    d3, result3, report3 = _distiller(
        store3, {"intimacy": {"value": "nan", "reason": "壊れ"}})
    d3._apply_state("s", result3, report3)
    assert report3["state_dropped"] == ["intimacy"], "nanが破棄されていない"
    assert store3.get_profile("emotion.intimacy") == "0.4", "nanがDBへ漏れた"


# ---- 注入・バックフィル ----

def test_injection_order() -> None:
    cfg = CoreConfig()
    out = context.build_system(
        "PERSONA_MARK",
        [{"type": "fact", "content": "STATIC_MARK", "metadata": {}}],
        [{"type": "fact", "content": "REF_MARK", "metadata": {}}],
        cfg,
        open_threads=[{"question": "THREAD_MARK", "context": None}],
        emotion={"narrative_mood": "MOOD_MARK", "shy": True},
    )
    order = [out.index(m) for m in
             ("PERSONA_MARK", "MOOD_MARK", "STATIC_MARK", "REF_MARK", "THREAD_MARK")]
    assert order == sorted(order), f"注入順序が崩れている: {order}"
    assert "照れ隠し" in out, "shy=True で照れ隠し制約が注入されない"
    assert "会話スタイル（最優先で厳守）" in out.split("THREAD_MARK")[-1], "会話スタイル契約が末尾にない"


def test_backfill_idempotent() -> None:
    store = _fresh_store()
    mid = store.add_memory(type="knowledge", content="宮古島で再会する約束",
                           importance=1.0, metadata={}, source="継承記憶r1.md", pinned=True)
    conn_fake = FakeConnector(["<keywords>宮古島,約束の海,再会</keywords>"] * 2)
    stats = run_backfill(store, conn_fake, store.get_all_pinned())
    assert stats == {"updated": 1, "skipped": 0, "failed": 0}
    mem = store.get(mid)
    assert mem["metadata"]["trigger_keywords"] == ["宮古島", "約束の海", "再会"]
    assert mem["content"] == "宮古島で再会する約束", "contentが書き換わった"
    stats2 = run_backfill(store, conn_fake, store.get_all_pinned())
    assert stats2 == {"updated": 0, "skipped": 1, "failed": 0}, "冪等でない"
    assert len(conn_fake.calls) == 1, "スキップ対象にLLMを呼んだ"


def main() -> None:
    tests = [
        test_decay_halving,
        test_decay_floor,
        test_pinned_no_decay,
        test_metadata_half_life_override,
        test_invalidated_excluded,
        test_trigger_mount,
        test_hot_promotion,
        test_gravity_pipeline,
        test_gravity_time_only,
        test_shy_spike,
        test_injection_order,
        test_backfill_idempotent,
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
