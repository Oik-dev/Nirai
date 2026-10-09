"""Core intake の関所（§2.5）。報告書の書式と、返答のあとの評価の検査。

守るもの：脳の評価は提案。選択肢にない答えは捨て（そのターンは評価なし）、言葉は整えて受け取る。
数を書いてきても、数は受け取らない（体の芯をどれだけ動かすかは Core の対応表。tests/test_feelings.py）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

import pytest

from mind.brains.contract.schema import ContractFormatError
from mind.core.feeling.appraisal import FEELING_MAX, parse_appraisal
from mind.core.intake.gate import process_report


def _raw_report(appraisal: object = None) -> dict:
    return {
        "reply": "そうだったんですね",
        "appraisal": appraisal,
    }


def _answer(**overrides: object) -> dict:
    answer = {
        "feeling": "ちょっと照れくさいけど、嬉しい",
        "valence": "うれしい",
        "arousal": "少し動いた",
        "distance": "近づいた",
        "master_state": "楽しそう",
    }
    return {**answer, **overrides}


def test_valid_appraisal_is_accepted() -> None:
    result = process_report(_raw_report(_answer()))
    assert result.appraisal is not None
    assert result.appraisal.feeling == "ちょっと照れくさいけど、嬉しい"
    assert result.appraisal.evaluation() == {
        "valence": "うれしい", "arousal": "少し動いた", "distance": "近づいた", "master_state": "楽しそう",
    }


def test_choice_outside_the_options_discards_the_appraisal() -> None:
    """数や、選択肢にない言葉で答えてきたら、評価ごと捨てる（返答は届く）。"""
    for bad in (_answer(valence=0.8), _answer(arousal="すごく"), _answer(distance=None)):
        result = process_report(_raw_report(bad))
        assert result.appraisal is None
        assert result.report.reply == "そうだったんですね"


def test_missing_appraisal_is_none() -> None:
    assert process_report(_raw_report(None)).appraisal is None
    assert process_report(_raw_report("うれしい")).appraisal is None


def test_words_are_tidied() -> None:
    long = "あ" * (FEELING_MAX + 30)
    appraisal = parse_appraisal(_answer(feeling=f"「 {long} 」", master_state=None, valence=" うれしい "))
    assert len(appraisal.feeling) <= FEELING_MAX + 2
    assert appraisal.master_state == ""
    assert appraisal.valence == "うれしい"


def test_broken_report_is_rejected() -> None:
    raw = _raw_report(_answer())
    raw["reply"] = "  "
    with pytest.raises(ContractFormatError):
        process_report(raw)
