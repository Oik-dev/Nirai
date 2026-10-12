"""Geminiの会話通訳。失敗はCoreが同一ターンで手元へ戻す。"""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.request import Request, urlopen

from mind.core.context.pack import ContextPack
from mind.core.feeling.appraisal import appraisal_question
from mind.core.perception import BodyCatalog, body_alone_question


_CATEGORIES = (
    'HARM_CATEGORY_HARASSMENT', 'HARM_CATEGORY_HATE_SPEECH',
    'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'HARM_CATEGORY_DANGEROUS_CONTENT',
)


class GeminiAdapter:
    def __init__(self, api_key: str | None, model: str, timeout: float = 90.0) -> None:
        self._key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    @staticmethod
    def _text(event: dict) -> str:
        return ''.join(p.get('text', '') for c in event.get('candidates', [])
                       for p in c.get('content', {}).get('parts', [])
                       if not p.get('thought') and isinstance(p.get('text'), str))

    def _request(self, prompt: str, *, stream: bool, as_json: bool = False, think: bool = False):
        if not self.enabled:
            raise RuntimeError('Gemini API key is missing')
        method = 'streamGenerateContent?alt=sse' if stream else 'generateContent'
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{self._model}:{method}'
        config: dict = {'thinkingConfig': {'thinkingLevel': 'high' if think else 'low'}}
        if as_json:
            config['responseMimeType'] = 'application/json'
        data = json.dumps({
            'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
            'generationConfig': config,
            'safetySettings': [
                {'category': category, 'threshold': 'BLOCK_NONE'} for category in _CATEGORIES
            ],
        }).encode('utf-8')
        request = Request(url, data=data, headers={
            'Content-Type': 'application/json', 'x-goog-api-key': self._key,
        })
        if not stream:
            with urlopen(request, timeout=self._timeout) as response:
                result = json.load(response)
            return self._text(result)
        def events():
            finished = False
            with urlopen(request, timeout=self._timeout) as response:
                for line in response:
                    if not line.startswith(b'data: '):
                        continue
                    event = json.loads(line[6:])
                    for candidate in event.get('candidates', []):
                        if candidate.get('finishReason') not in (None, 'STOP'):
                            raise RuntimeError('Gemini stream was interrupted')
                        if candidate.get('finishReason') == 'STOP':
                            finished = True
                    yield self._text(event)
            if not finished:
                raise RuntimeError('Gemini stream ended without STOP')
        return events()

    def converse(
        self, pack: ContextPack, *, think: bool = False,
        on_token: Callable[[str], None] | None = None,
        on_reply: Callable[[str], None] | None = None,
        catalog: BodyCatalog | None = None,
        on_body: Callable[[dict], None] | None = None,
    ) -> dict:
        chunks: list[str] = []
        for chunk in self._request(pack.render(), stream=True, think=think):
            if chunk:
                chunks.append(chunk)
                if on_token:
                    on_token(chunk)
        reply = ''.join(chunks).strip()
        if not reply:
            raise RuntimeError('Gemini returned no reply')
        if on_reply:
            on_reply(reply)
        appraisal = None
        try:
            prompt = f'{pack.render()}\n【あなたが今返した言葉】\n{reply}\n\n{appraisal_question(catalog)}'
            appraisal = json.loads(self._request(prompt, stream=False, as_json=True))
            if not isinstance(appraisal, dict):
                appraisal = None
            if appraisal is not None and catalog is not None and on_body is not None:
                on_body(appraisal)
        except Exception:
            appraisal = None  # 返事は確定した。裏方失敗では再生成しない。
        return {'reply': reply, 'appraisal': appraisal}

    def raw_call(self, prompt: str) -> str:
        return self._request(prompt, stream=False)

    def choose_pulse(self, prompt: str, *, on_approach=None) -> dict:
        return json.loads(self._request(prompt, stream=False, as_json=True))

    def choose_body(self, prompt: str, said: str, catalog: BodyCatalog) -> dict | None:
        request = f'{prompt}\n【自分が話した言葉】\n{said}\n\n{body_alone_question(catalog)}'
        return json.loads(self._request(request, stream=False, as_json=True))
