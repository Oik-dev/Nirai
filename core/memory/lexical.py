"""Shared text-only matching rules; public/private storage and retrieval stay separate."""
from __future__ import annotations

import re


def raw_search_terms(text: str) -> list[str]:
    runs = [
        match.group(0).casefold()
        for match in re.finditer(r"[0-9A-Za-z_\u3040-\u30ff\u3400-\u9fff]+", text)
        if match.group(0)
    ]
    terms: list[str] = []
    for run in runs:
        if len(run) < 2:
            continue
        terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(terms))


def normalize_lexical_text(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z一-龥ぁ-んァ-ヴー]+", "", text).casefold()


def has_distinctive_anchor_match(question: str, text: str) -> bool:
    query_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", question)
        if len(token) >= 2
        and (len(token) >= 4 or any(char.isdigit() for char in token) or any(char in "._:/-" for char in token))
    }
    if not query_tokens:
        return False
    haystack = text.casefold()
    return any(token in haystack for token in query_tokens)
