"""裏方ワーカー（蒸留・再固結）— REPL / GUI 共用"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from serina.connectors.chat_llm import OllamaChatConnector
from serina.core.consolidation import (
    consolidation_due,
    create_consolidator,
    format_consolidation_report,
)
from serina.core.reflection import create_distiller, format_report, generate_idle_thought
from serina.core.session import SessionManager

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
        distilled_ok = False
        try:
            for report in distiller.distill_all_pending():
                if report["status"] == "ok" and report.get("diary") and \
                        not str(report["diary"]).startswith("("):
                    distilled_ok = True
                with self._lock:
                    self._reports.append(format_report(report))
        except Exception as exc:  # noqa: BLE001
            logger.exception("蒸留ワーカーが異常終了")
            with self._lock:
                self._reports.append(f"[蒸留失敗] 予期せぬ例外: {exc}（pending維持）")
            return
        # 4b: 経路Bの蒸留が実行された起動のみ、独り言を生成（捏造ガードは生成側）
        try:
            if distilled_ok and not self.cancel_event.is_set():
                idle = generate_idle_thought(
                    self.core.store, self.core.persona, cfg, distiller.aurora)
                if idle:
                    with self._lock:
                        self._reports.append(f"[独り言] {idle[:60]}…" if len(idle) > 60
                                             else f"[独り言] {idle}")
        except Exception:  # noqa: BLE001 — 独り言は失敗しても本体に影響させない
            logger.exception("独り言の生成に失敗")
        # 4a: 蒸留完了後、週次の再固結が期限を迎えていれば続けて実行（同じ裏方キュー）
        try:
            if not self.cancel_event.is_set() and consolidation_due(self.core.store, cfg):
                consolidator = create_consolidator(self.core, self.cancel_event)
                with self._lock:
                    self._reports.append(
                        format_consolidation_report(consolidator.consolidate()))
        except Exception as exc:  # noqa: BLE001
            logger.exception("再固結ワーカーが異常終了")
            with self._lock:
                self._reports.append(f"[再固結失敗] 予期せぬ例外: {exc}（次回再試行）")


def pending_count(core) -> int:
    return len(core.store.list_sessions_by_status("pending"))


def distill_now(
    core,
    session_mgr: SessionManager,
    session_id: str,
    emit: Callable[[str], None],
) -> str:
    """経路A: 同期蒸留。現activeをpending化→全pendingを蒸留→新activeを返す。

    emit にはレポート行の出力先を渡す（REPL は print、GUI は notice キュー）。
    """
    cfg = core.config
    try:
        OllamaChatConnector.ensure_model_available(cfg.reason_model, cfg.base_url)
    except Exception as exc:  # noqa: BLE001
        emit(f"理性エンジンを確認できません: {exc}")
        return session_id

    if core.store.get_session_history(session_id):
        core.store.set_session_status(session_id, "pending")

    distiller = create_distiller(core)
    for report in distiller.distill_all_pending():
        emit(format_report(report))

    new_id, _ = session_mgr.resolve_active_session()
    return new_id
