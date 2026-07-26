"""Core intake: 付箋の受領窓口。設計書 §2.5(関所①〜③), §2.7(状態更新の流れ)

Phase 1範囲: 関所①書式検査・②確信度足切り・③急変防止弁（気分層）まで。
関所④引用照合・記憶候補の審査ラインはPhase 2で接続する。

外部相談（Gemini advisor）の実行主体はCore（`runtime.py:_apply_advisor_pipeline`）の
事実レーンのみ。本モジュールは`precomputed_advisor_outcome`で結果を受け取って
報告書へ載せるだけで、外部通信は一切行わない（通常会話ターンからの誤発火防止）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from serina.brains.contract.schema import Fusen, Report, validate_report_lenient
from serina.core.config import ThresholdsConfig
from serina.core.intake.advisor_tools import AdvisorToolOutcome
from serina.core.intake.memory_tools import MemoryToolOutcome, execute_memory_tool_calls, parse_memory_tool_calls
from serina.core.memory.store import MemoryStore
from serina.core.state.emotion import EmotionState
from serina.core.state.relationship import RelationshipState


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


def process_report(
    raw: dict,
    *,
    emotion: EmotionState,
    relationship: RelationshipState,
    thresholds: ThresholdsConfig,
    memory_store: MemoryStore | None = None,
    precomputed_advisor_outcome: AdvisorToolOutcome | None = None,
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
        _apply_fusen(fusen, emotion=emotion, relationship=relationship, thresholds=thresholds)

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
) -> None:
    """情動（apply_affect_delta）と関係観測のみを担当する。

    2026-07-26 A6: 気分の更新（apply_mood_bleed）はここでは行わない。付箋ループの
    外（`process_report`側）でターンにつき1回だけ呼ぶ（付箋0枚のターンでも気分は
    情動を追うため）。
    """
    if fusen.kind == "心の動き":
        deltas = fusen.content.get("deltas", {})
        # 情動: 制限なしで更新（§2.3）。2026-07-26 A5: 対極カップリングつき。
        emotion.apply_affect_delta(
            deltas, opposite_coupling_ratio=thresholds.emotion_opposite_coupling_ratio,
        )
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
        # Wave 7: 受理記録。実行は advisor_tool_calls / 付箋抽出 → execute_advisor_tool_calls
        pass
