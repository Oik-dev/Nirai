"""Memory層の設定値（後から調整可能）"""

from dataclasses import dataclass, field


@dataclass
class SearchConfig:
    """ハイブリッド検索の係数（3c: フロア付き乗算。β独立項は廃止し鮮度は関連度への乗算ゲートへ）"""

    alpha: float = 0.85  # 関連度（ベクトル類似度）
    gamma: float = 0.15  # 重要度
    kappa: float = 0.35  # 減衰フロア（0=完全乗算、1=減衰なし）
    vector_candidate_multiplier: int = 5  # ベクトル検索で取得する候補倍率
    # type → 半減期（日）。metadata.half_life_days があれば個別上書き。
    # 短期3日の type は現状存在しない（将来の一時的関心事用に予約）
    half_life_days_by_type: dict = field(default_factory=lambda: {
        "fact": 180.0,
        "knowledge": 180.0,
        "relationship": 180.0,
        "promise": 180.0,
        "belief": 180.0,  # 4a用の予約値（現状未使用）
        "diary": 30.0,
        "event": 30.0,
        "growth_note": 30.0,  # 4a用の予約値（現状未使用）
    })
    default_half_life_days: float = 30.0


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
