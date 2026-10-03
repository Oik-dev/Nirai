"""Core intake: 付箋の受領窓口。設計書 §2.5(関所①〜③), §2.7(状態更新の流れ)

関所①書式検査・②確信度足切り・③急変防止弁（気分層）。付箋が動かすのは感情と関係の状態だけ。
記憶は付箋からは書かない（眠りの間に会話の記録から本人が書く。core/memory/sleep.py）。

外部相談（Gemini advisor・Tavily検索）の実行主体はCore（`runtime.py:_resolve_advisor_window`。
2026-07-31 Phase D 無言統合パイプライン）のみ。本モジュールは`precomputed_advisor_outcome`で
結果を受け取って報告書へ載せるだけで、外部通信は一切行わない（通常会話ターンからの誤発火防止）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from mind.brains.contract.schema import Fusen, Report, validate_report_lenient
from mind.core.config import ThresholdsConfig
from mind.core.intake.advisor_tools import AdvisorToolOutcome
from mind.core.state.desire import DesireState, is_desire_gate_open
from mind.core.state.emotion import EmotionState
from mind.core.state.relationship import RelationshipState


@dataclass
class IntakeResult:
    report: Report
    accepted_fusen: list[Fusen] = field(default_factory=list)
    rejected_by_confidence: list[Fusen] = field(default_factory=list)
    discarded_by_format: list[dict] = field(default_factory=list)
    advisor_tool_outcome: AdvisorToolOutcome | None = None
    # 2026-07-31 Phase D: Tavily出典（画面の注記。設定はCore._process_turn）。
    # Core所有の定型テンプレート＋URL文字列のみ。report.replyとは別経路で、
    # セッション履歴・記憶には混ぜない（Phase D-6）。
    citations: list[dict] | None = None


def process_report(
    raw: dict,
    *,
    emotion: EmotionState,
    relationship: RelationshipState,
    thresholds: ThresholdsConfig,
    precomputed_advisor_outcome: AdvisorToolOutcome | None = None,
    desire: DesireState | None = None,
    now: datetime | None = None,
) -> IntakeResult:
    """報告書を関所①〜③に通し、感情・関係状態を更新する。

    外部相談は実行しない。`precomputed_advisor_outcome`（Coreの事実レーンが
    既に実行した結果）をそのまま報告書へ載せるだけ。
    """
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
    # ほかの種類の付箋は受理の記録だけ（記憶は眠りの間に会話の記録から書く。core/memory/sleep.py）。
