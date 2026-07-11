"""Core本体。設計書v2 第1章〜第2章, §3(ルーティング), §4(記憶接続)。

会話 → 想起(長期記憶) → 文脈パック組み立て → Brain選択・呼び出し
 → 関所①②③ → 状態更新 → 記憶候補の審査ライン(関所④) → セッションへ記録。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from serina.brains.contract.schema import CloudRejectionError, ContractFormatError, validate_report_lenient
from serina.core_v2.chores.chore_box import ChoreBox
from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.context.pack import build_context_pack
from serina.core_v2.intake.gate import IntakeResult, process_report
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
        chore_box: ChoreBox | None = None,
    ) -> None:
        self.persona_text = persona_text
        self.absolute_rules = absolute_rules
        self.thresholds = thresholds
        self.memory_store = memory_store
        self.registry = registry
        self.quota_ledger = quota_ledger
        self.routing_rules = routing_rules
        self.brains = brains
        self.chore_box = chore_box
        # §2.4: 蒸留の宿題は会話中に積む。フラグメント（小分け単位）に満ちるまでの一時蓄積
        self._pending_fragment: list[Turn] = []
        self.emotion = EmotionState()
        self.relationship = RelationshipState()
        self.session = SessionState()
        # NOTE: 即時DB書き込み(裏口)を廃止したため、現時点でこのカウンタを増やす経路はない
        # （記憶化件数の上限は蒸留消化ロジック側のバッチ内カウンタが別途担う。DECISIONS参照）。
        # end_session()でのリセットのみ残し、将来の統合方針は未確定のため据え置く。
        self.session_candidate_count = 0
        # §3.4: 昇格は自己申告があるまで持続する（毎ターン揮発しない）
        self.current_tier = "primary"
        # §3.4交代要請: 次ターンは直接フォールバック(Aurora)へ
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

        # 想起は宛先に依存しないため1回だけ。パックは候補Brainごとに宛先を確定して組み直す
        # （クラウド→ローカルのフォールバックで所在が変わるため。§3.3個人情報フィルタはCore専権）
        recalled_memories = self._recall_memories(master_utterance)

        used_name, raw_report = self._obtain_valid_report(
            master_utterance, chosen_name, by_name, fallback_entry.name, recalled_memories,
        )

        self.quota_ledger.record_use(used_name, now=now)

        result = self._process_turn(
            master_utterance, raw_report, turn_location=by_name[used_name].location,
        )

        self._update_tier(by_name.get(used_name), result)
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
        # §2.4 裏方便の二車線: 断片ごとの個人情報フィルタで振り分ける（2026-07-12実装）。
        # routing_rules未設定（テスト等）なら安全側デフォルト(local)を維持。
        lane = "local"
        if self.routing_rules is not None:
            fragment_text = "\n".join(t.text for t in fragment)
            lane = "local" if self.routing_rules.is_sensitive(fragment_text) else "cloud"
        return self.chore_box.enqueue("蒸留", lane=lane, payload=payload)  # type: ignore[union-attr]

    def end_session(self) -> list[int]:
        """セッション境界（§2.4の3トリガーのいずれか）。トリガー検知自体はアプリ層の責務。

        蒸留の宿題自体は会話中に器が満ちるたびに積んである（_flush_full_chore_fragments）。
        ここでは器に満たない端数（partial fragment）を最後に積み、session_candidate_countと
        SessionStateを次セッション用に初期化する（Phase3からの繰り越し課題。
        §2.6: セッション状態は「セッション中のみ」）。
        戻り値はここで新規に積んだ宿題のID一覧（端数がない・chore_box未設定なら空リスト）。
        """
        job_ids: list[int] = []
        if self.chore_box is not None and self._pending_fragment:
            job_ids.append(self._enqueue_chore_fragment(self._pending_fragment))
            self._pending_fragment = []

        self.session = SessionState()
        self.session_candidate_count = 0
        return job_ids

    def _obtain_valid_report(
        self,
        master_utterance: str,
        chosen_name: str,
        by_name: dict[str, BrainEntry],
        fallback_name: str,
        recalled_memories,  # noqa: ANN001
    ) -> tuple[str, dict]:
        """§3.2最終防衛線: どんな失敗（呼び出し例外・書式違反）が起きても契約書式を満たす報告書を返す。

        turn_routedがBrain側の異常でクラッシュ＝セリナが沈黙する事態を防ぐ（§3.2）。
        Aurora（最終脚）ですら書式違反や例外を起こしうる（§5.5-7: 既知の最大リスク）ため、
        全滅時は合成した最小限の報告書で確定させる。
        パックは候補ごとにその所在（cloud/local）を宛先として組む（§3.3: クラウド宛は
        機微記憶の間引き・ローカルターンの伏せ字、ローカル宛は全記憶を原文で載せる）。
        """
        candidates = [chosen_name] if chosen_name == fallback_name else [chosen_name, fallback_name]
        for name in candidates:
            pack = self._build_pack(
                master_utterance,
                destination_location=by_name[name].location,
                recalled_memories=recalled_memories,
            )
            try:
                raw_report = self.brains[name].converse(pack)
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

    def _recall_memories(self, master_utterance: str):
        if not self.memory_store:
            return None
        try:
            return self.memory_store.recall(master_utterance, top_k=RECALL_TOP_K)
        except Exception:  # noqa: BLE001
            # §2.4: 裏方（想起）が壊れても会話は壊れない。今回は記憶なしで進める
            return None

    def _build_pack(
        self,
        master_utterance: str,
        destination_location: str | None = None,
        recalled_memories=None,  # noqa: ANN001
    ):
        if recalled_memories is None:
            recalled_memories = self._recall_memories(master_utterance)
        return build_context_pack(
            persona_text=self.persona_text,
            absolute_rules=self.absolute_rules,
            session=self.session,
            master_utterance=master_utterance,
            recalled_memories=recalled_memories,
            destination_location=destination_location,
        )

    def _process_turn(
        self, master_utterance: str, raw_report: dict, turn_location: str | None = None,
    ) -> IntakeResult:
        result = process_report(
            raw_report,
            emotion=self.emotion,
            relationship=self.relationship,
            thresholds=self.thresholds,
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

        # §4.1「記憶DBに書き込めるのはこのライン一本だけ。裏口は存在させない」。
        # 即時便で届いた「記憶候補」付箋は、ここでDBへ直接書き込まない（旧・裏口。DECISIONS参照）。
        # 蒸留ジョブ（宿題箱→裏方便）が記憶候補の唯一の生成源であり、審査(review_candidate)は
        # core_v2/chores/distillation.pyの消化ロジックが担う。

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
