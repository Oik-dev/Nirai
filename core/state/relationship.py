"""関係状態: 親密度・最近のマスターの調子・続いている話題。設計書 §2.6（ゆっくり変化）"""

from __future__ import annotations


class RelationshipState:
    def __init__(self) -> None:
        self.intimacy: float = 0.0
        self.recent_master_mood: str | None = None
        self.ongoing_topics: list[str] = []

    def observe(self, master_mood: str | None = None, topic: str | None = None) -> None:
        if master_mood is not None:
            self.recent_master_mood = master_mood
        if topic is not None and topic not in self.ongoing_topics:
            self.ongoing_topics.append(topic)
