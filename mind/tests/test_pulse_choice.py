"""Pulse で話しかけるかと、話したあとの体を本人が選ぶ（Core.pulse と通訳）。設計書 §2.8・世界の計画書 §2.4。

守るもの：
- 話しかけるかは本人が決める。材料に手元の会話の流れ（いつ言われたかつき）があり、空の答えは「今は話さない」。
- 話したら、同じ前置きの後ろで体の欄だけを聞き、カタログで確かめてから渡す。カタログがなければ聞かない。
- 空の答えも答え（通訳は例外にしない）。
Ollama 不要。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.brains.ollama.adapter import OllamaAdapter
from mind.core.chores.idle_policy import PulseCandidate
from mind.core.config import ThresholdsConfig
from mind.core.perception import BodyCatalog, BodyChoice, body_alone_schema
from mind.core.routing.quota_ledger import QuotaLedger
from mind.core.routing.registry import BrainEntry
from mind.core.runtime import Core
from mind.core.state.routing_rules import RoutingRules
from mind.core.state.session import Turn

NOW = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)  # 日本時間 12:00
CANDIDATE = PulseCandidate(kind="connection", trigger_id="connection-2026-10-08", context={"reason": "missing_master"})
CATALOG = BodyCatalog(expressions=("喜び",), gestures=("うなずく",))


class _Brain:
    def __init__(self, words: str, body: dict | None = None) -> None:
        self.words = words
        self.body = body
        self.prompts: list[str] = []
        self.asked_body: list[tuple] = []

    def raw_call(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.words

    def choose_body(self, prompt: str, said: str, catalog: BodyCatalog) -> dict | None:
        self.asked_body.append((prompt, said, catalog))
        return self.body


def _core(brain: _Brain) -> Core:
    return Core(
        persona_text="人格", absolute_rules="ルール", thresholds=ThresholdsConfig(),
        registry=[BrainEntry("primary_brain", "ollama", "local", "primary", -1, -1, "small")],
        quota_ledger=QuotaLedger(), routing_rules=RoutingRules(), brains={"primary_brain": brain},
    )


def test_she_reads_the_flow_and_may_decide_not_to_speak_now() -> None:
    brain = _Brain("  ")
    core = _core(brain)
    core.session.add_turn(Turn(speaker="master", text="しばらく静かにしてて", ts="2026-10-08T01:00:00+00:00"))
    core.perceive(CATALOG)
    heard: list = []

    assert core.pulse(CANDIDATE, now=NOW, on_said=heard.append, on_body=heard.append) is False

    assert heard == [] and brain.asked_body == []
    prompt = brain.prompts[0]
    assert "【手元の会話の流れ】\n今は 10/08 12:00\nマスター（10/08 10:00）: しばらく静かにしてて" in prompt
    assert "今は話しかけないと決めたら、何も書かずに終えてください。" in prompt


def test_after_speaking_she_chooses_her_body_with_the_same_prefix() -> None:
    brain = _Brain("ねえ、起きてる？", body={"expression": "喜び", "gesture": "跳ねる"})
    core = _core(brain)
    core.perceive(CATALOG)
    happened: list = []

    spoke = core.pulse(
        CANDIDATE, now=NOW, on_said=lambda text: happened.append(("said", text)),
        on_body=lambda choice: happened.append(("body", choice)),
    )

    assert spoke is True
    assert happened == [("said", "ねえ、起きてる？"), ("body", BodyChoice(expression="喜び"))]  # カタログにない身振りは落とす
    assert brain.asked_body == [(brain.prompts[0], "ねえ、起きてる？", CATALOG)]


def test_without_a_catalog_the_body_is_not_asked() -> None:
    brain = _Brain("ねえ", body={"expression": "喜び"})
    core = _core(brain)
    bodies: list = []

    assert core.pulse(CANDIDATE, now=NOW, on_said=lambda _text: None, on_body=bodies.append) is True
    assert brain.asked_body == [] and bodies == []


def test_the_adapter_asks_the_body_alone_behind_the_pulse_and_keeps_an_empty_answer(monkeypatch) -> None:  # noqa: ANN001
    posts: list[dict] = []

    class Answer:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args):  # noqa: ANN002, ANN204
            return None

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"response": ""}  # 今は話さない

        def iter_lines(self):  # noqa: ANN202
            yield json.dumps({"response": '{"expression": "喜び", "gesture": "なし"}', "done": True}).encode()

    def fake_post(url, json=None, timeout=None, stream=False):  # noqa: ANN001
        posts.append(json)
        return Answer(json)

    import mind.brains.ollama.adapter as adapter_module

    monkeypatch.setattr(adapter_module.serve, "post", fake_post)
    adapter = OllamaAdapter()

    assert adapter.raw_call("Pulseの問い") == ""
    assert adapter.choose_body("Pulseの問い", "ねえ", CATALOG) == {"expression": "喜び", "gesture": "なし"}
    body_call = posts[1]
    assert body_call["prompt"].startswith("Pulseの問い\n【あなたが今かけた言葉】\nねえ\n")
    assert body_call["format"] == body_alone_schema(CATALOG)
    assert body_call["options"]["temperature"] == 0.0
