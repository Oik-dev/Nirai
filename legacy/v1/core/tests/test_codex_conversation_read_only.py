from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys

from core.agents import AgentRunRequest, AgentWorkspacePolicy, CodexAppServerAdapter


_FAKE_READ_ONLY_CODEX_SERVER = r'''
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


cwd = None
for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")

    if request_id == 900:
        if message.get("result", {}).get("decision") != "decline":
            send({"method": "turn/completed", "params": {"turn": {"id": "turn-ro", "status": "failed", "error": {"message": "read-only approval was not declined"}}}})
            continue
        send({"id": 901, "method": "item/tool/requestUserInput", "params": {"threadId": "thread-ro", "turnId": "turn-ro", "itemId": "question-ro", "questions": [{"id": "q1", "question": "Need input?", "options": []}]}})
        continue

    if request_id == 901:
        if message.get("result", {}).get("answers") != {}:
            send({"method": "turn/completed", "params": {"turn": {"id": "turn-ro", "status": "failed", "error": {"message": "read-only question was not skipped"}}}})
            continue
        send({"method": "item/completed", "params": {"threadId": "thread-ro", "turnId": "turn-ro", "item": {"id": "msg-ro", "type": "agentMessage", "text": "相談回答", "phase": "final_answer"}}})
        send({"method": "turn/completed", "params": {"turn": {"id": "turn-ro", "items": [], "status": "completed", "error": None}}})
        continue

    if method == "initialize" and request_id is not None:
        send({"id": request_id, "result": {"userAgent": "fake-read-only"}})
    elif method == "initialized":
        pass
    elif method == "thread/start" and request_id is not None:
        params = message.get("params", {})
        cwd = params.get("cwd")
        if params.get("sandbox") != "read-only":
            send({"id": request_id, "error": {"code": -32010, "message": "thread was not read-only"}})
            continue
        if "read-only Conversation boundary" not in params.get("developerInstructions", ""):
            send({"id": request_id, "error": {"code": -32011, "message": "read-only boundary instruction missing"}})
            continue
        send({"id": request_id, "result": {"thread": {"id": "thread-ro"}}})
    elif method == "turn/start" and request_id is not None:
        params = message.get("params", {})
        sandbox = params.get("sandboxPolicy", {})
        if sandbox.get("type") != "readOnly" or sandbox.get("networkAccess") is not False:
            send({"id": request_id, "error": {"code": -32012, "message": "turn was not readOnly"}})
            continue
        if "writableRoots" in sandbox:
            send({"id": request_id, "error": {"code": -32013, "message": "read-only turn exposed writable roots"}})
            continue
        send({"id": request_id, "result": {"turn": {"id": "turn-ro", "items": [], "status": "inProgress"}}})
        send({"method": "turn/started", "params": {"threadId": "thread-ro", "turn": {"id": "turn-ro", "items": [], "status": "inProgress"}}})
        send({"id": 900, "method": "item/commandExecution/requestApproval", "params": {"threadId": "thread-ro", "turnId": "turn-ro", "itemId": "approval-ro", "command": "git status", "cwd": cwd, "reason": "test read-only decline"}})
'''


def test_codex_read_only_conversation_uses_read_only_sandbox_and_never_asks_master(tmp_path: Path, monkeypatch) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        fake_server = tmp_path / "fake_read_only_codex.py"
        fake_server.write_text(_FAKE_READ_ONLY_CODEX_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]
        events: list[tuple[str, dict[str, object]]] = []
        master_requests: list[tuple[str, str, dict[str, object]]] = []

        async def emit(event_type: str, payload: dict[str, object]) -> None:
            events.append((event_type, payload))

        async def wait_for_master(request_id: str, kind: str, payload: dict[str, object]):
            master_requests.append((request_id, kind, payload))
            raise AssertionError("read-only Conversation must never delegate approval/question to Master")

        summary = await asyncio.wait_for(
            adapter.run(
                AgentRunRequest(
                    task_id="HC-READ-ONLY",
                    agent_session_id="AS-READ-ONLY",
                    resident="Holo",
                    provider="codex",
                    prompt="仕様上の懸念を相談したい",
                    working_dir=tmp_path.resolve(),
                    read_only=True,
                    purpose="consult",
                ),
                emit=emit,  # type: ignore[arg-type]
                wait_for_master=wait_for_master,  # type: ignore[arg-type]
            ),
            timeout=5.0,
        )

        assert summary == "相談回答"
        assert master_requests == []
        assert any(
            event_type == "status_message"
            and payload.get("kind") == "codex_read_only_approval_declined"
            for event_type, payload in events
        )
        assert any(
            event_type == "status_message"
            and payload.get("kind") == "codex_read_only_question_skipped"
            for event_type, payload in events
        )
        assert not (tmp_path / "runtime" / "codex_agent_homes" / "AS-READ-ONLY").exists()

    asyncio.run(scenario())


_FAKE_NATIVE_CODEX_SERVER = r'''
import json
import os
from pathlib import Path
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
HOME = Path(os.environ["CODEX_HOME"])
MARKER = HOME / "native-thread.marker"
LOG = Path(__LOG_PATH__)
(HOME / ".sandbox-secrets").mkdir(parents=True, exist_ok=True)
(HOME / ".sandbox-secrets" / "ephemeral.txt").write_text("secret-ish", encoding="utf-8")


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def log(value):
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params", {})
    if method == "initialize" and request_id is not None:
        send({"id": request_id, "result": {"userAgent": "fake-native"}})
    elif method == "initialized":
        pass
    elif method == "thread/start" and request_id is not None:
        MARKER.write_text("thread-native-1", encoding="utf-8")
        log({"method": method, "threadId": "thread-native-1", "cwd": params.get("cwd")})
        send({"id": request_id, "result": {"thread": {"id": "thread-native-1"}}})
    elif method == "thread/resume" and request_id is not None:
        if params.get("threadId") != "thread-native-1" or not MARKER.is_file():
            send({"id": request_id, "error": {"code": -32020, "message": "native thread state was not preserved"}})
            continue
        log({"method": method, "threadId": params.get("threadId"), "cwd": params.get("cwd")})
        send({"id": request_id, "result": {"thread": {"id": "thread-native-1"}}})
    elif method == "turn/start" and request_id is not None:
        text = params.get("input", [{}])[0].get("text", "")
        log({"method": method, "threadId": params.get("threadId"), "input": text})
        turn_id = "turn-second" if "second turn" in text else "turn-first"
        answer = "CODEX-SECOND" if "second turn" in text else "CODEX-FIRST"
        send({"id": request_id, "result": {"turn": {"id": turn_id, "items": [], "status": "inProgress"}}})
        send({"method": "turn/started", "params": {"threadId": "thread-native-1", "turn": {"id": turn_id, "items": [], "status": "inProgress"}}})
        if "second turn" in text:
            send({"method": "item/completed", "params": {"threadId": "thread-native-1", "turnId": turn_id, "item": {"id": "compact-1", "type": "contextCompaction"}}})
        send({"method": "item/completed", "params": {"threadId": "thread-native-1", "turnId": turn_id, "item": {"id": "msg-" + turn_id, "type": "agentMessage", "text": answer, "phase": "final_answer"}}})
        send({"method": "turn/completed", "params": {"threadId": "thread-native-1", "turn": {"id": turn_id, "items": [], "status": "completed", "error": None}}})
'''


def test_codex_native_conversation_resumes_thread_and_keeps_auth_ephemeral(tmp_path: Path, monkeypatch) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    log_path = tmp_path / "codex-native-log.jsonl"
    fake_server = tmp_path / "fake_native_codex.py"
    fake_server.write_text(
        _FAKE_NATIVE_CODEX_SERVER.replace("__LOG_PATH__", json.dumps(str(log_path))),
        encoding="utf-8",
    )

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]

        async def no_master(*_args):
            raise AssertionError("read-only native Conversation must not ask Master")

        async def run_turn(agent_session_id: str, prompt: str, provider_session_id: str | None):
            events: list[tuple[str, dict[str, object]]] = []

            async def emit(event_type: str, payload: dict[str, object]) -> None:
                events.append((event_type, payload))

            summary = await adapter.run(
                AgentRunRequest(
                    task_id=f"HC-{agent_session_id}",
                    agent_session_id=agent_session_id,
                    resident="Holo",
                    provider="codex",
                    prompt=prompt,
                    working_dir=tmp_path.resolve(),
                    read_only=True,
                    purpose="consult",
                    conversation_id="CV-CODEX-NATIVE",
                    provider_session_id=provider_session_id,
                ),
                emit=emit,  # type: ignore[arg-type]
                wait_for_master=no_master,  # type: ignore[arg-type]
            )
            native = next(
                payload.get("provider_session_id")
                for event_type, payload in events
                if event_type == "run_state" and payload.get("provider_session_id")
            )
            return summary, native, events

        first_summary, native, first_events = await asyncio.wait_for(
            run_turn("AS-CODEX-NATIVE-1", "first turn", None),
            timeout=5,
        )
        digest = hashlib.sha256("CV-CODEX-NATIVE".encode("utf-8")).hexdigest()[:24]
        conversation_home = tmp_path / "runtime" / "codex_conversation_homes" / f"CV-{digest}"
        assert first_summary == "CODEX-FIRST"
        assert not any(
            event_type == "run_state" and payload.get("context_compacted") is True
            for event_type, payload in first_events
        )
        assert native == "thread-native-1"
        assert conversation_home.is_dir()
        assert (conversation_home / "native-thread.marker").is_file()
        assert not (conversation_home / "auth.json").exists()
        assert not (conversation_home / ".sandbox-secrets").exists()

        second_summary, resumed_native, second_events = await asyncio.wait_for(
            run_turn("AS-CODEX-NATIVE-2", "second turn", str(native)),
            timeout=5,
        )
        assert second_summary == "CODEX-SECOND"
        assert resumed_native == native
        assert any(
            event_type == "run_state" and payload.get("context_compacted") is True
            for event_type, payload in second_events
        )
        assert (conversation_home / "native-thread.marker").is_file()
        assert not (conversation_home / "auth.json").exists()
        assert not (conversation_home / ".sandbox-secrets").exists()

        calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        thread_calls = [item for item in calls if item["method"] in {"thread/start", "thread/resume"}]
        assert [item["method"] for item in thread_calls] == ["thread/start", "thread/resume"]
        assert thread_calls[0]["cwd"] == thread_calls[1]["cwd"]
        turn_inputs = [item["input"] for item in calls if item["method"] == "turn/start"]
        assert turn_inputs == ["first turn", "second turn"]

        adapter.discard_conversation_context("CV-CODEX-NATIVE")
        assert not conversation_home.exists()

    asyncio.run(scenario())
