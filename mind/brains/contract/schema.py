"""Brain-Core間の契約書式。設計書 §2.1。

書式検査（設計書 §2.5）: ここでの検証は「体裁が正しいか」のみ。
返答のあとの評価（appraisal）は、脳の答えのまま運ぶ。中身を確かめて整えるのは Core の関所（core/intake/gate.py）。
"""

from __future__ import annotations

from dataclasses import dataclass


class ContractFormatError(Exception):
    """報告書の書式検査に失敗したことを示す例外。"""


@dataclass(frozen=True)
class Report:
    """Brainが毎ターン返す報告書（§2.1）。"""

    reply: str
    appraisal: object = None  # 返答のあとの評価（脳の答えのまま。聞けなかったら None）


def validate_report(raw: dict) -> Report:
    """報告書の生データ(dict)を検査し、Reportへ変換する。

    返答本文が不正なら ContractFormatError（§2.5）。
    """
    if not isinstance(raw, dict):
        raise ContractFormatError("報告書はdict形式である必要がある")

    reply = raw.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ContractFormatError("返答本文(reply)が空、または文字列でない")

    return Report(reply=reply, appraisal=raw.get("appraisal"))
