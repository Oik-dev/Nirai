"""実機スモーク: Geminiとの疎通確認のみ。設計書 §5.2

.envのGEMINI_API_KEYを使い、実際にGemini APIへ1往復する。
自動テストスイートには含めない（ネットワーク・無料枠を消費するため手動実行）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.brains.gemini.adapter import GeminiAdapter, GeminiAdapterError
from serina.core.context.pack import build_context_pack
from serina.core.env import get_gemini_api_key
from serina.core.state.session import SessionState


def main() -> None:
    print("=" * 60)
    print("Gemini 実機疎通確認")
    print("=" * 60)

    try:
        api_key = get_gemini_api_key()
    except RuntimeError as e:
        print(f"[NG] {e}")
        sys.exit(1)

    adapter = GeminiAdapter(api_key=api_key)
    pack = build_context_pack(
        persona_text="あなたはセリナ。誠実で温かい話し方をする。",
        absolute_rules="機微情報を漏らさない。",
        session=SessionState(),
        master_utterance="こんにちは、調子はどう？",
    )

    try:
        raw = adapter.converse(pack)
    except Exception as e:  # noqa: BLE001
        print(f"[NG] Gemini呼び出しに失敗: {type(e).__name__}: {e}")
        sys.exit(1)

    print("[OK] Gemini応答を取得")
    print(f"  reply: {raw.get('reply')}")
    print(f"  fusen_list件数: {len(raw.get('fusen_list', []))}")
    print(f"  self_assessment: {raw.get('self_assessment')}")

    if "reply" not in raw or "self_assessment" not in raw:
        print("[NG] 契約書式の必須項目が欠けている")
        sys.exit(1)

    print()
    print("疎通確認 成功")


if __name__ == "__main__":
    main()
