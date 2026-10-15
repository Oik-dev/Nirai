"""Googleが正本の無料枠：429分類、上位復帰、永久休み。実通信不要。"""

import io
import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

from mind.core.routing.api_failure import Rest
from mind.core.routing.decision import decide_brains
from mind.core.routing.registry import BrainEntry


def error(code, details=None, status=None):
    body = json.dumps({'error': {'status': status, 'details': details or [],
                                 'message': '私的な会話をログに書かないこと'}}).encode()
    return HTTPError('https://example.test', code, '', {}, io.BytesIO(body))


NOW = datetime(2026, 10, 15, 20, tzinfo=timezone.utc)


def test_daily_reset_uses_pacific_midnight_and_dst():
    rest = Rest()
    info = rest.failed(error(429, [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                                    'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}]), now=NOW)
    assert info.reason == 'daily' and info.rest_seconds > 0
    assert not rest.ready(NOW)
    assert rest.ready(NOW + timedelta(days=1))
    at_dstu = datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc)
    later = Rest()
    sleep = later.failed(error(429, status='quota_exceeded'), now=at_dstu)
    assert sleep.rest_seconds == 24 * 3600 + 30 * 60  # 00:30 PDT → 翌00:00 PST


def test_short_unusable_and_unknown_errors():
    short = Rest()
    got = short.failed(error(429, [{'@type': 'type.googleapis.com/google.rpc.RetryInfo',
                                   'retryDelay': '33s'}]), now=NOW)
    assert got.reason == 'short' and got.rest_seconds == 33
    assert not short.ready(NOW + timedelta(seconds=32))
    assert short.ready(NOW + timedelta(seconds=33))
    assert Rest().failed(error(429)).rest_seconds == 60
    assert Rest().failed(error(503)).reason == 'short'
    assert Rest().failed(URLError('unreachable')).reason == 'short'
    for code in (400, 401, 402, 403, 404):
        rest = Rest()
        assert rest.failed(error(code)).reason == 'unusable'
        assert not rest.ready(NOW + timedelta(days=365))


def test_cloud_order_and_rest_recovery():
    entries = [BrainEntry('gemma', 'ollama', 'local', 'primary', 'small')]
    entries += [BrainEntry(name, 'gemini', 'cloud', 'conversation', 'large')
                for name in ('3.8', '3.7', '3.5')]
    rest = {'3.8': Rest(), '3.7': Rest(), '3.5': Rest()}
    def order(at):
        return decide_brains(registry=entries, now=at, topic_home=False,
                             is_alive=lambda name: rest[name].ready(at))
    assert order(NOW) == ['3.8', '3.7', '3.5', 'gemma']
    rest['3.8'].failed(error(429), now=NOW)
    assert order(NOW) == ['3.7', '3.5', 'gemma']
    assert order(NOW + timedelta(seconds=61))[0] == '3.8'
    assert decide_brains(registry=entries, now=NOW, topic_home=True) == ['gemma']
