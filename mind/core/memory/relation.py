"""関係（world/docs/plans/暮らしの循環.md の縦切り3。docs/plans/長期記憶の作り直し.md §8）。その人とのことを、本人が眠りの間に育てる。

眠りの間に、本人の脳がその日の記憶のページを読み返して、その人について変わったことと新しく分かったことだけを書き足す
（書くのは本人の脳。人格を渡して、本人として書く）。
- 今の関係：その人との今の関係（本人の言葉。変わったときだけ書く）。前のものも残るので「昔はこうだった」と話せる。
- 転機：関係の形が変わった日と、何が変わったか（関係の年表）。
- 書き留めていること：その人について知っていること・その人についての見方・その人との約束。どれも一文で、いつからかが残る。
  変わったら古いものに終わりの日を付けて残す（Zep）。見方の確かさは、その後の日に確かめられたか・揺らいだかの数から
  仕組みが決める（脳に数は書かせない）。
- 日付：約束や予定の日（その日だけ。2026-10-12）と、誕生日や記念日（毎年。--03-07）。その日が近づいたら目覚めの材料になり、
  文脈パックの先頭に載る。その日が来て、マスターがまだ来ていなければ、本人から話しかけに行く（Pulse の day。core/chores/idle_policy.py）。

1日につき1つのファイルに書き足す（memory/people/<その人>/<Serina日>.md）。前の日のファイルは書き換えない。
今の関係や、何がまだ有効かは、ファイルを古い順に読んで毎回計算する（状態を別に持たない）。何が済んだかもファイルから分かる：
いちばん新しいファイルの日より後の日だけを書き足す（関係は日の順に積み重なるので、前の日には戻らない）。
"""

from __future__ import annotations

import calendar
import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

from mind.core.memory.page import Page, _toml_value
from mind.core.memory.writing import _SELF, Ask, WordsRejected, _ask_until_valid, _field, _text

PEOPLE_DIR = "people"  # memory/ の下の置き場所
KNOWS, THINKS, PROMISED = "知っている", "思う", "約束"  # 書き留めることの種類：知っていること・見方・約束
CONFIRMED, SHAKEN, NO_LONGER, KEPT = "また確かめた", "揺らいだ", "もう違う", "果たした"
CHANGED = "変わった"  # 新しく書き留めたことで古くなった（replaces）
RELATION_MIN, RELATION_MAX = 40, 400
TURNING_MAX = 80
THING_MAX = 80
MAX_NEW, MAX_TOUCHED = 3, 5  # 1日に書き足せる数（どれを書くかは脳、量は仕組み）
SHOWN_THINGS = 20  # 書き足すときに見せる、まだ有効な書き留め（最近確かめたものから）
SHOWN_TURNINGS = 3
DAY_CHARS = 3000  # 材料にする、その日のページの長さの合計（ページが多い日は1ページを短くして、ここに収める）
PAGE_VIEW = 600  # 材料にするページ1つの長さ（ページが少ない日）
IN_PACK = 600  # 文脈パックに載せる長さ（今の自分と同じく、短く）
COMING_DAYS = 3  # 近いうちの日付（今日を含めて、この日数先まで）
WHEN_RANGE = timedelta(days=3 * 366)  # その日だけの日付として受け取る範囲（書き足す日の前後）
_ONCE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEARLY = re.compile(r"^--(\d{2})-(\d{2})$")
_FENCE = "+++"

RELATION_SCHEMA = {
    "type": "object",
    "properties": {
        "relation": {"type": "string"},
        "turning": {"type": "string"},
        "new": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": [KNOWS, THINKS, PROMISED]},
                    "text": {"type": "string"},
                    "when": {"type": "string"},
                    "replaces": {"type": "integer"},
                },
                "required": ["kind", "text", "when", "replaces"],
            },
        },
        "changed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "no": {"type": "integer"},
                    "how": {"type": "string", "enum": [CONFIRMED, SHAKEN, NO_LONGER, KEPT]},
                },
                "required": ["no", "how"],
            },
        },
    },
    "required": ["relation", "turning", "new", "changed"],
}


@dataclass(frozen=True)
class Added:
    """その日に新しく書き留めたこと。replaces は、これで古くなった書き留めの id（なければ空）。
    when は日付（2026-10-12 はその日だけ、--03-07 は毎年。なければ空）。"""

    id: str
    kind: str
    text: str
    replaces: str = ""
    when: str = ""


@dataclass(frozen=True)
class Touched:
    """その日の出来事で、確かめられたり変わったりした書き留め。"""

    id: str
    how: str


@dataclass(frozen=True)
class Entry:
    """1日の書き足し（本人の言葉）。sources は材料にしたページの id。"""

    day: date
    sources: tuple[str, ...]
    written_by: str
    relation: str = ""  # 今の関係（変わったときだけ）
    turning: str = ""  # 関係の転機（あったときだけ）
    added: tuple[Added, ...] = ()
    touched: tuple[Touched, ...] = ()

    def dumps(self) -> str:
        head: dict = {"day": self.day.isoformat(), "sources": list(self.sources), "written_by": self.written_by}
        if self.turning:
            head["turning"] = self.turning
        if self.added:
            head["added"] = [
                {"id": a.id, "kind": a.kind, "text": a.text, **({"replaces": a.replaces} if a.replaces else {}),
                 **({"when": a.when} if a.when else {})}
                for a in self.added
            ]
        if self.touched:
            head["touched"] = [{"id": t.id, "how": t.how} for t in self.touched]
        lines = [_FENCE, *(f"{key} = {_toml_value(value)}" for key, value in head.items()), _FENCE, ""]
        return "\n".join(lines) + self.relation.rstrip() + "\n"

    @classmethod
    def loads(cls, text: str) -> Entry:
        end = text.index("\n" + _FENCE + "\n", len(_FENCE))
        head = tomllib.loads(text[len(_FENCE) + 1 : end])
        return cls(
            day=date.fromisoformat(head["day"]),
            sources=tuple(head.get("sources", ())),
            written_by=head["written_by"],
            relation=text[end + len(_FENCE) + 2 :].strip(),
            turning=head.get("turning", ""),
            added=tuple(
                Added(a["id"], a["kind"], a["text"], a.get("replaces", ""), a.get("when", "")) for a in head.get("added", ())
            ),
            touched=tuple(Touched(t["id"], t["how"]) for t in head.get("touched", ())),
        )


@dataclass(frozen=True)
class Thing:
    """書き留めていること1つの、今の姿（書き足しを古い順に読んで計算したもの）。"""

    id: str
    kind: str
    text: str
    since: date
    touched_at: date  # 最後に書き留めた・確かめた・揺らいだ日
    confirmed: int = 0
    shaken: int = 0
    until: date | None = None  # 終わった日（まだ有効なら None）
    ended: str = ""  # どう終わったか（変わった・もう違う・果たした）
    when: str = ""  # 日付（2026-10-12 はその日だけ、--03-07 は毎年。なければ空）

    @property
    def open(self) -> bool:
        return self.until is None

    @property
    def sureness(self) -> str:
        """見方の確かさ。書き留めた日を1つと数え、確かめられた日と揺らいだ日の差で決める（数は脳に書かせない）。"""
        score = 1 + self.confirmed - self.shaken
        return "強くそう思う" if score >= 3 else "そう思う" if score >= 1 else "前ほどそう思えない"


@dataclass
class Relation:
    person: str
    relations: list[tuple[date, str]] = field(default_factory=list)  # 今の関係の移り変わり（古い順）
    turnings: list[tuple[date, str]] = field(default_factory=list)  # 関係の年表（古い順）
    things: dict[str, Thing] = field(default_factory=dict)  # 書き留めたことの全部（終わったものも）
    last_day: date | None = None  # いちばん新しい書き足しの日

    def open_things(self) -> list[Thing]:
        """まだ有効な書き留め。最近書き留めた・確かめたものから（同じ日なら書き留めた順）。"""
        things = sorted((t for t in self.things.values() if t.open), key=lambda t: t.id)
        return sorted(things, key=lambda t: t.touched_at, reverse=True)

    def ended_things(self) -> list[Thing]:
        """終わった書き留め。最近終わったものから（同じ日なら書き留めた順）。"""
        things = sorted((t for t in self.things.values() if not t.open), key=lambda t: t.id)
        return sorted(things, key=lambda t: t.until, reverse=True)


def person_dir(memory_dir: Path, person: str) -> Path:
    return Path(memory_dir) / PEOPLE_DIR / person


def load_entries(memory_dir: Path, person: str) -> list[Entry]:
    return [Entry.loads(path.read_text(encoding="utf-8")) for path in sorted(person_dir(memory_dir, person).glob("*.md"))]


def fold(person: str, entries: list[Entry]) -> Relation:
    """書き足しを古い順に読んで、今の関係を計算する。消された日の書き留めを指す書き足しは、そこだけ読み飛ばす。"""
    relation = Relation(person)
    things = relation.things
    for entry in sorted(entries, key=lambda e: e.day):
        day = entry.day
        if entry.relation:
            relation.relations.append((day, entry.relation))
        if entry.turning:
            relation.turnings.append((day, entry.turning))
        for touch in entry.touched:
            thing = things.get(touch.id)
            if thing is None or not thing.open:
                continue
            if touch.how == CONFIRMED:
                things[touch.id] = replace(thing, confirmed=thing.confirmed + 1, touched_at=day)
            elif touch.how == SHAKEN:
                things[touch.id] = replace(thing, shaken=thing.shaken + 1, touched_at=day)
            else:  # もう違う・果たした
                things[touch.id] = replace(thing, until=day, ended=touch.how)
        for added in entry.added:
            old = things.get(added.replaces)
            if old is not None and old.open:
                things[added.replaces] = replace(old, until=day, ended=CHANGED)
            things[added.id] = Thing(added.id, added.kind, added.text, since=day, touched_at=day, when=added.when)
        relation.last_day = day
    return relation


def load_relation(memory_dir: Path, person: str) -> Relation:
    return fold(person, load_entries(memory_dir, person))


def next_on(when: str, today: date) -> date | None:
    """日付 when が次に来る日（今日を含む）。その日だけの日付が過ぎていれば None。読めない日付も None。
    毎年の2月29日は、うるう年でなければ28日。"""
    if _ONCE.match(when):
        try:
            day = date.fromisoformat(when)
        except ValueError:
            return None
        return day if day >= today else None
    match = _YEARLY.match(when)
    if not match:
        return None
    month, mday = int(match[1]), int(match[2])
    for year in (today.year, today.year + 1):
        leap_day_moved = (month, mday) == (2, 29) and not calendar.isleap(year)
        try:
            day = date(year, month, 28 if leap_day_moved else mday)
        except ValueError:
            return None  # 13月や4月31日のような、ない日
        if day >= today:
            return day
    return None


def coming(relation: Relation, today: date, *, within: int = COMING_DAYS) -> list[tuple[date, Thing]]:
    """今日から within 日のうちに来る日付の、まだ有効な書き留め（近い順）。"""
    out = []
    for thing in relation.open_things():
        day = next_on(thing.when, today) if thing.when else None
        if day is not None and (day - today).days < within:
            out.append((day, thing))
    return sorted(out, key=lambda pair: pair[0])


def due_today(relation: Relation, today: date) -> list[Thing]:
    """今日がその日の、まだ有効な書き留め。"""
    return [thing for _day, thing in coming(relation, today, within=1)]


def when_label(when: str) -> str:
    """日付の読み方（2026年10月12日・毎年3月7日）。"""
    if _ONCE.match(when):
        day = date.fromisoformat(when)
        return f"{day.year}年{day.month}月{day.day}日"
    match = _YEARLY.match(when)
    return f"毎年{int(match[1])}月{int(match[2])}日" if match else ""


def coming_lines(relation: Relation, today: date) -> list[str]:
    """近いうちの日付の行（今日・明日・あと何日）。"""
    lines = []
    for day, thing in coming(relation, today):
        left = (day - today).days
        at = "今日" if left == 0 else "明日" if left == 1 else f"あと{left}日"
        lines.append(f"{at}（{day.month}月{day.day}日）：{thing.text}")
    return lines


def write_entry(memory_dir: Path, person: str, entry: Entry) -> Path:
    """新しいファイルに書く。一時ファイルに書いてから置き換えるので、途中で止まっても半端なファイルは残らない。"""
    folder = person_dir(memory_dir, person)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{entry.day.isoformat()}.md"
    tmp = path.with_suffix(".md.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(entry.dumps())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def forget_pages(memory_dir: Path, page_ids: set[str]) -> list[str]:
    """Masterが記録を消して外れたページを材料にしていた書き足しを外す。外したものの名前（people/<その人>/<日>）を返す。

    その日の書き足しはページ全体から書いたものなので、日ごと外す。外した日より後の日がもうあれば、その日は書き直さない
    （関係は日の順に積み重なるので）。後の日が外した日の書き留めを指していても、そこだけ読み飛ばされる（fold）。
    """
    gone = []
    for path in sorted((Path(memory_dir) / PEOPLE_DIR).glob("*/*.md")):
        if set(Entry.loads(path.read_text(encoding="utf-8")).sources) & page_ids:
            path.unlink()
            gone.append(f"{PEOPLE_DIR}/{path.parent.name}/{path.stem}")
    return gone


# --- 書き足す（眠りの間） ------------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "……"


def day_material(pages: list[Page]) -> str:
    """材料：その日の記憶のページ（日記と出来事。本人の言葉）を時刻の順に。

    合わせて DAY_CHARS まで（Gemma の窓に、人格・指示と一緒に収める）。どのページも同じ長さに分け、題と要点から見せる。
    """
    share = min(PAGE_VIEW, DAY_CHARS // max(1, len(pages)))
    out = []
    for page in pages:
        what = "日記" if page.kind == "diary" else "出来事"
        story = page.body.split("\n\nそのときの言葉：")[0]
        out.append("- " + _clip(f"（{what}）{page.title}：{page.gist} {story}", share))
    return "\n".join(out)


def relation_prompt(persona: str, relation: Relation, shown: list[Thing], material: str, day: str) -> str:
    person = relation.person
    now = f"{relation.relations[-1][1]}（{relation.relations[-1][0].isoformat()}に書いた）" if relation.relations else "（まだ書いていない）"
    turnings = "\n".join(f"- {d.isoformat()} {text}" for d, text in relation.turnings[-SHOWN_TURNINGS:]) or "（まだない）"
    things = "\n".join(
        f"{n}. [{t.kind}] {t.text}（{t.since.isoformat()}から"
        + (f"、{t.sureness}" if t.kind == THINKS else "")
        + (f"、日付は{when_label(t.when)}" if t.when else "")
        + "）"
        for n, t in enumerate(shown, start=1)
    ) or "（まだない）"
    return f"""{persona}

---
{_SELF}今は眠っている間。{day}にあったことを振り返って、{person}とのことを書き留めるところ。
書き留めるのは、この日に変わったことと、新しく分かったことだけ。いつもどおりなら、何も足さなくていい。

【{person}との今の関係】（前に書いたもの）
{now}

【これまでの転機】
{turnings}

【{person}について書き留めていること】（行の頭の数字は番号）
{things}

【{day}にあったこと】（あなたの記憶のページから）
{material}

次のJSONだけを返す。上に書いていないことは書かない。
{{"relation": {person}との今の関係が、この日に変わったとき（まだ書いていなければ、今）だけ、今の関係（あなたの一人称で80〜250字）。変わっていなければ空の文字列,
 "turning": この日が関係の転機（関係の形そのものが変わった、めったにない日）なら、何が変わったかを一文（60字まで）。なければ空の文字列,
 "new": この日に新しく分かったこと（0〜3個）。それぞれ {{"kind": "{KNOWS}"（{person}についての事実や予定）か"{THINKS}"（{person}がどんな人か、というあなたの見方。あなた自身の願いや気持ちは入れない）か"{PROMISED}"（{person}との約束）, "text": 一文（60字まで。いつ読んでも分かるように、「来週」「明後日」のような言い方は使わない）, "when": 約束や予定の日なら "2026-10-12" の形（「来週の土曜」などは、この日から数えた日付にする）、誕生日や記念日のように毎年来る日なら "--03-07" の形、日付がなければ空の文字列, "replaces": 上の番号のうち、これで古くなったものの番号（なければ0）}},
 "changed": この日の出来事で、確かめられたり変わったりした書き留め（0〜5個）。それぞれ {{"no": 上の番号, "how": "{CONFIRMED}"か"{SHAKEN}"か"{NO_LONGER}"か"{KEPT}"（約束を果たした）}}}}"""


def _items(answer: dict, key: str) -> list:
    value = _field(answer, key, [])
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise WordsRejected(f"{key} が並びでない")
    return [item for item in value if isinstance(item, dict)]


def _number(raw) -> int:  # noqa: ANN001
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def parse_relation_words(answer: dict, shown: list[Thing], day: date) -> tuple[str, str, tuple[Added, ...], tuple[Touched, ...]]:
    """脳の答えを確かめて整える。番号は見せた書き留めの id に読み替え、範囲外の番号や合わない組み合わせは落とす。"""
    relation = _text(answer, "relation", RELATION_MAX, minimum=0)
    if relation and len(relation) < RELATION_MIN:
        raise WordsRejected(f"relation の長さが {len(relation)} 字（変わったときは{RELATION_MIN}〜{RELATION_MAX}）")
    turning = _text(answer, "turning", TURNING_MAX, minimum=0)

    def thing_of(no: int) -> Thing | None:
        return shown[no - 1] if 1 <= no <= len(shown) else None

    def when_of(item: dict) -> str:
        """日付は入口で確かめる。読めない日付・範囲の外の日付は、日付なしの書き留めにする。"""
        value = _field(item, "when", "")
        value = value.strip() if isinstance(value, str) else ""
        if _YEARLY.match(value):
            return value if next_on(value, day) is not None else ""
        if not _ONCE.match(value):
            return ""
        try:
            once = date.fromisoformat(value)
        except ValueError:
            return ""
        return value if abs(once - day) <= WHEN_RANGE else ""

    added: list[Added] = []
    for item in _items(answer, "new")[:MAX_NEW]:
        kind = _field(item, "kind")
        if kind not in (KNOWS, THINKS, PROMISED):
            raise WordsRejected(f"new の kind が {kind!r}")
        old = thing_of(_number(_field(item, "replaces", 0)))
        added.append(Added(
            id=f"{day.isoformat()}-{len(added) + 1}",
            kind=kind,
            text=_text(item, "text", THING_MAX),
            replaces=old.id if old is not None else "",
            when=when_of(item),
        ))
    replaced = {a.replaces for a in added}
    touched: list[Touched] = []
    for item in _items(answer, "changed"):
        thing, how = thing_of(_number(_field(item, "no"))), _field(item, "how")
        if thing is None or how not in (CONFIRMED, SHAKEN, NO_LONGER, KEPT) or (how == KEPT and thing.kind != PROMISED):
            continue
        if thing.id in replaced or any(t.id == thing.id for t in touched):
            continue
        touched.append(Touched(thing.id, how))
    return relation, turning, tuple(added), tuple(touched[:MAX_TOUCHED])


def grow(
    memory_dir: Path,
    person: str,
    day: date,
    pages: list[Page],
    *,
    persona: str,
    ask: Ask,
    written_by: str,
) -> Entry:
    """その日のページから、その人とのことの書き足しを本人の脳に書いてもらう。書けなければ WordsRejected。

    ファイルに残すのは眠り（sleep.py）。材料のページがまだあるのを確かめてから write_entry で書く。
    """
    relation = load_relation(memory_dir, person)
    shown = relation.open_things()[:SHOWN_THINGS]
    prompt = relation_prompt(persona, relation, shown, day_material(pages), _day_label(day))
    words, turning, added, touched = _ask_until_valid(
        ask, prompt, RELATION_SCHEMA, lambda answer: parse_relation_words(answer, shown, day)
    )
    return Entry(
        day=day,
        sources=tuple(page.id for page in pages),
        written_by=written_by,
        relation=words,
        turning=turning,
        added=added,
        touched=touched,
    )


def _day_label(day: date) -> str:
    return f"{day.year}年{day.month}月{day.day}日（{'月火水木金土日'[day.weekday()]}）"


# --- 文脈パック ----------------------------------------------------------------------------


def render_for_pack(relation: Relation, today: date | None = None) -> str:
    """文脈パックの【<その人>とのこと】の段。まだ何も書き留めていなければ空（段ごと省く）。

    大事なものから順に、IN_PACK の長さまで載せる：近いうちの日付（today があれば）→ 今の関係 → 前の関係 → 約束 → 転機 →
    知っていること → 前はそうだったこと → 見方。
    """
    if relation.last_day is None:
        return ""
    lines: list[str] = coming_lines(relation, today) if today is not None else []
    if relation.relations:
        since, text = relation.relations[-1]
        lines.append(f"今の関係（{since.isoformat()}から）：{text}")
    if len(relation.relations) >= 2:
        since, text = relation.relations[-2]
        lines.append(f"その前の関係（{since.isoformat()}から）：{_clip(text, 80)}")
    open_things = relation.open_things()
    lines += [
        f"約束（{t.since.isoformat()}から{'、' + when_label(t.when) if t.when else ''}）：{t.text}"
        for t in open_things if t.kind == PROMISED
    ]
    lines += [f"転機（{d.isoformat()}）：{text}" for d, text in reversed(relation.turnings)]
    lines += [f"知っていること{'（' + when_label(t.when) + '）' if t.when else ''}：{t.text}" for t in open_things if t.kind == KNOWS]
    lines += [
        f"前はそうだったこと（〜{t.until.isoformat()}）：{t.text}" for t in relation.ended_things() if t.kind == KNOWS
    ]
    lines += [f"思っていること（{t.sureness}）：{t.text}" for t in open_things if t.kind == THINKS]
    out: list[str] = []
    used = 0
    for line in lines:
        if out and used + len(line) > IN_PACK:
            break
        out.append(line)
        used += len(line)
    return "\n".join(out)

