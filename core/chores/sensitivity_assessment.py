"""既存記憶の機微査定。設計書 §4.6-3。

既存858件（等級初期値=全件2＝安全側）を、裏方便のアイドル仕事としてAuroraが少しずつ
査定する。機微等級(0/1/2)を判定し、1と判定した記憶には化粧版（クラウド用言い換え版）も
生成する。数週間かけて自然にクラウド解禁率が上がる想定（§4.6-3）。

安全設計（advisor+マスター確認、2026-07-12）:
- `RoutingRules.is_sensitive()`を下限フロアとして使う。Auroraが0/1と言っても、
  パターン（電話番号・APIキー等の形）に引っかかれば絶対に2未満へは下げない（downgrade禁止）。
- 化粧版そのものにも`is_sensitive()`の検証門を通す。化粧版がまだ機微の形を持つ場合は
  化粧版を破棄し、その記憶は等級2に据え置く（12B地元モデルの化粧版品質が不安定でも、
  「漏れない」方向にしか倒れないようにする）。
- 化粧版が要求どおり得られなかった等級1判定も、化粧版無しの等級1として確定させない
  （pack.pyの「化粧版無し機微1は除外」フェイルセーフと二重の防御になるよう、
  そもそも書き込み側でも等級2に落とす）。
- 等級2（または2に据え置き）には化粧版を持たせない（§4.2「真の秘匿値は化粧版を作らず
  本文記載自体を避ける」の精神を踏襲し、機微な原文の言い換えをDBに残さない）。
- JSON解釈失敗・LLM例外は`sensitivity_assessed`を立てず未査定のまま残す（次回再挑戦。
  蒸留消化(`distillation.py`)と同じ電源断耐性の思想）。
- 査定結果（等級遷移・化粧版採否）はChangeLogに記録する（§4.6-3「査定結果は変更レポートに
  記録」、保護3原則の原則1=無言破棄の禁止）。本文自体は書き換えないので世代保存（原則2）は
  対象外（`apply_protected_change`は本文before/after前提のため、ここでは通さない）。

注意: `is_sensitive()`通過（=False）は「意味的に完全に安全」ではなく、パターン検出を
すり抜けたという意味に過ぎない。氏名フルセット平文などはパターンに出ない。§4.2の
組み合わせ禁止表（氏名×住所を同一パックに同居させない等）は別ゲート（Core側）の担当で
あり、本モジュールのスコープ外。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from serina.core.chores.chore_box import ChoreBox
from serina.core.memory.protection import ChangeLog, ChangeReport
from serina.core.memory.store import MemoryRecord, MemoryStore
from serina.core.state.routing_rules import RoutingRules

ASSESSMENT_FORMAT_INSTRUCTION = """
次の記憶1件を査定してください。機微等級を判定し、必要なら言い換え版（化粧版）を作ってください。

機微等級:
0: 公開可（例: 好きな食べ物、口癖、居住市区町村レベルの情報）
1: 準機微（例: 職業の詳細、家族構成。単体なら化粧版付きでクラウドへ出せる）
2: 機微（例: APIキー、本名フルセット、番地までの住所、口座番号。いかなる場合も出さない）

等級1の場合のみ、番地・電話番号・氏名フルセット等の特定情報を市区町村レベル等に言い換えた
「化粧版」を作ってください。等級0または2の場合、cosmetic_versionはnullのままでよい。

必ず次のJSON形式のみをコードブロックで返すこと（前後に説明文を含めてもよいが、JSON本体は改変しないこと）:
```json
{"grade": 0, "cosmetic_version": null}
```
""".strip()


class AssessmentParseError(Exception):
    """査定応答からJSONを抽出できなかったことを示す例外。"""


@dataclass(frozen=True)
class AssessmentOutcome:
    """記憶1件の査定結果。"""

    memory_id: int
    assessed: bool
    raw_grade: int | None = None
    final_grade: int | None = None
    cosmetic_version: str | None = None
    cosmetic_rejected: bool = False


@dataclass(frozen=True)
class AssessmentBatchSummary:
    """査定バッチ全体の結果。"""

    processed: list[AssessmentOutcome] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)

    @property
    def total_assessed(self) -> int:
        return sum(1 for outcome in self.processed if outcome.assessed)


def build_assessment_prompt(content: str) -> str:
    """記憶本文から査定発注プロンプトを組み立てる。"""
    return f"【記憶本文】\n{content}\n\n{ASSESSMENT_FORMAT_INSTRUCTION}"


def _extract_assessment(response_text: str) -> dict:
    match = re.search(r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    candidate_text = match.group(1) if match else response_text
    try:
        data = json.loads(candidate_text)
    except json.JSONDecodeError as e:
        raise AssessmentParseError(f"査定応答からJSONを抽出できない: {e}") from e
    if "grade" not in data:
        raise AssessmentParseError("gradeフィールドが無い")
    return data


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def assess_memory(
    record: MemoryRecord,
    *,
    call_fn: Callable[[str], str],
    routing_rules: RoutingRules,
) -> AssessmentOutcome:
    """記憶1件を査定する。LLM呼び出し・JSON解釈が失敗した場合はassessed=Falseで返す
    （呼び出し側はDBを更新しない＝次回再挑戦の未査定のまま）。

    ChangeLogへの記録はここでは行わない（DB更新の成否が未確定なため）。呼び出し側が
    `memory_store.update_sensitivity()`の成功を確認してから記録すること
    （査定した"つもり"がDB未反映のまま監査ログに残る乖離を避けるため）。
    """
    try:
        response_text = call_fn(build_assessment_prompt(record.content))
        data = _extract_assessment(response_text)
        raw_grade = data.get("grade")
        raw_cosmetic = data.get("cosmetic_version")
    except Exception:  # noqa: BLE001
        return AssessmentOutcome(memory_id=record.id, assessed=False)

    if not isinstance(raw_grade, int) or raw_grade not in (0, 1, 2):
        return AssessmentOutcome(memory_id=record.id, assessed=False)

    # is_sensitive()を下限フロアとして使う。Auroraの自己申告だけでは信用しない（downgrade禁止）。
    grade = raw_grade
    if routing_rules.is_sensitive(record.content) and grade < 2:
        grade = 2

    cosmetic_version: str | None = None
    cosmetic_rejected = False
    if grade == 1:
        if isinstance(raw_cosmetic, str) and raw_cosmetic.strip():
            if routing_rules.is_sensitive(raw_cosmetic):
                # 化粧版自体がまだ機微の形を持つ→破棄して等級2に据え置く（漏れない方向に倒す）
                grade = 2
                cosmetic_rejected = True
            else:
                cosmetic_version = raw_cosmetic
        else:
            # 化粧版が要求どおり得られなかった等級1は確定させない
            # （pack.py側の「化粧版無し機微1は除外」フェイルセーフと二重の防御）
            grade = 2

    return AssessmentOutcome(
        memory_id=record.id,
        assessed=True,
        raw_grade=raw_grade,
        final_grade=grade,
        cosmetic_version=cosmetic_version,
        cosmetic_rejected=cosmetic_rejected,
    )


DEFAULT_FAILURE_SHELVE_THRESHOLD = 3


def run_sensitivity_assessment_chunk(
    memory_store: MemoryStore,
    *,
    call_fn: Callable[[str], str],
    routing_rules: RoutingRules,
    change_log: ChangeLog,
    limit: int = 1,
    chore_box: ChoreBox | None = None,
    failure_shelve_threshold: int = DEFAULT_FAILURE_SHELVE_THRESHOLD,
) -> AssessmentBatchSummary:
    """未査定の記憶をlimit件だけ査定してDBへ反映する（§4.6-3、Auroraのアイドル仕事）。

    呼び出し側（GUIの見回りスレッド）が「今アイドルか」「GPUは空いているか」
    「会話ロックは空いているか」を判定してから、指定件数だけ呼ぶことを想定する薄いラッパ
    （`run_idle_digest_chunk`と同じ設計）。未査定の記憶が無ければ何もしない。

    chore_box: 2026-07-12追加。機微査定は車線が"local"(Aurora)1本のみのため蒸留ジョブの
    ような車線振替はできない。同一記憶が`failure_shelve_threshold`回（既定3）連続で査定
    失敗したら棚上げ棚へ移動し（`chore_box.shelved_assessment_ids()`で以後除外）、
    change_logへ日本語レポートを残す（原則1: 無言破棄禁止。「毒饅頭ジョブの先頭詰まり」
    対策・DECISIONS 2026-07-12参照）。Noneの場合（chore_box未配線のテスト等）は従来通り
    失敗回数を記録せず、毎回再挑戦の対象のまま残る。
    """
    exclude_ids = chore_box.shelved_assessment_ids() if chore_box is not None else set()
    records = memory_store.get_unassessed_memories(limit=limit, exclude_ids=exclude_ids)
    processed: list[AssessmentOutcome] = []
    failed: list[int] = []
    for record in records:
        outcome = assess_memory(record, call_fn=call_fn, routing_rules=routing_rules)
        if outcome.assessed:
            memory_store.update_sensitivity(
                record.id, grade=outcome.final_grade, cosmetic_version=outcome.cosmetic_version
            )
            # DB更新が成功してから記録する（査定"したつもり"がDB未反映のまま監査ログに
            # 残る乖離を避けるため。serina-code-reviewerレビューM-1の指摘で修正）。
            change_log.record(
                ChangeReport(
                    timestamp=_utc_now_iso(),
                    action="機微査定",
                    target_id=record.id,
                    reason=(
                        f"Aurora判定grade={outcome.raw_grade}→確定grade={outcome.final_grade}"
                        + ("（化粧版はis_sensitive検証で破棄）" if outcome.cosmetic_rejected else "")
                    ),
                    before=f"sensitivity_grade={record.sensitivity_grade}(未査定)",
                    after=(
                        f"sensitivity_grade={outcome.final_grade}"
                        + ("、化粧版あり" if outcome.cosmetic_version else "")
                    ),
                )
            )
            processed.append(outcome)
        else:
            failed.append(record.id)
            if chore_box is not None:
                failure_count = chore_box.note_assessment_failure(record.id)
                if failure_count >= failure_shelve_threshold:
                    reason = f"機微査定が{failure_count}回連続失敗のため棚上げ"
                    chore_box.shelve_assessment(record.id, reason=reason)
                    change_log.record(
                        ChangeReport(
                            timestamp=_utc_now_iso(), action="機微査定棚上げ", target_id=record.id,
                            reason=reason, before=f"sensitivity_grade={record.sensitivity_grade}(未査定)",
                            after=None,
                        )
                    )
    return AssessmentBatchSummary(processed=processed, failed=failed)
