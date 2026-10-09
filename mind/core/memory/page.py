"""記憶のページ（docs/設計書.md §4.2）。

ページは、住人が思い出せるもののひとまとまり。出来事（会話から）・日記・覚え書き（継承した構造化記憶や約束）の3種類。
1ページは1つの Markdown ファイルで、先頭の +++ で囲んだ TOML に見出し情報を、その後ろに本文を書く。人もAIも読めるので、
どの脳・どの身体になっても読める。置き場所はイデアの memory/（読み方はイデアの memory/README.md）。

ページには2人の書き手がいる。
- 整理：いつ・どの記録から・どんな概念とつながるか（id・kind・when・source・concepts・前後のページ）と、そのときの芯の数
  （affect。出来事のあいだの気持ちの記録から仕組みが決める。core/feeling/feelings.py の peak_end）。
  最初の記憶づくりは Claude が受け持った（2026-10-03、Masterの了承）。これからは眠りの間に精神が行う。
- 言葉：題・要点・本人の一人称の文・大事さ。いつも住人本人（その時の脳）が書き、一度書いたら書き換えない
  （本人が書いた記憶は、後の脳が書き直さない。§3）。日記と覚え書きは本文がもともと本人の言葉なので、題・要点・大事さだけを書く。
  気持ちは本文に本人の言葉で書いてあるので、脳に気持ちの数は書かせない。2026-10 までのページには、そのころの本人が
  プルチックの8軸でつけた気持ち（feeling）が残っている。本人が書いたものなので書き換えず、読み書きでそのまま運ぶ。
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

KINDS = {"episode": "episodes", "diary": "diary", "note": "notes", "reflection": "reflections"}  # 種類 → memory/ の下のフォルダー
_FENCE = "+++"


class WordsAlreadyWritten(Exception):
    """本人がもう書いたページに、言葉を書き直そうとした。"""


@dataclass(frozen=True)
class Page:
    id: str
    kind: str
    start: datetime | None  # この記憶が始まった時刻（覚え書きで分からなければ None）
    end: datetime | None  # 終わった時刻。この時刻より前の「今」には、まだ思い出せない
    source: tuple[str, ...]  # 拠っている記録（イデアからの相対の場所と、#の後ろに範囲）
    concepts: tuple[str, ...]
    prev: str = ""
    next: str = ""
    structured_by: str = ""
    title: str = ""
    gist: str = ""
    importance: int | None = None  # 本人にとっての大事さ（1〜10）
    affect: dict[str, float] | None = None  # そのときの芯の数（valence -1〜1・arousal 0〜1）。気持ちの記録がなければ None
    feeling: dict[str, float] = field(default_factory=dict)  # 2026-10 までのページの、本人がつけた8軸の気持ち（そのまま運ぶ）
    later: tuple[dict[str, str], ...] = ()  # 後から思い出したときの「今思うと」。本文は書き換えず、日付つきで足す
    written_by: str = ""
    body: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"知らない種類のページ: {self.kind}")

    @property
    def written(self) -> bool:
        """本人の言葉がもう書かれているか。"""
        return bool(self.written_by)

    @property
    def arousal(self) -> float | None:
        """心の動きの強さ（そのときの高ぶり）。分からなければ None。"""
        return self.affect["arousal"] if self.affect else None

    def with_words(
        self,
        *,
        title: str,
        gist: str,
        importance: int,
        written_by: str,
        body: str | None = None,
    ) -> Page:
        """本人の言葉を書き入れたページを返す。もう書いてあれば断る。"""
        if self.written:
            raise WordsAlreadyWritten(self.id)
        if not 1 <= importance <= 10:
            raise ValueError(f"大事さは1〜10: {importance}")
        return replace(
            self,
            title=title,
            gist=gist,
            importance=importance,
            written_by=written_by,
            body=self.body if body is None else body,
        )

    def path_in(self, memory_dir: Path) -> Path:
        folder = Path(memory_dir) / KINDS[self.kind]
        if self.start is not None and self.kind != "note":
            folder = folder / f"{self.start:%Y}"
        return folder / f"{self.id}.md"


def _toml_value(value) -> str:  # noqa: ANN001
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(round(value, 3))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # JSON の文字列は TOML の基本文字列としても読める
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
    raise TypeError(f"TOMLに書けない値: {value!r}")


def dumps(page: Page) -> str:
    head: dict = {"id": page.id, "kind": page.kind}
    if page.start is not None:
        head["when"] = [page.start, page.end or page.start]
    head["source"] = list(page.source)
    head["concepts"] = list(page.concepts)
    for key in ("prev", "next", "structured_by", "title", "gist"):
        if getattr(page, key):
            head[key] = getattr(page, key)
    if page.importance is not None:
        head["importance"] = page.importance
    if page.affect:
        head["affect"] = page.affect
    if page.feeling:
        head["feeling"] = page.feeling
    if page.later:
        head["later"] = list(page.later)
    if page.written_by:
        head["written_by"] = page.written_by
    lines = [_FENCE, *(f"{key} = {_toml_value(value)}" for key, value in head.items()), _FENCE, ""]
    return "\n".join(lines) + page.body.rstrip() + "\n"


def loads(text: str) -> Page:
    if not text.startswith(_FENCE + "\n"):
        raise ValueError("ページの先頭に +++ がない")
    end = text.index("\n" + _FENCE + "\n", len(_FENCE))
    head = tomllib.loads(text[len(_FENCE) + 1 : end])
    body = text[end + len(_FENCE) + 2 :].lstrip("\n").rstrip()
    when = head.get("when")
    return Page(
        id=head["id"],
        kind=head["kind"],
        start=when[0] if when else None,
        end=when[1] if when else None,
        source=tuple(head.get("source", ())),
        concepts=tuple(head.get("concepts", ())),
        prev=head.get("prev", ""),
        next=head.get("next", ""),
        structured_by=head.get("structured_by", ""),
        title=head.get("title", ""),
        gist=head.get("gist", ""),
        importance=head.get("importance"),
        affect=dict(head["affect"]) if "affect" in head else None,
        feeling=dict(head.get("feeling", {})),
        later=tuple(dict(item) for item in head.get("later", ())),
        written_by=head.get("written_by", ""),
        body=body,
    )


def write_page(memory_dir: Path, page: Page) -> Path:
    """ページを書く。一時ファイルに書いてから置き換えるので、途中で止まっても半端なページは残らない。"""
    path = page.path_in(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(page))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def load_pages(memory_dir: Path) -> list[Page]:
    """memory/ のすべてのページを、始まりの時刻の順に（時刻のない覚え書きは最後）。"""
    pages = []
    for kind_dir in KINDS.values():
        for path in sorted((Path(memory_dir) / kind_dir).rglob("*.md")):
            pages.append(loads(path.read_text(encoding="utf-8")))
    return sorted(pages, key=lambda p: (p.start is None, p.start.timestamp() if p.start else 0.0, p.id))
