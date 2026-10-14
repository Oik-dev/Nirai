"""Interests are derived from indexed episodes, never stored as mutable state.

Master's enthusiasm can begin an interest; Serina's joyful experience sustains it.
Both traces are rebuilt from pages and fade with time on every read.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from mind.core.memory.index import MemoryIndex

HEAT_WEIGHTS = {"いいえ": 0.0, "少し": 0.5, "とても": 1.0}


@dataclass(frozen=True)
class Interest:
    name: str
    score: float
    source: str  # Masterの熱 or 自分の楽しさ


@dataclass(frozen=True)
class InterestParams:
    decay: float = 0.5
    top: int = 5
    neighbors: int = 3


def joy_weight(affect: dict[str, float] | None, sources: tuple[str, ...]) -> float:
    """Only a letter or research episode can leave a trace of Serina's joy."""
    if not affect or not any(s.startswith(("lifelog/letters/", "lifelog/explore/")) for s in sources):
        return 0.0
    v = max(0.0, min(1.0, float(affect.get("valence", 0.0))))
    a = max(0.0, min(1.0, float(affect.get("arousal", 0.0))))
    return v * (0.5 + 0.5 * a)


def interests(index: MemoryIndex, now: datetime, params: InterestParams = InterestParams()) -> tuple[list[Interest], list[str]]:
    """ACT-R fading x IDF rarity; nearby terms have the fewest supporting pages."""
    pages = [p for p in index.pages.values() if p.end is None or p.end <= now]
    total = len(pages)
    if total < 2:
        return [], []
    frequency: dict[str, int] = {}
    support: dict[str, tuple[float, float]] = {}
    cooccurrence: dict[str, set[str]] = {}
    for page in pages:
        days = max(1.0, (now - (page.end or page.start or now)).total_seconds() / 86400)
        fading = days ** (-params.decay)
        for concept in page.concepts:
            frequency[concept] = frequency.get(concept, 0) + 1
            master, own = support.get(concept, (0.0, 0.0))
            support[concept] = (master + (page.master_heat if page.written else 0.0) * fading,
                                own + (page.own_joy if page.written else 0.0) * fading)
            cooccurrence.setdefault(concept, set()).update(page.concepts - {concept})
    ranked: list[Interest] = []
    for concept, (master, own) in support.items():
        rarity = math.log(total / frequency[concept])
        score = (master + own) * rarity
        if score > 0:
            ranked.append(Interest(concept, score, "自分で楽しかった" if own > master else "マスターが夢中"))
    ranked.sort(key=lambda entry: (-entry.score, entry.name))
    top = ranked[: params.top]
    chosen = {entry.name for entry in top}
    nearby = set().union(*(cooccurrence[entry.name] for entry in top)) if top else set()
    nearby.difference_update(chosen)
    neighbors = sorted(nearby, key=lambda name: (frequency[name], name))[: params.neighbors]
    return top, neighbors


def mark_interest_names(index: MemoryIndex, now: datetime, marks, judge) -> bool:
    """Judge every unmarked concept name together; failure leaves all unknown at home."""
    top, neighbors = interests(index, now)
    names = list(dict.fromkeys([*(item.name for item in top), *neighbors]))
    unknown = marks.unmarked(names)
    if not unknown:
        return True
    try:
        judged = judge(unknown)
        marks.mark(unknown, judged)
        return True
    except Exception:
        return False


def visible_interest_names(index: MemoryIndex, now: datetime, *, marks=None, cloud: bool = False) -> tuple[list[Interest], list[str]]:
    """No unmarked or home-only concept name enters a cloud background."""
    top, neighbors = interests(index, now)
    if not cloud:
        return top, neighbors
    if marks is None:
        return [], []
    return [item for item in top if not marks.home(item.name)], [name for name in neighbors if not marks.home(name)]


def interest_background(index: MemoryIndex, now: datetime, *, marks=None, cloud: bool = False) -> str:
    top, near = visible_interest_names(index, now, marks=marks, cloud=cloud)
    if not top:
        return ""
    lines = ["【いま興味のあること】"]
    lines.extend(f"- {item.name}（{item.source}）" for item in top)
    if near:
        lines.append("隣：" + "、".join(near))
    return "\n".join(lines)
