from __future__ import annotations

import asyncio
from pathlib import Path

from core.brains import gemini as gemini_module
from core.memory.structured import GeminiWorldMemoryProcessor


def write_key(root: Path) -> None:
    world = root / "world"
    world.mkdir(parents=True, exist_ok=True)
    (world / ".env").write_text("GEMINI_API_KEY=test-secret\n", encoding="utf-8")


def test_gemini_world_memory_processor_extracts_schema_grounded_candidates(monkeypatch, tmp_path: Path) -> None:
    write_key(tmp_path)
    calls: list[tuple[str, dict | None, str | None]] = []

    async def fake_request(api_key: str, path: str, payload: dict | None = None, *, method: str | None = None) -> dict:
        assert api_key == "test-secret"
        calls.append((path, payload, method))
        return {
            "id": "INT-MEMORY",
            "status": "completed",
            "steps": [{
                "type": "model_output",
                "content": [{
                    "type": "text",
                    "text": '{"candidates":[{"quote":"今は青のほうが好き","kind":"preference","subject":"master","attribute":"favorite_color","value":"青","statement":"Masterは今は青のほうが好き","certainty":"confirmed","explicit_correction":true,"previous_value":"赤"}]}',
                }],
            }],
        }

    monkeypatch.setattr(gemini_module, "_request_json_async", fake_request)
    # structured.py imported the function directly, so patch that module too.
    import core.memory.structured as structured_module
    monkeypatch.setattr(structured_module, "_request_json_async", fake_request)

    processor = GeminiWorldMemoryProcessor(tmp_path)
    candidates = asyncio.run(processor.extract(
        "前に好きな色は赤って言ったけど、今は青のほうが好き。",
        speaker_id="master",
    ))

    assert candidates[0].value == "青"
    assert candidates[0].previous_value == "赤"
    assert calls[0][0] == "/interactions"
    payload = calls[0][1]
    assert payload is not None
    assert payload["store"] is False
    assert payload["generation_config"] == {"thinking_level": "minimal", "seed": 1}
    assert payload["response_format"]["mime_type"] == "application/json"
    assert payload["system_instruction"]
    assert payload["input"].startswith("RAW SPEAKER: master")


def test_gemini_world_memory_processor_batches_document_embeddings_in_one_request(monkeypatch, tmp_path: Path) -> None:
    write_key(tmp_path)
    calls: list[tuple[str, dict | None]] = []

    async def fake_request(api_key: str, path: str, payload: dict | None = None, *, method: str | None = None) -> dict:
        assert api_key == "test-secret"
        calls.append((path, payload))
        return {
            "embeddings": [
                {"values": [0.1, 0.2, 0.3]},
                {"values": [0.4, 0.5, 0.6]},
            ]
        }

    import core.memory.structured as structured_module
    monkeypatch.setattr(structured_module, "_request_json_async", fake_request)
    processor = GeminiWorldMemoryProcessor(tmp_path, embedding_dim=3)

    vectors = asyncio.run(processor.embed_documents(["記憶A", "記憶B"]))

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert len(calls) == 1
    assert calls[0][0] == "/models/gemini-embedding-2:batchEmbedContents"
    requests = calls[0][1]["requests"] if calls[0][1] is not None else []
    assert len(requests) == 2
    assert all(item["embedContentConfig"]["taskType"] == "RETRIEVAL_DOCUMENT" for item in requests)
    assert all(item["embedContentConfig"]["autoTruncate"] is False for item in requests)


def test_gemini_world_memory_processor_separates_document_and_query_embedding_tasks(monkeypatch, tmp_path: Path) -> None:
    write_key(tmp_path)
    tasks: list[str] = []
    auto_truncate_values: list[bool] = []

    async def fake_request(api_key: str, path: str, payload: dict | None = None, *, method: str | None = None) -> dict:
        assert api_key == "test-secret"
        assert path == "/models/gemini-embedding-2:embedContent"
        assert payload is not None
        tasks.append(payload["embedContentConfig"]["taskType"])
        auto_truncate_values.append(payload["embedContentConfig"]["autoTruncate"])
        return {"embedding": {"values": [0.1, 0.2, 0.3]}}

    import core.memory.structured as structured_module
    monkeypatch.setattr(structured_module, "_request_json_async", fake_request)
    processor = GeminiWorldMemoryProcessor(tmp_path, embedding_dim=3)

    document = asyncio.run(processor.embed_document("公開記憶"))
    query = asyncio.run(processor.embed_query("何を覚えてる？"))

    assert document == [0.1, 0.2, 0.3]
    assert query == [0.1, 0.2, 0.3]
    assert tasks == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
    assert auto_truncate_values == [False, False]
