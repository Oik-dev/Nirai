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

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from serina.core.chores.chore_box import ChoreBox
from serina.core.chores.diary import DiaryOutcome, gather_diary_material, generate_and_save_diary
from serina.core.chores.distillation import ConsumptionSummary, consume_pending_distillation_jobs
from serina.core.chores.persona_propose import (
    ProposeOutcome,
    run_idle_persona_propose_chunk,
    should_run_persona_propose,
)
from serina.core.chores.summaries import load_summary_blocks, rebuild_summaries_from_facts
from serina.core.chores.persona_revise import PERSONA_REVISE_CHORE_KIND, revise_persona_block
from serina.core.chores.rolling_summary import (
    SummaryUpdateOutcome,
    TurnSummaryBatchOutcome,
    update_coarse_rolling_summary,
    update_turn_summaries,
)
from serina.core.config import ThresholdsConfig
from serina.core.memory.protection import (
    ChangeLog,
    ChangeReport,
    GenerationStore,
    ProtectionError,
)
from serina.core.memory.store import MemoryStore
from serina.core.persona_assets import DEFAULT_PERSONA_DIR, load_persona_assets
from serina.core.runtime import Core
from serina.core.state.routing_rules import RoutingRules

DEFAULT_EXPORT_LIFE_MIN_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class IdleChoreTickOutcome:
    """見回り1ティックの裏方仕事結果（§3.8 / §4-2）。"""

    kind: str | None = None
    # distillation | rolling_summary | persona_revise |
    # persona_propose | export_life | None
    progressed: bool = False
    interrupted: bool = False


def run_idle_digest_chunk(
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    limit: int = 1,
    change_log: ChangeLog | None = None,
    failure_shelve_threshold: int = 3,
    yield_check: Callable[[], bool] | None = None,
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
        change_log=change_log,
        failure_shelve_threshold=failure_shelve_threshold,
        yield_check=yield_check,
    )


def run_idle_export_life(
    *,
    db_path: Path | str,
    life_dir: Path | str,
    summaries_path: Path | str | None = None,
    weekly_log_path: Path | str | None = None,
    min_interval_seconds: float = DEFAULT_EXPORT_LIFE_MIN_INTERVAL_SECONDS,
    last_export_at: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """②アイドル時: DB→life/ 一方向生成（§3.4 / Wave 4 A10）。LLM 不要。

    最短間隔未満なら False（未実行）。呼び出し側が `last_export_at` を更新する。
    """
    current = now or datetime.now(timezone.utc)
    if (
        last_export_at is not None
        and min_interval_seconds > 0
        and (current - last_export_at).total_seconds() < min_interval_seconds
    ):
        return False
    # tools/ はリポジトリ直下。cwd 非依存で読む。
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from tools.export_life import export_life

    export_life(
        db_path, life_dir,
        summaries_path=summaries_path,
        weekly_log_path=weekly_log_path,
    )
    return True


def run_idle_persona_revise_chunk(
    chore_box: ChoreBox,
    *,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    core: Core | None = None,
    persona_dir: Path | str | None = None,
    db_path: Path | str | None = None,
    backup_dir: Path | str | None = None,
    backup_db_fn: Callable[..., Path] | None = None,
    limit: int = 1,
    yield_check: Callable[[], bool] | None = None,
) -> bool:
    """②アイドル時: 宿題箱の `persona改訂` を1件適用（Brain / Sleep 提案の共通出口）。"""
    jobs = chore_box.pending(kind=PERSONA_REVISE_CHORE_KIND, limit=limit)
    if not jobs:
        return False
    if yield_check is not None and yield_check():
        return False

    job = jobs[0]
    payload = job.payload if isinstance(job.payload, dict) else {}

    def _shelve_with_report(reason: str) -> None:
        # 蒸留側の棚上げ（distillation.py）と同様に変更レポートを残す（監査一貫性）
        chore_box.shelve(job.id, reason=reason)
        change_log.record(ChangeReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action="persona改訂棚上げ", target_id=job.id, reason=reason,
            before=json.dumps(job.payload, ensure_ascii=False, default=str), after=None,
        ))

    block_id = payload.get("block_id") or payload.get("target")
    new_content = payload.get("new_content") or payload.get("content") or payload.get("text")
    reason = payload.get("reason") or "Brain提案（propose_identity_edit）"
    if not isinstance(block_id, str) or not block_id.strip():
        _shelve_with_report("persona改訂: block_id 欠落")
        return False
    if not isinstance(new_content, str) or not new_content.strip():
        _shelve_with_report("persona改訂: new_content 欠落")
        return False

    directory = Path(persona_dir) if persona_dir is not None else DEFAULT_PERSONA_DIR
    try:
        revise_persona_block(
            block_id.strip(),
            new_content,
            reason=str(reason),
            change_log=change_log,
            generation_store=generation_store,
            persona_dir=directory,
            mood_contaminated=bool(payload.get("mood_contaminated", False)),
            backup_db_fn=backup_db_fn,
            db_path=db_path,
            backup_dir=backup_dir,
        )
    except (ProtectionError, ValueError, OSError) as exc:
        _shelve_with_report(f"persona改訂: 関所拒否または書き込み失敗（{exc}）")
        return False

    chore_box.mark_done(job.id)
    if core is not None:
        assets = load_persona_assets(directory)
        core.persona_text = assets.persona_text
        core.absolute_rules = assets.absolute_rules
    return True


def run_idle_chore_tick(
    core: Core,
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    change_log: ChangeLog,
    generation_store: GenerationStore | None = None,
    db_path: Path | str | None = None,
    life_dir: Path | str | None = None,
    summaries_path: Path | str | None = None,
    weekly_log_path: Path | str | None = None,
    persona_dir: Path | str | None = None,
    export_min_interval_seconds: float = DEFAULT_EXPORT_LIFE_MIN_INTERVAL_SECONDS,
    last_export_life_at: datetime | None = None,
    last_persona_propose_at: datetime | None = None,
    now: datetime | None = None,
    limit: int = 1,
    failure_shelve_threshold: int = 3,
    yield_check: Callable[[], bool] | None = None,
) -> IdleChoreTickOutcome:
    """§3.8 配下の裏方1ティック。

    優先順: 蒸留 → 転がし要約 → persona改訂 → Sleep提案 → life/ 出力。
    呼び出し側が `should_run_idle_chores(session_ended=True)`・GPU 空き・turn_lock 取得後に呼ぶ。
    `yield_check` が True を返したら checkpoint を残して中断（発話割り込み）。
    persona_revise / persona_propose / export_life も同じ停止規則（session_ended のみ）の配下。
    Fact 転記は蒸留消化内（`write_fact_from_distillation_candidate`）で行う。
    """
    if chore_box.count(kind="蒸留") > 0:
        summary = run_idle_digest_chunk(
            chore_box,
            memory_store=memory_store,
            thresholds=thresholds,
            lane_call_fns=lane_call_fns,
            limit=limit,
            change_log=change_log,
            failure_shelve_threshold=failure_shelve_threshold,
            yield_check=yield_check,
        )
        if yield_check is not None and yield_check():
            return IdleChoreTickOutcome(kind="distillation", progressed=False, interrupted=True)
        if summary.processed:
            return IdleChoreTickOutcome(kind="distillation", progressed=True)

    local_call_fn = lane_call_fns.get("local")
    # 転がし要約・Sleep提案は LLM 必須。persona適用 / life/ は LLM 不要なので続行可。
    if local_call_fn is not None:
        summary_outcome = run_idle_summary_update(core, call_fn=local_call_fn)
        if summary_outcome.updated:
            return IdleChoreTickOutcome(kind="rolling_summary", progressed=True)

    if generation_store is not None and chore_box.count(kind=PERSONA_REVISE_CHORE_KIND) > 0:
        if yield_check is not None and yield_check():
            return IdleChoreTickOutcome(kind="persona_revise", progressed=False, interrupted=True)
        revised = run_idle_persona_revise_chunk(
            chore_box,
            change_log=change_log,
            generation_store=generation_store,
            core=core,
            persona_dir=persona_dir,
            db_path=db_path,
            yield_check=yield_check,
        )
        if revised:
            return IdleChoreTickOutcome(kind="persona_revise", progressed=True)

    if local_call_fn is not None:
        if yield_check is not None and yield_check():
            return IdleChoreTickOutcome(kind="persona_propose", progressed=False, interrupted=True)
        propose_outcome: ProposeOutcome = run_idle_persona_propose_chunk(
            chore_box,
            memory_store=memory_store,
            call_fn=local_call_fn,
            change_log=change_log,
            prefs_summary=getattr(core, "prefs_summary", "") or "",
            relation_summary=getattr(core, "relation_summary", "") or "",
            persona_dir=persona_dir,
            diary_limit=thresholds.persona_propose_diary_limit,
            max_retries=thresholds.persona_propose_max_retries,
            now=now,
            last_propose_at=last_persona_propose_at,
            yield_check=yield_check,
        )
        if propose_outcome.advance_cooldown:
            return IdleChoreTickOutcome(kind="persona_propose", progressed=True)

    if db_path is not None and life_dir is not None:
        if yield_check is not None and yield_check():
            return IdleChoreTickOutcome(kind="export_life", progressed=False, interrupted=True)
        exported = run_idle_export_life(
            db_path=db_path,
            life_dir=life_dir,
            summaries_path=summaries_path,
            weekly_log_path=weekly_log_path,
            min_interval_seconds=export_min_interval_seconds,
            last_export_at=last_export_life_at,
        )
        if exported:
            return IdleChoreTickOutcome(kind="export_life", progressed=True)

    return IdleChoreTickOutcome()


def run_startup_chores(
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    limit: int | None = None,
    change_log: ChangeLog | None = None,
    failure_shelve_threshold: int = 3,
) -> ConsumptionSummary:
    """起動／日界の蒸留消化（§2.4）。persona／life は `run_growth_chores` 側。"""
    return consume_pending_distillation_jobs(
        chore_box,
        memory_store=memory_store,
        thresholds=thresholds,
        lane_call_fns=lane_call_fns,
        limit=limit,
        change_log=change_log,
        failure_shelve_threshold=failure_shelve_threshold,
    )


@dataclass(frozen=True)
class GrowthChoresOutcome:
    """persona改訂 / Sleep提案 / life 出力の消化結果。"""

    persona_revise_runs: int = 0
    persona_propose_ran: bool = False
    export_life_ran: bool = False
    last_persona_propose_at: datetime | None = None
    last_export_life_at: datetime | None = None


def run_growth_chores(
    core: Core,
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    change_log: ChangeLog,
    generation_store: GenerationStore | None = None,
    db_path: Path | str | None = None,
    life_dir: Path | str | None = None,
    summaries_path: Path | str | None = None,
    weekly_log_path: Path | str | None = None,
    persona_dir: Path | str | None = None,
    export_min_interval_seconds: float = DEFAULT_EXPORT_LIFE_MIN_INTERVAL_SECONDS,
    last_export_life_at: datetime | None = None,
    last_persona_propose_at: datetime | None = None,
    now: datetime | None = None,
    max_rounds: int = 32,
) -> GrowthChoresOutcome:
    """日界／朝礼用: persona改訂 → Sleep提案 → life/ を消化する（蒸留は含めない）。

    idle 廃止後も人格成長と life/ が止まらないための専用経路。
    """
    current = now or datetime.now(timezone.utc)
    revise_runs = 0
    propose_ran = False
    export_ran = False
    propose_at = last_persona_propose_at
    export_at = last_export_life_at
    local_call_fn = lane_call_fns.get("local")

    for _ in range(max(1, max_rounds)):
        progressed = False
        if generation_store is not None and chore_box.count(kind=PERSONA_REVISE_CHORE_KIND) > 0:
            if run_idle_persona_revise_chunk(
                chore_box,
                change_log=change_log,
                generation_store=generation_store,
                core=core,
                persona_dir=persona_dir,
                db_path=db_path,
            ):
                revise_runs += 1
                progressed = True
                continue

        if local_call_fn is not None:
            # persona提案と同じ「1日1回」のタイミングで、facts台帳からprefs/relation要約を
            # 再構築する（死んでいた自動更新配線の復活。設計書決定事項5・2026-07-23）。
            # 提案本体より前に反映し、今日分の提案材料に載せる。
            if should_run_persona_propose(now=current, last_propose_at=propose_at):
                rebuild_summaries_from_facts(
                    memory_store.facts,
                    change_log=change_log,
                    path=summaries_path,
                )
                fresh_blocks = load_summary_blocks(summaries_path)
                core.prefs_summary = fresh_blocks.prefs_summary
                core.relation_summary = fresh_blocks.relation_summary

            propose_outcome = run_idle_persona_propose_chunk(
                chore_box,
                memory_store=memory_store,
                call_fn=local_call_fn,
                change_log=change_log,
                prefs_summary=getattr(core, "prefs_summary", "") or "",
                relation_summary=getattr(core, "relation_summary", "") or "",
                persona_dir=persona_dir,
                diary_limit=thresholds.persona_propose_diary_limit,
                max_retries=thresholds.persona_propose_max_retries,
                now=current,
                last_propose_at=propose_at,
            )
            if propose_outcome.advance_cooldown:
                propose_ran = True
                propose_at = current
                progressed = True
                continue

        if db_path is not None and life_dir is not None:
            if run_idle_export_life(
                db_path=db_path,
                life_dir=life_dir,
                summaries_path=summaries_path,
                weekly_log_path=weekly_log_path,
                min_interval_seconds=export_min_interval_seconds,
                last_export_at=export_at,
                now=current,
            ):
                export_ran = True
                export_at = current
                progressed = True
                continue

        if not progressed:
            break

    return GrowthChoresOutcome(
        persona_revise_runs=revise_runs,
        persona_propose_ran=propose_ran,
        export_life_ran=export_ran,
        last_persona_propose_at=propose_at,
        last_export_life_at=export_at,
    )


def run_session_end_chores(
    core: Core,
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    limit: int | None = None,
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
        change_log=change_log,
        failure_shelve_threshold=failure_shelve_threshold,
    )
    return job_ids, summary


def run_diary_generation(
    core: Core,
    *,
    since_iso: str,
    until_iso: str | None = None,
    include_mood: bool = True,
    target_date: str | None = None,
    created_at: str | None = None,
    routing_rules: RoutingRules,
    lane_call_fns: dict[str, Callable[[str], str]],
    change_log: ChangeLog,
) -> DiaryOutcome:
    """夜間放出時（その日の最終セッション終了時）: 日記を1本生成して保存する（§4.5）。

    `since_iso`（当日の始まりのUTC ISO時刻）の算出はアプリ層の責務（呼び出しタイミングの
    判定と同じく§2.4の配線思想を踏襲。ローカル暦日→UTC変換はタイムゾーンを持つ
    呼び出し側の仕事）。`until_iso`（当日の終わり）を渡すと材料をその日1本分に絞る
    （§4.5「Serina日ごとに1本」。省略時は従来通り無制限＝呼び出し側が複数日分を意図的に
    まとめたい場合の後方互換）。

    `include_mood=False`なら気分の軌跡を材料に混ぜない（2026-07-25是正: 長期未起動後の
    キャッチアップで複数日分をまとめて処理する際、2日目以降は「今日の」気分ではない
    軌跡を過去日の日記に載せてしまう穴を防ぐ。呼び出し側は直近1日分のときだけTrueにする）。
    気分の軌跡は**生成に成功した時だけ**ここで消費して空にする（LLM呼び出し失敗時にまで
    軌跡や材料窓を消費すると、瞬断1回で当日分の内省材料が丸ごと失われ「次回の夜間放出
    機会に持ち越す」という電源断耐性が成立しなくなるため。serina-code-reviewer
    2026-07-12 Important指摘）。窓（since_iso）の前進判断も同様に呼び出し側が
    outcome.generatedを見てから行う（このモジュールではlast_diary_at等の永続状態は
    持たないため、outcomeを返すのみ）。

    2026-07-18: 書き手はlocalの1車線のみ（§9.3）のため、旧cloud車線の残弾台帳
    （quota_ledger/cloud_quota）引数は削除した（`core/chores/diary.py`参照）。

    `target_date`/`created_at`はキャッチアップ（複数日分の未処理を回収する回）で対象
    Serina日を明示するために使う（2026-07-25是正I-3: 省略時はDB既定＝生成時刻）。
    """
    mood_summary = core.emotion.summarize_trajectory() if include_mood else ""
    material = gather_diary_material(
        core.memory_store, since_iso=since_iso, until_iso=until_iso, mood_summary=mood_summary,
        target_date=target_date,
    )
    outcome = generate_and_save_diary(
        core.memory_store,
        material=material,
        routing_rules=routing_rules,
        lane_call_fns=lane_call_fns,
        change_log=change_log,
        created_at=created_at,
    )
    if outcome.generated and include_mood:
        # include_mood=Falseの回は軌跡を消費していないので、クリアもしない
        # （温存して次の「本来の当日分」まで持ち越す）。
        core.emotion.clear_trajectory()
    return outcome


def run_idle_summary_update(
    core: Core,
    *,
    call_fn: Callable[[str], str],
) -> SummaryUpdateOutcome:
    """②アイドル時: fine_band より古い未折り込みターンを粗く追記（§1.4）。

    LLM失敗時は次回再挑戦。
    """
    return update_coarse_rolling_summary(
        core.session,
        call_fn=call_fn,
        fine_band_turns=core.thresholds.fine_band_turns,
        step_turns=core.thresholds.coarse_update_every_n_turns,
    )


def run_post_turn_summaries(
    core: Core,
    *,
    call_fn: Callable[[str], str],
) -> TurnSummaryBatchOutcome:
    """ターン確定後: fine 更新＋条件付き coarse 更新（§1.5 ④⑦）。"""
    return update_turn_summaries(
        core.session,
        call_fn=call_fn,
        thresholds=core.thresholds,
    )


def build_default_lane_call_fns() -> dict[str, Callable[[str], str]]:
    """実運用向けlane_call_fns。§9.3でcloud車線は永久退役、local車線のみ（Qwen/Ollama）。

    断片ごとの車線振り分け(Coreの個人情報フィルタ)は`Core._enqueue_chore_fragment`が
    lane="local"固定で積む（§9.3）。ここではlocal用call_fnを用意するだけでよい。
    """
    from serina.brains.qwen.adapter import QwenAdapter
    from serina.core.config import load_thresholds

    thresholds = load_thresholds()
    return {
        "local": QwenAdapter(
            request_timeout_seconds=thresholds.qwen_request_timeout_seconds,
        ).raw_call,
    }
