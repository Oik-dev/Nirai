"""契約試験（brains/contract）: 報告書の書式検査。設計書 §2.1, §2.2, §3.4, §5.2"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.brains.contract.schema import (
    ContractFormatError,
    Fusen,
    Report,
    SelfAssessment,
    validate_report,
    validate_report_lenient,
)


def _valid_report_dict() -> dict:
    return {
        "reply": "おかえりなさい",
        "fusen_list": [
            {
                "kind": "心の動き",
                "version": 1,
                "content": {"deltas": {"喜び": 0.2}, "trigger": "帰宅の挨拶"},
                "confidence": 0.8,
            }
        ],
        "self_assessment": {"over_capacity": False, "reason": "日常会話の範囲"},
    }


def test_valid_report_passes_format_check() -> None:
    report = validate_report(_valid_report_dict())
    assert isinstance(report, Report)
    assert report.reply == "おかえりなさい"
    assert len(report.fusen_list) == 1
    assert isinstance(report.fusen_list[0], Fusen)
    assert isinstance(report.self_assessment, SelfAssessment)


def test_missing_self_assessment_is_rejected() -> None:
    raw = _valid_report_dict()
    del raw["self_assessment"]
    try:
        validate_report(raw)
        raise AssertionError("自己評価欄なしの報告書が通ってしまった")
    except ContractFormatError:
        pass


def test_empty_self_assessment_reason_is_rejected() -> None:
    raw = _valid_report_dict()
    raw["self_assessment"]["reason"] = "  "
    try:
        validate_report(raw)
        raise AssertionError("理由が空の自己評価欄が通ってしまった")
    except ContractFormatError:
        pass


def test_fusen_confidence_out_of_range_is_rejected() -> None:
    raw = _valid_report_dict()
    raw["fusen_list"][0]["confidence"] = 1.5
    try:
        validate_report(raw)
        raise AssertionError("確信度が範囲外の付箋が通ってしまった")
    except ContractFormatError:
        pass


def test_unreadable_fusen_kind_is_not_rejected_by_format_check() -> None:
    """未知の付箋種類そのものは書式検査の対象外。§2.1: 読めない種類は保管庫へ回す。"""
    raw = _valid_report_dict()
    raw["fusen_list"][0]["kind"] = "未来の付箋"
    report = validate_report(raw)
    assert report.fusen_list[0].kind == "未来の付箋"


def test_lenient_validation_discards_only_broken_fusen() -> None:
    """§2.5 関所①: 壊れた付箋は個別に破棄し、報告書全体は棄却しない"""
    raw = _valid_report_dict()
    raw["fusen_list"].append({
        "kind": "マスター観測",
        "version": 1,
        "content": {"note": "元気そう"},
        "confidence": 2.0,  # 範囲外 → この付箋だけ壊れている
    })
    report, discarded = validate_report_lenient(raw)
    assert report.reply == "おかえりなさい"
    assert len(report.fusen_list) == 1
    assert len(discarded) == 1
    assert discarded[0]["kind"] == "マスター観測"


def test_lenient_validation_still_rejects_missing_self_assessment() -> None:
    """自己評価欄が空の報告書は全体を棄却する（§3.4）"""
    raw = _valid_report_dict()
    del raw["self_assessment"]
    try:
        validate_report_lenient(raw)
        raise AssertionError("自己評価欄なしの報告書が通ってしまった")
    except ContractFormatError:
        pass


def main() -> None:
    tests = [
        test_valid_report_passes_format_check,
        test_lenient_validation_discards_only_broken_fusen,
        test_lenient_validation_still_rejects_missing_self_assessment,
        test_missing_self_assessment_is_rejected,
        test_empty_self_assessment_reason_is_rejected,
        test_fusen_confidence_out_of_range_is_rejected,
        test_unreadable_fusen_kind_is_not_rejected_by_format_check,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
