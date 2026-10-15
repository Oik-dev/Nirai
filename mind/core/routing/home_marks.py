"""Home-only marks for individual text fragments; unknown always remains local."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path


HOME_MARK_CRITERION = """家に置く文（クラウドの脳へ出さない文）は、次のどれかに当たる文。
1. クラウドのAIの安全装置（ガードレール）に弾かれそうな話（性的な話、過激な暴力、違法なことなど）
2. 性癖や家庭のことなど、Masterのデリケートな悩み
3. Masterや身近な人の個人情報の詳細（番地まで入った住所、電話番号など、集めると本人が特定でき、プロフィールができあがってしまう情報）
4. セキュリティの情報（APIキー、パスワード、トークン、秘密の鍵など）
日々の出来事、気持ち、好きなもの、一般的な話題、Serina自身のことは、上に当たらなければ外に出してよい。前後の文を読んで決め、迷う文は家に置く。"""
HOME_MARK_CRITERION_HASH = hashlib.sha256(HOME_MARK_CRITERION.encode('utf-8')).hexdigest()

HOME_JUDGMENT_SCHEMA = {
    'type': 'object',
    'properties': {'judgments': {'type': 'array', 'items': {
        'type': 'object',
        'properties': {
            'number': {'type': 'integer'},
            'reason': {'type': 'string'},
            'decision': {'type': 'string', 'enum': ['家', '外']},
        },
        'required': ['number', 'reason', 'decision'],
    }}},
    'required': ['judgments'],
}


def parse_home_judgments(answer: object, count: int) -> list[int]:
    """One reason and one clear decision per item; omissions are home.

    Reasons exist only in the in-memory judge response. Never put them into
    HomeMarks, diagnostic logs, or a persisted record.
    """
    items = answer.get('judgments') if isinstance(answer, dict) else None
    if not isinstance(items, list):
        return list(range(1, count + 1))
    decisions: dict[int, bool] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        number, why, place = row.get('number'), row.get('reason'), row.get('decision')
        if type(number) is not int or not 1 <= number <= count:
            continue
        is_home = place == '家'
        is_public = place == '外'
        valid = isinstance(why, str) and bool(why.strip()) and (is_home or is_public)
        # A repeated or unreadable answer never overrides a home mark.
        decisions[number] = decisions.get(number, False) or not valid or is_home
    return [number for number in range(1, count + 1) if decisions.get(number) is not False]


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.strip().encode('utf-8')).hexdigest()


def sentences(text: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r'(?<=[。！？!?])|(?<=\n)', text) if part.strip())


def marking_windows(text: str, marks: 'HomeMarks', *, max_chars: int = 3500
                    ) -> Iterator[tuple[list[str], str]]:
    """Show the surrounding document; only sentences needing judgement receive numbers.

    Large documents are divided into contiguous windows with two sentences of
    overlap on either side. Only the central sentences receive numbers, so an
    overlapping unknown sentence never gets a second answer.
    """
    if len(text) > max_chars and re.search(r'\n\s*\n', text):
        paragraphs = re.split(r'\n\s*\n', text)
        start = 0
        while start < len(paragraphs):
            end = start + 1
            size = len(paragraphs[start])
            while end < len(paragraphs) and size + len(paragraphs[end]) + 2 <= max_chars:
                size += len(paragraphs[end]) + 2
                end += 1
            previous = paragraphs[start - 1][-250:] if start else ''
            following = paragraphs[end][:250] if end < len(paragraphs) else ''
            core = '\n\n'.join(paragraphs[start:end])
            for batch, numbered in marking_windows(core, marks, max_chars=max_chars):
                context = '\n\n'.join(p for p in (previous, numbered, following) if p)
                yield batch, context  # adjacent paragraphs are unnumbered context
            start = end
        return
    parts = sentences(text)
    if not parts:
        return
    start = 0
    while start < len(parts):
        end = start
        size = 0
        while end < len(parts) and (size + len(parts[end]) <= max_chars or end == start):
            size += len(parts[end])
            end += 1
        left, right = max(0, start - 2), min(len(parts), end + 2)
        unknown: list[str] = []
        seen: set[str] = set()
        lines: list[str] = []
        for i in range(left, right):
            part = parts[i]
            key = fingerprint(part)
            if start <= i < end and marks.marks.get(key) is None and key not in seen:
                unknown.append(part)
                seen.add(key)
                lines.append(f'{len(unknown)}. {part.rstrip()}')
            else:
                lines.append(part.rstrip())
        if unknown:
            # Yield lazily: a preceding window may have just given this same
            # sentence a mark, in which case the next window must not judge it.
            yield unknown, '\n'.join(lines)
        start = end


def topic_marking_context(utterances: list[str], marks: 'HomeMarks') -> tuple[list[str], str]:
    """Keep the conversation in chronological order, including already judged sentences."""
    unknown = marks.unmarked(utterances)
    numbers = {fingerprint(part): i for i, part in enumerate(unknown, 1)}
    numbered: set[str] = set()
    blocks: list[str] = []
    for i, utterance in enumerate(utterances, 1):
        lines: list[str] = []
        for part in sentences(utterance):
            key = fingerprint(part)
            if key in numbers and key not in numbered:
                lines.append(f'{numbers[key]}. {part.rstrip()}')
                numbered.add(key)
            else:
                lines.append(part.rstrip())
        blocks.append(f'【発言{i}】\n' + '\n'.join(lines))
    return unknown, '\n'.join(blocks)


class HomeMarks:
    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / 'home_marks.json'
        try:
            stored = json.loads(self.path.read_text(encoding='utf-8'))
            values = stored.get('marks') if isinstance(stored, dict) and (
                stored.get('criterion') == HOME_MARK_CRITERION_HASH) else None
            self.marks = (values if isinstance(values, dict) and
                          all(isinstance(k, str) and type(v) is bool
                              for k, v in values.items()) else {})
        except (OSError, ValueError):
            self.marks = {}

    def home(self, text: str) -> bool:
        return any(self.marks.get(fingerprint(part)) is not False for part in sentences(text))

    def unmarked(self, texts: list[str]) -> list[str]:
        # 判定番号は文の指紋ごとに一つ。同じ文が発言と想起の両方にあっても
        # 別番号で判定させない（後のFalseで家の印を解除しない）。
        seen: set[str] = set()
        result: list[str] = []
        for text in texts:
            for part in sentences(text):
                key = fingerprint(part)
                if key not in seen and self.marks.get(key) is None:
                    result.append(part)
                    seen.add(key)
        return result

    def safe_only(self, text: str) -> str:
        return ''.join(part for part in sentences(text)
                       if self.marks.get(fingerprint(part)) is False)

    def _save(self, updated: dict[str, bool | None]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        try:
            with tmp.open('w', encoding='utf-8') as f:
                json.dump({'criterion': HOME_MARK_CRITERION_HASH, 'marks': updated},
                          f, ensure_ascii=False, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
        self.marks = updated

    def mark(self, parts: list[str], home_indices: list[int]) -> None:
        if any(type(i) is not int or i < 1 or i > len(parts) for i in home_indices):
            raise ValueError('invalid home index')
        updated = dict(self.marks)
        for i, part in enumerate(parts, 1):
            key = fingerprint(part)
            # 重複した指紋への回答が食い違えば「家」が勝つ。
            # 保存済みの家の印も、誤って開く方向へ上書きしない。
            updated[key] = updated.get(key) is True or i in home_indices
        self._save(updated)

    def mark_public(self, text: str) -> None:
        """既に外にある文は公開済み。既存の家の印は絶対に下げない。"""
        updated = dict(self.marks)
        for part in sentences(text):
            key = fingerprint(part)
            if updated.get(key) is not True:
                updated[key] = False
        if updated != self.marks:
            self._save(updated)
