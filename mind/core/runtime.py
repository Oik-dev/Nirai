"""Core本体。設計書 第1章〜第2章, §3(ルーティング), §4(記憶)。

会話 → 思い出す（長期記憶。浮かばなければ黙る） → 文脈パック組み立て → Brain選択・呼び出し（返答と、返答のあとの評価）
 → 関所 → セッション（手元の会話の流れ）へ記録。会話を記録したら、評価で気持ちを動かして気持ちの記録に残す（feel）。
会話はアプリ層（app/server.py）がイデアの生ログに記録し、記憶のページは眠りの間に書く（core/memory/sleep.py）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from mind.brains.contract.schema import CloudRejectionError, ContractFormatError, validate_report
from mind.core.chores.idle_policy import PulseCandidate
from mind.core.chores.pulse import PulseGenerationContext, generate_pulse_message
from mind.core.config import ThresholdsConfig
from mind.core import debug_log
from mind.core.context.pack import build_context_pack
from mind.core.feeling.feelings import Feelings
from mind.core.intake.advisor_tools import (
    AdvisorToolOutcome,
    execute_advisor_tool_calls,
    execute_tavily_search,
)
from mind.core.intake.gate import IntakeResult, process_report
from mind.core.lifelog import Position
from mind.core.persona.blade import apply_visible_brake
from mind.core.memory.memory import Memory
from mind.core.memory.recall import Cue
from mind.core.memory.relation import render_for_pack as relation_for_pack
from mind.core.memory.structure import JST, MASTER_NAME
from mind.core.memory.waking import render_for_pack
from mind.core.routing.advisor_force import plan_forced_advisor
from mind.core.routing.decision import decide_brain
from mind.core.routing.quota_ledger import QuotaLedger
from mind.core.routing.registry import BrainEntry
from mind.core.routing.tavily_rules import decide_tavily_search
from mind.core.routing.think_rules import plan_think
from mind.core.state.routing_rules import RoutingRules
from mind.core.state.session import SessionState, Turn
from mind.skills.gemini_advisor.skill import GeminiAdvisorSkill
from mind.skills.tavily_search.skill import TavilySearchSkill

logger = logging.getLogger(__name__)

RECALL_RECENT_TURNS = 4  # 思い出す手がかりにする直前の会話（記憶テストと同じ）

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

# citations（画面のリンク）へ載せてよいURLのスキーマallowlist。外部（Tavily）由来の
# 未検証コンテンツが初めてクリック可能なhrefになる経路のため、Core側で境界を敷く
# （serina-code-reviewer指摘。javascript: 等の危険スキーマを最終防衛線として弾く）。
_SAFE_CITATION_URL_PREFIXES = ("http://", "https://")


def _is_safe_citation_url(url: str) -> bool:
    return isinstance(url, str) and url.strip().lower().startswith(_SAFE_CITATION_URL_PREFIXES)


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
    # none | gemini | gemini_miss | gemini_unavailable | tavily | tavily_miss
    kind: str = "none"
    why: str = ""


class Core:
    def __init__(
        self,
        persona_text: str,
        absolute_rules: str,
        thresholds: ThresholdsConfig,
        memory: Memory | None = None,
        feelings: Feelings | None = None,
        registry: list[BrainEntry] | None = None,
        quota_ledger: QuotaLedger | None = None,
        routing_rules: RoutingRules | None = None,
        brains: dict[str, Brain] | None = None,
        gemini_advisor: GeminiAdvisorSkill | None = None,
        tavily_search: TavilySearchSkill | None = None,
        warm: Callable[[], None] | None = None,
    ) -> None:
        self.persona_text = persona_text
        self.absolute_rules = absolute_rules
        self.thresholds = thresholds
        self.memory = memory
        self.feelings = feelings
        self.registry = registry
        self.quota_ledger = quota_ledger
        self.routing_rules = routing_rules
        self.brains = brains
        self.gemini_advisor = gemini_advisor
        self.tavily_search = tavily_search
        self.warm = warm  # 脳と思い出す道具を載せておく（暇な間に見回りが呼ぶ。factory が順を決める）
        self.session = SessionState()

    def turn(
        self,
        master_utterance: str,
        brain: Brain,
        *,
        now: datetime | None = None,
    ) -> IntakeResult:
        """Brainを明示指定して1ターン処理する（ルーティングなし。テスト用の口。本番はturn_routed）。"""
        turn_at = now or datetime.now(timezone.utc)
        pack = self._build_pack(master_utterance, self._recall(master_utterance, turn_at), now=turn_at)
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

        # 思い出すのは1回だけ。パックは候補Brainごとに組み直す
        remembered = self._recall(master_utterance, now)

        used_name, raw_report = self._obtain_valid_report(
            master_utterance,
            chosen_name,
            by_name,
            fallback_entry.name,
            remembered,
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

    def end_session(self, *, keep: Iterable[Turn] = ()) -> None:
        """セッション境界。手元の会話の流れ（SessionState）を新しくする（§2.6: セッション状態は「セッション中のみ」）。

        keep は新しい流れに残す発言（起動したときの、続いているセッションの発言。日界のあとも、まだ眠っていない今日の発言）。
        トリガーの判定はアプリ層の責務。
        """
        self.session = SessionState(turns=keep)

    def _obtain_valid_report(
        self,
        master_utterance: str,
        chosen_name: str,
        by_name: dict[str, BrainEntry],
        fallback_name: str,
        remembered: list[str],
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
                remembered,
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
                    reason="Gemini無効（APIキー無し等）",
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
                if not answer:
                    # 空回答は「聞いた体」の材料にしない（拘束条件5。契約上到達しない想定だが
                    # consultの戻り型は str | None で空文字を排除しないため下流で保険を掛ける）。
                    debug_log.emit(
                        kind="advisor_window", action="gemini_miss", why=forced_plan.why,
                        reason="Gemini応答が空文字",
                    )
                    return _AdvisorWindowResolution(
                        advisor_tool_outcome=outcome, kind="gemini_miss", why=forced_plan.why,
                    )
                debug_log.emit(kind="advisor_window", action="gemini_hit", why=forced_plan.why)
                return _AdvisorWindowResolution(
                    advisor_context_text=(
                        f"{GEMINI_MATERIAL_INSTRUCTION}\n\nGeminiからの回答:\n{answer}"
                    ),
                    advisor_tool_outcome=outcome,
                    kind="gemini",
                    why=forced_plan.why,
                )
            discard_reason = (
                str(outcome.discarded[0].get("reason") or "") if outcome.discarded else "不明"
            )
            debug_log.emit(
                kind="advisor_window", action="gemini_miss", why=forced_plan.why,
                reason=discard_reason,
            )
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
            debug_log.emit(
                kind="advisor_window", action="tavily_miss", why=decision.why,
                reason=outcome.reason or "不明",
            )
            return _AdvisorWindowResolution(kind="tavily_miss", why=decision.why)

        result = outcome.result
        material_lines = [result.answer] if result.answer else []
        material_lines.extend(
            item.get("snippet", "") for item in result.results if item.get("snippet")
        )
        material_text = "\n".join(material_lines) or "（該当情報なし）"
        citations = [
            {"url": item["url"]} for item in result.results
            if item.get("url") and _is_safe_citation_url(item["url"])
        ] or None
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
            validate_report(raw_report)
            return True
        except ContractFormatError:
            return False

    @staticmethod
    def _minimal_raw_report() -> dict:
        return {
            "reply": "うまく言葉にできなかったけど、ここにいるよ。",
            "self_assessment": {"over_capacity": False, "reason": "内部エラーのため安全側の既定応答"},
        }

    def _recall(self, master_utterance: str, now: datetime) -> list[str]:
        """長期記憶から、今の発言と直前の会話を手がかりに思い出す。浮かんだものの文（0件なら黙る）。

        同じ会話の中ですでに浮かんだページは、自然には浮かび直さない（core/memory/recall.py）。
        記憶が使えないとき（埋め込みの失敗など）は、何も思い出さずに会話を続ける。
        """
        if self.memory is None:
            return []
        recent = tuple((turn.speaker, turn.text) for turn in self.session.turns[-RECALL_RECENT_TURNS:])
        try:
            remembered = self.memory.recall(Cue(master_utterance, recent, now), already=self.session.recalled)
        except Exception as exc:  # noqa: BLE001
            logger.exception("思い出すのに失敗。何も思い出さずに続けます")
            debug_log.emit(kind="recall", action="error", error=type(exc).__name__, detail=str(exc))
            return []
        self.session.recalled |= {m.page_id for m in remembered}
        debug_log.emit(
            kind="recall",
            action="remembered" if remembered else "silent",
            pages=[m.page_id for m in remembered],
            activations=[round(m.activation, 2) for m in remembered],
        )
        return [m.text for m in remembered]

    def _now_self(self) -> str:
        """今の自分（目覚めたときに本人が書いたもの。core/memory/waking.py）。読めないときは、なしで会話を続ける。"""
        if self.memory is None:
            return ""
        try:
            return render_for_pack(self.memory.waking())
        except Exception as exc:  # noqa: BLE001
            logger.exception("今の自分を読めなかった。なしで続けます")
            debug_log.emit(kind="waking", action="error", error=type(exc).__name__, detail=str(exc))
            return ""

    def _now_relation(self) -> str:
        """マスターとのこと（眠りの間に本人が書き足してきたもの。core/memory/relation.py）。読めないときは、なしで会話を続ける。"""
        if self.memory is None:
            return ""
        try:
            return relation_for_pack(self.memory.relation(MASTER_NAME), today=datetime.now(JST).date())
        except Exception as exc:  # noqa: BLE001
            logger.exception("マスターとのことを読めなかった。なしで続けます")
            debug_log.emit(kind="relation", action="error", error=type(exc).__name__, detail=str(exc))
            return ""

    def _decide_deep_thinking(self, master_utterance: str, brain: Brain) -> bool:
        """think ON/OFF 判定。ルール先行（2026-07-20 応答高速化）。

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

    def _feeling_text(self, now: datetime) -> str:
        """文脈パックの⑤（core/feeling/feelings.py）。読めないときは、なしで会話を続ける。"""
        if self.feelings is None:
            return ""
        try:
            return self.feelings.for_pack(now)
        except Exception as exc:  # noqa: BLE001
            logger.exception("気持ちを読めなかった。なしで続けます")
            debug_log.emit(kind="feeling", action="error", error=type(exc).__name__, detail=str(exc))
            return ""

    def forget(self, erased: Collection[Position]) -> list[str]:
        """Masterが記録から消した会話の行（日のファイル名, 行番号）に拠っていたものを外す（設計書 §4.8）。

        記憶のページは外し（同じ出来事の残りは次の眠りで思い出し直す）、気持ちの記録はその行の言葉だけを消す（数は残る）。
        外したページの id を返す。
        """
        forgotten = self.memory.forget_lines(erased) if self.memory is not None else []
        if self.feelings is not None:
            self.feelings.forget(erased)
        return forgotten

    def feel(self, result: IntakeResult, *, source: Iterable[str], now: datetime) -> dict | None:
        """Masterが話したターンを記録したあとに呼ぶ。返答のあとの評価で気持ちを動かし、気持ちの記録に1行残す。

        source はそのターンの会話の記録の場所（発言と返事）。評価を聞けなかったターンも、Masterが来たことは残る。
        気持ちの記録に書けなくても、会話は続ける（そのターンの気持ちが残らないだけ）。
        """
        if self.feelings is None:
            return None
        try:
            return self.feelings.feel(result.appraisal, source=list(source), at=now)
        except Exception as exc:  # noqa: BLE001
            logger.exception("気持ちを記録できなかった。会話は続けます")
            debug_log.emit(kind="feeling", action="error", error=type(exc).__name__, detail=str(exc))
            return None

    def _build_pack(
        self,
        master_utterance: str,
        remembered: list[str],
        context_size: str | None = None,
        now: datetime | None = None,
        advisor_context_text: str = "",
    ):
        return build_context_pack(
            persona_text=self.persona_text,
            absolute_rules=self.absolute_rules,
            session=self.session,
            master_utterance=master_utterance,
            remembered=remembered,
            recent_turns_limit=self.thresholds.recent_turns_for(context_size),
            # turn_routedのnowを通す（気持ちの「いつのことか」を、テストが注入するnowと同じ時刻でそろえる）。
            feeling_text=self._feeling_text(now or datetime.now(timezone.utc)),
            advisor_context_text=advisor_context_text,
            self_text=self._now_self(),
            relation_text=self._now_relation(),
        )

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
        # セッション履歴・会話の記録（Turn.text）には一切混ぜない（下記 Turn 生成部参照）。
        citations = raw_report.pop("citations", None)
        if not isinstance(citations, list):
            citations = None

        result = process_report(raw_report, precomputed_advisor_outcome=precomputed)
        result.citations = citations

        # §3.3第3経路の前提: どのBrain（所在）が担当したターンかを刻む。
        # citationsはここで意図的に使わない（result.report.replyのみをTurnへ刻む。Phase D-6）。
        # tsは発話時刻のUTC（手元の会話の流れを、日界のあとも今日の分だけ残すときに使う）。
        turn_ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        self.session.add_turn(Turn(speaker="master", text=master_utterance, location=turn_location, ts=turn_ts))
        self.session.add_turn(Turn(speaker="serina", text=result.report.reply, location=turn_location, ts=turn_ts))
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
            feeling_text=self._feeling_text(datetime.now(timezone.utc)),
            self_text=self._now_self(),
            relation_text=self._now_relation(),
        )
        try:
            return generate_pulse_message(ctx, brain_call=raw_call)
        except Exception:  # noqa: BLE001
            return ""
