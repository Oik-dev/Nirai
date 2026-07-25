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

EPISODIC_MEMORY_TYPE = "episodic"
DIARY_PROTECTION_GRADE = "A"
DIARY_SENSITIVITY_GRADE = 2

def _diary_format_instruction(day_label: str) -> str:
    """日記本文の発注指示。day_labelで対象日を明示する（既定は「今日」）。

    2026-07-25是正(I-3): キャッチアップで過去日分を生成する回に「今日」固定文言のまま
    発注すると、実際は数日前の出来事なのに「今日」の日記として書かれてしまう
    （serina-code-reviewer 持ち越し指摘）。day_labelに対象Serina日を渡すことで防ぐ。
    """
    return (
        f"以下はセリナの{day_label}の記憶材料（出来事の断片）と気分の軌跡です。\n"
        "これを元に、一人称視点であなた自身が自然に思い出す形で書いてください。\n"
        "人が記憶を想起するように、印象に残った場面・感じたこと・感情が揺れた瞬間を自由な長さ・構成で書いてください。\n"
        "本文中に個人の発言を書く/引用する際は、それがマスターの言葉なのか、セリナの言葉なのかを区別できるように書き、これを混同しないでください。\n"
        "説明文や前置きは不要です。本文のみを返してください。"
    )


@dataclass(frozen=True)
class DiaryMaterial:
    """日記材料一式。

    target_date: 対象Serina日（ISO日付文字列、例"2026-07-20"）。Noneなら「今日」扱い
    （2026-07-25是正I-3: キャッチアップで過去日分を生成する際に指定する）。
    """

    memories: list[MemoryRecord]
    mood_summary: str
    target_date: str | None = None

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
    memory_store: MemoryStore,
    *,
    since_iso: str,
    until_iso: str | None = None,
    mood_summary: str,
    target_date: str | None = None,
) -> DiaryMaterial:
    """当日分の記憶（蒸留断片＝採用記憶候補）と気分の軌跡を集める。

    `since_iso`（当日の始まりのUTC ISO時刻）の決定はアプリ層の責務（§2.4と同じ配線思想。
    ローカル暦日とUTCのズレをどう扱うかはタイムゾーン設定を持つ呼び出し側が決める）。
    `until_iso`（当日の終わり＝次のSerina日の開始）を渡すと、その範囲内だけに材料を絞る
    （§4.5「Serina日ごとに1本」。長期間未起動後の初回起動で複数日分が1本に混ざる事故の防止）。
    省略時（None）は従来通り無制限。
    `target_date`はプロンプト・保存時刻に使う対象Serina日（省略時は「今日」扱い）。
    """
    memories = memory_store.list_memories_since(
        since_iso=since_iso, until_iso=until_iso, exclude_type=EPISODIC_MEMORY_TYPE,
    )
    return DiaryMaterial(memories=memories, mood_summary=mood_summary, target_date=target_date)


def build_diary_prompt(material: DiaryMaterial) -> str:
    """材料から日記発注プロンプトを組み立てる。target_dateがあれば「今日」の代わりに使う。"""
    day_label = material.target_date or "今日"
    fragments = "\n".join(f"- {m.content}" for m in material.memories) or f"（{day_label}は記憶に残る断片なし）"
    mood_summary = material.mood_summary.strip() or f"（{day_label}は気分の動きの記録なし）"
    return (
        f"【{day_label}の出来事の断片】\n{fragments}\n\n"
        f"【{day_label}の気分の軌跡】\n{mood_summary}\n\n"
        f"{_diary_format_instruction(day_label)}"
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
    created_at: str | None = None,
) -> DiaryOutcome:
    """材料から日記を1本生成しDBへ保存する（§4.5）。

    材料が空（当日1件も記憶が無く気分の動きも無い）なら生成しない（書くことが無い日に
    空疎な日記を量産しない）。

    `created_at`省略時はDB既定（生成時刻）。キャッチアップで過去日分を生成する回は
    呼び出し側が対象Serina日の終わりを渡す（2026-07-25是正I-3: 生成時刻のまま保存すると
    対象日の日付が記録上残らない・serina-code-reviewer持ち越し指摘）。

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
        type=EPISODIC_MEMORY_TYPE,
        importance=0.8,
        sensitivity_grade=DIARY_SENSITIVITY_GRADE,
        protection_grade=DIARY_PROTECTION_GRADE,
        created_at=created_at,
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
