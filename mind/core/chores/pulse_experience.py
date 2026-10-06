"""話しかけたあとの反応から、つながり Pulse の間隔を決める（段階3 S7）。"""

from __future__ import annotations

from datetime import datetime

from mind.core.lifelog import Line, MASTER

FAST_REPLY_SECONDS = 3600.0
MAX_CONNECTION_GAP_SECONDS = 24 * 3600.0


def _connection_times(entries: list[dict]) -> list[datetime]:
    out: list[datetime] = []
    for row in entries:
        if row.get("kind") != "connection":
            continue
        try:
            out.append(datetime.fromisoformat(str(row["ts"])))
        except (KeyError, ValueError):
            continue
    return sorted(out)


def unanswered_connection_streak(entries: list[dict], conversation: list[Line]) -> int:
    """直近から何回続けて「1時間以内の返事がなかった」か。

    各 connection Pulse のあと、次の connection Pulse より前に来た最初の Master 発言を見る。
    1時間以内ならそこで連敗は終わる。遅い返事・返事なしは1回として数える。
    """
    pulses = _connection_times(entries)
    if not pulses:
        return 0
    masters = sorted((line.ts for line in conversation if line.speaker == MASTER))
    missed = 0
    for index in range(len(pulses) - 1, -1, -1):
        fired = pulses[index]
        next_fire = pulses[index + 1] if index + 1 < len(pulses) else None
        response = next(
            (at for at in masters if at > fired and (next_fire is None or at < next_fire)),
            None,
        )
        if response is not None and (response - fired).total_seconds() <= FAST_REPLY_SECONDS:
            break
        missed += 1
    return missed


def connection_gap_seconds(entries: list[dict], conversation: list[Line], *, base_seconds: float) -> float:
    """3→6→12→24時間のように、返事なしが続くたび倍にする。速い返事があれば基準へ戻る。"""
    misses = unanswered_connection_streak(entries, conversation)
    return min(float(base_seconds) * (2 ** misses), MAX_CONNECTION_GAP_SECONDS)
