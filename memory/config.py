"""Memory層の設定値（後から調整可能）"""

from dataclasses import dataclass, field


@dataclass
class SearchConfig:
    """ハイブリッド検索の係数"""

    alpha: float = 0.6  # 関連度（ベクトル類似度）
    beta: float = 0.25  # 新しさ（最終想起からの指数減衰）
    gamma: float = 0.15  # 重要度
    recency_half_life_days: float = 30.0  # 新しさが半分になるまでの日数
    vector_candidate_multiplier: int = 5  # ベクトル検索で取得する候補倍率


@dataclass
class MigrateConfig:
    """移行ツールの設定"""

    dedup_similarity_threshold: float = 0.92  # 0.88 から引き上げ
    pinned_keywords: tuple[str, ...] = ("呪文", "優先固定", "最優先ルール", "消えない記憶")
    max_importance: float = 1.0
    default_importance: float = 0.6
    canonical_importance: float = 0.9  # 正典の既定 importance
    # ソース優先度（大きいほど正典）。canonical=継承記憶
    source_priority: dict = field(
        default_factory=lambda: {
            "継承記憶r1.md": 3,
            "セリナの記憶.json": 2,
        }
    )
    canonical_sources: tuple[str, ...] = ("継承記憶r1.md",)
