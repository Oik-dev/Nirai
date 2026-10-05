"""憲法テスト: 条文A/B違反・Skill境界の検知（回帰させない番犬）。設計書 §1.6, §5.2

条文A（Brainはターンをまたいで状態を持たない）・条文B（通訳はpackの中身を足し引きしない）・
Skill ペイロードに記憶／人格が混入しないか（§5.6）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.brains.ollama.adapter import OllamaAdapter
from mind.core.context.pack import build_context_pack
from mind.core.state.session import SessionState, Turn


def _script(reply: str) -> dict:
    return {
        "reply": reply,
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    }


class ScriptedCallFn:
    """呼び出し回数だけ記録する。adapter自身が履歴を持っていないかを検査するための計測用。"""

    def __init__(self) -> None:
        self.received_prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.received_prompts.append(prompt)
        return "了解"


def test_条文A_adapterは自分でターン履歴を蓄積しない() -> None:
    """adapterインスタンスを使い回しても、adapter自身の内部状態に前ターンの痕跡が残らない。

    Coreがsessionを差し替えて全く別の会話（履歴なし）を渡した場合、
    前の会話の内容がpromptに漏れ出さないことで検証する。
    """
    call_fn = ScriptedCallFn()
    adapter = OllamaAdapter(chat_call_fn=call_fn)

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

    # index 0,1 = pack_aの返答呼び・評価の呼び。index 2 = pack_bの返答呼び
    # （OllamaAdapter.converseは返答生成と、返答のあとの評価の2回呼ぶため、pack_b分の返答呼びはindex 2に来る）。
    assert "秘密の話題A" not in call_fn.received_prompts[2], (
        "adapterが前ターンの内容を自分の内部状態として保持し、次のpromptに漏らしている（条文A違反）"
    )


def test_条文B_通訳はpackの中身を足し引きしない() -> None:
    """通訳（adapter）はpackの内容をそのままpromptに反映するだけで、削除・改変・追加内容の混入をしない。

    pack.render()の全文がprompt中にそのまま含まれることで検証する（順序や定型句の追加は許容）。
    """
    call_fn = ScriptedCallFn()
    adapter = OllamaAdapter(chat_call_fn=call_fn)

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


def test_Skillペイロードに記憶と人格を載せない() -> None:
    """§5.6: Gemini アドバイザーへは相談クエリのみ。記憶バンドル・人格テキストは禁止。

    2026-07-31是正（Phase C2）: sanitize_query/build_payloadはfail-closedになり、
    routing_rules未接続（None）では常にNoneを返すようになった。本テストの主眼は
    「人格・記憶がペイロードに含まれないこと」であり機微判定そのものではないため、
    機微でない素のRoutingRules()を明示的に渡す。
    """
    from mind.core.state.routing_rules import RoutingRules
    from mind.skills.gemini_advisor.payload import FORBIDDEN_PAYLOAD_KEYS, build_payload

    payload = build_payload(
        "明日の東京の天気", category="web_search", routing_rules=RoutingRules(),
    )
    assert payload is not None
    audit = payload.to_audit_dict()
    for key in FORBIDDEN_PAYLOAD_KEYS:
        assert key not in audit
    body = payload.to_api_body(system_instruction="無人格アドバイザー")
    blob = str(body)
    assert "memories" not in blob
    assert "persona_text" not in blob
    assert "明日の東京の天気" in blob


def main() -> None:
    tests = [
        test_条文A_adapterは自分でターン履歴を蓄積しない,
        test_条文B_通訳はpackの中身を足し引きしない,
        test_Skillペイロードに記憶と人格を載せない,
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


def test_Skillは上位層をimportしない() -> None:
    """憲章 B-1: 依存は Core → 通訳 → Brain → Skill の一方向。Skill から上位層への逆流禁止。"""
    skill_dir = ROOT / "skills" / "gemini_advisor"
    for py in skill_dir.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "mind.core" not in src, f"{py.name} が Core を逆輸入している"
        assert "mind.brains" not in src, f"{py.name} が Brain 層を逆輸入している"
