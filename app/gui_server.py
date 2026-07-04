"""GUI サーバ（FastAPI）— Core の薄い皮。判断ロジックは持たない"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from serina.app.workers import DistillWorker, distill_now, pending_count
from serina.core.consolidation import consolidation_due
from serina.core.runtime import create_core
from serina.core.session import SessionManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

GUI_HOST = "127.0.0.1"
GUI_PORT = 8765
WEB_DIR = Path(__file__).resolve().parent / "web"

FALLBACK_APOLOGY = "ごめん、今つながりにくいみたい。Ollama が動いているか確認してもらえる？"


class GuiState:
    """プロセス内で1つだけ持つ実行状態（単一ユーザー前提）。"""

    def __init__(self, core, session_mgr: SessionManager, session_id: str) -> None:
        self.core = core
        self.session_mgr = session_mgr
        self.session_id = session_id
        self.worker = DistillWorker(core)
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
    """1ターンを実行し、イベントを events へ積む。REPL の main ループと同じ段取り。"""
    state = _state()
    with state.turn_lock:
        try:
            # 対話最優先: 裏の蒸留が動いていたら中断要求（pending維持・次回再試行）
            if state.worker.is_running():
                state.worker.request_cancel()

            try:
                result = state.core.turn(
                    state.session_id, text,
                    on_token=lambda c: events.put(_ev("token", text=c)))
            except Exception:  # noqa: BLE001 — 人格の謝り文言に変換
                logger.exception("GUI ターン処理に失敗")
                events.put(_ev("error", text=FALLBACK_APOLOGY))
                return

            # 裏ワーカーの完了レポートを排出（前ターンで起動していた分）
            for line in state.worker.drain_reports():
                events.put(_ev("notice", text=line))

            # 蒸留インテント（経路A）: REPL の /distill と同じ同期フロー
            if result["skill"] == "distill":
                if state.worker.is_running():
                    state.worker.request_cancel()
                    state.worker.thread.join(timeout=30)
                    for line in state.worker.drain_reports():
                        events.put(_ev("notice", text=line))
                if state.worker.is_running():
                    events.put(_ev(
                        "notice",
                        text="裏の蒸留がまだ終わっていません。少し待ってからもう一度お願いします。"))
                else:
                    try:
                        state.session_id = distill_now(
                            state.core, state.session_mgr, state.session_id,
                            emit=lambda line: events.put(_ev("notice", text=line)))
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("同期蒸留に失敗")
                        events.put(_ev("notice", text=f"[蒸留失敗] {exc}（pending維持）"))

            # 経路B: 返答直後、pending か週次固結の期限があれば裏で実行
            if not state.worker.is_running() and (
                pending_count(state.core) > 0
                or consolidation_due(state.core.store, state.core.config)
            ):
                if state.worker.start():
                    events.put(_ev("notice", text="（裏でこれまでの会話を整理しています…）"))

            events.put(_ev("done", reply=result["reply"], skill=result["skill"],
                           session_id=state.session_id))
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
        "pending": pending_count(state.core),
    }


@app.get("/api/history")
def api_history():
    state = _state()
    return state.core.store.get_session_history(state.session_id)


@app.get("/api/sessions")
def api_sessions():
    store = _state().core.store
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
    store = _state().core.store
    return store.get_session_history(session_id) or store.get_archived_history(session_id)


@app.get("/api/album")
def api_album():
    diaries = _state().core.store.list_memories_by_type("diary", limit=200)
    return [{"created_at": d["created_at"], "content": d["content"]} for d in diaries]


# 静的ファイル（/api より後に mount するので API が優先される）
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


def main() -> None:
    global STATE
    import uvicorn

    print("Serina GUI を起動しています…（Ollama と Aurora モデルが必要です）")
    try:
        core = create_core()
    except RuntimeError as exc:
        print(f"起動エラー: {exc}")
        sys.exit(1)

    # REPL と同一の起動手順（3b: 孤児セッションの採用 → active 解決）
    orphans = core.store.backfill_orphan_sessions()
    if orphans:
        print(f"（過去の未整理セッション {len(orphans)} 件を蒸留待ちに登録しました）")
    session_mgr = SessionManager(core.store, core.config)
    session_id, pending_id = session_mgr.resolve_active_session()
    if pending_id:
        print(f"（前回セッション {pending_id} を蒸留待ち[pending]にしました）")

    STATE = GuiState(core, session_mgr, session_id)

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
