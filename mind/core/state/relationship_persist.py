"""関係状態の永続化。emotion_persist.py と同型（JSON・UTF-8・tmp+os.replace）。

`RelationshipState`(core/state/relationship.py)はI/Oを持たせない方針のため、
アプリ/Core境界のこのモジュールが担う（2026-07-26 B1）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mind.core.state.relationship import RelationshipState
from mind.core.idea import DATA_DIR

DEFAULT_RELATIONSHIP_STATE_PATH = DATA_DIR / "relationship_state.json"


def load_relationship_state(path: Path | str = DEFAULT_RELATIONSHIP_STATE_PATH) -> dict:
    """recent_master_mood, recent_master_mood_at(iso or None), ongoing_topics, intimacy を読む。

    ファイルが無ければ初期値。
    """
    target = Path(path)
    if not target.exists():
        return {
            "recent_master_mood": None,
            "recent_master_mood_at": None,
            "ongoing_topics": [],
            "intimacy": 0.0,
        }
    data = json.loads(target.read_text(encoding="utf-8"))
    return {
        "recent_master_mood": data.get("recent_master_mood"),
        "recent_master_mood_at": data.get("recent_master_mood_at"),
        "ongoing_topics": data.get("ongoing_topics", []),
        "intimacy": data.get("intimacy", 0.0),
    }


def save_relationship_state(
    path: Path | str,
    *,
    recent_master_mood: str | None,
    recent_master_mood_at: datetime | None,
    ongoing_topics: list[str],
    intimacy: float,
) -> None:
    """アトミック保存（一時ファイル+os.replace。emotion_persist.pyと同じ理由）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recent_master_mood": recent_master_mood,
        "recent_master_mood_at": (
            recent_master_mood_at.isoformat() if recent_master_mood_at is not None else None
        ),
        "ongoing_topics": ongoing_topics,
        "intimacy": intimacy,
    }
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)


def apply_loaded_to_relationship(relationship: RelationshipState, data: dict) -> None:
    """load_relationship_state の結果を RelationshipState へ反映する。"""
    relationship.recent_master_mood = data.get("recent_master_mood")
    raw_at = data.get("recent_master_mood_at")
    observed_at: datetime | None = None
    if raw_at:
        try:
            observed_at = datetime.fromisoformat(raw_at)
        except ValueError:
            observed_at = None
        else:
            # 2026-07-26 B1是正(serina-code-reviewer指摘I-4): JSONはマスターが手で
            # 開ける外部ファイルであり、tzオフセット無しの文字列が書かれうる。naiveな
            # datetimeを本番のaware `now`と減算するとTypeErrorになり、パック組み立てが
            # 毎ターン例外に落ちる（沈黙しない設計思想に反する）。UTCとして補う。
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)
    relationship.recent_master_mood_at = observed_at
    topics = data.get("ongoing_topics", [])
    relationship.ongoing_topics = list(topics) if isinstance(topics, list) else []
    try:
        relationship.intimacy = float(data.get("intimacy", 0.0))
    except (TypeError, ValueError):
        relationship.intimacy = 0.0


def snapshot_from_relationship(relationship: RelationshipState) -> dict:
    """save 用の値を RelationshipState から生成する（recent_master_mood_at は datetime のまま）。"""
    return {
        "recent_master_mood": relationship.recent_master_mood,
        "recent_master_mood_at": relationship.recent_master_mood_at,
        "ongoing_topics": list(relationship.ongoing_topics),
        "intimacy": relationship.intimacy,
    }


def save_relationship_from_state(path: Path | str, relationship: RelationshipState) -> None:
    snap = snapshot_from_relationship(relationship)
    save_relationship_state(
        path,
        recent_master_mood=snap["recent_master_mood"],
        recent_master_mood_at=snap["recent_master_mood_at"],
        ongoing_topics=snap["ongoing_topics"],
        intimacy=snap["intimacy"],
    )
