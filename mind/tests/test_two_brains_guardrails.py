"""段1の守るものだけを確かめる。外部APIも本物のイデアも使わない。"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from mind.core.config import load_thresholds
from mind.core.chores.idle_policy import PulseCandidate
from mind.core.idea import Idea
from mind.core.routing.home_marks import HomeMarks
from mind.core.routing.registry import BrainEntry
from mind.core.runtime import Core

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


class Brain:
    def __init__(self, *, home=(), broken=False, token=''):
        self.calls = 0
        self.judges = 0
        self.home = tuple(home)
        self.broken = broken
        self.token = token
        self.packs = []
        self.enabled = True

    def ready(self, now=None):  # noqa: ANN001, ANN202, ARG002
        return self.enabled

    def judge(self, prompt):
        self.judges += 1
        context = (prompt.split('【印を決める文】')[-1] if '【印を決める文】' in prompt
                   else prompt.split('JSONのみを返す。')[-1])
        count = len(re.findall(r'(?m)^\d+\. ', context))
        return {'search': False, 'judgments': [
            {'number': i, 'reason': 'テスト用の判定', 'decision': '家' if i in self.home else '外'}
            for i in range(1, count + 1)]}

    def converse(self, pack, *, on_token=None, on_reply=None, **_):
        self.calls += 1
        self.packs.append(pack.render())
        if self.token and on_token:
            on_token(self.token)
        if self.broken:
            raise RuntimeError('断線')
        if on_reply:
            on_reply('返事')
        return {'reply': '返事', 'appraisal': None}


def setup(tmp_path, *, local=None, cloud=None):
    local = local or Brain()
    cloud = cloud or Brain()
    marks = HomeMarks(tmp_path)
    core = Core(
        persona_text='一般の人格。秘密の人格。', absolute_rules='公開のルール。',
        thresholds=load_thresholds(),
        registry=[
            BrainEntry('gemma', 'ollama', 'local', 'small'),
            BrainEntry('gemini', 'gemini', 'cloud', 'large'),
        ],
        brains={'gemma': local, 'gemini': cloud}, home_marks=marks,
    )
    core._decide_deep_thinking = lambda *_: False
    return core, marks, local, cloud


def test_home_mark_fail_closed(tmp_path):
    marks = HomeMarks(tmp_path)
    assert marks.home('まだ判定していない言葉。')
    marks.mark(['安全。', '家の話。'], [2])
    assert not marks.home('安全。')
    assert marks.home('家の話。')
    marks.path.unlink()
    assert HomeMarks(tmp_path).home('安全。')
    marks.path.write_text('{broken', encoding='utf-8')
    assert HomeMarks(tmp_path).home('安全。')


def test_duplicate_text_cannot_cancel_a_home_mark(tmp_path):
    marks = HomeMarks(tmp_path)
    assert marks.unmarked(['秘密。', '秘密。', '公開。']) == ['秘密。', '公開。']
    # 古い判定の番号が重複していても、「家」が優先される。
    marks.mark(['秘密。', '公開。', '秘密。'], [1])
    assert marks.home('秘密。')
    assert not marks.home('公開。')
    marks.mark(['秘密。'], [])
    assert marks.home('秘密。')


def test_same_text_in_master_and_recall_never_reaches_cloud(tmp_path):
    core, marks, local, cloud = setup(tmp_path, local=Brain(home=(1,)))
    core._recall = lambda *_: ['秘密。']
    core.turn_routed('秘密。', now=NOW)
    assert marks.home('秘密。')
    assert local.calls == 1 and cloud.calls == 0


def test_unmarked_background_waits_for_later_marking(tmp_path):
    core, marks, local, cloud = setup(tmp_path)
    core._now_relation = lambda: '今の関係（2026-10-12から）：相棒として信頼している。'
    core.session.rolling_summary = '前に海の話をした。'
    core.session.fine_summary = '昨日の約束を覚えている。'
    core.turn_routed('こんにちは。', now=NOW)
    assert local.judges == 1
    assert cloud.calls == 1
    assert '前に海の話をした。' not in cloud.packs[0]
    assert '昨日の約束を覚えている。' not in cloud.packs[0]
    assert '相棒として信頼している。' not in cloud.packs[0]
    assert core.mark_background(now=NOW) >= 3
    core.turn_routed('いい天気。', now=NOW)
    assert '前に海の話をした。' in cloud.packs[-1]
    assert '昨日の約束を覚えている。' in cloud.packs[-1]


def test_sensitive_memory_is_background_not_routing_topic(tmp_path):
    core, marks, local, cloud = setup(tmp_path)
    marks.mark(['心の秘密。', '海が綺麗だ。'], [1])
    core._recall = lambda *_: ['心の秘密。\n海が綺麗だ。']
    core.turn_routed('今日はいい天気。', now=NOW)
    assert cloud.calls == 1 and local.calls == 0
    assert '心の秘密。' not in cloud.packs[0]
    assert '海が綺麗だ。' in cloud.packs[0]


def test_cloud_reply_and_external_material_are_public_but_true_is_sticky(tmp_path):
    marks = HomeMarks(tmp_path)
    marks.mark(['家の秘密。'], [1])
    marks.mark_public('家の秘密。公開済み。')
    assert marks.home('家の秘密。')
    assert not marks.home('公開済み。')


def test_cloud_second_turn_keeps_original_flow_without_summary(tmp_path):
    core, marks, local, cloud = setup(tmp_path)
    core.turn_routed('最初は海の話。', now=NOW)
    core.turn_routed('さっきは何の話？', now=NOW)
    assert local.judges == 2
    assert cloud.calls == 2
    assert 'マスター: 最初は海の話。' in cloud.packs[1]
    assert 'セリナ: 返事' in cloud.packs[1]


def test_pulse_routes_by_content_not_candidate_metadata(tmp_path):
    core, marks, local, cloud = setup(tmp_path)
    local.choose_pulse = lambda prompt, **_: {'speak': False, 'text': ''}
    cloud.choose_pulse = lambda prompt, **_: {'speak': False, 'text': ''}
    ordinary = PulseCandidate('connection', 'connection-2026-10-12',
                              {'reason': 'missing_master'})
    assert core.pulse(ordinary, now=NOW, on_said=lambda _: None) is False
    assert core.last_reply_brain == 'gemini'
    home = PulseCandidate('wake', 'wake-2026-10-12', {'thought': '心の秘密。'})
    assert core.pulse(home, now=NOW, on_said=lambda _: None) is False
    assert core.last_reply_brain == 'gemma'
    marks.mark(['心の秘密。'], [])
    assert core.pulse(home, now=NOW, on_said=lambda _: None) is False
    assert core.last_reply_brain == 'gemini'


def test_topic_home_never_calls_cloud(tmp_path):
    core, marks, local, cloud = setup(tmp_path, local=Brain(home=(1,)))
    core.turn_routed('心の奥の話。', now=NOW)
    assert local.calls == 1
    assert cloud.calls == 0
    assert marks.home('心の奥の話。')


def test_background_home_text_is_stripped(tmp_path):
    core, marks, local, cloud = setup(tmp_path)
    marks.mark(['一般の人格。', '秘密の人格。', '公開のルール。'], [2])
    core.session.rolling_summary = '公開の前史。秘密の前史。'
    marks.mark(['公開の前史。', '秘密の前史。'], [2])
    core.turn_routed('こんにちは。', now=NOW)
    assert cloud.calls == 1
    assert '秘密の人格。' not in cloud.packs[0]
    assert '秘密の前史。' not in cloud.packs[0]
    assert '一般の人格。' in cloud.packs[0]


def test_stream_rewrite_resets_once_and_records_once(tmp_path):
    core, marks, local, cloud = setup(tmp_path, cloud=Brain(broken=True, token='途中'))
    events = []
    core.turn_routed('こんにちは。', now=NOW,
                     on_token=lambda text: events.append(('token', text)),
                     on_reset=lambda: events.append(('reset', None)),
                     on_reply=lambda text: events.append(('reply', text)))
    assert events == [('token', '途中'), ('reset', None), ('reply', '返事')]
    assert local.calls == 1 and cloud.calls == 1
    assert len(core.session.turns) == 2


def test_single_home_search_judge_call(tmp_path):
    core, marks, local, cloud = setup(tmp_path)
    core.turn_routed('こんにちは。', now=NOW)
    assert local.judges == 1
    assert local.calls == 0
    assert cloud.calls == 1


def test_sleep_uses_only_local_brain(tmp_path, monkeypatch):
    from mind.core.chores import orchestrator

    core, marks, local, cloud = setup(tmp_path)
    core.memory = object()  # 眠りの入り口に記憶が接続されている
    called = []

    def fake_asker(model, **_):
        called.append(('asker', model))
        return lambda *_: None

    def fake_sleep(memory, **kwargs):
        called.append(('sleep', kwargs['brain']))
        return kwargs

    monkeypatch.setattr(orchestrator, 'asker', fake_asker)
    monkeypatch.setattr(orchestrator, 'sleep', fake_sleep)
    orchestrator.run_sleep(core, now=NOW)
    assert called == [('asker', 'gemma'), ('sleep', 'gemma')]
    assert cloud.calls == 0


def test_new_ability_log_is_append_only_and_idempotent(tmp_path):
    from mind.core.abilities import record_new, rows

    idea = Idea(tmp_path)
    config = tmp_path / 'abilities.toml'
    config.write_text('[ability]\ntwo_brains = "新しい脳を使える。"\n', encoding='utf-8')
    assert record_new(idea, config=config, now=NOW) == ['two_brains']
    assert record_new(idea, config=config, now=NOW) == []
    config.write_text('[ability]\ntwo_brains = "新しい脳を使える。"\nsearch = "調べられる。"\n', encoding='utf-8')
    assert record_new(idea, config=config, now=NOW) == ['search']
    assert [entry['id'] for entry in rows(idea)] == ['two_brains', 'search']


def test_sleep_rebuilds_unknown_marks_without_reclassifying_completed(tmp_path):
    from mind.core.memory.sleep import _mark_sleep_texts

    idea = Idea(tmp_path)
    idea.persona.mkdir(parents=True)
    (idea.persona / 'first.md').write_text('安全。秘密。', encoding='utf-8')
    marks = HomeMarks(idea.data)
    calls = []

    def ask(prompt, _schema, _attempt):
        calls.append(prompt)
        return {'judgments': [
            {'number': i, 'reason': 'テスト用の判定', 'decision': '家' if i == 2 else '外'}
            for i in range(1, 3)]}

    _mark_sleep_texts(idea, marks, ask, should_stop=lambda: False)
    assert not marks.home('安全。')
    assert marks.home('秘密。')
    _mark_sleep_texts(idea, marks, ask, should_stop=lambda: False)
    assert len(calls) == 1
