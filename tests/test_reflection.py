"""3b 蒸留（二役リフレクション）の自動アサーションテスト（Ollama不要）"""

from __future__ import annotations

import hashlib
import random
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core import context
from serina.core.config import CoreConfig
from serina.core.reflection import Distiller, create_distiller
from serina.core.reflection_parser import parse_diary, parse_reason_output
from serina.core.runtime import Core
from serina.memory.store import MemoryStore


class FakeEmbedder:
    """同一テキスト→同一ベクトル（決定論的）。異なるテキストはほぼ直交。"""

    def embed(self, text: str) -> list[float]:
        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(1024)]


class FakeConnector:
    """缶詰応答を順に返す ChatConnector。呼び出しを記録する。"""

    def __init__(self, responses: list[Any], name: str = "", call_log: list[str] | None = None) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.name = name
        self.call_log = call_log if call_log is not None else []

    def chat(self, system: str, messages: list[dict[str, str]], options=None) -> str:
        self.calls.append({"system": system, "messages": messages, "options": options})
        self.call_log.append(self.name)
        if not self.responses:
            raise RuntimeError("FakeConnector: 応答が尽きました")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class FakeSkill:
    name = "fake"
    last_ctx = None

    def can_handle(self, user_input: str) -> bool:
        return True

    def run(self, ctx) -> str:
        FakeSkill.last_ctx = ctx
        return "了解です、マスター"


def _fresh_store() -> MemoryStore:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    return MemoryStore(FakeEmbedder(), db_path=tmp)


def _make_pending_session(store: MemoryStore, sid: str = "s_test_01") -> str:
    store.create_session(sid)
    store.add_history(sid, "user", "京都に引っ越したよ。最近は紅茶ばかり飲んでる")
    store.add_history(sid, "assistant", "京都、いいですね。紅茶派になったこと覚えておきます")
    store.set_session_status(sid, "pending")
    return sid


REASON_XML = """<new_facts>
<fact keywords="京都,引っ越し,新居">マスターは京都に引っ越した</fact>
<fact keywords="紅茶,アールグレイ,飲み物">マスターは紅茶派になった</fact>
</new_facts>
<state_update>
<param name="intimacy" value="0.55"/>打ち明け話が増えた
<param name="tension" value="0.25"/>安定している
<param name="energy_level" value="0.60"/>引っ越しを終えて上向き
<narrative_mood>新生活の話を聞けて嬉しい</narrative_mood>
</state_update>
<open_threads>
<thread context="新居の話">新しい部屋には慣れた？</thread>
</open_threads>"""

DIARY_XML = "<diary>今日はマスターの引っ越しの話を聞いた。新しい街の話をする声が弾んでいて、私まで嬉しくなった。</diary>"


def _make_distiller(store, reason_responses, aurora_responses, cancel_event=None):
    cfg = CoreConfig()
    call_log: list[str] = []
    return Distiller(
        store, "あなたはセリナです", cfg,
        FakeConnector(reason_responses, "reason", call_log),
        FakeConnector(aurora_responses, "aurora", call_log),
        cancel_event,
    ), cfg


# ---- 1. パーサ堅牢性 ----

def test_parser_robustness() -> None:
    broken = """<diary>閉じタグの無い日記本文
<state_update>
<param name="intimacy" value="0.7"/>根拠テキスト
<param name="tension" value="0.4"/>途中で切れ"""
    diary = parse_diary(broken)
    assert diary == "閉じタグの無い日記本文", f"日記サルベージ失敗: {diary!r}"
    r = parse_reason_output(broken)
    assert r.state["intimacy"]["value"] == "0.7"
    assert r.state["tension"]["value"] == "0.4"
    assert r.state["intimacy"]["reason"] == "根拠テキスト"


def test_parser_fact_keywords() -> None:
    xml = """<new_facts>
<fact keywords="京都, 引っ越し、新居">マスターは京都在住</fact>
<fact>属性の無い事実</fact>
</new_facts>"""
    r = parse_reason_output(xml)
    assert r.facts[0]["keywords"] == ["京都", "引っ越し", "新居"], r.facts[0]["keywords"]
    assert r.facts[1]["keywords"] == []
    assert r.facts[1]["content"] == "属性の無い事実"


# ---- 2. 蒸留本体 ----

def test_distill_happy_path() -> None:
    store = _fresh_store()
    sid = _make_pending_session(store)
    distiller, _ = _make_distiller(store, [REASON_XML], [DIARY_XML])
    report = distiller.distill_session(sid)

    assert report["status"] == "ok", report["error"]
    assert distiller.reason.call_log == ["reason", "aurora"], "二役の呼び出し順が想定外"
    assert report["facts_saved"] == 2
    diaries = store.list_memories_by_type("diary")
    facts = store.list_memories_by_type("fact")
    assert len(diaries) == 1 and diaries[0]["source"] == f"session:{sid}"
    assert len(facts) == 2
    assert facts[0]["metadata"]["trigger_keywords"], "trigger_keywordsが保存されていない"
    assert store.get_session_history(sid) == [], "historyが残っている"
    assert store.count_archived(sid) == 2, "archived_historyへ移動していない"
    sessions = store.list_sessions_by_status("distilled")
    assert [s["id"] for s in sessions] == [sid]
    assert store.get_profile("emotion.intimacy") == "0.55"
    assert store.get_profile("emotion.narrative_mood") == "新生活の話を聞けて嬉しい"
    assert report["threads_added"] == 1


def test_distill_failure_keeps_pending() -> None:
    store = _fresh_store()
    sid = _make_pending_session(store)
    before = store.count_memories()
    distiller, _ = _make_distiller(store, [RuntimeError("理性エンジン死亡")], [DIARY_XML])
    report = distiller.distill_session(sid)
    assert report["status"] == "error"
    assert store.count_memories() == before, "失敗時に書き込みが発生した"
    assert len(store.get_session_history(sid)) == 2, "失敗時にアーカイブされた"
    assert [s["id"] for s in store.list_sessions_by_status("pending")] == [sid]


def test_diary_missing_fails() -> None:
    store = _fresh_store()
    sid = _make_pending_session(store)
    distiller, _ = _make_distiller(store, [REASON_XML], ["今日はいい日でした（タグ無し）"])
    report = distiller.distill_session(sid)
    assert report["status"] == "error"
    assert "diary" in report["error"]
    assert [s["id"] for s in store.list_sessions_by_status("pending")] == [sid]
    assert store.list_memories_by_type("diary") == []


def test_state_clamp_and_audit() -> None:
    xml = """<state_update>
<param name="intimacy" value="1.7"/>過剰な提案
<param name="tension" value="abc"/>壊れた値
</state_update>"""
    # value="abc" は数値正規表現に一致せずパーサ段階で落ちるため、Distiller側の破棄経路は
    # 手動で state を仕込んで確認する
    store = _fresh_store()
    sid = _make_pending_session(store)
    distiller, _ = _make_distiller(store, [xml], [DIARY_XML])
    report = distiller.distill_session(sid)
    assert report["status"] == "ok"
    assert store.get_profile("emotion.intimacy") == "1.0", "クランプされていない"
    conn = store._conn()
    try:
        rows = conn.execute("SELECT * FROM state_audit WHERE param = 'intimacy'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1 and rows[0]["reason"] == "過剰な提案"
    assert rows[0]["old_value"] == "0.4", "初期値がベースラインでない"

    # 破棄経路（float変換不能）
    store2 = _fresh_store()
    sid2 = _make_pending_session(store2)
    distiller2, _ = _make_distiller(store2, [REASON_XML], [DIARY_XML])
    result_stub = SimpleNamespace(
        state={"intimacy": {"value": "abc", "reason": "壊れ"}},
        narrative_mood=None,
    )
    report2 = {"state_changes": [], "state_dropped": []}
    distiller2._apply_state(sid2, result_stub, report2)
    assert report2["state_dropped"] == ["intimacy"]
    assert store2.get_profile("emotion.intimacy") is None, "破棄値が書き込まれた"


def test_dedup_rerun() -> None:
    store = _fresh_store()
    sid = _make_pending_session(store)
    distiller, _ = _make_distiller(store, [REASON_XML, REASON_XML], [DIARY_XML, DIARY_XML])

    # 1回目: 書き込み完了後・アーカイブ直前に失敗させる（部分失敗の再現）
    original = store.archive_session_history
    call_state = {"raised": False}

    def flaky(session_id: str) -> int:
        if not call_state["raised"]:
            call_state["raised"] = True
            raise RuntimeError("アーカイブ直前に停電")
        return original(session_id)

    store.archive_session_history = flaky  # type: ignore[method-assign]
    report1 = distiller.distill_session(sid)
    assert report1["status"] == "error"
    assert [s["id"] for s in store.list_sessions_by_status("pending")] == [sid]

    # 2回目（再試行）: dedup 0.92 が二重登録を防ぐ
    report2 = distiller.distill_session(sid)
    assert report2["status"] == "ok", report2["error"]
    assert report2["facts_saved"] == 0 and report2["facts_dup"] == 2, report2
    assert len(store.list_memories_by_type("diary")) == 1, "日記が二重登録された"
    assert len(store.list_memories_by_type("fact")) == 2, "事実が二重登録された"


# ---- 3. open_threads ----

def test_open_thread_lifecycle() -> None:
    store = _fresh_store()
    t1 = store.add_open_thread("面接どうだった？", "面接の話", "s1", 14)
    assert t1 is not None
    # セッションを跨いだ同一質問は open 中はスキップ
    assert store.add_open_thread("面接どうだった？", None, "s2", 14) is None
    t2 = store.add_open_thread("風邪は治った？", None, "s1", 14)
    # 期限切れは list から除外
    t3 = store.add_open_thread("昔の話どうなった？", None, "s1", -1)
    listed = store.list_open_threads(10)
    ids = {t["id"] for t in listed}
    assert t1 in ids and t2 in ids and t3 not in ids, "期限切れが除外されていない"
    # resolve → 再登録可能
    assert store.set_open_thread_status(t1, "resolved")
    assert store.add_open_thread("面接どうだった？", None, "s3", 14) is not None


def test_open_thread_injection() -> None:
    store = _fresh_store()
    store.add_open_thread("新しい部屋には慣れた？", "引っ越し", "s0", 14)
    cfg = CoreConfig()
    core = Core(store, "あなたはセリナです", [FakeSkill()], config=cfg)

    sid = "s_inject"
    store.create_session(sid)
    core.turn(sid, "おはよう")  # 履歴0行 → 注入される
    assert "気になっていること" in FakeSkill.last_ctx.system_prompt
    assert "新しい部屋には慣れた？" in FakeSkill.last_ctx.system_prompt

    for i in range(4):  # 履歴を閾値(6)以上に増やす
        store.add_history(sid, "user", f"話題{i}")
    core.turn(sid, "続きの話")
    assert "気になっていること" not in FakeSkill.last_ctx.system_prompt, "冒頭以外でも注入された"


# ---- 4. 事実の失効（バイテンポラル） ----

def test_fact_invalidate() -> None:
    store = _fresh_store()
    old_id = store.add_memory(
        type="fact", content="マスターは大阪に住んでいる", importance=0.7,
        metadata={"trigger_keywords": ["大阪", "住まい"]}, source="session:s_old",
    )
    canon_id = store.add_memory(
        type="fact", content="正典の約束ごと", importance=1.0,
        metadata={}, source="継承記憶r1.md", pinned=True,
    )
    sid = _make_pending_session(store)
    xml = f"""<fact_updates>
<update target_id="{old_id}" reason="京都へ転居と発言">マスターは京都に住んでいる</update>
<update target_id="{canon_id}" reason="矛盾に見えた">正典を書き換えたい</update>
</fact_updates>"""
    distiller, _ = _make_distiller(store, [xml], [DIARY_XML])
    report = distiller.distill_session(sid)
    assert report["status"] == "ok"

    old = store.get(old_id)
    assert old["content"] == "マスターは大阪に住んでいる", "原文が書き換えられた"
    assert old["metadata"]["invalidated_at"], "invalidated_atが無い"
    assert old["metadata"]["superseded_by"], "superseded_byが無い"
    assert report["facts_invalidated"] == [old_id]

    canon = store.get(canon_id)
    meta = canon.get("metadata") or {}
    assert not meta.get("invalidated_at"), "正典が自動失効された（正典保護違反）"
    assert report["canon_holds"] == [canon_id]


# ---- 5. 孤児バックフィル・キャンセル・配線 ----

def test_orphan_backfill() -> None:
    store = _fresh_store()
    store.add_history("repl_orphan1", "user", "昔の発言")
    store.add_history("smoke_orphan2", "user", "テスト発言")
    orphans = store.backfill_orphan_sessions()
    assert set(orphans) == {"repl_orphan1", "smoke_orphan2"}
    pending = {s["id"] for s in store.list_sessions_by_status("pending")}
    assert pending == {"repl_orphan1", "smoke_orphan2"}
    assert store.backfill_orphan_sessions() == [], "二重登録された"


def test_cancel_checkpoint() -> None:
    import threading

    store = _fresh_store()
    sid = _make_pending_session(store)
    ev = threading.Event()
    ev.set()
    before = store.count_memories()
    distiller, _ = _make_distiller(store, [REASON_XML], [DIARY_XML], cancel_event=ev)
    report = distiller.distill_session(sid)
    assert report["status"] == "cancelled"
    assert store.count_memories() == before, "キャンセル後に書き込みが発生"
    assert [s["id"] for s in store.list_sessions_by_status("pending")] == [sid]


def test_create_distiller_keep_alive() -> None:
    store = _fresh_store()
    core = SimpleNamespace(store=store, persona="p", config=CoreConfig())
    distiller = create_distiller(core)
    assert distiller.reason.keep_alive == 0, "理性エンジンに keep_alive=0 が無い"
    assert distiller.aurora.keep_alive is None, "Aurora側に keep_alive が設定されている"
    assert distiller.reason.model == CoreConfig().reason_model


def main() -> None:
    tests = [
        test_parser_robustness,
        test_parser_fact_keywords,
        test_distill_happy_path,
        test_distill_failure_keeps_pending,
        test_diary_missing_fails,
        test_state_clamp_and_audit,
        test_dedup_rerun,
        test_open_thread_lifecycle,
        test_open_thread_injection,
        test_fact_invalidate,
        test_orphan_backfill,
        test_cancel_checkpoint,
        test_create_distiller_keep_alive,
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
