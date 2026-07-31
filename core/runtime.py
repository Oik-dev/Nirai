"""Core本体。設計書 第1章〜第2章, §3(ルーティング), §4(記憶接続)。

会話 → 想起(長期記憶) → 文脈パック組み立て → Brain選択・呼び出し
 → 関所①②③ → 状態更新 → 記憶候補の審査ライン(関所④) → セッションへ記録。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
    execute_tavily_search,
)
from serina.core.intake.gate import IntakeResult, apply_schedule_propose_facts, process_report
from serina.core.persona.blade import apply_visible_brake
from serina.core.memory.protection import ChangeLog
from serina.core.memory.recall_planner import (
    RecallBundle,
    merge_memory_recalls,
    plan_recall,
    resolve_facts_for_plan,
)
from serina.core.memory.store import MemoryStore
from serina.core.routing.advisor_force import plan_forced_advisor
from serina.core.routing.decision import decide_brain
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import BrainEntry
from serina.core.routing.tavily_rules import decide_tavily_search
from serina.core.routing.think_rules import plan_think
from serina.core.state.desire import DesireState
from serina.core.state.emotion import EmotionState
from serina.core.state.relationship import RelationshipState
from serina.core.state.routing_rules import RoutingRules
from serina.core.state.serina_day import SERINA_DAY_HOUR, serina_day_id
from serina.core.state.session import SessionState, Turn
from serina.skills.gemini_advisor.skill import GeminiAdvisorSkill
from serina.skills.tavily_search.skill import TavilySearchSkill

RECALL_TOP_K = 5

THINK_JUDGE_INSTRUCTION = """
会話生成前の判定です。深い思考（think）が必要かだけをJSONで返してください。
```json
{"needs_deep_thinking": false, "reason": "日本語1文"}
```
曖昧な雑談・感情吐露は false（速度優先）。数学・論理・多段推論・明示的な「考えて」要求は true。
"""

# 2026-07-31 Phase D: Gemini/Tavily窓口の無言統合パイプライン。Core が組み立てる
# 今回限りの指示欄（persona資産ではない。prompt/persona/には置かない。A-1参照）。
# 「近所のお姉さん」のキャラ付けはここに置く（人格固定ブロックを黙って編集しない歯止め）。
GEMINI_MATERIAL_INSTRUCTION = (
    "Geminiに相談して返ってきた答え。近所のお姉さんに聞いてきたような体裁で、"
    "自分の言葉で自然に伝えてよい。"
)
TAVILY_MATERIAL_INSTRUCTION = (
    "検索結果。自然に触れてよいが、URLや見出し文をそのまま書かない。"
    "出典はCoreが末尾に別途付与する。"
)
# 拘束条件5: 窓口に失敗・拒否・タイムアウトした場合、Voiceはその窓口を使った体で話さない。
# 呼んでいない（そもそも該当なし）場合も同じガードを常時添える。
NO_ADVISOR_MATERIAL_INSTRUCTION = (
    "今回はGeminiにも検索にも相談していない。聞いた・調べたという体で話さない。"
)


class Brain(Protocol):
    def converse(self, pack) -> dict: ...  # noqa: ANN001


@dataclass(frozen=True)
class _AdvisorWindowResolution:
    """Gemini/Tavily窓口の無言解決結果（Phase D 統合パイプライン）。

    Voice（converse）を呼ぶ前に確定させる（拘束条件4）。advisor_context_textは
    常に非空（材料が無いときはNO_ADVISOR_MATERIAL_INSTRUCTIONが入る＝拘束条件5）。
    """

    advisor_context_text: str = NO_ADVISOR_MATERIAL_INSTRUCTION
    citations: list[dict] | None = None
    advisor_tool_outcome: AdvisorToolOutcome | None = None
    kind: str = "none"  # none | gemini | gemini_miss | tavily | tavily_miss
    why: str = ""


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
        tavily_search: TavilySearchSkill | None = None,
        serina_day_boundary_hour: int = SERINA_DAY_HOUR,
        change_log: ChangeLog | None = None,
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
        self.tavily_search = tavily_search
        self.change_log = change_log
        # app_timing.toml の serina_day.boundary_hour と揃える（日記キャッチアップと同値）。
        self.serina_day_boundary_hour = serina_day_boundary_hour
        # §2.4: 蒸留の宿題は会話中に積む。フラグメント（小分け単位）に満ちるまでの一時蓄積
        self._pending_fragment: list[Turn] = []
        self.emotion = EmotionState(baselines=thresholds.emotion_baselines)
        self.desire = DesireState(
            refractory_seconds=thresholds.desire_refractory_seconds,
            decay_tau_seconds=thresholds.desire_decay_tau_seconds,
            discharge_level=thresholds.desire_discharge_level,
            fulfillment_level_threshold=thresholds.desire_fulfillment_level_threshold,
        )
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

        2026-07-30: この経路は_cool_emotionを呼ばないため_tick_desireも回らず、
        desire.level_before_tickは初期値/直近の復元値のまま進む（本番はturn_routedのみ
        使うため実害なし。レビューM-5）。
        """
        turn_at = now or datetime.now(timezone.utc)
        self.emotion.current_day = serina_day_id(
            turn_at, boundary_hour=self.serina_day_boundary_hour,
        ).isoformat()
        self.relationship.current_turn_at = turn_at
        pack = self._build_pack(master_utterance, now=turn_at)
        raw_report = brain.converse(pack)
        return self._process_turn(master_utterance, raw_report, now=turn_at)

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
            now=now,
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

        2026-07-31 Phase D: Gemini/Tavily窓口はループの外・候補選定の前に一度だけ解決する
        （拘束条件4: Voice=converseを呼ぶ前に窓口の結果を確定させる。同一ターンでの
        二重外聞き・二重検索も構造的に防げる）。ループ内はパック組み立て→converse 1回のみ。
        窓口解決自体はこのメソッドの try/except（§3.2最終防衛線）の外にあるため、
        ここで例外を握っておかないと沈黙契約が破れる（advisor指摘・completion-review前是正）。
        """
        try:
            window = self._resolve_advisor_window(master_utterance, chosen_name)
        except Exception:  # noqa: BLE001
            window = _AdvisorWindowResolution()  # 材料なし（既定のガード文のみ）で安全側へ

        candidates = [chosen_name] if chosen_name == fallback_name else [chosen_name, fallback_name]
        for name in candidates:
            entry = by_name[name]
            pack = self._build_pack(
                master_utterance,
                recall_bundle=recall_bundle,
                context_size=entry.context_size,
                now=now,
                advisor_context_text=window.advisor_context_text,
            )
            try:
                think = self._decide_deep_thinking(master_utterance, self.brains[name])
                # 注意: 代打（2周目）でもon_tokenを渡すため、1周目がストリーム途中で失敗した
                # 場合は画面上でトークンが重複しうる。実運用はBrain単一（候補1つ）で発生せず、
                # 復帰は呼び出し元の「done時に本文へ置き換え」で吸収する。
                raw_report = self._call_brain_converse(
                    self.brains[name], pack, think=think,
                    on_token=on_token, on_reply=on_reply,
                )
                if window.advisor_tool_outcome is not None:
                    raw_report = {
                        **raw_report,
                        "_precomputed_advisor_outcome": window.advisor_tool_outcome,
                    }
                if window.citations:
                    raw_report = {**raw_report, "citations": window.citations}
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

    def _resolve_advisor_window(
        self, master_utterance: str, chosen_name: str,
    ) -> _AdvisorWindowResolution:
        """Gemini/Tavily窓口を無言で解決する（Phase D 統合パイプライン）。

        保留文は出さない。結果が確定してからでないとVoice（converse）を呼ばない
        （拘束条件4）。GeminiとTavilyは排他（設計の骨子・D-1）: Gemini呼びかけが
        あればGeminiのみを試し、無ければTavily判定（合言葉ゼロ・毎発話）へ進む。
        判定へ供給する材料はmaster_utteranceのみ（Phase B契約）。
        """
        forced_plan = plan_forced_advisor(master_utterance)

        if forced_plan is not None:
            gemini_ready = self.gemini_advisor is not None and self.gemini_advisor.enabled
            if not gemini_ready:
                debug_log.emit(
                    kind="advisor_window", action="gemini_unavailable", why=forced_plan.why,
                )
                return _AdvisorWindowResolution(kind="gemini_unavailable", why=forced_plan.why)
            outcome = execute_advisor_tool_calls(
                [{"type": forced_plan.tool, "query": forced_plan.query}],
                self.gemini_advisor,
                routing_rules=self.routing_rules,
                turn_budget_seconds=self.thresholds.advisor_turn_budget_seconds,
            )
            if outcome.executed:
                answer = str(outcome.executed[0].get("answer") or "").strip()
                debug_log.emit(kind="advisor_window", action="gemini_hit", why=forced_plan.why)
                return _AdvisorWindowResolution(
                    advisor_context_text=(
                        f"{GEMINI_MATERIAL_INSTRUCTION}\n\nGeminiからの回答:\n{answer}"
                    ),
                    advisor_tool_outcome=outcome,
                    kind="gemini",
                    why=forced_plan.why,
                )
            debug_log.emit(kind="advisor_window", action="gemini_miss", why=forced_plan.why)
            return _AdvisorWindowResolution(
                advisor_tool_outcome=outcome, kind="gemini_miss", why=forced_plan.why,
            )

        judge_brain = self.brains.get(chosen_name) if self.brains else None
        if judge_brain is None:
            return _AdvisorWindowResolution(kind="none")

        decision = decide_tavily_search(master_utterance, judge_brain)
        if not decision.needs_search:
            return _AdvisorWindowResolution(kind="none", why=decision.why)

        outcome = execute_tavily_search(
            master_utterance, decision.query, self.tavily_search, routing_rules=self.routing_rules,
        )
        if not outcome.executed or outcome.result is None:
            debug_log.emit(kind="advisor_window", action="tavily_miss", why=decision.why)
            return _AdvisorWindowResolution(kind="tavily_miss", why=decision.why)

        result = outcome.result
        material_lines = [result.answer] if result.answer else []
        material_lines.extend(
            item.get("snippet", "") for item in result.results if item.get("snippet")
        )
        material_text = "\n".join(material_lines) or "（該当情報なし）"
        citations = [{"url": item["url"]} for item in result.results if item.get("url")] or None
        debug_log.emit(kind="advisor_window", action="tavily_hit", why=decision.why)
        return _AdvisorWindowResolution(
            advisor_context_text=f"{TAVILY_MATERIAL_INSTRUCTION}\n\n検索結果:\n{material_text}",
            citations=citations,
            kind="tavily",
            why=decision.why,
        )

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
        self.emotion.apply_time_cooling(
            now,
            tau_affect_seconds=self.thresholds.tau_affect_seconds,
            tau_mood_seconds=self.thresholds.tau_mood_seconds,
            baselines=self.emotion.baseline,
            tau_baseline_seconds=self.thresholds.tau_baseline_seconds,
            baseline_max=self.thresholds.emotion_baseline_max,
        )
        self._tick_desire(now)

    def _tick_desire(self, now: datetime) -> None:
        """欲求層の時計蓄積／未充足減衰（Task 3-2 / 3-4）。

        level が閾値以上のまま次 tick を迎えたら「満たされていない」とみなし減衰。
        不応期中は DesireState.tick 側で蓄積も減衰も停止。
        """
        from serina.core.state.desire import (
            baseline_comfort_factor_from_baseline,
            mood_factor_from_mood,
        )

        unfulfilled = self.desire.is_fulfillment_level_high()
        self.desire.tick(
            now,
            mood_factor_from_mood(self.emotion.mood),
            baseline_comfort_factor_from_baseline(self.emotion.baseline),
            unfulfilled_decay=unfulfilled,
        )

    def _build_pack(
        self,
        master_utterance: str,
        recalled_memories=None,  # noqa: ANN001
        recall_bundle: RecallBundle | None = None,
        context_size: str | None = None,
        now: datetime | None = None,
        advisor_context_text: str = "",
    ):
        bundled_facts: list[str] | None = None
        if recall_bundle is not None:
            recalled_memories = recall_bundle.memories
            bundled_facts = [fact.statement for fact in recall_bundle.bundled_facts]
        elif recalled_memories is None:
            recalled_memories = self._recall_memories(master_utterance)
        # Task 1-6: 開いている予定/記念日の窓を最大1件、【時間付き事実】へ差し込む
        schedule_line = self._open_schedule_fact_line(now)
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
            schedule_fact_line=schedule_line,
            recent_turns_limit=recent_turns_limit,
            emotion=self.emotion,
            desire=self.desire,
            relationship=self.relationship,
            thresholds=self.thresholds,
            # 2026-07-26 B1是正(serina-code-reviewer指摘M-1): turn_routedのnowを通す。
            # 省略時（now未指定・実時計）とテストが注入するnowとで経路が食い違わないよう、
            # 想起の相対日ラベル（core/context/memory_time.py）・マスター観測の鮮度判定
            # （core/context/relationship_render.py）を同じnowで決定論的に揃える。
            now=now,
            advisor_context_text=advisor_context_text,
        )

    def _open_schedule_fact_line(self, now: datetime | None) -> str | None:
        """窓が開いている予定/記念日を最大1件、パック用の一文にする（Task 1-6）。"""
        from serina.core.context.schedule_window import pick_open_schedule_fact
        from serina.core.memory.facts import FACT_CATEGORY_ANNIVERSARY, FACT_CATEGORY_SCHEDULE

        if not self.memory_store or now is None:
            return None
        facts = []
        facts.extend(self.memory_store.facts.list_active_facts_by_category(FACT_CATEGORY_SCHEDULE))
        facts.extend(self.memory_store.facts.list_active_facts_by_category(FACT_CATEGORY_ANNIVERSARY))
        picked = pick_open_schedule_fact(now, facts)
        if picked is None:
            return None
        fact, window = picked
        return f"[{window}] {fact.statement}"

    def _process_turn(
        self,
        master_utterance: str,
        raw_report: dict,
        turn_location: str | None = None,
        *,
        now: datetime | None = None,
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

        # 2026-07-31 Phase E: 旧「保留文→2通目」機構は退役済み。統合パイプライン（Phase D）は
        # 1ターンにつき1通で完結するため、followup_replyはもう生成されない。

        # 2026-07-31 Phase D-6: Tavily出典（citations）はCore所有の定型テンプレート＋URL文字列
        # のみで構成され、Voiceが生成した本文（reply）とは別フィールドとして届く。
        # 画面の注記として扱う契約のため、ここで raw_report から抜き取り、
        # セッション履歴・記憶蒸留の材料（Turn.text）には一切混ぜない（下記 Turn 生成部参照）。
        citations = raw_report.pop("citations", None)
        if not isinstance(citations, list):
            citations = None

        result = process_report(
            raw_report,
            emotion=self.emotion,
            relationship=self.relationship,
            thresholds=self.thresholds,
            memory_store=self.memory_store,
            precomputed_advisor_outcome=precomputed,
            desire=self.desire,
            now=now,
        )

        # §4.9 v5: 予定/記念日の propose_fact はターン確定後に関所が即時書き込む。
        # 日時抽出失敗時は何も書かず、通常の蒸留経路へ委ねる（機械的条件のみ）。
        # now は turn / turn_routed から明示注入（日付依存テストの再現性と年跨ぎ補正のため）。
        apply_schedule_propose_facts(
            master_utterance=master_utterance,
            memory_tool_outcome=result.memory_tool_outcome,
            memory_store=self.memory_store,
            change_log=self.change_log,
            now=now,
        )

        result.citations = citations

        # §3.3第3経路の前提: どのBrain（所在）が担当したターンかを刻む。
        # マスター発言も担当Brainの所在で刻む（その原文が既にそのBrainへ渡っているため）
        # citationsはここで意図的に使わない（result.report.replyのみをTurnへ刻む。Phase D-6）。
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

    def list_schedule_pulse_candidates(
        self,
        now: datetime,
        schedule_pulse_state: dict,
    ) -> list:
        """開いていて未発火の予定/記念日窓を Pulse 候補として返す。"""
        from serina.core.chores.idle_policy import build_schedule_candidates
        from serina.core.memory.facts import FACT_CATEGORY_ANNIVERSARY, FACT_CATEGORY_SCHEDULE

        if not self.memory_store:
            return []
        facts = []
        facts.extend(self.memory_store.facts.list_active_facts_by_category(FACT_CATEGORY_SCHEDULE))
        facts.extend(self.memory_store.facts.list_active_facts_by_category(FACT_CATEGORY_ANNIVERSARY))
        return build_schedule_candidates(
            now=now,
            facts=facts,
            schedule_pulse_state=schedule_pulse_state,
        )

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
