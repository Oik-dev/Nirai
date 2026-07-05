"""C1 判断とVoiceの分離（配線＝Decision→Action→Evidence→Voice）の回帰テスト（Ollama不要）

守るべき壁:
- 判断(Decision)は決定論・気分/人格に非依存（逆流の壁）
- 判断結果は不変（Voiceが書き換えられない）
- 行動結果は根拠(Evidence)として構造化され、Voice(skill)へ読み取り専用で渡る
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.config import CoreConfig
from serina.core.decision import DecisionResult, Evidence, build_evidence, decide
from serina.core.runtime import Core
from serina.memory.store import MemoryStore
from serina.skills.distill_intent import DistillIntentSkill
from serina.tests.test_reflection import FakeEmbedder, FakeSkill


def _fresh_store() -> MemoryStore:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    return MemoryStore(FakeEmbedder(), db_path=tmp)


# ---- ① Decision（判断層） ----

def test_decide_returns_structured_verdict() -> None:
    skills = [DistillIntentSkill(), FakeSkill()]
    d = decide("おはよう、今日も頑張ろう", skills)
    assert d.intent == "fake", f"通常会話の判断先が違う: {d.intent}"
    assert d.action == "answer_from_knowledge", f"C1既定の行動が違う: {d.action}"
    assert d.query is None, "検索復帰用の query フィールドが温存されていない"

    d2 = decide("今日の話を整理して", skills)
    assert d2.intent == "distill", f"蒸留インテントの判断先が違う: {d2.intent}"


def test_decision_is_immutable() -> None:
    """Voiceが判断を書き換えられないこと＝DecisionResultは不変(frozen)"""
    d = decide("こんにちは", [FakeSkill()])
    try:
        d.action = "search"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("DecisionResult が書き換え可能（Voice逆流の穴）")


# ---- ②→③ Evidence（行動結果の構造化） ----

def test_evidence_carries_recalled_memory() -> None:
    d = DecisionResult(intent="fake")
    mems = [{"type": "fact", "content": "マスターは猫が好き"}]
    ev = build_evidence(d, mems)
    assert isinstance(ev, Evidence)
    assert ev.status == d.action, "Evidenceのstatusが行動方針を反映していない"
    assert ev.source == "memory", "C1のEvidence源は記憶のはず"
    assert any("猫が好き" in item for item in ev.items), "想起記憶が根拠に載っていない"


# ---- 逆流回帰（哲学の核） ----

def test_decision_unchanged_when_emotion_changes() -> None:
    """同一入力で気分ブロックを変えても判断(action/intent)が動かない＝人格・口調に非依存"""
    store = _fresh_store()
    store.create_session("s")
    core = Core(store, "p", [DistillIntentSkill(), FakeSkill()], config=CoreConfig())

    store.set_profile("emotion.narrative_mood", "とても機嫌が良く甘えたい")
    r1 = core.turn("s", "雨って汚いの？")
    store.set_profile("emotion.narrative_mood", "ひどく落ち込んで塞ぎ込んでいる")
    r2 = core.turn("s", "雨って汚いの？")

    assert r1["decision"].action == r2["decision"].action == "answer_from_knowledge", "気分で行動方針が揺れた"
    assert r1["decision"].intent == r2["decision"].intent == "fake", "気分で判断先が揺れた"


# ---- 配線（turn が Decision/Evidence を通し、Voice へ渡す） ----

def test_turn_wires_decision_and_evidence_to_voice() -> None:
    store = _fresh_store()
    store.create_session("s")
    core = Core(store, "p", [FakeSkill()], config=CoreConfig())
    result = core.turn("s", "元気？")

    assert isinstance(result["decision"], DecisionResult), "turn結果にDecisionが無い"
    assert isinstance(result["evidence"], Evidence), "turn結果にEvidenceが無い"
    # Voice(skill)は判断と根拠を読み取り専用で受け取る
    assert FakeSkill.last_ctx.decision is result["decision"], "VoiceにDecisionが渡っていない"
    assert FakeSkill.last_ctx.evidence is result["evidence"], "VoiceにEvidenceが渡っていない"


def main() -> None:
    tests = [
        test_decide_returns_structured_verdict,
        test_decision_is_immutable,
        test_evidence_carries_recalled_memory,
        test_decision_unchanged_when_emotion_changes,
        test_turn_wires_decision_and_evidence_to_voice,
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
