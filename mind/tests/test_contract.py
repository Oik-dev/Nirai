"""契約試験（brains/contract）: 報告書の書式検査。設計書 §2.1, §5.2

守るもの：返答本文の体裁が崩れた報告書は通さない。返答のあとの評価は、脳の答えのまま運ぶ
（中身を確かめるのは Core の関所。tests/test_intake_gate.py）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

import pytest

from mind.brains.contract.schema import ContractFormatError, Report, validate_report


def _valid_report_dict() -> dict:
    return {
        "reply": "おかえりなさい",
        "appraisal": {"feeling": "うれしい", "valence": "うれしい", "arousal": "少し動いた",
                      "distance": "近づいた", "master_state": ""},
    }


def test_valid_report_passes_format_check() -> None:
    report = validate_report(_valid_report_dict())
    assert isinstance(report, Report)
    assert report.reply == "おかえりなさい"
    assert report.appraisal["valence"] == "うれしい"


def test_report_without_appraisal_passes() -> None:
    """評価を聞けなかったターンも、返答は届く。"""
    raw = _valid_report_dict()
    del raw["appraisal"]
    assert validate_report(raw).appraisal is None


def test_empty_reply_is_rejected() -> None:
    raw = _valid_report_dict()
    raw["reply"] = " "
    with pytest.raises(ContractFormatError):
        validate_report(raw)
