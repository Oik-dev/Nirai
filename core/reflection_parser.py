"""蒸留出力の前方一致パーサ（12Bの構文崩れ耐性）

厳密なXMLパースは行わない。閉じタグ欠損・タグ外の前置き・トークン切れを前提に、
クラッシュせず取れるだけ取る（設計書§3b「Pythonパーサーの例外処理」）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_TOP_TAGS = (
    "diary",
    "new_facts",
    "state_update",
    "open_threads",
    "fact_updates",
    "resolved_threads",
)


def extract_block(tag: str, text: str) -> str | None:
    """<tag>〜</tag> を抽出。閉じタグが無ければ次のトップレベル開始タグ or 文末まで前方サルベージ。"""
    m = re.search(rf"<{tag}(?:\s[^>]*)?>", text)
    if not m:
        return None
    start = m.end()
    close = re.search(rf"</{tag}>", text[start:])
    if close:
        return text[start : start + close.start()].strip()
    others = "|".join(t for t in _TOP_TAGS if t != tag)
    nxt = re.search(rf"<(?:{others})(?:\s[^>]*)?>", text[start:])
    return text[start : start + nxt.start()].strip() if nxt else text[start:].strip()


@dataclass
class ReflectionResult:
    diary: str | None = None
    facts: list[dict[str, Any]] = field(default_factory=list)  # {content, keywords: [..]}
    state: dict[str, dict[str, str]] = field(default_factory=dict)  # {param: {value, reason}}
    narrative_mood: str | None = None
    open_threads: list[dict[str, str]] = field(default_factory=list)  # {question, context}
    fact_updates: list[dict[str, Any]] = field(default_factory=list)  # {target_id, content, reason}
    resolved_threads: list[dict[str, Any]] = field(default_factory=list)  # {id, reason}


def _split_keywords(raw: str) -> list[str]:
    return [k.strip() for k in raw.replace("、", ",").split(",") if k.strip()]


def parse_reason_output(text: str) -> ReflectionResult:
    r = ReflectionResult()

    block = extract_block("new_facts", text) or ""
    for m in re.finditer(
        r'<fact(?:\s+keywords="([^"]*)")?\s*>(.*?)(?:</fact>|(?=<fact)|\Z)',
        block,
        re.DOTALL,
    ):
        content = m.group(2).strip()
        if content:
            r.facts.append({"content": content, "keywords": _split_keywords(m.group(1) or "")})

    block = extract_block("state_update", text) or ""
    for m in re.finditer(
        r'<param\s+name="(\w+)"\s+value="([\d.+-]+)"\s*/?>([^<]*)',
        block,
    ):
        r.state[m.group(1)] = {"value": m.group(2), "reason": m.group(3).strip()}
    mood = re.search(r"<narrative_mood>(.*?)(?:</narrative_mood>|\Z)", block, re.DOTALL)
    if mood and mood.group(1).strip():
        r.narrative_mood = mood.group(1).strip()

    block = extract_block("open_threads", text) or ""
    for m in re.finditer(
        r'<thread(?:\s+context="([^"]*)")?\s*>(.*?)(?:</thread>|(?=<thread)|\Z)',
        block,
        re.DOTALL,
    ):
        q = m.group(2).strip()
        if q:
            r.open_threads.append({"question": q, "context": (m.group(1) or "").strip()})

    block = extract_block("fact_updates", text) or ""
    for m in re.finditer(
        r'<update\s+target_id="(\d+)"(?:\s+reason="([^"]*)")?\s*>(.*?)(?:</update>|(?=<update)|\Z)',
        block,
        re.DOTALL,
    ):
        r.fact_updates.append(
            {
                "target_id": int(m.group(1)),
                "reason": (m.group(2) or "").strip(),
                "content": m.group(3).strip(),
            }
        )

    block = extract_block("resolved_threads", text) or ""
    for m in re.finditer(
        r'<resolved\s+id="(\d+)"\s*/?>([^<]*)',
        block,
    ):
        r.resolved_threads.append({"id": int(m.group(1)), "reason": m.group(2).strip()})

    return r


def parse_diary(text: str) -> str | None:
    return extract_block("diary", text)
