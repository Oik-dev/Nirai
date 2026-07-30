"""欲求層（独立状態）。設計書 Phase 3 / 実装計画 Task 3-1〜3-4。

情動/気分/平常値とは時間の向きが逆（放置すると上がる）のため EmotionState には混ぜない。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Task 3-2: ゼロから1.0まで7日で到達する線形速度
DEFAULT_BASE_RATE_PER_SECOND = 1.0 / 604_800.0
# Task 3-2: 蓄積速度の変動要因の重み（tick()のcombined_factor計算と一致させる）。
# 各factorは[-1,1]に収まるため、combined_factorの理論下限は -(sum of weights)。
MOOD_FACTOR_WEIGHT = 0.20
BASELINE_COMFORT_FACTOR_WEIGHT = 0.10
CIRCADIAN_FACTOR_WEIGHT = 0.10
COMBINED_FACTOR_MIN = -(
    MOOD_FACTOR_WEIGHT + BASELINE_COMFORT_FACTOR_WEIGHT + CIRCADIAN_FACTOR_WEIGHT
)
# Task 3-3 / 3-4 / 3-5: 抑制門・満たされた判定・パック表出で共通
DEFAULT_SUPPRESSION_THRESHOLD = 0.6
DEFAULT_FULFILLMENT_LEVEL_THRESHOLD = 0.6
# Task 3-4: 不応期 1.5日、放電後のほぼ0
DEFAULT_REFRACTORY_SECONDS = 129_600.0
DEFAULT_DISCHARGE_LEVEL = 0.05
# 未充足時の減衰τ=30日（2026-07-30 マスター承認a: レビューC-2是正）。
# 不変条件: base_rate_per_second × (1 + COMBINED_FACTOR_MIN) > 1 / decay_tau_seconds
# （蓄積側の最遅速度が、level=1.0での減衰速度を常に上回る）。重みやbase_rateを
# 変えるときはこの式を満たすようdecay_tauを再逆算すること。旧3日では
# level>=0.6域で減衰が蓄積の約2倍速く、三相モデルの「満タン付近で放電」が
# 構造的に到達不能だった。
DEFAULT_DECAY_TAU_SECONDS = 2_592_000.0

_JST = ZoneInfo("Asia/Tokyo")
_NEGATIVE_AFFECT_AXES: tuple[str, ...] = ("嫌悪", "怒り", "悲しみ", "恐れ")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalize_avg_to_factor(values: list[float]) -> float:
    """0.0〜1.0 の平均を -1.0〜1.0 へ正規化する。"""
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    return _clamp01(avg) * 2.0 - 1.0


def mood_factor_from_mood(mood: dict[str, float]) -> float:
    """喜び・信頼の平均を -1.0〜1.0 に正規化（Task 3-2 mood_factor）。"""
    return _normalize_avg_to_factor([
        float(mood.get("喜び", 0.0)),
        float(mood.get("信頼", 0.0)),
    ])


def baseline_comfort_factor_from_baseline(baseline: dict[str, float]) -> float:
    """baseline の喜び・信頼平均を -1.0〜1.0 に正規化（Task 3-2）。"""
    return _normalize_avg_to_factor([
        float(baseline.get("喜び", 0.0)),
        float(baseline.get("信頼", 0.0)),
    ])


def circadian_factor_from_now(now: datetime) -> float:
    """cos(2π × (時刻 − 1時) / 24)。深夜1時が山頂・13時が谷。

    時刻は Asia/Tokyo（JST）へ変換してから取る（Task 3-4 差し戻し解決・マスター承認）。
    aware なら `.astimezone(JST)`、naive なら `.replace(tzinfo=JST)`。
    """
    local = now.astimezone(_JST) if now.tzinfo is not None else now.replace(tzinfo=_JST)
    hours = local.hour + local.minute / 60.0 + local.second / 3600.0
    return math.cos(2.0 * math.pi * (hours - 1.0) / 24.0)


def is_desire_gate_open(
    affect: dict[str, float],
    threshold: float = DEFAULT_SUPPRESSION_THRESHOLD,
) -> bool:
    """抑制門（硬い門）。嫌悪・怒り・悲しみ・恐れのいずれかが閾値超なら閉じる。"""
    for axis in _NEGATIVE_AFFECT_AXES:
        if float(affect.get(axis, 0.0)) > threshold:
            return False
    return True


class DesireState:
    """欲求の蓄積量と不応期。EmotionState と同型の最小構造。"""

    def __init__(
        self,
        *,
        base_rate_per_second: float = DEFAULT_BASE_RATE_PER_SECOND,
        refractory_seconds: float = DEFAULT_REFRACTORY_SECONDS,
        decay_tau_seconds: float = DEFAULT_DECAY_TAU_SECONDS,
        discharge_level: float = DEFAULT_DISCHARGE_LEVEL,
        fulfillment_level_threshold: float = DEFAULT_FULFILLMENT_LEVEL_THRESHOLD,
    ) -> None:
        self.level: float = 0.0
        # 直近tick開始時点のlevelスナップショット（2026-07-30 マスター承認b:
        # レビューC-2是正。「満たされた」判定〈gate.py〉は同ターンのtickで
        # levelが動いた"後"ではなく、tick前の値で見る。level=0.6ちょうど付近で
        # 減衰が僅差で先に効いて判定をすり抜ける事故を防ぐ）
        self.level_before_tick: float = 0.0
        self.refractory_until: datetime | None = None
        self.last_tick_at: datetime | None = None
        self.base_rate_per_second = base_rate_per_second
        self.refractory_seconds = refractory_seconds
        self.decay_tau_seconds = decay_tau_seconds
        self.discharge_level = discharge_level
        self.fulfillment_level_threshold = fulfillment_level_threshold

    def is_in_refractory(self, now: datetime) -> bool:
        return self.refractory_until is not None and now < self.refractory_until

    def tick(
        self,
        now: datetime,
        mood_factor: float,
        baseline_comfort_factor: float,
        *,
        unfulfilled_decay: bool = False,
    ) -> None:
        """時計ベースの蓄積（または未充足時の緩やかな減衰）。

        circadian_factor は now から内部計算する。不応期中は蓄積も減衰もしない（据え置き）。
        """
        # 2026-07-30 マスター承認b: このtickでlevelを動かす前のスナップショットを
        # 必ず先頭で記録する（早期returnの経路でも直前値のまま保たれる）。
        self.level_before_tick = self.level
        if self.last_tick_at is None:
            self.last_tick_at = now
            return

        dt = (now - self.last_tick_at).total_seconds()
        if dt <= 0:
            self.last_tick_at = now
            return

        if self.is_in_refractory(now):
            self.last_tick_at = now
            return

        if unfulfilled_decay:
            # Task 3-4: 満たされない場合の指数減衰（apply_time_cooling と同型、別tau）
            g = (
                1.0 - math.exp(-dt / self.decay_tau_seconds)
                if self.decay_tau_seconds > 0
                else 1.0
            )
            self.level = _clamp01(self.level + (0.0 - self.level) * g)
            self.last_tick_at = now
            return

        circadian = circadian_factor_from_now(now)
        combined_factor = (
            MOOD_FACTOR_WEIGHT * mood_factor
            + BASELINE_COMFORT_FACTOR_WEIGHT * baseline_comfort_factor
            + CIRCADIAN_FACTOR_WEIGHT * circadian
        )
        effective_rate = self.base_rate_per_second * (1.0 + combined_factor)
        self.level = _clamp01(self.level + effective_rate * dt)
        self.last_tick_at = now

    def discharge_and_enter_refractory(self, now: datetime) -> None:
        """満たされたときの放電＋不応期セット（Task 3-4）。"""
        self.level = self.discharge_level
        # 2026-07-30 レビューC-b是正: level_before_tickも同時にリセットしないと、
        # 同ターン内に「心の動き」付箋が複数あった場合、2枚目以降でも
        # gate.pyの判定（level_before_tick基準）が高いまま成立し、放電が多重発火する。
        self.level_before_tick = self.discharge_level
        self.refractory_until = now + timedelta(seconds=self.refractory_seconds)
        self.last_tick_at = now

    def is_fulfillment_level_high(self) -> bool:
        return self.level >= self.fulfillment_level_threshold
