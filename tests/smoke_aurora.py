"""実機スモーク: Aurora(Ollama, NemoAurora-RP-12B)との疎通確認のみ。設計書 §5.2

自動テストスイートには含めない（Ollama起動が前提のため手動実行）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.brains.aurora.adapter import AuroraAdapter
from serina.core.context.pack import build_context_pack
from serina.core.state.session import SessionState


def main() -> None:
    print("=" * 60)
    print("Aurora(Ollama) 実機疎通確認")
    print("=" * 60)

    adapter = AuroraAdapter()
    pack = build_context_pack(
        persona_text="あなたはセリナ。誠実で温かい話し方をする。",
        absolute_rules="機微情報を漏らさない。",
        session=SessionState(),
        master_utterance="こんにちは、調子はどう？",
    )

    try:
        raw = adapter.converse(pack)
    except Exception as e:  # noqa: BLE001
        print(f"[NG] Aurora呼び出しに失敗: {type(e).__name__}: {e}")
        sys.exit(1)

    print("[OK] Aurora応答を取得（二段方式）")
    print(f"  reply(1回目・自由会話): {raw.get('reply')}")
    print(f"  fusen_list件数(2回目・抽出): {len(raw.get('fusen_list', []))}")
    print(f"  self_assessment: {raw.get('self_assessment')}")

    if "reply" not in raw or raw.get("self_assessment") is None:
        print("[NG] 契約書式の必須項目が欠けている")
        sys.exit(1)

    print()
    print("疎通確認 成功")


if __name__ == "__main__":
    main()
