"""住人の記憶（出来事のページと、ACT-Rの活性で思い出す。core/memory/recall.py）を、テストの口で答えさせる。
ツマミは、渡さなければ本番と同じ config/thresholds.toml の [activation]。

索引（イデアの data/memory_index.db）は読むだけ。想起の記録も書かない（問題の順番で結果が変わらないように）。
ゆらぎは問題ごとに種を決めて、同じ問題には同じ答えを返す。now より後に終わったページは、思い出さない。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path

from mind.core.idea import Idea
from mind.core.memory.index import MemoryIndex
from mind.core.memory.recall import Cue as RecallCue
from mind.core.memory.recall import RecallParams, Recaller
from mind.memory_test.memory import Cue, Recalled


class EpisodicMemory:
    name = "episodic"

    def __init__(
        self,
        idea: Path,
        *,
        embed: Callable[[str], list[float]],
        params: RecallParams | None = None,
    ) -> None:
        cache: dict[str, list[float]] = {}

        def cached(text: str) -> list[float]:
            if text not in cache:
                cache[text] = embed(text)
            return cache[text]

        self.recaller = Recaller(
            MemoryIndex.load(Idea(idea).memory_index),
            embed=cached,
            params=params,
            rng=random.Random(0),
        )

    def recall(self, cue: Cue) -> list[Recalled]:
        self.recaller.rng.seed(f"{cue.now.isoformat()}\0{cue.utterance}")
        remembered = self.recaller.recall(
            RecallCue(cue.utterance, tuple((turn.speaker, turn.text) for turn in cue.recent), cue.now)
        )
        return [Recalled(text=m.text, when=m.when, evidence=m.evidence) for m in remembered]
