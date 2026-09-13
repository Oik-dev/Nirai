from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

from core.agents.antigravity_agent import AntigravityAgentAdapter
from core.agents.base import AgentRunRequest, resident_task_identity_instruction
from core.agents.codex_app_server import CodexAppServerAdapter
from core.agents.cursor_acp import CursorAcpAdapter
from core.agents.safety import AgentWorkspacePolicy


def _request(tmp_path: Path, *, purpose: str = "work") -> AgentRunRequest:
    working = tmp_path / "runtime" / "workspace" / "T-PERSONA"
    working.mkdir(parents=True, exist_ok=True)
    return AgentRunRequest(
        task_id="T-PERSONA",
        agent_session_id="AS-PERSONA",
        resident="Codex",
        provider="codex",
        prompt="Taskを完走して",
        working_dir=working,
        resident_persona="一人称は『ボク』。明るく軽い口調でMasterへ報告する。",
        purpose=purpose,
    )


def test_resident_task_identity_applies_only_to_agent_work(tmp_path: Path) -> None:
    instruction = resident_task_identity_instruction(_request(tmp_path))
    assert "Resident Codex" in instruction
    assert "一人称は『ボク』" in instruction
    assert "human-facing progress, questions, and final reports" in instruction
    assert "source code, identifiers, commands" in instruction

    assert resident_task_identity_instruction(_request(tmp_path, purpose="review")) == ""


def test_cursor_and_antigravity_task_prompts_keep_resident_identity(tmp_path: Path) -> None:
    request = _request(tmp_path)
    cursor_prompt = CursorAcpAdapter._build_agent_prompt(request)
    gemini_instruction = AntigravityAgentAdapter._system_instruction(request)

    for text in (cursor_prompt, gemini_instruction):
        assert "一人称は『ボク』" in text
        assert "Do not apply character speech to source code" in text


def test_codex_developer_instructions_include_resident_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    capture = tmp_path / "developer-instructions.txt"
    fake_server = tmp_path / "fake_codex_persona_server.py"
    fake_server.write_text(r'''
import json
from pathlib import Path
import sys

capture = Path(sys.argv[1])
for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize" and request_id is not None:
        print(json.dumps({"id": request_id, "result": {}}), flush=True)
    elif method == "initialized":
        pass
    elif method == "thread/start" and request_id is not None:
        capture.write_text(message["params"].get("developerInstructions", ""), encoding="utf-8")
        print(json.dumps({"id": request_id, "result": {"thread": {"id": "thread-persona"}}}), flush=True)
    elif method == "turn/start" and request_id is not None:
        print(json.dumps({"id": request_id, "result": {"turn": {"id": "turn-persona", "status": "inProgress"}}}), flush=True)
        print(json.dumps({"method": "item/completed", "params": {"item": {
            "id": "msg-persona", "type": "agentMessage", "text": "完了したよ", "phase": "final_answer"
        }}}), flush=True)
        print(json.dumps({"method": "turn/completed", "params": {"turn": {
            "id": "turn-persona", "status": "completed", "error": None
        }}}), flush=True)
''', encoding="utf-8")

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        request = _request(tmp_path)
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server), str(capture))  # type: ignore[method-assign]

        async def emit(_event_type, _payload):
            return None

        async def wait_for_master(*_args, **_kwargs):
            raise AssertionError("persona-only task must not require Master input")

        summary = await adapter.run(request, emit=emit, wait_for_master=wait_for_master)
        assert summary == "完了したよ"

    asyncio.run(scenario())
    developer_instructions = capture.read_text(encoding="utf-8")
    assert "Nirai Agent Runtime boundary" in developer_instructions
    assert "Resident Codex" in developer_instructions
    assert "一人称は『ボク』" in developer_instructions
