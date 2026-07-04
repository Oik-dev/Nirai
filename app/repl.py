"""CLI チャット入口（人間検証用）"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.app.workers import DistillWorker, distill_now, pending_count
from serina.core.consolidation import consolidation_due
from serina.core.runtime import create_core
from serina.core.session import SessionManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _print_reports(worker: DistillWorker) -> None:
    for line in worker.drain_reports():
        print(line)


def _distill_now(core, session_mgr: SessionManager, session_id: str) -> str:
    return distill_now(core, session_mgr, session_id, emit=print)


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
        if user_input.lower().startswith("/rate"):
            # 4c: 馴染み度の主観計測（1〜5＋任意の一言）
            tokens = user_input.split(maxsplit=2)
            try:
                rating = int(tokens[1])
                if not 1 <= rating <= 5:
                    raise ValueError
            except (IndexError, ValueError):
                print("使い方: /rate <1-5> [一言]  例: /rate 4 今日は察しがよかった")
                continue
            note = tokens[2] if len(tokens) > 2 else None
            core.store.add_metric("master_rating", float(rating), note)
            print(f"記録しました: {rating}/5" + (f"（{note}）" if note else ""))
            continue
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

        # ストリーミング表示: 生成中のトークンを逐次表示（非対応Skillは従来の一括表示）
        streamed = False

        def on_token(chunk: str) -> None:
            nonlocal streamed
            streamed = True
            print(chunk, end="", flush=True)

        print("\nセリナ> ", end="", flush=True)
        try:
            result = core.turn(session_id, user_input, on_token=on_token)
            if streamed:
                print("\n")
            else:
                print(f"{result['reply']}\n")
            # スライス2: 蒸留インテント（経路A）検知 → /distill と同じ同期フロー
            if result["skill"] == "distill":
                if worker.is_running():
                    worker.request_cancel()
                    worker.thread.join(timeout=30)
                    _print_reports(worker)
                if not worker.is_running():
                    session_id = _distill_now(core, session_mgr, session_id)
                    print(f"session_id: {session_id}")
                else:
                    print("裏の蒸留がまだ終わっていません。少し待ってからもう一度お願いします。")
                continue
        except Exception:
            logger.exception("ターン処理に失敗")
            # プレフィックス「セリナ> 」は表示済み。途中まで流れていたら改行してから謝る
            print(
                ("\n" if streamed else "")
                + "ごめん、今つながりにくいみたい。"
                "Ollama が動いているか確認してもらえる？\n"
            )
            continue

        # 経路B: 返答直後、pending か 週次固結の期限があれば裏で実行（GPUは対話優先で使い終わった後）
        _print_reports(worker)
        if not worker.is_running() and (
            pending_count(core) > 0 or consolidation_due(core.store, core.config)
        ):
            if worker.start():
                print("（裏でこれまでの会話を整理しています…）")


if __name__ == "__main__":
    main()
