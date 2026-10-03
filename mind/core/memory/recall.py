"""思い出す（docs/plans/長期記憶の作り直し.md §7）。

記憶の活性 A = 基礎活性（強さ。strength.py）＋ 文脈からの拡散活性 ＋ 意味の近さ ＋ 時の手がかり ＋ ゆらぎ。
閾値を超えたものだけが浮かぶ。何も超えなければ黙る。

- 文脈は、今の発言と直前の会話。今の一言だけでは探さない。
- 拡散活性は、文脈に出てきた手がかりから、つながったページへ流れる（ACT-R）。手がかりは2種類：
  概念（ページの整理でつないだもの。呼び名の辞書で別名もそろえる）と、言葉（ページや記録にそのまま出てくる言葉）。
  たくさんのページとつながる手がかりほど、1本あたりは弱い（ファン効果：S − ln(つながりの数)）。だから「マスター」は
  何も目立たせず、「高野漁港」は数ページだけを強く呼び起こす。手がかりの注意は、手がかりの数で分け合う。
- 意味の近さは bge-m3。ページの題と要点・本人の文・記録の原文のうち、一番近い一切れで測る。
- 時の手がかり（time_cue.py）が読めたら、その時期のページを持ち上げる。
- 思い出そうとしているとき（「覚えてる？」など）は、閾値が下がり、浮かぶ数の上限も上がる（自然に浮かぶ／思い出そうとする）。
- 出来事は、その日が終わって眠ったあと（次の Serina 日の始まり。朝7時）から思い出せる。眠りの間にページになるため。
  それまでの話は、記憶ではなく、手元の会話の流れにある。

渡し方：はっきり思い出したもの（活性が閾値を大きく超えたもの）は本文まで、うっすらは題と要点だけ。いつのことかも添える。
"""

from __future__ import annotations

import math
import random
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mind.core.config import DEFAULT_THRESHOLDS_PATH
from mind.core.memory.index import IndexedPage, MemoryIndex, normalize
from mind.core.memory.strength import StrengthParams, base_level, encoding_boost, ranks
from mind.core.memory.time_cue import Period, read_period
from mind.core.state.serina_day import SERINA_DAY_HOUR

JST = ZoneInfo("Asia/Tokyo")

# 思い出そうとしている言い方（Tulving の retrieval mode）。「〜っけ」は日本語で思い出そうとするときの言い方そのもの
INTENT = re.compile(
    r"っけ|覚えて|おぼえて|憶えて|思い出(?!した)|話したこと|言ったこと|話したよね|言ってたよね|前はどう|"
    r"あの時|あのとき|あの日|あの頃|あのころ|この前の|前に(話|言)|いつだっけ|どんな(話|こと)を?した"
)
# 言葉の手がかり：漢字2字以上・カタカナ3字以上・英数字3字以上のつながり
_TERM = re.compile(r"[一-龥々〆ヵヶ]{2,}|[ァ-ヴー]{3,}|[a-z0-9][a-z0-9._-]{2,}")
# 時の言葉は時の手がかりで読むので、言葉の手がかりにはしない
_TIME_WORDS = {"昨日", "今日", "明日", "去年", "昨年", "今年", "先週", "先月", "今月", "来月", "最近", "最初", "一昨日", "今夜", "昨夜"}
_SINGLE = re.compile(r"[一-龥]")


@dataclass(frozen=True)
class Cue:
    utterance: str
    recent: tuple[tuple[str, str], ...]  # (話者, 文)。古い順
    now: datetime


@dataclass(frozen=True)
class Remembered:
    page_id: str
    text: str  # 住人の手元に渡す文
    when: date | None
    evidence: str  # このページが拠っている記録の原文
    activation: float
    vivid: bool


@dataclass(frozen=True)
class RecallParams:
    """値は config/thresholds.toml の [activation]（load_recall_params）。それぞれの意味はそちらに書いてある。"""

    threshold: float
    intent_relief: float
    max_spontaneous: int
    max_intentional: int
    spread_strength: float
    spread_weight: float
    recent_weight: float
    literal_share: float
    semantic_weight: float
    semantic_center: float
    recent_turns: int
    time_weight: float
    noise: float
    vivid_margin: float
    vivid_chars: int
    faint_chars: int
    strength: StrengthParams


def load_recall_params(path: Path = DEFAULT_THRESHOLDS_PATH) -> RecallParams:
    """config/thresholds.toml の [activation] を読む。強さのツマミ（StrengthParams）も同じ節。足りなくても余っても断る。"""
    with Path(path).open("rb") as f:
        section = tomllib.load(f).get("activation", {})
    recall_keys = {f.name for f in fields(RecallParams)} - {"strength"}
    strength_keys = {f.name for f in fields(StrengthParams)}
    missing = (recall_keys | strength_keys) - set(section)
    unknown = set(section) - recall_keys - strength_keys
    if missing or unknown:
        raise ValueError(f"[activation] の項目が合わない。足りない: {sorted(missing)}、知らない: {sorted(unknown)}")
    strength = StrengthParams(**{k: v for k, v in section.items() if k in strength_keys})
    return RecallParams(**{k: v for k, v in section.items() if k in recall_keys}, strength=strength)


def _jst(at: datetime) -> datetime:
    return at.astimezone(JST)


def recallable_from(end: datetime) -> datetime:
    """その出来事を思い出せるようになる時刻：終わった日の次の Serina 日の始まり（眠りの間にページになる）。"""
    local = _jst(end)
    day = local.date() - timedelta(days=1) if local.hour < SERINA_DAY_HOUR else local.date()
    return datetime(day.year, day.month, day.day, SERINA_DAY_HOUR, tzinfo=JST) + timedelta(days=1)


def ago(then: date, now: date) -> str:
    days = (now - then).days
    if days <= 0:
        return "今日"
    if days == 1:
        return "昨日"
    if days < 7:
        return f"{days}日前"
    if days < 31:
        return f"{days // 7}週間前"
    months = round(days / 30.4)
    if months < 12:
        return f"{months}か月前"
    years, rest = divmod(months, 12)
    return f"{years}年前" if rest == 0 else f"{years}年{rest}か月前"


class Recaller:
    def __init__(
        self,
        index: MemoryIndex,
        *,
        embed: Callable[[str], list[float]],
        params: RecallParams | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.index = index
        self.embed = embed
        self.params = params or load_recall_params()
        self.rng = rng or random.Random()
        dated = [p.start for p in index.pages.values() if p.start is not None]
        self._first = min(dated) if dated else None  # 時刻のないページ（継承記憶）は、記録の始まりに覚えたものとして扱う
        self._surfaces = sorted(index.surfaces.items(), key=lambda kv: -len(kv[0]))
        written = {pid: page for pid, page in index.pages.items() if page.written}
        importance = ranks({pid: page.importance for pid, page in written.items()})
        arousal = ranks({pid: page.arousal for pid, page in written.items()})
        self._boost = {
            pid: encoding_boost(importance.get(pid, 0.5), arousal.get(pid, 0.5), self.params.strength) for pid in index.pages
        }

    # --- 手がかり -------------------------------------------------------------

    def concepts_in(self, text: str) -> set[str]:
        norm = normalize(text)
        found: set[str] = set()
        for surface, concept in self._surfaces:
            if not surface:
                continue
            if len(surface) == 1:
                if _SINGLE.match(surface) and re.search(rf"(?<![一-龥]){re.escape(surface)}(?![一-龥])", norm):
                    found.add(concept)
            elif surface in norm:
                found.add(concept)
        return found

    def terms_in(self, text: str, concepts: set[str]) -> set[str]:
        covered = [normalize(c) for c in concepts]
        terms = set()
        for term in _TERM.findall(normalize(text)):
            if term in _TIME_WORDS or any(term in c or c in term for c in covered):
                continue
            terms.add(term)
        return terms

    def _spread(self, text: str, weight: float) -> dict[str, float]:
        """text の手がかりから、ページへ流れる活性。"""
        p = self.params
        concepts = self.concepts_in(text)
        terms = self.terms_in(text, concepts)
        sources: list[tuple[float, float, set[str]]] = []  # (注意の割合, 強さ, つながったページ)
        for concept in concepts:
            linked = {pid for pid, page in self.index.pages.items() if concept in page.concepts}
            sources.append((1.0, p.spread_strength - math.log(len(linked)), linked) if linked else (1.0, 0.0, set()))
        for term in terms:
            df = self.index.document_frequency(term)
            if df:
                linked = {pid for pid, page in self.index.pages.items() if term in page.searchable}
                sources.append((p.literal_share, p.spread_strength - math.log(df), linked))
        total = sum(share for share, _, _ in sources)
        if total <= 0:
            return {}
        out: dict[str, float] = {}
        for share, strength, linked in sources:
            if strength <= 0:
                continue
            for pid in linked:
                out[pid] = out.get(pid, 0.0) + weight * share / total * strength
        return out

    def _similarity(self, cue: Cue) -> dict[str, float]:
        texts = [cue.utterance]
        recent = [text for _, text in cue.recent[-self.params.recent_turns :]]
        if recent:
            texts.append("\n".join(recent + [cue.utterance]))
        vectors = []
        for text in texts:
            v = self.embed(text)
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v])
        best: dict[str, float] = {}
        for passage in self.index.passages:
            sim = max(sum(a * b for a, b in zip(v, passage.vector)) for v in vectors)
            if sim > best.get(passage.page_id, -1.0):
                best[passage.page_id] = sim
        return best

    # --- 活性 -------------------------------------------------------------------

    def _available(self, page: IndexedPage, now: datetime) -> bool:
        return page.end is None or recallable_from(page.end) <= now

    def base(self, page: IndexedPage, now: datetime) -> float:
        encoded = page.end or page.start or self._first
        if encoded is None:
            return 0.0
        # 思い出した時刻の並び（想起の記録）は、まだない（計画書§15）。今は出来事の時刻だけで強さが決まる
        return base_level(encoded, self._boost[page.id], [], now, self.params.strength)

    def activations(self, cue: Cue) -> list[tuple[IndexedPage, float, dict[str, float]]]:
        """浮かびうるすべてのページと、その活性・内訳。活性の高い順。"""
        p = self.params
        spread = self._spread(cue.utterance, p.spread_weight)
        if cue.recent:
            context = "\n".join(text for _, text in cue.recent)
            for pid, value in self._spread(context, p.recent_weight).items():
                spread[pid] = spread.get(pid, 0.0) + value
        similarity = self._similarity(cue)
        period = read_period(cue.utterance, _jst(cue.now), earliest=_jst(self._first).date() if self._first else None)
        out = []
        for page in self.index.pages.values():
            if not self._available(page, cue.now):
                continue
            parts = {
                "base": self.base(page, cue.now),
                "spread": spread.get(page.id, 0.0),
                "meaning": p.semantic_weight * (similarity.get(page.id, 0.0) - p.semantic_center),
                "time": self._time(page, period),
                "noise": self._noise(),
            }
            out.append((page, sum(parts.values()), parts))
        return sorted(out, key=lambda x: -x[1])

    def _time(self, page: IndexedPage, period: Period | None) -> float:
        if period is None or page.start is None or not period.contains(_jst(page.start).date()):
            return 0.0
        return self.params.time_weight if period.sharp else self.params.time_weight / 2

    def _noise(self) -> float:
        if self.params.noise <= 0:
            return 0.0
        u = min(max(self.rng.random(), 1e-9), 1 - 1e-9)
        return self.params.noise * math.log(u / (1 - u))

    # --- 思い出す ---------------------------------------------------------------

    def recall(self, cue: Cue) -> list[Remembered]:
        p = self.params
        intent = bool(INTENT.search(cue.utterance))
        threshold = p.threshold - (p.intent_relief if intent else 0.0)
        limit = p.max_intentional if intent else p.max_spontaneous
        chosen = [(page, a) for page, a, _ in self.activations(cue) if a >= threshold][:limit]
        now = _jst(cue.now).date()
        return [self._remembered(page, a, a - threshold >= p.vivid_margin, now, cue) for page, a in chosen]

    def _remembered(self, page: IndexedPage, activation: float, vivid: bool, today: date, cue: Cue) -> Remembered:
        p = self.params
        day = _jst(page.start).date() if page.start else None
        when = f"{day.year}年{day.month}月{day.day}日、{ago(day, today)}" if day else "いつのことかは分からない"
        title = page.title or _first_line(page.body or page.evidence)
        head = f"（{when}）{title}"
        if vivid:
            content = page.body if page.kind == "episode" and page.written else self._best_passage(page, cue)
            text = f"{head}\n{page.gist}\n{_clip(content, p.vivid_chars)}" if page.gist else f"{head}\n{_clip(content, p.vivid_chars)}"
        else:
            text = f"{head}：{_clip(page.gist or page.body or page.evidence, p.faint_chars)}"
        return Remembered(page.id, text, day, page.evidence, activation, vivid)

    def _best_passage(self, page: IndexedPage, cue: Cue) -> str:
        """はっきり思い出した日記・覚え書きは、今の話に一番近い一切れを渡す。"""
        passages = [x for x in self.index.passages if x.page_id == page.id and x.role != "head"]
        if not passages:
            return page.body or page.evidence
        v = self.embed(cue.utterance)
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return max(passages, key=lambda x: sum(a * b / norm for a, b in zip(v, x.vector))).text


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")[:40]


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "……"
