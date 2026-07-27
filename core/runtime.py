"""Core本体。設計書 第1章〜第2章, §3(ルーティング), §4(記憶接続)。

会話 → 想起(長期記憶) → 文脈パック組み立て → Brain選択・呼び出し
 → 関所①②③ → 状態更新 → 記憶候補の審査ライン(関所④) → セッションへ記録。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from serina.brains.contract.schema import CloudRejectionError, ContractFormatError, validate_report_lenient
from serina.core.chores.chore_box import ChoreBox
from serina.core.chores.idle_policy import PulseCandidate
from serina.core.chores.pulse import PulseGenerationContext, generate_pulse_message
from serina.core.config import ThresholdsConfig
from serina.core import debug_log
from serina.core.context.pack import build_context_pack
from serina.core.context.recall_neighbors import expand_recall_neighbors
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
from serina.core.routing.advisor_force import (
    FACT_LANE_HOLD_REPLY,
    ForcedAdvisorPlan,
    plan_forced_advisor,
)
from serina.core.routing.decision import decide_brain
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import BrainEntry
from serina.core.routing.think_rules import plan_think
from serina.core.state.emotion import EmotionState
from serina.core.state.relationship import RelationshipState
from serina.core.state.routing_rules import RoutingRules
from serina.core.state.serina_day import SERINA_DAY_HOUR, serina_day_id
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
        serina_day_boundary_hour: int = SERINA_DAY_HOUR,
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
        # app_timing.toml の serina_day.boundary_hour と揃える（日記キャッチアップと同値）。
        self.serina_day_boundary_hour = serina_day_boundary_hour
        # §2.4: 蒸留の宿題は会話中に積む。フラグメント（小分け単位）に満ちるまでの一時蓄積
        self._pending_fragment: list[Turn] = []
        self.emotion = EmotionState()
        self.relationship = RelationshipState()
        self.session = SessionState()

    def turn(
        self,
        master_utterance: str,
        brain: Brain,
        *,
        now: datetime | None = None,
    ) -> IntakeResult:
        """Brainを明示指定して1ターン処理する（ルーティングなし。Phase1/2互換）。

        宛先不明のため安全側（クラウド扱い: 機微等級2の記憶は載せない・ローカルターンは伏せる）で
        パックを組む。本番経路はturn_routed（registryの所在から宛先を確定して組む）。

        2026-07-26 Minor是正: turn_routedと同様に軌跡のSerina日タグとマスター観測時刻を
        付与する（未設定のまま積むと_day=Noneが永久に残る）。
        """
        turn_at = now or datetime.now(timezone.utc)
        self.emotion.current_day = serina_day_id(
            turn_at, boundary_hour=self.serina_day_boundary_hour,
        ).isoformat()
        self.relationship.current_turn_at = turn_at
        pack = self._build_pack(master_utterance, now=turn_at)
        raw_report = brain.converse(pack)
        return self._process_turn(master_utterance, raw_report)

    def turn_routed(
        self,
        master_utterance: str,
        *,
        now: datetime,
        is_alive: Callable[[str], bool] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reply: Callable[[str], None] | None = None,
    ) -> IntakeResult:
        """§3.2の決定論チェックリストでBrainを選び、§3.5のフォールバック作法込みで1ターン処理する。

        2026-07-18: 品質昇格機構は廃止済み（§9.2）。escalate_requestedは常にFalseで呼ぶ。
        fallback役が登録簿に存在しない構成（Brain単一運用）ではprimaryを代用する
        （§9.1: Brain全滅時は機械的な既定応答で「セリナは沈黙しない」を満たす）。

        on_token/on_reply（2026-07-20 応答高速化）: GUIストリーミング用。対応Brainのみ
        発火し、非対応Brainは従来どおり一括（呼び出し元は on_reply 未発火時のフォールバック
        表示を持つこと）。on_reply発火後の抽出・advisor・記憶処理は同ターン内で続行される。
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
            is_alive=is_alive,
        )

        # 2026-07-26 A3: 気分の軌跡へ付与するSerina日タグ（EmotionStateは時計を持たない方針）。
        # boundary_hourはapp_timingと揃える（日記キャッチアップとの日ズレ防止）。
        self.emotion.current_day = serina_day_id(
            now, boundary_hour=self.serina_day_boundary_hour,
        ).isoformat()
        # 2026-07-26 B1: マスター観測の取得時刻（RelationshipStateも時計を持たない方針）。
        self.relationship.current_turn_at = now
        # 想起は宛先に依存しないため1回だけ。パックは候補Brainごとに宛先を確定して組み直す
        self._cool_emotion(now)
        recall_bundle = self._recall_with_planner(master_utterance, now=now, chosen_name=chosen_name)

        used_name, raw_report = self._obtain_valid_report(
            master_utterance,
            chosen_name,
            by_name,
            fallback_entry.name,
            recall_bundle,
            now=now,
            on_token=on_token,
            on_reply=on_reply,
        )

        # 全滅時（合成の最小報告書）でもfallback名で記帳する。fallback役は無制限quota運用の
        # ため実害はないが、「呼んでいないのに記帳」ではなく「最終防衛線としてこのターンの
        # 担当に確定した」意味の記帳。
        self.quota_ledger.record_use(used_name, now=now)

        return self._process_turn(
            master_utterance,
            raw_report,
            turn_location=by_name[used_name].location,
        )

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
        on_token: Callable[[str], None] | None = None,
        on_reply: Callable[[str], None] | None = None,
    ) -> tuple[str, dict]:
        """§3.2最終防衛線: どんな失敗（呼び出し例外・書式違反）が起きても契約書式を満たす報告書を返す。

        turn_routedがBrain側の異常でクラッシュ＝セリナが沈黙する事態を防ぐ（§3.2）。
        fallback役（最終脚）ですら書式違反や例外を起こしうる（§5.5-7: 既知の最大リスク）ため、
        全滅時は合成した最小限の報告書で確定させる。
        パックは常にローカル Brain 向けに記憶原文で組む（cloud 宛間引きは退役済み。§3.3）。
        外聞き（advisor）は同一ターンで一度だけ実行する。契約違反→代打の再試行で
        同じ相談を二度外に出さない（advisor_consulted ガード）。
        """
        candidates = [chosen_name] if chosen_name == fallback_name else [chosen_name, fallback_name]
        advisor_consulted = False
        forced_plan = plan_forced_advisor(master_utterance)
        for name in candidates:
            entry = by_name[name]
            pack = self._build_pack(
                master_utterance,
                recall_bundle=recall_bundle,
                context_size=entry.context_size,
                now=now,
            )
            try:
                # 事実レーン: Voice に断定させず保留短文のみ。外聞きは Core が強制。
                # アドバイザー無効時は通常会話へ（保留だけ残して沈黙するのを避ける）。
                advisor_ready = (
                    self.gemini_advisor is not None and self.gemini_advisor.enabled
                )
                consulted = False
                if forced_plan is not None and advisor_ready:
                    debug_log.emit(
                        kind="fact_lane",
                        action="enter",
                        why=forced_plan.why,
                        tool=forced_plan.tool,
                    )
                    raw_report = self._fact_lane_hold_report(
                        forced_plan, on_token=on_token, on_reply=on_reply,
                    )
                    raw_report, consulted = self._apply_advisor_pipeline(
                        self.brains[name],
                        pack,
                        raw_report,
                        think=False,
                        allow_external=not advisor_consulted,
                    )
                    if not raw_report.get("followup_reply"):
                        debug_log.emit(
                            kind="fact_lane",
                            action="followup_miss",
                            why=forced_plan.why,
                            tool=forced_plan.tool,
                        )
                        raw_report = {
                            **raw_report,
                            "followup_reply": (
                                "外の情報まで届かなかったみたい。もう一度だけ聞いてくれる？"
                            ),
                        }
                    else:
                        debug_log.emit(
                            kind="fact_lane",
                            action="followup",
                            why=forced_plan.why,
                            tool=forced_plan.tool,
                        )
                    # 感情報告は答え方の分岐のあとで合流（手順1本）
                    raw_report = self._attach_emotion_fusen(
                        self.brains[name], pack, raw_report,
                    )
                else:
                    think = self._decide_deep_thinking(master_utterance, self.brains[name])
                    # 注意: 代打（2周目）でもon_tokenを渡すため、1周目がストリーム途中で失敗した
                    # 場合は画面上でトークンが重複しうる。実運用はBrain単一（候補1つ）で発生せず、
                    # 復帰は呼び出し元の「done時に本文へ置き換え」で吸収する。
                    raw_report = self._call_brain_converse(
                        self.brains[name], pack, think=think,
                        on_token=on_token, on_reply=on_reply,
                    )
                    # 通常会話は外聞きしない（旧自律第3発注は廃止）
                advisor_consulted = advisor_consulted or consulted
            except CloudRejectionError:
                # 会話 Brain のクラウド拒否→tighten は退役（会話はローカル固定）。
                # Advisor 側の拒否は skill.consult が None で握り、ここには来ない。
                continue
            except Exception:  # noqa: BLE001
                # 通信エラー・弾切れは同ターン代打のみ
                continue
            if self._is_contract_valid(raw_report):
                return name, raw_report

        return fallback_name, self._minimal_raw_report()

    @staticmethod
    def _fact_lane_hold_report(
        plan: ForcedAdvisorPlan,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reply: Callable[[str], None] | None = None,
    ) -> dict:
        """事実レーンの1通目: 固定保留のみ（数値・原因の断定なし）。GUI へは通常どおり流す。"""
        hold = FACT_LANE_HOLD_REPLY
        if on_token is not None:
            for ch in hold:
                on_token(ch)
        if on_reply is not None:
            on_reply(hold)
        return {
            "reply": hold,
            "fusen_list": [],
            "self_assessment": {
                "over_capacity": False,
                "reason": f"事実レーン保留（{plan.why}）",
            },
            "advisor_tool_calls": [
                {"type": plan.tool, "query": plan.query},
            ],
        }

    def _attach_emotion_fusen(self, brain: Brain, pack, raw_report: dict) -> dict:
        """事実レーン完了後に感情報告を合流させる。通常会話は converse 内で済み。"""
        extract = getattr(brain, "extract_emotion_fusen", None)
        if not callable(extract):
            return raw_report
        hold = raw_report.get("reply", "")
        followup = raw_report.get("followup_reply", "")
        if not isinstance(hold, str):
            hold = ""
        if not isinstance(followup, str):
            followup = ""
        serina_text = hold if not followup.strip() else f"{hold}\n\n{followup}"
        try:
            fusen_list = extract(pack, serina_text)
        except Exception:  # noqa: BLE001
            fusen_list = []
        if not isinstance(fusen_list, list):
            fusen_list = []
        return {**raw_report, "fusen_list": fusen_list}

    def _apply_advisor_pipeline(
        self,
        brain: Brain,
        pack,
        raw_report: dict,
        *,
        think: bool = False,
        allow_external: bool = True,
    ) -> tuple[dict, bool]:
        """提案→関所→advisor→2通目生成。失敗時は raw_report をそのまま返す（沈黙しない）。

        戻り値: (raw_report, 外聞きを実際に試みたか)。allow_external=False のときは
        外部呼び出しをせず、相談は理由付きで破棄する（同一ターンの二重外聞き防止）。

        2026-07-20 応答高速化: 旧「言い直し（replyの置換）」は退役。ストリーミング導入で
        1通目は既に画面表示済みのため、advisor結果は followup_reply（2通目メッセージ）
        として報告書に載せ、_process_turn がセッションへ刻み、GUI が追加吹き出しで届ける。
        """
        # 2026-07-26 Minor是正: fusen「道具使用」からのフォールバックは削除。
        # 外聞きは事実レーンが advisor_tool_calls を必ず非空で渡す経路のみ（A1）。
        calls, _ = parse_advisor_tool_calls(raw_report.get("advisor_tool_calls"))
        if not calls:
            return raw_report, False

        if not allow_external:
            outcome = AdvisorToolOutcome()
            for call in calls:
                outcome.discarded.append({
                    "tool": call.get("type") or call.get("tool"),
                    "query": call.get("query") or call.get("q") or "",
                    "reason": "同一ターンで外聞き実行済みのため再実行しない",
                })
            raw_report = {
                **raw_report,
                "advisor_tool_calls": [],
                "_precomputed_advisor_outcome": outcome,
            }
            return raw_report, False

        outcome = execute_advisor_tool_calls(
            calls,
            self.gemini_advisor,
            routing_rules=self.routing_rules,
            turn_budget_seconds=self.thresholds.advisor_turn_budget_seconds,
        )
        stage1_reply = raw_report.get("reply", "")
        if not isinstance(stage1_reply, str):
            stage1_reply = ""

        if outcome.executed:
            compose = getattr(brain, "compose_advisor_followup", None)
            if callable(compose):
                try:
                    followup = compose(
                        pack,
                        stage1_reply,
                        outcome.executed,
                        think=think,
                    )
                except Exception:  # noqa: BLE001
                    followup = ""
                if isinstance(followup, str) and followup.strip():
                    raw_report = {**raw_report, "followup_reply": followup.strip()}

        raw_report = {
            **raw_report,
            "advisor_tool_calls": [],
            "_precomputed_advisor_outcome": outcome,
        }
        return raw_report, True

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
        """think ON/OFF 判定。ルール先行（RecallPlanner同方式・2026-07-20 応答高速化）。

        規則で確信できる発話はLLMを呼ばず即決し、中間帯だけ judge へ相談する。
        曖昧・失敗時は false（速度優先。§3.1）。
        """
        decision = plan_think(master_utterance)
        if decision.think is not None:
            return decision.think
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
    def _call_brain_converse(  # noqa: ANN001
        brain: Brain,
        pack,
        *,
        think: bool = False,
        on_token: Callable[[str], None] | None = None,
        on_reply: Callable[[str], None] | None = None,
    ) -> dict:
        converse = getattr(brain, "converse", None)
        if not callable(converse):
            raise RuntimeError("Brain に converse がない")
        # 新→旧の順で署名を試す（callbacks非対応→think非対応の段階フォールバック）
        try:
            return converse(pack, think=think, on_token=on_token, on_reply=on_reply)
        except TypeError:
            pass
        try:
            return converse(pack, think=think)
        except TypeError:
            return converse(pack)

    def _cool_emotion(self, now: datetime) -> None:
        """前回記録から now までの空き時間だけ感情を冷ます（起動オフライン分も含む）。"""
        baselines = self.thresholds.emotion_baselines or {}
        self.emotion.apply_time_cooling(
            now,
            tau_affect_seconds=self.thresholds.tau_affect_seconds,
            tau_mood_seconds=self.thresholds.tau_mood_seconds,
            baselines=baselines,
        )

    def _build_pack(
        self,
        master_utterance: str,
        recalled_memories=None,  # noqa: ANN001
        recall_bundle: RecallBundle | None = None,
        context_size: str | None = None,
        now: datetime | None = None,
    ):
        bundled_facts: list[str] | None = None
        if recall_bundle is not None:
            recalled_memories = recall_bundle.memories
            bundled_facts = [fact.statement for fact in recall_bundle.bundled_facts]
        elif recalled_memories is None:
            recalled_memories = self._recall_memories(master_utterance)
        # 日記チャンクヒットを親近傍のつながった文章へ（活性化モデル自体は変更しない）
        if recalled_memories:
            recalled_memories = expand_recall_neighbors(self.memory_store, list(recalled_memories))
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
            recent_turns_limit=recent_turns_limit,
            emotion=self.emotion,
            relationship=self.relationship,
            thresholds=self.thresholds,
            # 2026-07-26 B1是正(serina-code-reviewer指摘M-1): turn_routedのnowを通す。
            # 省略時（now未指定・実時計）とテストが注入するnowとで経路が食い違わないよう、
            # 想起の相対日ラベル（core/context/memory_time.py）・マスター観測の鮮度判定
            # （core/context/relationship_render.py）を同じnowで決定論的に揃える。
            now=now,
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

        # 2026-07-20: advisor結果の2通目（_apply_advisor_pipelineが載せる）。関所は通さない
        # （1通目と同じくローカルBrainがpersona込みで生成した本文であり、信頼水準は同じ）。
        followup_reply = raw_report.pop("followup_reply", None)
        if not isinstance(followup_reply, str) or not followup_reply.strip():
            followup_reply = None

        result = process_report(
            raw_report,
            emotion=self.emotion,
            relationship=self.relationship,
            thresholds=self.thresholds,
            memory_store=self.memory_store,
            precomputed_advisor_outcome=precomputed,
        )

        # §3.3第3経路の前提: どのBrain（所在）が担当したターンかを刻む。
        # マスター発言も担当Brainの所在で刻む（その原文が既にそのBrainへ渡っているため）
        master_turn = Turn(speaker="master", text=master_utterance, location=turn_location)
        serina_turn = Turn(speaker="serina", text=result.report.reply, location=turn_location)
        self.session.add_turn(master_turn)
        self.session.add_turn(serina_turn)

        # 2通目もセリナの発話としてセッションに刻む（次ターンの文脈・蒸留材料に含める）
        followup_turn: Turn | None = None
        if followup_reply is not None:
            result.followup_reply = followup_reply
            followup_turn = Turn(speaker="serina", text=followup_reply, location=turn_location)
            self.session.add_turn(followup_turn)

        if self.chore_box is not None:
            # §2.4「会話中: Coreが蒸留の宿題（細切れ断片）を宿題箱に積む」。
            # セッション終了を待たず、器（fragment_turns）が満ちるたびに積む＝強制終了でも
            # 直前まで積んだ分は宿題箱に残り、次回起動時の朝礼（③）で回収できる（§2.4 line244）。
            self._pending_fragment.append(master_turn)
            self._pending_fragment.append(serina_turn)
            if followup_turn is not None:
                self._pending_fragment.append(followup_turn)
            self._flush_full_chore_fragments()
            # §4.10: propose_identity_edit は提案のみ。採否・適用は idle の revise_persona_block。
            self._enqueue_persona_revise_proposals(result)

        # §4.1「記憶DBに書き込めるのはこのライン一本だけ。裏口は存在させない」。
        # 即時便で届いた「記憶候補」付箋は、ここでDBへ直接書き込まない（旧・裏口。DECISIONS参照）。
        # 蒸留ジョブ（宿題箱→裏方便）が記憶候補の唯一の生成源であり、審査(review_candidate)は
        # core/chores/distillation.pyの消化ロジックが担う。

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
        """未回収約束の Pulse 候補用（protection_grade A/S の promise のみ）。

        2026-07-23のepisodic/semantic統合でtype="promise"は"semantic"へ畳まれたため、
        旧分類は`metadata.legacy_type`で引く（`tools/migrate_memory_types.py`参照）。
        """
        if not self.memory_store:
            return []
        records = self.memory_store.list_by_type_and_legacy_type(
            "semantic", legacy_type="promise", limit=50,
        )
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
