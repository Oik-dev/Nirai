"""Privacy boundary regression tests: use only synthetic sentences and fake judges."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mind.core.routing.home_marks import (
    HOME_MARK_CRITERION_HASH,
    HomeMarks,
    fingerprint,
    marking_windows,
)
from mind.core.state.session import Turn
from test_two_brains_guardrails import Brain, NOW, setup


def test_criterion_fingerprint_invalidates_old_and_mismatched_marks(tmp_path):
    marks = HomeMarks(tmp_path)
    marks.mark(['公開済み。', '家のこと。'], [2])
    stored = json.loads(marks.path.read_text(encoding='utf-8'))
    assert stored == {
        'criterion': HOME_MARK_CRITERION_HASH,
        'marks': {fingerprint('公開済み。'): False, fingerprint('家のこと。'): True},
    }
    assert HomeMarks(tmp_path).safe_only('公開済み。家のこと。') == '公開済み。'

    stored['criterion'] = 'obsolete'
    marks.path.write_text(json.dumps(stored), encoding='utf-8')
    reset = HomeMarks(tmp_path)
    assert reset.marks == {}
    assert reset.home('公開済み。家のこと。')
    assert reset.safe_only('公開済み。家のこと。') == ''

    marks.path.write_text(json.dumps(stored['marks']), encoding='utf-8')
    reset = HomeMarks(tmp_path)
    assert reset.marks == {}
    assert reset.unmarked(['公開済み。家のこと。']) == ['公開済み。', '家のこと。']


def test_obsolete_marks_cannot_expose_background_to_cloud(tmp_path):
    original = HomeMarks(tmp_path)
    original.mark(['個人情報の詳細。'], [])
    stored = json.loads(original.path.read_text(encoding='utf-8'))
    stored['criterion'] = 'outdated'
    original.path.write_text(json.dumps(stored), encoding='utf-8')

    core, marks, _local, cloud = setup(tmp_path)
    core._recall = lambda *_: ['個人情報の詳細。']
    core.turn_routed('よい天気。', now=NOW)
    assert cloud.calls == 1
    assert '個人情報の詳細。' not in cloud.packs[0]
    assert marks.home('個人情報の詳細。')


@pytest.mark.parametrize('sensitive', [
    '住所は架空市星見町2丁目3番4号です。',
    '電話番号は090-0000-0000です。',
    'APIキーはexample-not-a-real-keyです。',
])
def test_fake_home_judge_blocks_sensitive_topic_and_background(tmp_path, sensitive):
    core, marks, local, cloud = setup(tmp_path, local=Brain(home=(1,)))
    core.turn_routed(sensitive, now=NOW)
    assert local.calls == 1
    assert cloud.calls == 0
    assert marks.home(sensitive)

    other, other_marks, _local, other_cloud = setup(tmp_path / 'background')
    other_marks.mark(['公開情報。', sensitive], [2])
    other._recall = lambda *_: [f'公開情報。{sensitive}']
    other.turn_routed('今日は晴れ。', now=NOW)
    assert other_cloud.calls == 1
    assert '公開情報。' in other_cloud.packs[0]
    assert sensitive not in other_cloud.packs[0]


def test_turn_judge_sees_four_previous_utterances_in_order(tmp_path):
    core, marks, local, _cloud = setup(tmp_path)
    marks.mark(['既知の発言。'], [])
    core.session.turns = [
        Turn('master', '既知の発言。'), Turn('serina', '第二の発言。'),
        Turn('master', '第三の発言。'), Turn('serina', '第四の発言。'),
    ]
    prompts = []

    def capture(prompt):
        prompts.append(prompt)
        return {'search': False, 'home': []}

    local.judge = capture
    core.turn_routed('今の発言。', now=NOW)
    prompt = prompts[0]
    assert prompt.index('【発言1】') < prompt.index('【発言2】') < prompt.index('【発言3】')
    assert prompt.index('【発言3】') < prompt.index('【発言4】') < prompt.index('【発言5】')
    assert '【発言1】\n既知の発言。' in prompt
    assert '1. 第二の発言。' in prompt
    assert '4. 今の発言。' in prompt


def test_document_context_includes_unnumbered_neighbors_and_overlap(tmp_path):
    marks = HomeMarks(tmp_path)
    marks.mark(['前文。', '後文。'], [])
    windows = list(marking_windows('前文。要判定。後文。', marks))
    assert windows == [(['要判定。'], '前文。\n1. 要判定。\n後文。')]

    text = '前文。中間の一。中間の二。後文。'
    windows = list(marking_windows(text, HomeMarks(tmp_path / 'split'), max_chars=12))
    assert len(windows) >= 2
    assert all('前文。' in context or '後文。' in context
               for _, context in windows)
    numbered = [part for batch, _context in windows for part in batch]
    assert numbered == ['前文。', '中間の一。', '中間の二。', '後文。']


def test_criterion_text_has_only_one_definition():
    code = Path(__file__).resolve().parents[1] / 'core'
    occurrences = sum(
        path.read_text(encoding='utf-8').count('家に置く文（クラウドの脳へ出さない文）は')
        for path in code.rglob('*.py')
    )
    assert occurrences == 1
