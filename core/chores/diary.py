"""日記生成フロー。設計書 §4.5

夜間放出時（その日の最終セッション終了時）に、その日の記憶材料（蒸留済み断片＝採用された
記憶候補。§4.1「記憶DBに書き込めるのはこのライン一本」により両者は同じ集合）と気分の軌跡
（`EmotionState.summarize_trajectory()`）から一人称の日記を書く。

書き手分岐（旧: 材料が非機微のみ→上位モデル／機微を含む日→Aurora）は、§9.3で
lane="local"固定に変更した。裏方便のcloud車線が永久退役したため（会話文・その要約を
クラウドへ送らない確定方針・議題2.5）、以前は「機微査定が積み残しの後ろに並び当日中に
grade0/1へ落ちることは実運用では起きない」との理由でcloud分岐が実質death-branchだった
上に、Gemini退役後は「非機微な日の日記が生成されなくなるバグ」として顕在化していた
（chief=Fableレビューで発見。合意台帳 §9.3）。

日記本文は等級A（忘却対象外）・レガシー列 sensitivity_grade=2 固定で保存する
（読取ロジックは退役済み。列互換のためのデフォルト書き込み）。

電源断耐性: LLM呼び出し失敗・空応答時はDBを更新しない（次回の夜間放出機会に持ち越す。
distillation.pyと同じ思想）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from serina.core.memory.protection import ChangeLog, ChangeReport
from serina.core.memory.store import MemoryRecord, MemoryStore
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
    """書き手分岐（§4.5・§9.3改訂）。裏方便のcloud車線は永久退役したため常にlocal固定。

    routing_rules引数は呼び出し側との互換のため残す（機微判定そのものは行わない。
    §9.3: 会話文・その要約をクラウドへ送らない確定方針のため、機微か否かに関わらず
    書き手はlocalの1車線のみ）。
    """
    return "local"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_and_save_diary(
    memory_store: MemoryStore,
    *,
    material: DiaryMaterial,
    routing_rules: RoutingRules,
    lane_call_fns: dict[str, Callable[[str], str]],
    change_log: ChangeLog,
) -> DiaryOutcome:
    """材料から日記を1本生成しDBへ保存する（§4.5）。

    材料が空（当日1件も記憶が無く気分の動きも無い）なら生成しない（書くことが無い日に
    空疎な日記を量産しない）。

    2026-07-18: 書き手はlocalの1車線のみ（§9.3）のため、旧cloud車線の残弾台帳
    （quota_ledger/cloud_quota）ゲートは削除した（呼ばれることのない死に枝だった）。
    """
    if material.is_empty():
        return DiaryOutcome(generated=False, reason="材料なし")

    lane = determine_writer_lane(material, routing_rules=routing_rules)
    call_fn = lane_call_fns.get(lane)
    if call_fn is None:
        return DiaryOutcome(generated=False, lane=lane, reason=f"車線{lane}のcall_fn未設定")

    prompt = build_diary_prompt(material)
    try:
        diary_text = call_fn(prompt).strip()
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
