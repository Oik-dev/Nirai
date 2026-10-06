"""裏方の表口（トリガー配線）。設計書 §2.4。

呼ぶタイミング（起動時の朝礼・Serina 日界・ターン確定後）はアプリ層（app/gui_server.py）の責務。
ここは、Core の状態と、眠り（core/memory/sleep.py）・人格の見直し（persona_propose.py）・目覚め（core/memory/waking.py）・
会話の要約を結ぶ薄い窓口。眠る → 人格を見直す → 目覚めて今の自分を確かめる、の順に呼ぶのはアプリ層。
Core クラス自身には脳への裏方の発注を持ち込まない（Core＝判断、裏方＝脳への発注、という層の分離）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from mind.brains.ollama.ask_json import asker
from mind.core.chores.persona_propose import ProposeOutcome, run_persona_growth
from mind.core.chores.rolling_summary import TurnSummaryBatchOutcome, update_turn_summaries
from mind.core.memory.sleep import SleepReport, sleep
from mind.core.memory.waking import Waking, wake
from mind.core.protection import ChangeLog, GenerationStore
from mind.core.runtime import Core
from mind.core.state.serina_day import serina_day_id, serina_day_start


def default_call_fn() -> Callable[[str], str]:
    """裏方が使う手元の脳（会話の要約・人格の見直し）。会話と同じローカルの Ollama。"""
    from mind.brains.ollama.adapter import OllamaAdapter
    from mind.core.config import load_thresholds

    thresholds = load_thresholds()
    return OllamaAdapter(
        request_timeout_seconds=thresholds.ollama_request_timeout_seconds,
        num_ctx=thresholds.ollama_num_ctx,
        use_mmap=thresholds.ollama_use_mmap,
    ).raw_call


def run_post_turn_summaries(core: Core, *, call_fn: Callable[[str], str]) -> TurnSummaryBatchOutcome:
    """ターン確定後: fine 更新＋条件付き coarse 更新（§1.5 ④⑦）。"""
    return update_turn_summaries(core.session, call_fn=call_fn, thresholds=core.thresholds)


def _primary_brain(core: Core) -> str:
    return next(e.name for e in core.registry if e.role == "primary") if core.registry else "unknown"


def run_sleep(
    core: Core,
    *,
    now: datetime,
    should_stop: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] | None = None,
) -> SleepReport | None:
    """眠る：今の Serina 日より前の、まだ記憶になっていない会話を本人の脳でページにする。記憶がなければ何もしない。

    ページの芯の数と日記の材料の気持ちの流れは、気持ちの記録（core.feelings）から。
    """
    if core.memory is None:
        return None
    brain = _primary_brain(core)
    kwargs = {"progress": progress} if progress is not None else {}
    return sleep(
        core.memory,
        before=serina_day_start(serina_day_id(now)),
        ask=asker(
            brain,
            num_ctx=core.thresholds.ollama_num_ctx,
            use_mmap=core.thresholds.ollama_use_mmap,
        ),
        persona=core.persona_text,
        brain=brain,
        feelings=core.feelings,
        should_stop=should_stop,
        today=serina_day_id(now),
        **kwargs,
    )


def run_persona_growth_for(
    core: Core,
    *,
    call_fn: Callable[[str], str],
    change_log: ChangeLog,
    generation_store: GenerationStore,
    now: datetime,
    last_propose_at: datetime | None,
    after_reflection: str = "",
) -> ProposeOutcome:
    """眠りのあと: 新しい振り返りから人格の可変ブロックを見直す（1日1回）。"""
    from mind.core.persona_assets import load_persona_assets

    if core.memory is None:
        return ProposeOutcome(asked=False, reason="記憶がない")
    outcome = run_persona_growth(
        memory_dir=core.memory.idea.memory,
        call_fn=call_fn,
        change_log=change_log,
        generation_store=generation_store,
        reflection_limit=core.thresholds.persona_propose_reflection_limit,
        max_retries=core.thresholds.persona_propose_max_retries,
        now=now,
        last_propose_at=last_propose_at,
        after_reflection=after_reflection,
    )
    if outcome.revised:
        assets = load_persona_assets()
        core.persona_text = assets.persona_text
        core.absolute_rules = assets.absolute_rules
    return outcome


def run_waking(core: Core, *, now: datetime) -> Waking | None:
    """眠り終えて人格を見直したあと：新しい日記か振り返りがあれば、本人が今の自分と伝えたいことを書く。"""
    if core.memory is None:
        return None
    brain = _primary_brain(core)
    return wake(
        core.memory.idea.memory,
        persona=core.persona_text,
        ask=asker(
            brain,
            num_ctx=core.thresholds.ollama_num_ctx,
            use_mmap=core.thresholds.ollama_use_mmap,
        ),
        written_by=brain,
        now=now,
    )
