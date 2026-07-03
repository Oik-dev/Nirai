"""4b 行動化（差分想起・独り言・スレッド期限）の自動アサーションテスト（Ollama不要）"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.config import CoreConfig
from serina.core.reflection import generate_idle_thought
from serina.core.runtime import Core
from serina.memory.store import MemoryStore
from serina.tests.test_reflection import FakeConnector, FakeEmbedder, FakeSkill


class FixedRng:
    """random() が固定値を返すスタブ（差分想起ゲートの決定論的検証用）"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


def _fresh_store() -> MemoryStore:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    return MemoryStore(FakeEmbedder(), db_path=tmp)


def _core(store, rng) -> Core:
    return Core(store, "あなたはセリナです", [FakeSkill()], config=CoreConfig(), rng=rng)


def _add_growth_note(store, content: str) -> int:
    return store.add_memory(type="growth_note", content=content, importance=0.5,
                            metadata={"used": False}, source="consolidation")


# ---- 差分想起（設計書§4: 乱数固定でp_growth制御どおり・used後は再注入されない） ----

def test_growth_gate() -> None:
    store = _fresh_store()
    nid = _add_growth_note(store, "前は作業の話ばかり→最近は体調の話が先")
    store.create_session("s1")

    # 乱数 0.9 > p_growth 0.25 → 注入されない
    _core(store, FixedRng(0.9)).turn("s1", "おはよう")
    assert "気づいた変化" not in FakeSkill.last_ctx.system_prompt, "確率を無視して注入された"
    assert store.get(nid)["metadata"]["used"] is False, "非注入なのにusedが立った"

    # 乱数 0.1 < 0.25 → 注入される＋used化
    store2 = _fresh_store()
    nid2 = _add_growth_note(store2, "前は敬語だけ→最近は砕けた口調も混ざる")
    store2.create_session("s1")
    _core(store2, FixedRng(0.1)).turn("s1", "おはよう")
    assert "気づいた変化" in FakeSkill.last_ctx.system_prompt
    assert "砕けた口調" in FakeSkill.last_ctx.system_prompt
    assert store2.get(nid2)["metadata"]["used"] is True, "注入後にusedが立たない"

    # used済みは再注入されない（検索想起は妨げない=memoriesには残る）
    store2.create_session("s2")
    _core(store2, FixedRng(0.1)).turn("s2", "こんにちは")
    assert "気づいた変化" not in FakeSkill.last_ctx.system_prompt, "used済みが再注入された"
    assert store2.get(nid2) is not None


# ---- 独り言（設計書§4: 種ゼロで生成しない・使用後クリア） ----

def test_idle_thought_guard_and_clear() -> None:
    cfg = CoreConfig()
    # 種記憶ゼロ → 生成しない（LLMも呼ばない）
    store = _fresh_store()
    conn_fake = FakeConnector(["<idle>考えごと</idle>"])
    assert generate_idle_thought(store, "p", cfg, conn_fake) is None
    assert len(conn_fake.calls) == 0, "種ゼロでLLMを呼んだ（捏造ガード違反）"
    assert store.get_profile("idle_thought") is None

    # 日記あり → 生成して保存
    store.add_memory(type="diary", content="今日は引っ越しの話を聞いた", importance=0.55,
                     metadata={}, source="session:s")
    idle = generate_idle_thought(store, "p", cfg, FakeConnector(["<idle>新居の窓からの景色、見てみたいな</idle>"]))
    assert idle == "新居の窓からの景色、見てみたいな"
    assert store.get_profile("idle_thought") == idle

    # セッション冒頭で注入され、使用後は必ずクリア（再放送は幻滅イベント）
    store.create_session("s1")
    _core(store, FixedRng(0.9)).turn("s1", "ただいま")
    assert "いない間に考えていたこと" in FakeSkill.last_ctx.system_prompt
    assert "新居の窓" in FakeSkill.last_ctx.system_prompt
    assert store.get_profile("idle_thought") == "", "使用後にクリアされていない"
    _core(store, FixedRng(0.9)).turn("s1", "続き")
    assert "いない間に考えていたこと" not in FakeSkill.last_ctx.system_prompt, "独り言が再放送された"


# ---- open_thread 期限（設計書§4: 15日経過→expired化・注入されない） ----

def test_thread_expiry() -> None:
    store = _fresh_store()
    old = store.add_open_thread("15日前の質問？", None, "s0", ttl_days=-1)  # 既に期限切れ
    fresh = store.add_open_thread("昨日の質問？", None, "s0", ttl_days=14)
    expired = store.expire_open_threads()
    assert expired == 1, f"expired化の件数が想定外: {expired}"
    listed = {t["id"] for t in store.list_open_threads(10)}
    assert fresh in listed and old not in listed
    conn = store._conn()
    try:
        row = conn.execute("SELECT status FROM open_threads WHERE id = ?", (old,)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "expired"


class FailSkill:
    name = "fail"

    def can_handle(self, user_input: str) -> bool:
        return True

    def run(self, ctx) -> str:
        raise RuntimeError("LLM死亡")


def test_no_consume_on_failure() -> None:
    """quality-reviewer指摘: 応答失敗時に一度きり演出（used化・クリア）を空撃ちしない"""
    store = _fresh_store()
    nid = _add_growth_note(store, "変化メモ")
    store.set_profile("idle_thought", "考えごと")
    store.create_session("s1")
    core = Core(store, "p", [FailSkill()], config=CoreConfig(), rng=FixedRng(0.1))
    try:
        core.turn("s1", "おはよう")
        raise AssertionError("FailSkillが例外を投げていない")
    except RuntimeError:
        pass
    assert store.get(nid)["metadata"]["used"] is False, "失敗ターンでusedが立った（空撃ち）"
    assert store.get_profile("idle_thought") == "考えごと", "失敗ターンで独り言が消えた（空撃ち）"


def main() -> None:
    tests = [test_growth_gate, test_idle_thought_guard_and_clear, test_thread_expiry,
             test_no_consume_on_failure]
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
