"""五段の候補、表示を巻き戻す契約、家の印の境界。通信なし。"""

from datetime import datetime, timezone

from mind.core.config import ThresholdsConfig
from mind.core.routing.home_marks import HomeMarks
from mind.core.routing.registry import BrainEntry
from mind.core.runtime import Core

NOW = datetime(2026, 10, 15, 10, tzinfo=timezone.utc)


class Brain:
    def __init__(self, result='ok', broken=False, partial=False):
        self.result, self.broken, self.partial = result, broken, partial
        self.calls = 0

    def judge(self, prompt):
        return {'search': False, 'judgments': [
            {'number': 1, 'reason': '一般的な話題', 'decision': '外'}]}

    def converse(self, pack, *, on_token=None, on_reply=None, **kwargs):
        self.calls += 1
        if self.partial and on_token:
            on_token('途中')
        if self.broken:
            raise RuntimeError('失敗')
        if on_token:
            on_token(self.result)
        if on_reply:
            on_reply(self.result)
        return {'reply': self.result, 'appraisal': None}


def setup(tmp_path, first, second, local):
    names = ['local', 'first', 'second']
    entries = [BrainEntry('local', 'ollama', 'local', 'primary', 'small')]
    entries += [BrainEntry(name, 'gemini', 'cloud', 'conversation', 'large')
                for name in names[1:]]
    core = Core(persona_text='ふつうの人格', absolute_rules='ふつうのルール',
                thresholds=ThresholdsConfig(), registry=entries,
                brains=dict(zip(names, (local, first, second))),
                home_marks=HomeMarks(tmp_path))
    core._decide_deep_thinking = lambda *_: False
    return core


def test_first_fails_second_replies_once(tmp_path):
    first, second, local = Brain(broken=True), Brain('2番め'), Brain('手元')
    core = setup(tmp_path, first, second, local)
    callbacks = []
    report = core.turn_routed('今日はいい天気', now=NOW,
                              on_token=lambda t: callbacks.append(('token', t)),
                              on_reply=lambda t: callbacks.append(('reply', t)))
    assert report.report.reply == '2番め'
    assert (first.calls, second.calls, local.calls) == (1, 1, 0)
    assert [v for k, v in callbacks if k == 'reply'] == ['2番め']


def test_partial_output_resets_before_next_candidate(tmp_path):
    first, second, local = Brain(broken=True, partial=True), Brain('代打'), Brain('手元')
    core = setup(tmp_path, first, second, local)
    calls = []
    result = core.turn_routed('こんばんは', now=NOW,
                              on_token=lambda t: calls.append(('token', t)),
                              on_reset=lambda: calls.append(('reset', None)),
                              on_reply=lambda t: calls.append(('reply', t)))
    assert result.report.reply == '代打'
    assert calls == [('token', '途中'), ('reset', None), ('token', '代打'), ('reply', '代打')]


def test_all_cloud_broken_falls_back_to_local(tmp_path):
    first, second, local = Brain(broken=True), Brain(broken=True), Brain('手元の言葉')
    core = setup(tmp_path, first, second, local)
    result = core.turn_routed('おはよう', now=NOW)
    assert result.report.reply == '手元の言葉'
    assert local.calls == 1


def test_home_is_never_sent_to_cloud_even_when_local_fails(tmp_path):
    first, second, local = Brain(), Brain(), Brain(broken=True)
    core = setup(tmp_path, first, second, local)
    core.home_marks.mark(['家の秘密'], [1])
    result = core.turn_routed('家の秘密', now=NOW)
    assert result.report.reply
    assert first.calls == second.calls == 0
    assert local.calls == 1
