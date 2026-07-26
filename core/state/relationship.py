"""関係状態: 親密度・最近のマスターの調子・続いている話題。設計書 §2.6（ゆっくり変化）"""

from __future__ import annotations

from datetime import datetime, timezone

# 2026-07-26 B1: ongoing_topicsは追記のみで除去経路が無いため、永続化すると際限なく
# 膨張する（architecture-reviewer指摘）。直近N件だけを保持し古いものから捨てる。
MAX_ONGOING_TOPICS = 5


class RelationshipState:
    def __init__(self) -> None:
        self.intimacy: float = 0.0
        self.recent_master_mood: str | None = None
        # 2026-07-26 B1: 観測の取得時刻（時刻の錨）。無いまま永続化・パック提示すると、
        # 鮮度切れの観測を「今の様子」として読ませてしまう（architecture-reviewer指摘）。
        self.recent_master_mood_at: datetime | None = None
        self.ongoing_topics: list[str] = []
        # RelationshipState自身は時計を持たない方針（EmotionState.current_dayと同型）。
        # 値は外から与える（core/runtime.py:turn_routedが毎ターン冒頭で設定する）。
        self.current_turn_at: datetime | None = None

    def observe(self, master_mood: str | None = None, topic: str | None = None) -> None:
        if master_mood is not None:
            self.recent_master_mood = master_mood
            self.recent_master_mood_at = self.current_turn_at or datetime.now(timezone.utc)
        if topic is not None and topic not in self.ongoing_topics:
            self.ongoing_topics.append(topic)
            if len(self.ongoing_topics) > MAX_ONGOING_TOPICS:
                self.ongoing_topics = self.ongoing_topics[-MAX_ONGOING_TOPICS:]
