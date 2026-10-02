"""関係状態→文脈パック用自然文への変換。設計書 §1.5(段⑤末尾), §2.6(関係状態)

2026-07-26 B1: マスター観測（`RelationshipState.recent_master_mood`）を段⑤の末尾へ
1行添えるための変換。生きた `RelationshipState` オブジェクトはパックへ持たせず、
ここで意訳した文字列だけを載せる（条文A）。

architecture-reviewer指摘（2026-07-26）: 観測に時刻の錨が無いまま「今の様子」として
提示すると、②の時間ラベルが防いでいる誤読（古い情報を“いま”と読ませる）を⑤で
再生産してしまう。そのため取得時刻からの経過が
`thresholds.relationship_observation_stale_after_seconds` を超えた観測は省略する。
"""

from __future__ import annotations

from datetime import datetime, timezone

from mind.core.config import ThresholdsConfig
from mind.core.state.relationship import RelationshipState


def render_master_observation_for_pack(
    relationship: RelationshipState | None,
    thresholds: ThresholdsConfig,
    *,
    now: datetime | None = None,
) -> str:
    """マスターの様子（直近観測）を1行の自然文にする。鮮度切れ・未観測は空文字。

    空文字を返した場合、呼び出し側（pack.py）は行ごと省略する
    （§1.5⑤「値が無いときは行ごと省略する」）。
    """
    if relationship is None:
        return ""
    mood = relationship.recent_master_mood
    observed_at = relationship.recent_master_mood_at
    if not mood or observed_at is None:
        return ""

    current = now or datetime.now(timezone.utc)
    try:
        age_seconds = (current - observed_at).total_seconds()
    except TypeError:
        # 2026-07-26 B1是正(serina-code-reviewer指摘I-4): naive/aware混在等で減算に
        # 失敗しても、パック組み立ては絶対に例外を出さない（沈黙しないという設計思想。
        # apply_loaded_to_relationshipでUTC補完済みだが、直接RelationshipStateへ
        # naiveな値を設定するテスト・呼び出しに対する最終防衛線として二重に備える）。
        return ""
    if age_seconds < 0:
        age_seconds = 0.0
    if age_seconds > thresholds.relationship_observation_stale_after_seconds:
        return ""
    return mood
