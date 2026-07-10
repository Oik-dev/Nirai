"""Gemini通訳のテスト。設計書v2 §1.2(通訳), §3.4(自己評価欄の強制)

実際のAPI呼び出しはinjectableなcall_fnで差し替え、ネットワークに依存しない。
実機疎通確認はtests/smoke_gemini.py（Task #8）で別途行う。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.brains.gemini.adapter import GeminiAdapter, GeminiAdapterError
from serina.core_v2.context.pack import build_context_pack
from serina.core_v2.state.session import SessionState


def _pack():
    return build_context_pack(
        persona_text="価値観: 誠実であること",
        absolute_rules="機微情報を漏らさない",
        session=SessionState(),
        master_utterance="今日は天気がいいね",
    )


def test_build_prompt_includes_context_and_self_assessment_instruction() -> None:
    adapter = GeminiAdapter(api_key="dummy", call_fn=lambda *a, **kw: "")
    prompt = adapter.build_prompt(_pack())
    assert "今日は天気がいいね" in prompt
    assert "自己評価欄" in prompt or "self_assessment" in prompt


def test_converse_parses_json_response() -> None:
    fake_response = '''ここに応答本文はなく、JSONのみ:
```json
{"reply": "そうですね", "fusen_list": [], "self_assessment": {"over_capacity": false, "reason": "日常会話"}}
```
'''
    adapter = GeminiAdapter(api_key="dummy", call_fn=lambda prompt: fake_response)
    raw = adapter.converse(_pack())
    assert raw["reply"] == "そうですね"
    assert raw["self_assessment"]["reason"] == "日常会話"


def test_converse_raises_on_unparsable_response() -> None:
    adapter = GeminiAdapter(api_key="dummy", call_fn=lambda prompt: "JSONではない普通の文章")
    try:
        adapter.converse(_pack())
        raise AssertionError("不正な応答が例外を出さず通過した")
    except GeminiAdapterError:
        pass


def main() -> None:
    tests = [
        test_build_prompt_includes_context_and_self_assessment_instruction,
        test_converse_parses_json_response,
        test_converse_raises_on_unparsable_response,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
