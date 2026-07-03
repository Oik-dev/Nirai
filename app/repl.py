"""CLI チャット入口（人間検証用）"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.connectors.chat_llm import OllamaChatConnector
from serina.core.reflection import create_distiller, format_report
from serina.core.runtime import create_core
from serina.core.session import SessionManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class DistillWorker:
    """経路B: 蒸留のバックグラウンド実行。対話最優先（cancelで中断→pending維持）。"""

    def __init__(self, core) -> None:
        self.core = core
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._reports: list[str] = []
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> bool:
        if self.is_running():
            return False
        self.cancel_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def drain_reports(self) -> list[str]:
        with self._lock:
            reports, self._reports = self._reports, []
        return reports

    def _run(self) -> None:
        cfg = self.core.config
        try:
            OllamaChatConnector.ensure_model_available(cfg.reason_model, cfg.base_url)
        except Exception as exc:  # noqa: BLE001 — 理性エンジン未導入でも対話は妨げない
            with self._lock:
                self._reports.append(f"[蒸留スキップ] 理性エンジンを確認できません: {exc}")
            return
        distiller = create_distiller(self.core, self.cancel_event)
        try:
            for report in distiller.distill_all_pending():
                with self._lock:
                    self._reports.append(format_report(report))
        except Exception as exc:  # noqa: BLE001
            logger.exception("蒸留ワーカーが異常終了")
            with self._lock:
                self._reports.append(f"[蒸留失敗] 予期せぬ例外: {exc}（pending維持）")


def _pending_count(core) -> int:
    return len(core.store.list_sessions_by_status("pending"))


def _print_reports(worker: DistillWorker) -> None:
    for line in worker.drain_reports():
        print(line)


def _distill_now(core, session_mgr: SessionManager, session_id: str) -> str:
    """経路A: 同期蒸留。現activeをpending化→全pendingを蒸留→新activeを返す。"""
    cfg = core.config
    try:
        OllamaChatConnector.ensure_model_available(cfg.reason_model, cfg.base_url)
    except Exception as exc:  # noqa: BLE001
        print(f"理性エンジンを確認できません: {exc}")
        return session_id

    if core.store.get_session_history(session_id):
        core.store.set_session_status(session_id, "pending")

    distiller = create_distiller(core)
    for report in distiller.distill_all_pending():
        print(format_report(report))

    new_id, _ = session_mgr.resolve_active_session()
    return new_id


def main() -> None:
    print("Serina Core REPL（/exit で終了、/distill で今すぐ蒸留）")
    print("Ollama と Aurora モデルが必要です。")

    try:
        core = create_core()
    except RuntimeError as exc:
        print(f"起動エラー: {exc}")
        sys.exit(1)

    # 3b: 孤児セッションの採用（sessions未登録のhistoryをpendingへ）
    orphans = core.store.backfill_orphan_sessions()
    if orphans:
        print(f"（過去の未整理セッション {len(orphans)} 件を蒸留待ちに登録しました）")

    session_mgr = SessionManager(core.store, core.config)
    session_id, pending_id = session_mgr.resolve_active_session()
    print(f"session_id: {session_id}")
    if pending_id:
        print(f"（前回セッション {pending_id} を蒸留待ち[pending]にしました）")
    print("-" * 40)

    worker = DistillWorker(core)

    while True:
        try:
            user_input = input("マスター> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n終了します。")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            if worker.is_running():
                print("蒸留が未完了です。pending のまま残し、次回起動時に再試行します。")
                worker.request_cancel()
                worker.thread.join(timeout=10)
                _print_reports(worker)
            print("終了します。")
            break
        if user_input.lower() == "/distill":
            if worker.is_running():
                worker.request_cancel()
                worker.thread.join(timeout=30)
                _print_reports(worker)
                if worker.is_running():
                    # join タイムアウト。二重蒸留を避けるため同期蒸留は開始しない
                    print("裏の蒸留がまだ終わっていません。少し待ってからもう一度 /distill してください。")
                    continue
            session_id = _distill_now(core, session_mgr, session_id)
            print(f"session_id: {session_id}")
            continue

        # 対話最優先: 蒸留中に入力が来たら中断させる（pending維持・次回再試行）
        if worker.is_running():
            worker.request_cancel()

        try:
            result = core.turn(session_id, user_input)
            print(f"\nセリナ> {result['reply']}\n")
        except Exception:
            logger.exception("ターン処理に失敗")
            print(
                "\nセリナ> ごめん、今つながりにくいみたい。"
                "Ollama が動いているか確認してもらえる？\n"
            )
            continue

        # 経路B: 返答直後、pendingが残っていれば裏で蒸留（GPUは対話優先で使い終わった後）
        _print_reports(worker)
        if not worker.is_running() and _pending_count(core) > 0:
            if worker.start():
                print("（裏でこれまでの会話を日記にまとめています…）")


if __name__ == "__main__":
    main()
