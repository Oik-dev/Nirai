"""問題の形と、問題集（cases.jsonl）の読み書き。

1問は「いつ（now）、どんな流れ（recent）で、Masterが何と言ったか（utterance）」と、正解の持ち方からなる。
正解は次のどれか。
- marks：目印の組の並び。どの組も、組の中のどれか1つが、思い出したものの中に見つかれば当たり。
  ふつうは1組。「変化」は、前の様子と今の様子の2組。
- period：この期間（日付の両端を含む）の出来事が思い出されれば当たり。「時間」で使う。
- どちらもない：「黙る」は何も浮かばなければ合格。「実際の会話」は、浮かんだものが話に関係あるかをGemmaが判定する。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

KINDS = {
    "direct": "正面から",
    "cue": "自然に浮かぶ",
    "followup": "続きの話",
    "time": "時間",
    "change": "変化",
    "silence": "黙る",
    "replay": "実際の会話",
}


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    utterance: str
    now: str  # ISO 8601（時差つき）
    recent: tuple[Turn, ...] = ()
    source: str = ""  # record.SOURCES のキー。どこの記録についての問題か
    target: str = ""  # 材料にした記録の単位（record.Unit.id）
    marks: tuple[tuple[str, ...], ...] = ()
    period: tuple[str, str] | None = None
    author: str = ""  # gemma（Gemmaが記録から作った）／template（型から作った）／replay（実際の会話）／claude

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"知らない種類: {self.kind}")
        datetime.fromisoformat(self.now)

    @property
    def now_at(self) -> datetime:
        return datetime.fromisoformat(self.now)


def case_from_json(row: dict) -> Case:
    return Case(
        id=row["id"],
        kind=row["kind"],
        utterance=row["utterance"],
        now=row["now"],
        recent=tuple(Turn(**turn) for turn in row.get("recent", [])),
        source=row.get("source", ""),
        target=row.get("target", ""),
        marks=tuple(tuple(group) for group in row.get("marks", [])),
        period=tuple(row["period"]) if row.get("period") else None,
        author=row.get("author", ""),
    )


def load_cases(path: Path) -> list[Case]:
    with path.open(encoding="utf-8") as f:
        return [case_from_json(json.loads(raw)) for raw in f if raw.strip()]


def save_cases(path: Path, cases: list[Case]) -> None:
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("問題のIDが重複している")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for case in cases:
            f.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")
    tmp.replace(path)


@dataclass
class CaseSet:
    """問題集の置き場所（イデアの data/memory_test/）。"""

    root: Path
    cases: Path = field(init=False)
    drafts: Path = field(init=False)
    review: Path = field(init=False)
    judgments: Path = field(init=False)
    results: Path = field(init=False)

    def __post_init__(self) -> None:
        self.cases = self.root / "cases.jsonl"
        self.drafts = self.root / "drafts.jsonl"
        self.review = self.root / "review.jsonl"
        self.judgments = self.root / "judgments.jsonl"
        self.results = self.root / "results"

    @classmethod
    def of_idea(cls, idea: Path) -> CaseSet:
        return cls(idea / "data" / "memory_test")
