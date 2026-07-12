"""アイドル時トリガーの判定ロジック。設計書v2 §2.4(セッション終了の定義3トリガー・②アイドル時)。

タイマ・スレッド・ロックといった実行の都合から切り離した純粋関数にする
（advisorレビュー2026-07-11: スレッドループに判定を埋めるとsleep依存でテストできない）。
呼び出し側（app/gui_server.pyの見回りスレッド）が「起こす・判定を呼ぶ・実行する」だけを担う。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EndDecision:
    should_end: bool
    reason: str | None = None  # "heartbeat_lost" | "idle_timeout"


def decide_session_end(
    *,
    now: datetime,
    last_heartbeat_at: datetime,
    last_activity_at: datetime,
    session_ended: bool,
    heartbeat_lost_after_seconds: float,
    idle_timeout_after_seconds: float,
) -> EndDecision:
    """§2.4トリガー1(GUI終了=心拍途絶)・トリガー2(無操作タイムアウト)の判定。

    既にsession_endedならFalse（end_session()の二重呼び出し防止。呼び出し側で
    次の発話が来たときにsession_endedをFalseへ戻す）。
    """
    if session_ended:
        return EndDecision(should_end=False)

    if (now - last_heartbeat_at).total_seconds() >= heartbeat_lost_after_seconds:
        return EndDecision(should_end=True, reason="heartbeat_lost")

    if (now - last_activity_at).total_seconds() >= idle_timeout_after_seconds:
        return EndDecision(should_end=True, reason="idle_timeout")

    return EndDecision(should_end=False)


def should_digest(
    *,
    now: datetime,
    last_activity_at: datetime,
    session_ended: bool,
    digest_gap_seconds: float,
) -> bool:
    """§2.4②「会話の合間のアイドル時…1〜2件ずつ内職」の判定。

    セッション終了後（session_ended）は、会話が戻ってくる心配がないぶん間隔を待たず
    毎ティック消化を試みてよい（実際に何かを消化するかは宿題箱の中身次第。
    呼び出し側がpending件数・GPU番人・ロックを見て最終判断する）。
    """
    if session_ended:
        return True
    return (now - last_activity_at).total_seconds() >= digest_gap_seconds


def should_generate_diary(
    *,
    now: datetime,
    last_diary_at: datetime,
    session_ended: bool,
    diary_min_gap_seconds: float,
) -> bool:
    """§4.5「夜間放出時（その日の最終セッション終了時）」の判定。

    未来のセッション再開を予知できないため、「セッション終了後、直近の日記生成から
    十分な時間（既定6時間）が経っている」を近似条件とする。1日に何本も量産しないための
    下限ゲート（実際に生成するかは呼び出し側が材料の有無等を見て最終判断する）。
    """
    if not session_ended:
        return False
    return (now - last_diary_at).total_seconds() >= diary_min_gap_seconds
