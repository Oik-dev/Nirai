"""Core intake: 付箋の受領窓口。設計書 §2.5(関所①〜③), §2.7(状態更新の流れ)

Phase 1範囲: 関所①書式検査・②確信度足切り・③急変防止弁（気分層）まで。
関所④引用照合・記憶候補の審査ラインはPhase 2で接続する。

外部相談（Gemini advisor）の実行主体はCore（`runtime.py:_apply_advisor_pipeline`）の
事実レーンのみ。本モジュールは`precomputed_advisor_outcome`で結果を受け取って
報告書へ載せるだけで、外部通信は一切行わない（通常会話ターンからの誤発火防止）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from serina.brains.contract.schema import Fusen, Report, validate_report_lenient
from serina.core.config import ThresholdsConfig
from serina.core.context.temporal_cue import extract_schedule_datetime
from serina.core.intake.advisor_tools import AdvisorToolOutcome
from serina.core.intake.memory_tools import MemoryToolOutcome, execute_memory_tool_calls, parse_memory_tool_calls
from serina.core.memory.facts import (
    FACT_CATEGORY_ANNIVERSARY,
    FACT_CATEGORY_SCHEDULE,
    FactError,
)
from serina.core.memory.protection import ChangeLog, ChangeReport
from serina.core.memory.store import MemoryStore
from serina.core.state.desire import DesireState, is_desire_gate_open
from serina.core.state.emotion import EmotionState
from serina.core.state.relationship import RelationshipState

_SCHEDULE_IMMEDIATE_CATEGORIES = frozenset({FACT_CATEGORY_SCHEDULE, FACT_CATEGORY_ANNIVERSARY})


@dataclass
class IntakeResult:
    report: Report
    accepted_fusen: list[Fusen] = field(default_factory=list)
    rejected_by_confidence: list[Fusen] = field(default_factory=list)
    discarded_by_format: list[dict] = field(default_factory=list)
    memory_tool_outcome: MemoryToolOutcome | None = None
    advisor_tool_outcome: AdvisorToolOutcome | None = None
    # advisor結果の2通目メッセージ（2026-07-20 応答高速化。設定はCore._process_turn）。
    # 1通目(report.reply)は表示済みのため置換せず、追加の吹き出しとして届ける。
    followup_reply: str | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_valid_from(category: str, when: datetime) -> str:
    """予定は ISO、記念日は年無し `--MM-DD[THH:MM]`。"""
    local = when.astimezone() if when.tzinfo else when
    if category == FACT_CATEGORY_ANNIVERSARY:
        base = f"--{local.month:02d}-{local.day:02d}"
        if local.hour or local.minute:
            return f"{base}T{local.hour:02d}:{local.minute:02d}"
        return base
    return local.isoformat()


def apply_schedule_propose_facts(
    *,
    master_utterance: str,
    memory_tool_outcome: MemoryToolOutcome | None,
    memory_store: MemoryStore | None,
    change_log: ChangeLog | None = None,
    now: datetime | None = None,
) -> list[ChangeReport]:
    """予定/記念日の propose_fact をターン後関所で即時書き込む（§4.9 v5）。

    日時抽出に失敗したら何も書かず、proposal は通常の蒸留経路（pending_distillation）に委ねる。
    意味的判断はせず、カテゴリ一致と抽出成否だけで機械的に分岐する。
    """
    reports: list[ChangeReport] = []
    if memory_store is None or memory_tool_outcome is None:
        return reports
    turn_now = now or datetime.now(timezone.utc)
    extracted = extract_schedule_datetime(master_utterance, turn_now)

    for prop in memory_tool_outcome.proposals:
        if prop.get("tool") != "propose_fact":
            continue
        proposal = prop.get("proposal") or {}
        if not isinstance(proposal, dict):
            continue
        category = proposal.get("category")
        if category not in _SCHEDULE_IMMEDIATE_CATEGORIES:
            continue

        # 抽出失敗 → 即時パスを使わず蒸留へ委ねる（proposal は pending_distillation のまま）
        if extracted is None:
            report = ChangeReport(
                timestamp=_utc_now_iso(),
                action="予定即時書き込み見送り",
                target_id=0,
                reason="日時抽出失敗のため通常の蒸留経路へ委ねる",
                before=None,
                after=str(proposal.get("statement") or ""),
            )
            reports.append(report)
            if change_log is not None:
                change_log.record(report)
            continue

        statement = str(proposal.get("statement") or "").strip()
        if not statement:
            report = ChangeReport(
                timestamp=_utc_now_iso(),
                action="予定即時書き込み見送り",
                target_id=0,
                reason="statement が空のため書き込み抑止",
                before=None,
                after=None,
            )
            reports.append(report)
            if change_log is not None:
                change_log.record(report)
            continue

        valid_from = _format_valid_from(str(category), extracted)

        # 同一日時・同一カテゴリの完全一致 → 抑止＋変更レポート
        duplicates = [
            f for f in memory_store.facts.list_active_facts_by_category(str(category))
            if f.valid_from == valid_from
        ]
        if duplicates:
            report = ChangeReport(
                timestamp=_utc_now_iso(),
                action="予定即時書き込み抑止（重複）",
                target_id=0,
                reason=(
                    f"同一日時・同一カテゴリの active fact が既にある"
                    f"（id={duplicates[0].id}, valid_from={valid_from}）"
                ),
                before=duplicates[0].statement,
                after=statement,
            )
            reports.append(report)
            if change_log is not None:
                change_log.record(report)
            prop["status"] = "suppressed_duplicate"
            continue

        subject = str(proposal.get("subject") or "マスター")
        predicate = str(
            proposal.get("predicate")
            or ("has_anniversary" if category == FACT_CATEGORY_ANNIVERSARY else "has_schedule")
        )
        obj = str(proposal.get("object") or statement[:40])
        supersedes = proposal.get("supersedes")
        try:
            if isinstance(supersedes, str) and supersedes.strip():
                new_id = memory_store.facts.supersede_fact(
                    supersedes.strip(),
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    statement=statement,
                    valid_from=valid_from,
                    episode_ids=[],
                    status="active",
                    category=str(category),
                )
                action = "予定即時 supersede"
            else:
                new_id = memory_store.facts.add_fact(
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    statement=statement,
                    valid_from=valid_from,
                    episode_ids=[],
                    status="active",
                    category=str(category),
                )
                action = "予定即時追加"
        except FactError as exc:
            report = ChangeReport(
                timestamp=_utc_now_iso(),
                action="予定即時書き込み失敗",
                target_id=0,
                reason=str(exc),
                before=None,
                after=statement,
            )
            reports.append(report)
            if change_log is not None:
                change_log.record(report)
            continue

        report = ChangeReport(
            timestamp=_utc_now_iso(),
            action=action,
            target_id=new_id,  # type: ignore[arg-type]  # fact ULID（既存 distillation と同じ）
            reason=f"カテゴリ={category}, valid_from={valid_from}",
            before=None,
            after=statement,
        )
        reports.append(report)
        if change_log is not None:
            change_log.record(report)
        prop["status"] = "accepted_immediate"
        prop["fact_id"] = new_id

    return reports


def process_report(
    raw: dict,
    *,
    emotion: EmotionState,
    relationship: RelationshipState,
    thresholds: ThresholdsConfig,
    memory_store: MemoryStore | None = None,
    precomputed_advisor_outcome: AdvisorToolOutcome | None = None,
    desire: DesireState | None = None,
    now: datetime | None = None,
) -> IntakeResult:
    """報告書を関所①〜③に通し、感情・関係状態を更新する。

    外部相談は実行しない。`precomputed_advisor_outcome`（Coreの事実レーンが
    既に実行した結果）をそのまま報告書へ載せるだけ。
    """
    tool_calls, _discarded_tools = parse_memory_tool_calls(raw.get("memory_tool_calls"))
    memory_tool_outcome: MemoryToolOutcome | None = None
    if memory_store is not None and tool_calls:
        memory_tool_outcome = execute_memory_tool_calls(tool_calls, memory_store)

    advisor_tool_outcome: AdvisorToolOutcome | None = precomputed_advisor_outcome

    # 関所①: 書式検査（壊れた付箋は個別に破棄）
    report, discarded_by_format = validate_report_lenient(raw)

    accepted_fusen: list[Fusen] = []
    rejected_by_confidence: list[Fusen] = []

    for fusen in report.fusen_list:
        # 関所②: 確信度の足切り
        threshold = thresholds.confidence_threshold_for(fusen.kind)
        if fusen.confidence < threshold:
            rejected_by_confidence.append(fusen)
            continue
        accepted_fusen.append(fusen)
        _apply_fusen(
            fusen,
            emotion=emotion,
            relationship=relationship,
            thresholds=thresholds,
            desire=desire,
            now=now,
        )

    # 気分: 関所③ 情動へのにじみ（§2.3 A6）。ループ完了後に1ターンにつきちょうど1回だけ
    # 呼ぶ（付箋が1枚も無いターンでも、気分は情動の履歴を追うため呼ぶ）。
    emotion.apply_mood_bleed(
        bleed_rate=thresholds.emotion_mood_bleed_rate,
        max_delta_per_turn=thresholds.mood_guard_max_delta_per_turn,
    )

    return IntakeResult(
        report=report,
        accepted_fusen=accepted_fusen,
        rejected_by_confidence=rejected_by_confidence,
        discarded_by_format=discarded_by_format,
        memory_tool_outcome=memory_tool_outcome,
        advisor_tool_outcome=advisor_tool_outcome,
    )


def _apply_fusen(
    fusen: Fusen,
    *,
    emotion: EmotionState,
    relationship: RelationshipState,
    thresholds: ThresholdsConfig,
    desire: DesireState | None = None,
    now: datetime | None = None,
) -> None:
    """情動（apply_affect_delta）と関係観測のみを担当する。

    2026-07-26 A6: 気分の更新（apply_mood_bleed）はここでは行わない。付箋ループの
    外（`process_report`側）でターンにつき1回だけ呼ぶ（付箋0枚のターンでも気分は
    情動を追うため）。
    Task 3-4: 「満たされた」判定は心の動き付箋の delta 適用直後に行う。
    """
    if fusen.kind == "心の動き":
        deltas = fusen.content.get("deltas", {})
        # 情動: 制限なしで更新（§2.3）。2026-07-26 A5: 対極カップリングつき。
        emotion.apply_affect_delta(
            deltas, opposite_coupling_ratio=thresholds.emotion_opposite_coupling_ratio,
        )
        # Task 3-4: 欲求が高く抑制門が開いていて、喜び/信頼が大きく上がった → 放電＋不応
        if desire is not None and now is not None:
            gate_open = is_desire_gate_open(
                emotion.affect,
                threshold=thresholds.desire_suppression_threshold,
            )
            joy_or_trust_big = (
                float(deltas.get("喜び", 0.0)) >= thresholds.desire_fulfillment_delta_threshold
                or float(deltas.get("信頼", 0.0)) >= thresholds.desire_fulfillment_delta_threshold
            )
            # 2026-07-30 マスター承認b（レビューC-2是正）: 判定は「このターンの
            # tickでlevelが動く前」のスナップショットで見る。is_fulfillment_level_high()
            # の現在値を使うと、同ターン内でtickの未充足減衰が先に効いて0.6を僅差で
            # 割り込み、判定がすり抜けることがあった。
            fulfilled_before_tick = desire.level_before_tick >= desire.fulfillment_level_threshold
            # 2026-07-30 レビューC-b多重防御: 同ターン内に「心の動き」付箋が複数あるとき、
            # 1枚目の放電で不応期に入った後は2枚目以降で再放電させない
            # （discharge_and_enter_refractoryのlevel_before_tickリセットが主防御）。
            if (
                gate_open
                and fulfilled_before_tick
                and joy_or_trust_big
                and not desire.is_in_refractory(now)
            ):
                emotion.apply_affect_delta(
                    {
                        "喜び": thresholds.desire_fulfillment_boost,
                        "信頼": thresholds.desire_fulfillment_boost,
                    },
                    opposite_coupling_ratio=thresholds.emotion_opposite_coupling_ratio,
                )
                desire.discharge_and_enter_refractory(now)
    elif fusen.kind == "マスター観測":
        observation = fusen.content.get("observation")
        if observation:
            relationship.observe(master_mood=observation)
    elif fusen.kind == "forget_request":
        # Wave 2: 付箋受理のみ。実行は directed_forget（Wave 3 で Brain ツール接続）
        pass
    # 記憶候補は蒸留（裏方便）が唯一の生成源。
    # センシティブ観測・交代要請は会話クラウド退役により消費しない（受理記録のみ残りうる）。
    elif fusen.kind == "道具使用":
        # Wave 7: 受理記録のみ。実行は advisor_tool_calls → execute_advisor_tool_calls
        # （2026-07-26: fusenからのフォールバック抽出は削除。A1）
        pass
