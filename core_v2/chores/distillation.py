"""蒸留ジョブの消化ロジック。設計書v2 §2.4(裏方便の二車線), §4.1(記憶の一生)。

宿題箱に積まれた「蒸留」ジョブ（会話ターンの細切れ断片）をBrainへ実発注し、
記憶候補（引用つき）を抽出、関所④(引用照合・重複チェック・上限)を経てDBへ書き込む。

§4.1「記憶DBに書き込めるのはこのライン一本だけ。裏口は存在させない」を実現するため、
蒸留ジョブの消化がここで記憶候補の唯一の生成源となる（会話中の即時DB書き込みは廃止済み。
DECISIONS 2026-07-11参照）。

時刻指定バッチは組まない。この関数を呼ぶタイミング（①セッション終了時 ②アイドル時
③次回起動時の朝礼）はアプリ層の責務（§2.4）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from serina.brains.contract.schema import Fusen
from serina.core_v2.chores.chore_box import ChoreBox
from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.intake.memory_review import review_candidate
from serina.core_v2.memory.store import MemoryStore
from serina.core_v2.state.session import SessionState, Turn

# 蒸留由来の新記憶は§4.6-2に倣い機微等級2(ローカルのみ)で開始する。
# 機微の実査定はAurora裏方便のアイドル仕事(§4.6-3)が別途行う（今回スコープ外）。
DISTILLED_MEMORY_SENSITIVITY_GRADE = 2

DISTILLATION_FORMAT_INSTRUCTION = """
以下は会話ログの断片です。この中から、長期記憶として残す価値がある事実・出来事・約束・情緒を、
会話ログからの一字一句の原文引用つきで抜き出してください。該当がなければ空配列で構いません。
必ず次のJSON形式のみをコードブロックで返すこと（前後に説明文を含めてもよいが、JSON本体は改変しないこと）:
```json
{
  "candidates": [
    {"quote": "会話ログからの一字一句の引用", "content": "記憶として保存する文", "type": "fact", "importance": 0.5, "confidence": 0.8}
  ]
}
```
""".strip()


class DistillationParseError(Exception):
    """蒸留発注の応答からJSONを抽出できなかったことを示す例外。"""


@dataclass(frozen=True)
class JobOutcome:
    """1件の蒸留ジョブを消化した結果。"""

    job_id: int
    accepted: int
    rejected: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConsumptionSummary:
    """消化バッチ全体の結果（app層のログ・裏方便レポートに使う）。"""

    processed: list[JobOutcome] = field(default_factory=list)
    skipped_no_lane: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)

    @property
    def total_accepted(self) -> int:
        return sum(outcome.accepted for outcome in self.processed)


def build_distillation_prompt(turns: list[dict]) -> str:
    """蒸留ジョブのpayload["turns"]から発注プロンプトを組み立てる。"""
    lines = "\n".join(f"{t['speaker']}: {t['text']}" for t in turns)
    return f"【会話ログの断片】\n{lines}\n\n{DISTILLATION_FORMAT_INSTRUCTION}"


def _extract_candidates(response_text: str) -> list[dict]:
    match = re.search(r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    candidate_text = match.group(1) if match else response_text
    try:
        data = json.loads(candidate_text)
    except json.JSONDecodeError as e:
        raise DistillationParseError(f"蒸留応答からJSONを抽出できない: {e}") from e
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        raise DistillationParseError("candidatesがlist形式でない")
    return candidates


def _session_from_turns(turns_payload: list[dict]) -> SessionState:
    """関所④の引用照合は、蒸留ジョブに積まれた断片そのものを会話ログとして使う
    （消化時点でCoreの現在セッションは既に別物になっている可能性があるため）。"""
    session = SessionState()
    for t in turns_payload:
        session.add_turn(Turn(speaker=t["speaker"], text=t["text"]))
    return session


def consume_pending_distillation_jobs(
    chore_box: ChoreBox,
    *,
    memory_store: MemoryStore,
    thresholds: ThresholdsConfig,
    lane_call_fns: dict[str, Callable[[str], str]],
    limit: int | None = None,
) -> ConsumptionSummary:
    """宿題箱の「蒸留」ジョブを消化する（§2.4機会駆動: 呼び出しタイミングはアプリ層の責務）。

    lane_call_fns: {"local": Auroraの生テキスト呼び出し, "cloud": Geminiの余り弾の生テキスト呼び出し}。
    対応するcall_fnが無いlaneのジョブはpendingのまま残す（次回の消化機会に回す。会話優先の思想と同じ:
    処理できない宿題は無理に処理しない）。
    LLM呼び出し・JSON解釈が失敗したジョブもpendingのまま残す（電源断耐性と同じ思想。1件単位で
    中断・再挑戦できる。§2.4「会話優先。裏方の仕事は1件単位で中断可能に作る」）。

    1バッチあたりの記憶化件数上限（§2.5, thresholds.memory_max_candidates_per_session）は、
    このバッチ内で走らせるローカルカウンタで近似する（Core.session_candidate_countとは現時点で
    連動しない。会話セッションと蒸留消化バッチは別物になり得るため。DECISIONS参照）。
    """
    summary_processed: list[JobOutcome] = []
    skipped_no_lane: list[int] = []
    failed: list[int] = []
    batch_candidate_count = 0
    confidence_threshold = thresholds.confidence_threshold_for("記憶候補")

    jobs = chore_box.pending(kind="蒸留", limit=limit)
    for job in jobs:
        call_fn = lane_call_fns.get(job.lane)
        if call_fn is None:
            skipped_no_lane.append(job.id)
            continue

        try:
            response_text = call_fn(build_distillation_prompt(job.payload["turns"]))
            candidates = _extract_candidates(response_text)
        except Exception:  # noqa: BLE001
            failed.append(job.id)
            continue

        job_session = _session_from_turns(job.payload["turns"])
        accepted = 0
        rejected: list[str] = []
        for raw in candidates:
            if not isinstance(raw, dict):
                rejected.append("不正な候補形式")
                continue
            confidence = raw.get("confidence", 0.0)
            if not isinstance(confidence, (int, float)) or float(confidence) < confidence_threshold:
                rejected.append("確信度不足")
                continue

            fusen = Fusen(
                kind="記憶候補",
                version=1,
                content={
                    "quote": raw.get("quote", ""),
                    "content": raw.get("content", ""),
                    "type": raw.get("type", "fact"),
                    "importance": raw.get("importance", 0.5),
                    "sensitivity_grade": DISTILLED_MEMORY_SENSITIVITY_GRADE,
                },
                confidence=float(confidence),
            )
            review = review_candidate(
                fusen,
                session=job_session,
                store=memory_store,
                thresholds=thresholds,
                session_candidate_count=batch_candidate_count,
            )
            if review.accepted:
                accepted += 1
                batch_candidate_count += 1
            else:
                rejected.append(review.reason)

        # ジョブ自体の処理(LLM発注・解釈)は成功したのでmark_done。個々の候補が
        # 関所で棄却されても、それは正常な審査結果であり再試行対象ではない。
        chore_box.mark_done(job.id)
        summary_processed.append(JobOutcome(job_id=job.id, accepted=accepted, rejected=rejected))

    return ConsumptionSummary(
        processed=summary_processed,
        skipped_no_lane=skipped_no_lane,
        failed=failed,
    )
