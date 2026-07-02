"""CLI チャット入口（人間検証用）"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.runtime import create_core
from serina.core.session import SessionManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    print("Serina Core REPL（/exit で終了）")
    print("Ollama と Aurora モデルが必要です。")

    try:
        core = create_core()
    except RuntimeError as exc:
        print(f"起動エラー: {exc}")
        sys.exit(1)

    session_mgr = SessionManager(core.store, core.config)
    session_id, pending_id = session_mgr.resolve_active_session()
    print(f"session_id: {session_id}")
    if pending_id:
        print(
            f"（前回セッション {pending_id} を蒸留待ち[pending]にしました。"
            "蒸留はスライス3bで実装予定です）"
        )
    print("-" * 40)

    while True:
        try:
            user_input = input("マスター> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n終了します。")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            print("終了します。")
            break

        try:
            result = core.turn(session_id, user_input)
            print(f"\nセリナ> {result['reply']}\n")
        except Exception as exc:
            logger.exception("ターン処理に失敗")
            print(
                "\nセリナ> ごめん、今つながりにくいみたい。"
                "Ollama が動いているか確認してもらえる？\n"
            )


if __name__ == "__main__":
    main()
