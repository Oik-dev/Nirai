"""Privacy boundary regression tests: use only synthetic sentences and fake judges."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from mind.core.routing.home_marks import (
    HOME_MARK_CRITERION_HASH,
    HomeMarks,
    fingerprint,
    marking_windows,
    parse_home_judgments,
)
from mind.core.idea import Idea
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
        return {'search': False, 'judgments': [
            {'number': i, 'reason': '普通の話題', 'decision': '外'}
            for i in range(1, 5)]}

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


def test_unanswered_or_unreadable_decisions_stay_home():
    assert parse_home_judgments({'judgments': [
        {'number': 1, 'reason': '具体的に問題ない', 'decision': '外'},
        {'number': 2, 'reason': '', 'decision': '外'},
        {'number': 3, 'reason': '説明', 'decision': '解釈できない'},
    ]}, 4) == [2, 3, 4]
    assert parse_home_judgments({'home': []}, 2) == [1, 2]


def test_rebuild_uses_newest_document_and_saves_only_completed_documents(tmp_path):
    from mind.core.memory.sleep import _mark_sleep_texts

    idea = Idea(tmp_path)
    idea.persona.mkdir(parents=True, exist_ok=True)
    recent = idea.persona / 'new.md'
    older = idea.persona / 'old.md'
    recent.write_text('新しい一般文。', encoding='utf-8')
    older.write_text(''.join(f'昔の文{i:03d}。' for i in range(350)), encoding='utf-8')
    os.utime(recent, (2000, 2000))
    os.utime(older, (1000, 1000))
    marks = HomeMarks(idea.data)
    seen: list[str] = []
    stopped = False

    def ask(prompt, _schema, _attempt):
        nonlocal stopped
        seen.append(prompt)
        numbers = [int(n) for n in re.findall(r'(?m)^(\d+)\. ', prompt.split('JSONのみ答える。')[-1])]
        if '昔の文' in prompt and not stopped:
            stopped = True
        return {'judgments': [
            {'number': n, 'reason': 'PRIVATE_REASON_CANARY', 'decision': '外'}
            for n in numbers]}

    complete = _mark_sleep_texts(idea, marks, ask, should_stop=lambda: stopped, num_ctx=2048)
    assert not complete
    assert '新しい一般文。' in seen[0]
    assert not marks.home('新しい一般文。')
    assert marks.home('昔の文000。')
    assert 'PRIVATE_REASON_CANARY' not in marks.path.read_text(encoding='utf-8')

    stopped = False
    seen.clear()
    assert _mark_sleep_texts(idea, marks, ask, should_stop=lambda: False, num_ctx=2048)
    assert not marks.home('昔の文000。')
    assert '新しい一般文。' not in ''.join(seen)
