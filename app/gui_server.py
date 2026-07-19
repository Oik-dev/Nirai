"""GUI サーバ（FastAPI）— Core の薄い皮。判断ロジックは持たない

会話・想起・付箋・蒸留の判断は core.runtime.Core に一本化。
セッションID・会話履歴の永続化は core の SessionStore + SessionManager（帳簿係）。
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from serina.app.idle_config import AppTimingConfig, load_app_timing
from serina.core.chores.gpu_guard import is_gpu_busy
from serina.core.chores.idle_policy import (
    decide_pulse,
    decide_session_end,
    should_digest,
    should_generate_diary,
    should_generate_diary_at_startup,
    should_run_idle_chores,
)
from serina.core.chores.pulse_state import (
    DEFAULT_PULSE_STATE_PATH,
    load_pulse_state,
    record_pulse_fire,
    save_pulse_state,
)
from serina.core.chores.orchestrator import (
    build_default_lane_call_fns,
    run_diary_generation,
    run_idle_chore_tick,
    run_startup_chores,
)
from serina.core.chores.summaries import DEFAULT_BLOCKS_PATH
from serina.core.factory import DEFAULT_MEMORY_DB_PATH, create_core
from serina.core.memory.protection import (
    DEFAULT_CHANGE_LOG_PATH,
    DEFAULT_GENERATION_STORE_PATH,
    ChangeLog,
    GenerationStore,
)
from serina.core.memory.session_store import SessionStore
from serina.core.state.diary_state import DEFAULT_DIARY_STATE_PATH, load_diary_state, save_diary_state
from serina.core.state.persona_propose_state import (
    DEFAULT_PERSONA_PROPOSE_STATE_PATH,
    load_persona_propose_state,
    save_persona_propose_state,
)
from serina.core.state.session_book import SessionBookConfig, SessionManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

GUI_HOST = "127.0.0.1"
DEFAULT_LIFE_DIR = ROOT / "life"
GUI_PORT = 8765
WEB_DIR = Path(__file__).resolve().parent / "web"


class NoCacheStaticFiles(StaticFiles):
    """静的ファイル（app.js等）をブラウザキャッシュに古いまま握らせないための配信クラス。

    DECISIONS 2026-07-11: コード更新後もブラウザが古い app.js を実行し続ける事故が
    実機確認で発生した。Cache-Control: no-cache を付けると「使う前に必ずサーバへ
    確認する」動作になり（ETag/Last-Modified による条件付きGET）、内容が同じなら
    304 が返るだけなので通信量は増えない。
    """

    def file_response(self, *args: Any, **kwargs: Any):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

FALLBACK_APOLOGY = "ごめん、今つながりにくいみたい。Ollama が動いているか確認してもらえる？"

# §2.4セッション終了の定義「明示の別れの挨拶」。無操作タイムアウトは _idle_watchdog が別途担う
# （旧トリガー1=心拍途絶は2026-07-12に死に枝と判明し廃止。DECISIONS参照）。
FAREWELL_PHRASES = ("おやすみ", "またね", "じゃあね", "バイバイ", "ばいばい")


def _is_farewell(text: str) -> bool:
    return any(phrase in text for phrase in FAREWELL_PHRASES)


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
        self.lane_call_fns = build_default_lane_call_fns()
        self.change_log = ChangeLog(DEFAULT_CHANGE_LOG_PATH)
        self.generation_store = GenerationStore(DEFAULT_GENERATION_STORE_PATH)
        self.db_path = DEFAULT_MEMORY_DB_PATH
        self.life_dir = DEFAULT_LIFE_DIR
        self.summaries_path = DEFAULT_BLOCKS_PATH
        self.last_export_life_at: datetime | None = None

        # §2.4 セッション終了の定義・②アイドル時トリガー用の見張り状態。
        # 起動直後は「今まさに繋がった」とみなし、活動時刻を現在時刻で初期化する。
        now = datetime.now(timezone.utc)
        self.last_activity_at = now
        self.session_ended = False
        self.watchdog_lock = threading.Lock()  # session_ended・タイムスタンプの読み書き保護
        # §4.5 夜間放出: since_iso=前回日記以降を当日材料とみなす（calendar日付演算を避ける
        # 設計。2026-07-12マスター承認）。last_diary_atと気分の軌跡は電源断をまたいで
        # 永続化する（2026-07-12改訂。旧設計はプロセス内メモリのみで「夜に会話→電源断」
        # 運用では日記が一度も生成されない構造欠陥があった。DECISIONS参照）。
        self.diary_state_path = DEFAULT_DIARY_STATE_PATH
        last_diary_at, mood_trajectory = load_diary_state(self.diary_state_path)
        self.last_diary_at = last_diary_at
        self.core.emotion.mood_trajectory = mood_trajectory

        # Sleep 人格提案器: 1日1回の試行時刻（電源断耐性）
        self.persona_propose_state_path = DEFAULT_PERSONA_PROPOSE_STATE_PATH
        self.last_persona_propose_at = load_persona_propose_state(
            self.persona_propose_state_path,
        )

        # §3.6 Pulse: 発火履歴・mute・GUI 通知キュー
        self.pulse_state_path = DEFAULT_PULSE_STATE_PATH
        self.pulse_mute = False
        self.pulse_queue: list[dict[str, str]] = []
        self._pulse_lock = threading.Lock()


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

    NewCore（core）はon_token（トークン単位ストリーミング）に対応していない
    （Qwenも返答生成＋感情抽出の内部2段発注のため一括返答が基本。DECISIONS 2026-07-11・
    2026-07-18決定7参照）。
    "token"イベントは発行せず、"done"イベントのreplyのみ返す。フロントエンド
    （app/web/app.js）は既にトークン無しの一括表示フォールバックを持つため無改修で動く。
    """
    state = _state()
    # §2.4: 会話が来た＝生きている証拠。見回りスレッドの誤終了判定を防ぎ、
    # 前回アイドル終了していれば新しいセッションとして再開する。
    with state.watchdog_lock:
        state.last_activity_at = datetime.now(timezone.utc)
        state.session_ended = False

    with state.turn_lock:
        try:
            try:
                result = state.core.turn_routed(text, now=datetime.now(timezone.utc))
                reply = result.report.reply
            except Exception:  # noqa: BLE001 — 人格の謝り文言に変換
                logger.exception("GUI ターン処理に失敗")
                events.put(_ev("error", text=FALLBACK_APOLOGY))
                return

            state.session_store.add_history(state.session_id, "user", text)
            state.session_store.add_history(state.session_id, "assistant", reply)

            # §4.5 気分の軌跡はターン境界でスナップショットを永続化する（EmotionState自体は
            # I/Oを持たないLLM無しコアのため、境界はアプリ層のここが担う。advisorレビュー
            # 2026-07-12: apply_mood_delta毎ではなくターン単位で十分）。
            save_diary_state(
                state.diary_state_path,
                last_diary_at=state.last_diary_at,
                mood_trajectory=state.core.emotion.mood_trajectory,
            )

            # §2.4トリガー3(明示の別れの挨拶): 区切り印のみとし、同期での全pending消化は
            # 廃止した（2026-07-12マスター承認。重い消化はアイドル時②・朝礼③が既存配線で
            # 回収する。DECISIONS参照: 誤爆時・長い会話の直後に数十秒ブロッキングする実害を
            # 解消するため）。end_session()自体はLLM呼び出しを伴わない端数flushのみ。
            if _is_farewell(text):
                try:
                    job_ids = state.core.end_session()
                    with state.watchdog_lock:
                        state.session_ended = True
                    if state.session_mgr is not None:
                        state.session_id = state.session_mgr.rotate(
                            state.session_id, now=datetime.now(timezone.utc)
                        )
                    events.put(_ev(
                        "notice",
                        text=f"（また今度ゆっくり話そうね。新しく積んだ宿題{len(job_ids)}件、"
                             f"整理は後でやっておくよ）",
                    ))
                except Exception:  # noqa: BLE001
                    logger.exception("セッション終了処理（挨拶）に失敗")
                    events.put(_ev("notice", text="（また今度ね）"))

            events.put(_ev("done", reply=reply, session_id=state.session_id))
        finally:
            events.put(None)  # 番兵: HTTP側のジェネレータを必ず終了させる


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    return StreamingResponse(
        _chat_events(req.text), media_type="application/x-ndjson")


@app.get("/api/state")
def api_state():
    state = _state()
    return {
        "session_id": state.session_id,
        "pending": state.core.chore_box.count(kind="蒸留"),
        # 2026-07-12追加: 毒饅頭ジョブの棚上げ棚（原則1: 無言破棄禁止のGUI表示。DECISIONS参照）
        "shelved": state.core.chore_box.shelved_count(),
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
    """未読 Pulse 通知を取得してキューを空にする。"""
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


@app.get("/api/album")
def api_album():
    """日記アルバム。§4.5で生成される日記（type="diary"）を新しい順に返す。

    正典 memories は core.memory_store のみを問い合わせる（二重表示防止）。
    """
    state = _state()
    diaries = state.core.memory_store.list_by_type("diary", limit=200)
    return [{"created_at": d.created_at, "content": d.content} for d in diaries]


# 静的ファイル（/api より後に mount するので API が優先される）
app.mount("/", NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="web")


def _idle_watchdog(state: GuiState, timing: AppTimingConfig) -> None:
    """見回りスレッド。§2.4の無操作タイムアウトと②アイドル小分け消化を
    ここで駆動する。判定自体は idle_policy.py の純粋関数に委ね、ここは「起こす・判定を呼ぶ・
    実行する」だけを担う（advisorレビュー2026-07-11）。

    会話ロック(turn_lock)を共有することで「会話最優先・1件単位で中断可能」を実現する:
    - セッション終了処理(end_session()自体はLLM呼び出しなし)はロックをブロッキング取得
      （会話が長引いていても数十ms待つだけ）。取得後に判定を取り直し、その間に会話が
      再開していれば終了処理を取り消す。
    - 小分け消化はロックを非ブロッキング取得。会話中なら今回は諦めて次のティックへ譲る。
    """
    while True:
        try:
            time.sleep(timing.idle_poll_interval_seconds)
            _watchdog_tick(state, timing)
        except Exception:  # noqa: BLE001 — 見回りスレッドが死ぬと全トリガーが止まるため必ず継続
            logger.exception("見回りスレッドで例外")


def _watchdog_tick(state: GuiState, timing: AppTimingConfig) -> None:
    _watchdog_tick_at(state, timing, now=datetime.now(timezone.utc))


def _watchdog_tick_at(state: GuiState, timing: AppTimingConfig, *, now: datetime) -> None:
    """`now`を注入できる本体（tests/test_gui_watchdog.pyがsleep無しで検査するための縫い目）。"""
    with state.watchdog_lock:
        snapshot = (state.last_activity_at, state.session_ended)

    decision = decide_session_end(
        now=now,
        last_activity_at=snapshot[0],
        session_ended=snapshot[1],
        idle_timeout_after_seconds=timing.idle_timeout_after_seconds,
    )
    if decision.should_end:
        with state.turn_lock:
            # ロック待ちの間に会話が再開している可能性があるため取り直す
            now2 = datetime.now(timezone.utc)
            with state.watchdog_lock:
                snapshot2 = (state.last_activity_at, state.session_ended)
            recheck = decide_session_end(
                now=now2,
                last_activity_at=snapshot2[0],
                session_ended=snapshot2[1],
                idle_timeout_after_seconds=timing.idle_timeout_after_seconds,
            )
            if recheck.should_end:
                job_ids = state.core.end_session()
                with state.watchdog_lock:
                    state.session_ended = True
                if state.session_mgr is not None:
                    state.session_id = state.session_mgr.rotate(state.session_id, now=now2)
                logger.info(
                    "見回り: %s によりセッション終了処理（新規宿題%d件）",
                    recheck.reason, len(job_ids),
                )

    _maybe_fire_pulse(state, now=now)

    with state.watchdog_lock:
        la, ended = state.last_activity_at, state.session_ended
    if not should_run_idle_chores(session_ended=ended):
        return
    if is_gpu_busy(timing.gpu_busy_threshold_percent):
        logger.info("見回り: GPU使用率が閾値%.0f%%を超えたため裏方便の発注を見送り", timing.gpu_busy_threshold_percent)
        return
    if not state.turn_lock.acquire(blocking=False):
        return

    def _yield_to_conversation() -> bool:
        with state.watchdog_lock:
            return not state.session_ended

    try:
        outcome = run_idle_chore_tick(
            state.core,
            state.core.chore_box,
            memory_store=state.core.memory_store,
            thresholds=state.core.thresholds,
            lane_call_fns=state.lane_call_fns,
            change_log=state.change_log,
            generation_store=getattr(state, "generation_store", None),
            db_path=getattr(state, "db_path", None),
            life_dir=getattr(state, "life_dir", None),
            summaries_path=getattr(state, "summaries_path", None),
            export_min_interval_seconds=timing.export_life_min_interval_seconds,
            last_export_life_at=getattr(state, "last_export_life_at", None),
            last_persona_propose_at=getattr(state, "last_persona_propose_at", None),
            now=now,
            limit=timing.idle_digest_chunk_limit,
            failure_shelve_threshold=timing.chore_failure_shelve_threshold,
            yield_check=_yield_to_conversation,
        )
        if outcome.interrupted:
            logger.info("見回り: 会話再開のため裏方仕事を checkpoint 付きで中断")
        elif outcome.progressed:
            if outcome.kind == "distillation":
                logger.info("見回り: アイドル小分け消化を実行")
            elif outcome.kind == "rolling_summary":
                logger.info("見回り: 転がし要約を更新しました")
            elif outcome.kind == "persona_revise":
                logger.info("見回り: persona 可変ブロックを改訂しました")
            elif outcome.kind == "persona_propose":
                state.last_persona_propose_at = now
                save_persona_propose_state(
                    state.persona_propose_state_path,
                    last_propose_at=now,
                )
                logger.info("見回り: Sleep 人格提案を試行しました")
            elif outcome.kind == "export_life":
                state.last_export_life_at = now
                logger.info("見回り: life/ を DB から再生成しました")
    finally:
        state.turn_lock.release()

    _maybe_generate_diary(state, timing, now=now)


def _pulse_mood_from_core(core) -> dict[str, float]:  # noqa: ANN001
    emotion = getattr(core, "emotion", None)
    if emotion is None:
        return {}
    mood = getattr(emotion, "mood", None)
    if isinstance(mood, dict):
        return dict(mood)
    return {}


def _maybe_fire_pulse(state: GuiState, *, now: datetime) -> None:
    """§3.6 Pulse: 決定論判定 → Brain 文面生成 → GUI キュー。"""
    try:
        _maybe_fire_pulse_inner(state, now=now)
    except Exception:  # noqa: BLE001 — 見回りスレッドは Pulse 失敗でも継続
        logger.exception("見回り: Pulse 判定/生成に失敗")


def _maybe_fire_pulse_inner(state: GuiState, *, now: datetime) -> None:
    pulse_state_path = getattr(state, "pulse_state_path", DEFAULT_PULSE_STATE_PATH)
    with state.watchdog_lock:
        last_activity_at = state.last_activity_at
        mute = getattr(state, "pulse_mute", False)
    conversation_active = state.turn_lock.locked()
    pulse_state = load_pulse_state(pulse_state_path)
    list_promises = getattr(state.core, "list_promise_memories_for_pulse", None)
    promise_memories = list_promises() if callable(list_promises) else []
    decision = decide_pulse(
        now=now,
        last_activity_at=last_activity_at,
        mute=mute,
        conversation_active=conversation_active,
        last_pulse_at=pulse_state.get("last_pulse_at"),
        last_by_kind=pulse_state.get("last_by_kind") or {},
        pulsed_promise_ids=pulse_state.get("pulsed_promise_ids") or [],
        promise_memories=promise_memories,
        mood=_pulse_mood_from_core(state.core),
        config=state.core.thresholds.pulse_config(),
    )
    if not decision.should_fire or decision.candidate is None:
        return
    if not state.turn_lock.acquire(blocking=False):
        return
    try:
        gen = getattr(state.core, "generate_pulse_text", None)
        if not callable(gen):
            return
        text = gen(decision.candidate)
        if not text:
            logger.info("見回り: Pulse 文面生成を見送り（Brain 空応答）")
            return
        updated = record_pulse_fire(
            pulse_state,
            kind=decision.candidate.kind,
            trigger_id=decision.candidate.trigger_id,
            fired_at=now,
        )
        save_pulse_state(
            pulse_state_path,
            last_pulse_at=now,
            last_by_kind=updated["last_by_kind"],
            pulsed_promise_ids=updated["pulsed_promise_ids"],
        )
        pulse_queue = getattr(state, "pulse_queue", None)
        pulse_lock = getattr(state, "_pulse_lock", None)
        if pulse_queue is not None and pulse_lock is not None:
            with pulse_lock:
                pulse_queue.append(
                    {
                        "kind": decision.candidate.kind,
                        "text": text,
                        "trigger_id": decision.candidate.trigger_id,
                    }
                )
        logger.info("見回り: Pulse 通知を生成（kind=%s）", decision.candidate.kind)
    finally:
        state.turn_lock.release()


def _maybe_generate_diary(state: GuiState, timing: AppTimingConfig, *, now: datetime) -> None:
    """§4.5 夜間放出: セッション終了後・前回の日記生成から十分間隔が空いたら1本生成する。

    `_watchdog_tick_at`内で蒸留消化・機微査定の後に呼ばれるため、宿題箱に蒸留ジョブが
    残っている間はそちらが先に消化される（1件消化した時点で当該tickは`return`する配線。
    アイドル中は新規蒸留ジョブが供給されずバックログは1〜2件/tickで枯渇するため、
    日記生成が遅延しても有界。優先度は「会話最優先」のみ共通で守る＝turn_lockの
    非ブロッキング取得で会話中は必ず譲る）。
    """
    with state.watchdog_lock:
        ended, last_diary_at = state.session_ended, state.last_diary_at
    if not should_generate_diary(
        now=now, last_diary_at=last_diary_at, session_ended=ended,
        diary_min_gap_seconds=timing.diary_min_gap_seconds,
    ):
        return
    if is_gpu_busy(timing.gpu_busy_threshold_percent):
        logger.info("見回り: GPU使用率が閾値%.0f%%を超えたため日記生成を見送り", timing.gpu_busy_threshold_percent)
        return
    if not state.turn_lock.acquire(blocking=False):
        return
    try:
        outcome = run_diary_generation(
            state.core,
            since_iso=last_diary_at.isoformat(),
            routing_rules=state.core.routing_rules,
            lane_call_fns=state.lane_call_fns,
            change_log=state.change_log,
        )
        # 生成に成功した時だけ窓(last_diary_at)を前進させる。LLM失敗・空応答時に前進させると
        # その間の記憶・気分軌跡が二度と日記材料に載らなくなる（serina-code-reviewer
        # 2026-07-12 Important指摘）。「材料なし」も未前進のまま次tickで安価に再判定される
        # （generate_and_save_diaryはLLM呼び出し前に空材料判定するため実害は小さい）。
        if outcome.generated:
            with state.watchdog_lock:
                state.last_diary_at = now
            save_diary_state(
                state.diary_state_path,
                last_diary_at=now,
                mood_trajectory=state.core.emotion.mood_trajectory,
            )
            logger.info("見回り: 日記を生成しました（書き手=%s）", outcome.lane)
        else:
            logger.info("見回り: 日記生成を見送り（理由=%s）", outcome.reason)
    finally:
        state.turn_lock.release()


def main() -> None:
    global STATE
    import uvicorn

    print("Serina GUI を起動しています…（Ollama が必要。Qwen単一運用）")

    core = create_core()
    timing = load_app_timing()

    # セッションID・会話履歴の帳簿（正典 memories とは別口の SessionStore）
    session_store = SessionStore()
    orphans = session_store.backfill_orphan_sessions()
    if orphans:
        print(f"（過去の未整理セッション {len(orphans)} 件を整理しました）")
    session_mgr = SessionManager(session_store, SessionBookConfig())
    session_id, pending_id = session_mgr.resolve_active_session()
    if pending_id:
        print(f"（前回セッション {pending_id} を区切りました）")

    # GuiStateを先に組み立て、change_log/lane_call_fnsを朝礼でも使い回す
    STATE = GuiState(core, session_store, session_mgr, session_id)

    # §2.4トリガー3(次回起動時の朝礼): 前回のやり残し(pending)を消化する。
    # 2026-07-12監査C-1: 朝礼失敗でも起動は続行（会話最優先）。
    try:
        startup_summary = run_startup_chores(
            core.chore_box,
            memory_store=core.memory_store,
            thresholds=core.thresholds,
            lane_call_fns=STATE.lane_call_fns,
            change_log=STATE.change_log,
            failure_shelve_threshold=timing.chore_failure_shelve_threshold,
        )
        if startup_summary.processed or startup_summary.failed:
            print(
                f"（前回までの積み残しを消化: 記憶化{startup_summary.total_accepted}件・"
                f"失敗{len(startup_summary.failed)}件はpending維持）"
            )
        if startup_summary.shelved:
            print(f"（処理できなかった宿題{len(startup_summary.shelved)}件を棚上げしました）")
    except Exception:  # noqa: BLE001
        logger.exception("起動時の朝礼（蒸留消化）に失敗。会話は継続します")
        print("（前回までの積み残しの消化に失敗しました。会話は始められます）")

    # §4.5①朝礼(主経路、2026-07-12改訂): 最後に日記を書いた日が前日以前なら、
    # 前回日記以降の材料で1本書く。
    if should_generate_diary_at_startup(now=datetime.now(timezone.utc), last_diary_at=STATE.last_diary_at):
        # serina-code-reviewer 2026-07-13 Important指摘(I-4): 朝礼の蒸留消化(run_startup_chores)
        # と対称に、日記生成の例外でGUI起動そのものが止まらないようにする（会話最優先）。
        try:
            diary_outcome = run_diary_generation(
                core,
                since_iso=STATE.last_diary_at.isoformat(),
                routing_rules=core.routing_rules,
                lane_call_fns=STATE.lane_call_fns,
                change_log=STATE.change_log,
            )
            if diary_outcome.generated:
                now_ = datetime.now(timezone.utc)
                STATE.last_diary_at = now_
                save_diary_state(
                    STATE.diary_state_path, last_diary_at=now_, mood_trajectory=core.emotion.mood_trajectory,
                )
                print(f"（朝礼: 前回日記以降の日記を1本書きました。書き手={diary_outcome.lane}）")
            else:
                print(f"（朝礼: 日記生成を見送り。理由={diary_outcome.reason}）")
        except Exception:  # noqa: BLE001
            logger.exception("起動時の朝礼（日記生成）に失敗。会話は継続します")
            print("（朝礼: 日記生成に失敗しました。会話は始められます）")

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
