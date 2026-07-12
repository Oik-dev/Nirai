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
from serina.core_v2.chores.idle_policy import (
    decide_session_end,
    should_digest,
    should_generate_diary,
    should_generate_diary_at_startup,
)
from serina.core_v2.chores.orchestrator import (
    build_cloud_quota_spec,
    build_default_lane_call_fns,
    run_diary_generation,
    run_idle_assessment_chunk,
    run_idle_digest_chunk,
    run_startup_chores,
)
from serina.core_v2.env import get_gemini_api_key
from serina.core_v2.factory import create_core_v2
from serina.core_v2.memory.protection import DEFAULT_CHANGE_LOG_PATH, ChangeLog
from serina.core_v2.state.diary_state import DEFAULT_DIARY_STATE_PATH, load_diary_state, save_diary_state
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
        # 2026-07-12追加: 裏方便(蒸留・日記)のクラウド発注が参照する残弾台帳の対象Brain。
        # 会話用の一次Brainと同一の"余り弾"を共有する（DECISIONS参照）。
        self.cloud_quota = build_cloud_quota_spec(core.registry) if core.registry else None

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


def get_gemini_api_key_or_none() -> str | None:
    try:
        return get_gemini_api_key()
    except RuntimeError:
        return None


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
        "shelved": state.core.chore_box.shelved_count() + state.core.chore_box.shelved_assessment_count(),
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
    """日記アルバム。旧アーキ由来の日記（`legacy/`移行分）と§4.5で新規生成される日記
    （type="diary"）を新しい順に返す。

    `session_store`（旧`serina.memory.store.MemoryStore`）と`core.memory_store`
    （`serina.core_v2.memory.store.MemoryStore`）は別クラスだが、どちらもデフォルトで
    同じ物理DB（`data/serina_memory.db`）の同じ`memories`テーブルに接続する
    （Phase2のスキーマ移行で旧`legacy/`日記も同テーブルへ統合済みのため）。
    両方に問い合わせると同一行が2回返るため、`core.memory_store`側のみを問い合わせる
    （2026-07-12実機確認で二重表示を発見・修正。DECISIONS参照）。
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

    with state.watchdog_lock:
        la, ended = state.last_activity_at, state.session_ended
    if not should_digest(now=now, last_activity_at=la, session_ended=ended, digest_gap_seconds=timing.idle_digest_gap_seconds):
        return
    if is_gpu_busy(timing.gpu_busy_threshold_percent):
        logger.info("見回り: GPU使用率が閾値%.0f%%を超えたため裏方便の発注を見送り", timing.gpu_busy_threshold_percent)
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
                quota_ledger=state.core.quota_ledger,
                cloud_quota=state.cloud_quota,
                change_log=state.change_log,
                failure_shelve_threshold=timing.chore_failure_shelve_threshold,
            )
            if summary.processed:
                logger.info("見回り: アイドル小分け消化で記憶化%d件", summary.total_accepted)
            if summary.lane_switched:
                logger.info("見回り: 蒸留ジョブ%d件をlocal車線へ振替", len(summary.lane_switched))
            if summary.shelved:
                logger.info(
                    "見回り: 蒸留ジョブ%d件を棚上げ（棚上げ棚の累計%d件）",
                    len(summary.shelved), state.core.chore_box.shelved_count(),
                )
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
            chore_box=state.core.chore_box,
            failure_shelve_threshold=timing.chore_failure_shelve_threshold,
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
            quota_ledger=state.core.quota_ledger,
            cloud_quota=state.cloud_quota,
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

    print("Serina GUI を起動しています…（Ollama が必要。Gemini未設定ならAuroraのみで稼働）")

    gemini_api_key = get_gemini_api_key_or_none()
    if not gemini_api_key:
        print("（GEMINI_API_KEY未設定。クラウド車線は使わずローカル(Aurora)のみで稼働します）")

    core = create_core_v2(gemini_api_key=gemini_api_key)
    timing = load_app_timing()
    startup_change_log = ChangeLog(DEFAULT_CHANGE_LOG_PATH)
    startup_cloud_quota = build_cloud_quota_spec(core.registry) if core.registry else None

    # §2.4トリガー3(次回起動時の朝礼): 前回のやり残し(pending)を新しい日の残弾で消化する。
    # 積み残しが多いとAurora実発注でここが数分かかりうる（起動直後・GUIオープン前）。
    startup_summary = run_startup_chores(
        core.chore_box,
        memory_store=core.memory_store,
        thresholds=core.thresholds,
        lane_call_fns=build_default_lane_call_fns(gemini_api_key),
        quota_ledger=core.quota_ledger,
        cloud_quota=startup_cloud_quota,
        change_log=startup_change_log,
        failure_shelve_threshold=timing.chore_failure_shelve_threshold,
    )
    if startup_summary.processed or startup_summary.failed:
        print(
            f"（前回までの積み残しを消化: 記憶化{startup_summary.total_accepted}件・"
            f"失敗{len(startup_summary.failed)}件はpending維持）"
        )
    if startup_summary.shelved:
        print(f"（処理できなかった宿題{len(startup_summary.shelved)}件を棚上げしました）")

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

    # §4.5①朝礼(主経路、2026-07-12改訂): 最後に日記を書いた日が前日以前なら、
    # 前回日記以降の材料で1本書く。「夜に会話→電源断」運用でも翌朝ここで必ず回収される
    # （last_diary_at・気分の軌跡はGuiState初期化時に既に永続状態から復元済み）。
    if should_generate_diary_at_startup(now=datetime.now(timezone.utc), last_diary_at=STATE.last_diary_at):
        diary_outcome = run_diary_generation(
            core,
            since_iso=STATE.last_diary_at.isoformat(),
            routing_rules=core.routing_rules,
            lane_call_fns=STATE.lane_call_fns,
            change_log=STATE.change_log,
            quota_ledger=core.quota_ledger,
            cloud_quota=STATE.cloud_quota,
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
