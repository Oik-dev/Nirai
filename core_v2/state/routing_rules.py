"""振り分けルール（センシティブ/個人情報の判定キーワード）。設計書v2 §2.6, §3.3.1

逆止弁（ラチェット）: 厳しくなる方向は自動反映、緩む方向はマスター承認必須。
門は自動では締まる一方、開けるのは人間だけ。

判定は3種類を併用する（2026-07-12 Phase4車線振り分け方針決定）:
  A: 話題の言葉（_sensitive_keywords）— 話題そのものが地雷なもの（NSFW等）に限定。
     「医療」「法律」等の一般的な相談カテゴリは含めない（話題自体は自由にクラウドの
     賢さを使ってよく、危険なのは話題ではなく話中の具体的な特定情報のため）
  B: 形のパターン（_SENSITIVE_PATTERNS）— 電話番号・マイナンバー・クレカ番号・APIキー等、
     値は無限だが形が決まっているもの。実データを事前保管する必要がない汎用の形テスト
  C: 固有名詞の登録簿（_proper_nouns）— 企業名・プロジェクト名・第三者の実名等。
     初期値は空。追加は「厳しくなる方向」なので承認不要（tightenと同じ扱い）
"""

from __future__ import annotations

import re
import unicodedata

# カテゴリB: 形が決まった機微情報の正規表現。実データを保持せず「形」のみで検出する。
# 過検出（安全側=local判定）は許容し、見逃し（危険側=cloud判定）を避ける方針。
#
# 2026-07-12 レビュー(C-1)で判明: 全角数字(０-９)はNFKC正規化しないと`\d`に一致せず、
# ドット/スペース/括弧区切りの番号も素通りする。そのため判定は2段構えにする:
#   1. is_sensitive入口でNFKC正規化（全角→半角）
#   2. 数字系パターンは「区切り文字を除去した文字列」に対しても照合する
#
# 再レビュー(Important#1)で判明: 区切り文字を列挙するクラス([\s\-.()（）ー]等)は
# en-dash/em-dash/マイナス記号/中黒のような未列挙の区切りをすり抜ける。
# 列挙をいくら足しても新しい区切り方に追従できないため、Unicodeの「カテゴリ」で
# 判定する（句読点P*・空白区切りZs・数学記号Sm=マイナス記号はまとめて除去対象）。
def _strip_separators(text: str) -> str:
    return "".join(
        ch for ch in text
        if not (
            unicodedata.category(ch)[0] in ("P", "Z")
            or unicodedata.category(ch) == "Sm"
            or ch == "ー"  # 長音記号（全角ー）。カテゴリはLmで句読点扱いされないため個別に除去
        )
    )

# 区切り除去後の文字列に対して照合する（電話番号・マイナンバー・クレカ番号）。
# 10桁(市外局番なし携帯)〜16桁(クレカ)の連続数字を広く拾う。
_DIGIT_RUN_PATTERN = re.compile(r"\d{10,16}")

# 区切り除去前の正規化済みテキストにそのまま照合するもの（英字混在のためズレない）。
_TOKEN_PATTERNS: list[re.Pattern[str]] = [
    # APIキーの典型的な接頭辞＋長い英数字トークン
    re.compile(r"(?:sk-|sk-ant-|ghp_|github_pat_|AIza|xox[baprs]-)[A-Za-z0-9_\-]{16,}"),
    # 接頭辞のない汎用トークン（英字と数字が混在する24文字以上の連続）
    re.compile(r"(?=[A-Za-z0-9_\-]*[A-Za-z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{24,}"),
]


def _normalize(text: str) -> str:
    """全角英数字・全角記号を半角化する（登録側・判定側の両方で必ず通す）。"""
    return unicodedata.normalize("NFKC", text)


class RoutingRuleError(Exception):
    """振り分けルールの逆止弁に反する操作（承認なしの緩和）を示す例外。"""


class RoutingRules:
    def __init__(self) -> None:
        self._sensitive_keywords: set[str] = set()
        self._proper_nouns: set[str] = set()

    def is_sensitive(self, text: str) -> bool:
        normalized = _normalize(text)
        if any(keyword in normalized for keyword in self._sensitive_keywords):
            return True
        if any(noun in normalized for noun in self._proper_nouns):
            return True
        if any(pattern.search(normalized) for pattern in _TOKEN_PATTERNS):
            return True
        # 区切り文字（句読点・空白・各種ダッシュ/マイナス記号・全角ー等）を除去した上で、
        # 電話番号・マイナンバー・クレカ番号相当の連続数字を探す。
        digits_only = _strip_separators(normalized)
        return bool(_DIGIT_RUN_PATTERN.search(digits_only))

    def tighten(self, keyword: str) -> None:
        """厳しくなる方向（センシティブ拡大）。自動で反映してよい。"""
        self._sensitive_keywords.add(_normalize(keyword))

    def loosen(self, keyword: str, *, master_approved: bool = False) -> None:
        """緩む方向（クラウド解禁拡大）。マスター承認なしには反映しない。"""
        if not master_approved:
            raise RoutingRuleError(
                f"振り分けルールの緩和（'{keyword}'）はマスター承認なしに行えない（§3.3.1）"
            )
        self._sensitive_keywords.discard(_normalize(keyword))

    def add_proper_noun(self, name: str) -> None:
        """カテゴリC: 企業名・プロジェクト名・第三者の実名等を登録簿に追加。

        追加は「厳しくなる方向」（センシティブ拡大）なので承認不要でtightenと同じ扱い。
        """
        self._proper_nouns.add(_normalize(name))

    def remove_proper_noun(self, name: str, *, master_approved: bool = False) -> None:
        """緩む方向（登録簿からの削除）。マスター承認なしには反映しない。"""
        if not master_approved:
            raise RoutingRuleError(
                f"固有名詞登録簿からの削除（'{name}'）はマスター承認なしに行えない（§3.3.1）"
            )
        self._proper_nouns.discard(_normalize(name))
