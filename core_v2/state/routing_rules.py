"""振り分けルール（センシティブ/個人情報の判定キーワード）。設計書v2 §2.6, §3.3.1

逆止弁（ラチェット）: 厳しくなる方向は自動反映、緩む方向はマスター承認必須。
門は自動では締まる一方、開けるのは人間だけ。
"""

from __future__ import annotations


class RoutingRuleError(Exception):
    """振り分けルールの逆止弁に反する操作（承認なしの緩和）を示す例外。"""


class RoutingRules:
    def __init__(self) -> None:
        self._sensitive_keywords: set[str] = set()

    def is_sensitive(self, text: str) -> bool:
        return any(keyword in text for keyword in self._sensitive_keywords)

    def tighten(self, keyword: str) -> None:
        """厳しくなる方向（センシティブ拡大）。自動で反映してよい。"""
        self._sensitive_keywords.add(keyword)

    def loosen(self, keyword: str, *, master_approved: bool = False) -> None:
        """緩む方向（クラウド解禁拡大）。マスター承認なしには反映しない。"""
        if not master_approved:
            raise RoutingRuleError(
                f"振り分けルールの緩和（'{keyword}'）はマスター承認なしに行えない（§3.3.1）"
            )
        self._sensitive_keywords.discard(keyword)
