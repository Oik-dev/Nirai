"""日記生成フローのテスト。設計書 §4.5。

core/chores/diary.py の材料組み立て・書き手分岐・生成保存と、
core/state/emotion.py の気分軌跡ログを検査する。LLM不要（call_fnをスタブ化）。

2026-07-18改訂（合意台帳§9.3）: 裏方便のcloud車線は永久退役し、`determine_writer_lane`は
入力（材料の機微性・保存grade）に関わらず常にlocal固定になった。旧テストは「保存grade
ではなくRoutingRules.is_sensitive()をその場で再評価し、非機微ならcloudへ倒れる」ことを
検証していたが、cloud分岐自体が消滅したため、代わりに「入力の中身が何であれ常にlocalへ
収束する」ことを検証する（Gemini退役後に発覚した「非機微な日の日記が生成されなくなる
バグ」の再発防止・DECISIONS参照）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.diary import (
    EPISODIC_MEMORY_TYPE,
    DIARY_PROTECTION_GRADE,
    DIARY_SENSITIVITY_GRADE,
    DiaryMaterial,
    determine_writer_lane,
    gather_diary_material,
    generate_and_save_diary,
)
from serina.core.chores.idle_policy import (
    should_generate_diary,
    should_generate_diary_at_startup,
    should_retry_diary_after_empty,
)
from serina.core.chores.orchestrator import run_diary_generation
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.protection import ChangeLog
from serina.core.memory.store import MemoryRecord, MemoryStore
from serina.core.state.emotion import EmotionState
from serina.core.state.routing_rules import RoutingRules
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def _fake_embedder() -> OllamaEmbedder:
    def call_fn(model: str, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0] if "散歩" in text else [0.0, 0.0, 0.0, 1.0]

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    return MemoryStore(
        str(Path(tempfile.mkdtemp()) / "test_memory.db"), embedder=_fake_embedder(), vector_dim=4,
    )


def _fresh_change_log() -> ChangeLog:
    return ChangeLog(Path(tempfile.mkdtemp()) / "changes.jsonl")


def _record(content: str, *, sensitivity_grade: int = 2) -> MemoryRecord:
    return MemoryRecord(
        id=1,
        type="fact",
        content=content,
        importance=0.5,
        sensitivity_grade=sensitivity_grade,
        protection_grade="B",
        cosmetic_version=None,
        created_at="2026-01-01T00:00:00+00:00",
        last_accessed="2026-01-01T00:00:00+00:00",
        sensitivity_assessed=False,
    )


# --- 書き手分岐（本命） -------------------------------------------------


def test_determine_writer_lane_non_sensitive_material_goes_local() -> None:
    """§9.3: 機微パターンを含まない材料でもlocal固定（旧: 上位モデルcloudへ）。"""
    material = DiaryMaterial(
        memories=[_record("散歩が好きだという話", sensitivity_grade=2)],
        mood_summary="喜び: 開始0.10→終了0.30",
    )
    assert determine_writer_lane(material, routing_rules=RoutingRules()) == "local"


def test_determine_writer_lane_sensitive_material_goes_local() -> None:
    """機微パターン（電話番号）を含む材料もlocalへ（cloud車線自体が存在しない）。"""
    material = DiaryMaterial(
        memories=[_record("携帯は09012345678だと教えてもらった", sensitivity_grade=2)],
        mood_summary="信頼: 開始0.20→終了0.40",
    )
    assert determine_writer_lane(material, routing_rules=RoutingRules()) == "local"


def test_determine_writer_lane_ignores_stored_grade_and_content_entirely() -> None:
    """§9.3の回帰防止: Gemini退役後に「非機微な日の日記が生成されなくなるバグ」が
    あったため、材料の中身・保存gradeに関わらずlocalへ収束することを固定する。
    """
    material = DiaryMaterial(
        memories=[_record("今日は天気が良かった", sensitivity_grade=2)],
        mood_summary="",
    )
    assert determine_writer_lane(material, routing_rules=RoutingRules()) == "local"


# --- 材料組み立て --------------------------------------------------------


def test_gather_diary_material_collects_todays_memories_only() -> None:
    store = _fresh_store()
    store.add_memory("昨日の記憶", type="fact")
    since = "2026-07-12T00:00:00+00:00"
    # add_memoryは現在時刻で書くため、当日分として拾われることを確認する
    today_id = store.add_memory("今日の記憶", type="fact")

    material = gather_diary_material(store, since_iso=since, mood_summary="")

    ids = [m.id for m in material.memories]
    assert today_id in ids


def test_gather_diary_material_excludes_diary_type_itself() -> None:
    store = _fresh_store()
    store.add_memory("前回の日記本文", type=EPISODIC_MEMORY_TYPE, protection_grade="A")
    fact_id = store.add_memory("今日の出来事", type="fact")
    since = "2020-01-01T00:00:00+00:00"

    material = gather_diary_material(store, since_iso=since, mood_summary="")

    types = {m.type for m in material.memories}
    assert EPISODIC_MEMORY_TYPE not in types
    assert fact_id in [m.id for m in material.memories]


def test_diary_material_empty_when_nothing_happened() -> None:
    assert DiaryMaterial(memories=[], mood_summary="").is_empty() is True
    assert DiaryMaterial(memories=[], mood_summary="  ").is_empty() is True
    assert DiaryMaterial(memories=[_record("何か")], mood_summary="").is_empty() is False


# --- 生成・保存 -----------------------------------------------------------


def test_generate_and_save_diary_empty_material_not_generated() -> None:
    store = _fresh_store()
    outcome = generate_and_save_diary(
        store,
        material=DiaryMaterial(memories=[], mood_summary=""),
        routing_rules=RoutingRules(),
        lane_call_fns={"local": lambda p: "本文"},
        change_log=_fresh_change_log(),
    )
    assert outcome.generated is False
    assert outcome.reason == "材料なし"


def test_generate_and_save_diary_success_saves_as_grade_a() -> None:
    store = _fresh_store()
    material = DiaryMaterial(
        memories=[_record("散歩が好きだという話")], mood_summary="喜び: 開始0.1→終了0.3",
    )
    change_log = _fresh_change_log()

    outcome = generate_and_save_diary(
        store,
        material=material,
        routing_rules=RoutingRules(),
        lane_call_fns={"local": lambda p: "今日は良い散歩日和だった。"},
        change_log=change_log,
    )

    assert outcome.generated is True
    assert outcome.lane == "local"
    saved = [r for r in store.list_by_type(EPISODIC_MEMORY_TYPE) if r.id == outcome.memory_id]
    assert len(saved) == 1
    assert saved[0].protection_grade == DIARY_PROTECTION_GRADE
    assert saved[0].sensitivity_grade == DIARY_SENSITIVITY_GRADE
    assert saved[0].content == "今日は良い散歩日和だった。"
    reports = change_log.read_all()
    assert any(r.action == "日記生成" and r.target_id == outcome.memory_id for r in reports)


def test_generate_and_save_diary_llm_failure_does_not_write() -> None:
    store = _fresh_store()
    material = DiaryMaterial(memories=[_record("散歩が好き")], mood_summary="")

    def failing_call_fn(prompt: str) -> str:
        raise RuntimeError("接続失敗")

    outcome = generate_and_save_diary(
        store,
        material=material,
        routing_rules=RoutingRules(),
        lane_call_fns={"local": failing_call_fn},
        change_log=_fresh_change_log(),
    )
    assert outcome.generated is False
    assert outcome.reason == "LLM呼び出し失敗"
    assert store.list_by_type(EPISODIC_MEMORY_TYPE) == []


def test_generate_and_save_diary_missing_lane_call_fn_not_generated() -> None:
    """§9.3: localのcall_fnすら無ければ（Ollama未起動等）発注せず見送る。"""
    store = _fresh_store()
    material = DiaryMaterial(memories=[_record("散歩が好き")], mood_summary="")

    outcome = generate_and_save_diary(
        store,
        material=material,
        routing_rules=RoutingRules(),
        lane_call_fns={},  # local用call_fnが無い
        change_log=_fresh_change_log(),
    )
    assert outcome.generated is False
    assert outcome.lane == "local"
    assert "call_fn未設定" in outcome.reason


def test_generate_and_save_diary_sensitive_material_also_routes_to_local() -> None:
    """機微を含む材料でも(そうでなくても)localの1車線のみ。"""
    store = _fresh_store()
    material = DiaryMaterial(
        memories=[_record("携帯は09012345678")], mood_summary="",
    )
    calls = {}

    def local_call_fn(prompt: str) -> str:
        calls["lane"] = "local"
        return "今日は電話の話をした。"

    outcome = generate_and_save_diary(
        store,
        material=material,
        routing_rules=RoutingRules(),
        lane_call_fns={"local": local_call_fn},
        change_log=_fresh_change_log(),
    )
    assert outcome.generated is True
    assert outcome.lane == "local"
    assert calls.get("lane") == "local"


# --- 気分の軌跡（EmotionState） -------------------------------------------


def test_emotion_trajectory_accumulates_across_mood_updates() -> None:
    emotion = EmotionState()
    emotion.apply_mood_delta({"喜び": 0.1}, max_delta_per_turn=0.5)
    emotion.apply_mood_delta({"喜び": 0.1}, max_delta_per_turn=0.5)
    assert len(emotion.mood_trajectory) == 2


def test_emotion_summarize_trajectory_reports_start_end_min_max() -> None:
    emotion = EmotionState()
    emotion.apply_mood_delta({"喜び": 0.3}, max_delta_per_turn=0.5)
    emotion.apply_mood_delta({"喜び": -0.1}, max_delta_per_turn=0.5)
    summary = emotion.summarize_trajectory()
    assert "喜び" in summary
    assert "開始0.30" in summary
    assert "終了0.20" in summary


def test_emotion_summarize_trajectory_empty_when_no_updates() -> None:
    """空文字を返す（プレースホルダを返すとDiaryMaterial.is_emptyが機能しなくなるため。
    表示側の「記録なし」フォールバックはbuild_diary_prompt側で担う）。"""
    emotion = EmotionState()
    assert emotion.summarize_trajectory() == ""


def test_emotion_clear_trajectory_resets() -> None:
    emotion = EmotionState()
    emotion.apply_mood_delta({"喜び": 0.1}, max_delta_per_turn=0.5)
    emotion.clear_trajectory()
    assert emotion.mood_trajectory == []


# --- 夜間放出トリガーの判定（idle_policy） --------------------------------


def test_should_generate_diary_false_when_session_not_ended() -> None:
    now = datetime.now(timezone.utc)
    assert should_generate_diary(
        now=now, last_diary_at=now - timedelta(hours=10),
        session_ended=False, diary_min_gap_seconds=21600,
    ) is False


def test_should_generate_diary_false_when_gap_too_short() -> None:
    now = datetime.now(timezone.utc)
    assert should_generate_diary(
        now=now, last_diary_at=now - timedelta(hours=1),
        session_ended=True, diary_min_gap_seconds=21600,
    ) is False


def test_should_generate_diary_true_when_ended_and_gap_elapsed() -> None:
    now = datetime.now(timezone.utc)
    assert should_generate_diary(
        now=now, last_diary_at=now - timedelta(hours=7),
        session_ended=True, diary_min_gap_seconds=21600,
    ) is True


def test_should_retry_diary_after_empty_true_when_never_skipped() -> None:
    now = datetime.now(timezone.utc)
    assert should_retry_diary_after_empty(
        now=now, last_empty_skip_at=None, empty_retry_seconds=3600,
    ) is True


def test_should_retry_diary_after_empty_false_within_cooldown() -> None:
    now = datetime.now(timezone.utc)
    assert should_retry_diary_after_empty(
        now=now,
        last_empty_skip_at=now - timedelta(minutes=10),
        empty_retry_seconds=3600,
    ) is False


def test_should_retry_diary_after_empty_true_after_cooldown() -> None:
    now = datetime.now(timezone.utc)
    assert should_retry_diary_after_empty(
        now=now,
        last_empty_skip_at=now - timedelta(hours=2),
        empty_retry_seconds=3600,
    ) is True


# --- 朝礼トリガーの判定（§4.5①、2026-07-12改訂の主経路） -----------------


def test_should_generate_diary_at_startup_true_when_last_diary_was_yesterday_or_earlier() -> None:
    """Serina 日が変わっていれば朝礼で日記を書く。"""
    jst = ZoneInfo("Asia/Tokyo")
    now = datetime(2026, 7, 22, 8, 0, tzinfo=jst).astimezone(timezone.utc)
    last_diary_at = datetime(2026, 7, 21, 10, 0, tzinfo=jst).astimezone(timezone.utc)
    assert should_generate_diary_at_startup(now=now, last_diary_at=last_diary_at, boundary_hour=7) is True


def test_should_generate_diary_at_startup_false_when_already_written_today() -> None:
    """同一 Serina 日なら朝礼では書かない。"""
    jst = ZoneInfo("Asia/Tokyo")
    now = datetime(2026, 7, 22, 10, 0, tzinfo=jst).astimezone(timezone.utc)
    last_diary_at = datetime(2026, 7, 22, 8, 0, tzinfo=jst).astimezone(timezone.utc)
    assert should_generate_diary_at_startup(now=now, last_diary_at=last_diary_at, boundary_hour=7) is False


def test_should_generate_diary_at_startup_true_across_7am_boundary() -> None:
    """06:59 に書いた日記は 07:30 時点で別 Serina 日扱い。"""
    jst = ZoneInfo("Asia/Tokyo")
    last_diary_at = datetime(2026, 7, 22, 6, 59, tzinfo=jst).astimezone(timezone.utc)
    now = datetime(2026, 7, 22, 7, 30, tzinfo=jst).astimezone(timezone.utc)
    assert should_generate_diary_at_startup(now=now, last_diary_at=last_diary_at, boundary_hour=7) is True


# --- run_diary_generation 統合（serina-code-reviewer 2026-07-12 Important指摘の再発防止） ---


class _Core:
    def __init__(self, memory_store: MemoryStore) -> None:
        self.memory_store = memory_store
        self.routing_rules = RoutingRules()
        self.emotion = EmotionState()


def test_run_diary_generation_quiet_day_does_not_call_llm_or_write() -> None:
    """記憶0件・気分変化0の静かな日は、summarize_trajectoryの空文字→is_empty()経由で
    LLMを一切呼ばずに見送られる（プレースホルダ文字列によるfalse greenの再発防止）。
    """
    store = _fresh_store()
    core = _Core(store)
    calls = []

    def spy_call_fn(prompt: str) -> str:
        calls.append(prompt)
        return "呼ばれてはいけない"

    outcome = run_diary_generation(
        core,
        since_iso="2026-07-12T00:00:00+00:00",
        routing_rules=core.routing_rules,
        lane_call_fns={"local": spy_call_fn},
        change_log=_fresh_change_log(),
    )
    assert outcome.generated is False
    assert outcome.reason == "材料なし"
    assert calls == []
    assert store.list_by_type(EPISODIC_MEMORY_TYPE) == []


def test_run_diary_generation_llm_failure_preserves_trajectory_for_retry() -> None:
    """LLM失敗時は気分軌跡をclearしない（次回の夜間放出機会に材料を持ち越すため）。"""
    store = _fresh_store()
    store.add_memory("散歩が好きだという話", type="fact")
    core = _Core(store)
    core.emotion.apply_mood_delta({"喜び": 0.2}, max_delta_per_turn=0.5)

    def failing_call_fn(prompt: str) -> str:
        raise RuntimeError("接続失敗")

    outcome = run_diary_generation(
        core,
        since_iso="2020-01-01T00:00:00+00:00",
        routing_rules=core.routing_rules,
        lane_call_fns={"local": failing_call_fn},
        change_log=_fresh_change_log(),
    )
    assert outcome.generated is False
    assert core.emotion.mood_trajectory != []  # クリアされていない=次回に持ち越せる


def test_run_diary_generation_success_clears_trajectory() -> None:
    store = _fresh_store()
    store.add_memory("散歩が好きだという話", type="fact")
    core = _Core(store)
    core.emotion.apply_mood_delta({"喜び": 0.2}, max_delta_per_turn=0.5)

    outcome = run_diary_generation(
        core,
        since_iso="2020-01-01T00:00:00+00:00",
        routing_rules=core.routing_rules,
        lane_call_fns={"local": lambda p: "今日は良い日だった。"},
        change_log=_fresh_change_log(),
    )
    assert outcome.generated is True
    assert core.emotion.mood_trajectory == []
