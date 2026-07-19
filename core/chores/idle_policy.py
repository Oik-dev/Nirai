"""アイドル時トリガーの判定ロジック。設計書 §2.4(セッション終了の定義・②アイドル時)。

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
from typing import Any


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


def should_run_idle_chores(*, session_ended: bool) -> bool:
    """§3.8 会話優先: 裏方便はセッション終了後のみ起動してよい。

    セッション継続中（アイドル判定前＝`session_ended` が False）は、蒸留・査定・日記・
    facts 転記・要約更新・life 生成・persona 改訂・忘却を一切起動しない。
    再開条件（GPU 空き・turn_lock 非ブロッキング取得）は呼び出し側（GUI 見回り）の責務。
    """
    return session_ended


def should_digest(
    *,
    now: datetime,
    last_activity_at: datetime,
    session_ended: bool,
    digest_gap_seconds: float,
) -> bool:
    """§2.4②アイドル内職＋§3.8会話優先のゲート。

    2026-07-19 Wave 5: セッション継続中の digest_gap 経過だけでは起動しない。
    `session_ended` のときのみ True（終了後は毎ティック試行してよい）。
    `digest_gap_seconds` / `last_activity_at` / `now` は後方互換のため残すが判定には使わない。
    """
    _ = (now, last_activity_at, digest_gap_seconds)
    return should_run_idle_chores(session_ended=session_ended)


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


# --- Pulse（合意台帳 §3.6 / Wave 6）---


@dataclass(frozen=True)
class PulseConfig:
    """Pulse 発火判定の設定値（config/thresholds.toml [pulse]）。"""

    idle_before_seconds: float
    active_hour_start: int
    active_hour_end: int
    same_kind_gap_seconds: float
    emotion_gap_seconds: float
    min_interval_seconds: float
    late_night_start: int
    late_night_end: int
    mood_deviation_threshold: float


@dataclass(frozen=True)
class PulseCandidate:
    kind: str  # "time" | "memory" | "emotion"
    trigger_id: str
    context: dict[str, Any]


@dataclass(frozen=True)
class PulseDecision:
    should_fire: bool
    candidate: PulseCandidate | None = None
    suppressed_reason: str | None = None


def is_active_hours(*, now: datetime, active_hour_start: int, active_hour_end: int) -> bool:
    """活動時間帯（ローカル時刻）内か。"""
    hour = now.astimezone().hour
    if active_hour_start <= active_hour_end:
        return active_hour_start <= hour < active_hour_end
    return hour >= active_hour_start or hour < active_hour_end


def is_late_night(*, now: datetime, late_night_start: int, late_night_end: int) -> bool:
    """深夜帯（ローカル時刻）。active_hours の逆ではなく独立設定。"""
    hour = now.astimezone().hour
    if late_night_start <= late_night_end:
        return late_night_start <= hour < late_night_end
    return hour >= late_night_start or hour < late_night_end


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts)


def _seconds_since(ts: str | None, now: datetime) -> float | None:
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    return (now - parsed).total_seconds()


def should_suppress_pulse(
    *,
    now: datetime,
    mute: bool,
    conversation_active: bool,
    last_pulse_at: str | None,
    config: PulseConfig,
) -> str | None:
    """沈黙優先条件。抑制理由文字列を返す（抑制なしなら None）。"""
    if mute:
        return "mute"
    if conversation_active:
        return "conversation_active"
    if is_late_night(
        now=now,
        late_night_start=config.late_night_start,
        late_night_end=config.late_night_end,
    ):
        return "late_night"
    elapsed = _seconds_since(last_pulse_at, now)
    if elapsed is not None and elapsed < config.min_interval_seconds:
        return "min_interval"
    return None


def _kind_gap_ok(
    *,
    kind: str,
    now: datetime,
    last_by_kind: dict[str, str],
    config: PulseConfig,
) -> bool:
    last = last_by_kind.get(kind)
    elapsed = _seconds_since(last, now)
    if elapsed is None:
        return True
    gap = config.emotion_gap_seconds if kind == "emotion" else config.same_kind_gap_seconds
    return elapsed >= gap


def collect_time_pulse_candidate(
    *,
    now: datetime,
    last_activity_at: datetime,
    last_by_kind: dict[str, str],
    config: PulseConfig,
) -> PulseCandidate | None:
    if not is_active_hours(
        now=now,
        active_hour_start=config.active_hour_start,
        active_hour_end=config.active_hour_end,
    ):
        return None
    if (now - last_activity_at).total_seconds() < config.idle_before_seconds:
        return None
    if not _kind_gap_ok(kind="time", now=now, last_by_kind=last_by_kind, config=config):
        return None
    idle_min = int((now - last_activity_at).total_seconds() // 60)
    return PulseCandidate(
        kind="time",
        trigger_id=f"time-{now.astimezone().date().isoformat()}",
        context={"idle_minutes": idle_min, "reason": "idle_timeout"},
    )


def collect_memory_pulse_candidates(
    *,
    now: datetime,
    promise_memories: list[tuple[int, str]],
    pulsed_promise_ids: list[int],
    last_by_kind: dict[str, str],
    config: PulseConfig,
) -> list[PulseCandidate]:
    if not is_active_hours(
        now=now,
        active_hour_start=config.active_hour_start,
        active_hour_end=config.active_hour_end,
    ):
        return []
    if not _kind_gap_ok(kind="memory", now=now, last_by_kind=last_by_kind, config=config):
        return []
    pulsed = set(pulsed_promise_ids)
    out: list[PulseCandidate] = []
    for mid, content in promise_memories:
        if mid in pulsed:
            continue
        snippet = content.replace("\n", " ")[:120]
        out.append(
            PulseCandidate(
                kind="memory",
                trigger_id=str(mid),
                context={"memory_id": mid, "snippet": snippet, "reason": "unreclaimed_promise"},
            )
        )
    return out


def collect_emotion_pulse_candidate(
    *,
    now: datetime,
    mood: dict[str, float],
    last_by_kind: dict[str, str],
    config: PulseConfig,
) -> PulseCandidate | None:
    if not is_active_hours(
        now=now,
        active_hour_start=config.active_hour_start,
        active_hour_end=config.active_hour_end,
    ):
        return None
    if not _kind_gap_ok(kind="emotion", now=now, last_by_kind=last_by_kind, config=config):
        return None
    peak_axis = max(mood, key=mood.get, default="")
    peak_value = mood.get(peak_axis, 0.0)
    if peak_value < config.mood_deviation_threshold:
        return None
    return PulseCandidate(
        kind="emotion",
        trigger_id=f"emotion-{peak_axis}",
        context={
            "dominant_axis": peak_axis,
            "dominant_value": peak_value,
            "reason": "sustained_mood",
        },
    )


def decide_pulse(
    *,
    now: datetime,
    last_activity_at: datetime,
    mute: bool,
    conversation_active: bool,
    last_pulse_at: str | None,
    last_by_kind: dict[str, str],
    pulsed_promise_ids: list[int],
    promise_memories: list[tuple[int, str]],
    mood: dict[str, float],
    config: PulseConfig,
) -> PulseDecision:
    """Pulse 発火判定（決定論）。文面生成は呼び出し側が Brain へ委譲する。"""
    suppressed = should_suppress_pulse(
        now=now,
        mute=mute,
        conversation_active=conversation_active,
        last_pulse_at=last_pulse_at,
        config=config,
    )
    if suppressed:
        return PulseDecision(should_fire=False, suppressed_reason=suppressed)

    candidates: list[PulseCandidate] = []
    mem = collect_memory_pulse_candidates(
        now=now,
        promise_memories=promise_memories,
        pulsed_promise_ids=pulsed_promise_ids,
        last_by_kind=last_by_kind,
        config=config,
    )
    candidates.extend(mem)
    emo = collect_emotion_pulse_candidate(
        now=now, mood=mood, last_by_kind=last_by_kind, config=config,
    )
    if emo:
        candidates.append(emo)
    tim = collect_time_pulse_candidate(
        now=now,
        last_activity_at=last_activity_at,
        last_by_kind=last_by_kind,
        config=config,
    )
    if tim:
        candidates.append(tim)

    if not candidates:
        return PulseDecision(should_fire=False)

    # 優先: memory → emotion → time
    priority = {"memory": 0, "emotion": 1, "time": 2}
    chosen = min(candidates, key=lambda c: priority.get(c.kind, 99))
    return PulseDecision(should_fire=True, candidate=chosen)
