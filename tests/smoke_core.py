"""Core 層スモーク（記憶注入が見える形で検証）"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.runtime import create_core


def preview(text: str, n: int = 80) -> str:
    s = " ".join(text.split())
    return s[:n] + ("..." if len(s) > n else "")


def print_turn(label: str, result: dict) -> None:
    print(f"\n{'=' * 60}")
    print(label)
    print("=" * 60)

    print("\n(a) 取得された記憶:")
    mems = result.get("retrieved") or []
    if not mems:
        print("  （なし）")
    else:
        for i, mem in enumerate(mems, 1):
            pin = "[固定]" if mem.get("pinned") else ""
            print(f"  {i}. {pin}[{mem.get('type')}] {preview(mem.get('content', ''), 100)}")

    print(f"\n(b) system プロンプト長: {result.get('system_len', 0)} 文字")
    print(f"(c) 使われた Skill: {result.get('skill')}")
    print(f"\n(d) Aurora の応答:")
    print(result.get("reply", ""))


def main() -> None:
    print("Serina Core 層スモークテスト")
    print("Ollama (bge-m3 + Aurora) が起動している必要があります。")

    try:
        core = create_core()
    except RuntimeError as exc:
        print(f"起動エラー: {exc}")
        sys.exit(1)

    session_id = "smoke_core_test"

    q1 = "私との約束は？宮古島や高野漁港、約束の海のことも教えて。"
    r1 = core.turn(session_id, q1)
    print_turn(f"1ターン目: {q1}", r1)

    q2 = "さっきの続きだけど、わたしは誰のもの？"
    r2 = core.turn(session_id, q2)
    print_turn(f"2ターン目: {q2}", r2)

    print("\n" + "=" * 60)
    print("判定メモ")
    print("=" * 60)
    has_memory = len(r1.get("retrieved") or []) > 0
    print(f"- 1ターン目に関連記憶が取れた: {'はい' if has_memory else 'いいえ'}")
    print(f"- 2ターン目の履歴件数（DB）: {len(core.store.get_recent_history(session_id, 10))} 件")
    print("  （user/assistant が2往復=4件なら履歴注入OK）")


if __name__ == "__main__":
    main()
