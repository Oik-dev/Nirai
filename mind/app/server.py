"""精神のサーバー（FastAPI）— Core の薄い皮。判断ロジックは持たない

会話・想起・気持ちの判断は core.runtime.Core に一本化。眠り（記憶のページづくり）は core/memory/sleep.py。
会話の正本はイデアの生ログ（core/lifelog.py）。手元の会話の流れ・Masterが最後に話した時刻・日界は、記録から決める。
窓（Niraiの海）が HTTP でつなぐ。どの住人かは NIRAI_IDEA、どのポートで待つかは NIRAI_MIND_PORT で受け取る。

1日の流れ：起動したら、すぐ話せるようにしてから、まだ記憶になっていない会話を裏で眠って記憶にし、人格を見直し、
目覚めて今の自分を書く（話しかけられたら区切りで起き、会話が途切れたら続きから眠る）。起きている間は会話し、Masterが話したターンを記録したら、本人の評価で気持ちを動かして気持ちの記録に残す（Core.feel）。
見回りスレッドが Pulse と Serina 日界を見る。日界を過ぎて会話が途切れたら、また眠る。
Masterの手元が忙しい間（core/chores/busy.py）は、Pulseと眠りを始めず、眠りは区切りで止め（続きはあとで）、会話中でなければ
脳をグラボから下ろす。Masterの話しかけには答える。
眠り終えたら、手元の会話の流れを今日の分だけにする。目覚めて伝えたいことがあり、
マスターがまだ来ていなければ、本人から話しかけに行く（Pulse の wake）。人恋しくなっても会いに行く（Pulse の connection）。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from mind.app.idle_config import AppTimingConfig, load_app_timing
from mind.core import debug_log
from mind.core.chores.busy import Busy
from mind.core.chores.idle_policy import decide_pulse
from mind.core.chores.pulse_experience import connection_gap_seconds
from mind.core.chores.orchestrator import (
    default_call_fn,
    rest_brain,
    run_persona_growth_for,
    run_post_turn_summaries,
    run_sleep,
    run_waking,
)
from mind.core.chores.pulse_state import (
    DEFAULT_PULSE_STATE_PATH,
    load_pulse_state,
    record_pulse_fire,
    save_pulse_state,
)
from mind.core.factory import create_core
from mind.core.idea import Idea
from mind.core.lifelog import MASTER, ConversationLog, Line, PulseLog, read_conversation, refs_of
from mind.core.memory.memory import Memory
from mind.core.memory.page import load_pages
from mind.core.memory.relation import due_today
from mind.core.memory.sleep import SleepReport, unslept_lines
from mind.core.memory.structure import JST, MASTER_NAME
from mind.core.memory.writing import WordsRejected
from mind.core.protection import (
    DEFAULT_CHANGE_LOG_PATH,
    DEFAULT_GENERATION_STORE_PATH,
    ChangeLog,
    ChangeReport,
    GenerationStore,
)
from mind.core.state.persona_propose_state import (
    DEFAULT_PERSONA_PROPOSE_STATE_PATH,
    load_persona_propose_state,
    save_persona_propose_state,
)
from mind.core.state.serina_boundary_state import (
    DEFAULT_SERINA_BOUNDARY_STATE_PATH,
    load_serina_boundary_state,
    save_serina_boundary_state,
)
from mind.core.state.serina_day import serina_day_id, serina_day_start, should_run_day_boundary
from mind.core.state.session import Turn

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT_ENV = "NIRAI_MIND_PORT"  # 精神への道の正本は世界の設定（world/sea/settings.ts）。ここは既定値を持たない

FALLBACK_APOLOGY = "ごめん、今つながりにくいみたい。Ollama が動いているか確認してもらえる？"
UNRECORDED_NOTICE = "（今のやりとりを記録に書けなかった。この会話は覚えていられないかもしれない）"
MASTER_DELETE_REASON = "マスター手動（窓で発言を削除）"


def _run_post_turn_summaries_async(state: "MindState") -> None:
    """ターン確定後の fine/coarse 要約更新（失敗しても会話は返済済み）。

    連続入力時に前回の要約スレッドが走行中なら何もしない（同じ範囲を二度要約しないため。次ターンで再挑戦される）。
    """
    if not state.summary_lock.acquire(blocking=False):
        logger.debug("ターン後要約: 前回分が走行中のためスキップ")
        return
    try:
        run_post_turn_summaries(state.core, call_fn=state.call_fn)
    except Exception:  # noqa: BLE001
        logger.exception("ターン後要約更新に失敗")
    finally:
        state.summary_lock.release()


def unslept_turns(memory: Memory | None, *, before: datetime) -> list[Turn]:
    """記録のうち before より前の、まだ記憶（出来事のページ）になっていない発言を、手元の会話の流れの形にする。

    何が記憶になったかは、眠りと同じく記録とページから決める（sleep.unslept_lines）。眠りは今の Serina 日より前しか
    ページにしないので、before に今の時刻を渡すと、まだ眠っていない前の日までの発言と、今日の発言の両方になる。
    """
    if memory is None:
        return []
    with memory.pages_lock:
        lines = unslept_lines(read_conversation(memory.idea.conversation), load_pages(memory.idea.memory), before=before)
    speakers = {MASTER: "master", memory.idea.name: "serina"}
    return [
        Turn(speaker=speakers[line.speaker], text=line.text, ts=line.ts.astimezone(timezone.utc).isoformat())
        for line in lines
        if line.speaker in speakers
    ]


def last_master_line(lines: list[Line]) -> Line | None:
    """記録で、Masterが最後に話した行。まだ一度もなければ None。"""
    return next((line for line in reversed(lines) if line.speaker == MASTER), None)


def _today_start(now: datetime) -> datetime:
    return serina_day_start(serina_day_id(now))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MindState:
    """プロセス内で1つだけ持つ実行状態（住人1人につき1プロセス）。"""

    def __init__(self, core, idea: Idea, busy: Busy) -> None:
        self.core = core
        self.busy = busy  # Masterの手元が忙しいか（見回りと眠りの区切りが聞く）
        self.conversation = ConversationLog(idea.conversation)  # 会話の生ログ（会話の正本）
        self.name = idea.name  # 記録の上の、本人の話者の名前
        self.turn_lock = threading.Lock()  # 多重送信は先行ターン完了まで待つ
        self.summary_lock = threading.Lock()  # ターン後要約スレッドの二重起動防止（非ブロッキング取得）
        self.call_fn = default_call_fn()
        self.change_log = ChangeLog(DEFAULT_CHANGE_LOG_PATH)
        self.generation_store = GenerationStore(DEFAULT_GENERATION_STORE_PATH)
        self.last_sleep: SleepReport | None = None
        # 眠り残し（起動したところ・眠りが失敗した・途中で起こされた）。会話が途切れたら（起動してまだ誰も来ていなければ
        # すぐに）、続きから眠る
        self.sleep_owed = False
        self.sleep_retry_at: datetime | None = None  # 脳の不調で眠りに失敗したら、この時刻まではやり直さない

        # Masterが最後に話しかけた時刻。起動のときは記録の最後の Master の行から始め（起こし直した直後でも会話中を守り、
        # 何日も動き続けても日界が来る）、そのあとはターンの始まりで更新する。記録に Master の行がなければ None（起動は来訪ではない）。
        # 眠りの「会話が途切れたか」・日界・Pulse の「会話中」の判定に使う。目覚めのあとにMasterが来たか・どれだけ会っていないかは、
        # 記録から読む（_maybe_fire_pulse_inner）。
        self.last_activity_at: datetime | None = None
        self.watchdog_lock = threading.Lock()  # タイムスタンプの読み書き保護
        self.serina_boundary_state_path = DEFAULT_SERINA_BOUNDARY_STATE_PATH
        self.last_boundary_serina_day = load_serina_boundary_state(self.serina_boundary_state_path)

        # 眠りのあとの人格の見直し: 1日1回の試行時刻（電源断耐性）
        self.persona_propose_state_path = DEFAULT_PERSONA_PROPOSE_STATE_PATH
        self.last_persona_propose_at = load_persona_propose_state(self.persona_propose_state_path)

        # §2.8 Pulse: 発火履歴・mute
        self.pulse_state_path = DEFAULT_PULSE_STATE_PATH
        self.pulse_mute = False
        self.event_subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._event_lock = threading.Lock()

    def reseed_flow(self, *, now: datetime) -> None:
        """手元の会話の流れを記録から作り直す：まだ記憶になっていない発言（前の日までの分は眠り終えるまで。今日の分）。
        昨日の会話が、手元にも記憶にもない時間を作らない。
        """
        self.core.end_session(keep=unslept_turns(self.core.memory, before=now))


STATE: MindState | None = None

app = FastAPI(title="Nirai Mind")


def _state() -> MindState:
    if STATE is None:
        raise RuntimeError("精神が初期化されていません（main() から起動してください）")
    return STATE


class ChatRequest(BaseModel):
    text: str


def _ev(type: str, **fields: Any) -> str:
    return json.dumps({"type": type, **fields}, ensure_ascii=False) + "\n"


def _conversation_ref(day_file: str, no: int) -> str:
    return refs_of([(day_file, no)])[0]


def _parse_conversation_ref(ref: str) -> tuple[str, int]:
    prefix = "lifelog/conversation/"
    if not ref.startswith(prefix) or ".jsonl#" not in ref:
        raise ValueError("ref")
    filename, span = ref[len(prefix):].split(".jsonl#", 1)
    if "-" not in span:
        raise ValueError("ref")
    first, last = span.split("-", 1)
    if first != last:
        raise ValueError("ref")
    no = int(first)
    if no < 1 or not filename or any(ch not in "0123456789-" for ch in filename):
        raise ValueError("ref")
    return filename, no


def _event_parts(state: MindState) -> tuple[set[queue.Queue[dict[str, Any]]], threading.Lock]:
    if not hasattr(state, "event_subscribers"):
        state.event_subscribers = set()
        state._event_lock = threading.Lock()
    return state.event_subscribers, state._event_lock


def _publish_event(state: MindState, event: dict[str, Any]) -> None:
    subscribers, lock = _event_parts(state)
    with lock:
        targets = tuple(subscribers)
    for target in targets:
        target.put(event)


def _sse_events() -> Iterator[str]:
    state = _state()
    target: queue.Queue[dict[str, Any]] = queue.Queue()
    subscribers, lock = _event_parts(state)
    with lock:
        subscribers.add(target)
    try:
        yield ": connected\n\n"
        while True:
            try:
                event = target.get(timeout=15)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
    finally:
        with lock:
            subscribers.discard(target)


def _chat_events(text: str) -> Iterator[str]:
    """1ターン分の NDJSON イベントを流す。

    本体（ロック保持・Core呼び出し）は製造スレッド側で実行し、HTTP側の
    このジェネレータはキューを読むだけ。クライアントが切断されても製造
    スレッドは必ず完走するため、履歴保存とロック解放が保証される
    （ジェネレータへの GeneratorExit 配達はサーバ実装依存で信頼できない）。
    """
    events: "queue.Queue[str | None]" = queue.Queue()
    threading.Thread(target=_produce_turn, args=(text, events), daemon=True).start()
    while (item := events.get()) is not None:
        yield item


def _produce_turn(text: str, events: "queue.Queue[str | None]") -> None:
    """1ターンを実行し、イベントを events へ積む。

    Core.turn_routed の on_token/on_reply で返答本文をトークン単位ストリーミングする。イベント順序:
      token* → done(reply のみ・1通目確定) → [裏で評価・記録・気持ち] → done(reply+citations・終幕)
    1通目確定後の抽出は同スレッドで続くため、HTTPストリームは終幕まで開いたまま。
    on_reply が発火しないBrain（callbacks非対応・空応答からの最終防衛線復帰）でも、
    終幕の done がフロントの一括表示フォールバックを駆動する。
    Gemini/Tavily窓口の結果はConverse呼び出し前にCoreが確定させ、1通で返す（無言統合パイプライン）。
    出典はcitations（終幕doneの付加フィールド）として届き、reply（記録に残る発話本体）には混ざらない。
    """
    state = _state()
    # §2.4: 会話が来た＝生きている証拠。眠っていれば、区切りのいいところで起きる（core/memory/sleep.py）。
    with state.watchdog_lock:
        state.last_activity_at = datetime.now(timezone.utc)

    with state.turn_lock:
        try:
            delivered = {"reply": False}  # 1通目が画面に確定済みか（例外時の文言出し分け用）

            def on_token(chunk: str) -> None:
                events.put(_ev("token", text=chunk))

            def on_reply(reply_text: str) -> None:
                delivered["reply"] = True
                events.put(_ev("done", reply=reply_text))

            try:
                result = state.core.turn_routed(
                    text,
                    now=datetime.now(timezone.utc),
                    on_token=on_token,
                    on_reply=on_reply,
                )
                reply = result.report.reply
            except Exception as exc:  # noqa: BLE001 — 人格の謝り文言に変換
                logger.exception("ターン処理に失敗")
                phase = "after_reply" if delivered["reply"] else "before_reply"
                debug_log.emit(
                    kind="turn",
                    action="error",
                    phase=phase,
                    error=type(exc).__name__,
                    detail=str(exc),
                )
                if delivered["reply"]:
                    # 返答は届いている。裏方（抽出）の失敗で本文を上書きしない
                    events.put(_ev("notice", text="（裏の整理で少しつまずいたみたい。会話は続けられるよ）"))
                else:
                    events.put(_ev("error", text=FALLBACK_APOLOGY))
                return

            try:
                said = state.conversation.append(ts=_utc_now_iso(), speaker=MASTER, text=text)
                answered = state.conversation.append(ts=_utc_now_iso(), speaker=state.name, text=reply)
            except Exception as exc:  # noqa: BLE001 — 記録に書けなかったことを、書けたように扱わない
                logger.exception("会話を記録に書けなかった")
                debug_log.emit(kind="lifelog", action="append_failed", error=type(exc).__name__, detail=str(exc))
                events.put(_ev("notice", text=UNRECORDED_NOTICE))
                return
            _publish_event(state, {"type": "said", "ref": _conversation_ref(*answered), "text": reply})
            # 会話を記録してから、評価で気持ちを動かす（気持ちの記録は、拠った会話の場所を持つ）
            state.core.feel(result, source=refs_of((said, answered)), now=datetime.now(timezone.utc))

            threading.Thread(
                target=_run_post_turn_summaries_async,
                args=(state,),
                daemon=True,
            ).start()

            # Tavily出典（citations）はreply（記録に残る発話本体）とは別経路でGUIへ届ける（画面の注記）。
            citations = getattr(result, "citations", None)
            events.put(_ev("done", reply=reply, citations=citations))
        finally:
            events.put(None)  # 番兵: HTTP側のジェネレータを必ず終了させる


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    return StreamingResponse(
        _chat_events(req.text), media_type="application/x-ndjson")


@app.get("/api/conversation")
def api_conversation(before: str | None = None, limit: int = 50):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit は1〜200です")
    state = _state()
    lines = read_conversation(state.conversation.directory)
    end = len(lines)
    if before is not None:
        try:
            day_file, no = _parse_conversation_ref(before)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="before のrefが不正です") from None
        end = next(
            (i for i, line in enumerate(lines) if line.day_file == day_file and line.no == no),
            -1,
        )
        if end < 0:
            raise HTTPException(status_code=404, detail="before の発言が見つかりません")
    start = max(0, end - limit)
    page = lines[start:end]
    return {
        "messages": [
            {
                "ref": _conversation_ref(line.day_file, line.no),
                "ts": line.ts.isoformat(),
                "speaker": line.speaker,
                "text": line.text,
            }
            for line in page
        ],
        "has_more": start > 0,
    }


@app.get("/api/events")
def api_events():
    return StreamingResponse(
        _sse_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/state")
def api_state():
    state = _state()
    sleep = state.last_sleep
    return {
        # 眠りで本人の言葉を書けなかったページ（次の眠りでもう一度。原則1: 無言で捨てない）
        "unwritten_pages": len(sleep.failed) if sleep else 0,
    }


@app.post("/api/pulse/mute")
def api_pulse_mute(mute: bool = True):
    state = _state()
    with state.watchdog_lock:
        state.pulse_mute = mute
    return {"mute": mute}


def _require_master_confirm(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true が必要です")


def _forget(state: MindState, erased: list, *, what: str) -> list[str]:
    """Masterが記録から消した発言に拠っていたページを外し（気持ちの記録はその言葉を消し）、変更レポートを残す（原則1）。"""
    forgotten = state.core.forget(erased)
    state.change_log.record(
        ChangeReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=f"Masterが{what}を削除",
            target_id=",".join(forgotten) or 0,
            reason=MASTER_DELETE_REASON,
            before=json.dumps({"erased_lines": len(erased), "forgotten_pages": forgotten}, ensure_ascii=False),
            after=None,
        )
    )
    return forgotten


@app.delete("/api/conversation/{ref:path}")
def api_conversation_delete(ref: str, confirm: bool = False):
    """発言1件を記録から消す（マスター確認必須。本文を消した印の行になる）。その発言に拠っていた記憶のページも外し、
    手元の会話の流れも作り直す。同じ出来事の残りの発言は、次の眠りで本人が思い出し直す（core/memory/memory.py）。
    """
    _require_master_confirm(confirm)
    try:
        day_file, no = _parse_conversation_ref(ref)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="refが不正です") from None
    state = _state()
    with state.turn_lock:
        if state.conversation.remove_at(day_file, no) is None:
            raise HTTPException(status_code=404, detail="発言が見つかりません")
        forgotten = _forget(state, [(day_file, no)], what="発言")
        state.reseed_flow(now=datetime.now(timezone.utc))
    return {"ok": True, "ref": ref, "forgotten_pages": forgotten}


def _idle_watchdog(state: MindState, timing: AppTimingConfig) -> None:
    """見回りスレッド。Pulse と Serina 日界（眠り）を駆動する。"""
    while True:
        try:
            time.sleep(timing.idle_poll_interval_seconds)
            _watchdog_tick_at(state, timing, now=datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001 — 見回りスレッドが死ぬと全トリガーが止まるため必ず継続
            logger.exception("見回りスレッドで例外")


def _watchdog_tick_at(state: MindState, timing: AppTimingConfig, *, now: datetime) -> None:
    """`now`を注入できる本体（テストが sleep 無しで検査するための縫い目）。"""
    if state.busy.busy(now):
        _rest_brain_while_busy(state, timing, now=now)
        return
    _maybe_fire_pulse(state, timing, now=now)
    _maybe_run_serina_day_boundary(state, timing, now=now)


def _conversation_active(state: MindState, timing: AppTimingConfig, *, now: datetime) -> bool:
    """会話中か：ターンの最中だけでなく、Masterの最後の発言から少しのあいだも（会話が途切れるまで）。"""
    with state.watchdog_lock:
        last_activity_at = state.last_activity_at
    return state.turn_lock.locked() or (
        last_activity_at is not None
        and (now - last_activity_at).total_seconds() < timing.serina_day_grace_after_activity_seconds
    )


def _rest_brain_while_busy(state: MindState, timing: AppTimingConfig, *, now: datetime) -> None:
    """Masterの手元が忙しい間は、会話中でなければ、脳をグラボから下ろす。話しかけられたら、Ollama がまた載せる。"""
    if _conversation_active(state, timing, now=now):
        return
    try:
        if rest_brain(state.core):
            logger.info("見回り: Masterの手元が忙しいので、脳をグラボから下ろした")
    except Exception:  # noqa: BLE001 — 下ろせなくても、見回りは続ける
        logger.exception("見回り: 脳を下ろせなかった")


def _sleep_and_grow(
    state: MindState,
    *,
    now: datetime,
    should_stop: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """眠って、眠り終えたら人格を見直す（1日1回）。最後まで眠れたら True。"""

    def on_progress(message: str) -> None:
        logger.info(message)
        if progress is not None:
            progress(message)

    report = run_sleep(state.core, now=now, should_stop=should_stop, progress=on_progress)
    state.last_sleep = report
    if report is not None and not report.finished:
        return False
    if report is not None and report.failed:
        logger.warning("眠り: 本人の言葉を書けなかったページ %d（次の眠りでもう一度）", len(report.failed))
    if should_stop():  # 人格の見直しと目覚めも、眠りの続き（次に眠るとき、眠ることはもうないので、ここから続く）
        return False
    outcome = run_persona_growth_for(
        state.core,
        call_fn=state.call_fn,
        change_log=state.change_log,
        generation_store=state.generation_store,
        now=now,
        last_propose_at=state.last_persona_propose_at,
    )
    if outcome.advance_cooldown:
        state.last_persona_propose_at = now
        save_persona_propose_state(state.persona_propose_state_path, last_propose_at=now)
    if outcome.revised:
        logger.info("人格の見直し: %s を書き換えた（%s）", outcome.block_id, outcome.reason)
    if should_stop():
        return False
    try:
        waking = run_waking(state.core, now=now)
    except WordsRejected as e:  # 書けなくても眠りは済んでいる。次に眠り終えたときに、もう一度書く
        logger.warning("目覚め: 今の自分を書けなかった（%s）", e)
    else:
        if waking is not None:
            on_progress("目覚めて、今の自分を書いた" + ("（伝えたいことがある）" if waking.tell else ""))
    return True


def _mark_boundary(state: MindState, *, now: datetime) -> None:
    current_day = serina_day_id(now)
    with state.watchdog_lock:
        state.last_boundary_serina_day = current_day
    save_serina_boundary_state(state.serina_boundary_state_path, last_boundary_serina_day=current_day)


def _maybe_run_serina_day_boundary(state: MindState, timing: AppTimingConfig, *, now: datetime) -> None:
    """§2.4 Serina 日界と眠り残し: 会話が途切れたら眠る → 人格の見直し → 目覚め → 手元の流れを今日の分に。"""
    try:
        _maybe_run_serina_day_boundary_inner(state, timing, now=now)
    except Exception:  # noqa: BLE001
        logger.exception("見回り: Serina 日界処理に失敗")


def _try_sleep(state: MindState, timing: AppTimingConfig, *, now: datetime, should_stop: Callable[[], bool], progress=None) -> bool:  # noqa: ANN001
    """眠る。最後まで眠れたら True。脳の不調で失敗したら、しばらくあけてからやり直す（見回りのたびに失敗を繰り返さない）。"""
    try:
        finished = _sleep_and_grow(state, now=now, should_stop=should_stop, progress=progress)
    except Exception:  # noqa: BLE001 — 脳（Ollama）の停止など。記録は残っているので、あとで続きから眠る
        logger.exception("眠りに失敗（%d秒あけて、続きから眠る）", timing.sleep_retry_after_failure_seconds)
        state.sleep_owed = True
        state.sleep_retry_at = now + timedelta(seconds=timing.sleep_retry_after_failure_seconds)
        return False
    state.sleep_owed = not finished
    state.sleep_retry_at = None
    return finished


def _maybe_run_serina_day_boundary_inner(state: MindState, timing: AppTimingConfig, *, now: datetime) -> None:
    if state.sleep_retry_at is not None and now < state.sleep_retry_at:
        return
    with state.watchdog_lock:
        last_activity = state.last_activity_at
        last_boundary = state.last_boundary_serina_day
    grace = timing.serina_day_grace_after_activity_seconds
    if last_activity is None:  # Masterがまだ一度も来ていない：会話は途切れている。眠り残しは眠る
        quiet, boundary_due = True, False
    else:
        quiet = (now - last_activity).total_seconds() >= grace
        boundary_due = should_run_day_boundary(
            now=now, last_activity_at=last_activity, last_boundary_serina_day=last_boundary, grace_seconds=grace,
        )
    today = _today_start(now)
    stale_flow = any(t.ts and datetime.fromisoformat(t.ts) < today for t in state.core.session.turns)
    if not (boundary_due or (quiet and (state.sleep_owed or stale_flow))):
        return

    # 眠っている間も会話はできる（脳は順番に使う）。Masterが話しかけたか、Masterの手元が忙しくなったら、区切りのいいところで
    # 起きて、次に会話が途切れて手が空いたときに続きから眠る。眠り終えるまでは、昨日の会話も手元の流れに残っている。
    started = time.monotonic()

    def woken() -> bool:
        with state.watchdog_lock:
            if state.last_activity_at != last_activity:
                return True
        return state.busy.busy(now + timedelta(seconds=time.monotonic() - started))

    if not _try_sleep(state, timing, now=now, should_stop=woken):
        logger.info("見回り: 眠り残しがある（次に会話が途切れたら続きから）")
        return
    if not state.turn_lock.acquire(blocking=False):
        return  # 会話中。次の見回りで（もう眠り終えているので、次は眠りの残りがなく、すぐ済む）
    try:
        # 眠り終えたら、手元の会話の流れは、まだ眠っていない今日の発言だけ（昨日の分は記憶になった）
        state.core.end_session(
            keep=[t for t in state.core.session.turns if t.ts and datetime.fromisoformat(t.ts) >= today],
        )
        if boundary_due:
            _mark_boundary(state, now=now)
            logger.info("見回り: Serina 日界。眠り終えた（day=%s）", serina_day_id(now).isoformat())
    finally:
        state.turn_lock.release()


def _maybe_fire_pulse(state: MindState, timing: AppTimingConfig, *, now: datetime) -> None:
    """§2.8 Pulse: 決定論判定 → Brain 文面生成 → 会話の記録（窓へは said で知らせる）。"""
    try:
        _maybe_fire_pulse_inner(state, timing, now=now)
    except Exception:  # noqa: BLE001 — 見回りスレッドは Pulse 失敗でも継続
        logger.exception("見回り: Pulse 判定/生成に失敗")


def _due_today(state: MindState, now: datetime) -> tuple[str, ...]:
    """今日がその日の、マスターとの約束や予定・記念日（core/memory/relation.py）。読めなければ、なし。"""
    if state.core.memory is None:
        return ()
    try:
        relation = state.core.memory.relation(MASTER_NAME)
    except Exception:  # noqa: BLE001 — 読めなくても Pulse の判定は続ける
        logger.exception("見回り: マスターとのことを読めなかった")
        return ()
    return tuple(thing.text for thing in due_today(relation, now.astimezone(JST).date()))


def _maybe_fire_pulse_inner(state: MindState, timing: AppTimingConfig, *, now: datetime) -> None:
    with state.watchdog_lock:
        mute = state.pulse_mute
    conversation_active = _conversation_active(state, timing, now=now)  # 眠りと同じく、会話が途切れてから
    pulse_state = load_pulse_state(state.pulse_state_path)
    waking = state.core.memory.waking() if state.core.memory is not None else None
    feelings = state.core.feelings
    connection_gap = state.core.thresholds.pulse_same_kind_gap_seconds
    # 目覚めのあとにMasterが来たか・どれだけ会っていないかは、記録で見る（再起動をまたいでも失わない）
    lines = read_conversation(state.conversation.directory)
    master_line = last_master_line(lines)
    pulse_log: PulseLog | None = None
    memory_idea = getattr(state.core.memory, "idea", None) if state.core.memory is not None else None
    if isinstance(memory_idea, Idea):
        pulse_log = PulseLog(memory_idea.pulse)
        connection_gap = connection_gap_seconds(pulse_log.entries(), lines, base_seconds=connection_gap)
    decision = decide_pulse(
        now=now,
        mute=mute,
        conversation_active=conversation_active,
        last_pulse_at=pulse_state.get("last_pulse_at"),
        last_by_kind=pulse_state.get("last_by_kind") or {},
        config=state.core.thresholds.pulse_config(),
        lonely=feelings.lonely(now) if feelings is not None else False,
        woke_at=waking.at if waking else None,
        tell=waking.tell if waking else "",
        connection_gap_seconds=connection_gap,
        connection_preferred_time=waking.call_time if waking else "",
        master_spoke_at=master_line.ts if master_line else None,
        due_today=_due_today(state, now),
    )
    if not decision.should_fire or decision.candidate is None:
        return
    if not state.turn_lock.acquire(blocking=False):
        debug_log.emit(
            kind="pulse",
            action="skip",
            reason="turn_lock",
            pulse_kind=decision.candidate.kind,
            trigger_id=decision.candidate.trigger_id,
        )
        return
    try:
        text = state.core.generate_pulse_text(decision.candidate)
        if not text:
            logger.info("見回り: Pulse 文面生成を見送り（Brain 空応答）")
            debug_log.emit(
                kind="pulse",
                action="skip",
                reason="empty_brain",
                pulse_kind=decision.candidate.kind,
                trigger_id=decision.candidate.trigger_id,
            )
            return
        updated = record_pulse_fire(
            pulse_state,
            kind=decision.candidate.kind,
            trigger_id=decision.candidate.trigger_id,
            fired_at=now,
        )
        save_pulse_state(
            state.pulse_state_path,
            last_pulse_at=now,
            last_by_kind=updated["last_by_kind"],
        )
        if pulse_log is not None:
            pulse_log.append(
                ts=now,
                kind=decision.candidate.kind,
                trigger_id=decision.candidate.trigger_id,
            )
        # 返事と同じく会話の記録に書いてから、窓へ知らせる。本人が話したことなので、手元の会話の流れにも置く
        ts = now.astimezone(timezone.utc).isoformat()
        recorded = state.conversation.append(ts=ts, speaker=state.name, text=text)
        state.core.session.add_turn(Turn(speaker="serina", text=text, ts=ts))
        _publish_event(
            state,
            {"type": "said", "ref": _conversation_ref(*recorded), "text": text, "kind": decision.candidate.kind},
        )
        ctx = decision.candidate.context or {}
        debug_log.emit(
            kind="pulse",
            action="fire",
            pulse_kind=decision.candidate.kind,
            trigger_id=decision.candidate.trigger_id,
            reason=ctx.get("reason"),
            since_master_spoke=ctx.get("since_master_spoke"),
        )
        logger.info("見回り: Pulse を会話の記録へ書いた（kind=%s）", decision.candidate.kind)
    finally:
        state.turn_lock.release()


def run_startup_morning_routine(state: MindState, *, now: datetime) -> None:
    """§2.4 起動時の朝礼: 記録から、手元の会話の流れと、Masterが最後に話した時刻を作り、眠り残しの印を付ける。
    眠りそのものは見回りが裏で行う。

    起動したらすぐ話せるようにする（前は話し始める前に眠り終えていたので、脳の読み込みと眠りで10分近く待った）。
    眠り終えるまでは、記録のうちまだ記憶になっていない前の日までの会話を手元に持つので、昨日の会話が手元にも記憶にもない
    時間はできない。眠り終えたら、見回りが手元を今日の分だけにする。眠ることがなければ、眠りはすぐ済む。
    """
    state.reseed_flow(now=now)
    master_line = last_master_line(read_conversation(state.conversation.directory))
    with state.watchdog_lock:
        state.last_activity_at = master_line.ts if master_line else None
    state.sleep_owed = True


_MIND_LOCK: Any = None  # プロセスの最後まで握る（閉じるとロックが外れる）


def hold_mind_lock(idea: Idea) -> bool:
    """イデアごとに精神は1つ。<イデア>/data/mind.lock を OS のロックで取る。取れたら True。

    ロックはプロセスが終われば（落ちても）OS が外す。窓は精神が止まっていれば起こしに来るので（1分に1回まで。窓が
    2つ開くこともある）、起こす声が重なっても、手で起こした精神と重なっても、1つのイデアに2つの精神が書かない。
    """
    global _MIND_LOCK
    import msvcrt

    path = idea.data / "mind.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 — 握ったままにする
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False
    _MIND_LOCK = handle
    return True


def main() -> None:
    global STATE
    import uvicorn

    from mind.core.idea import IDEA

    port = os.environ.get(PORT_ENV, "").strip()
    if not port.isdigit():
        print(f"環境変数 {PORT_ENV} に、待つポートを指定してください（精神への道は世界の設定が持つ）。", flush=True)
        sys.exit(2)
    if not hold_mind_lock(IDEA):  # イデアに触れる前に
        print(f"{IDEA.root} の精神は、もう起きています（data/mind.lock）。", flush=True)
        sys.exit(3)
    print("精神を起動しています…（Ollama が必要。会話Brainは単一構成）", flush=True)

    core = create_core()
    timing = load_app_timing()

    STATE = MindState(core, IDEA, Busy(timing.busy))
    run_startup_morning_routine(STATE, now=datetime.now(timezone.utc))

    threading.Thread(target=_idle_watchdog, args=(STATE, timing), daemon=True).start()

    print(f"http://{HOST}:{port} で待っています。", flush=True)
    try:
        uvicorn.run(app, host=HOST, port=int(port), log_level="warning")
    except SystemExit:
        raise
    except OSError as exc:
        print(f"起動エラー: ポート {port} を使えません: {exc}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
