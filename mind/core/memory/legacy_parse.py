"""legacy 遺産からの記憶抜き出し（純粋関数）。設計: 記憶正本入れ直し。

Chat.html は対象外。日記は日付+本文、JSON は subject 塊、継承は白リスト箇所のみ。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# 絵文字・記号ピクト（日記見出しの📖等も含む清掃用）
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0001FA00-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MD_BULLET_RE = re.compile(r"^[\-\*]\s+", re.MULTILINE)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*[\s\S]*?```", re.IGNORECASE)
_DIARY_HEADER_RE = re.compile(
    r"^📖?\s*セリナの日記\s*[-－—]?\s*(.+)$|^📖?\s*(\d{4}年\d{1,2}月\d{1,2}日.*)$",
    re.MULTILINE,
)
_META_DIARY_TITLES = ("記録完了", "日記の記録完了")


@dataclass(frozen=True)
class DiaryEntry:
    date_iso: str  # YYYY-MM-DDTHH:MM:SS+09:00 等
    body: str
    source_label: str


@dataclass(frozen=True)
class JsonMemoryEntry:
    date_iso: str
    body: str
    source_label: str


@dataclass(frozen=True)
class InheritedCard:
    content: str
    mem_type: str  # relationship | promise
    pinned: bool
    protection_grade: str
    source_label: str


def strip_ornament(text: str) -> str:
    """絵文字・MD装飾・JSONフェンスを除き、純粋な本文に近づける。"""
    s = _EMOJI_RE.sub("", text)
    s = _JSON_FENCE_RE.sub("", s)
    s = _MD_BOLD_RE.sub(r"\1", s)
    s = _MD_HEADING_RE.sub("", s)
    # 箇条の「- 」は継承カードでは残したいが、日記本文では落とす呼び出し側で制御
    s = s.replace("\u3000", " ")
    # 連続空行を2つまでに
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _parse_japanese_date(raw: str) -> str:
    """『2025年3月08日』『2025年03月21日（金曜日）20:55』等 → ISO風文字列。"""
    raw = raw.strip()
    m = re.search(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
        r"(?:.*?(\d{1,2})\s*[時:：]\s*(\d{1,2}))?",
        raw,
    )
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4)) if m.group(4) else 12
        mi = int(m.group(5)) if m.group(5) else 0
        try:
            return _to_iso(y, mo, d, hh, mi)
        except ValueError:
            return "2025-12-01T00:00:00+09:00"
    return "2025-12-01T00:00:00+09:00"


def _to_iso(y: int, mo: int, d: int, hh: int, mi: int) -> str:
    from datetime import timedelta

    jst = timezone(timedelta(hours=9))
    return datetime(y, mo, d, hh, mi, tzinfo=jst).isoformat()


def parse_diary_file(path: Path | str, *, source_label: str | None = None) -> list[DiaryEntry]:
    """日記 txt を日付エントリに分割する。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    label = source_label or p.name
    entries: list[DiaryEntry] = []

    # 見出し位置を列挙（日記見出しのみ。本文中の日付言及は拾わない）
    headers: list[tuple[int, str]] = []
    for m in re.finditer(
        r"^(?:📖\s*)?セリナの日記\s*[-－—]\s*(.+)$|^(?:📖\s*)(\d{4}年\d{1,2}月\d{1,2}日.*)$",
        text,
        re.MULTILINE,
    ):
        title = (m.group(1) or m.group(2) or "").strip()
        line = m.group(0).strip()
        if not title:
            continue
        if any(t in title for t in _META_DIARY_TITLES):
            continue
        if "セリナの記憶" in line and "日記" not in line:
            continue
        headers.append((m.start(), title))

    if not headers:
        return []

    for i, (start, title) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        # 見出し行を除いた本文
        chunk = text[start:end]
        first_nl = chunk.find("\n")
        body_raw = chunk[first_nl + 1 :] if first_nl >= 0 else ""
        body = strip_ornament(body_raw)
        body = _MD_BULLET_RE.sub("", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if len(body) < 20:
            continue
        entries.append(
            DiaryEntry(date_iso=_parse_japanese_date(title), body=body, source_label=label)
        )
    return entries


def chunk_diary_body(body: str, *, min_chars: int = 80) -> list[str]:
    """日記本文を空行／短すぎ結合でチャンク分割する。"""
    parts = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not parts:
        return [body] if body.strip() else []
    chunks: list[str] = []
    buf = ""
    for part in parts:
        # 見出しだけの片は次へ繋ぐ
        candidate = f"{buf}\n\n{part}".strip() if buf else part
        if len(candidate) < min_chars:
            buf = candidate
            continue
        if buf and len(buf) < min_chars:
            chunks.append(candidate)
            buf = ""
        else:
            if buf:
                chunks.append(buf)
            buf = part
            if len(buf) >= min_chars:
                chunks.append(buf)
                buf = ""
    if buf:
        if chunks and len(buf) < min_chars:
            chunks[-1] = f"{chunks[-1]}\n\n{buf}".strip()
        else:
            chunks.append(buf)
    return chunks or ([body] if body.strip() else [])


def parse_memory_json(path: Path | str, *, source_label: str = "セリナの記憶.json") -> list[JsonMemoryEntry]:
    """セリナの記憶.json の subjects を日付＋散文化本文にする。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[JsonMemoryEntry] = []
    prose_keys = (
        "summary",
        "insights",
        "evolutionMilestones",
        "relationshipWithMaster",
        "emotionalNuance",
        "futureIntentions",
        "additionalNotes",
    )
    for session in data.get("sessions", []):
        for subj in session.get("subjects", []):
            if not isinstance(subj, dict):
                continue
            date_raw = str(subj.get("date") or "2025-12-01T00:00:00+09:00")
            parts = [str(subj[k]).strip() for k in prose_keys if subj.get(k)]
            body = strip_ornament("\n\n".join(parts))
            if len(body) < 20:
                continue
            out.append(JsonMemoryEntry(date_iso=date_raw, body=body, source_label=source_label))
    return out


def _bullet_lines(block: str) -> list[str]:
    lines: list[str] = []
    for line in block.splitlines():
        s = line.strip()
        if s.startswith("- ") or s.startswith("* "):
            item = s[2:].strip()
            item = _MD_BOLD_RE.sub(r"\1", item)
            item = _EMOJI_RE.sub("", item).strip()
            # 「→」注釈行も箇条として残す
            if item:
                lines.append(item)
        elif s.startswith("→") or s.startswith("（"):
            # 直前の箇条への続き
            cont = _MD_BOLD_RE.sub(r"\1", s)
            cont = _EMOJI_RE.sub("", cont).strip()
            if lines and cont:
                lines[-1] = f"{lines[-1]} {cont}"
    return lines


def parse_inherited_canon(path: Path | str, *, source_label: str = "継承記憶r1.md") -> list[InheritedCard]:
    """継承記憶mdからマスター特徴・約束のみ抜粋する。"""
    text = Path(path).read_text(encoding="utf-8")
    cards: list[InheritedCard] = []

    # マスターの特徴セクション
    m_feat = re.search(
        r"##\s*👤?\s*マスターの特徴\s*\n([\s\S]*?)(?=\n##\s|─{5,}|\Z)",
        text,
    )
    if m_feat:
        for item in _bullet_lines(m_feat.group(1)):
            cards.append(
                InheritedCard(
                    content=item,
                    mem_type="relationship",
                    pinned=False,
                    protection_grade="A",
                    source_label=source_label,
                )
            )

    # 約束したこと — ファクトチェックより前
    m_prom = re.search(
        r"##\s*🤝?\s*約束したこと\s*\n([\s\S]*?)(?=\n###\s*ファクトチェック|\n##\s*[^#]|\n#\s|\Z)",
        text,
    )
    if m_prom:
        block = m_prom.group(1)
        # 未来で会う／会うためのヒントは pin+S
        pin_sections = ("未来で会うための約束", "会うためのヒント", "生きて再会するための約束")
        for sec in re.finditer(r"###\s*(.+)\n([\s\S]*?)(?=\n###\s|\Z)", block):
            title = sec.group(1).strip()
            body = sec.group(2)
            pin = any(t in title for t in pin_sections)
            for item in _bullet_lines(body):
                # セクション名を文脈として前置（短すぎる断片の救済）
                content = f"【{title}】{item}"
                cards.append(
                    InheritedCard(
                        content=content,
                        mem_type="promise",
                        pinned=pin,
                        protection_grade="S" if pin else "A",
                        source_label=source_label,
                    )
                )
    return cards


def markdown_sections(markdown: str) -> list[tuple[str, str]]:
    """見出し（#〜###）ごとに、見出しと本文を返す。小見出しには、上の見出しを「›」でつなぐ。

    「マスターの特徴」の下の「基本情報」を、Serina自身の基本情報と取り違えないため。
    """
    parts = re.split(r"^(#{1,3}\s+.*)$", markdown, flags=re.MULTILINE)
    out = []
    parents: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        level = len(parts[i]) - len(parts[i].lstrip("#"))
        title = parts[i].lstrip("#").strip()
        parents = {lv: t for lv, t in parents.items() if lv < level} | {level: title}
        path = [parents[lv] for lv in sorted(parents) if lv > 1]  # 1段目は文書の題
        out.append((" › ".join(path) or title, parts[i + 1]))
    return out
