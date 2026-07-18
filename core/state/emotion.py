"""感情状態: プルチック8軸×情動/気分二層。設計書 §2.3, §2.6"""

from __future__ import annotations

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

_MIN_VALUE = 0.0
_MAX_VALUE = 1.0


def _clamp(value: float) -> float:
    return max(_MIN_VALUE, min(_MAX_VALUE, value))


class EmotionState:
    """情動（速い層・変化制限なし）と気分（遅い層・急変防止弁つき）を保持する。"""

    def __init__(self) -> None:
        self.affect: dict[str, float] = {axis: 0.0 for axis in PLUTCHIK_AXES}
        self.mood: dict[str, float] = {axis: 0.0 for axis in PLUTCHIK_AXES}
        # §4.5 日記材料: 気分層(mood)が動くたびのスナップショット。セッションをまたいで
        # Coreが生きている間（=その日）蓄積し、日記生成後にclear_trajectory()で空にする。
        self.mood_trajectory: list[dict[str, float]] = []

    def apply_affect_delta(self, deltas: dict[str, float]) -> None:
        """情動層を更新する。1ターンで振り切ってよく、変化幅の制限はない（§2.3）。

        未知の軸名は黙って無視する（Brain幻覚キーでターン全体を落とさない。
        正規化の一次防壁は通訳側 sanitize、ここは関所通過後の二次防壁）。
        """
        for axis, delta in deltas.items():
            if axis not in self.affect:
                continue
            self.affect[axis] = _clamp(self.affect[axis] + delta)

    def apply_mood_delta(self, deltas: dict[str, float], max_delta_per_turn: float) -> None:
        """気分層を更新する。急変防止弁により1ターンあたりの変化幅を制限する（§2.3）。

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
            self.mood_trajectory.append(dict(self.mood))

    def summarize_trajectory(self) -> str:
        """今日の気分の軌跡を日記材料用の短い日本語記述にする（§4.5）。

        生スナップショット列を丸ごと渡すとプロンプトが肥大するため、軸ごとの
        開始値・終了値・最大値・最小値だけを渡す（「今日どう動いたか」の要約）。
        """
        if not self.mood_trajectory:
            # 空文字を返す（プレースホルダ文字列を返すとDiaryMaterial.is_emptyが常にFalseになり
            # 空材料ガードが機能しなくなる。serina-code-reviewer 2026-07-12 Important指摘）。
            # プロンプト組み立て側（build_diary_prompt）で「記録なし」の表示を補う。
            return ""
        lines = []
        for axis in PLUTCHIK_AXES:
            series = [snap[axis] for snap in self.mood_trajectory if axis in snap]
            if not series:
                continue
            lines.append(
                f"{axis}: 開始{series[0]:.2f}→終了{series[-1]:.2f}"
                f"（最大{max(series):.2f}・最小{min(series):.2f}）"
            )
        return "\n".join(lines)

    def clear_trajectory(self) -> None:
        """日記生成後に軌跡を空にする（次の日の分と混ざらないように）。"""
        self.mood_trajectory = []
