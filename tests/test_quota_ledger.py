"""残弾台帳のテスト。設計書v2 §2.6, §2.4(夜間放出), §3.2④残弾チェック"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.routing.quota_ledger import QuotaLedger


def test_unlimited_quota_is_always_available() -> None:
    ledger = QuotaLedger()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert ledger.can_use("aurora", daily_quota=-1, per_minute_quota=-1, now=now)
    for _ in range(1000):
        ledger.record_use("aurora", now=now)
    assert ledger.can_use("aurora", daily_quota=-1, per_minute_quota=-1, now=now)


def test_daily_quota_is_exhausted_after_use() -> None:
    ledger = QuotaLedger()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    for _ in range(3):
        assert ledger.can_use("gemini_flash", daily_quota=3, per_minute_quota=100, now=now)
        ledger.record_use("gemini_flash", now=now)
    assert not ledger.can_use("gemini_flash", daily_quota=3, per_minute_quota=100, now=now)


def test_daily_quota_resets_on_new_day() -> None:
    ledger = QuotaLedger()
    day1 = datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 1, 2, 0, 5, tzinfo=timezone.utc)
    for _ in range(3):
        ledger.record_use("gemini_flash", now=day1)
    assert not ledger.can_use("gemini_flash", daily_quota=3, per_minute_quota=100, now=day1)
    assert ledger.can_use("gemini_flash", daily_quota=3, per_minute_quota=100, now=day2), "日付が変わればリセットされるべき"


def test_per_minute_quota_blocks_burst_usage() -> None:
    ledger = QuotaLedger()
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(2):
        assert ledger.can_use("gemini_lite", daily_quota=500, per_minute_quota=2, now=now)
        ledger.record_use("gemini_lite", now=now)
    assert not ledger.can_use("gemini_lite", daily_quota=500, per_minute_quota=2, now=now)

    later = now + timedelta(seconds=61)
    assert ledger.can_use("gemini_lite", daily_quota=500, per_minute_quota=2, now=later), "1分経てば再度使えるべき"


def main() -> None:
    tests = [
        test_unlimited_quota_is_always_available,
        test_daily_quota_is_exhausted_after_use,
        test_daily_quota_resets_on_new_day,
        test_per_minute_quota_blocks_burst_usage,
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
