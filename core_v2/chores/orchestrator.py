"""蒸留消化の表口（トリガー配線）。設計書v2 §2.4。

`consume_pending_distillation_jobs()`（core_v2/chores/distillation.py）自体は
「呼べば動く」状態まで実装済みだが、実際に呼び出す経路（①セッション終了時 ②アイドル時
③次回起動時の朝礼）が今まで存在しなかったため、宿題箱に積まれた蒸留ジョブは永遠に
pendingのまま＝長期記憶DBへの書き込みが一切起きなかった
（DECISIONS 2026-07-11「蒸留消化ロジックを実装」の未解決事項#1参照）。

このモジュールがその表口。§2.4「呼ぶタイミングはアプリ層の責務」を実体化する。
Coreクラス自身にconsume呼び出しを取り込まない（Core=判断／消化=LLM発注、という既存の
層分離を守るため。advisorレビュー2026-07-11）。

②アイドル時トリガー（GPU見送り判定・タイマー・1件単位の中断）は`run_idle_digest_chunk`が
担う。GPU番人・見回りタイマー・会話ロックとの調停はGUI側（app/gui_server.py）の責務とし、
ここでは「指定件数だけ消化する」薄い窓口のみを提供する（advisorレビュー2026-07-11: layerの
衛生上、タイマ設定はCoreの判断ツマミ(ThresholdsConfig)と混ぜない）。
"""

from __future__ import annotations

from collections.abc import Callable

from serina.core_v2.chores.chore_box import ChoreBox
from serina.core_v2.chores.distillation import ConsumptionSummary, consume_pending_distillation_jobs
from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.memory.store import MemoryStore
from serina.core_v2.runtime import Core


def run_startup_chores(
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    limit: int | None = None,
) -> ConsumptionSummary:
    """③次回起動時の朝礼: 前回のやり残し(pending)を新しい日の残弾で消化する（§2.4 line230）。

    強制終了・電源断で①(セッション終了時)が走らなかった宿題を回収する唯一の経路。
    アプリ起動直後に1回呼ぶ想定。
    """
    return consume_pending_distillation_jobs(
        chore_box,
        memory_store=memory_store,
        thresholds=thresholds,
        lane_call_fns=lane_call_fns,
        limit=limit,
    )


def run_session_end_chores(
    core: Core,
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    limit: int | None = None,
) -> tuple[list[int], ConsumptionSummary]:
    """①セッション終了時: 端数を宿題箱へ積んでから(Core.end_session)、アプリを閉じる前に消化する
    （§2.4 line228, セッション終了の定義=3トリガーはアプリ層が判定してこの関数を呼ぶ）。

    Core.end_session()は端数flushとCore状態のリセットのみを担う。消化(LLM発注)はCoreの
    外側=ここで行うことで、Core=判断／消化=別関数という層分離を守る
    （advisorレビュー2026-07-11。Core.end_session()の中でconsumeを呼ぶとCoreにLLM呼び出しが
    侵入し、Core単体のスタブテスト(StubBrainのみ・LLM不要)が効かなくなる）。

    limitで区切らない限り、このセッションの端数だけでなく宿題箱に残る全pending(蒸留)を
    消化する(過去セッションの積み残しも含めて回収するのが①の役目)。
    """
    job_ids = core.end_session()
    summary = consume_pending_distillation_jobs(
        chore_box,
        memory_store=memory_store,
        thresholds=thresholds,
        lane_call_fns=lane_call_fns,
        limit=limit,
    )
    return job_ids, summary


def run_idle_digest_chunk(
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    limit: int = 1,
) -> ConsumptionSummary:
    """②会話の合間のアイドル時: 1〜2件ずつ内職する（§2.4 line229）。

    呼び出し側（GUIの見回りスレッド）が「今アイドルか」「GPUは空いているか」
    「会話ロックは空いているか」を判定してから、指定件数だけ呼ぶことを想定する薄いラッパ。
    宿題箱が空なら何もしない（ConsumptionSummaryが空で返る）。
    """
    return consume_pending_distillation_jobs(
        chore_box,
        memory_store=memory_store,
        thresholds=thresholds,
        lane_call_fns=lane_call_fns,
        limit=limit,
    )


def build_default_lane_call_fns(
    gemini_api_key: str | None = None,
) -> dict[str, Callable[[str], str]]:
    """実運用向けlane_call_fns。ローカル車線はAurora(Ollama)、クラウド車線はGemini
    （APIキーがある場合のみ）。§2.4「裏方便の二車線」の実call_fn配線。

    ただし断片ごとの車線振り分け(Coreの個人情報フィルタ)自体は未実装で、現状すべての
    断片がlane="local"固定で積まれる（DECISIONS 2026-07-11）。そのためcloudレーンの
    ジョブは今のところ発生しないが、車線振り分け実装後(MILESTONE次アクション#2)に
    そのまま使えるよう用意しておく。
    """
    from serina.brains.aurora.adapter import AuroraAdapter

    lane_call_fns: dict[str, Callable[[str], str]] = {"local": AuroraAdapter().raw_call}
    if gemini_api_key:
        from serina.brains.gemini.adapter import GeminiAdapter

        lane_call_fns["cloud"] = GeminiAdapter(api_key=gemini_api_key).raw_call
    return lane_call_fns
