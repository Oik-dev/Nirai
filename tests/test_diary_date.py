"""日記対象日メタデータ解決の単体テスト。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.memory.diary_date import parse_memory_metadata, resolve_diary_target_date


def test_resolve_prefers_target_date_over_heuristic() -> None:
    assert (
        resolve_diary_target_date(
            created_at="2026-07-21T07:00:00+09:00",
            metadata={"target_date": "2026-07-19"},
        )
        == "2026-07-19"
    )


def test_resolve_falls_back_to_legacy_date_key() -> None:
    assert (
        resolve_diary_target_date(
            created_at="2026-07-21T07:00:00+09:00",
            metadata={"date": "2026-03-21"},
        )
        == "2026-03-21"
    )


def test_resolve_heuristic_for_day_boundary_created_at() -> None:
    assert (
        resolve_diary_target_date(created_at="2026-07-21T07:00:00+09:00", metadata=None)
        == "2026-07-20"
    )


def test_parse_memory_metadata_tolerant() -> None:
    assert parse_memory_metadata(None) == {}
    assert parse_memory_metadata("{") == {}
    assert parse_memory_metadata('{"target_date": "2026-07-20"}') == {"target_date": "2026-07-20"}
