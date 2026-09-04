import asyncio
from pathlib import Path

import pytest

from core.brains import gemini as gemini_module
from core.brains.base import BrainUnavailableError
from core.brains.gemini import GeminiDriver, list_gemini_models, load_gemini_api_key


def write_key(root: Path) -> None:
    world = root / "world"
    world.mkdir(parents=True, exist_ok=True)
    (world / ".env").write_text("GEMINI_API_KEY=test-secret\n", encoding="utf-8")


def completed_interaction(text: str, *, model: str = "gemini-3.5-flash") -> dict:
    return {
        "id": "INT-COMPLETE",
        "status": "completed",
        "model": model,
        "steps": [{
            "type": "model_output",
            "content": [{"type": "text", "text": text}],
        }],
    }


def test_load_gemini_api_key_reads_named_secret_without_exposing_it(tmp_path: Path) -> None:
    write_key(tmp_path)
    assert load_gemini_api_key(tmp_path) == "test-secret"


def test_gemini_driver_uses_interactions_structured_output_for_normal_model(monkeypatch, tmp_path: Path) -> None:
    write_key(tmp_path)
    calls: list[tuple[str, str, dict | None, str | None]] = []

    async def fake_request(api_key: str, path: str, payload: dict | None = None, *, method: str | None = None) -> dict:
        calls.append((api_key, path, payload, method))
        return completed_interaction('{"say":"こんにちは","actions":[],"pass":false,"to":null}')

    monkeypatch.setattr(gemini_module, "_request_json_async", fake_request)
    driver = GeminiDriver(tmp_path)

    response = asyncio.run(driver.think(
        "INV-GEMINI-1",
        "talk",
        {"name": "Kina", "persona": "短く話す。", "brain_model": "gemini-3.5-flash"},
        {"history": [{"from": "master", "text": "こんにちは"}]},
    ))

    assert response.say == "こんにちは"
    assert len(calls) == 1
    assert calls[0][0] == "test-secret"
    assert calls[0][1] == "/interactions"
    payload = calls[0][2]
    assert payload is not None
    assert payload["model"] == "gemini-3.5-flash"
    assert "agent" not in payload
    assert payload["response_format"]["mime_type"] == "application/json"
    assert payload["response_format"]["schema"]["properties"]["say"]["type"] == "string"


def test_gemini_driver_supports_task_consult_volunteer_schema(monkeypatch, tmp_path: Path) -> None:
    write_key(tmp_path)
    calls: list[dict] = []

    async def fake_request(api_key: str, path: str, payload: dict | None = None, *, method: str | None = None) -> dict:
        assert path == "/interactions"
        assert payload is not None
        calls.append(payload)
        return completed_interaction(
            '{"say":"相談する","actions":[],"pass":false,"to":null,"volunteer":false}'
        )

    monkeypatch.setattr(gemini_module, "_request_json_async", fake_request)
    driver = GeminiDriver(tmp_path)

    response = asyncio.run(driver.think(
        "INV-GEMINI-CONSULT",
        "consult",
        {"name": "Gemini", "persona": "短く話す。", "brain_model": "gemini-3.5-flash"},
        {"task_text": "相談案件", "can_agent_work": False, "current_residents": ["Gemini", "Codex"]},
    ))

    assert response.volunteer is False
    assert calls[0]["response_format"]["schema"]["properties"]["volunteer"]["type"] == "boolean"
    assert "volunteerは必ずfalse" in calls[0]["input"]


def test_antigravity_uses_background_interaction_and_poll(monkeypatch, tmp_path: Path) -> None:
    write_key(tmp_path)
    calls: list[tuple[str, dict | None, str | None]] = []

    async def fake_request(api_key: str, path: str, payload: dict | None = None, *, method: str | None = None) -> dict:
        calls.append((path, payload, method))
        if path == "/interactions":
            return {"id": "INT-AGENT", "status": "in_progress", "agent": "antigravity-preview-05-2026"}
        assert path == "/interactions/INT-AGENT"
        assert method == "GET"
        return completed_interaction(
            '{"say":"Agent返答","actions":[],"pass":false,"to":null}',
            model="antigravity-preview-05-2026",
        )

    monkeypatch.setattr(gemini_module, "_request_json_async", fake_request)
    monkeypatch.setattr(gemini_module, "GEMINI_POLL_INTERVAL_SEC", 0.0)
    driver = GeminiDriver(tmp_path)

    response = asyncio.run(driver.think(
        "INV-ANTIGRAVITY",
        "talk",
        {"name": "Kina", "persona": "短く話す。", "brain_model": "antigravity-preview-05-2026"},
        {"history": []},
    ))

    assert response.say == "Agent返答"
    assert len(calls) == 2
    first_payload = calls[0][1]
    assert first_payload is not None
    assert first_payload["agent"] == "antigravity-preview-05-2026"
    assert first_payload["background"] is True
    assert first_payload["environment"] == "remote"
    assert first_payload["tools"] == [
        {"type": "google_search"},
        {"type": "url_context"},
    ]
    assert "Web検索・URL参照を使って構いません" in first_payload["input"]
    assert "response_format" not in first_payload
    assert calls[1] == ("/interactions/INT-AGENT", None, "GET")


def test_gemini_cancel_stops_remote_interaction_and_cancels_poll(monkeypatch, tmp_path: Path) -> None:
    write_key(tmp_path)

    async def scenario() -> None:
        polling = asyncio.Event()
        cancelled = asyncio.Event()

        async def fake_request(api_key: str, path: str, payload: dict | None = None, *, method: str | None = None) -> dict:
            if path == "/interactions" and method is None:
                return {"id": "INT-CANCEL", "status": "in_progress", "agent": "antigravity-preview-05-2026"}
            if path == "/interactions/INT-CANCEL/cancel" and method == "POST":
                cancelled.set()
                return {"id": "INT-CANCEL", "status": "cancelled"}
            if path == "/interactions/INT-CANCEL" and method == "GET":
                polling.set()
                await asyncio.Event().wait()
            raise AssertionError((path, method))

        monkeypatch.setattr(gemini_module, "_request_json_async", fake_request)
        monkeypatch.setattr(gemini_module, "GEMINI_POLL_INTERVAL_SEC", 0.0)
        driver = GeminiDriver(tmp_path)
        think_task = asyncio.create_task(driver.think(
            "INV-GEMINI-CANCEL",
            "talk",
            {"name": "Kina", "brain_model": "antigravity-preview-05-2026"},
            {"history": []},
        ))

        await asyncio.wait_for(polling.wait(), timeout=1)
        assert await driver.cancel("INV-GEMINI-CANCEL") is True
        with pytest.raises(asyncio.CancelledError):
            await think_task
        assert cancelled.is_set()
        assert await driver.cancel("INV-GEMINI-CANCEL") is False

    asyncio.run(scenario())


def test_antigravity_timeout_cancels_remote_interaction(monkeypatch, tmp_path: Path) -> None:
    write_key(tmp_path)

    async def scenario() -> None:
        cancel_paths: list[str] = []

        async def fake_request(api_key: str, path: str, payload: dict | None = None, *, method: str | None = None) -> dict:
            if path == "/interactions" and method is None:
                return {"id": "INT-TIMEOUT", "status": "in_progress", "agent": "antigravity-preview-05-2026"}
            if path == "/interactions/INT-TIMEOUT" and method == "GET":
                await asyncio.Event().wait()
            if path == "/interactions/INT-TIMEOUT/cancel" and method == "POST":
                cancel_paths.append(path)
                return {"id": "INT-TIMEOUT", "status": "cancelled"}
            raise AssertionError((path, method))

        monkeypatch.setattr(gemini_module, "_request_json_async", fake_request)
        monkeypatch.setattr(gemini_module, "GEMINI_POLL_INTERVAL_SEC", 0.0)
        monkeypatch.setattr(gemini_module, "GEMINI_TIMEOUT_SEC", 0.02)
        driver = GeminiDriver(tmp_path)

        with pytest.raises(BrainUnavailableError, match="interaction timed out"):
            await driver.think(
                "INV-GEMINI-TIMEOUT",
                "talk",
                {"name": "Kina", "brain_model": "antigravity-preview-05-2026"},
                {"history": []},
            )

        assert cancel_paths == ["/interactions/INT-TIMEOUT/cancel"]
        assert "INV-GEMINI-TIMEOUT" not in driver._interaction_ids

    asyncio.run(scenario())


def test_gemini_truncated_content_length_becomes_brain_error() -> None:
    async def scenario() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"partial":')
        reader.feed_eof()

        with pytest.raises(BrainUnavailableError, match="connection closed"):
            await gemini_module._read_http_response_body(reader, {"content-length": "64"})

    asyncio.run(scenario())


def test_gemini_model_catalog_keeps_text_and_antigravity_but_excludes_specialized_models(monkeypatch, tmp_path: Path) -> None:
    write_key(tmp_path)

    def fake_request(api_key: str, path: str, payload: dict | None = None) -> dict:
        assert api_key == "test-secret"
        assert path.startswith("/models")
        assert payload is None
        return {
            "models": [
                {
                    "name": "models/gemini-3.7-flash",
                    "displayName": "Gemini 3.7 Flash",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/antigravity-preview-05-2026",
                    "displayName": "Antigravity Agent Preview",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-3.1-flash-image",
                    "displayName": "Image",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/text-embedding-999",
                    "displayName": "Embedding",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        }

    monkeypatch.setattr(gemini_module, "_request_json", fake_request)

    assert list_gemini_models(tmp_path) == [
        {"id": "gemini-3.7-flash", "display_name": "Gemini 3.7 Flash"},
        {"id": "antigravity-preview-05-2026", "display_name": "Antigravity Agent Preview"},
    ]
