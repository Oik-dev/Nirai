"""Brain-Core間の契約書式。設計書v2 §2.1, §2.2, §3.4。

書式検査（設計書v2 §2.5 関所①）: ここでの検証は「体裁が正しいか」のみ。
内容の真偽（引用照合など）はPhase 2以降の関所④で扱う。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ContractFormatError(Exception):
    """報告書の書式検査に失敗したことを示す例外。"""


class CloudRejectionError(Exception):
    """クラウドBrainが安全フィルタ等で応答そのものを拒否したことを示す例外。

    設計書v2 §3.5: 振り分けルール(ラチェット)を研ぐのは「クラウドの拒否」だけ。
    通信エラー・弾切れ・単純な契約書式違反はこの例外ではないため、
    Core._obtain_valid_reportはtighten()の対象からそれらを除外できる。
    """


@dataclass(frozen=True)
class SelfAssessment:
    """自己評価欄（§3.4）。必須項目。空欄の報告書は書式検査で弾く。"""

    over_capacity: bool
    reason: str


@dataclass(frozen=True)
class Fusen:
    """付箋（§2.1, §2.2）。種類名・版数・中身・確信度を持つ。"""

    kind: str
    version: int
    content: dict
    confidence: float


@dataclass(frozen=True)
class Report:
    """Brainが毎ターン返す報告書（§2.1）。"""

    reply: str
    fusen_list: list[Fusen] = field(default_factory=list)
    self_assessment: SelfAssessment | None = None


def _validate_report_header(raw: dict) -> tuple[str, SelfAssessment, list]:
    """報告書の必須項目（返答本文・自己評価欄）を検査し、付箋束の生データを返す。

    不正なら ContractFormatError を送出する（§2.5 関所①）。
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
    self_assessment = SelfAssessment(over_capacity=over_capacity, reason=reason)

    fusen_list_raw = raw.get("fusen_list", [])
    if not isinstance(fusen_list_raw, list):
        raise ContractFormatError("fusen_listはlist形式である必要がある")

    return reply, self_assessment, fusen_list_raw


def validate_report(raw: dict) -> Report:
    """報告書の生データ(dict)を検査し、Reportへ変換する（1つでも壊れた付箋があれば全体を棄却）。"""
    reply, self_assessment, fusen_list_raw = _validate_report_header(raw)
    fusen_list = [_validate_fusen(item) for item in fusen_list_raw]
    return Report(reply=reply, fusen_list=fusen_list, self_assessment=self_assessment)


def validate_report_lenient(raw: dict) -> tuple[Report, list[dict]]:
    """報告書を検査する。壊れた付箋は個別に破棄し、報告書全体は棄却しない（§2.5）。

    ただし返答本文・自己評価欄が不正な場合は報告書全体を ContractFormatError で棄却する。
    戻り値: (有効な付箋のみを含むReport, 破棄された付箋の生データ一覧)
    """
    reply, self_assessment, fusen_list_raw = _validate_report_header(raw)

    fusen_list: list[Fusen] = []
    discarded: list[dict] = []
    for item in fusen_list_raw:
        try:
            fusen_list.append(_validate_fusen(item))
        except ContractFormatError:
            discarded.append(item)

    report = Report(reply=reply, fusen_list=fusen_list, self_assessment=self_assessment)
    return report, discarded


def _validate_fusen(raw: dict) -> Fusen:
    if not isinstance(raw, dict):
        raise ContractFormatError("付箋はdict形式である必要がある")

    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise ContractFormatError("付箋のkindが空")

    version = raw.get("version")
    if not isinstance(version, int):
        raise ContractFormatError(f"付箋のversionがint型でない: {kind}")

    content = raw.get("content")
    if not isinstance(content, dict):
        raise ContractFormatError(f"付箋のcontentがdict型でない: {kind}")

    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
        raise ContractFormatError(f"付箋のconfidenceが0〜1の範囲外: {kind}")

    return Fusen(kind=kind, version=version, content=content, confidence=float(confidence))
