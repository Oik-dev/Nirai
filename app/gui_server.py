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

from serina.app.idle_config import AppTimingConfig, load_app_timing
from serina.core import debug_log
from serina.core.chores.gpu_guard import is_gpu_busy
from serina.core.chores.idle_policy import (
    decide_pulse,
    should_generate_diary_at_startup,
)
from serina.core.chores.pulse_state import (
    DEFAULT_PULSE_STATE_PATH,
    load_pulse_state,
    record_pulse_fire,
    save_pulse_state,
)
from serina.core.chores.schedule_pulse_state import (
    DEFAULT_SCHEDULE_PULSE_STATE_PATH,
    load_schedule_pulse_state,
    record_window_fire,
    save_schedule_pulse_state,
)
from serina.core.chores.orchestrator import (
    build_default_lane_call_fns,
    run_diary_generation,
    run_growth_chores,
    run_post_turn_summaries,
    run_startup_chores,
)
from serina.core.chores.summaries import DEFAULT_BLOCKS_PATH
from serina.core.factory import DEFAULT_MEMORY_DB_PATH, create_core
from serina.core.memory.diary_cascade import (
    collect_diary_material_targets,
    collect_legacy_chunk_children,
)
from serina.core.memory.directed_forget import confirm_forget
from serina.core.memory.memory_edit import edit_memory
from serina.core.memory.message_delete import (
    MASTER_DELETE_REASON as MESSAGE_DELETE_REASON,
    delete_message_with_effects,
    purge_effects_for_session_rows,
)
from serina.core.memory.protection import (
    DEFAULT_CHANGE_LOG_PATH,
    DEFAULT_GENERATION_STORE_PATH,
    ChangeLog,
    ChangeReport,
    GenerationStore,
    ProtectionError,
)
from serina.core.memory.session_store import SessionStore
from serina.core.state.episodic_state import (
    DEFAULT_EPISODIC_STATE_PATH,
    load_episodic_state,
    save_episodic_state,
)
from serina.core.state.serina_boundary_state import (
    DEFAULT_SERINA_BOUNDARY_STATE_PATH,
    load_serina_boundary_state,
    save_serina_boundary_state,
)
from serina.core.state.serina_day import (
    serina_day_id,
    serina_day_start,
    should_run_day_boundary,
)
from serina.core.state.emotion_persist import (
    DEFAULT_EMOTION_STATE_PATH,
    apply_loaded_to_emotion,
    load_emotion_state,
    save_emotion_from_state,
)
from serina.core.state.desire_persist import (
    DEFAULT_DESIRE_STATE_PATH,
    apply_loaded_to_desire,
    load_desire_state,
    save_desire_from_state,
)
from serina.core.state.persona_propose_state import (
    DEFAULT_PERSONA_PROPOSE_STATE_PATH,
    load_persona_propose_state,
    save_persona_propose_state,
)
from serina.core.state.relationship_persist import (
    DEFAULT_RELATIONSHIP_STATE_PATH,
    apply_loaded_to_relationship,
    load_relationship_state,
    save_relationship_from_state,
)
from serina.core.state.session_book import SessionBookConfig, SessionManager
from tools.backup_db import backup_db

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


def _migration_anchor_serina_day(
    loaded_emotion_data: dict,
    *,
    now: datetime,
    boundary_hour: int = 7,
) -> str:
    """2026-07-26 A3是正(serina-code-reviewer指摘I-3): 旧mood_trajectory（`_day`未タグ）
    へ一度だけ付与するSerina日を決める。

    起動時点(`now`)ではなく、最後にターンを処理した時刻(`emotion_state.json`の
    `last_tick_at`)を錨にする。`now`を使うと、キャッチアップで生成される過去日の
    日記の材料がこの軌跡ぶん空になり、未タグ分がまるごと「今日の日記」に混入して
    しまう（A3が解消しようとした出来事日と感情日のずれを、移行の1回だけ再現する）。
    `last_tick_at`が無い（初回起動等）場合のみ`now`にフォールバックする。

    `boundary_hour`は日記キャッチアップ（`timing.serina_day_boundary_hour`）と同値を
    渡すこと。既定7以外に変えたとき軌跡の`_day`と日記`target_date`がズレないようにする。
    """
    raw_last_tick_at = loaded_emotion_data.get("last_tick_at")
    anchor = datetime.fromisoformat(raw_last_tick_at) if raw_last_tick_at else now
    return serina_day_id(anchor, boundary_hour=boundary_hour).isoformat()


def _run_post_turn_summaries_async(state: "GuiState") -> None:
    """ターン確定後の fine/coarse 要約更新（失敗しても会話は返済済み）。

    2026-07-26 A4: 連続入力時に前回の要約スレッドが走行中でも新規スレッドが立ち、
    同一SessionStateのrolling_summary/summarized_turn_countを並行更新しうる
    （実害: 同じ範囲を二度要約し、その分のターンが要約から抜ける）ため、
    非ブロッキングLockで多重起動を防ぐ。取得できなければ何もせず戻る
    （次ターンで再挑戦されるため取りこぼさない）。
    """
    if not state.summary_lock.acquire(blocking=False):
        logger.debug("ターン後要約: 前回分が走行中のためスキップ")
        return
    try:
        lane_fns = getattr(state, "lane_call_fns", None) or {}
        call_fn = lane_fns.get("local") if isinstance(lane_fns, dict) else None
        if call_fn is None:
            return
        try:
            run_post_turn_summaries(state.core, call_fn=call_fn)
        except Exception:  # noqa: BLE001
            logger.exception("ターン後要約更新に失敗")
    finally:
        state.summary_lock.release()


class GuiState:
    """プロセス内で1つだけ持つ実行状態（単一ユーザー前提）。"""

    def __init__(
        self,
        core,
        session_store: SessionStore,
        session_mgr: SessionManager,
        session_id: str,
        *,
        serina_day_boundary_hour: int = 7,
    ) -> None:
        self.core = core
        # 日記キャッチアップ・軌跡タグ・移行錨で同じ日界を使う。
        self.core.serina_day_boundary_hour = serina_day_boundary_hour
        self.session_store = session_store
        self.session_mgr = session_mgr
        self.session_id = session_id
        self.turn_lock = threading.Lock()  # 多重送信は先行ターン完了まで待つ
        # 2026-07-26 A4: ターン後要約スレッドの二重起動防止（非ブロッキング取得）。
        self.summary_lock = threading.Lock()
        self.lane_call_fns = build_default_lane_call_fns()
        self.change_log = ChangeLog(DEFAULT_CHANGE_LOG_PATH)
        self.core.change_log = self.change_log
        self.generation_store = GenerationStore(DEFAULT_GENERATION_STORE_PATH)
        self.db_path = DEFAULT_MEMORY_DB_PATH
        self.life_dir = DEFAULT_LIFE_DIR
        self.summaries_path = DEFAULT_BLOCKS_PATH
        self.last_export_life_at: datetime | None = None

        # §2.4 セッション終了の定義・②アイドル時トリガー用の見張り状態。
        # 起動直後は「今まさに繋がった」とみなし、活動時刻を現在時刻で初期化する。
        now = datetime.now(timezone.utc)
        self.last_activity_at = now
        # 2026-08-01是正: 起動直後、まだマスターの最初の発言（実際の会話）が来ていない間は
        # 日界処理・Pulseを走らせない（起動後10分程度でセリナから話しかけてくる・待機中に
        # セッションが切り替わる、というマスター報告への対策）。最初のターンで True になる。
        self.has_had_first_turn = False
        self.session_ended = False
        self.watchdog_lock = threading.Lock()  # session_ended・タイムスタンプの読み書き保護
        # §4.5 夜間放出: since_iso=前回episodic記憶以降を当日材料とみなす（calendar日付演算を
        # 避ける設計。2026-07-12マスター承認）。last_episodic_atと気分の軌跡は電源断をまたいで
        # 永続化する（2026-07-12改訂。旧設計はプロセス内メモリのみで「夜に会話→電源断」
        # 運用ではepisodic記憶が一度も生成されない構造欠陥があった。DECISIONS参照）。
        self.episodic_state_path = DEFAULT_EPISODIC_STATE_PATH
        last_episodic_at, mood_trajectory = load_episodic_state(self.episodic_state_path)
        self.last_episodic_at = last_episodic_at
        self.serina_boundary_state_path = DEFAULT_SERINA_BOUNDARY_STATE_PATH
        self.last_boundary_serina_day = load_serina_boundary_state(self.serina_boundary_state_path)

        # §2.3 感情本体の永続（affect/mood/最終更新）。起動時にオフライン分を冷ます。
        self.emotion_state_path = DEFAULT_EMOTION_STATE_PATH
        loaded_emotion_data = load_emotion_state(self.emotion_state_path)
        apply_loaded_to_emotion(self.core.emotion, loaded_emotion_data)

        # Task 3-4: 欲求層の永続（level / 不応期 / last_tick）。感情と同タイミングでロード。
        self.desire_state_path = DEFAULT_DESIRE_STATE_PATH
        apply_loaded_to_desire(self.core.desire, load_desire_state(self.desire_state_path))

        # 2026-07-26 A3是正(serina-code-reviewer指摘I-3): 軌跡は毎スナップショットに
        # `_day`（Serina日タグ）を持つ設計へ移行。移行前に保存されたスナップショットは
        # `_day`キーを持たないため、未タグのまま残るとsummarize_trajectory(day=...)/
        # clear_trajectory(day=...)のどの日フィルタにも一致せず永久に集計・消去されなくなる。
        migration_serina_day = _migration_anchor_serina_day(
            loaded_emotion_data,
            now=now,
            boundary_hour=serina_day_boundary_hour,
        )
        for snapshot in mood_trajectory:
            if "_day" not in snapshot:
                snapshot["_day"] = migration_serina_day
        self.core.emotion.mood_trajectory = mood_trajectory

        self.core._cool_emotion(now)
        save_emotion_from_state(self.emotion_state_path, self.core.emotion)
        save_desire_from_state(self.desire_state_path, self.core.desire)

        # 2026-07-26 B1: 関係状態（マスター観測）の永続化。プロセス終了時に消えていた
        # recent_master_moodをまたいで復元する（§2.6・§1.5⑤末尾）。
        self.relationship_state_path = DEFAULT_RELATIONSHIP_STATE_PATH
        apply_loaded_to_relationship(
            self.core.relationship, load_relationship_state(self.relationship_state_path),
        )

        # Sleep 人格提案器: 1日1回の試行時刻（電源断耐性）
        self.persona_propose_state_path = DEFAULT_PERSONA_PROPOSE_STATE_PATH
        self.last_persona_propose_at = load_persona_propose_state(
            self.persona_propose_state_path,
        )

        # §2.8 Pulse: 発火履歴・mute・チャット欄へ載せるための新着キュー
        self.pulse_state_path = DEFAULT_PULSE_STATE_PATH
        self.schedule_pulse_state_path = DEFAULT_SCHEDULE_PULSE_STATE_PATH
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

    2026-07-20 応答高速化: Core.turn_routed の on_token/on_reply で返答本文を
    トークン単位ストリーミングする。イベント順序:
      token* → done(reply のみ・1通目確定) → [裏で感情抽出等]
      → notice?（別れの挨拶） → done(reply+session_id+citations・終幕)
    1通目確定後の抽出・記憶処理は同スレッドで続くため、HTTPストリームは終幕まで開いたまま。
    on_reply が発火しないBrain（callbacks非対応・空応答からの最終防衛線復帰）でも、
    終幕の done がフロントの一括表示フォールバックを駆動する。

    2026-07-31 Phase E: 旧「保留文→2通目」機構（followupイベント）は退役済み。
    Gemini/Tavily窓口の結果はConverse呼び出し前にCoreが確定させ、1通で返す
    （無言統合パイプライン。Phase D）。出典はcitations（終幕doneの付加フィールド）
    として届き、reply（記憶に残る発話本体）には混ざらない。
    """
    state = _state()
    # §2.4: 会話が来た＝生きている証拠。見回りスレッドの誤終了判定を防ぎ、
    # 前回アイドル終了していれば新しいセッションとして再開する。
    with state.watchdog_lock:
        state.last_activity_at = datetime.now(timezone.utc)
        state.session_ended = False
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
                    # 返答は届いている。裏方（抽出・記憶処理）の失敗で本文を上書きしない
                    events.put(_ev("notice", text="（裏の整理で少しつまずいたみたい。会話は続けられるよ）"))
                else:
                    events.put(_ev("error", text=FALLBACK_APOLOGY))
                return

            state.session_store.add_history(state.session_id, "user", text)
            state.session_store.add_history(state.session_id, "assistant", reply)

            # §4.5 気分の軌跡はターン境界でスナップショットを永続化する（EmotionState自体は
            # I/Oを持たないLLM無しコアのため、境界はアプリ層のここが担う。advisorレビュー
            # 2026-07-12: apply_mood_delta毎ではなくターン単位で十分）。
            save_episodic_state(
                state.episodic_state_path,
                last_episodic_at=state.last_episodic_at,
                mood_trajectory=state.core.emotion.mood_trajectory,
            )
            save_emotion_from_state(state.emotion_state_path, state.core.emotion)
            save_desire_from_state(state.desire_state_path, state.core.desire)
            # 2026-07-26 B1: 関係状態（マスター観測）も同じターン境界で永続化する。
            save_relationship_from_state(state.relationship_state_path, state.core.relationship)

            threading.Thread(
                target=_run_post_turn_summaries_async,
                args=(state,),
                daemon=True,
            ).start()

            # 2026-07-31 Phase E: Tavily出典（citations）はreply（記憶に残る発話本体）とは
            # 別経路でGUIへ届ける。画面上はreplyの下に注記として小さく表示する想定
            # （見た目の細部はフロント実装判断。契約は「記憶書き込み経路に混ぜない」のみ）。
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
    box = state.core.chore_box
    from serina.core.chores.persona_revise import PERSONA_REVISE_CHORE_KIND

    return {
        "session_id": state.session_id,
        "pending": box.count(kind="蒸留"),
        "pending_persona_revise": box.count(kind=PERSONA_REVISE_CHORE_KIND),
        # 2026-07-12追加: 毒饅頭ジョブの棚上げ棚（原則1: 無言破棄禁止のGUI表示。DECISIONS参照）
        "shelved": box.shelved_count(),
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


@app.get("/api/eval/report")
def api_eval_report():
    """最新の週次評価レポート（無ければ null）。"""
    from serina.core.eval_report import (
        build_claude_copy_text,
        load_eval_ack,
        load_eval_report,
        report_needs_attention,
    )

    report = load_eval_report()
    ack = load_eval_ack()
    if report is None:
        return {
            "report": None,
            "needs_attention": False,
            "claude_copy": "",
        }
    return {
        "report": report,
        "needs_attention": report_needs_attention(report, ack),
        "claude_copy": build_claude_copy_text(report),
    }


@app.post("/api/eval/ack")
def api_eval_ack():
    """レポートを確認済みにする（バッジ消去）。"""
    from serina.core.eval_report import load_eval_report, save_eval_ack

    report = load_eval_report()
    if report is None or not report.get("ran_at"):
        return {"ok": False, "reason": "レポートがありません"}
    save_eval_ack(str(report["ran_at"]))
    return {"ok": True, "acked_ran_at": report["ran_at"]}


MASTER_DELETE_REASON = "マスター手動（GUIメンテ削除・物理削除）"
MASTER_DELETE_CHUNK_REASON = MASTER_DELETE_REASON + "（レガシー日記チャンクの連鎖削除）"


def _require_master_confirm(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true が必要です")


def _resync_episodic_state_after_delete(state: GuiState) -> None:
    """episodic記憶を消したあと、last_episodic_at を残件に合わせる（§4.5夜間放出の水位）。

    2026-08-01是正: 水位は後退させない。削除は「もう要らない」という意思表示であり
    「書き直してほしい」ではないため、現在値と再同期候補の新しい方を採用する（max）。
    旧実装は無条件で残存最新へ同期しており、最新のepisodicを消すと水位が過去へ巻き戻り、
    削除した日を含む複数日が翌日以降の日界処理で無言のうちに再生成される事故が実際に
    起きていた（2026-07-23に日記を削除→2026-08-01に2025年3月末〜4月上旬・7/23・7/31の
    計6日分が一斉に再生成。削除した日の日記も別内容で復活した。マスター報告により発覚）。
    """
    remaining = state.core.memory_store.list_by_type("episodic", limit=1)
    candidate = (
        datetime.fromisoformat(remaining[0].created_at)
        if remaining
        else datetime.now(timezone.utc)
    )
    # 2026-08-01是正(serina-code-reviewer指摘M-1): レガシー投入分にtzオフセット無しの
    # created_atが混じっていると candidate が naive になり、aware な state.last_episodic_at
    # とのmax()比較でTypeErrorになる（削除APIが500を返す）。naiveはUTCとみなして補う。
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    last_at = max(state.last_episodic_at, candidate)
    state.last_episodic_at = last_at
    save_episodic_state(
        state.episodic_state_path,
        last_episodic_at=last_at,
        mood_trajectory=list(state.core.emotion.mood_trajectory),
    )


class MemoryEditRequest(BaseModel):
    content: str | None = None
    protection_grade: str | None = None


@app.get("/api/memories")
def api_memories_list(
    q: str = "", type: str = "", sort: str = "date", dir: str = "desc",
    limit: int = 100, page: int = 1,
):
    """メンテ用記憶一覧。type=episodic/semanticで絞り込み・列見出しでソート可。"""
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit は1以上")
    if page < 1:
        raise HTTPException(status_code=400, detail="page は1以上")
    state = _state()
    offset = (page - 1) * limit
    try:
        items, total = state.core.memory_store.list_memories_for_maint(
            q=q, type=type, sort=sort, dir=dir, limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pages = (total + limit - 1) // limit if total else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
    }


@app.get("/api/memories/{memory_id}/diary_material")
def api_memories_diary_material(memory_id: int):
    """日記(episodic)が参照している材料記憶を読み取り専用で列挙する（削除はしない）。

    §4.5材料窓内の本番蒸留（source空・B級・非pinned）のみ。原典・S/A・pinnedは出さない。
    マスターがここで見て判断し、消す場合は個別に DELETE /api/memories/{id} を呼ぶ。
    """
    state = _state()
    pair = state.core.memory_store.get_memory_by_id(memory_id)
    if pair is None:
        raise HTTPException(status_code=404, detail=f"記憶 id={memory_id} が見つからない")
    record, _pinned = pair
    if record.type != "episodic":
        raise HTTPException(status_code=400, detail="参照記憶の列挙は type=episodic のみ")
    targets = collect_diary_material_targets(state.core.memory_store, record)
    return [
        {
            "id": t.id,
            "type": t.type,
            "content": t.content,
            "protection_grade": t.protection_grade,
            "created_at": t.created_at,
            "pinned": False,
        }
        for t in targets
    ]


@app.patch("/api/memories/{memory_id}")
def api_memories_edit(memory_id: int, req: MemoryEditRequest, confirm: bool = False):
    """記憶の本文・保護等級を編集する（マスター確認必須）。

    pinned（正典固定）は禁止。等級S絡みの編集もGUI確認ダイアログ通過をもって許可する。
    """
    _require_master_confirm(confirm)
    state = _state()
    backup_db(state.db_path)
    try:
        edit_memory(
            state.core.memory_store,
            memory_id=memory_id,
            new_content=req.content,
            new_protection_grade=req.protection_grade,
            change_log=state.change_log,
            generation_store=state.generation_store,
            skip_backup=True,
        )
    except (ProtectionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pair = state.core.memory_store.get_memory_by_id(memory_id)
    if pair is None:
        raise HTTPException(status_code=404, detail=f"記憶 id={memory_id} が見つからない")
    record, _pinned = pair
    return {
        "ok": True,
        "memory_id": memory_id,
        "type": record.type,
        "content": record.content,
        "protection_grade": record.protection_grade,
    }


@app.delete("/api/memories/{memory_id}")
def api_memories_delete(memory_id: int, confirm: bool = False):
    """記憶1件を物理削除する（type問わず・マスター確認必須）。

    固定9件（pinned）は confirm_forget 側で ProtectionError。
    保護等級Sは GUI 確認ダイアログ通過をもって master_confirmed_s とみなす。
    type=episodicのときは、レガシー投入時代の検索用チャンク（`parent_id`で当該行を参照する
    同一内容の残骸）を自動で連鎖物理削除し、last_episodic_atを残件へ再同期する
    （2026-07-23: アルバム廃止に伴い記憶メンテへ統合。マスター判断は挟まない＝
    別内容の材料ではなく同一行の残留物のため）。
    """
    _require_master_confirm(confirm)
    state = _state()
    pair = state.core.memory_store.get_memory_by_id(memory_id)
    if pair is None:
        raise HTTPException(status_code=404, detail=f"記憶 id={memory_id} が見つからない")
    record, _pinned = pair
    chunk_children = (
        collect_legacy_chunk_children(state.core.memory_store, memory_id)
        if record.type == "episodic"
        else []
    )
    backup_db(state.db_path)
    cascade_deleted: list[int] = []
    try:
        for child in chunk_children:
            confirm_forget(
                state.core.memory_store,
                memory_id=child.id,
                physical_delete=True,
                master_confirmed_s=child.protection_grade == "S",
                reason=MASTER_DELETE_CHUNK_REASON,
                change_log=state.change_log,
                generation_store=state.generation_store,
                skip_backup=True,
                chore_box=state.core.chore_box,
            )
            cascade_deleted.append(child.id)
        confirm_forget(
            state.core.memory_store,
            memory_id=memory_id,
            physical_delete=True,
            master_confirmed_s=record.protection_grade == "S",
            reason=MASTER_DELETE_REASON,
            change_log=state.change_log,
            generation_store=state.generation_store,
            skip_backup=True,
            chore_box=state.core.chore_box,
        )
    except ProtectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record.type == "episodic":
        _resync_episodic_state_after_delete(state)
    return {"ok": True, "memory_id": memory_id, "cascade_deleted": cascade_deleted}


@app.post("/api/sessions/new")
def api_sessions_new(confirm: bool = False):
    """現行セッションをアーカイブし新セッションへ切替（蒸留消化なし・端数 flush のみ）。"""
    _require_master_confirm(confirm)
    state = _state()
    with state.turn_lock:
        state.core.end_session()
        with state.watchdog_lock:
            state.session_ended = False
        if state.session_mgr is not None:
            state.session_id = state.session_mgr.rotate(
                state.session_id, now=datetime.now(timezone.utc),
            )
    return {"ok": True, "session_id": state.session_id}


@app.delete("/api/messages/{message_id}")
def api_message_delete(message_id: int, confirm: bool = False):
    """発言1件を会話帳簿から削除（マスター確認必須・§4.8.1）。"""
    _require_master_confirm(confirm)
    state = _state()
    backup_db(state.db_path)
    try:
        with state.turn_lock:
            outcome = delete_message_with_effects(
                session_store=state.session_store,
                memory_store=state.core.memory_store,
                chore_box=state.core.chore_box,
                session=state.core.session,
                message_id=message_id,
                current_session_id=state.session_id,
                change_log=state.change_log,
                generation_store=state.generation_store,
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    before_lines = [
        f"message_id={message_id}",
        f"session={outcome.session_id}",
        f"role={outcome.role}",
        f"content={outcome.content_preview}",
    ]
    if outcome.chore_jobs_removed:
        before_lines.append(f"chore_removed={outcome.chore_jobs_removed}")
    if outcome.memories_deleted:
        before_lines.append(f"memories_deleted={outcome.memories_deleted}")
    if outcome.memories_quote_trimmed:
        before_lines.append(f"memories_quote_trimmed={outcome.memories_quote_trimmed}")
    if outcome.diary_trace_notes:
        before_lines.extend(outcome.diary_trace_notes)
    if outcome.notes:
        before_lines.extend(outcome.notes)

    state.change_log.record(
        ChangeReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action="指示忘却（発言単位削除）",
            target_id=message_id,
            reason=MESSAGE_DELETE_REASON,
            before="\n".join(before_lines),
            after=None,
        )
    )
    return {
        "ok": True,
        "message_id": message_id,
        "session_id": outcome.session_id,
        "chore_jobs_removed": outcome.chore_jobs_removed,
        "memories_deleted": outcome.memories_deleted,
        "memories_quote_trimmed": outcome.memories_quote_trimmed,
        "diary_trace_notes": outcome.diary_trace_notes,
        "notes": outcome.notes,
    }


@app.delete("/api/sessions/{session_id}")
def api_session_delete(session_id: str, confirm: bool = False):
    """過去セッションの会話帳簿を物理削除する（マスター確認必須）。

    現行 active セッションは拒否。2026-07-23: 発言1件削除と同じ副作用として
    `purge_effects_for_session_rows` を通すため、このセッションの発言と完全一致する
    引用元を持つ蒸留済み grade-B 正典 memories は物理削除・引用整理の対象になる
    （`reconcile_distilled_memories`）。pinned・grade S/A・source ありの記憶は対象外。
    """
    _require_master_confirm(confirm)
    state = _state()
    if session_id == state.session_id:
        raise HTTPException(status_code=400, detail="今日の会話（現行セッション）は削除できない")
    # 全文はここで確保しておく（delete_session の preview は120文字切り詰めのため、
    # 宿題・記憶引用との完全一致照合には使えない）
    rows = (
        state.session_store.get_session_history(session_id)
        or state.session_store.get_archived_history(session_id)
    )
    # 破壊前バックアップ（指示忘却と同じ可逆性。宿題台帳は対象外＝マスター判断2026-07-23）
    backup_db(state.db_path)
    result = state.session_store.delete_session(session_id)
    if result["session_deleted"] == 0 and result["history_deleted"] == 0 and result["archived_deleted"] == 0:
        raise HTTPException(status_code=404, detail=f"セッション {session_id} が見つからない")
    # テスト会話→セッション削除で「会話していなかった」に近い状態へ戻すためのマスター要望
    # （2026-07-23）: 発言1件削除と同じ副作用（宿題除去・記憶引用整理・日記材料痕跡）を
    # セッション内の全発言に対してまとめて適用する。
    purge_outcome = purge_effects_for_session_rows(
        rows,
        chore_box=state.core.chore_box,
        memory_store=state.core.memory_store,
        change_log=state.change_log,
        generation_store=state.generation_store,
    )
    before_lines = [f"session={session_id}", result["preview"]]
    if purge_outcome.chore_jobs_removed:
        before_lines.append(f"chore_removed={purge_outcome.chore_jobs_removed}")
    if purge_outcome.memories_deleted:
        before_lines.append(f"memories_deleted={purge_outcome.memories_deleted}")
    if purge_outcome.memories_quote_trimmed:
        before_lines.append(f"memories_quote_trimmed={purge_outcome.memories_quote_trimmed}")
    if purge_outcome.diary_trace_notes:
        before_lines.extend(purge_outcome.diary_trace_notes)
    if purge_outcome.notes:
        before_lines.extend(purge_outcome.notes)
    state.change_log.record(
        ChangeReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action="指示忘却（会話セッション物理削除）",
            target_id=0,
            reason=MASTER_DELETE_REASON,
            before="\n".join(before_lines),
            after=None,
        )
    )
    return {
        "ok": True,
        **result,
        "chore_jobs_removed": purge_outcome.chore_jobs_removed,
        "memories_deleted": purge_outcome.memories_deleted,
        "memories_quote_trimmed": purge_outcome.memories_quote_trimmed,
        "diary_trace_notes": purge_outcome.diary_trace_notes,
        "notes": purge_outcome.notes,
    }


# 静的ファイル（/api より後に mount するので API が優先される）
app.mount("/", NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="web")


def _idle_watchdog(state: GuiState, timing: AppTimingConfig) -> None:
    """見回りスレッド。Pulse と Serina 日界処理を駆動する。"""
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
    gpu_busy = is_gpu_busy(timing.gpu_busy_threshold_percent)
    if not gpu_busy:
        _maybe_fire_pulse(state, now=now)
        _maybe_run_serina_day_boundary(state, timing, now=now)


def _run_pending_diaries_for_serina_days(
    state: GuiState,
    timing: AppTimingConfig,
    *,
    now: datetime,
    max_count: int | None = None,
) -> int:
    """未処理の Serina 日ごとに日記を1本ずつ生成する。生成件数を返す。"""
    boundary_hour = timing.serina_day_boundary_hour
    generated = 0
    while True:
        with state.watchdog_lock:
            last_episodic_at = state.last_episodic_at
        if serina_day_id(last_episodic_at, boundary_hour=boundary_hour) >= serina_day_id(
            now, boundary_hour=boundary_hour,
        ):
            break
        target_day = serina_day_id(last_episodic_at, boundary_hour=boundary_hour)
        # 2026-07-26 A3: 気分の軌跡はSerina日ごとに独立して蓄積・消費される
        # （EmotionState._day タグ）ため、キャッチアップの何日目でも上限で持ち越されても
        # 別日の軌跡と混ざらない。旧is_most_recent_pending_day分岐（I-2/I-4後追い対処）は
        # 不要になったため削除した。
        if max_count is not None and generated >= max_count:
            logger.info(
                "日記キャッチアップ: 上限%d件に到達、残りは次回の日界（約24時間後）または"
                "次回起動の朝礼へ持ち越し", max_count,
            )
            break
        day_end = serina_day_start(target_day + timedelta(days=1), boundary_hour=boundary_hour)
        # このイテレーションで材料にしてよいのは target_day 分だけ（§4.5 Serina日ごとに1本）。
        # day_end が now を超えることは無い（day_end <= now のときだけこのループへ入るため）。
        outcome = run_diary_generation(
            state.core,
            since_iso=last_episodic_at.isoformat(),
            until_iso=day_end.isoformat(),
            target_date=target_day.isoformat(),
            created_at=day_end.isoformat(),
            routing_rules=state.core.routing_rules,
            lane_call_fns=state.lane_call_fns,
            change_log=state.change_log,
        )
        if outcome.generated:
            with state.watchdog_lock:
                state.last_episodic_at = day_end
            save_episodic_state(
                state.episodic_state_path,
                last_episodic_at=day_end,
                mood_trajectory=state.core.emotion.mood_trajectory,
            )
            generated += 1
            logger.info("日記を生成しました（書き手=%s）", outcome.lane)
        elif outcome.reason == "材料なし":
            empty_day = serina_day_id(last_episodic_at, boundary_hour=boundary_hour)
            # 2026-07-31是正(completion-review C-1): 「材料なし」は「本当に会話が無かった日」
            # と「蒸留がまだ未消化なだけ」を区別できない。宿題箱に未消化の蒸留ジョブが残って
            # いる状態で窓を前進させると、docs/設計書.md §4.5「材料窓は成功時のみ前進」に反する
            # うえ、後で蒸留が完了した記憶（created_atは発話時刻＝この日）がどの日記の材料窓にも
            # 入らず永久に脱落する（created_atを発話時刻で刻むようにした本修正の副作用）。
            # 未消化ジョブが残る限りは前進させず次回の日界へ持ち越す（透明性: 無言破棄しない）。
            # 壊れたジョブ（毒饅頭）はconsume側のfailure_shelve_threshold（既定3）で棚上げされ
            # count()の対象から外れるため、ここで永久停滞にはならない。
            chore_box = getattr(state.core, "chore_box", None)
            pending_distillation = chore_box.count(kind="蒸留") if chore_box is not None else 0
            if pending_distillation > 0:
                logger.warning(
                    "日記生成を見送り（材料なし・Serina日=%s）だが未消化の蒸留ジョブが%d件"
                    "残っているため材料窓を前進させず次回の日界へ持ち越し",
                    empty_day.isoformat(), pending_distillation,
                )
                break
            next_start = serina_day_start(
                empty_day + timedelta(days=1), boundary_hour=boundary_hour,
            )
            with state.watchdog_lock:
                state.last_episodic_at = next_start
            save_episodic_state(
                state.episodic_state_path,
                last_episodic_at=next_start,
                mood_trajectory=state.core.emotion.mood_trajectory,
            )
            logger.debug("日記生成を見送り（材料なし・Serina日=%s）", empty_day.isoformat())
        else:
            logger.info("日記生成を見送り（理由=%s）", outcome.reason)
            break
    return generated


def _run_growth_chores_for_state(state: "GuiState", timing: AppTimingConfig, *, now: datetime) -> None:
    """persona改訂 / Sleep提案 / life/ を日界・朝礼で消化する。"""
    outcome = run_growth_chores(
        state.core,
        state.core.chore_box,
        memory_store=state.core.memory_store,
        thresholds=state.core.thresholds,
        lane_call_fns=getattr(state, "lane_call_fns", None) or {},
        change_log=state.change_log,
        generation_store=getattr(state, "generation_store", None),
        db_path=getattr(state, "db_path", None),
        life_dir=getattr(state, "life_dir", None),
        summaries_path=getattr(state, "summaries_path", None),
        export_min_interval_seconds=timing.export_life_min_interval_seconds,
        last_export_life_at=getattr(state, "last_export_life_at", None),
        last_persona_propose_at=getattr(state, "last_persona_propose_at", None),
        now=now,
    )
    if outcome.last_persona_propose_at is not None:
        state.last_persona_propose_at = outcome.last_persona_propose_at
        path = getattr(state, "persona_propose_state_path", None)
        if path is not None:
            save_persona_propose_state(
                path,
                last_propose_at=outcome.last_persona_propose_at,
            )
    if outcome.last_export_life_at is not None:
        state.last_export_life_at = outcome.last_export_life_at
    if outcome.persona_revise_runs or outcome.persona_propose_ran or outcome.export_life_ran:
        logger.info(
            "成長系裏方: persona改訂%d / Sleep提案=%s / life=%s",
            outcome.persona_revise_runs,
            outcome.persona_propose_ran,
            outcome.export_life_ran,
        )


def _maybe_run_serina_day_boundary(
    state: GuiState,
    timing: AppTimingConfig,
    *,
    now: datetime,
) -> None:
    """§2.4 Serina 日界: 蒸留消化 → 成長系裏方 → 日記 → セッション切替。"""
    try:
        _maybe_run_serina_day_boundary_inner(state, timing, now=now)
    except Exception:  # noqa: BLE001
        logger.exception("見回り: Serina 日界処理に失敗")


def _maybe_run_serina_day_boundary_inner(
    state: GuiState,
    timing: AppTimingConfig,
    *,
    now: datetime,
) -> None:
    # 2026-08-01是正: マスターの最初の発言（実際の会話）がまだ来ていない間は、
    # 日界処理（蒸留消化・成長系裏方・日記・セッション切替）を走らせない。
    # 起動直後にセリナから話しかけてくる／マスターが返信を待っている間に
    # セッションが切り替わる、というマスター報告への対策。
    # 2026-08-01是正(serina-code-reviewer指摘I-5): getattrのデフォルト値に頼ると、
    # 将来GuiStateの初期化経路が増えて設定漏れが起きた場合、日界処理が無言で
    # 恒久的に止まる（今回是正した「無言の抑制」と同じ失敗の形になる）。
    # 直接参照にして、欠落時はAttributeErrorで気づけるようにする。
    if not state.has_had_first_turn:
        return

    with state.watchdog_lock:
        last_activity = state.last_activity_at
        last_boundary = state.last_boundary_serina_day

    if not should_run_day_boundary(
        now=now,
        last_activity_at=last_activity,
        last_boundary_serina_day=last_boundary,
        grace_seconds=timing.serina_day_grace_after_activity_seconds,
        boundary_hour=timing.serina_day_boundary_hour,
    ):
        return

    if not state.turn_lock.acquire(blocking=False):
        return

    try:
        with state.watchdog_lock:
            last_activity = state.last_activity_at
            last_boundary = state.last_boundary_serina_day

        if not should_run_day_boundary(
            now=now,
            last_activity_at=last_activity,
            last_boundary_serina_day=last_boundary,
            grace_seconds=timing.serina_day_grace_after_activity_seconds,
            boundary_hour=timing.serina_day_boundary_hour,
        ):
            return

        # 2026-07-23是正: 起動時朝礼(main())は元々フェーズごとに独立した
        # try/exceptだったが、見回り側はここ一箇所に全フェーズをまとめていたため、
        # いずれか1フェーズの例外で後続（日記・セッション切替・日界マーキング）が
        # 丸ごと止まり、かつ last_boundary_serina_day が更新されないまま次の
        # tick でも should_run_day_boundary が真になり続け、失敗フェーズを
        # 何度もやり直す無限リトライになっていた（実機ログで確認）。
        # 起動時朝礼と同じ「宿題は消えず次回の朝礼／日界で回収する」設計
        # （設計書§2.4）に合わせ、各フェーズを個別に隔離する。
        #
        # 2026-07-31是正(completion-review I-1): end_session()（端数flush→宿題箱へ積む）を
        # 蒸留消化フェーズより前に持ってくる。旧順序（蒸留消化→…→end_session）だと、器に
        # 満たない端数ターンはこの日界の蒸留消化に一切間に合わず、次の日界まで持ち越される。
        # その頃には last_episodic_at が既に day_end(当日) へ前進済みのため、端数由来の
        # 記憶（発話時刻＝当日）はどの日記の材料窓 [since, day_end) にも入らず永久に脱落する
        # （2026-07-31是正で記憶のcreated_atを発話時刻に固定した副作用。処理時刻のままなら
        # 「1日ズレて出る」で済んでいたが、発話時刻固定後は「一切出ない」に悪化していた）。
        # 先に積んでおけば同じ日界の蒸留消化フェーズで拾われ、日記キャッチアップにも間に合う。
        # 他フェーズと同じく独立したtry/exceptで隔離し、失敗時は次回の日界へ持ち越す
        # （無限リトライ再発防止。architecture-reviewer 2026-07-31懸念への対応）。
        #
        # architecture-reviewer 2026-07-31実施結果: 総合評価PASS（憲章違反なし）。ただし
        # 「end_session()の順序変更だけでは、蒸留された記憶のcreated_atが処理時刻のままである
        # 限り事故は再発する。真因は記憶の日付帰属方式」という懸念が示された。この懸念を受けて
        # 記憶のcreated_atを発話時刻(Turn.ts)で刻む本体修正（core/intake/memory_review.py等）
        # を先に実装した上で、本順序変更を組み合わせている（completion-review I-D対応）。
        try:
            state.core.end_session()
            with state.watchdog_lock:
                state.session_ended = False
        except Exception:  # noqa: BLE001
            logger.exception("見回り: 日界のセッション締め（端数flush）に失敗")

        try:
            startup_summary = run_startup_chores(
                state.core.chore_box,
                memory_store=state.core.memory_store,
                thresholds=state.core.thresholds,
                lane_call_fns=state.lane_call_fns,
                change_log=state.change_log,
                failure_shelve_threshold=timing.chore_failure_shelve_threshold,
            )
            if startup_summary.processed or startup_summary.failed:
                logger.info(
                    "見回り: 日界の蒸留消化（記憶化%d件・失敗%d件）",
                    startup_summary.total_accepted,
                    len(startup_summary.failed),
                )
        except Exception:  # noqa: BLE001
            logger.exception("見回り: 日界の蒸留消化に失敗")

        try:
            _run_growth_chores_for_state(state, timing, now=now)
        except Exception:  # noqa: BLE001
            logger.exception("見回り: 日界の成長系裏方に失敗")

        try:
            # 2026-07-25是正(I-4): 長期未起動後の未処理日を無制限に一括生成しない
            # （朝礼側は既にmax_count=1固定。日界側にも控えめな上限を設ける。この日界
            # フェーズ自体が1日1回しか走らないため、上限を超えた残りが回収されるのは
            # 次の日界（約24時間後）または次回起動の朝礼＝どちらも「次回の見回り」では
            # ない。誤解を招く旧表現を訂正）。
            _run_pending_diaries_for_serina_days(
                state, timing, now=now, max_count=timing.diary_catchup_max_count,
            )
        except Exception:  # noqa: BLE001
            logger.exception("見回り: 日界の日記生成に失敗")

        # セッション切替・日界マーキングは上記フェーズの成否によらず必ず実行する
        # （ここで打ち切ると次tickで再び最初からやり直す無限ループになるため）
        if state.session_mgr is not None:
            state.session_id = state.session_mgr.rotate(state.session_id, now=now)

        current_day = serina_day_id(now, boundary_hour=timing.serina_day_boundary_hour)
        with state.watchdog_lock:
            state.last_boundary_serina_day = current_day
        save_serina_boundary_state(
            state.serina_boundary_state_path,
            last_boundary_serina_day=current_day,
        )
        logger.info("見回り: Serina 日界によりセッション切替（day=%s）", current_day.isoformat())
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
    # 2026-08-01是正: マスターの最初の発言がまだ来ていない間はPulse（セリナから話しかける
    # 動作）を発火させない。起動後10分程度で話しかけてくる、というマスター報告への対策。
    # 直接参照にする理由はis_run_serina_day_boundary_inner側と同じ（I-5）。
    if not state.has_had_first_turn:
        return

    pulse_state_path = getattr(state, "pulse_state_path", DEFAULT_PULSE_STATE_PATH)
    schedule_path = getattr(
        state, "schedule_pulse_state_path", DEFAULT_SCHEDULE_PULSE_STATE_PATH,
    )
    with state.watchdog_lock:
        last_activity_at = state.last_activity_at
        mute = getattr(state, "pulse_mute", False)
    conversation_active = state.turn_lock.locked()
    pulse_state = load_pulse_state(pulse_state_path)
    schedule_pulse_state = load_schedule_pulse_state(schedule_path)
    # Task 1-6b: 窓終了後の tombstone / 記念日フラグリセット。
    # 会話中・mute 中は Pulse 発火本体と同じガードでスキップ（進行中ターンと SQLite ロック争奪を避ける）。
    # 状態に変化があったときだけ schedule_pulse_state.json を書く。
    if not conversation_active and not mute:
        facts = getattr(getattr(state.core, "memory_store", None), "facts", None)
        if facts is not None:
            from serina.core.chores.schedule_lifecycle import reconcile_schedule_lifecycle

            before_fired = dict(schedule_pulse_state.get("fired") or {})
            schedule_pulse_state = reconcile_schedule_lifecycle(
                now=now,
                fact_store=facts,
                schedule_pulse_state=schedule_pulse_state,
                change_log=getattr(state, "change_log", None) or getattr(state.core, "change_log", None),
            )
            after_fired = schedule_pulse_state.get("fired") or {}
            if after_fired != before_fired:
                save_schedule_pulse_state(schedule_path, fired=after_fired)
    list_fn = getattr(state.core, "list_schedule_pulse_candidates", None)
    schedule_candidates = (
        list_fn(now, schedule_pulse_state) if callable(list_fn) else []
    )
    mood = _pulse_mood_from_core(state.core)
    decision = decide_pulse(
        now=now,
        last_activity_at=last_activity_at,
        mute=mute,
        conversation_active=conversation_active,
        last_pulse_at=pulse_state.get("last_pulse_at"),
        last_by_kind=pulse_state.get("last_by_kind") or {},
        schedule_candidates=schedule_candidates,
        mood=mood,
        config=state.core.thresholds.pulse_config(),
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
        gen = getattr(state.core, "generate_pulse_text", None)
        if not callable(gen):
            return
        text = gen(decision.candidate)
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
            pulse_state_path,
            last_pulse_at=now,
            last_by_kind=updated["last_by_kind"],
        )
        # 予定窓の発火済み（Task 1-3）: memory 種別は trigger_id = "{fact_id}:{window}"
        if decision.candidate.kind == "memory":
            ctx = decision.candidate.context or {}
            fact_id = ctx.get("fact_id")
            window = ctx.get("window")
            if isinstance(fact_id, str) and isinstance(window, str):
                sched_updated = record_window_fire(
                    schedule_pulse_state,
                    fact_id=fact_id,
                    window=window,
                    fired_at=now,
                )
                save_schedule_pulse_state(schedule_path, fired=sched_updated["fired"])
        # 通常返答と同じ経路で履歴に載せ、チャット欄へ出す
        store = getattr(state, "session_store", None)
        sid = getattr(state, "session_id", None)
        if store is not None and sid:
            store.add_history(sid, "assistant", text)
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
            window=ctx.get("window"),
            session_id=sid,
        )
        logger.info("見回り: Pulse をチャット履歴へ追加（kind=%s）", decision.candidate.kind)
    finally:
        state.turn_lock.release()


def run_startup_morning_routine(state: GuiState, timing: AppTimingConfig, *, now: datetime) -> None:
    """§2.4トリガー3(次回起動時の朝礼): 前回のやり残しをまとめて消化する。

    2026-08-01是正: 従来はここで日記生成の`max_count=1`固定・`last_boundary_serina_day`の
    更新なし、という2点の不備があった。前者は複数日分の積み残しがあっても起動時に1本しか
    書かず残りを見回いスレッド任せにしていた。後者は「起動時朝礼で当日分の境界処理は
    実質終わっているのに、境界通過の記録だけが残らない」ため、見回いスレッドの
    `should_run_day_boundary`が「今日はまだ未処理」と誤認し、`grace_seconds`経過後に
    もう一度`_maybe_run_serina_day_boundary_inner`一式（蒸留消化・成長系裏方・日記・
    セッション切替）を実行してしまっていた。蒸留・日記は空振りで実害は薄いが、
    セッション切替だけは無条件実行のため「会話中に不意にセッションが切り替わる」形で
    実害化していた（マスター報告により発覚）。
    本関数は起動時朝礼の内容をmain()から切り出したもの（ユニットテスト容易化のため）。
    """
    try:
        startup_summary = run_startup_chores(
            state.core.chore_box,
            memory_store=state.core.memory_store,
            thresholds=state.core.thresholds,
            lane_call_fns=state.lane_call_fns,
            change_log=state.change_log,
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

    try:
        _run_growth_chores_for_state(state, timing, now=now)
    except Exception:  # noqa: BLE001
        logger.exception("起動時の成長系裏方に失敗。会話は継続します")

    # §4.5①朝礼: 最後に日記を書いた Serina 日が現在より前なら書く。
    # 2026-08-01是正: max_count=1固定をやめ、見回いスレッドと同じdiary_catchup_max_count
    # （既定5）を使う。積み残しが複数日分あっても起動直後にその場で片付ける
    # （マスター承認2026-08-01: 起動が多少長くなっても、事故が起きにくいシンプルな
    # 仕組みを優先）。
    if should_generate_diary_at_startup(
        now=now,
        last_diary_at=state.last_episodic_at,
        boundary_hour=timing.serina_day_boundary_hour,
    ):
        try:
            count = _run_pending_diaries_for_serina_days(
                state, timing, now=now, max_count=timing.diary_catchup_max_count,
            )
            if count:
                print(f"（朝礼: 前回日記以降の日記を{count}本書きました）")
            else:
                print("（朝礼: 日記生成を見送り）")
        except Exception:  # noqa: BLE001
            logger.exception("起動時の朝礼（日記生成）に失敗。会話は継続します")
            print("（朝礼: 日記生成に失敗しました。会話は始められます）")

    # 2026-08-01是正: 起動時朝礼で当日分の境界処理が試みられたことを記録する。
    # これが無いと見回いスレッドが「今日はまだ未処理」と誤認し、grace_seconds経過後に
    # 日界処理一式（特にセッション切替）を重複実行してしまう。
    current_day = serina_day_id(now, boundary_hour=timing.serina_day_boundary_hour)
    state.last_boundary_serina_day = current_day
    save_serina_boundary_state(
        state.serina_boundary_state_path,
        last_boundary_serina_day=current_day,
    )


def main() -> None:
    global STATE
    import uvicorn

    print("Serina GUI を起動しています…（Ollama が必要。会話Brainは単一構成）")

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
    STATE = GuiState(
        core,
        session_store,
        session_mgr,
        session_id,
        serina_day_boundary_hour=timing.serina_day_boundary_hour,
    )

    run_startup_morning_routine(STATE, timing, now=datetime.now(timezone.utc))

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
