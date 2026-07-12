"""日記生成フロー。設計書 §4.5

夜間放出時（その日の最終セッション終了時）に、その日の記憶材料（蒸留済み断片＝採用された
記憶候補。§4.1「記憶DBに書き込めるのはこのライン一本」により両者は同じ集合）と気分の軌跡
（`EmotionState.summarize_trajectory()`）から一人称の日記を書く。

書き手分岐（材料が非機微のみ→上位モデル／機微を含む日→Aurora）は、保存済みの
`sensitivity_grade`ではなく**その場で**`RoutingRules.is_sensitive()`を材料テキストへ
再評価して決める（2026-07-12 検証・マスター確認）。理由: 蒸留由来の新規記憶は§4.6-2に
より常にgrade=2で書き込まれ、機微査定(§4.6-3)は1件ずつのアイドル内職で既存858件の
積み残しの後ろに並ぶため、「当日の断片が同日中にgrade0/1へ落ちる」ことは実運用では
起きない。保存gradeを読む設計だと上位モデル分岐が死に枝になる。

日記本文は等級A（忘却対象外）・機微等級2固定で保存する。本文自体は完成した一人称の
文章のため化粧版は作らず安全側に倒す（§4.2「真の秘匿値は化粧版を作らず本文記載自体を
避ける」の精神を踏襲）。将来の機微査定バックログ(§4.6-3)の対象にはなる。

電源断耐性: LLM呼び出し失敗・空応答時はDBを更新しない（次回の夜間放出機会に持ち越す。
distillation.py/sensitivity_assessment.pyと同じ思想）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from serina.core.memory.protection import ChangeLog, ChangeReport
from serina.core.memory.store import MemoryRecord, MemoryStore
from serina.core.routing.quota_ledger import QuotaLedger, QuotaSpec
from serina.core.state.routing_rules import RoutingRules

DIARY_MEMORY_TYPE = "diary"
DIARY_PROTECTION_GRADE = "A"
DIARY_SENSITIVITY_GRADE = 2

DIARY_FORMAT_INSTRUCTION = """
以下はセリナの今日1日の記憶材料（出来事の断片）と気分の軌跡です。これを元に、セリナの
一人称視点で今日1日の日記を書いてください。「何があったか」の記録ではなく「どう感じた
一日だったか」の記録にしてください。説明文や前置きは不要です。日記本文のみを返してください。
""".strip()


@dataclass(frozen=True)
class DiaryMaterial:
    """日記材料一式。"""

    memories: list[MemoryRecord]
    mood_summary: str

    def is_empty(self) -> bool:
        return not self.memories and not self.mood_summary.strip()


@dataclass(frozen=True)
class DiaryOutcome:
    """日記生成の結果。"""

    generated: bool
    memory_id: int | None = None
    lane: str | None = None
    reason: str | None = None


def gather_diary_material(
    memory_store: MemoryStore, *, since_iso: str, mood_summary: str,
) -> DiaryMaterial:
    """当日分の記憶（蒸留断片＝採用記憶候補）と気分の軌跡を集める。

    `since_iso`（当日の始まりのUTC ISO時刻）の決定はアプリ層の責務（§2.4と同じ配線思想。
    ローカル暦日とUTCのズレをどう扱うかはタイムゾーン設定を持つ呼び出し側が決める）。
    """
    memories = memory_store.list_memories_since(since_iso=since_iso, exclude_type=DIARY_MEMORY_TYPE)
    return DiaryMaterial(memories=memories, mood_summary=mood_summary)


def build_diary_prompt(material: DiaryMaterial) -> str:
    """材料から日記発注プロンプトを組み立てる。"""
    fragments = "\n".join(f"- {m.content}" for m in material.memories) or "（今日は記憶に残る断片なし）"
    mood_summary = material.mood_summary.strip() or "（今日は気分の動きの記録なし）"
    return (
        f"【今日の出来事の断片】\n{fragments}\n\n"
        f"【今日の気分の軌跡】\n{mood_summary}\n\n"
        f"{DIARY_FORMAT_INSTRUCTION}"
    )


def determine_writer_lane(material: DiaryMaterial, *, routing_rules: RoutingRules) -> str:
    """書き手分岐（§4.5）。保存済みsensitivity_gradeは見ず、材料テキスト全体に
    `RoutingRules.is_sensitive()`をその場で再評価する（理由はモジュールdocstring参照）。
    非機微のみ→"cloud"（上位モデル）／機微を含む→"local"（Aurora）。
    """
    combined_text = "\n".join(m.content for m in material.memories)
    if routing_rules.is_sensitive(combined_text):
        return "local"
    return "cloud"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_and_save_diary(
    memory_store: MemoryStore,
    *,
    material: DiaryMaterial,
    routing_rules: RoutingRules,
    lane_call_fns: dict[str, Callable[[str], str]],
    change_log: ChangeLog,
    quota_ledger: QuotaLedger | None = None,
    cloud_quota: QuotaSpec | None = None,
) -> DiaryOutcome:
    """材料から日記を1本生成しDBへ保存する（§4.5）。

    材料が空（当日1件も記憶が無く気分の動きも無い）なら生成しない（書くことが無い日に
    空疎な日記を量産しない）。

    quota_ledger/cloud_quota: 2026-07-12追加。書き手分岐がcloud（会話用クラウドBrainと
    同じ"余り弾"）の場合、発注前に残弾台帳を確認する。弾切れ・分間制限中なら発注せず
    見送る（`outcome.generated=False`）。呼び出し側(`run_diary_generation`)はgenerated=False
    時にlast_diary_at・気分の軌跡を前進/消費しないため、次回の夜間放出/朝礼機会に
    安全に持ち越せる。
    """
    if material.is_empty():
        return DiaryOutcome(generated=False, reason="材料なし")

    lane = determine_writer_lane(material, routing_rules=routing_rules)
    call_fn = lane_call_fns.get(lane)
    if call_fn is None:
        return DiaryOutcome(generated=False, lane=lane, reason=f"車線{lane}のcall_fn未設定")

    if lane == "cloud" and quota_ledger is not None and cloud_quota is not None:
        if not quota_ledger.can_use(
            cloud_quota.name,
            daily_quota=cloud_quota.daily_quota,
            per_minute_quota=cloud_quota.per_minute_quota,
            now=datetime.now(timezone.utc),
        ):
            return DiaryOutcome(generated=False, lane=lane, reason="クラウド残弾切れ")

    prompt = build_diary_prompt(material)
    try:
        diary_text = call_fn(prompt).strip()
        if lane == "cloud" and quota_ledger is not None and cloud_quota is not None:
            quota_ledger.record_use(cloud_quota.name, now=datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001
        return DiaryOutcome(generated=False, lane=lane, reason="LLM呼び出し失敗")

    if not diary_text:
        return DiaryOutcome(generated=False, lane=lane, reason="空応答")

    memory_id = memory_store.add_memory(
        diary_text,
        type=DIARY_MEMORY_TYPE,
        importance=0.8,
        sensitivity_grade=DIARY_SENSITIVITY_GRADE,
        protection_grade=DIARY_PROTECTION_GRADE,
    )
    change_log.record(
        ChangeReport(
            timestamp=_utc_now_iso(),
            action="日記生成",
            target_id=memory_id,
            reason=f"書き手={lane}・材料{len(material.memories)}件",
            before=None,
            after=diary_text,
        )
    )
    return DiaryOutcome(generated=True, memory_id=memory_id, lane=lane)
