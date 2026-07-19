"""Core本体。設計書 第1章〜第2章, §3(ルーティング), §4(記憶接続)。

会話 → 想起(長期記憶) → 文脈パック組み立て → Brain選択・呼び出し
 → 関所①②③ → 状態更新 → 記憶候補の審査ライン(関所④) → セッションへ記録。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from serina.brains.contract.schema import CloudRejectionError, ContractFormatError, validate_report_lenient
from serina.core.chores.chore_box import ChoreBox
from serina.core.chores.idle_policy import PulseCandidate
from serina.core.chores.pulse import PulseGenerationContext, generate_pulse_message
from serina.core.config import ThresholdsConfig
from serina.core.context.pack import build_context_pack
from serina.core.intake.advisor_tools import (
    AdvisorToolOutcome,
    execute_advisor_tool_calls,
    parse_advisor_tool_calls,
)
from serina.core.intake.gate import IntakeResult, process_report
from serina.core.persona.blade import apply_visible_brake
from serina.core.memory.recall_planner import (
    RecallBundle,
    merge_memory_recalls,
    plan_recall,
    resolve_facts_for_plan,
)
from serina.core.memory.store import MemoryStore
from serina.core.routing.decision import decide_brain
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import BrainEntry
from serina.core.state.emotion import EmotionState
from serina.core.state.relationship import RelationshipState
from serina.core.state.routing_rules import RoutingRules
from serina.core.state.routing_rules import RoutingRules
from serina.core.state.session import SessionState, Turn
from serina.skills.gemini_advisor.skill import GeminiAdvisorSkill

RECALL_TOP_K = 5

THINK_JUDGE_INSTRUCTION = """
会話生成前の判定です。深い思考（think）が必要かだけをJSONで返してください。
```json
{"needs_deep_thinking": false, "reason": "日本語1文"}
```
曖昧な雑談・感情吐露は false（速度優先）。数学・論理・多段推論・明示的な「考えて」要求は true。
"""


class Brain(Protocol):
    def converse(self, pack) -> dict: ...  # noqa: ANN001


class Core:
    def __init__(
        self,
        persona_text: str,
        absolute_rules: str,
        thresholds: ThresholdsConfig,
        prefs_summary: str = "",
        relation_summary: str = "",
        memory_store: MemoryStore | None = None,
        registry: list[BrainEntry] | None = None,
        quota_ledger: QuotaLedger | None = None,
        routing_rules: RoutingRules | None = None,
        brains: dict[str, Brain] | None = None,
        chore_box: ChoreBox | None = None,
        gemini_advisor: GeminiAdvisorSkill | None = None,
    ) -> None:
        self.persona_text = persona_text
        self.absolute_rules = absolute_rules
        self.prefs_summary = prefs_summary
        self.relation_summary = relation_summary
        self.thresholds = thresholds
        self.memory_store = memory_store
        self.registry = registry
        self.quota_ledger = quota_ledger
        self.routing_rules = routing_rules
        self.brains = brains
        self.chore_box = chore_box
        self.gemini_advisor = gemini_advisor
        # §2.4: 蒸留の宿題は会話中に積む。フラグメント（小分け単位）に満ちるまでの一時蓄積
        self._pending_fragment: list[Turn] = []
        self.emotion = EmotionState()
        self.relationship = RelationshipState()
        self.session = SessionState()
        # §3.4交代要請: 次ターンは直接フォールバック役へ
        self.pending_switch_request = False

    def turn(self, master_utterance: str, brain: Brain) -> IntakeResult:
        """Brainを明示指定して1ターン処理する（ルーティングなし。Phase1/2互換）。

        宛先不明のため安全側（クラウド扱い: 機微等級2の記憶は載せない・ローカルターンは伏せる）で
        パックを組む。本番経路はturn_routed（registryの所在から宛先を確定して組む）。
        """
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
        """§3.2の決定論チェックリストでBrainを選び、§3.5のフォールバック作法込みで1ターン処理する。

        2026-07-18: 品質昇格機構は廃止済み（§9.2）。escalate_requestedは常にFalseで呼ぶ。
        fallback役が登録簿に存在しない構成（Qwen単一運用）ではprimaryを代用する
        （§9.1: Brain全滅時は機械的な既定応答で「セリナは沈黙しない」を満たす）。
        """
        if not (self.registry and self.quota_ledger is not None and self.routing_rules and self.brains):
            raise RuntimeError("turn_routedにはregistry/quota_ledger/routing_rules/brainsが必要")

        by_name = {e.name: e for e in self.registry}
        fallback_entry = next(
            (e for e in self.registry if e.role == "fallback"),
            next(e for e in self.registry if e.role == "primary"),
        )

        chosen_name = decide_brain(
            registry=self.registry,
            quota_ledger=self.quota_ledger,
            routing_rules=self.routing_rules,
            master_utterance=master_utterance,
            now=now,
            switch_requested=self.pending_switch_request,
            is_alive=is_alive,
        )
        self.pending_switch_request = False

        # 想起は宛先に依存しないため1回だけ。パックは候補Brainごとに宛先を確定して組み直す
        # （クラウド→ローカルのフォールバックで所在が変わるため。§3.3個人情報フィルタはCore専権）
        recall_bundle = self._recall_with_planner(master_utterance, now=now, chosen_name=chosen_name)

        used_name, raw_report = self._obtain_valid_report(
            master_utterance,
            chosen_name,
            by_name,
            fallback_entry.name,
            recall_bundle,
            now=now,
        )

        # 全滅時（合成の最小報告書）でもfallback名で記帳する。fallback役は無制限quota運用の
        # ため実害はないが、「呼んでいないのに記帳」ではなく「最終防衛線としてこのターンの
        # 担当に確定した」意味の記帳。
        self.quota_ledger.record_use(used_name, now=now)

        result = self._process_turn(
            master_utterance,
            raw_report,
            turn_location=by_name[used_name].location,
        )

        self._update_switch_request(result)

        return result

    def _flush_full_chore_fragments(self) -> None:
        """蓄積中の断片が器（fragment_turns）を満たすたびに宿題箱へ積む（§2.4 line226）。"""
        fragment_size = max(1, self.thresholds.chore_fragment_turns)
        while len(self._pending_fragment) >= fragment_size:
            fragment, self._pending_fragment = (
                self._pending_fragment[:fragment_size],
                self._pending_fragment[fragment_size:],
            )
            self._enqueue_chore_fragment(fragment)

    def _enqueue_chore_fragment(self, fragment: list[Turn]) -> int:
        payload = {"turns": [{"speaker": t.speaker, "text": t.text} for t in fragment]}
        # §9.3: 裏方便のcloud車線は永久退役。会話文・その要約をクラウドへ送らない
        # 確定方針（議題2.5）のため、機微判定に関わらずlocal固定。
        return self.chore_box.enqueue("蒸留", lane="local", payload=payload)  # type: ignore[union-attr]

    def end_session(self) -> list[int]:
        """セッション境界（§2.4の3トリガーのいずれか）。トリガー検知自体はアプリ層の責務。

        蒸留の宿題自体は会話中に器が満ちるたびに積んである（_flush_full_chore_fragments）。
        ここでは器に満たない端数（partial fragment）を最後に積み、
        SessionStateを次セッション用に初期化する（§2.6: セッション状態は「セッション中のみ」）。
        記憶化件数上限は蒸留ジョブ単位（§2.5）のため、ここでは数えない。
        戻り値はここで新規に積んだ宿題のID一覧（端数がない・chore_box未設定なら空リスト）。
        """
        job_ids: list[int] = []
        if self.chore_box is not None and self._pending_fragment:
            job_ids.append(self._enqueue_chore_fragment(self._pending_fragment))
            self._pending_fragment = []

        self.session = SessionState()
        return job_ids

    def _obtain_valid_report(
        self,
        master_utterance: str,
        chosen_name: str,
        by_name: dict[str, BrainEntry],
        fallback_name: str,
        recall_bundle: RecallBundle | None,
        *,
        now: datetime | None = None,
    ) -> tuple[str, dict]:
        """§3.2最終防衛線: どんな失敗（呼び出し例外・書式違反）が起きても契約書式を満たす報告書を返す。

        turn_routedがBrain側の異常でクラッシュ＝セリナが沈黙する事態を防ぐ（§3.2）。
        fallback役（最終脚）ですら書式違反や例外を起こしうる（§5.5-7: 既知の最大リスク）ため、
        全滅時は合成した最小限の報告書で確定させる。
        パックは候補ごとにその所在（cloud/local）を宛先として組む（§3.3: クラウド宛は
        機微記憶の間引き・ローカルターンの伏せ字、ローカル宛は全記憶を原文で載せる）。
        """
        candidates = [chosen_name] if chosen_name == fallback_name else [chosen_name, fallback_name]
        for name in candidates:
            entry = by_name[name]
            pack = self._build_pack(
                master_utterance,
                destination_location=entry.location,
                recall_bundle=recall_bundle,
                context_size=entry.context_size,
            )
            try:
                think = self._decide_deep_thinking(master_utterance, self.brains[name])
                raw_report = self._call_brain_converse(self.brains[name], pack, think=think)
                raw_report = self._apply_advisor_pipeline(
                    self.brains[name],
                    pack,
                    raw_report,
                    think=think,
                )
            except CloudRejectionError:
                # §3.5: クラウドの拒否（安全フィルタ）だけが振り分けルールのラチェットを研ぐ対象
                # 発話全文を鍵にする（意図的な高精度フロア）: この時点ではBrainからの報告書が
                # 存在しない（呼び出し自体が拒否された）ため、根拠語をBrainに書かせる通常経路
                # （センシティブ観測付箋 → _apply_sensitivity_observation）が使えない。
                # 全文一致は再ヒット率が低い代わりに誤爆もしない（適合率優先。ラチェットは
                # 二次防壁に過ぎず、一次防壁はdecide_brainの内容ベース判定＝DECISIONS 2026-07-11）。
                # 汎化はセンシティブ観測付箋（通常成功時にBrainが自発的に書く経路）に委ねる。
                if by_name[name].location == "cloud":
                    self.routing_rules.tighten(master_utterance)
                continue
            except Exception:  # noqa: BLE001
                # §3.5: 通信エラー・弾切れは同ターン代打のみ。ラチェットは研がない
                continue
            if self._is_contract_valid(raw_report):
                return name, raw_report
            # 契約書式違反も「クラウドの拒否」ではないためラチェット対象外（DECISIONS 2026-07-10繰り越し対応）

        return fallback_name, self._minimal_raw_report()

    def _apply_advisor_pipeline(
        self,
        brain: Brain,
        pack,
        raw_report: dict,
        *,
        think: bool = False,
    ) -> dict:
        """提案→関所→advisor→言い直し。失敗時は raw_report をそのまま返す（沈黙しない）。"""
        calls, _ = parse_advisor_tool_calls(raw_report.get("advisor_tool_calls"))
        if not calls:
            fusen_raw = raw_report.get("fusen_list")
            if isinstance(fusen_raw, list):
                from serina.core.intake.advisor_tools import advisor_calls_from_fusen

                calls = advisor_calls_from_fusen(fusen_raw)
        if not calls:
            return raw_report

        outcome = execute_advisor_tool_calls(
            calls,
            self.gemini_advisor,
            routing_rules=self.routing_rules,
        )
        stage1_reply = raw_report.get("reply", "")
        if not isinstance(stage1_reply, str):
            stage1_reply = ""

        if outcome.executed:
            rephrase = getattr(brain, "rephrase_with_advisor", None)
            if callable(rephrase):
                try:
                    final_reply = rephrase(
                        pack,
                        stage1_reply,
                        outcome.executed,
                        think=think,
                    )
                    raw_report = {**raw_report, "reply": final_reply}
                except Exception:  # noqa: BLE001
                    pass

        raw_report = {
            **raw_report,
            "advisor_tool_calls": [],
            "_precomputed_advisor_outcome": outcome,
        }
        return raw_report

    @staticmethod
    def _is_contract_valid(raw_report: dict) -> bool:
        try:
            validate_report_lenient(raw_report)
            return True
        except ContractFormatError:
            return False

    @staticmethod
    def _minimal_raw_report() -> dict:
        return {
            "reply": "うまく言葉にできなかったけど、ここにいるよ。",
            "fusen_list": [],
            "self_assessment": {"over_capacity": False, "reason": "内部エラーのため安全側の既定応答"},
        }

    def _update_switch_request(self, result: IntakeResult) -> None:
        for fusen in result.accepted_fusen:
            if fusen.kind == "交代要請":
                self.pending_switch_request = True

    def _recall_with_planner(
        self,
        master_utterance: str,
        *,
        now: datetime,
        chosen_name: str | None = None,
    ) -> RecallBundle | None:
        if not self.memory_store:
            return None
        try:
            judge_fn = None
            if chosen_name and self.brains:
                brain = self.brains.get(chosen_name)
                judge_method = getattr(brain, "judge", None)
                if callable(judge_method):
                    judge_fn = judge_method

            session_tail = self._session_tail_text()
            plan = plan_recall(
                master_utterance,
                session_tail=session_tail,
                now=now,
                judge=judge_fn,
            )
            primary = self.memory_store.recall(master_utterance, top_k=RECALL_TOP_K)
            extras = []
            for query in plan.queries:
                if query.type == "semantic":
                    extras.append(self.memory_store.recall(query.q, top_k=RECALL_TOP_K))
            memories = merge_memory_recalls(primary, *extras, top_k=RECALL_TOP_K)
            bundled_facts = resolve_facts_for_plan(plan, self.memory_store.facts)
            return RecallBundle(memories=memories, bundled_facts=bundled_facts, plan=plan)
        except Exception:  # noqa: BLE001
            return None

    def _session_tail_text(self, *, limit: int = 4) -> str:
        lines = [f"{turn.speaker}: {turn.text}" for turn in self.session.turns[-limit:]]
        return "\n".join(lines)

    def _recall_memories(self, master_utterance: str):
        if not self.memory_store:
            return None
        try:
            return self.memory_store.recall(master_utterance, top_k=RECALL_TOP_K)
        except Exception:  # noqa: BLE001
            return None

    def _decide_deep_thinking(self, master_utterance: str, brain: Brain) -> bool:
        """think ON/OFF 判定。曖昧・失敗時は false（速度優先）。"""
        judge = getattr(brain, "judge", None)
        if not callable(judge):
            return False
        prompt = f"{THINK_JUDGE_INSTRUCTION}\n\n【発話】\n{master_utterance}\n"
        try:
            result = judge(prompt)
            if not isinstance(result, dict):
                return False
            return bool(result.get("needs_deep_thinking", False))
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _call_brain_converse(brain: Brain, pack, *, think: bool = False) -> dict:  # noqa: ANN001
        converse = getattr(brain, "converse", None)
        if not callable(converse):
            raise RuntimeError("Brain に converse がない")
        try:
            return converse(pack, think=think)
        except TypeError:
            return converse(pack)

    def _build_pack(
        self,
        master_utterance: str,
        destination_location: str | None = None,
        recalled_memories=None,  # noqa: ANN001
        recall_bundle: RecallBundle | None = None,
        context_size: str | None = None,
    ):
        bundled_facts: list[str] | None = None
        if recall_bundle is not None:
            recalled_memories = recall_bundle.memories
            bundled_facts = [fact.statement for fact in recall_bundle.bundled_facts]
        elif recalled_memories is None:
            recalled_memories = self._recall_memories(master_utterance)
        recent_turns_limit = self.thresholds.recent_turns_for(context_size)
        return build_context_pack(
            persona_text=self.persona_text,
            absolute_rules=self.absolute_rules,
            prefs_summary=self.prefs_summary,
            relation_summary=self.relation_summary,
            session=self.session,
            master_utterance=master_utterance,
            recalled_memories=recalled_memories,
            bundled_facts=bundled_facts,
            destination_location=destination_location,
            recent_turns_limit=recent_turns_limit,
            routing_rules=self.routing_rules,
            emotion=self.emotion,
            thresholds=self.thresholds,
        )

    def _process_turn(
        self, master_utterance: str, raw_report: dict, turn_location: str | None = None,
    ) -> IntakeResult:
        filtered = bool(raw_report.get("safety_filtered"))
        if filtered:
            reply = raw_report.get("reply", "")
            if isinstance(reply, str):
                raw_report = {
                    **raw_report,
                    "reply": apply_visible_brake(
                        reply,
                        mode=self.thresholds.persona_blade_visible_brake_mode,
                        filtered=True,
                    ),
                }

        precomputed = raw_report.pop("_precomputed_advisor_outcome", None)
        if precomputed is not None and not isinstance(precomputed, AdvisorToolOutcome):
            precomputed = None

        result = process_report(
            raw_report,
            emotion=self.emotion,
            relationship=self.relationship,
            thresholds=self.thresholds,
            memory_store=self.memory_store,
            gemini_advisor=self.gemini_advisor,
            routing_rules=self.routing_rules,
            precomputed_advisor_outcome=precomputed,
        )

        # §3.3第3経路の前提: どのBrain（所在）が担当したターンかを刻む。
        # マスター発言も担当Brainの所在で刻む（その原文が既にそのBrainへ渡っているため）
        master_turn = Turn(speaker="master", text=master_utterance, location=turn_location)
        serina_turn = Turn(speaker="serina", text=result.report.reply, location=turn_location)
        self.session.add_turn(master_turn)
        self.session.add_turn(serina_turn)

        if self.chore_box is not None:
            # §2.4「会話中: Coreが蒸留の宿題（細切れ断片）を宿題箱に積む」。
            # セッション終了を待たず、器（fragment_turns）が満ちるたびに積む＝強制終了でも
            # 直前まで積んだ分は宿題箱に残り、次回起動時の朝礼（③）で回収できる（§2.4 line244）。
            self._pending_fragment.append(master_turn)
            self._pending_fragment.append(serina_turn)
            self._flush_full_chore_fragments()
            # §4.10: propose_identity_edit は提案のみ。採否・適用は idle の revise_persona_block。
            self._enqueue_persona_revise_proposals(result)

        # §4.1「記憶DBに書き込めるのはこのライン一本だけ。裏口は存在させない」。
        # 即時便で届いた「記憶候補」付箋は、ここでDBへ直接書き込まない（旧・裏口。DECISIONS参照）。
        # 蒸留ジョブ（宿題箱→裏方便）が記憶候補の唯一の生成源であり、審査(review_candidate)は
        # core/chores/distillation.pyの消化ロジックが担う。

        if self.routing_rules:
            for fusen in result.accepted_fusen:
                if fusen.kind != "センシティブ観測":
                    continue
                try:
                    self._apply_sensitivity_observation(fusen)
                except Exception:  # noqa: BLE001
                    # §2.4: 裏方（振り分けルールの学習）が壊れても会話は壊れない
                    continue

        return result

    def generate_pulse_text(self, candidate: PulseCandidate) -> str:
        """Pulse 文面を Brain で生成する（§3.6: 判定と生成の分離）。"""
        if not self.brains or not self.registry:
            return ""
        primary = next(e for e in self.registry if e.role == "primary")
        brain = self.brains.get(primary.name)
        if brain is None:
            return ""
        raw_call = getattr(brain, "raw_call", None)
        if not callable(raw_call):
            return ""
        ctx = PulseGenerationContext(
            candidate=candidate,
            persona_text=self.persona_text,
            absolute_rules=self.absolute_rules,
            prefs_summary=self.prefs_summary,
            relation_summary=self.relation_summary,
            emotion=self.emotion,
            thresholds=self.thresholds,
        )
        try:
            return generate_pulse_message(ctx, brain_call=raw_call)
        except Exception:  # noqa: BLE001
            return ""

    def list_promise_memories_for_pulse(self) -> list[tuple[int, str]]:
        """未回収約束の Pulse 候補用（protection_grade A/S の promise のみ）。"""
        if not self.memory_store:
            return []
        records = self.memory_store.list_by_type("promise", limit=50)
        return [
            (r.id, r.content)
            for r in records
            if r.protection_grade in ("A", "S")
        ]

    def _enqueue_persona_revise_proposals(self, result: IntakeResult) -> None:
        """propose_identity_edit を宿題箱へ積む（idle で revise_persona_block が適用）。"""
        from serina.core.chores.orchestrator import PERSONA_REVISE_CHORE_KIND

        outcome = result.memory_tool_outcome
        if outcome is None or self.chore_box is None:
            return
        for prop in outcome.proposals:
            if prop.get("tool") != "propose_identity_edit":
                continue
            if prop.get("status") != "pending_review":
                continue
            proposal = prop.get("proposal") or {}
            if not isinstance(proposal, dict):
                continue
            block_id = proposal.get("block_id") or proposal.get("target")
            new_content = (
                proposal.get("new_content")
                or proposal.get("content")
                or proposal.get("text")
            )
            if not isinstance(block_id, str) or not block_id.strip():
                continue
            if not isinstance(new_content, str) or not new_content.strip():
                continue
            reason = proposal.get("reason") or "Brain提案（propose_identity_edit）"
            self.chore_box.enqueue(
                PERSONA_REVISE_CHORE_KIND,
                lane="local",
                payload={
                    "block_id": block_id.strip(),
                    "new_content": new_content,
                    "reason": str(reason),
                    "mood_contaminated": bool(proposal.get("mood_contaminated", False)),
                },
            )

    def _apply_sensitivity_observation(self, fusen) -> None:  # noqa: ANN001
        """センシティブ観測付箋（§2.2）を振り分けルールへ反映する。

        §3.3.1の逆止弁: 厳しくなる方向（direction="不向き"）のみ自動反映。
        緩む方向（direction="平気"）はマスター承認が必須のため、ここでは反映しない
        （棄却ではなく、accepted_fusenとして受理済み＝無言破棄ではない。承認導線は将来対応）。
        """
        direction = fusen.content.get("direction")
        keywords = fusen.content.get("keywords")
        if direction != "不向き" or not isinstance(keywords, list):
            return
        for keyword in keywords:
            if isinstance(keyword, str) and keyword.strip():
                self.routing_rules.tighten(keyword.strip())
