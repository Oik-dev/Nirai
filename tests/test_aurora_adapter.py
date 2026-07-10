"""Aurora通訳(二段方式)のテスト。設計書v2 §5.5-7

1回目: 自由に会話させる（RP特化・書式強制なし）。2回目: 直前の会話から付箋だけを抜き出す小さな作業。
実際のOllama呼び出しはinjectableなcall_fnで差し替え、ネットワークに依存しない。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.brains.aurora.adapter import AuroraAdapter, AuroraAdapterError
from serina.core_v2.context.pack import build_context_pack
from serina.core_v2.state.session import SessionState


def _pack():
    return build_context_pack(
        persona_text="価値観: 誠実であること",
        absolute_rules="機微情報を漏らさない",
        session=SessionState(),
        master_utterance="今日は疲れたよ",
    )


def test_stage1_is_free_form_without_json_constraint() -> None:
    """1回目は書式強制なしで自由に会話させる"""
    seen_prompts: list[str] = []

    def chat_call(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "お疲れ様、ゆっくり休んでね"

    def extract_call(prompt: str) -> str:
        return '{"fusen_list": [], "self_assessment": {"over_capacity": false, "reason": "日常会話"}}'

    adapter = AuroraAdapter(chat_call_fn=chat_call, extract_call_fn=extract_call)
    raw = adapter.converse(_pack())

    assert raw["reply"] == "お疲れ様、ゆっくり休んでね"
    assert "```json" not in seen_prompts[0], "1回目のプロンプトはJSON書式を強制しない"


def test_stage2_extracts_fusen_from_stage1_conversation() -> None:
    """2回目は直前の会話から付箋だけを抜き出す別発注"""
    def chat_call(prompt: str) -> str:
        return "お疲れ様、ゆっくり休んでね"

    seen_extract_prompts: list[str] = []

    def extract_call(prompt: str) -> str:
        seen_extract_prompts.append(prompt)
        return '''```json
{"fusen_list": [{"kind": "マスター観測", "version": 1, "content": {"observation": "疲れてそう"}, "confidence": 0.8}],
 "self_assessment": {"over_capacity": false, "reason": "日常会話"}}
```'''

    adapter = AuroraAdapter(chat_call_fn=chat_call, extract_call_fn=extract_call)
    raw = adapter.converse(_pack())

    assert len(raw["fusen_list"]) == 1
    assert raw["fusen_list"][0]["kind"] == "マスター観測"
    assert raw["self_assessment"]["reason"] == "日常会話"
    assert "お疲れ様、ゆっくり休んでね" in seen_extract_prompts[0], "2回目は1回目の会話を材料にする"


def test_converse_raises_when_stage2_extraction_always_fails() -> None:
    def chat_call(prompt: str) -> str:
        return "うん、そうだね"

    def extract_call(prompt: str) -> str:
        return "JSONではない普通の文章"

    adapter = AuroraAdapter(chat_call_fn=chat_call, extract_call_fn=extract_call, max_extraction_retries=2)
    try:
        adapter.converse(_pack())
        raise AssertionError("2回目の抽出失敗が例外を出さず通過した")
    except AuroraAdapterError:
        pass


def test_stage2_retries_on_malformed_json_and_succeeds() -> None:
    """§5.5-7: Auroraは書式が苦手なため、2回目の抽出は失敗してもリトライする（通訳内部に閉じた対策）"""
    def chat_call(prompt: str) -> str:
        return "うん、そうだね"

    attempts = {"count": 0}

    def extract_call(prompt: str) -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return "崩れたJSONもどき { fusen_list:"
        return '{"fusen_list": [], "self_assessment": {"over_capacity": false, "reason": "日常会話"}}'

    adapter = AuroraAdapter(chat_call_fn=chat_call, extract_call_fn=extract_call, max_extraction_retries=3)
    raw = adapter.converse(_pack())

    assert attempts["count"] == 2
    assert raw["self_assessment"]["reason"] == "日常会話"


def main() -> None:
    tests = [
        test_stage1_is_free_form_without_json_constraint,
        test_stage2_extracts_fusen_from_stage1_conversation,
        test_converse_raises_when_stage2_extraction_always_fails,
        test_stage2_retries_on_malformed_json_and_succeeds,
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
