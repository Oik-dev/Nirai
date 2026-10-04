"""住人の長期記憶（docs/plans/長期記憶の作り直し.md）。精神の会話と眠りは、この口から記憶に触れる。

記憶の正本は、イデアの memory/ のページ（本人の言葉）と lifelog/ の記録。索引（data/memory_index.db）は
そこからいつでも作り直せる写しで、ここで手元に読み込んで使う。

- 思い出す（recall）：会話のたびに、手がかりから浮かんだページを返す（recall.py）。浮かんだものは想起の記録
  （lifelog/recall/）に残り、そのページを強くする。
- 眠り（sleep.py）が新しいページを書いたら、索引を作り直して読み直す（rebuild_index）。
- 今の自分（waking）：眠り終えて目覚めた本人が書いた、いちばん新しい今の自分（waking.py）。
- 忘れる（forget_lines）：Masterが記録から消した発言に拠っていたページを外す。同じ出来事の残りの発言は、
  どのページにも拠られていない記録に戻るので、次の眠りで本人が思い出し直す（書き直す）。
"""

from __future__ import annotations

import random
import threading
from collections.abc import Callable, Collection
from pathlib import Path

from mind.core.idea import Idea
from mind.core.lifelog import RecallLog
from mind.core.memory.index import MemoryIndex, build_index, normalize
from mind.core.memory.page import load_pages, write_page
from mind.core.memory.recall import Cue, RecallParams, Recaller, Remembered
from mind.core.memory.structure import conversation_positions, link_neighbors
from mind.core.memory.waking import Waking, latest_waking

MAX_KNOWN_NAMES = 30  # 区切るときに見せる、これまでの概念の名前の数


class Memory:
    def __init__(
        self,
        idea: Idea,
        *,
        embed: Callable[[str], list[float]],
        embed_model: str,
        params: RecallParams | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.idea = idea
        self.embed = embed
        self.embed_model = embed_model
        self.params = params
        self.rng = rng or random.Random()
        self.recall_log = RecallLog(idea.recall)
        self._lock = threading.Lock()  # ページと索引を書き換える仕事（眠り・忘れる）を1つずつにする
        self.reload()

    def reload(self) -> None:
        """索引と想起の記録を読み直す。読み終えたものと入れ替えるので、会話は止まらない。"""
        path = self.idea.memory_index
        index = MemoryIndex.load(path) if path.exists() else MemoryIndex({}, [], {})
        self._recaller = Recaller(
            index, embed=self.embed, params=self.params, rng=self.rng, recalls=self.recall_log.times()
        )

    @property
    def index(self) -> MemoryIndex:
        return self._recaller.index

    def recall(self, cue: Cue, *, already: Collection[str] = ()) -> list[Remembered]:
        recaller = self._recaller
        remembered = recaller.recall(cue, already=already)
        for m in remembered:
            self.recall_log.append(ts=cue.now, page=m.page_id, activation=m.activation, vivid=m.vivid, intent=m.intent)
            recaller.strengthen(m.page_id, cue.now)
        return remembered

    def waking(self) -> Waking | None:
        """今の自分（いちばん新しい目覚め。core/memory/waking.py）。会話のたびに読むので、目覚めればすぐ変わる。"""
        return latest_waking(self.idea.memory)

    # --- 整理のための口（眠りが使う） -------------------------------------------------

    def known_in(self, text: str) -> list[str]:
        """text に出てくる、これまでの記憶の概念の名前。"""
        return sorted(self._recaller.concepts_in(text))[:MAX_KNOWN_NAMES]

    def name_of(self, name: str) -> str:
        """概念の名前を、呼び名の辞書とこれまでの名前の代表にそろえる。"""
        return self.index.surfaces.get(normalize(name), name)

    def rebuild_index(self, *, progress: Callable[[str], None] = print) -> None:
        build_index(
            self.idea.memory,
            self.idea.conversation,
            self.idea.name,
            self.idea.memory_index,
            embed=self.embed,
            model=self.embed_model,
            progress=progress,
        )
        self.reload()

    @property
    def pages_lock(self) -> threading.Lock:
        """ページを書き換える間に持つ錠（眠りと、忘れることが重ならないように）。"""
        return self._lock

    # --- 忘れる ---------------------------------------------------------------------

    def forget_lines(self, positions: Collection[tuple[str, int]]) -> list[str]:
        """記録から消された行（日のファイル名, 行番号）に拠っていたページを外す。外したページの id を返す。"""
        erased = set(positions)
        if not erased:
            return []
        with self._lock:
            pages = load_pages(self.idea.memory)
            gone = [page for page in pages if conversation_positions(page) & erased]
            if not gone:
                return []
            for page in gone:
                page.path_in(self.idea.memory).unlink()
            relink(self.idea.memory)
            self.rebuild_index(progress=lambda _msg: None)
        return [page.id for page in gone]


def relink(memory_dir: Path) -> int:
    """すべてのページの前後のつながりを、時刻の順に結び直す。変わったページだけ書き直し、その数を返す。"""
    pages = load_pages(memory_dir)
    changed = 0
    for before, after in zip(pages, link_neighbors(pages)):
        if (before.prev, before.next) != (after.prev, after.next):
            write_page(memory_dir, after)
            changed += 1
    return changed
