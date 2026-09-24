from __future__ import annotations

from pathlib import Path

from core.brains import gemini as gemini_module
from core.memory.evaluation_extraction import (
    ExtractionCandidate,
    ExtractionEvaluationHarness,
    GeminiMemoryExtractor,
    load_extraction_fixture,
)


FIXTURE = Path(__file__).with_name("memory_extraction_golden_v1.json")


def test_extraction_scorer_requires_exact_raw_quote_and_rejects_extra_candidates() -> None:
    case = next(
        item for item in load_extraction_fixture(FIXTURE) if item.case_id == "extract-exact-code"
    )

    class BadExtractor:
        model = "bad"

        def extract(self, text: str, *, speaker_id: str = "master"):
            return [
                ExtractionCandidate(
                    quote="ZX-7419というコードだった",
                    kind="fact",
                    subject="master",
                    attribute="maintenance_code",
                    value="ZX-7419",
                    statement="保守用合言葉はZX-7419",
                    certainty="confirmed",
                ),
                ExtractionCandidate(
                    quote="型番まで正確に覚えておいて。",
                    kind="fact",
                    subject="master",
                    attribute="invented",
                    value="存在しない事実",
                    statement="存在しない事実",
                    certainty="confirmed",
                ),
            ]

    report = ExtractionEvaluationHarness((case,)).run(BadExtractor())  # type: ignore[arg-type]

    assert report.passed == 0
    assert report.cases[0].unsupported_quotes == ("ZX-7419というコードだった",)
    assert report.candidate_precision < 1.0


def test_gemini_extractor_uses_interactions_structured_output(monkeypatch, tmp_path: Path) -> None:
    world = tmp_path / "world"
    world.mkdir(parents=True)
    (world / ".env").write_text("GEMINI_API_KEY=test-secret\n", encoding="utf-8")
    calls: list[tuple[str, dict | None, str | None]] = []

    async def fake_request(api_key: str, path: str, payload: dict | None = None, *, method: str | None = None) -> dict:
        assert api_key == "test-secret"
        calls.append((path, payload, method))
        return {
            "id": "INT-MEM",
            "status": "completed",
            "steps": [{
                "type": "model_output",
                "content": [{
                    "type": "text",
                    "text": '{"candidates":[{"quote":"ZX-7419","kind":"fact","subject":"master","attribute":"maintenance_code","value":"ZX-7419","statement":"保守用合言葉はZX-7419","certainty":"confirmed","explicit_correction":false,"previous_value":null}]}',
                }],
            }],
        }

    monkeypatch.setattr(gemini_module, "_request_json_async", fake_request)
    extractor = GeminiMemoryExtractor(tmp_path)
    candidates = extractor.extract("保守用合言葉はZX-7419。")

    assert candidates[0].value == "ZX-7419"
    assert calls[0][0] == "/interactions"
    payload = calls[0][1]
    assert payload is not None
    assert payload["model"] == "gemini-3.5-flash-lite"
    assert payload["response_format"]["mime_type"] == "application/json"
    schema = payload["response_format"]["schema"]
    assert schema["properties"]["candidates"]["items"]["properties"]["certainty"]["enum"] == [
        "confirmed",
        "hypothesis",
    ]


def test_extraction_scorer_accepts_empty_output_for_one_off_event() -> None:
    case = next(
        item for item in load_extraction_fixture(FIXTURE) if item.case_id == "reject-one-off-mood"
    )

    class EmptyExtractor:
        model = "empty"

        def extract(self, text: str, *, speaker_id: str = "master"):
            return []

    report = ExtractionEvaluationHarness((case,)).run(EmptyExtractor())  # type: ignore[arg-type]

    assert report.passed == 1
    assert report.candidate_precision == 1.0
    assert report.candidate_recall == 1.0
