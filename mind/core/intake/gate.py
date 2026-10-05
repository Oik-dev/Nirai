"""Core intake: 報告書の受領窓口。設計書 §2.5（関所）, §2.7（状態更新の流れ）。

関所は2つ。報告書の書式検査（返答本文・自己評価欄）と、返答のあとの評価の検査（選択肢にない答えは捨てる。
core/feeling/appraisal.py）。脳の評価は提案で、体の芯をどれだけ動かすかは Core の対応表が決める（core/feeling/body.py）。
気持ちの記録に残すのは、会話を記録したあと（拠った会話の場所が決まってから。Core.feel）。
記憶は報告書からは書かない（眠りの間に会話の記録から本人が書く。core/memory/sleep.py）。

外部相談（Gemini advisor・Tavily検索）の実行主体はCore（`runtime.py:_resolve_advisor_window`。
2026-07-31 Phase D 無言統合パイプライン）のみ。本モジュールは`precomputed_advisor_outcome`で
結果を受け取って報告書へ載せるだけで、外部通信は一切行わない（通常会話ターンからの誤発火防止）。
"""

from __future__ import annotations

from dataclasses import dataclass

from mind.brains.contract.schema import Report, validate_report
from mind.core import debug_log
from mind.core.feeling.appraisal import Appraisal, parse_appraisal
from mind.core.intake.advisor_tools import AdvisorToolOutcome


@dataclass
class IntakeResult:
    report: Report
    appraisal: Appraisal | None = None  # 確かめて整えた評価（聞けなかった・使えなかったら None）
    advisor_tool_outcome: AdvisorToolOutcome | None = None
    # 2026-07-31 Phase D: Tavily出典（画面の注記。設定はCore._process_turn）。
    # Core所有の定型テンプレート＋URL文字列のみ。report.replyとは別経路で、
    # セッション履歴・記憶には混ぜない（Phase D-6）。
    citations: list[dict] | None = None


def process_report(
    raw: dict,
    *,
    precomputed_advisor_outcome: AdvisorToolOutcome | None = None,
) -> IntakeResult:
    """報告書を関所に通す。外部相談は実行しない（Coreの事実レーンが実行済みの結果を載せるだけ）。"""
    report = validate_report(raw)
    appraisal = None
    if report.appraisal is not None:
        try:
            appraisal = parse_appraisal(report.appraisal)
        except ValueError as exc:
            debug_log.emit(kind="appraisal", action="discard", reason=str(exc))
    return IntakeResult(report=report, appraisal=appraisal, advisor_tool_outcome=precomputed_advisor_outcome)
