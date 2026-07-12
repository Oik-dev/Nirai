"""アイドル時トリガーの判定ロジック。設計書v2 §2.4(セッション終了の定義・②アイドル時)。

タイマ・スレッド・ロックといった実行の都合から切り離した純粋関数にする
（advisorレビュー2026-07-11: スレッドループに判定を埋めるとsleep依存でテストできない）。
呼び出し側（app/gui_server.pyの見回りスレッド）が「起こす・判定を呼ぶ・実行する」だけを担う。

旧トリガー1(GUI終了=心拍途絶)は2026-07-12に廃止した。心拍pingは会話とは無関係に
一定間隔で送られ続けるため「最後の発話」より常に新しいか同時刻になり、無操作タイムアウトの
300秒到達に常に先勝ちされる（発話直後・次のping到達前にタブを閉じる一瞬しか単独発火しない）
死に枝だったと実機確認で判明（DECISIONS参照）。マスター判断でトリガー1と心拍ping機構
一式（/api/heartbeat・app.jsの送信）を削除し、無操作タイムアウト単独に統合した。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EndDecision:
    should_end: bool
    reason: str | None = None  # "idle_timeout"


def decide_session_end(
    *,
    now: datetime,
    last_activity_at: datetime,
    session_ended: bool,
    idle_timeout_after_seconds: float,
) -> EndDecision:
    """§2.4無操作タイムアウトによるセッション終了判定。

    既にsession_endedならFalse（end_session()の二重呼び出し防止。呼び出し側で
    次の発話が来たときにsession_endedをFalseへ戻す）。
    """
    if session_ended:
        return EndDecision(should_end=False)

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
    """§4.5②「夜間放出時（その日の最終セッション終了時）」の判定（点けっぱなし運用の補助経路）。

    未来のセッション再開を予知できないため、「セッション終了後、直近の日記生成から
    十分な時間（既定6時間）が経っている」を近似条件とする。1日に何本も量産しないための
    下限ゲート（実際に生成するかは呼び出し側が材料の有無等を見て最終判断する）。
    """
    if not session_ended:
        return False
    return (now - last_diary_at).total_seconds() >= diary_min_gap_seconds


def should_generate_diary_at_startup(*, now: datetime, last_diary_at: datetime) -> bool:
    """§4.5①「朝礼時」の判定（主経路、2026-07-12改訂）。

    起動時、最後に日記を書いた日が前日以前ならその日はまだ日記を書いていないとみなし、
    朝礼として1本書く。「当日」の判定はローカル暦日で行う（`now`/`last_diary_at`を
    システムローカルタイムゾーンへ変換して日付部分だけを比較する）。生活日オフセット
    （深夜稼働を前日扱いにする等）はここでは持たせない——日記は「その日書いたか」の粗い
    判定で十分であり、既存の`living_date`（core/session.py、旧アーキ）のような細かい
    オフセット概念を持ち込むと2つの「今日」定義が並立し混乱するため（advisorレビュー
    2026-07-12）。材料の窓（since_iso）自体は`last_diary_at`そのものを使うため、
    ここでの日付判定はあくまで「書くかどうか」のトリガーに限定される。
    """
    return last_diary_at.astimezone().date() < now.astimezone().date()
