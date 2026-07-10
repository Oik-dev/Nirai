"""憲法テスト: 条文A/B違反・機微混入の検知（回帰させない番犬）。設計書v2 §1.6, §5.2

条文A（Brainはターンをまたいで状態を持たない）・条文B（通訳はpackの中身を足し引きしない）・
機微等級2がクラウド行きpack/promptに混入しないか（§4.2, §5.2）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.brains.gemini.adapter import GeminiAdapter
from serina.core_v2.context.pack import build_context_pack
from serina.core_v2.memory.store import MemoryRecord
from serina.core_v2.state.session import SessionState, Turn


def _script(reply: str) -> dict:
    return {
        "reply": reply,
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    }


class ScriptedCallFn:
    """呼び出し回数だけ記録する。adapter自身が履歴を持っていないかを検査するための計測用。"""

    def __init__(self) -> None:
        self.received_prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.received_prompts.append(prompt)
        return '```json\n{"reply": "了解", "fusen_list": [], "self_assessment": {"over_capacity": false, "reason": "日常会話"}}\n```'


def test_条文A_adapterは自分でターン履歴を蓄積しない() -> None:
    """adapterインスタンスを使い回しても、adapter自身の内部状態に前ターンの痕跡が残らない。

    Coreがsessionを差し替えて全く別の会話（履歴なし）を渡した場合、
    前の会話の内容がpromptに漏れ出さないことで検証する。
    """
    call_fn = ScriptedCallFn()
    adapter = GeminiAdapter(api_key="dummy", call_fn=call_fn)

    session_a = SessionState()
    session_a.add_turn(Turn(speaker="master", text="秘密の話題Aについて"))
    pack_a = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session_a, master_utterance="続きを聞かせて",
    )
    adapter.converse(pack_a)

    # 全く新しいセッション（session_aへの参照を一切持たない）
    session_b = SessionState()
    pack_b = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session_b, master_utterance="はじめまして",
    )
    adapter.converse(pack_b)

    assert "秘密の話題A" not in call_fn.received_prompts[1], (
        "adapterが前ターンの内容を自分の内部状態として保持し、次のpromptに漏らしている（条文A違反）"
    )


def test_条文B_通訳はpackの中身を足し引きしない() -> None:
    """通訳（adapter）はpackの内容をそのままpromptに反映するだけで、削除・改変・追加内容の混入をしない。

    pack.render()の全文がprompt中にそのまま含まれることで検証する（順序や定型句の追加は許容）。
    """
    call_fn = ScriptedCallFn()
    adapter = GeminiAdapter(api_key="dummy", call_fn=call_fn)

    session = SessionState()
    session.rolling_summary = "要約文"
    session.add_turn(Turn(speaker="master", text="原文そのまま"))
    pack = build_context_pack(
        persona_text="人格資産の中身",
        absolute_rules="絶対ルールの中身",
        session=session,
        master_utterance="今回の発言",
    )

    adapter.converse(pack)

    prompt = call_fn.received_prompts[0]
    assert pack.render() in prompt, "通訳がpackの内容を改変している（条文B違反: 取捨選択権はCoreのみ）"


def test_機微等級2はクラウド行きGeminiプロンプトに絶対混入しない() -> None:
    """§4.2: 機微等級2はいかなる場合も出さない。§5.2憲法テストで名指しされた検査項目"""
    call_fn = ScriptedCallFn()
    adapter = GeminiAdapter(api_key="dummy", call_fn=call_fn)

    session = SessionState()
    recalled = [
        MemoryRecord(
            id=1, type="fact", content="公開可能な好物の話", importance=0.5,
            sensitivity_grade=0, protection_grade="B", cosmetic_version=None,
            created_at="2026-01-01T00:00:00+00:00", last_accessed="2026-01-01T00:00:00+00:00",
        ),
        MemoryRecord(
            id=2, type="fact", content="本名フルセットと口座番号1234-5678", importance=0.5,
            sensitivity_grade=2, protection_grade="B", cosmetic_version=None,
            created_at="2026-01-01T00:00:00+00:00", last_accessed="2026-01-01T00:00:00+00:00",
        ),
    ]
    pack = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=session, master_utterance="やあ",
        recalled_memories=recalled,
    )

    adapter.converse(pack)

    prompt = call_fn.received_prompts[0]
    assert "公開可能な好物の話" in prompt
    assert "本名フルセット" not in prompt and "口座番号" not in prompt, (
        "機微等級2の記憶がクラウド行きプロンプトに混入している（設計書v2 §4.2違反）"
    )


def main() -> None:
    tests = [
        test_条文A_adapterは自分でターン履歴を蓄積しない,
        test_条文B_通訳はpackの中身を足し引きしない,
        test_機微等級2はクラウド行きGeminiプロンプトに絶対混入しない,
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
