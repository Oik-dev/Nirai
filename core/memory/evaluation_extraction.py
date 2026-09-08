from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ExtractionEvaluationError(RuntimeError):
    pass


class MemoryExtractor(Protocol):
    model: str

    def extract(self, text: str, *, speaker_id: str = "master") -> list["ExtractionCandidate"]: ...


@dataclass(frozen=True)
class ExtractionCandidate:
    quote: str
    kind: str
    subject: str
    attribute: str
    value: str
    statement: str
    certainty: str
    explicit_correction: bool = False
    previous_value: str | None = None


@dataclass(frozen=True)
class ExtractionExpectation:
    kind: str
    required_terms: tuple[str, ...]
    certainty: str
    subject: str | None = None
    explicit_correction: bool = False
    previous_value: str | None = None


@dataclass(frozen=True)
class ExtractionCase:
    case_id: str
    text: str
    expected: tuple[ExtractionExpectation, ...]
    speaker_id: str = "master"


@dataclass(frozen=True)
class ExtractionCaseResult:
    case_id: str
    passed: bool
    expected_count: int
    candidate_count: int
    matched_count: int
    unsupported_quotes: tuple[str, ...]
    latency_ms: float


@dataclass(frozen=True)
class ExtractionReport:
    model: str
    cases: tuple[ExtractionCaseResult, ...]

    @property
    def passed(self) -> int:
        return sum(item.passed for item in self.cases)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def candidate_precision(self) -> float:
        total_candidates = sum(item.candidate_count for item in self.cases)
        total_matched = sum(item.matched_count for item in self.cases)
        return total_matched / total_candidates if total_candidates else 1.0

    @property
    def candidate_recall(self) -> float:
        total_expected = sum(item.expected_count for item in self.cases)
        total_matched = sum(item.matched_count for item in self.cases)
        return total_matched / total_expected if total_expected else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "passed": self.passed,
            "total": self.total,
            "candidate_precision": round(self.candidate_precision, 4),
            "candidate_recall": round(self.candidate_recall, 4),
            "cases": [
                {
                    "case_id": item.case_id,
                    "passed": item.passed,
                    "expected_count": item.expected_count,
                    "candidate_count": item.candidate_count,
                    "matched_count": item.matched_count,
                    "unsupported_quotes": list(item.unsupported_quotes),
                    "latency_ms": round(item.latency_ms, 3),
                }
                for item in self.cases
            ],
        }


GEMINI_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "kind": {"type": "string", "enum": ["fact", "promise", "preference", "relationship"]},
                    "subject": {"type": "string", "enum": ["master", "resident", "other"]},
                    "attribute": {"type": "string"},
                    "value": {"type": "string"},
                    "statement": {"type": "string"},
                    "certainty": {"type": "string", "enum": ["confirmed", "hypothesis"]},
                    "explicit_correction": {"type": "boolean"},
                    "previous_value": {"type": ["string", "null"]},
                },
                "required": [
                    "quote",
                    "kind",
                    "subject",
                    "attribute",
                    "value",
                    "statement",
                    "certainty",
                    "explicit_correction",
                    "previous_value",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


EXTRACTION_INSTRUCTION = """
You are a background memory extractor. Extract only durable information explicitly supported by the single RAW utterance below.

Keep only:
- confirmed durable facts
- promises / future commitments
- sustained preferences
- sustained relationship changes
- uncertain but potentially durable plans only as certainty=hypothesis

Kind rules:
- preference = stable likes/dislikes or choices only
- promise = an explicit commitment/agreement to do something
- relationship = sustained relationship state/change
- fact = plans, status, identity, dates, codes, and other durable propositions, including uncertain plans when certainty=hypothesis

Do not keep:
- one-off mood, meal, weather, or incidental activity
- guesses or implications not stated in RAW

Rules:
1. Every candidate MUST contain quote copied verbatim as a contiguous substring of RAW.
2. subject must identify who the information is about. Do not swap speakers. The RAW speaker is provided separately.
3. certainty is confirmed or hypothesis. Words like "maybe", "might", "not decided" require hypothesis.
4. explicit_correction is true only when RAW explicitly corrects/replaces an earlier value.
5. previous_value is only the old value explicitly stated by RAW; otherwise null.
6. Preserve the RAW language and lexical values. Never translate values such as 赤→red or 青→blue.
7. If a sentence contains both a durable plan and a separate commitment, extracting both is allowed when both independently matter later.
8. If nothing deserves durable memory, return an empty candidates array.
9. Return JSON only. No markdown.

Schema:
{
  "candidates": [
    {
      "quote": "exact RAW substring",
      "kind": "fact|promise|preference|relationship",
      "subject": "master|resident|other",
      "attribute": "short stable attribute name",
      "value": "concise value",
      "statement": "source-grounded concise statement",
      "certainty": "confirmed|hypothesis",
      "explicit_correction": false,
      "previous_value": null
    }
  ]
}
""".strip()


class GeminiMemoryExtractor:
    """Evaluation adapter for Gemini structured extraction.

    The model is intentionally not trusted as the memory authority. It only
    proposes typed candidates; the evaluation scorer and product mutation gate
    still verify exact Raw evidence and semantics before commit.
    """

    def __init__(
        self,
        nirai_root: Path,
        *,
        model: str = "gemini-3.5-flash-lite",
        timeout_sec: float = 60.0,
        poll_interval_sec: float = 0.5,
    ) -> None:
        from core.brains.gemini import load_gemini_api_key

        self.model = model
        self.timeout_sec = timeout_sec
        self.poll_interval_sec = poll_interval_sec
        self.api_key = load_gemini_api_key(nirai_root)
        if self.api_key is None:
            raise ExtractionEvaluationError("GEMINI_API_KEY was not found in world/.env")

    def extract(self, text: str, *, speaker_id: str = "master") -> list[ExtractionCandidate]:
        try:
            return asyncio.run(self._extract_async(text, speaker_id=speaker_id))
        except ExtractionEvaluationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExtractionEvaluationError(f"Gemini extraction failed: {exc}") from exc

    async def _extract_async(self, text: str, *, speaker_id: str) -> list[ExtractionCandidate]:
        from core.brains.gemini import _extract_interaction_text, _request_json_async

        payload = {
            "model": self.model,
            "system_instruction": EXTRACTION_INSTRUCTION,
            "input": f"RAW SPEAKER: {speaker_id}\nRAW:\n{text}",
            "store": False,
            "generation_config": {
                "thinking_level": "minimal",
                "seed": 1,
            },
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": GEMINI_EXTRACTION_SCHEMA,
            },
        }

        async def execute() -> dict[str, Any]:
            response = await _request_json_async(self.api_key, "/interactions", payload)
            interaction_id = response.get("id")
            while True:
                status = response.get("status")
                if status == "completed":
                    return response
                if status not in {"queued", "in_progress"}:
                    raise ExtractionEvaluationError(
                        f"Gemini interaction failed: status={status}"
                    )
                if not isinstance(interaction_id, str) or not interaction_id:
                    raise ExtractionEvaluationError("Gemini interaction returned no id")
                await asyncio.sleep(self.poll_interval_sec)
                response = await _request_json_async(
                    self.api_key,
                    f"/interactions/{quote(interaction_id, safe='')}",
                    method="GET",
                )

        try:
            response = await asyncio.wait_for(execute(), timeout=self.timeout_sec)
        except asyncio.TimeoutError as exc:
            raise ExtractionEvaluationError("Gemini extraction timed out") from exc

        try:
            parsed = json.loads(_extract_interaction_text(response))
        except json.JSONDecodeError as exc:
            raise ExtractionEvaluationError("Gemini structured output was not JSON") from exc
        raw_candidates = parsed.get("candidates") if isinstance(parsed, dict) else None
        if not isinstance(raw_candidates, list):
            raise ExtractionEvaluationError("Gemini response candidates must be a list")
        return [_candidate(item) for item in raw_candidates]


class OllamaMemoryExtractor:
    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_sec: float = 180.0,
        num_gpu: int = 0,
        keep_alive: str = "10m",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.num_gpu = num_gpu
        self.keep_alive = keep_alive

    def extract(self, text: str, *, speaker_id: str = "master") -> list[ExtractionCandidate]:
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": f"{EXTRACTION_INSTRUCTION}\n\nRAW SPEAKER: {speaker_id}\nRAW:\n{text}",
                "stream": False,
                "format": "json",
                "think": False,
                "keep_alive": self.keep_alive,
                "options": {
                    "num_gpu": self.num_gpu,
                    "temperature": 0,
                    "num_ctx": 4096,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/generate",
            data=payload,
            method="POST",
            headers={"content-type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise ExtractionEvaluationError(f"Ollama extraction failed: {exc}") from exc
        raw_response = envelope.get("response") if isinstance(envelope, dict) else None
        if not isinstance(raw_response, str):
            raise ExtractionEvaluationError("Ollama extraction returned no response text")
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise ExtractionEvaluationError("Extractor response was not JSON") from exc
        raw_candidates = parsed.get("candidates") if isinstance(parsed, dict) else None
        if not isinstance(raw_candidates, list):
            raise ExtractionEvaluationError("Extractor response candidates must be a list")
        return [_candidate(item) for item in raw_candidates]

    def unload(self) -> None:
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": "",
                "stream": False,
                "keep_alive": 0,
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/generate",
            data=payload,
            method="POST",
            headers={"content-type": "application/json"},
        )
        try:
            with urlopen(request, timeout=30.0):
                return
        except Exception:
            return


class ExtractionEvaluationHarness:
    def __init__(self, cases: tuple[ExtractionCase, ...]) -> None:
        self.cases = cases

    def run(self, extractor: MemoryExtractor) -> ExtractionReport:
        results: list[ExtractionCaseResult] = []
        for case in self.cases:
            started = perf_counter()
            candidates = extractor.extract(case.text, speaker_id=case.speaker_id)
            latency_ms = (perf_counter() - started) * 1000.0
            results.append(_score_case(case, candidates, latency_ms))
        return ExtractionReport(model=extractor.model, cases=tuple(results))


def load_extraction_fixture(path) -> tuple[ExtractionCase, ...]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = parsed.get("cases") if isinstance(parsed, dict) else None
    if not isinstance(raw_cases, list):
        raise ExtractionEvaluationError("extraction fixture cases must be a list")
    cases: list[ExtractionCase] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ExtractionEvaluationError("extraction case must be an object")
        raw_expected = raw_case.get("expected")
        if not isinstance(raw_expected, list):
            raise ExtractionEvaluationError("extraction expected must be a list")
        expected = tuple(
            ExtractionExpectation(
                kind=str(item["kind"]),
                required_terms=tuple(str(term) for term in item.get("required_terms", [])),
                certainty=str(item["certainty"]),
                subject=(str(item["subject"]) if item.get("subject") is not None else None),
                explicit_correction=item.get("explicit_correction") is True,
                previous_value=(str(item["previous_value"]) if item.get("previous_value") is not None else None),
            )
            for item in raw_expected
            if isinstance(item, dict)
        )
        cases.append(
            ExtractionCase(
                case_id=str(raw_case["case_id"]),
                text=str(raw_case["text"]),
                expected=expected,
                speaker_id=str(raw_case.get("speaker_id", "master")),
            )
        )
    return tuple(cases)


def _candidate(value: Any) -> ExtractionCandidate:
    if not isinstance(value, dict):
        raise ExtractionEvaluationError("candidate must be an object")
    previous = value.get("previous_value")
    return ExtractionCandidate(
        quote=str(value.get("quote", "")),
        kind=str(value.get("kind", "")),
        subject=str(value.get("subject", "")),
        attribute=str(value.get("attribute", "")),
        value=str(value.get("value", "")),
        statement=str(value.get("statement", "")),
        certainty=str(value.get("certainty", "")),
        explicit_correction=value.get("explicit_correction") is True,
        previous_value=str(previous) if previous is not None else None,
    )


def _score_case(
    case: ExtractionCase,
    candidates: list[ExtractionCandidate],
    latency_ms: float,
) -> ExtractionCaseResult:
    unsupported_quotes = tuple(
        candidate.quote
        for candidate in candidates
        if not candidate.quote or candidate.quote not in case.text
    )
    unmatched = list(range(len(candidates)))
    matched = 0
    for expected in case.expected:
        match_index = next(
            (
                index
                for index in unmatched
                if _matches_expectation(expected, candidates[index])
                and candidates[index].quote in case.text
            ),
            None,
        )
        if match_index is not None:
            unmatched.remove(match_index)
            matched += 1
    passed = (
        matched == len(case.expected)
        and len(candidates) == len(case.expected)
        and not unsupported_quotes
    )
    return ExtractionCaseResult(
        case_id=case.case_id,
        passed=passed,
        expected_count=len(case.expected),
        candidate_count=len(candidates),
        matched_count=matched,
        unsupported_quotes=unsupported_quotes,
        latency_ms=latency_ms,
    )


def _matches_expectation(
    expected: ExtractionExpectation,
    candidate: ExtractionCandidate,
) -> bool:
    searchable = " ".join(
        [candidate.attribute, candidate.value, candidate.statement, candidate.quote]
    )
    return (
        candidate.kind == expected.kind
        and candidate.certainty == expected.certainty
        and (expected.subject is None or candidate.subject == expected.subject)
        and candidate.explicit_correction == expected.explicit_correction
        and (
            expected.previous_value is None
            or candidate.previous_value == expected.previous_value
        )
        and all(term in searchable for term in expected.required_terms)
    )
