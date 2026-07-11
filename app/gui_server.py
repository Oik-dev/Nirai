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

from serina.connectors.embedder import OllamaEmbedder
from serina.core.config import CoreConfig
from serina.core.session import SessionManager
from serina.core_v2.chores.orchestrator import (
    build_default_lane_call_fns,
    run_session_end_chores,
    run_startup_chores,
)
from serina.core_v2.env import get_gemini_api_key
from serina.core_v2.factory import create_core_v2
from serina.memory.store import MemoryStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

GUI_HOST = "127.0.0.1"
GUI_PORT = 8765
WEB_DIR = Path(__file__).resolve().parent / "web"

FALLBACK_APOLOGY = "ごめん、今つながりにくいみたい。Ollama が動いているか確認してもらえる？"

# §2.4セッション終了の定義トリガー3「明示の別れの挨拶」。GUI終了検知・アイドルタイマー
# （トリガー1,2）は次スライス（DECISIONS 2026-07-11「旧GUIをcore_v2へ移行」参照）。
FAREWELL_PHRASES = ("おやすみ", "またね", "じゃあね", "バイバイ", "ばいばい")


def _is_farewell(text: str) -> bool:
    return any(phrase in text for phrase in FAREWELL_PHRASES)


class GuiState:
    """プロセス内で1つだけ持つ実行状態（単一ユーザー前提）。"""

    def __init__(self, core, session_store: MemoryStore, session_mgr: SessionManager, session_id: str) -> None:
        self.core = core
        self.session_store = session_store
        self.session_mgr = session_mgr
        self.session_id = session_id
        self.turn_lock = threading.Lock()  # 多重送信は先行ターン完了まで待つ


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
            # Aurora実発注を伴うため数十秒〜かかりうるが、別れの挨拶直後の1回のみなので許容する
            # （GUI終了検知・アイドルタイマーによるトリガー1,2は次スライス）。
            if _is_farewell(text):
                events.put(_ev("notice", text="（裏でこれまでの会話を整理しています…）"))
                try:
                    job_ids, summary = run_session_end_chores(
                        state.core,
                        state.core.chore_box,
                        memory_store=state.core.memory_store,
                        thresholds=state.core.thresholds,
                        lane_call_fns=build_default_lane_call_fns(get_gemini_api_key_or_none()),
                    )
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
    diaries = _state().session_store.list_memories_by_type("diary", limit=200)
    return [{"created_at": d["created_at"], "content": d["content"]} for d in diaries]


# 静的ファイル（/api より後に mount するので API が優先される）
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


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

    STATE = GuiState(core, session_store, session_mgr, session_id)

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
