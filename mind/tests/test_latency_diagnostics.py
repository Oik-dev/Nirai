"""No live idea or network: timings must preserve calls, callbacks and privacy."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mind.brains.ollama import serve
from mind.brains.ollama.adapter import OllamaAdapter
from mind.core import debug_log, runtime
from mind.core.config import ThresholdsConfig
from mind.core.routing.home_marks import HomeMarks
from mind.core.routing.registry import BrainEntry
from mind.core.runtime import Core

CANARY = 'PRIVATE-CONTENT-CANARY'
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.fixture
def log_path(tmp_path):
    path = tmp_path / 'debug.jsonl'
    debug_log.configure(path)
    yield path
    debug_log.configure(None)


def rows(path):
    text = path.read_text(encoding='utf-8')
    assert CANARY not in text
    return [json.loads(line) for line in text.splitlines()]


def test_timing_preserves_exception_without_logging_message(log_path, monkeypatch):
    clock = iter((10.0, 10.25))
    monkeypatch.setattr(debug_log.time, 'monotonic', lambda: next(clock))
    with pytest.raises(ValueError, match=CANARY):
        with debug_log.timing('recall'):
            raise ValueError(CANARY)
    events = rows(log_path)
    assert [row['action'] for row in events] == ['started', 'finished']
    assert events[-1]['elapsed_ms'] == 250


def test_routed_timings_preserve_judge_and_reply_callbacks(log_path, tmp_path):
    calls = []
    callbacks = []

    class Brain:
        def judge(self, prompt):
            calls.append('judge')
            return {'search': False, 'home': []}

        def converse(self, pack, *, on_token, on_reply, **kwargs):
            calls.append('converse')
            on_token(CANARY)
            on_reply(CANARY)
            assert callbacks == ['token']  # Core intentionally stages the reply.
            calls.append('appraisal')
            return {'reply': CANARY}

    core = Core(CANARY, CANARY, ThresholdsConfig(),
                registry=[BrainEntry('mock', 'ollama', 'local', 'primary', 'small')],
                brains={'mock': Brain()}, home_marks=HomeMarks(tmp_path))
    result = core.turn_routed('こんにちは', now=NOW,
                              on_token=lambda text: callbacks.append('token'),
                              on_reply=lambda text: callbacks.append('reply'))
    assert result.report.reply == CANARY
    assert calls == ['judge', 'converse', 'appraisal']
    assert callbacks == ['token', 'reply']
    events = rows(log_path)
    phases = [row['phase'] for row in events if row['action'] == 'started']
    assert phases == ['home_judge', 'advisor_window', 'think_decision', 'converse']
    start = next(row for row in events if row.get('phase') == 'converse' and row['action'] == 'started')
    assert start['think'] is False
    assert any(row['action'] == 'reply_generated' for row in events)


def test_failed_candidate_still_falls_back_once(log_path):
    calls = []

    class Brain:
        def __init__(self, fail):
            self.fail = fail

        def converse(self, pack, **kwargs):
            calls.append(self.fail)
            if self.fail:
                raise RuntimeError(CANARY)
            return {'reply': CANARY}

    registry = [BrainEntry('first', 'ollama', 'local', 'primary', 'small'),
                BrainEntry('second', 'ollama', 'local', 'fallback', 'small')]
    core = Core(CANARY, CANARY, ThresholdsConfig(), registry=registry,
                brains={'first': Brain(True), 'second': Brain(False)})
    used, report = core._obtain_valid_report('こんにちは', 'first',
                                            {e.name: e for e in registry}, 'second', [], now=NOW)
    assert used == 'second' and report['reply'] == CANARY
    assert calls == [True, False]
    assert any(row['action'] == 'candidate_failed' and row['brain'] == 'first' for row in rows(log_path))


def test_recall_and_lookup_are_measured_without_text(log_path):
    calls = []

    class Memory:
        def recall(self, cue, *, already):
            calls.append('recall')
            return [SimpleNamespace(page_id='mock-page', activation=1.0, text=CANARY)]

        def lookup(self, cue, remembered, **kwargs):
            calls.append('lookup')
            return [CANARY]

    core = Core('', '', ThresholdsConfig(), memory=Memory())
    assert core._recall('こんにちは', NOW) == [CANARY]
    assert core._lookup_chunks == [CANARY]
    assert calls == ['recall', 'lookup']
    assert [row['phase'] for row in rows(log_path) if row['action'] == 'started'] == ['recall', 'lookup']


@pytest.mark.parametrize('cached, expected_cached', [(None, None), (0, 0), (3, 3), (True, None), (-1, None), (CANARY, None)])
@pytest.mark.parametrize('streaming', [False, True])
def test_ollama_metrics_keep_only_nonnegative_integers(log_path, monkeypatch, streaming, cached, expected_cached):
    data = {'done': True, 'response': CANARY, 'thinking': CANARY, 'context': [1, 2],
            'load_duration': 123, 'prompt_eval_count': 4, 'prompt_eval_duration': 456,
            'prompt_eval_cached_count': cached,
            'eval_count': True, 'eval_duration': CANARY, 'total_duration': -1}
    requests = []

    class Response:
        def raise_for_status(self): pass
        def json(self): return data
        def iter_lines(self): return iter([json.dumps(data).encode()])
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def post(url, **kwargs):
        requests.append(kwargs['json'])
        return Response()

    monkeypatch.setattr(serve, 'post', post)
    heard = []
    adapter = OllamaAdapter()
    result = adapter._default_chat_call(CANARY, on_token=heard.append if streaming else None)
    assert result == CANARY
    assert len(requests) == 1
    assert requests[0]['prompt'] == CANARY and requests[0]['think'] is False
    assert heard == ([CANARY] if streaming else [])
    event, = rows(log_path)
    expected_keys = {'ts', 'kind', 'action', 'load_duration', 'prompt_eval_count', 'prompt_eval_duration'}
    if expected_cached is not None:
        expected_keys.add('prompt_eval_cached_count')
        assert event['prompt_eval_cached_count'] == expected_cached
    assert set(event) == expected_keys
    assert event['load_duration'] == 123


def test_log_write_failure_does_not_change_turn_or_metrics(tmp_path, monkeypatch):
    path = tmp_path / 'blocked'
    path.write_text('mock', encoding='utf-8')
    debug_log.configure(path / 'debug.jsonl')
    try:
        with debug_log.timing('converse'):
            OllamaAdapter._record_metrics({'load_duration': 3})
        assert path.read_text(encoding='utf-8') == 'mock'
    finally:
        debug_log.configure(None)
