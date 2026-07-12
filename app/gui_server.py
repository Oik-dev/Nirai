"""GUI サーバ（FastAPI）— Core の薄い皮。判断ロジックは持たない

DECISIONS 2026-07-11「旧GUIをcore_v2へ移行」: 会話・想起・付箋・蒸留の判断は
core_v2.runtime.Core（NewCore）に一本化した。セッションID・会話履歴の永続化
（sessions/historyテーブル）は§2.6の設計上core_v2が持たない領域のため、
既存の serina.memory.store.MemoryStore + serina.core.session.SessionManager を
そのままGUIの帳簿係として流用する（NewCoreの記憶DB操作とはテーブルが別なので両立する）。
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
from serina.connectors.embedder import OllamaEmbedder
from serina.core.config import CoreConfig
from serina.core.session import SessionManager
from serina.core_v2.chores.gpu_guard import is_gpu_busy
from serina.core_v2.chores.idle_policy import decide_session_end, should_digest, should_generate_diary
from serina.core_v2.chores.orchestrator import (
    build_default_lane_call_fns,
    run_diary_generation,
    run_idle_assessment_chunk,
    run_idle_digest_chunk,
    run_session_end_chores,
    run_startup_chores,
)
from serina.core_v2.env import get_gemini_api_key
from serina.core_v2.factory import create_core_v2
from serina.core_v2.memory.protection import DEFAULT_CHANGE_LOG_PATH, ChangeLog
from serina.memory.store import MemoryStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

GUI_HOST = "127.0.0.1"
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

# §2.4セッション終了の定義トリガー3「明示の別れの挨拶」。トリガー1(GUI終了=心拍途絶)・
# トリガー2(無操作タイムアウト)は _idle_watchdog が別途担う。
FAREWELL_PHRASES = ("おやすみ", "またね", "じゃあね", "バイバイ", "ばいばい")


def _is_farewell(text: str) -> bool:
    return any(phrase in text for phrase in FAREWELL_PHRASES)


class GuiState:
    """プロセス内で1つだけ持つ実行状態（単一ユーザー前提）。"""

    def __init__(
        self,
        core,
        session_store: MemoryStore,
        session_mgr: SessionManager,
        session_id: str,
        *,
        gemini_api_key: str | None,
    ) -> None:
        self.core = core
        self.session_store = session_store
        self.session_mgr = session_mgr
        self.session_id = session_id
        self.turn_lock = threading.Lock()  # 多重送信は先行ターン完了まで待つ
        self.lane_call_fns = build_default_lane_call_fns(gemini_api_key)
        self.change_log = ChangeLog(DEFAULT_CHANGE_LOG_PATH)  # §4.6-3: 機微査定結果の記録先

        # §2.4 セッション終了の定義(3トリガー)・②アイドル時トリガー用の見張り状態。
        # 起動直後は「今まさに繋がった」とみなし、心拍・活動とも現在時刻で初期化する
        # （心拍が1本も届く前に途絶判定が誤発火しないように）。
        now = datetime.now(timezone.utc)
        self.last_heartbeat_at = now
        self.last_activity_at = now
        self.session_ended = False
        self.watchdog_lock = threading.Lock()  # session_ended・タイムスタンプの読み書き保護
        # §4.5 夜間放出: 起動直後は「まだ今日の日記は出していない」を起点時刻で表す
        # （since_iso=前回日記以降を当日材料とみなす。calendar日付演算を避ける設計。
        # 2026-07-12マスター承認）。
        self.last_diary_at = now


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

    NewCore（core_v2）はon_token（トークン単位ストリーミング）に対応していない
    （Auroraは内部で2段発注のため一括返答が基本。DECISIONS 2026-07-11参照）。
    "token"イベントは発行せず、"done"イベントのreplyのみ返す。フロントエンド
    （app/web/app.js）は既にトークン無しの一括表示フォールバックを持つため無改修で動く。
    """
    state = _state()
    # §2.4 3トリガー: 会話が来た＝生きている証拠。見回りスレッドの誤終了判定を防ぎ、
    # 前回アイドル終了していれば新しいセッションとして再開する。
    with state.watchdog_lock:
        state.last_activity_at = datetime.now(timezone.utc)
        state.last_heartbeat_at = state.last_activity_at
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

            # §2.4トリガー3(明示の別れの挨拶): ①セッション終了時の消化をここで実行する。
            # Aurora実発注を伴うため数十秒〜かかりうるが、別れの挨拶直後の1回のみなので許容する。
            if _is_farewell(text):
                events.put(_ev("notice", text="（裏でこれまでの会話を整理しています…）"))
                try:
                    job_ids, summary = run_session_end_chores(
                        state.core,
                        state.core.chore_box,
                        memory_store=state.core.memory_store,
                        thresholds=state.core.thresholds,
                        lane_call_fns=state.lane_call_fns,
                    )
                    with state.watchdog_lock:
                        state.session_ended = True
                    events.put(_ev(
                        "notice",
                        text=f"（記憶の整理が終わったよ。新しく積んだ宿題{len(job_ids)}件・"
                             f"今回の消化で記憶化{summary.total_accepted}件）",
                    ))
                except Exception:  # noqa: BLE001
                    logger.exception("セッション終了時の蒸留消化に失敗")
                    events.put(_ev("notice", text="（記憶の整理は次回に持ち越すね）"))

            events.put(_ev("done", reply=reply, session_id=state.session_id))
        finally:
            events.put(None)  # 番兵: HTTP側のジェネレータを必ず終了させる


def get_gemini_api_key_or_none() -> str | None:
    try:
        return get_gemini_api_key()
    except RuntimeError:
        return None


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    return StreamingResponse(
        _chat_events(req.text), media_type="application/x-ndjson")


@app.post("/api/heartbeat")
def api_heartbeat():
    """§2.4トリガー1(GUI終了)用の挙手ping。ブラウザが定期的に叩き、見回りスレッドが
    途絶を監視する。心拍だけでは会話の再開とは見なさない（session_endedは戻さない。
    ユーザーがまだ画面を眺めているだけの状態と、実際に話しかけて再開する状態は区別する）。
    """
    state = _state()
    with state.watchdog_lock:
        state.last_heartbeat_at = datetime.now(timezone.utc)
    return {"ok": True}


@app.get("/api/state")
def api_state():
    state = _state()
    return {
        "session_id": state.session_id,
        "pending": state.core.chore_box.count(kind="蒸留"),
    }


@app.get("/api/history")
def api_history():
    state = _state()
    return state.session_store.get_session_history(state.session_id)


@app.get("/api/sessions")
def api_sessions():
    store = _state().session_store
    sessions = []
    for s in store.list_sessions(limit=50):
        msgs = store.get_session_history(s["id"]) or store.get_archived_history(s["id"])
        first_user = next((m["content"] for m in msgs if m["role"] == "user"), "")
        sessions.append({
            "id": s["id"],
            "status": s["status"],
            "created_at": s["created_at"],
            "last_activity": s["last_activity"],
            "preview": first_user[:40],
            "empty": not msgs,
        })
    return sessions


@app.get("/api/sessions/{session_id}/history")
def api_session_history(session_id: str):
    store = _state().session_store
    return store.get_session_history(session_id) or store.get_archived_history(session_id)


@app.get("/api/album")
def api_album():
    """日記アルバム。旧アーキ由来の日記（`session_store`, `legacy/`移行分）と、
    §4.5で新規に生成される日記（`core.memory_store`, type="diary"）を新しい順にまとめる。
    """
    state = _state()
    legacy_diaries = state.session_store.list_memories_by_type("diary", limit=200)
    new_diaries = state.core.memory_store.list_by_type("diary", limit=200)
    combined = [{"created_at": d["created_at"], "content": d["content"]} for d in legacy_diaries] + [
        {"created_at": d.created_at, "content": d.content} for d in new_diaries
    ]
    combined.sort(key=lambda d: d["created_at"], reverse=True)
    return combined


# 静的ファイル（/api より後に mount するので API が優先される）
app.mount("/", NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="web")


def _idle_watchdog(state: GuiState, timing: AppTimingConfig) -> None:
    """見回りスレッド。§2.4の3トリガー(心拍途絶・無操作タイムアウト)と②アイドル小分け消化を
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
        snapshot = (state.last_heartbeat_at, state.last_activity_at, state.session_ended)

    decision = decide_session_end(
        now=now,
        last_heartbeat_at=snapshot[0],
        last_activity_at=snapshot[1],
        session_ended=snapshot[2],
        heartbeat_lost_after_seconds=timing.heartbeat_lost_after_seconds,
        idle_timeout_after_seconds=timing.idle_timeout_after_seconds,
    )
    if decision.should_end:
        with state.turn_lock:
            # ロック待ちの間に会話が再開している可能性があるため取り直す
            now2 = datetime.now(timezone.utc)
            with state.watchdog_lock:
                snapshot2 = (state.last_heartbeat_at, state.last_activity_at, state.session_ended)
            recheck = decide_session_end(
                now=now2,
                last_heartbeat_at=snapshot2[0],
                last_activity_at=snapshot2[1],
                session_ended=snapshot2[2],
                heartbeat_lost_after_seconds=timing.heartbeat_lost_after_seconds,
                idle_timeout_after_seconds=timing.idle_timeout_after_seconds,
            )
            if recheck.should_end:
                job_ids = state.core.end_session()
                with state.watchdog_lock:
                    state.session_ended = True
                logger.info(
                    "見回り: %s によりセッション終了処理（新規宿題%d件）",
                    recheck.reason, len(job_ids),
                )

    with state.watchdog_lock:
        la, ended = state.last_activity_at, state.session_ended
    if not should_digest(now=now, last_activity_at=la, session_ended=ended, digest_gap_seconds=timing.idle_digest_gap_seconds):
        return
    if is_gpu_busy(timing.gpu_busy_threshold_percent):
        return
    if not state.turn_lock.acquire(blocking=False):
        return
    try:
        if state.core.chore_box.count(kind="蒸留") > 0:
            summary = run_idle_digest_chunk(
                state.core.chore_box,
                memory_store=state.core.memory_store,
                thresholds=state.core.thresholds,
                lane_call_fns=state.lane_call_fns,
                limit=timing.idle_digest_chunk_limit,
            )
            if summary.processed:
                logger.info("見回り: アイドル小分け消化で記憶化%d件", summary.total_accepted)
            return
        # §4.6-3: 蒸留ジョブが無い時だけ、既存記憶の機微査定を優先度を落として回す
        # （Auroraのアイドル仕事。会話の記憶化が常に優先される）。
        local_call_fn = state.lane_call_fns.get("local")
        if local_call_fn is None:
            return
        assessment_summary = run_idle_assessment_chunk(
            state.core.memory_store,
            call_fn=local_call_fn,
            routing_rules=state.core.routing_rules,
            change_log=state.change_log,
            limit=timing.idle_digest_chunk_limit,
        )
        if assessment_summary.processed:
            logger.info("見回り: アイドル機微査定で%d件を査定", assessment_summary.total_assessed)
    finally:
        state.turn_lock.release()

    _maybe_generate_diary(state, timing, now=now)


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
            logger.info("見回り: 日記を生成しました（書き手=%s）", outcome.lane)
        else:
            logger.info("見回り: 日記生成を見送り（理由=%s）", outcome.reason)
    finally:
        state.turn_lock.release()


def main() -> None:
    global STATE
    import uvicorn

    print("Serina GUI を起動しています…（Ollama が必要。Gemini未設定ならAuroraのみで稼働）")

    gemini_api_key = get_gemini_api_key_or_none()
    if not gemini_api_key:
        print("（GEMINI_API_KEY未設定。クラウド車線は使わずローカル(Aurora)のみで稼働します）")

    core = create_core_v2(gemini_api_key=gemini_api_key)

    # §2.4トリガー3(次回起動時の朝礼): 前回のやり残し(pending)を新しい日の残弾で消化する。
    # 積み残しが多いとAurora実発注でここが数分かかりうる（起動直後・GUIオープン前）。
    startup_summary = run_startup_chores(
        core.chore_box,
        memory_store=core.memory_store,
        thresholds=core.thresholds,
        lane_call_fns=build_default_lane_call_fns(gemini_api_key),
    )
    if startup_summary.processed or startup_summary.failed:
        print(
            f"（前回までの積み残しを消化: 記憶化{startup_summary.total_accepted}件・"
            f"失敗{len(startup_summary.failed)}件はpending維持）"
        )

    # セッションID・会話履歴の帳簿は旧MemoryStore+SessionManagerをそのまま流用（§2.6参照）
    session_store = MemoryStore(OllamaEmbedder())
    session_config = CoreConfig()
    orphans = session_store.backfill_orphan_sessions()
    if orphans:
        print(f"（過去の未整理セッション {len(orphans)} 件を蒸留待ちに登録しました）")
    session_mgr = SessionManager(session_store, session_config)
    session_id, pending_id = session_mgr.resolve_active_session()
    if pending_id:
        print(f"（前回セッション {pending_id} を蒸留待ち[pending]にしました）")

    STATE = GuiState(core, session_store, session_mgr, session_id, gemini_api_key=gemini_api_key)

    timing = load_app_timing()
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
