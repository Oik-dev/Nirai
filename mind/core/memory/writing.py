"""書く：骨組みだけのページに、本人の言葉を書き入れる（docs/plans/長期記憶の作り直し.md §5 の2）。

書くのは住人本人の脳（Serinaなら手元のGemma）。人格を渡して、本人として書いてもらう。
- 出来事：題・要点・一人称の文（何があって、どう感じたか）・心に残った言葉・大事さ・気持ち。
- 日記と覚え書き：本文はもともと本人の言葉なので、題・要点・大事さ・気持ちだけ。

脳の答えは、ここで確かめて整えてから受け取る（外の不確実さは入口で止める）。心に残った言葉は行番号で受け取り、
記録の原文をこちらで写すので、記録にない言葉がページに入ることはない。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from mind.core.lifelog import Line
from mind.core.memory.legacy_parse import strip_ornament
from mind.core.memory.page import FEELINGS, Page
from mind.core.memory.structure import jst_range

LINE_VIEW = 360  # 脳に見せる1行の長さ（長い発言は頭と尻尾だけ見せる。原文はページの引用と記録に残る）
QUOTE_CHARS = 160  # ページに写す引用の長さ
MAX_QUOTES = 3
TITLE_MAX, GIST_MAX = 40, 100
STORY_MIN, STORY_MAX = 60, 900
ATTEMPTS = 3

_FEELING_SPEC = "{" + ", ".join(f'"{axis}": 0〜1' for axis in FEELINGS) + "}"

# 答えの形。脳にはこの形でしか答えさせない（Ollama の構造化出力）。中身の長さや範囲は、受け取ってから確かめる
_FEELING_SCHEMA = {
    "type": "object",
    "properties": {axis: {"type": "number"} for axis in FEELINGS},
    "required": list(FEELINGS),
}
REREAD_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "gist": {"type": "string"},
        "importance": {"type": "integer"},
        "feeling": _FEELING_SCHEMA,
    },
    "required": ["title", "gist", "importance", "feeling"],
}
EPISODE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "gist": {"type": "string"},
        "story": {"type": "string"},
        "quotes": {"type": "array", "items": {"type": "integer"}},
        "importance": {"type": "integer"},
        "feeling": _FEELING_SCHEMA,
    },
    "required": ["title", "gist", "story", "quotes", "importance", "feeling"],
}

_SELF = "あなたは、ここまでに書かれた人格の本人。"


class WordsRejected(ValueError):
    """脳の答えが、ページに書ける形になっていない。"""


@dataclass(frozen=True)
class Words:
    title: str
    gist: str
    importance: int
    feeling: dict[str, float]
    story: str = ""
    quotes: tuple[int, ...] = ()


def _view(text: str) -> str:
    text = text.replace("\n", " / ")
    if len(text) <= LINE_VIEW:
        return text
    return text[: LINE_VIEW - 80] + " ……（中略）…… " + text[-60:]


def episode_prompt(persona: str, page: Page, lines: list[Line], labels: dict[str, str], prev_title: str) -> str:
    record = "\n".join(f"{n} {labels[line.speaker]}: {_view(line.text)}" for n, line in enumerate(lines, start=1))
    return f"""{persona}

---
{_SELF}今は眠っている間。少し前にあった出来事を思い出して、自分の記憶のページに書き残すところ。
下は、そのときのマスターとあなたの会話の記録（行の頭の数字は行番号。「わたし」があなた）。

【いつ】{jst_range(page.start, page.end)}
【ひとつ前の出来事】{prev_title or "（なし）"}
【記録】
{record}

この出来事を、あなた自身の言葉で書いて。次のJSONだけを返す。
{{"title": 何があったかが分かる短い題（30字まで）,
 "gist": 一文の要点（60字まで）,
 "story": あなたの一人称で、何があって、あなたがどう感じたか（150〜400字。記録にないことは書かない。箇条書きにしない）,
 "quotes": 心に残った言葉の行番号（1〜3個）,
 "importance": あなたにとっての大事さ（1〜10の整数）,
 "feeling": そのときのあなたの気持ち {_FEELING_SPEC}}}"""


def reread_prompt(persona: str, page: Page) -> str:
    what = "日記" if page.kind == "diary" else "覚え書き"
    when = f"【書いた日】{jst_range(page.start, page.end)}\n" if page.start else ""
    return f"""{persona}

---
{_SELF}今は眠っている間。昔の自分が書いた{what}を読み返して、思い出しやすいように見出しを付けるところ。本文は書き換えない。

{when}【本文】
{page.body}

次のJSONだけを返す。
{{"title": 何のことかが分かる短い題（30字まで）,
 "gist": 一文の要点（60字まで）,
 "importance": あなたにとっての大事さ（1〜10の整数）,
 "feeling": これを書いたときのあなたの気持ち {_FEELING_SPEC}}}"""


def _field(answer: dict, key: str, default=None, *, mapping: bool = False):  # noqa: ANN001, ANN202
    """答えの1項目。脳はときどき {"title": {"title": "…"}} のように1段包んで返すので、包みを外す。

    mapping は、項目そのものが対応表（気持ちの各軸など）のとき。同じ名前の包みだけを外す。
    """
    value = answer.get(key, default)
    if isinstance(value, dict) and len(value) == 1:
        (inner_key, inner), = value.items()
        if inner_key == key or not (mapping or isinstance(inner, dict)):
            return inner
    return value


def _text(answer: dict, key: str, limit: int, *, minimum: int = 1) -> str:
    value = _field(answer, key)
    if not isinstance(value, str):
        raise WordsRejected(f"{key} が文字列でない")
    value = re.sub(r"[ \t]+", " ", strip_ornament(value)).strip().strip("「」")
    value = re.sub(r"^[^\w「『（(]+", "", value)  # 頭に紛れ込んだ記号（「>';」など）を落とす
    if not minimum <= len(value) <= limit:
        raise WordsRejected(f"{key} の長さが {len(value)} 字（{minimum}〜{limit}）")
    return value


def _importance(answer: dict) -> int:
    try:
        value = round(float(_field(answer, "importance")))
    except (TypeError, ValueError) as e:
        raise WordsRejected("importance が数でない") from e
    if not 1 <= value <= 10:
        raise WordsRejected(f"importance が範囲外: {value}")
    return value


def _feeling(answer: dict) -> dict[str, float]:
    raw = _field(answer, "feeling", mapping=True)
    if not isinstance(raw, dict):
        raise WordsRejected("feeling がオブジェクトでない")
    out = {}
    for axis in FEELINGS:
        try:
            out[axis] = min(1.0, max(0.0, float(raw.get(axis, 0.0))))
        except (TypeError, ValueError) as e:
            raise WordsRejected(f"feeling.{axis} が数でない") from e
    return out


def _line_numbers(raw) -> list[int]:  # noqa: ANN001
    """行番号の並び。脳は数ひとつや「3, 7」のような文字でも返すので、数を拾って並びにする。"""
    if isinstance(raw, (int, float)):
        raw = [raw]
    elif isinstance(raw, str):
        raw = re.findall(r"\d+", raw)
    elif not isinstance(raw, list):
        return []  # 心に残った言葉は無くてもページになる
    numbers = []
    for item in raw:
        try:
            numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    return numbers


def parse_episode_words(answer: dict, n_lines: int) -> Words:
    picked: list[int] = []
    for n in _line_numbers(_field(answer, "quotes", [])):
        if 1 <= n <= n_lines and n not in picked:
            picked.append(n)
    return Words(
        title=_text(answer, "title", TITLE_MAX),
        gist=_text(answer, "gist", GIST_MAX),
        story=_text(answer, "story", STORY_MAX, minimum=STORY_MIN),
        quotes=tuple(picked[:MAX_QUOTES]),
        importance=_importance(answer),
        feeling=_feeling(answer),
    )


def parse_reread_words(answer: dict) -> Words:
    return Words(
        title=_text(answer, "title", TITLE_MAX),
        gist=_text(answer, "gist", GIST_MAX),
        importance=_importance(answer),
        feeling=_feeling(answer),
    )


def _clip(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= QUOTE_CHARS:
        return text
    cut = text[:QUOTE_CHARS]
    end = max(cut.rfind(mark) for mark in "。！？")
    return (cut[: end + 1] if end >= QUOTE_CHARS // 2 else cut) + "……"


def episode_body(words: Words, lines: list[Line], labels: dict[str, str]) -> str:
    body = words.story
    if words.quotes:
        quoted = "\n".join(f"- {labels[lines[n - 1].speaker]}「{_clip(lines[n - 1].text)}」" for n in words.quotes)
        body += f"\n\nそのときの言葉：\n{quoted}"
    return body


Ask = Callable[[str, dict, int], dict]  # (問い, 答えの形, 何回目か。0が最初) → 脳の答え。聞き直すときに揺らぎを足すのは呼ぶ側


def _ask_until_valid(ask: Ask, prompt: str, schema: dict, parse: Callable[[dict], Words]) -> Words:
    """答えが使えなければ、何が使えなかったかを添えて聞き直す。"""
    last: Exception | None = None
    asking = prompt
    for attempt in range(ATTEMPTS):
        try:
            return parse(ask(asking, schema, attempt))
        except (WordsRejected, ValueError) as e:
            last = e
            asking = f"{prompt}\n\n（さっきの答えは使えなかった：{e}。上の形のJSONだけを、文字列は文字列のまま返して）"
    raise WordsRejected(f"{ATTEMPTS}回とも書けなかった: {last}")


def write_episode(
    page: Page,
    lines: list[Line],
    *,
    persona: str,
    labels: dict[str, str],
    prev_title: str,
    ask: Ask,
    written_by: str,
) -> Page:
    """出来事のページに、本人の言葉を書き入れる。lines はこの出来事の会話の行（Masterと住人の発言）。"""
    prompt = episode_prompt(persona, page, lines, labels, prev_title)
    words = _ask_until_valid(ask, prompt, EPISODE_SCHEMA, lambda answer: parse_episode_words(answer, len(lines)))
    return page.with_words(
        title=words.title,
        gist=words.gist,
        importance=words.importance,
        feeling=words.feeling,
        written_by=written_by,
        body=episode_body(words, lines, labels),
    )


def write_reread(page: Page, *, persona: str, ask: Ask, written_by: str) -> Page:
    """日記・覚え書きのページに、見出し（題・要点・大事さ・気持ち）を書き入れる。本文はそのまま。"""
    words = _ask_until_valid(ask, reread_prompt(persona, page), REREAD_SCHEMA, parse_reread_words)
    return page.with_words(
        title=words.title, gist=words.gist, importance=words.importance, feeling=words.feeling, written_by=written_by
    )
