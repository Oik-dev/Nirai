"""体の芯（設計書 §2.3）。快・不快と高ぶりの2つの数と、つながり。

仕組みが持つのはこの3つの数だけ。感情の名前と言葉は、その場で本人の脳が作る（Barrett の構成主義。芯の感じに名前を付けるのは脳）。
- 快・不快（-1〜1）と高ぶり（0〜1）は、平常からのずれとして、速い層（今の芯。数時間で戻る）と遅い層（気分。数日で戻る）に持つ。
  快・不快の平常は、気質とつながりで決まる（満たされていれば気質のまま、人恋しいと少し沈む）。高ぶりの平常は1日のリズムで、
  昼に上がり夜に下がる。
- つながり（0〜1）は、会えない時間で減り、Masterと話すと満ちる。満ちた分は快へ、欠けた分は不快へ響く（だから、久しぶりに
  会えたときほどうれしい）。時間で減ること自体は、平常が下がることで効くので、快・不快へ重ねて流さない。
- 出来事がどれだけ動かすかは仕組みが決める。本人の脳が選択肢で答えた評価を、対応表（config/thresholds.toml の [feeling]）で
  量にする。量は、今の値からその向きの端までの残りの割合なので、同じ向きが続くほど動きにくく、端に張り付かない。
  動いた量の一部（slow_share）は遅い層に入り、出来事の余韻が次の日まで残る。

状態はファイルに持たない。動いたあとの数（Body）を気持ちの記録に残し、今の数は最後の記録からの時間で計算する（feelings.py）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DAY_HOURS = 24.0


@dataclass(frozen=True)
class FeelingParams:
    """値は config/thresholds.toml の [feeling]（core/config.py が読む）。"""

    tau_fast_seconds: float  # 今の芯のずれが平常へ戻る速さ
    tau_slow_seconds: float  # 気分のずれが平常へ戻る速さ
    slow_share: float  # 1つの出来事の動きのうち、遅い層へ入る割合
    tau_connection_seconds: float  # つながりが、会えない時間で減る速さ
    initial_connection: float  # 気持ちの記録がまだないときのつながり
    temperament_valence: float  # 気質：満たされているときの快・不快の平常
    loneliness_valence: float  # つながりが空のとき、快・不快の平常がこれだけ下がる
    arousal_mid: float  # 1日のリズム：高ぶりの平常の真ん中
    arousal_swing: float  # 1日のリズムの振れ幅
    arousal_peak_hour: float  # 高ぶりの平常がいちばん高い時刻（日本時間）
    meeting_valence: float  # つながりが満ちた分・欠けた分が、快・不快へ響く強さ
    valence: dict[str, float]  # うれしさの選択肢 → 動き（+は快の端へ、-は不快の端へ。大きさは端までの残りの割合）
    arousal: dict[str, float]  # 揺れの選択肢 → 動き（+は高ぶりの端へ、-は落ち着きの端へ）
    distance: dict[str, float]  # 距離の選択肢 → つながりの動き（+は満ちる、-は欠ける。大きさは残りの割合）
    # 体の感じの言葉の境目と、パックに載せる量（feelings.py）
    bright_above: float
    dim_below: float
    stirred_above: float
    calm_below: float
    sleepy_below: float
    lonely_below: float  # つながりがこれより低いと人恋しい（Pulse で会いに行く）
    flow_items: int
    master_state_stale_after_seconds: float


@dataclass(frozen=True)
class Body:
    """体の芯。快・不快と高ぶりは平常からのずれ（速い層と遅い層）、つながりはそのままの値。気持ちの記録の after。"""

    fast_valence: float = 0.0
    fast_arousal: float = 0.0
    slow_valence: float = 0.0
    slow_arousal: float = 0.0
    connection: float = 0.5

    def as_record(self) -> dict[str, float]:
        return {key: round(value, 4) for key, value in self.__dict__.items()}

    @classmethod
    def from_record(cls, record: dict) -> Body:
        return cls(**{key: float(record[key]) for key in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Sense:
    """その時の体の感じ（平常とずれを足したもの）。"""

    valence: float
    arousal: float
    connection: float
    stir: float  # 平常からどれだけ動いているか（快・不快と高ぶりのずれの大きさの和）


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _push(value: float, k: float, low: float, high: float) -> float:
    """k が正なら high へ、負なら low へ、残りの |k| の割合だけ寄せる。"""
    if k > 0:
        return value + (high - value) * min(k, 1.0)
    if k < 0:
        return value + (low - value) * min(-k, 1.0)
    return value


def rhythm(at: datetime, params: FeelingParams) -> float:
    """1日のリズムによる高ぶりの平常（日本時間の時刻で決まる）。"""
    local = at.astimezone(JST)
    hour = local.hour + local.minute / 60
    return params.arousal_mid + params.arousal_swing * math.cos(2 * math.pi * (hour - params.arousal_peak_hour) / DAY_HOURS)


def is_night(at: datetime, params: FeelingParams) -> bool:
    """1日のリズムが下り半分（夜）か。"""
    return rhythm(at, params) < params.arousal_mid


def baseline_valence(connection: float, params: FeelingParams) -> float:
    return params.temperament_valence - params.loneliness_valence * (1.0 - connection)


def settle(body: Body, seconds: float, params: FeelingParams) -> Body:
    """seconds のあいだ何もなければ、ずれは平常へ戻り、つながりは減る。"""
    seconds = max(0.0, seconds)
    fast = math.exp(-seconds / params.tau_fast_seconds)
    slow = math.exp(-seconds / params.tau_slow_seconds)
    apart = math.exp(-seconds / params.tau_connection_seconds)
    return Body(
        fast_valence=body.fast_valence * fast,
        fast_arousal=body.fast_arousal * fast,
        slow_valence=body.slow_valence * slow,
        slow_arousal=body.slow_arousal * slow,
        connection=body.connection * apart,
    )


def sense(body: Body, at: datetime, params: FeelingParams) -> Sense:
    dv = body.fast_valence + body.slow_valence
    da = body.fast_arousal + body.slow_arousal
    base_v = baseline_valence(body.connection, params)
    base_a = rhythm(at, params)
    valence = _clamp(base_v + dv, -1.0, 1.0)
    arousal = _clamp(base_a + da, 0.0, 1.0)
    return Sense(
        valence=valence,
        arousal=arousal,
        connection=_clamp(body.connection, 0.0, 1.0),
        stir=abs(valence - base_v) + abs(arousal - base_a),
    )


def react(
    body: Body,
    at: datetime,
    *,
    valence: str | None,
    arousal: str | None,
    distance: str,
    params: FeelingParams,
) -> Body:
    """Masterと話したターンの評価で、体の芯を動かす（body は、そのときまで落ち着かせたもの）。

    評価のないターン（脳が答えられなかった）は、valence・arousal を None にして、Masterが来たこと（distance）だけを渡す。
    """
    connection = _clamp(_push(body.connection, params.distance[distance], 0.0, 1.0), 0.0, 1.0)
    met = replace(body, connection=connection)
    now = sense(met, at, params)
    v = _push(now.valence, params.valence.get(valence, 0.0) if valence else 0.0, -1.0, 1.0)
    v = _push(v, (connection - body.connection) * params.meeting_valence, -1.0, 1.0)
    a = _push(now.arousal, params.arousal.get(arousal, 0.0) if arousal else 0.0, 0.0, 1.0)
    dv, da = v - now.valence, a - now.arousal
    fast, slow = 1.0 - params.slow_share, params.slow_share
    return replace(
        met,
        fast_valence=met.fast_valence + fast * dv,
        slow_valence=met.slow_valence + slow * dv,
        fast_arousal=met.fast_arousal + fast * da,
        slow_arousal=met.slow_arousal + slow * da,
    )
