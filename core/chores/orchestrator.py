"""蒸留消化の表口（トリガー配線）。設計書 §2.4。

`consume_pending_distillation_jobs()`（core/chores/distillation.py）自体は
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

from serina.core.chores.chore_box import ChoreBox
from serina.core.chores.diary import DiaryOutcome, gather_diary_material, generate_and_save_diary
from serina.core.chores.distillation import ConsumptionSummary, consume_pending_distillation_jobs
from serina.core.chores.rolling_summary import SummaryUpdateOutcome, update_rolling_summary
from serina.core.chores.sensitivity_assessment import (
    AssessmentBatchSummary,
    run_sensitivity_assessment_chunk,
)
from serina.core.config import ThresholdsConfig
from serina.core.memory.protection import ChangeLog
from serina.core.memory.store import MemoryStore
from serina.core.routing.quota_ledger import QuotaLedger, QuotaSpec
from serina.core.routing.registry import BrainEntry
from serina.core.runtime import Core
from serina.core.state.routing_rules import RoutingRules

# 裏方便(蒸留・日記)のクラウド発注が共有する残弾台帳の対象Brain名。会話用の一次Brain
# （config/brains.toml "gemini_flash_lite"）と同一名にすることで、§3.3「Gemini の余り弾」＝
# 会話が使い切らなかった同じ日次枠、を実現する（2026-07-12決定）。
CLOUD_CHORE_BRAIN_NAME = "gemini_flash_lite"


def build_cloud_quota_spec(
    registry: list[BrainEntry], *, name: str = CLOUD_CHORE_BRAIN_NAME,
) -> QuotaSpec | None:
    """登録簿からQuotaSpecを組み立てる。該当Brainが登録簿に無ければNone
    （呼び出し側はNoneならquotaゲートをスキップし、cloud車線を無制限扱いにせず
    素通しはしない——`consume_pending_distillation_jobs`はquota_ledger/cloud_quotaの
    どちらかがNoneならquotaチェック自体を行わない設計のため、ここでNoneを返すのは
    「registry構成の想定外」を示すシグナルとして呼び出し側がログすることを推奨する）。
    """
    return next(
        (QuotaSpec(name=e.name, daily_quota=e.daily_quota, per_minute_quota=e.per_minute_quota)
         for e in registry if e.name == name),
        None,
    )


def run_startup_chores(
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    limit: int | None = None,
    quota_ledger: QuotaLedger | None = None,
    cloud_quota: QuotaSpec | None = None,
    change_log: ChangeLog | None = None,
    failure_shelve_threshold: int = 3,
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
        quota_ledger=quota_ledger,
        cloud_quota=cloud_quota,
        change_log=change_log,
        failure_shelve_threshold=failure_shelve_threshold,
    )


def run_session_end_chores(
    core: Core,
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    limit: int | None = None,
    quota_ledger: QuotaLedger | None = None,
    cloud_quota: QuotaSpec | None = None,
    change_log: ChangeLog | None = None,
    failure_shelve_threshold: int = 3,
) -> tuple[list[int], ConsumptionSummary]:
    """①セッション終了時: 端数を宿題箱へ積んでから(Core.end_session)、アプリを閉じる前に消化する
    （§2.4 line228, セッション終了の定義=3トリガーはアプリ層が判定してこの関数を呼ぶ）。

    Core.end_session()は端数flushとCore状態のリセットのみを担う。消化(LLM発注)はCoreの
    外側=ここで行うことで、Core=判断／消化=別関数という層分離を守る
    （advisorレビュー2026-07-11。Core.end_session()の中でconsumeを呼ぶとCoreにLLM呼び出しが
    侵入し、Core単体のスタブテスト(StubBrainのみ・LLM不要)が効かなくなる）。

    limitで区切らない限り、このセッションの端数だけでなく宿題箱に残る全pending(蒸留)を
    消化する(過去セッションの積み残しも含めて回収するのが①の役目)。

    2026-07-12: GUI（app/gui_server.py）は明示の別れの挨拶で本関数をもう呼ばない
    （同期の全量消化を廃止。区切り印のみとし、消化はアイドル時②・朝礼③に委ねる。
    DECISIONS参照）。本関数自体はテスト・将来の別経路のために残す。
    """
    job_ids = core.end_session()
    summary = consume_pending_distillation_jobs(
        chore_box,
        memory_store=memory_store,
        thresholds=thresholds,
        lane_call_fns=lane_call_fns,
        limit=limit,
        quota_ledger=quota_ledger,
        cloud_quota=cloud_quota,
        change_log=change_log,
        failure_shelve_threshold=failure_shelve_threshold,
    )
    return job_ids, summary


def run_idle_digest_chunk(
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    limit: int = 1,
    quota_ledger: QuotaLedger | None = None,
    cloud_quota: QuotaSpec | None = None,
    change_log: ChangeLog | None = None,
    failure_shelve_threshold: int = 3,
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
        quota_ledger=quota_ledger,
        cloud_quota=cloud_quota,
        change_log=change_log,
        failure_shelve_threshold=failure_shelve_threshold,
    )


def run_idle_assessment_chunk(
    memory_store: MemoryStore,
    *,
    call_fn: Callable[[str], str],
    routing_rules: RoutingRules,
    change_log: ChangeLog,
    limit: int = 1,
    chore_box: ChoreBox | None = None,
    failure_shelve_threshold: int = 3,
    max_retries: int = 3,
) -> AssessmentBatchSummary:
    """②会話の合間のアイドル時: 既存記憶の機微査定を1〜2件ずつ内職する（§4.6-3）。

    呼び出し側（GUIの見回りスレッド）が「今アイドルか」「GPUは空いているか」
    「会話ロックは空いているか」を判定してから呼ぶことを想定する薄いラッパ
    （`run_idle_digest_chunk`と対）。蒸留ジョブが無い時（宿題箱が空）だけこちらを
    回す優先度は呼び出し側（GUI見回りスレッド）が決める——このモジュールは「呼べば
    指定件数だけ査定する」窓口のみを提供する。未査定の記憶が無ければ何もしない。

    chore_box: 2026-07-12追加。機微査定の失敗回数記録・棚上げ（§4.6-3。車線が"local"1本
    のみのため車線振替は無く、既定回数連続失敗で直接棚上げる）に使う。
    """
    return run_sensitivity_assessment_chunk(
        memory_store,
        call_fn=call_fn,
        routing_rules=routing_rules,
        change_log=change_log,
        limit=limit,
        chore_box=chore_box,
        failure_shelve_threshold=failure_shelve_threshold,
        max_retries=max_retries,
    )


def run_diary_generation(
    core: Core,
    *,
    since_iso: str,
    routing_rules: RoutingRules,
    lane_call_fns: dict[str, Callable[[str], str]],
    change_log: ChangeLog,
    quota_ledger: QuotaLedger | None = None,
    cloud_quota: QuotaSpec | None = None,
) -> DiaryOutcome:
    """夜間放出時（その日の最終セッション終了時）: 日記を1本生成して保存する（§4.5）。

    `since_iso`（当日の始まりのUTC ISO時刻）の算出はアプリ層の責務（呼び出しタイミングの
    判定と同じく§2.4の配線思想を踏襲。ローカル暦日→UTC変換はタイムゾーンを持つ
    呼び出し側の仕事）。気分の軌跡は**生成に成功した時だけ**ここで消費して空にする
    （LLM呼び出し失敗時にまで軌跡や材料窓を消費すると、瞬断1回で当日分の内省材料が
    丸ごと失われ「次回の夜間放出機会に持ち越す」という電源断耐性が成立しなくなるため。
    serina-code-reviewer 2026-07-12 Important指摘）。窓（since_iso）の前進判断も同様に
    呼び出し側がoutcome.generatedを見てから行う（このモジュールではlast_diary_at等の
    永続状態は持たないため、outcomeを返すのみ）。
    """
    mood_summary = core.emotion.summarize_trajectory()
    material = gather_diary_material(
        core.memory_store, since_iso=since_iso, mood_summary=mood_summary,
    )
    outcome = generate_and_save_diary(
        core.memory_store,
        material=material,
        routing_rules=routing_rules,
        lane_call_fns=lane_call_fns,
        change_log=change_log,
        quota_ledger=quota_ledger,
        cloud_quota=cloud_quota,
    )
    if outcome.generated:
        core.emotion.clear_trajectory()
    return outcome


def run_idle_summary_update(
    core: Core,
    *,
    call_fn: Callable[[str], str],
) -> SummaryUpdateOutcome:
    """②アイドル時: 直近窓から溢れたターンを転がし要約へ折り込む（§1.4）。

    窓幅はsmall側（local想定）を使う。cloudのlarge窓では一部が要約と直近の両方に
    載りうるが、欠落より冗長の方が安全。LLM失敗時は次回再挑戦。
    """
    return update_rolling_summary(
        core.session,
        call_fn=call_fn,
        window_size=core.thresholds.recent_turns_small,
    )


def build_default_lane_call_fns(
    gemini_api_key: str | None = None,
) -> dict[str, Callable[[str], str]]:
    """実運用向けlane_call_fns。ローカル車線はAurora(Ollama)、クラウド車線はGemini
    （APIキーがある場合のみ）。§2.4「裏方便の二車線」の実call_fn配線。

    断片ごとの車線振り分け(Coreの個人情報フィルタ)は`Core._enqueue_chore_fragment`が
    `RoutingRules.is_sensitive()`（A:話題語 B:形パターン C:固有名詞）で判定して
    積む時点で決めている（2026-07-12実装）。ここではcloud/local双方のcall_fnを
    用意するだけでよい。
    """
    from serina.brains.aurora.adapter import AuroraAdapter
    from serina.core.config import load_thresholds

    thresholds = load_thresholds()
    lane_call_fns: dict[str, Callable[[str], str]] = {
        "local": AuroraAdapter(
            request_timeout_seconds=thresholds.aurora_request_timeout_seconds,
        ).raw_call,
    }
    if gemini_api_key:
        from serina.brains.gemini.adapter import GeminiAdapter

        lane_call_fns["cloud"] = GeminiAdapter(
            api_key=gemini_api_key,
            request_timeout_seconds=thresholds.gemini_request_timeout_seconds,
        ).raw_call
    return lane_call_fns
