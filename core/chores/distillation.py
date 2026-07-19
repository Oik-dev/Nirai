"""蒸留ジョブの消化ロジック。設計書 §2.4(裏方便の二車線), §4.1(記憶の一生)。

宿題箱に積まれた「蒸留」ジョブ（会話ターンの細切れ断片）をBrainへ実発注し、
記憶候補（引用つき）を抽出、関所④(引用照合・重複チェック・上限)を経てDBへ書き込む。

§4.1「記憶DBに書き込めるのはこのライン一本だけ。裏口は存在させない」を実現するため、
蒸留ジョブの消化がここで記憶候補の唯一の生成源となる（会話中の即時DB書き込みは廃止済み。
DECISIONS 2026-07-11参照）。

時刻指定バッチは組まない。この関数を呼ぶタイミング（①セッション終了時 ②アイドル時
③次回起動時の朝礼）はアプリ層の責務（§2.4）。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from serina.brains.contract.schema import Fusen
from serina.core.chores.chore_box import ChoreBox
from serina.core.config import ThresholdsConfig
from serina.core.intake.memory_review import review_candidate
from serina.core.memory.protection import ChangeLog, ChangeReport
from serina.core.memory.store import MemoryStore
from serina.core.state.session import SessionState, Turn

DEFAULT_FAILURE_SHELVE_THRESHOLD = 3


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def turns_content_hash(turns: list[dict]) -> str:
    """蒸留冪等キー（合意台帳 §4-1）。turns 集合の正規化 content hash。"""
    canonical = json.dumps(turns, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# レガシー列互換: sensitivity_grade 列へのデフォルト書き込み（読取ロジックは退役済み）。
DISTILLED_MEMORY_SENSITIVITY_GRADE = 2

DISTILLATION_FORMAT_INSTRUCTION = """
以下は会話ログの断片です。この中から、長期記憶として残す価値がある事実・出来事・約束・情緒を、
会話ログからの一字一句の原文引用つきで抜き出してください。該当がなければ空配列で構いません。
必ず次のJSON形式のみをコードブロックで返すこと（前後に説明文を含めてもよいが、JSON本体は改変しないこと）:
```json
{
  "candidates": [
    {
      "quote": "会話ログからの一字一句の引用",
      "content": "記憶として保存する文",
      "type": "fact",
      "importance": 0.5,
      "confidence": 0.8,
      "fact": {
        "subject": "主語",
        "predicate": "述語",
        "object": "目的語",
        "statement": "自然文の事実",
        "status": "hypothesis"
      }
    }
  ]
}
```
fact オブジェクトは時間付き事実台帳向け（省略可）。根拠が弱い・冗談・仮説は status=hypothesis。
確信度が低い候補や構造化できない候補は fact を付けなくてよい。
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
    skipped_quota: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    lane_switched: list[int] = field(default_factory=list)
    shelved: list[int] = field(default_factory=list)

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
    change_log: ChangeLog | None = None,
    failure_shelve_threshold: int = DEFAULT_FAILURE_SHELVE_THRESHOLD,
    yield_check: Callable[[], bool] | None = None,
) -> ConsumptionSummary:
    """宿題箱の「蒸留」ジョブを消化する（§2.4機会駆動: 呼び出しタイミングはアプリ層の責務）。

    lane_call_fns: {"local": Qwenの生テキスト呼び出し}。cloud 車線は永久退役。
    レガシーで lane=cloud の残ジョブがあれば local へ振替して処理する。

    LLM呼び出し・JSON解釈・候補処理の例外はpendingのまま残し、失敗回数を記録する。
    同一ジョブが `failure_shelve_threshold`回（既定3）失敗したら棚上げ棚へ移動し、
    change_logへ日本語レポートを残す（原則1: 無言破棄禁止）。

    1蒸留ジョブあたりの記憶化件数上限（§2.5, thresholds.memory_max_candidates_per_job）は、
    ジョブ（forループ）先頭でリセットするローカルカウンタで数える。
    """
    summary_processed: list[JobOutcome] = []
    skipped_no_lane: list[int] = []
    skipped_quota: list[int] = []
    failed: list[int] = []
    lane_switched: list[int] = []
    shelved: list[int] = []
    confidence_threshold = thresholds.confidence_threshold_for("記憶候補")

    jobs = chore_box.pending(kind="蒸留", limit=limit)
    for job in jobs:
        if yield_check is not None and yield_check():
            break
        job_candidate_count = 0
        active_lane = job.lane
        call_fn = lane_call_fns.get(active_lane)

        # レガシー cloud ジョブ → local 振替
        if call_fn is None and active_lane == "cloud" and lane_call_fns.get("local") is not None:
            chore_box.switch_lane(job.id, "local")
            lane_switched.append(job.id)
            if change_log is not None:
                change_log.record(ChangeReport(
                    timestamp=_utc_now_iso(), action="蒸留ジョブ車線振替", target_id=job.id,
                    reason="cloud車線退役のためlocalへ振替",
                    before="lane=cloud", after="lane=local",
                ))
            active_lane = "local"
            call_fn = lane_call_fns["local"]

        if call_fn is None:
            skipped_no_lane.append(job.id)
            continue

        turns_payload = job.payload["turns"]
        content_hash = turns_content_hash(turns_payload)
        # 冪等: 同一 turns 集合は再蒸留しない（二重記憶防止）
        if memory_store.has_distillation_key(content_hash):
            chore_box.mark_done(job.id)
            summary_processed.append(
                JobOutcome(job_id=job.id, accepted=0, rejected=["冪等スキップ（処理済み）"])
            )
            continue

        try:
            response_text = call_fn(build_distillation_prompt(turns_payload))
            candidates = _extract_candidates(response_text)

            # C-1: 候補処理もtry内。型崩れ・DB例外で失敗回数へ合流（起動クラッシュループ防止）
            job_session = _session_from_turns(turns_payload)
            accepted = 0
            rejected: list[str] = []
            checkpoint_scope = "distillation"
            checkpoint_key = str(job.id)
            processed_ids = chore_box.get_checkpoint_processed_ids(checkpoint_scope, checkpoint_key)
            interrupted = False
            for idx, raw in enumerate(candidates):
                if yield_check is not None and yield_check():
                    interrupted = True
                    break
                if str(idx) in processed_ids:
                    continue
                if not isinstance(raw, dict):
                    rejected.append("不正な候補形式")
                    chore_box.add_checkpoint_processed_id(checkpoint_scope, checkpoint_key, str(idx))
                    continue
                confidence = raw.get("confidence", 0.0)
                if not isinstance(confidence, (int, float)) or float(confidence) < confidence_threshold:
                    rejected.append("確信度不足")
                    chore_box.add_checkpoint_processed_id(checkpoint_scope, checkpoint_key, str(idx))
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
                    job_candidate_count=job_candidate_count,
                )
                if review.accepted:
                    accepted += 1
                    job_candidate_count += 1
                    # §4.9: Fact 転記は裏方便のみ（蒸留の拡張）。会話中即時 Fact 化は禁止。
                    # 記憶採用後に Fact 転記が落ちてもジョブ全体を失敗扱いしない（記憶は残す）。
                    episode_ids = [review.memory_id] if review.memory_id is not None else None
                    try:
                        write_fact_from_distillation_candidate(
                            memory_store, raw, episode_ids=episode_ids,
                        )
                    except Exception:  # noqa: BLE001
                        rejected.append("Fact転記失敗（記憶は採用済み）")
                else:
                    rejected.append(review.reason)
                chore_box.add_checkpoint_processed_id(checkpoint_scope, checkpoint_key, str(idx))

            if interrupted:
                continue

            # ジョブ自体の処理(LLM発注・解釈・候補審査)は成功したのでmark_done。
            # 個々の候補が関所で棄却されても、それは正常な審査結果であり再試行対象ではない。
            memory_store.record_distillation_key(content_hash)
            chore_box.mark_done(job.id)
            chore_box.clear_checkpoint(checkpoint_scope, checkpoint_key)
            summary_processed.append(JobOutcome(job_id=job.id, accepted=accepted, rejected=rejected))
        except Exception:  # noqa: BLE001
            failed.append(job.id)
            failure_count = chore_box.increment_failure(job.id)
            if failure_count >= failure_shelve_threshold:
                if active_lane == "cloud" and lane_call_fns.get("local") is not None:
                    chore_box.switch_lane(job.id, "local")
                    lane_switched.append(job.id)
                    if change_log is not None:
                        change_log.record(ChangeReport(
                            timestamp=_utc_now_iso(), action="蒸留ジョブ車線振替", target_id=job.id,
                            reason=f"cloud車線で{failure_count}回連続失敗のためlocalへ振替",
                            before="lane=cloud", after="lane=local",
                        ))
                else:
                    reason = f"{active_lane}車線で{failure_count}回連続失敗のため棚上げ"
                    chore_box.shelve(job.id, reason=reason)
                    shelved.append(job.id)
                    if change_log is not None:
                        change_log.record(ChangeReport(
                            timestamp=_utc_now_iso(), action="蒸留ジョブ棚上げ", target_id=job.id,
                            reason=reason, before=json.dumps(job.payload, ensure_ascii=False), after=None,
                        ))
            continue

    return ConsumptionSummary(
        processed=summary_processed,
        skipped_no_lane=skipped_no_lane,
        skipped_quota=skipped_quota,
        failed=failed,
        lane_switched=lane_switched,
        shelved=shelved,
    )


def write_fact_from_distillation_candidate(
    memory_store: MemoryStore,
    candidate: dict,
    *,
    episode_ids: list[int] | None = None,
) -> str | None:
    """蒸留候補から fact を書く裏方便ヘルパ（§4.9）。

    会話中即時 Fact 化の経路は作らない。gate/runtime からは呼ばない。
    候補に fact フィールドが無い、または confidence 不足の場合は None。
    """
    fact_payload = candidate.get("fact")
    if not isinstance(fact_payload, dict):
        return None
    try:
        confidence = float(candidate.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if confidence < 0.5:
        return None

    status = fact_payload.get("status", "hypothesis")
    episodes = episode_ids or fact_payload.get("episode_ids") or []
    if not isinstance(episodes, list):
        episodes = []
    if status == "active" and not episodes:
        status = "hypothesis"

    try:
        importance = float(fact_payload.get("importance", candidate.get("importance", 0.5)))
    except (TypeError, ValueError):
        importance = 0.5
    importance = max(0.0, min(1.0, importance))

    return memory_store.facts.add_fact(
        subject=str(fact_payload.get("subject", "")),
        predicate=str(fact_payload.get("predicate", "")),
        object=str(fact_payload.get("object", "")),
        statement=str(fact_payload.get("statement", candidate.get("content", ""))),
        confidence=confidence,
        episode_ids=[int(e) for e in episodes],
        importance=importance,
        status=str(status),
    )
