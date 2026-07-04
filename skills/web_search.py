"""WEB検索 Skill（自律検索・会話のキャッチオール本体）

セリナが「外の世界の情報」を自律的に調べる。ChatSkill を置き換える会話本体で、
発動は2経路:
  経路1 鮮度オーバーライド（決定論・backstop）… 鮮度語＋質問サインが揃ったら強制検索
  経路2 Aurora 自己判断 … 知らない時に 〔検索: キーワード〕 マーカーを自分で出す
どちらも SearXNGConnector（薄い通信係）で取得し、結果を system 末尾に注入して
Aurora が人格で語り直す。Phase 1a はスニペットのみ（精読=Fetch は 1b）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from serina.connectors.chat_llm import ChatConnector
from serina.connectors.search import SearXNGConnector
from serina.skills.base import SkillContext

logger = logging.getLogger(__name__)

# 検索マーカー契約（system 末尾＝RPモデルが最も従う位置に付与する）
SEARCH_CONTRACT = (
    "## 外の世界を調べる（WEB検索）\n"
    "ニュース・世間の出来事・製品や技術の事実・用語の意味など「外の世界の情報」で、"
    "自分の知識だけでは確信が持てない、または情報が古い可能性がある時は、"
    "**返答を憶測で書かず**、代わりに次の1行だけを返すこと"
    "（先頭から、他の言葉を一切混ぜない）:\n"
    "〔検索: 調べたいキーワード〕\n"
    "- キーワードは検索に効く簡潔な語にする（例: SpaceX Starship 最新）。\n"
    "- マスター自身の予定・好み・約束など『記憶にしか無い個人的な事』は検索しない。"
    "それは今まで通り、覚えていなければ正直に「覚えてない」と言う。"
)

# 自己発動マーカー（全角コロンも許容）。精読は Phase 1a では検索に降格して扱う
_MARKER_RE = re.compile(r"〔\s*(?:検索|精読)\s*[:：]\s*(.+?)\s*〕")
# 鮮度語（外部・最新情報を強く示唆する語のみ。過検知を避け保守的に列挙）
_RECENCY_RE = re.compile(
    r"最新|ニュース|速報|天気|気温|株価|為替|相場|時価|レート|"
    r"発売日|リリース日|ランキング|直近|現在の"
)
# 質問・依頼のサイン（鮮度語とこれの両方が揃った時だけ強制検索する＝取りこぼし優先）
_QUESTION_RE = re.compile(r"[?？]|ですか|かな|どう|何|なに|いくら|教えて|調べ|知りたい")
# news カテゴリで引くべき語
_NEWS_RE = re.compile(r"ニュース|速報|最新")


class _MarkerPeeker:
    """ストリーム先頭を覗き、検索マーカーなら表示を止める。通常発話は素通しする。

    誤って抑制しても呼び出し側（REPL/GUI）は「トークンが流れなければ戻り値を表示」
    する作りなので、最悪でもその1ターンの逐次表示が消えるだけで内容は失われない。
    """

    _PREFIXES = ("〔検索", "〔精読")

    def __init__(self, downstream: Callable[[str], None] | None) -> None:
        self.downstream = downstream
        self.buffer = ""
        self.decided = False
        self.suppress = False

    def feed(self, chunk: str) -> None:
        if self.decided:
            if not self.suppress and self.downstream:
                self.downstream(chunk)
            return
        self.buffer += chunk
        stripped = self.buffer.lstrip()
        if not stripped:
            return  # まだ空白だけ。判断保留
        if stripped[0] != "〔":
            self._decide(display=True)  # マーカーではない → 素通し開始
        elif any(stripped.startswith(p) for p in self._PREFIXES):
            self._decide(display=False)  # マーカー確定 → 表示しない
        elif len(stripped) >= 3:
            self._decide(display=True)  # 〔だが検索/精読でない → 通常扱い

    def _decide(self, *, display: bool) -> None:
        self.decided = True
        self.suppress = not display
        if display and self.downstream:
            self.downstream(self.buffer)

    def finish(self) -> None:
        if not self.decided:
            self._decide(display=True)


class WebSearchSkill:
    # skill名は履歴・計器盤の互換のため従来の "chat" を踏襲（通常会話ターンとして計上）
    name = "chat"

    def __init__(
        self,
        chat_connector: ChatConnector,
        search_connector: SearXNGConnector,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.chat = chat_connector
        self.search = search_connector
        self.options = options or {}

    def can_handle(self, user_input: str) -> bool:
        return True  # キャッチオール

    def run(self, ctx: SkillContext) -> str:
        messages = ctx.history + [{"role": "user", "content": ctx.user_input}]

        # 経路1: 鮮度オーバーライド（Auroraに聞くまでもなく強制検索）
        if self._forced_search(ctx.user_input):
            return self._answer_with_search(
                ctx,
                messages,
                self._clean_query(ctx.user_input),
                news=bool(_NEWS_RE.search(ctx.user_input)),
                on_token=ctx.on_token,
            )

        # 経路2: Aurora 自己判断（マーカー先頭傍受つき第1パス）
        system = ctx.system_prompt + "\n\n" + SEARCH_CONTRACT
        peeker = _MarkerPeeker(ctx.on_token)
        reply1 = self.chat.chat(system, messages, self.options, on_token=peeker.feed)
        m = _MARKER_RE.match(reply1.strip())
        if not m:
            peeker.finish()  # 通常発話。バッファ済みの先頭を確実に吐き出す
            return reply1
        query = m.group(1).strip()  # マーカーあり → 検索して第2パスで本回答
        return self._answer_with_search(
            ctx, messages, query, news=bool(_NEWS_RE.search(query)), on_token=ctx.on_token
        )

    def _answer_with_search(
        self,
        ctx: SkillContext,
        messages: list[dict[str, str]],
        query: str,
        *,
        news: bool,
        on_token: Callable[[str], None] | None,
    ) -> str:
        result = self.search.query(query, news=news)
        if result.status == "ok" and result.payload:
            system = (
                f"{ctx.system_prompt}\n\n"
                f"## 外部情報（web_search）— {query}\n{result.payload}\n\n"
                "上の外部情報は今WEBで調べた内容。これを素材に、セリナの言葉で分かりやすく"
                "答えること（URLの羅列や検索結果の生コピペはしない）。"
            )
        else:  # 取得失敗 → 外部情報なしで正直に謝らせる（無言死・生スタックトレース禁止）
            system = (
                f"{ctx.system_prompt}\n\n"
                "## 外部情報（web_search）\n"
                "いまWEB検索を試みたが結果を取得できなかった。知ったかぶりせず、"
                "「今ネットがうまく見られない」旨をセリナの言葉で正直に伝えること。"
            )
        return self.chat.chat(system, messages, self.options, on_token=on_token)

    @staticmethod
    def _forced_search(text: str) -> bool:
        return bool(_RECENCY_RE.search(text) and _QUESTION_RE.search(text))

    @staticmethod
    def _clean_query(text: str) -> str:
        """依頼語尾・句読点を落として検索語に寄せる（軽整形のみ）。空になれば原文。"""
        q = re.sub(r"[?？。、!！]", " ", text)
        q = re.sub(r"(について)?(ちょっと|なんか|教えて|調べて|知りたい|どんな感じ|かな|なの)", " ", q)
        cleaned = " ".join(q.split())
        return cleaned or text
