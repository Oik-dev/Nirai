"""Tavilyは検索クエリのみを受け取り、失敗しても会話を止めない。"""

from mind.core.intake.tavily_tools import execute_tavily_search
from mind.skills.tavily_search.skill import TavilySearchSkill


def test_disabled_without_key():
    skill = TavilySearchSkill()
    assert not execute_tavily_search('天気', skill).executed


def test_network_failure_has_reason():
    def fail(_query):
        raise TimeoutError('timed out')
    outcome = execute_tavily_search('天気', TavilySearchSkill(call_fn=fail))
    assert not outcome.executed
    assert 'timed out' in outcome.reason


def test_search_result_structure_and_query_only():
    received = []

    def fake(query):
        received.append(query)
        return {'answer': '晴れ', 'results': [
            {'title': '天気予報', 'url': 'https://example.com/', 'content': '晴天'},
        ]}

    outcome = execute_tavily_search('東京 天気', TavilySearchSkill(call_fn=fake))
    assert received == ['東京 天気']
    assert outcome.executed
    assert outcome.result.answer == '晴れ'
    assert outcome.result.results[0]['snippet'] == '晴天'
