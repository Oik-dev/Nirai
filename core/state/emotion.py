"""感情状態: プルチック8軸×情動/気分二層。設計書 §2.3, §2.6"""

from __future__ import annotations

import math
from datetime import datetime

PLUTCHIK_AXES: tuple[str, ...] = (
    "喜び",
    "信頼",
    "恐れ",
    "驚き",
    "悲しみ",
    "嫌悪",
    "怒り",
    "期待",
)

# プルチックの輪の対極ペア（§2.3 感情①対極カップリング）。双方向に引ける。
OPPOSITE_AXES: dict[str, str] = {
    "喜び": "悲しみ",
    "悲しみ": "喜び",
    "信頼": "嫌悪",
    "嫌悪": "信頼",
    "恐れ": "怒り",
    "怒り": "恐れ",
    "驚き": "期待",
    "期待": "驚き",
}

_MIN_VALUE = 0.0
_MAX_VALUE = 1.0
_DEFAULT_BASELINE_MAX = 0.5


def _clamp(value: float) -> float:
    return max(_MIN_VALUE, min(_MAX_VALUE, value))


def _clamp_baseline(value: float, *, max_value: float = _DEFAULT_BASELINE_MAX) -> float:
    """平常値用クランプ。上限はconfigの emotion_baseline.max（既定0.5）。下限は0.0。"""
    return max(_MIN_VALUE, min(max_value, value))


class EmotionState:
    """情動（速い層・変化制限なし）と気分（遅い層・急変防止弁つき）を保持する。"""

    def __init__(self, baselines: dict[str, float] | None = None) -> None:
        self.affect: dict[str, float] = {axis: 0.0 for axis in PLUTCHIK_AXES}
        self.mood: dict[str, float] = {axis: 0.0 for axis in PLUTCHIK_AXES}
        # Task 2-1: 平常値は永続状態。configの[emotion_baseline]は初期値として受け取る。
        self.baseline: dict[str, float] = {axis: 0.0 for axis in PLUTCHIK_AXES}
        if baselines:
            for axis, value in baselines.items():
                if axis in self.baseline:
                    self.baseline[axis] = float(value)
        self.last_tick_at: datetime | None = None
        # §4.5 日記材料: 気分層(mood)が動くたびのスナップショット。セッションをまたいで
        # Coreが生きている間（=その日）蓄積し、日記生成後にclear_trajectory()で空にする。
        self.mood_trajectory: list[dict[str, float]] = []
        # 2026-07-26 A3: 軌跡スナップショットへ付与するSerina日タグ（ISO日付文字列）。
        # EmotionState自身は時計を持たない方針を維持するため、値は外から与える
        # （`core/runtime.py:turn_routed`が毎ターン冒頭で設定する）。
        self.current_day: str | None = None

    def apply_affect_delta(
        self, deltas: dict[str, float], *, opposite_coupling_ratio: float = 0.0,
    ) -> None:
        """情動層を更新する。1ターンで振り切ってよく、変化幅の制限はない（§2.3）。

        未知の軸名は黙って無視する（Brain幻覚キーでターン全体を落とさない。
        正規化の一次防壁は通訳側 sanitize、ここは関所通過後の二次防壁）。

        2026-07-26 A5（感情①対極カップリング）: `opposite_coupling_ratio > 0`のとき、
        喜び0.9と悲しみ0.9が同時に立つような非人間的な状態を防ぐ。処理順:
          1. 明示デルタ（既知軸のみ）を抽出する
          2. 明示デルタを適用する
          3. 明示デルタのうち正の増加のみを対象に、その対極軸へ
             `-delta * opposite_coupling_ratio` を適用する
          4. 対極側の軸が明示デルタに含まれる場合はカップリングを適用しない
             （Brainの明示指定が優先。矛盾した報告でも決定論的に解決する）
          5. すべての適用後に0.0〜1.0でクランプする
        負のデルタにはカップリングを適用しない（「悲しみが減った」は「喜びが生まれた」
        ではない。幽霊感情の生成を防ぐ）。
        """
        known_deltas = {axis: delta for axis, delta in deltas.items() if axis in self.affect}
        effective: dict[str, float] = dict(known_deltas)
        if opposite_coupling_ratio:
            for axis, delta in known_deltas.items():
                if delta <= 0:
                    continue
                opposite = OPPOSITE_AXES.get(axis)
                if opposite is None or opposite in known_deltas:
                    continue
                effective[opposite] = effective.get(opposite, 0.0) - delta * opposite_coupling_ratio
        for axis, delta in effective.items():
            self.affect[axis] = _clamp(self.affect[axis] + delta)

    def apply_mood_delta(self, deltas: dict[str, float], max_delta_per_turn: float) -> None:
        """気分層を更新する。急変防止弁により1ターンあたりの変化幅を制限する（§2.3）。

        旧方式。2026-07-26 A6で現行経路は`apply_mood_bleed`（情動へのにじみ）へ移行した。
        既存テスト資産・後方互換のため削除しない。

        未知の軸名は黙って無視する（apply_affect_delta と同方針）。
        既知軸が1つも無いときは軌跡へ追記しない（日記材料の冗長スナップショット防止）。
        """
        applied = False
        for axis, delta in deltas.items():
            if axis not in self.mood:
                continue
            guarded_delta = max(-max_delta_per_turn, min(max_delta_per_turn, delta))
            self.mood[axis] = _clamp(self.mood[axis] + guarded_delta)
            applied = True
        if applied:
            snapshot = dict(self.mood)
            snapshot["_day"] = self.current_day
            self.mood_trajectory.append(snapshot)

    def apply_mood_bleed(self, *, bleed_rate: float, max_delta_per_turn: float) -> None:
        """気分を、その時点の情動へ少しだけ引き寄せる（§2.3 A6 情動のにじみ）。

        毎ターン呼ぶことを前提とする。1回きりの大きな感情では気分はほぼ動かず、
        同じ情動が続いた日数分だけ気分が染まっていく（指数移動平均に近い挙動）。

        各軸: `delta = (affect[axis] - mood[axis]) * bleed_rate`。`max_delta_per_turn`は
        暴走を防ぐ安全上限であり、にじみの主機構ではない（`bleed_rate`が主機構）。
        いずれかの軸が0.001以上動いたときのみ軌跡へスナップショットを追記する
        （毎ターン追記による軌跡肥大の防止）。
        """
        applied = False
        for axis in PLUTCHIK_AXES:
            delta = (self.affect[axis] - self.mood[axis]) * bleed_rate
            guarded_delta = max(-max_delta_per_turn, min(max_delta_per_turn, delta))
            if abs(guarded_delta) >= 0.001:
                applied = True
            self.mood[axis] = _clamp(self.mood[axis] + guarded_delta)
        if applied:
            snapshot = dict(self.mood)
            snapshot["_day"] = self.current_day
            self.mood_trajectory.append(snapshot)

    def apply_time_cooling(
        self,
        now: datetime,
        *,
        tau_affect_seconds: float,
        tau_mood_seconds: float,
        baselines: dict[str, float],
        tau_baseline_seconds: float = 1_209_600.0,
        baseline_max: float = _DEFAULT_BASELINE_MAX,
    ) -> None:
        """経過時間に応じて情動・気分を baseline へ指数減衰させる（§2.6 時間冷却）。

        Task 2-2: 同じtickで平常値ドリフト
        `baseline += (mood - baseline) * g_baseline` も行い、上限 baseline_max でクランプする。
        """
        if self.last_tick_at is None:
            self.last_tick_at = now
            return

        dt = (now - self.last_tick_at).total_seconds()
        if dt <= 0:
            self.last_tick_at = now
            return

        g_affect = 1.0 - math.exp(-dt / tau_affect_seconds) if tau_affect_seconds > 0 else 1.0
        g_mood = 1.0 - math.exp(-dt / tau_mood_seconds) if tau_mood_seconds > 0 else 1.0
        g_baseline = (
            1.0 - math.exp(-dt / tau_baseline_seconds) if tau_baseline_seconds > 0 else 1.0
        )

        for axis in PLUTCHIK_AXES:
            baseline = baselines.get(axis, 0.0)
            self.affect[axis] = _clamp(
                self.affect[axis] + (baseline - self.affect[axis]) * g_affect,
            )
            self.mood[axis] = _clamp(
                self.mood[axis] + (baseline - self.mood[axis]) * g_mood,
            )
            # 平常値ドリフト（対称。気分が低ければbaselineも下がる）
            self.baseline[axis] = _clamp_baseline(
                self.baseline[axis]
                + (self.mood[axis] - self.baseline[axis]) * g_baseline,
                max_value=baseline_max,
            )

        self.last_tick_at = now

    def summarize_trajectory(self, day: str | None = None) -> str:
        """気分の軌跡を日記材料用の短い日本語記述にする（§4.5）。

        生スナップショット列を丸ごと渡すとプロンプトが肥大するため、軸ごとの
        開始値・終了値・最大値・最小値だけを渡す（「今日どう動いたか」の要約）。

        2026-07-26 A3: `day`指定時はそのSerina日（`_day`タグ）のスナップショットのみを
        集計する。`day=None`は全件（従来動作・後方互換）。
        """
        snapshots = self._snapshots_for_day(day)
        if not snapshots:
            # 空文字を返す（プレースホルダ文字列を返すとDiaryMaterial.is_emptyが常にFalseになり
            # 空材料ガードが機能しなくなる。serina-code-reviewer 2026-07-12 Important指摘）。
            # プロンプト組み立て側（build_diary_prompt）で「記録なし」の表示を補う。
            return ""
        lines = []
        for axis in PLUTCHIK_AXES:
            series = [snap[axis] for snap in snapshots if axis in snap]
            if not series:
                continue
            lines.append(
                f"{axis}: 開始{series[0]:.2f}→終了{series[-1]:.2f}"
                f"（最大{max(series):.2f}・最小{min(series):.2f}）"
            )
        return "\n".join(lines)

    def clear_trajectory(self, day: str | None = None) -> None:
        """日記生成後に軌跡を空にする（次の日の分と混ざらないように）。

        2026-07-26 A3: `day`指定時は該当日（`_day`タグ）のスナップショットのみ除去する。
        `day=None`は全消去（従来動作・後方互換）。
        """
        if day is None:
            self.mood_trajectory = []
            return
        self.mood_trajectory = [
            snap for snap in self.mood_trajectory if snap.get("_day") != day
        ]

    def _snapshots_for_day(self, day: str | None) -> list[dict]:
        if day is None:
            return self.mood_trajectory
        return [snap for snap in self.mood_trajectory if snap.get("_day") == day]
