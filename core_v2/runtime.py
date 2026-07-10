"""Core本体。設計書v2 第1章〜第2章, §3(ルーティング), §4(記憶接続)。

会話 → 想起(長期記憶) → 文脈パック組み立て → Brain選択・呼び出し
 → 関所①②③ → 状態更新 → 記憶候補の審査ライン(関所④) → セッションへ記録。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.context.pack import build_context_pack
from serina.core_v2.intake.gate import IntakeResult, process_report
from serina.core_v2.intake.memory_review import review_candidate
from serina.core_v2.memory.store import MemoryStore
from serina.core_v2.routing.decision import decide_brain
from serina.core_v2.routing.quota_ledger import QuotaLedger
from serina.core_v2.routing.registry import BrainEntry
from serina.core_v2.state.emotion import EmotionState
from serina.core_v2.state.relationship import RelationshipState
from serina.core_v2.state.routing_rules import RoutingRules
from serina.core_v2.state.session import SessionState, Turn

RECALL_TOP_K = 5


class Brain(Protocol):
    def converse(self, pack) -> dict: ...  # noqa: ANN001


class Core:
    def __init__(
        self,
        persona_text: str,
        absolute_rules: str,
        thresholds: ThresholdsConfig,
        memory_store: MemoryStore | None = None,
        registry: list[BrainEntry] | None = None,
        quota_ledger: QuotaLedger | None = None,
        routing_rules: RoutingRules | None = None,
        brains: dict[str, Brain] | None = None,
    ) -> None:
        self.persona_text = persona_text
        self.absolute_rules = absolute_rules
        self.thresholds = thresholds
        self.memory_store = memory_store
        self.registry = registry
        self.quota_ledger = quota_ledger
        self.routing_rules = routing_rules
        self.brains = brains
        self.emotion = EmotionState()
        self.relationship = RelationshipState()
        self.session = SessionState()
        self.session_candidate_count = 0
        # §3.4: 昇格は自己申告があるまで持続する（毎ターン揮発しない）
        self.current_tier = "primary"
        # §3.4交代要請: 次ターンは直接フォールバック(Aurora)へ
        self.pending_switch_request = False

    def turn(self, master_utterance: str, brain: Brain) -> IntakeResult:
        """Brainを明示指定して1ターン処理する（ルーティングなし。Phase1/2互換）。"""
        pack = self._build_pack(master_utterance)
        raw_report = brain.converse(pack)
        return self._process_turn(master_utterance, raw_report)

    def turn_routed(
        self,
        master_utterance: str,
        *,
        now: datetime,
        is_alive: Callable[[str], bool] | None = None,
    ) -> IntakeResult:
        """§3.2の決定論チェックリストでBrainを選び、§3.5のフォールバック作法込みで1ターン処理する。"""
        if not (self.registry and self.quota_ledger is not None and self.routing_rules and self.brains):
            raise RuntimeError("turn_routedにはregistry/quota_ledger/routing_rules/brainsが必要")

        by_name = {e.name: e for e in self.registry}
        fallback_entry = next(e for e in self.registry if e.role == "fallback")

        escalate_requested = self.current_tier == "escalation"
        chosen_name = decide_brain(
            registry=self.registry,
            quota_ledger=self.quota_ledger,
            routing_rules=self.routing_rules,
            master_utterance=master_utterance,
            now=now,
            switch_requested=self.pending_switch_request,
            escalate_requested=escalate_requested,
            is_alive=is_alive,
        )
        self.pending_switch_request = False

        pack = self._build_pack(master_utterance)

        try:
            raw_report = self.brains[chosen_name].converse(pack)
            used_name = chosen_name
        except Exception:  # noqa: BLE001
            # §3.5: 通信エラー・弾切れ・クラウドの拒否 → 同ターンでAuroraが代打
            if by_name[chosen_name].location == "cloud":
                self.routing_rules.tighten(master_utterance)
            used_name = fallback_entry.name
            raw_report = self.brains[used_name].converse(pack)

        self.quota_ledger.record_use(used_name, now=now)

        result = self._process_turn(master_utterance, raw_report)

        self._update_tier(by_name.get(used_name), result)
        self._update_switch_request(result)

        return result

    def _update_tier(self, used_entry: BrainEntry | None, result: IntakeResult) -> None:
        if used_entry is None:
            return
        self_assessment = result.report.self_assessment
        over_capacity = self_assessment.over_capacity if self_assessment else False
        if used_entry.role == "primary" and over_capacity:
            self.current_tier = "escalation"
        elif used_entry.role == "escalation" and not over_capacity:
            self.current_tier = "primary"

    def _update_switch_request(self, result: IntakeResult) -> None:
        for fusen in result.accepted_fusen:
            if fusen.kind == "交代要請":
                self.pending_switch_request = True

    def _build_pack(self, master_utterance: str):
        recalled_memories = (
            self.memory_store.recall(master_utterance, top_k=RECALL_TOP_K) if self.memory_store else None
        )
        return build_context_pack(
            persona_text=self.persona_text,
            absolute_rules=self.absolute_rules,
            session=self.session,
            master_utterance=master_utterance,
            recalled_memories=recalled_memories,
        )

    def _process_turn(self, master_utterance: str, raw_report: dict) -> IntakeResult:
        result = process_report(
            raw_report,
            emotion=self.emotion,
            relationship=self.relationship,
            thresholds=self.thresholds,
        )

        self.session.add_turn(Turn(speaker="master", text=master_utterance))
        self.session.add_turn(Turn(speaker="serina", text=result.report.reply))

        if self.memory_store:
            for fusen in result.accepted_fusen:
                if fusen.kind != "記憶候補":
                    continue
                review = review_candidate(
                    fusen,
                    session=self.session,
                    store=self.memory_store,
                    thresholds=self.thresholds,
                    session_candidate_count=self.session_candidate_count,
                )
                if review.accepted:
                    self.session_candidate_count += 1

        return result
