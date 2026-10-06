"""眠りの間に、呼び名と昔の記憶の意味を本人が育てる（段階3 S5）。"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from mind.core.lifelog import RecallLog
from mind.core.memory.index import CONCEPTS_FILE, normalize
from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.relation import Relation, render_for_pack
from mind.core.memory.strength import ranks
from mind.core.memory.structure import JST
from mind.core.memory.waking import Waking
from mind.core.memory.writing import Ask, WordsRejected, _SELF, _ask_until_valid, _field
from mind.core.state.serina_day import serina_day_id

MAX_PAIRS = 3
MAX_LATER = 2
OLD_DAYS = 30
REPLAY_DAYS = 7
MAX_REPLAY = 2
LATER_MAX = 320


class GrowthRejected(WordsRejected):
    """段5の言葉を書けなかった。changed は失敗より前に追記できた件数。"""

    def __init__(self, message: str, *, changed: int = 0) -> None:
        super().__init__(message)
        self.changed = changed

CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                "required": ["a", "b"],
            },
        },
    },
    "required": ["pairs"],
}
LATER_SCHEMA = {
    "type": "object",
    "properties": {"later": {"type": "string"}},
    "required": ["later"],
}


def _concept_document(memory_dir: Path) -> dict:
    path = Path(memory_dir) / CONCEPTS_FILE
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def grown_days(memory_dir: Path) -> set[date]:
    out: set[date] = set()
    for entry in _concept_document(memory_dir).get("growth", []):
        try:
            out.add(date.fromisoformat(str(entry["on"])))
        except (KeyError, ValueError):
            continue
    return out


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _append_growth(memory_dir: Path, day: date, pairs: list[tuple[str, str]]) -> None:
    path = Path(memory_dir) / CONCEPTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.exists() and path.stat().st_size and not path.read_text(encoding="utf-8").endswith("\n"):
        prefix = "\n"
    encoded = "[" + ", ".join(f"[{_toml_string(a)}, {_toml_string(b)}]" for a, b in pairs) + "]"
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f'{prefix}[[growth]]\non = "{day.isoformat()}"\npairs = {encoded}\n')
        f.flush()
        os.fsync(f.fileno())


def _concepts_by_day(pages: list[Page]) -> dict[date, list[str]]:
    """本人の言葉まで書けた日だけを、古い順に返す。途中の未完成日は越えない。"""
    grouped: dict[date, list[Page]] = {}
    for page in pages:
        if page.kind != "episode" or page.start is None:
            continue
        day = serina_day_id(page.start)
        grouped.setdefault(day, []).append(page)
    out: dict[date, list[str]] = {}
    for day in sorted(grouped):
        day_pages = grouped[day]
        if not all(page.written for page in day_pages):
            break
        names = out.setdefault(day, [])
        for page in day_pages:
            for concept in page.concepts:
                if concept not in names:
                    names.append(concept)
    return out


def _concept_prompt(persona: str, day: date, new: list[str], known: list[str]) -> str:
    return f"""{persona}

---
{_SELF}今は眠っている間。{day.isoformat()}に出てきた呼び名を、これまでの呼び名と見比べている。
「同じものを別の言い方で呼んでいる」と確かに思う組だけを選んで。似ているだけのものは組にしない。

【今日初めて出てきた名前】
{chr(10).join("- " + x for x in new)}

【これまでに出てきた名前】
{chr(10).join("- " + x for x in known) or "（まだない）"}

次のJSONだけを返す。組は0〜{MAX_PAIRS}個。
{{"pairs":[{{"a":"上の一覧にある名前","b":"上の一覧にある別の名前"}}]}}"""


def _parse_pairs(answer: dict, new: list[str], known: list[str]) -> list[tuple[str, str]]:
    visible = {normalize(x): x for x in new + known}
    fresh = {normalize(x) for x in new}
    raw = _field(answer, "pairs", [])
    if not isinstance(raw, list):
        raise WordsRejected("pairs が配列でない")
    out: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise WordsRejected("pairs の中身が組でない")
        a, b = item.get("a"), item.get("b")
        if not isinstance(a, str) or not isinstance(b, str):
            raise WordsRejected("呼び名が文字列でない")
        na, nb = normalize(a), normalize(b)
        if na not in visible or nb not in visible:
            raise WordsRejected("一覧にない呼び名が含まれている")
        if na == nb or not ({na, nb} & fresh):
            continue
        if na in fresh and nb not in fresh:
            pair = (visible[nb], visible[na])  # これまでの呼び名を代表に保つ
        else:
            pair = (visible[na], visible[nb])
        if pair not in out and (pair[1], pair[0]) not in out:
            out.append(pair)
    return out[:MAX_PAIRS]


def grow_concepts(memory_dir: Path, pages: list[Page], *, persona: str, ask: Ask) -> int:
    """未処理の日を古い順に見る。空の追記も「その日は見た」という痕跡になる。"""
    by_day = _concepts_by_day(pages)
    done = grown_days(memory_dir)
    all_seen: list[str] = []
    changed = 0
    for day in sorted(by_day):
        names = by_day[day]
        if day in done:
            for name in names:
                if name not in all_seen:
                    all_seen.append(name)
            continue
        new = [name for name in names if normalize(name) not in {normalize(x) for x in all_seen}]
        pairs: list[tuple[str, str]] = []
        if new and all_seen:
            prompt = _concept_prompt(persona, day, new, all_seen)
            try:
                pairs = _ask_until_valid(ask, prompt, CONCEPT_SCHEMA, lambda answer: _parse_pairs(answer, new, all_seen))
            except WordsRejected as exc:
                raise GrowthRejected(f"{day.isoformat()} の呼び名を書けなかった: {exc}", changed=changed) from exc
        _append_growth(memory_dir, day, pairs)
        changed += 1
        for name in names:
            if name not in all_seen:
                all_seen.append(name)
    return changed


def _clean_later(answer: dict) -> str:
    value = _field(answer, "later", "")
    if not isinstance(value, str):
        raise WordsRejected("later が文字列でない")
    value = re.sub(r"[ \t]+", " ", value).strip().strip("「」")
    if len(value) > LATER_MAX:
        raise WordsRejected(f"later が長すぎる（{len(value)}字）")
    return value


def _later_prompt(persona: str, page: Page, waking: Waking | None, relation: Relation, day: date) -> str:
    now_self = waking.self_text if waking else "（まだ書かれていない）"
    relationship = render_for_pack(relation, day) or "（まだ書かれていない）"
    return f"""{persona}

---
{_SELF}今は眠っている間。今日ふと思い出した、30日以上前の記憶を読み返している。
昔の自分が書いた本文は変えない。今の自分から見ると意味が変わった、付け足しておきたい見方があるときだけ「今思うと」を書く。
変わっていなければ空の文字列にして。

【昔の記憶】
題：{page.title}
要点：{page.gist}
{page.body[:1200]}

【今の自分】
{now_self[:600]}

【今のマスターとのこと】
{relationship[:900]}

次のJSONだけを返す。
{{"later":"今思うと、の一文〜三文。変わっていなければ空"}}"""


def reconsolidate(
    memory_dir: Path,
    recall_log: RecallLog,
    *,
    day: date,
    persona: str,
    ask: Ask,
    waking: Waking | None,
    relation: Relation,
) -> int:
    pages = {page.id: page for page in load_pages(memory_dir)}
    previous_day = day - timedelta(days=1)
    rows = [
        row
        for calendar_day in (previous_day, day)
        for row in recall_log.entries_on(calendar_day)
        if row.get("intent") != "replay"
        and serina_day_id(datetime.fromisoformat(str(row.get("ts", "")))) == previous_day
    ]
    candidates: list[tuple[float, Page]] = []
    seen: set[str] = set()
    cutoff = day - timedelta(days=OLD_DAYS)
    for row in rows:
        pid = str(row.get("page", ""))
        page = pages.get(pid)
        if pid in seen or page is None or page.kind != "episode" or page.start is None:
            continue
        seen.add(pid)
        if serina_day_id(page.start) > cutoff:
            continue
        if any(item.get("on") == day.isoformat() for item in page.later):
            continue
        candidates.append((float(row.get("activation", 0.0)), page))
    already_today = sum(
        1
        for page in pages.values()
        if any(item.get("on") == day.isoformat() for item in page.later)
    )
    remaining = max(0, MAX_LATER - already_today)
    changed = 0
    for _, page in sorted(candidates, key=lambda item: -item[0])[:remaining]:
        prompt = _later_prompt(persona, page, waking, relation, day)
        try:
            words = _ask_until_valid(ask, prompt, LATER_SCHEMA, _clean_later)
        except WordsRejected as exc:
            raise GrowthRejected(f"{page.id} の今思うとを書けなかった: {exc}", changed=changed) from exc
        current = next((p for p in load_pages(memory_dir) if p.id == page.id), None)
        if current is None:
            continue
        write_page(memory_dir, replace(current, later=current.later + ({"on": day.isoformat(), "text": words},)))
        changed += 1
    return changed


def replay(memory_dir: Path, recall_log: RecallLog, *, day: date) -> int:
    """最近7日の大事で心が動いた出来事を、最大2つだけ眠りの中で再生する。"""
    previous_day = day - timedelta(days=1)
    rows = [
        row
        for calendar_day in (previous_day, day)
        for row in recall_log.entries_on(calendar_day)
        if serina_day_id(datetime.fromisoformat(str(row.get("ts", "")))) == previous_day
    ]
    already = {str(row.get("page", "")) for row in rows}
    replayed = {str(row.get("page", "")) for row in rows if row.get("intent") == "replay"}
    remaining = MAX_REPLAY - len(replayed)
    if remaining <= 0:
        return 0
    pages = [
        page
        for page in load_pages(memory_dir)
        if page.kind == "episode"
        and page.written
        and page.start is not None
        and day - timedelta(days=REPLAY_DAYS) <= serina_day_id(page.start) < day
        and page.id not in already
    ]
    importance = ranks({page.id: page.importance for page in pages})
    arousal = ranks({page.id: page.arousal for page in pages})
    picked = sorted(pages, key=lambda page: (importance[page.id] + arousal[page.id], page.start), reverse=True)[:remaining]
    at = datetime.combine(day, time(hour=6, minute=55), tzinfo=JST)
    for page in picked:
        recall_log.append(ts=at, page=page.id, activation=0.0, vivid=False, intent="replay")
        at += timedelta(seconds=1)
    return len(picked)
