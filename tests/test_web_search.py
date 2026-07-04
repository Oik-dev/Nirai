"""WEB検索 Skill（Phase 1a）の自動アサーションテスト（Ollama/SearXNG 不要・全てモック）"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.connectors.external import ServiceResult
from serina.skills.base import SkillContext
from serina.skills.web_search import WebSearchSkill


class FakeChat:
    """scripted なチャット。呼ばれるたび outputs を1つ返し、on_token にも流す。"""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.systems: list[str] = []

    def chat(self, system, messages, options=None, on_token=None):
        self.systems.append(system)
        out = self.outputs.pop(0)
        if on_token:
            on_token(out)  # 単一チャンクでストリームを模擬
        return out


class FakeSearch:
    def __init__(self, result: ServiceResult) -> None:
        self.result = result
        self.queries: list[tuple[str, bool]] = []

    def query(self, request: str, *, news: bool = False) -> ServiceResult:
        self.queries.append((request, news))
        return self.result


_OK = ServiceResult(status="ok", payload="- Starship試験成功\n  https://x", source="web_search")
_ERR = ServiceResult(status="error", payload="", source="web_search", error="boom")


def test_marker_triggers_search_and_hides_marker() -> None:
    chat = FakeChat(["〔検索: SpaceX 最新〕", "スペースXはStarshipの試験をしたみたい。"])
    search = FakeSearch(_OK)
    skill = WebSearchSkill(chat, search)
    tokens: list[str] = []
    ctx = SkillContext("SpaceXについて教えて", "SYS", [], on_token=tokens.append)
    reply = skill.run(ctx)
    # キーワードに「最新」を含むので news カテゴリで引く
    assert search.queries == [("SpaceX 最新", True)], f"検索語が違う: {search.queries}"
    assert reply == "スペースXはStarshipの試験をしたみたい。"
    assert "".join(tokens) == "スペースXはStarshipの試験をしたみたい。", "マーカーが表示に漏れた"
    assert "外部情報" in chat.systems[1], "第2パスに検索結果が注入されていない"


def test_normal_chat_does_not_search() -> None:
    chat = FakeChat(["こんにちは、今日はどうでしたか？"])
    search = FakeSearch(_OK)
    skill = WebSearchSkill(chat, search)
    tokens: list[str] = []
    ctx = SkillContext("ただいま", "SYS", [], on_token=tokens.append)
    reply = skill.run(ctx)
    assert search.queries == [], "通常会話で検索が走った"
    assert reply == "こんにちは、今日はどうでしたか？"
    assert "".join(tokens) == "こんにちは、今日はどうでしたか？", "通常発話が逐次表示されていない"


def test_recency_override_forces_news_search() -> None:
    chat = FakeChat(["最新のスペースXはね…"])  # 強制検索経路はチャット1回のみ
    search = FakeSearch(_OK)
    skill = WebSearchSkill(chat, search)
    ctx = SkillContext("SpaceXの最新ニュースは？", "SYS", [])
    reply = skill.run(ctx)
    assert len(search.queries) == 1, "鮮度オーバーライドで検索が走っていない"
    assert search.queries[0][1] is True, "ニュース語なのに news カテゴリで引いていない"
    assert len(chat.systems) == 1 and "外部情報" in chat.systems[0], "検索結果が注入されていない"
    assert reply == "最新のスペースXはね…"


def test_no_false_override_on_casual_mention() -> None:
    # 「天気」を含むが質問サインが無い雑談 → 強制検索しない、マーカーも無し
    chat = FakeChat(["そうだね、気持ちいい天気！"])
    search = FakeSearch(_OK)
    skill = WebSearchSkill(chat, search)
    reply = skill.run(SkillContext("今日はいい天気だね", "SYS", []))
    assert search.queries == [], "雑談で誤って検索が走った"
    assert reply == "そうだね、気持ちいい天気！"


def test_search_error_falls_back_to_honest_reply() -> None:
    chat = FakeChat(["〔検索: 何か〕", "ごめん、今ネットがうまく見られないみたい。"])
    search = FakeSearch(_ERR)
    skill = WebSearchSkill(chat, search)
    reply = skill.run(SkillContext("SpaceXについて教えて", "SYS", []))  # マーカー経路（強制検索でない）
    assert reply == "ごめん、今ネットがうまく見られないみたい。"
    assert "取得できなかった" in chat.systems[-1], "失敗時の正直フォールバック指示が無い"


def main() -> None:
    tests = [
        test_marker_triggers_search_and_hides_marker,
        test_normal_chat_does_not_search,
        test_recency_override_forces_news_search,
        test_no_false_override_on_casual_mention,
        test_search_error_falls_back_to_honest_reply,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
