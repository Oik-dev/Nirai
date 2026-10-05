"""Brain-Core間の契約書式。設計書 §2.1, §3.4。

書式検査（設計書 §2.5）: ここでの検証は「体裁が正しいか」のみ。
返答のあとの評価（appraisal）は、脳の答えのまま運ぶ。中身を確かめて整えるのは Core の関所（core/intake/gate.py）。
"""

from __future__ import annotations

from dataclasses import dataclass


class ContractFormatError(Exception):
    """報告書の書式検査に失敗したことを示す例外。"""


class CloudRejectionError(Exception):
    """クラウドBrainが安全フィルタ等で応答そのものを拒否したことを示す例外。

    設計書 §3.5: 振り分けルール(ラチェット)を研ぐのは「クラウドの拒否」だけ。
    通信エラー・弾切れ・単純な契約書式違反はこの例外ではないため、
    Core._obtain_valid_reportはtighten()の対象からそれらを除外できる。
    """


@dataclass(frozen=True)
class SelfAssessment:
    """自己評価欄（§3.4）。必須項目。空欄の報告書は書式検査で弾く。"""

    over_capacity: bool
    reason: str


@dataclass(frozen=True)
class Report:
    """Brainが毎ターン返す報告書（§2.1）。"""

    reply: str
    self_assessment: SelfAssessment | None = None
    appraisal: object = None  # 返答のあとの評価（脳の答えのまま。聞けなかったら None）


def validate_report(raw: dict) -> Report:
    """報告書の生データ(dict)を検査し、Reportへ変換する。

    返答本文・自己評価欄が不正なら ContractFormatError（§2.5）。
    """
    if not isinstance(raw, dict):
        raise ContractFormatError("報告書はdict形式である必要がある")

    reply = raw.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ContractFormatError("返答本文(reply)が空、または文字列でない")

    self_assessment_raw = raw.get("self_assessment")
    if not isinstance(self_assessment_raw, dict):
        raise ContractFormatError("自己評価欄(self_assessment)が存在しない")
    over_capacity = self_assessment_raw.get("over_capacity")
    reason = self_assessment_raw.get("reason")
    if not isinstance(over_capacity, bool):
        raise ContractFormatError("自己評価欄のover_capacityがbool型でない")
    if not isinstance(reason, str) or not reason.strip():
        raise ContractFormatError("自己評価欄のreasonが空")

    return Report(
        reply=reply,
        self_assessment=SelfAssessment(over_capacity=over_capacity, reason=reason),
        appraisal=raw.get("appraisal"),
    )
