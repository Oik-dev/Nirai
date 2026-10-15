"""Interactions API：問い合わせは問いだけ。会話・人格・記憶は送信しない。"""

from __future__ import annotations

import json
from urllib.parse import quote
from urllib.request import Request, urlopen

from mind.core.routing.api_failure import Rest

BASE = "https://generativelanguage.googleapis.com/v1beta/interactions"


class Antigravity:
    def __init__(self, api_key: str | None, *, timeout: float = 12.0):
        self._key = api_key
        self._timeout = timeout
        self.rest = Rest()

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    def ready(self, now=None) -> bool:
        return self.enabled and self.rest.ready(now)

    def _call(self, url: str, data: dict | None = None) -> dict:
        request = Request(url, data=json.dumps(data).encode('utf-8') if data is not None else None,
                          headers={'Content-Type': 'application/json', 'x-goog-api-key': self._key})
        try:
            with urlopen(request, timeout=self._timeout) as response:
                return json.load(response)
        except Exception as exc:
            self.rest.failed(exc)
            raise

    def start(self, question: str, *, agent: str, model: str, max_tokens: int) -> str:
        if not self.ready():
            raise RuntimeError('Antigravity unavailable')
        instruction = ('公開の資料から次の問いを調べて。最大5件、JSON配列だけで回答すること。'
                       '[{"title":"","url":"","excerpt":""}]。'
                       'excerptは資料に忠実に400字以内。資料にない事実を作らない。\n【問い】' + question)
        data = {'agent': agent, 'input': instruction,
                'tools': [{'type': 'google_search'}, {'type': 'url_context'}],
                'agent_config': {'type': 'antigravity', 'model': model,
                                 'max_total_tokens': max_tokens},
                'background': True, 'environment': 'remote'}
        response = self._call(BASE, data)
        identifier = response.get('id')
        if not isinstance(identifier, str) or not identifier:
            raise ValueError('Antigravity missing interaction id')
        return identifier.removeprefix('interactions/')

    def get(self, identifier: str) -> dict:
        identifier = identifier.removeprefix('interactions/')
        if not identifier or '/' in identifier:
            raise ValueError('invalid interaction id')
        return self._call(f'{BASE}/{quote(identifier, safe="")}')

    def cancel(self, identifier: str) -> None:
        self._call(f'{BASE}/{quote(identifier.removeprefix("interactions/"), safe="")}:cancel', {})


def result_rows(result: dict) -> list[dict]:
    """実際のREST output_text またはoutputsのtextのみ読み、検索先URLを検査する。"""
    from urllib.parse import urlsplit
    from mind.core.chores.explore import trimmed_results

    text = result.get('output_text')
    if not isinstance(text, str):
        outputs = result.get('outputs', result.get('output', []))
        def fragments(value):
            if isinstance(value, dict):
                if isinstance(value.get('text'), str):
                    yield value['text']
                for key in ('content', 'parts', 'outputs', 'output'):
                    if key in value:
                        yield from fragments(value[key])
            elif isinstance(value, list):
                for item in value:
                    yield from fragments(item)
        text = ''.join(fragments(outputs))
    first, last = text.find('['), text.rfind(']')
    if first < 0 or last <= first:
        raise ValueError('Antigravity output missing JSON array')
    rows = json.loads(text[first:last + 1])
    if not isinstance(rows, list):
        raise ValueError('Antigravity output not a list')
    safe = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('url'), str):
            continue
        url = urlsplit(row['url'])
        if url.scheme not in ('https', 'http') or not url.netloc:
            continue
        safe.append(row)
    return trimmed_results(safe)
