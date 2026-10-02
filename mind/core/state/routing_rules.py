"""外への相談クエリ向けの機微門番。設計書 §5.6（Gemini アドバイザー）。

会話をクラウド Brain へ振る振り分けは退役済み。本モジュールの実効先は
`skills/gemini_advisor/payload.sanitize_query`（相談クエリを外に出していいか）のみ。

判定は3種類を併用する:
  A: 話題の言葉（_sensitive_keywords）— 手動で足した地雷語
  B: 形のパターン — 電話番号・マイナンバー・クレカ番号・APIキー等
  C: 固有名詞の登録簿（_proper_nouns）

緩和（loosen）はマスター承認必須のまま残す（誤って足した語の手動解除用）。
Brain 学習ラチェット（センシティブ観測付箋）は廃止。
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

from mind.core.soul import DATA_DIR

DEFAULT_PERSIST_PATH = DATA_DIR / "routing_rules.json"

# カテゴリB: 形が決まった機微情報の正規表現。実データを保持せず「形」のみで検出する。
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
    """門番の緩和（承認なし）を示す例外。"""


class RoutingRules:
    """外相談クエリの機微門番。プロセス再起動を跨いでキーワード／固有名詞を永続化する。"""

    def __init__(self, *, persist_path: str | Path | None = None) -> None:
        self._sensitive_keywords: set[str] = set()
        self._proper_nouns: set[str] = set()
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path is not None:
            self._load()

    def _load(self) -> None:
        assert self._persist_path is not None
        if not self._persist_path.exists():
            return
        data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        self._sensitive_keywords = set(data.get("sensitive_keywords", []))
        self._proper_nouns = set(data.get("proper_nouns", []))
        # pending_loosen は廃止済み。古いファイルに残っていても無視する。

    def _save(self) -> None:
        # 一時ファイル+os.replaceでアトミックに置換し、last-goodを常に残す。
        if self._persist_path is None:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sensitive_keywords": sorted(self._sensitive_keywords),
            "proper_nouns": sorted(self._proper_nouns),
        }
        tmp_path = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self._persist_path)

    def is_sensitive(self, text: str) -> bool:
        normalized = _normalize(text)
        if any(keyword in normalized for keyword in self._sensitive_keywords):
            return True
        if any(noun in normalized for noun in self._proper_nouns):
            return True
        if any(pattern.search(normalized) for pattern in _TOKEN_PATTERNS):
            return True
        digits_only = _strip_separators(normalized)
        return bool(_DIGIT_RUN_PATTERN.search(digits_only))

    def tighten(self, keyword: str) -> None:
        """門番に語を足す（外への相談クエリ拒否を拡大）。"""
        self._sensitive_keywords.add(_normalize(keyword))
        self._save()

    def loosen(self, keyword: str, *, master_approved: bool = False) -> None:
        """門番から語を外す。マスター承認なしには行えない。"""
        if not master_approved:
            raise RoutingRuleError(
                f"門番の緩和（'{keyword}'）はマスター承認なしに行えない"
            )
        self._sensitive_keywords.discard(_normalize(keyword))
        self._save()

    def add_proper_noun(self, name: str) -> None:
        """固有名詞登録簿へ追加。"""
        self._proper_nouns.add(_normalize(name))
        self._save()

    def remove_proper_noun(self, name: str, *, master_approved: bool = False) -> None:
        """固有名詞登録簿からの削除。マスター承認なしには行えない。"""
        if not master_approved:
            raise RoutingRuleError(
                f"固有名詞登録簿からの削除（'{name}'）はマスター承認なしに行えない"
            )
        self._proper_nouns.discard(_normalize(name))
        self._save()
