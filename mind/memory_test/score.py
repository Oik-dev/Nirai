"""採点と集計。結果には問題のIDと数だけを残し、中身（発言や記憶の文）は残さない。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean

from mind.memory_test.cases import KINDS, Case
from mind.memory_test.memory import Recalled
from mind.memory_test.record import SOURCES, normalize

# Gemmaの判定：0 関係ない／1 少し関係ある／2 関係ある（思い出すと話が生きる）
UNRELATED, LOOSE, RELATED = 0, 1, 2


@dataclass(frozen=True)
class CaseResult:
    id: str
    kind: str
    source: str
    items: int  # 浮かんだ数
    chars: int  # 住人の手元に渡した文字数
    hit: bool | None = None  # 当たり（「黙る」は黙れたこと）。判定で測る問題は None
    period_share: float | None = None  # 「時間」：浮かんだもののうち、その時期の出来事の割合
    relevance: tuple[int, ...] | None = None  # 「実際の会話」：浮かんだもの1つずつへのGemmaの判定


def found(group: tuple[str, ...], texts: list[str]) -> bool:
    return any(normalize(mark) in text for mark in group for text in texts)


def score_case(case: Case, recalled: list[Recalled], relevance: list[int] | None = None) -> CaseResult:
    texts = [normalize(r.text) + "\n" + normalize(r.evidence) for r in recalled]
    hit, period_share = None, None
    if case.marks:
        hit = all(found(group, texts) for group in case.marks)
    elif case.period:
        start, end = (date.fromisoformat(d) for d in case.period)
        inside = [r.when is not None and start <= r.when <= end for r in recalled]
        hit, period_share = any(inside), _mean(inside)
    elif case.kind == "silence":
        hit = not recalled
    return CaseResult(
        id=case.id,
        kind=case.kind,
        source=case.source,
        items=len(recalled),
        chars=sum(len(r.text) for r in recalled),
        hit=hit,
        period_share=period_share,
        relevance=tuple(relevance) if relevance is not None else None,
    )


def _mean(values) -> float | None:  # noqa: ANN001
    values = [float(v) for v in values]
    return mean(values) if values else None


def summarize(results: list[CaseResult]) -> dict:
    by_kind: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        by_kind[result.kind].append(result)
    summary: dict = {}
    for kind in KINDS:
        group = by_kind.get(kind)
        if not group:
            continue
        row: dict = {
            "問題数": len(group),
            "平均の数": _mean(r.items for r in group),
            "平均の文字数": _mean(r.chars for r in group),
        }
        if kind == "replay":
            judged = [r for r in group if r.relevance is not None]
            row["判定した発言"] = len(judged)
            if judged:
                row["関係ない想起（1発言あたり）"] = _mean(r.relevance.count(UNRELATED) for r in judged)
                row["関係ある想起があった発言"] = _mean(RELATED in r.relevance for r in judged)
                row["何も浮かばなかった発言"] = _mean(r.items == 0 for r in judged)
            answered = [r for r in group if r.hit is not None]
            if answered:
                row[f"当たり（答えのある{len(answered)}発言）"] = _mean(bool(r.hit) for r in answered)
        else:
            row["当たり" if kind != "silence" else "黙れた"] = _mean(bool(r.hit) for r in group)
        if kind == "time":
            row["その時期の出来事の割合"] = _mean(r.period_share for r in group if r.period_share is not None)
        by_source: dict[str, list[bool]] = defaultdict(list)
        for r in group:
            if r.hit is not None and r.source:
                by_source[r.source].append(r.hit)
        if len(by_source) > 1:
            row["出どころ別の当たり"] = {SOURCES.get(s, s): _mean(v) for s, v in sorted(by_source.items())}
        summary[KINDS[kind]] = row
    return summary


def render(summary: dict) -> str:
    lines = []
    for kind, row in summary.items():
        lines.append(f"■ {kind}")
        for key, value in row.items():
            if isinstance(value, dict):
                inner = "、".join(f"{k} {_fmt(k, v)}" for k, v in value.items())
                lines.append(f"    {key}: {inner}")
            else:
                lines.append(f"    {key}: {_fmt(key, value)}")
    return "\n".join(lines)


def _fmt(key: str, value) -> str:  # noqa: ANN001
    if value is None:
        return "—"
    if isinstance(value, float) and not key.startswith("平均") and "1発言あたり" not in key:
        return f"{value:.0%}"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)
