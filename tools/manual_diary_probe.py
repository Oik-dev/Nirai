"""実機確認用ワンショット: 日記生成フロー(§4.5)を実DBに対して1回だけ実行する。

MILESTONE次アクション#1の実機確認用。自動テストには含めない。
実行後は`/api/album`で確認し、確認後に手動でDBから削除してよい（検証成果物のため）。

実行するたび実DB(`data/serina_memory.db`)に日記1件を実際に書き込む（等級A=忘却対象外）。
誤って複数回実行すると検証エントリが累積するため、実行前に確認を挟む
（serina-code-reviewer 2026-07-12 Minor指摘）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.orchestrator import build_default_lane_call_fns, run_diary_generation
from serina.core.factory import create_core
from serina.core.memory.protection import DEFAULT_CHANGE_LOG_PATH, ChangeLog


def main() -> None:
    since_iso = sys.argv[1] if len(sys.argv) > 1 else "2026-07-10T00:00:00+00:00"
    answer = input(f"実DB(data/serina_memory.db)に日記1件を書き込みます（since_iso={since_iso}）。続行しますか？ [y/N]: ")
    if answer.strip().lower() != "y":
        print("中止しました。")
        return

    core = create_core()
    lane_call_fns = build_default_lane_call_fns()
    change_log = ChangeLog(DEFAULT_CHANGE_LOG_PATH)

    outcome = run_diary_generation(
        core,
        since_iso=since_iso,
        routing_rules=core.routing_rules,
        lane_call_fns=lane_call_fns,
        change_log=change_log,
    )
    print("generated:", outcome.generated)
    print("memory_id:", outcome.memory_id)
    print("lane:", outcome.lane)
    print("reason:", outcome.reason)


if __name__ == "__main__":
    main()
