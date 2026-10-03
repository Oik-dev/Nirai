"""記憶の強さ（docs/plans/長期記憶の作り直し.md §6）。

ACT-R の基礎活性：B = ln（Σ 痕跡の重み ×（今 − その時刻）^−d）。痕跡は、出来事が起きた時と、思い出された各時点。
減衰 d は、その時点の活性が高いほど大きい（Pavlik & Anderson 2005）。だから、すぐに思い出し直すより、
間をあけて思い出したほうが長持ちする。最初の痕跡の重みは、本人がつけた大事さと、そのときの心の動きで決まる
（心が動いた出来事ほど強く残る。McGaugh、LUFY）。大事さと心の動きは、その住人の記憶の中での順位（0〜1）で使う。
脳がつける数はどれも高めに偏るので（Serinaの最初の記憶では、日記40ページすべてが大事さ10）、数そのものより、
自分の人生の中でどれだけ大事だったかのほうが確かだから。

強さは、出来事の時刻と思い出した時刻の並びだけから計算できる。だから記憶の索引が壊れても、記録から作り直せる。
時間の単位は日。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

DAY_SECONDS = 86400.0


@dataclass(frozen=True)
class StrengthParams:
    """値は config/thresholds.toml の [activation]（recall.load_recall_params）。"""

    decay_floor: float  # α：どの痕跡にもある減衰
    decay_scale: float  # c：思い出した時の活性が高いほど、その痕跡は速く減衰する
    decay_max: float  # 減衰の上限（日の単位では活性の値が大きく出るので、極端な減衰にしない）
    importance_weight: float  # 大事さの順位が最初の痕跡に足す強さ（一番上で +この値、一番下で -この値）
    arousal_weight: float  # 心の動きの順位が最初の痕跡に足す強さ（同上）
    min_age_days: float  # これより近い痕跡は、この古さとして扱う（今この瞬間の痕跡で無限大にしない）


def encoding_boost(importance_rank: float, arousal_rank: float, params: StrengthParams) -> float:
    """最初の痕跡の重み（対数）。順位は0（一番下）〜1（一番上）。分からなければ0.5。"""
    return params.importance_weight * (2 * importance_rank - 1) + params.arousal_weight * (2 * arousal_rank - 1)


def ranks(values: dict[str, float | None]) -> dict[str, float]:
    """値の順位（0〜1）。同じ値は同じ順位（平均）。値のないものは0.5。"""
    known = sorted(v for v in values.values() if v is not None)
    if len(known) < 2:
        return {key: 0.5 for key in values}
    out = {}
    for key, value in values.items():
        if value is None:
            out[key] = 0.5
            continue
        below = sum(1 for v in known if v < value)
        same = sum(1 for v in known if v == value)
        out[key] = (below + (same - 1) / 2) / (len(known) - 1)
    return out


def _age_days(then: datetime, now: datetime, params: StrengthParams) -> float:
    return max((now - then).total_seconds() / DAY_SECONDS, params.min_age_days)


def base_level(
    encoded_at: datetime,
    boost: float,
    recalls: Iterable[datetime],
    now: datetime,
    params: StrengthParams,
) -> float:
    """now の時点の基礎活性。now より後の想起は数えない。"""
    traces: list[tuple[datetime, float, float]] = []  # (時刻, 重みの対数, 減衰)
    for at, weight in [(encoded_at, boost)] + [(t, 0.0) for t in sorted(recalls) if encoded_at <= t <= now]:
        # d = c·e^m + α（m はこの時点までの活性。e^m は痕跡の和そのもの）
        decay = params.decay_floor + (params.decay_scale * _sum(traces, at, params) if traces else 0.0)
        decay = min(decay, params.decay_max)
        traces.append((at, weight, decay))
    return math.log(_sum(traces, now, params))


def _sum(traces: list[tuple[datetime, float, float]], now: datetime, params: StrengthParams) -> float:
    return sum(math.exp(weight) * _age_days(at, now, params) ** -decay for at, weight, decay in traces if at <= now)
