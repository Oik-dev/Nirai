"""GUI サーバ（FastAPI）— Core の薄い皮。判断ロジックは持たない

会話・想起・付箋の判断は core.runtime.Core に一本化。眠り（記憶のページづくり）は core/memory/sleep.py。
セッションID・会話履歴の永続化は core の SessionStore + SessionManager（帳簿係）。会話の正本はイデアの生ログ。

1日の流れ：起動時の朝礼で、まだ記憶になっていない会話を眠って記憶にし、人格を見直し、目覚めて今の自分を書く。
起きている間は会話し、見回りスレッドが Pulse と Serina 日界を見る。日界を過ぎて会話が途切れたら、また眠る。
眠り終えたら、手元の会話の流れを今日の分だけにして、帳簿のセッションを切り替える。目覚めて伝えたいことがあり、
マスターがまだ来ていなければ、本人から話しかけに行く（Pulse の wake）。
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
import webbrowser
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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mind.app.idle_config import AppTimingConfig, load_app_timing
from mind.core import debug_log
from mind.core.chores.gpu_guard import is_gpu_busy
from mind.core.chores.idle_policy import decide_pulse
from mind.core.chores.orchestrator import (
    default_call_fn,
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
from mind.core.memory.session_store import SessionStore
from mind.core.memory.sleep import SleepReport
from mind.core.memory.writing import WordsRejected
from mind.core.protection import (
    DEFAULT_CHANGE_LOG_PATH,
    DEFAULT_GENERATION_STORE_PATH,
    ChangeLog,
    ChangeReport,
    GenerationStore,
)
from mind.core.state.desire_persist import (
    DEFAULT_DESIRE_STATE_PATH,
    apply_loaded_to_desire,
    load_desire_state,
    save_desire_from_state,
)
from mind.core.state.emotion_persist import (
    DEFAULT_EMOTION_STATE_PATH,
    apply_loaded_to_emotion,
    load_emotion_state,
    save_emotion_from_state,
)
from mind.core.state.persona_propose_state import (
    DEFAULT_PERSONA_PROPOSE_STATE_PATH,
    load_persona_propose_state,
    save_persona_propose_state,
)
from mind.core.state.relationship_persist import (
    DEFAULT_RELATIONSHIP_STATE_PATH,
    apply_loaded_to_relationship,
    load_relationship_state,
    save_relationship_from_state,
)
from mind.core.state.serina_boundary_state import (
    DEFAULT_SERINA_BOUNDARY_STATE_PATH,
    load_serina_boundary_state,
    save_serina_boundary_state,
)
from mind.core.state.serina_day import serina_day_id, serina_day_start, should_run_day_boundary
from mind.core.state.session import Turn
from mind.core.state.session_book import SessionBookConfig, SessionManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

GUI_HOST = "127.0.0.1"
GUI_PORT = 8765
WEB_DIR = Path(__file__).resolve().parent / "web"


class NoCacheStaticFiles(StaticFiles):
    """静的ファイル（app.js等）をブラウザキャッシュに古いまま握らせないための配信クラス。

    Cache-Control: no-cache を付けると「使う前に必ずサーバへ確認する」動作になり（ETag/Last-Modified による
    条件付きGET）、内容が同じなら 304 が返るだけなので通信量は増えない。
    """

    def file_response(self, *args: Any, **kwargs: Any):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


FALLBACK_APOLOGY = "ごめん、今つながりにくいみたい。Ollama が動いているか確認してもらえる？"
MASTER_DELETE_REASON = "マスター手動（GUIで発言・会話を削除）"


def _run_post_turn_summaries_async(state: "GuiState") -> None:
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


def flow_turns(rows: list[dict], *, since: datetime) -> list[Turn]:
    """帳簿の発言のうち since 以降のものを、手元の会話の流れの形にする（まだ眠っていない、今の Serina 日の発言）。"""
    turns = []
    for row in rows:
        role = str(row.get("role") or "")
        if role not in ("user", "assistant") or not row.get("content"):
            continue
        if datetime.fromisoformat(row["ts"]) < since:
            continue
        turns.append(Turn(speaker="master" if role == "user" else "serina", text=row["content"], ts=row["ts"]))
    return turns


def _today_start(now: datetime) -> datetime:
    return serina_day_start(serina_day_id(now))


class GuiState:
    """プロセス内で1つだけ持つ実行状態（単一ユーザー前提）。"""

    def __init__(
        self,
        core,
        session_store: SessionStore,
        session_mgr: SessionManager,
        session_id: str,
    ) -> None:
        self.core = core
        self.session_store = session_store
        self.session_mgr = session_mgr
        self.session_id = session_id
        self.turn_lock = threading.Lock()  # 多重送信は先行ターン完了まで待つ
        self.summary_lock = threading.Lock()  # ターン後要約スレッドの二重起動防止（非ブロッキング取得）
        self.call_fn = default_call_fn()
        self.change_log = ChangeLog(DEFAULT_CHANGE_LOG_PATH)
        self.generation_store = GenerationStore(DEFAULT_GENERATION_STORE_PATH)
        self.last_sleep: SleepReport | None = None
        # 眠り残し（起動時の眠りが失敗した・途中で起こされた）。次に会話が途切れたら、続きから眠る
        self.sleep_owed = False
        self.sleep_retry_at: datetime | None = None  # 脳の不調で眠りに失敗したら、この時刻まではやり直さない

        # 起動直後は「今まさに繋がった」とみなし、活動時刻を現在時刻で初期化する。
        now = datetime.now(timezone.utc)
        self.last_activity_at = now
        # まだマスターの最初の発言が来ていない間は、日界処理・Pulseを走らせない
        # （起動直後にセリナから話しかけてくる・待機中にセッションが切り替わる、を防ぐ）。最初のターンで True になる。
        self.has_had_first_turn = False
        self.watchdog_lock = threading.Lock()  # タイムスタンプの読み書き保護
        self.serina_boundary_state_path = DEFAULT_SERINA_BOUNDARY_STATE_PATH
        self.last_boundary_serina_day = load_serina_boundary_state(self.serina_boundary_state_path)

        # 感情（気分の流れを含む）と欲求の永続。起動時にオフライン分を冷ます。
        self.emotion_state_path = DEFAULT_EMOTION_STATE_PATH
        apply_loaded_to_emotion(self.core.emotion, load_emotion_state(self.emotion_state_path))
        self.desire_state_path = DEFAULT_DESIRE_STATE_PATH
        apply_loaded_to_desire(self.core.desire, load_desire_state(self.desire_state_path))
        self.core._cool_emotion(now)
        save_emotion_from_state(self.emotion_state_path, self.core.emotion)
        save_desire_from_state(self.desire_state_path, self.core.desire)

        # 関係状態（マスター観測）の永続化（§2.6・§1.5⑤末尾）。
        self.relationship_state_path = DEFAULT_RELATIONSHIP_STATE_PATH
        apply_loaded_to_relationship(
            self.core.relationship, load_relationship_state(self.relationship_state_path),
        )

        # 眠りのあとの人格の見直し: 1日1回の試行時刻（電源断耐性）
        self.persona_propose_state_path = DEFAULT_PERSONA_PROPOSE_STATE_PATH
        self.last_persona_propose_at = load_persona_propose_state(self.persona_propose_state_path)

        # §2.8 Pulse: 発火履歴・mute・チャット欄へ載せるための新着キュー
        self.pulse_state_path = DEFAULT_PULSE_STATE_PATH
        self.pulse_mute = False
        self.pulse_queue: list[dict[str, str]] = []
        self._pulse_lock = threading.Lock()

    def reseed_flow(self, *, now: datetime) -> None:
        """手元の会話の流れを、帳簿の今のセッションの、今の Serina 日の発言で作り直す。"""
        rows = self.session_store.get_session_history(self.session_id)
        self.core.end_session(keep=flow_turns(rows, since=_today_start(now)))


STATE: GuiState | None = None

app = FastAPI(title="Serina GUI")


def _state() -> GuiState:
    if STATE is None:
        raise RuntimeError("GUI が初期化されていません（main() から起動してください）")
    return STATE


class ChatRequest(BaseModel):
    text: str


def _ev(type: str, **fields: Any) -> str:
    return json.dumps({"type": type, **fields}, ensure_ascii=False) + "\n"


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
      token* → done(reply のみ・1通目確定) → [裏で感情抽出等] → done(reply+session_id+citations・終幕)
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
        state.has_had_first_turn = True

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
                logger.exception("GUI ターン処理に失敗")
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

            state.session_store.add_history(state.session_id, "user", text)
            state.session_store.add_history(state.session_id, "assistant", reply)

            # 感情（気分の流れを含む）・欲求・関係はターン境界で永続化する（各状態はI/Oを持たないため、境界はアプリ層）。
            save_emotion_from_state(state.emotion_state_path, state.core.emotion)
            save_desire_from_state(state.desire_state_path, state.core.desire)
            save_relationship_from_state(state.relationship_state_path, state.core.relationship)

            threading.Thread(
                target=_run_post_turn_summaries_async,
                args=(state,),
                daemon=True,
            ).start()

            # Tavily出典（citations）はreply（記録に残る発話本体）とは別経路でGUIへ届ける（画面の注記）。
            citations = getattr(result, "citations", None)
            events.put(_ev(
                "done", reply=reply, session_id=state.session_id, citations=citations,
            ))
        finally:
            events.put(None)  # 番兵: HTTP側のジェネレータを必ず終了させる


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    return StreamingResponse(
        _chat_events(req.text), media_type="application/x-ndjson")


@app.get("/api/state")
def api_state():
    state = _state()
    sleep = state.last_sleep
    return {
        "session_id": state.session_id,
        # 眠りで本人の言葉を書けなかったページ（次の眠りでもう一度。原則1: 無言で捨てない）
        "unwritten_pages": len(sleep.failed) if sleep else 0,
    }


@app.get("/api/history")
def api_history():
    state = _state()
    return state.session_store.get_session_history(state.session_id)


@app.get("/api/sessions")
def api_sessions():
    return _state().session_store.list_session_previews(limit=50)


@app.get("/api/sessions/{session_id}/history")
def api_session_history(session_id: str):
    store = _state().session_store
    return store.get_session_history(session_id) or store.get_archived_history(session_id)


@app.get("/api/pulse/pending")
def api_pulse_pending():
    """未読 Pulse（チャット欄へ載せる新着）を取得してキューを空にする。"""
    state = _state()
    with state._pulse_lock:
        pending = list(state.pulse_queue)
        state.pulse_queue.clear()
    return {"messages": pending}


@app.post("/api/pulse/mute")
def api_pulse_mute(mute: bool = True):
    state = _state()
    with state.watchdog_lock:
        state.pulse_mute = mute
    return {"mute": mute}


def _require_master_confirm(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true が必要です")


def _forget(state: GuiState, erased: list, *, what: str) -> list[str]:
    """Masterが記録から消した発言に拠っていたページを外し、変更レポートを残す（原則1）。"""
    memory = state.core.memory
    forgotten = memory.forget_lines(erased) if memory is not None else []
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


@app.post("/api/sessions/new")
def api_sessions_new(confirm: bool = False):
    """現行セッションを区切り、新しいセッションへ切り替える（手元の会話の流れも新しくする）。"""
    _require_master_confirm(confirm)
    state = _state()
    with state.turn_lock:
        state.core.end_session()
        if state.session_mgr is not None:
            state.session_id = state.session_mgr.rotate(
                state.session_id, now=datetime.now(timezone.utc),
            )
    return {"ok": True, "session_id": state.session_id}


@app.delete("/api/messages/{message_id}")
def api_message_delete(message_id: int, confirm: bool = False):
    """発言1件を、帳簿と生ログから消す（マスター確認必須）。その発言に拠っていた記憶のページも外す。

    同じ出来事の残りの発言は、次の眠りで本人が思い出し直す（core/memory/memory.py）。
    """
    _require_master_confirm(confirm)
    state = _state()
    with state.turn_lock:
        row = state.session_store.delete_message(message_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"発言 id={message_id} が見つからない")
        forgotten = _forget(state, row["erased"], what="発言")
        if row["session_id"] == state.session_id:
            state.reseed_flow(now=datetime.now(timezone.utc))
    return {"ok": True, "message_id": message_id, "session_id": row["session_id"], "forgotten_pages": forgotten}


@app.delete("/api/sessions/{session_id}")
def api_session_delete(session_id: str, confirm: bool = False):
    """過去セッションの会話を、帳簿と生ログから消す（マスター確認必須）。拠っていた記憶のページも外す。現行セッションは拒否。"""
    _require_master_confirm(confirm)
    state = _state()
    if session_id == state.session_id:
        raise HTTPException(status_code=400, detail="今日の会話（現行セッション）は削除できない")
    with state.turn_lock:
        result = state.session_store.delete_session(session_id)
        if result["session_deleted"] == 0 and result["history_deleted"] == 0 and result["archived_deleted"] == 0:
            raise HTTPException(status_code=404, detail=f"セッション {session_id} が見つからない")
        forgotten = _forget(state, result["erased"], what="会話セッション")
    return {"ok": True, **{k: v for k, v in result.items() if k != "erased"}, "forgotten_pages": forgotten}


# 静的ファイル（/api より後に mount するので API が優先される）
app.mount("/", NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="web")


def _idle_watchdog(state: GuiState, timing: AppTimingConfig) -> None:
    """見回りスレッド。Pulse と Serina 日界（眠り）を駆動する。"""
    while True:
        try:
            time.sleep(timing.idle_poll_interval_seconds)
            _watchdog_tick_at(state, timing, now=datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001 — 見回りスレッドが死ぬと全トリガーが止まるため必ず継続
            logger.exception("見回りスレッドで例外")


def _watchdog_tick_at(state: GuiState, timing: AppTimingConfig, *, now: datetime) -> None:
    """`now`を注入できる本体（テストが sleep 無しで検査するための縫い目）。"""
    if not is_gpu_busy(timing.gpu_busy_threshold_percent):
        _maybe_fire_pulse(state, now=now)
        _maybe_run_serina_day_boundary(state, timing, now=now)


def _sleep_and_grow(
    state: GuiState,
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

    def on_diary(day) -> None:  # noqa: ANN001 — 日記に使った気分の流れを片づけて保存する（会話の保存と重ならないように）
        with state.turn_lock:
            state.core.emotion.clear_trajectory(day=day.isoformat())
            save_emotion_from_state(state.emotion_state_path, state.core.emotion)

    report = run_sleep(state.core, now=now, should_stop=should_stop, on_diary=on_diary, progress=on_progress)
    state.last_sleep = report
    if report is not None and not report.finished:
        return False
    if report is not None and report.failed:
        logger.warning("眠り: 本人の言葉を書けなかったページ %d（次の眠りでもう一度）", len(report.failed))
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
    try:
        waking = run_waking(state.core, now=now)
    except WordsRejected as e:  # 書けなくても眠りは済んでいる。次に眠り終えたときに、もう一度書く
        logger.warning("目覚め: 今の自分を書けなかった（%s）", e)
    else:
        if waking is not None:
            on_progress("目覚めて、今の自分を書いた" + ("（伝えたいことがある）" if waking.tell else ""))
    return True


def _mark_boundary(state: GuiState, *, now: datetime) -> None:
    current_day = serina_day_id(now)
    with state.watchdog_lock:
        state.last_boundary_serina_day = current_day
    save_serina_boundary_state(state.serina_boundary_state_path, last_boundary_serina_day=current_day)


def _maybe_run_serina_day_boundary(state: GuiState, timing: AppTimingConfig, *, now: datetime) -> None:
    """§2.4 Serina 日界と眠り残し: 会話が途切れたら眠る → 人格の見直し →（日界なら）手元の流れを今日の分にしてセッション切替。"""
    try:
        _maybe_run_serina_day_boundary_inner(state, timing, now=now)
    except Exception:  # noqa: BLE001
        logger.exception("見回り: Serina 日界処理に失敗")


def _try_sleep(state: GuiState, timing: AppTimingConfig, *, now: datetime, should_stop: Callable[[], bool], progress=None) -> bool:  # noqa: ANN001
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


def _maybe_run_serina_day_boundary_inner(state: GuiState, timing: AppTimingConfig, *, now: datetime) -> None:
    if not state.has_had_first_turn:
        return
    if state.sleep_retry_at is not None and now < state.sleep_retry_at:
        return
    with state.watchdog_lock:
        last_activity = state.last_activity_at
        last_boundary = state.last_boundary_serina_day
    grace = timing.serina_day_grace_after_activity_seconds
    boundary_due = should_run_day_boundary(
        now=now, last_activity_at=last_activity, last_boundary_serina_day=last_boundary, grace_seconds=grace,
    )
    owed_and_quiet = state.sleep_owed and (now - last_activity).total_seconds() >= grace
    if not (boundary_due or owed_and_quiet):
        return

    # 眠っている間も会話はできる（脳は順番に使う）。Masterが話しかけたら、区切りのいいところで起きて、
    # 次に会話が途切れたときに続きから眠る。眠り終えるまでは、昨日の会話も手元の流れに残っている。
    def woken() -> bool:
        with state.watchdog_lock:
            return state.last_activity_at > last_activity

    if not _try_sleep(state, timing, now=now, should_stop=woken):
        logger.info("見回り: 眠り残しがある（次に会話が途切れたら続きから）")
        return
    if not boundary_due:
        return
    if not state.turn_lock.acquire(blocking=False):
        return  # 会話中。次の見回りで切り替える（もう眠り終えているので、次は眠りの残りがなく、すぐ切り替わる）
    try:
        if state.session_mgr is not None:
            state.session_id = state.session_mgr.rotate(state.session_id, now=now)
        since = _today_start(now)
        state.core.end_session(
            keep=[t for t in state.core.session.turns if t.ts and datetime.fromisoformat(t.ts) >= since],
        )
        _mark_boundary(state, now=now)
        logger.info("見回り: Serina 日界。眠り終えてセッションを切り替えた（day=%s）", serina_day_id(now).isoformat())
    finally:
        state.turn_lock.release()


def _pulse_mood_from_core(core) -> dict[str, float]:  # noqa: ANN001
    emotion = getattr(core, "emotion", None)
    if emotion is None:
        return {}
    mood = getattr(emotion, "mood", None)
    if isinstance(mood, dict):
        return dict(mood)
    return {}


def _maybe_fire_pulse(state: GuiState, *, now: datetime) -> None:
    """§2.8 Pulse: 決定論判定 → Brain 文面生成 → セッション履歴（チャット欄）。"""
    try:
        _maybe_fire_pulse_inner(state, now=now)
    except Exception:  # noqa: BLE001 — 見回りスレッドは Pulse 失敗でも継続
        logger.exception("見回り: Pulse 判定/生成に失敗")


def _maybe_fire_pulse_inner(state: GuiState, *, now: datetime) -> None:
    # マスターの最初の発言がまだ来ていない間はPulse（セリナから話しかける動作）を発火させない。
    if not state.has_had_first_turn:
        return

    with state.watchdog_lock:
        last_activity_at = state.last_activity_at
        mute = state.pulse_mute
    conversation_active = state.turn_lock.locked()
    pulse_state = load_pulse_state(state.pulse_state_path)
    waking = state.core.memory.waking() if state.core.memory is not None else None
    decision = decide_pulse(
        now=now,
        last_activity_at=last_activity_at,
        mute=mute,
        conversation_active=conversation_active,
        last_pulse_at=pulse_state.get("last_pulse_at"),
        last_by_kind=pulse_state.get("last_by_kind") or {},
        mood=_pulse_mood_from_core(state.core),
        config=state.core.thresholds.pulse_config(),
        woke_at=waking.at if waking else None,
        tell=waking.tell if waking else "",
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
        # 通常返答と同じ経路で履歴に載せ、チャット欄へ出す。本人が話したことなので、手元の会話の流れにも置く
        state.session_store.add_history(state.session_id, "assistant", text)
        state.core.session.add_turn(Turn(speaker="serina", text=text, ts=now.astimezone(timezone.utc).isoformat()))
        with state._pulse_lock:
            state.pulse_queue.append(
                {
                    "kind": decision.candidate.kind,
                    "text": text,
                    "trigger_id": decision.candidate.trigger_id,
                }
            )
        ctx = decision.candidate.context or {}
        debug_log.emit(
            kind="pulse",
            action="fire",
            pulse_kind=decision.candidate.kind,
            trigger_id=decision.candidate.trigger_id,
            reason=ctx.get("reason"),
            axis=ctx.get("dominant_axis"),
            value=ctx.get("dominant_value"),
            idle_minutes=ctx.get("idle_minutes"),
            session_id=state.session_id,
        )
        logger.info("見回り: Pulse をチャット履歴へ追加（kind=%s）", decision.candidate.kind)
    finally:
        state.turn_lock.release()


def run_startup_morning_routine(state: GuiState, timing: AppTimingConfig, *, now: datetime) -> None:
    """§2.4 起動時の朝礼: 生ログの書き足し → 眠り（まだ記憶になっていない会話を記憶に）→ 人格の見直し。

    起動が少し長くなっても、話し始める前に前の日までの会話を記憶にしておく（眠り終える前に話すと、
    昨日の会話が手元にも記憶にもない時間ができるため）。今日の分の境界処理は済んだものとして記録する
    （記録しないと、見回りスレッドが今日の日界処理をもう一度走らせ、会話中にセッションを切り替えてしまう）。
    """
    try:
        added = state.session_store.sync_conversation_log()
        if added:
            print(f"（会話の生ログに、帳簿から{added}件を書き足しました）")
    except Exception:  # noqa: BLE001
        logger.exception("起動時の朝礼（生ログの書き足し）に失敗。会話は継続します")
        print("（会話の生ログを帳簿から書き足せませんでした。次回も再試行します）")

    if not _try_sleep(state, timing, now=now, should_stop=lambda: False, progress=lambda message: print(f"（{message}）")):
        print("（眠って記憶を整理しきれませんでした。会話は始められます。会話が途切れたら続きから眠ります）")

    _mark_boundary(state, now=now)


def main() -> None:
    global STATE
    import uvicorn

    print("Serina GUI を起動しています…（Ollama が必要。会話Brainは単一構成）")

    core = create_core()
    timing = load_app_timing()

    # セッションID・会話履歴の帳簿（会話の正本はイデアの生ログ。帳簿は画面のためのもの）
    session_store = SessionStore()
    orphans = session_store.backfill_orphan_sessions()
    if orphans:
        print(f"（過去の未整理セッション {len(orphans)} 件を整理しました）")
    session_mgr = SessionManager(session_store, SessionBookConfig())
    session_id, pending_id = session_mgr.resolve_active_session()
    if pending_id:
        print(f"（前回セッション {pending_id} を区切りました）")

    STATE = GuiState(core, session_store, session_mgr, session_id)
    now = datetime.now(timezone.utc)
    run_startup_morning_routine(STATE, timing, now=now)
    STATE.reseed_flow(now=now)  # 続いているセッションの、今日の発言を手元へ

    threading.Thread(target=_idle_watchdog, args=(STATE, timing), daemon=True).start()

    url = f"http://{GUI_HOST}:{GUI_PORT}"
    print(f"ブラウザで {url} を開きます。終了はこのウィンドウで Ctrl+C。")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(app, host=GUI_HOST, port=GUI_PORT, log_level="warning")
    except SystemExit:
        raise
    except OSError as exc:
        print(f"起動エラー: ポート {GUI_PORT} を使えません（多重起動していませんか？）: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
