"""目覚め（docs/設計書.md §4.6）。眠り終えた本人が、今の自分を確かめ、伝えたいことを思う。

眠りの間に書いた日記と週・月の振り返りを本人が読み返して、次の2つを書く（書くのは本人の脳。人格を渡して、本人として書く）。
- 今の自分：今の自分と、今気にかけていること。会話のたびに手元にある（文脈パックの【今の自分】）。マスターとの関係は
  眠りの間に書き足す関係（relation.py）が持つので、ここには書かない。
- 伝えたいこと：目覚めて、マスターに伝えたくなったこと（なければ空）。近いうちの約束や予定（relation.py）も材料になる。
  マスターがまだ来ていなければ、本人から話しかけに行く
  （Pulse の種類 wake。core/chores/idle_policy.py）。

目覚めるたびに、新しいファイルに書く（memory/self/<日時>.md）。前の今の自分は書き換えないので、並べると本人の変わり方が見える。
今の自分は、いちばん新しいファイル。何を材料にしたか（最後の日記と振り返りのページ）を残すので、どちらも増えていなければ
目覚めても書き直さない。状態を別に持たない。
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mind.core.memory.page import load_pages
from mind.core.memory.relation import coming_lines, load_relation
from mind.core.memory.structure import JST, MASTER_NAME
from mind.core.memory.writing import _SELF, Ask, WordsRejected, _ask_until_valid, _field, _text

SELF_DIR = "self"  # memory/ の下の置き場所
SELF_MIN, SELF_MAX = 60, 600
TELL_MAX = 120
DIARIES = 3  # 読み返す日記の数（前に目覚めてから書いたもののうち、新しいほうから）
DIARY_VIEW = 700  # 読み返す日記1つの長さ
REFLECTIONS = 2
REFLECTION_VIEW = 800
_FENCE = "+++"

WAKING_SCHEMA = {
    "type": "object",
    "properties": {
        "self": {"type": "string"},
        "tell": {"type": "string"},
        "call_time": {"type": "string", "enum": ["朝", "昼", "夕方", "夜", "今日はそっとしておく"]},
    },
    "required": ["self", "tell", "call_time"],
}
CALL_TIMES = ("朝", "昼", "夕方", "夜", "今日はそっとしておく")


@dataclass(frozen=True)
class Waking:
    at: datetime  # 目覚めた時刻
    after: str  # 材料にした最後の日記のページの id
    written_by: str
    self_text: str  # 今の自分
    after_reflection: str = ""  # 材料にした最後の週・月の振り返り
    seen_reflections: tuple[str, ...] = ()  # 実際に読み返した振り返り。後から作った過去分を時系列だけで既読扱いしない
    call_time: str = ""  # 今日、つながりのために声をかけたくなりそうな時間帯
    tell: str = ""  # マスターに伝えたいこと（なければ空）

    def dumps(self) -> str:
        head = {
            "at": self.at.isoformat(),
            "after": self.after,
            "after_reflection": self.after_reflection,
            "seen_reflections": list(self.seen_reflections),
            "written_by": self.written_by,
            "call_time": self.call_time,
            "tell": self.tell,
        }
        lines = [_FENCE, *(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in head.items()), _FENCE, ""]
        return "\n".join(lines) + self.self_text.rstrip() + "\n"

    @classmethod
    def loads(cls, text: str) -> Waking:
        end = text.index("\n" + _FENCE + "\n", len(_FENCE))
        head = tomllib.loads(text[len(_FENCE) + 1 : end])
        return cls(
            at=datetime.fromisoformat(head["at"]),
            after=head["after"],
            after_reflection=head.get("after_reflection", ""),
            seen_reflections=tuple(head.get("seen_reflections", ())),
            written_by=head["written_by"],
            call_time=head.get("call_time", ""),
            tell=head.get("tell", ""),
            self_text=text[end + len(_FENCE) + 2 :].strip(),
        )


def latest_waking(memory_dir: Path) -> Waking | None:
    """今の自分（いちばん新しい目覚め）。まだ一度も目覚めていなければ None。"""
    paths = sorted((Path(memory_dir) / SELF_DIR).glob("*.md"))
    return Waking.loads(paths[-1].read_text(encoding="utf-8")) if paths else None


def write_waking(memory_dir: Path, waking: Waking) -> Path:
    """新しいファイルに書く。一時ファイルに書いてから置き換えるので、途中で止まっても半端なファイルは残らない。"""
    folder = Path(memory_dir) / SELF_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{waking.at.astimezone(JST):%Y-%m-%d_%H%M%S}.md"
    tmp = path.with_suffix(".md.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(waking.dumps())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def render_for_pack(waking: Waking | None) -> str:
    """文脈パックの【今の自分】の段。まだ目覚めていなければ空（段ごと省く）。"""
    if waking is None:
        return ""
    text = waking.self_text
    if waking.tell:
        text += f"\n（目覚めたとき、マスターに伝えたいと思ったこと：{waking.tell}）"
    return text


def waking_prompt(
    persona: str,
    previous: Waking | None,
    diaries: str,
    today: str,
    coming: str = "",
    reflections: str = "",
) -> str:
    before = previous.self_text if previous else "（まだない。初めて書く）"
    soon = f"\n【近いうちの約束や予定】\n{coming}\n" if coming else ""
    return f"""{persona}

---
{_SELF}今は{today}、眠りから覚めたところ。眠っている間に書いた日記と振り返りを読み返して、今の自分を確かめる。

【前に目覚めたときの、今の自分】
{before}

【眠っている間に書いた日記】
{diaries or "（新しい日記はない）"}

【新しい週・月の振り返り】
{reflections or "（新しい振り返りはない）"}
{soon}
次のJSONだけを返す。
{{"self": 今のあなた（100〜400字。あなたの一人称で、今の自分と、今気にかけていること。前の「今の自分」から変わったところがあれば、それも。上の材料にないことは書かない。箇条書きにしない）,
 "tell": 目覚めて、マスターに伝えたくなったこと（あれば、その中身を80字まで。とくになければ空の文字列）,
 "call_time": 今日マスターに声をかけたくなりそうな時間帯（朝／昼／夕方／夜／今日はそっとしておく、のどれか）}}"""


def parse_waking_words(answer: dict) -> tuple[str, str, str]:
    self_text = _text(answer, "self", SELF_MAX, minimum=SELF_MIN)
    tell = _text(answer, "tell", TELL_MAX, minimum=0)
    call_time = _field(answer, "call_time")
    if call_time not in CALL_TIMES:
        raise WordsRejected("call_time が選択肢にない")
    return self_text, tell, call_time


def wake(memory_dir: Path, *, persona: str, ask: Ask, written_by: str, now: datetime) -> Waking | None:
    """眠り終えたあとに目覚める。新しい日記か振り返りがあれば、本人が今の自分と伝えたいことを書く。書いたら返す。

    どちらも増えていなければ何もしない。書けなければ WordsRejected。
    """
    pages = load_pages(memory_dir)
    diaries = [page for page in pages if page.kind == "diary" and page.written and page.body]
    reflections = [page for page in pages if page.kind == "reflection" and page.written and page.body]
    if not diaries and not reflections:
        return None
    previous = latest_waking(memory_dir)
    ids = [page.id for page in diaries]
    reflection_ids = [page.id for page in reflections]
    last_diary = ids[-1] if ids else ""
    seen_reflection_ids = set(previous.seen_reflections if previous is not None else ())
    # 旧形式の目覚めには一覧がない。少なくとも「最後に読んだ」と残っている1件は既読とする。
    if previous is not None and previous.after_reflection:
        seen_reflection_ids.add(previous.after_reflection)
    fresh_reflections = [page for page in reflections if page.id not in seen_reflection_ids]
    if previous is not None and previous.after == last_diary and not fresh_reflections:
        return None
    since = ids.index(previous.after) + 1 if previous is not None and previous.after in ids else 0
    fresh = diaries[since:][-DIARIES:]
    # 後から作られた過去週・月も落とさない。未読を古い順に少しずつ読む。
    fresh_reflections = fresh_reflections[:REFLECTIONS]
    material = "\n\n".join(
        f"{page.start.astimezone(JST):%Y-%m-%d}「{page.title}」\n{page.body[:DIARY_VIEW]}" if page.start else page.body[:DIARY_VIEW]
        for page in fresh
    )
    reflection_material = "\n\n".join(
        f"{page.start.astimezone(JST):%Y-%m-%d}「{page.title}」\n{page.body[:REFLECTION_VIEW]}"
        if page.start else page.body[:REFLECTION_VIEW]
        for page in fresh_reflections
    )
    today = f"{now.astimezone(JST):%Y-%m-%d}"
    coming = "\n".join(coming_lines(load_relation(memory_dir, MASTER_NAME), now.astimezone(JST).date()))
    prompt = waking_prompt(persona, previous, material, today, coming, reflection_material)
    self_text, tell, call_time = _ask_until_valid(ask, prompt, WAKING_SCHEMA, parse_waking_words)
    handled_ids = seen_reflection_ids | {page.id for page in fresh_reflections}
    handled_reflections = tuple(page_id for page_id in reflection_ids if page_id in handled_ids)
    last_reflection = fresh_reflections[-1].id if fresh_reflections else (previous.after_reflection if previous else "")
    waking = Waking(
        at=now,
        after=last_diary,
        after_reflection=last_reflection,
        seen_reflections=handled_reflections,
        written_by=written_by,
        self_text=self_text,
        call_time=call_time,
        tell=tell,
    )
    write_waking(memory_dir, waking)
    return waking

