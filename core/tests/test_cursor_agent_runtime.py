from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest

import core.agents.cursor_acp as cursor_acp_module
from core.agents import AgentRuntimeManager
from core.agents.base import AgentRunRequest, AgentRuntimeError
from core.agents.cursor_acp import (
    CursorAcpAdapter,
    _common_permission_options,
    _cursor_review_manifest,
    _permission_option_for_decision,
    _permission_reject_result,
    _resolve_select_value,
    _stop_process_tree,
)
from core.agents.cursor_events import (
    cursor_message_chunk_text,
    normalize_cursor_session_update,
    validate_cursor_tool_paths,
)
from core.agents.safety import AgentSafetyError, AgentWorkspacePolicy
from core.brains.process_manager import CompletedInvocation


def _policy(tmp_path: Path) -> AgentWorkspacePolicy:
    return AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))


def _request(tmp_path: Path) -> AgentRunRequest:
    working = tmp_path / "runtime" / "workspace" / "TASK-CURSOR"
    working.mkdir(parents=True, exist_ok=True)
    return AgentRunRequest(
        task_id="TASK-CURSOR",
        agent_session_id="AS-CURSOR",
        resident="Cursor",
        provider="cursor",
        prompt="create result.txt",
        working_dir=working,
        model="cursor-grok-4.6-high",
    )


def test_cursor_nirai_read_only_staging_excludes_secret_and_private_asset_roots(tmp_path: Path) -> None:
    root = tmp_path
    (root / "runtime" / "workspace").mkdir(parents=True)
    (root / "world" / "out").mkdir(parents=True)
    (root / "world" / ".env").write_text("SECRET=do-not-stage\n", encoding="utf-8")
    (root / "world" / ".env.local").write_text("SECRET_LOCAL=do-not-stage\n", encoding="utf-8")
    (root / "world" / "visible.ts").write_text("export const visible = true\n", encoding="utf-8")
    (root / "world" / "out" / "bundle.js").write_text("generated\n", encoding="utf-8")
    for private_root in (".tools", ".vrm", ".vrma"):
        path = root / private_root
        path.mkdir()
        (path / "private.bin").write_bytes(b"do-not-stage")
    policy = AgentWorkspacePolicy(root, ("runtime\\workspace",))
    adapter = CursorAcpAdapter(policy)

    ignored = adapter._read_only_staging_ignore_parts(root)
    staging, baseline = adapter._prepare_staging_workspace(
        "AS-SECRET-REVIEW",
        root,
        ignore_parts=ignored,
    )
    try:
        assert "world/.env" not in baseline
        assert "world/.env.local" not in baseline
        assert "world/out/bundle.js" not in baseline
        assert ".tools/private.bin" not in baseline
        assert ".vrm/private.bin" not in baseline
        assert ".vrma/private.bin" not in baseline
        assert "world/visible.ts" in baseline
        assert not (staging / "world" / ".env").exists()
        assert not (staging / "world" / ".env.local").exists()
        assert not (staging / "world" / "out").exists()
        assert not (staging / ".tools").exists()
        assert not (staging / ".vrm").exists()
        assert not (staging / ".vrma").exists()
        assert (staging / "world" / "visible.ts").is_file()
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_event_normalizer_drops_private_thought_and_aggregates_message_separately(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    working = policy.resolve_working_dir(None, task_id="TASK-EVENT")

    thought = {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "private"}}
    message = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "public"}}

    assert normalize_cursor_session_update(thought, working_dir=working, workspace_policy=policy) == []
    assert normalize_cursor_session_update(message, working_dir=working, workspace_policy=policy) == []
    assert cursor_message_chunk_text(thought) is None
    assert cursor_message_chunk_text(message) == "public"


def test_cursor_event_normalizer_maps_file_and_command_tools_inside_workspace(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    working = policy.resolve_working_dir(None, task_id="TASK-TOOLS")

    file_events = normalize_cursor_session_update({
        "sessionUpdate": "tool_call",
        "toolCallId": "edit-1",
        "kind": "edit",
        "title": "Edit result.txt",
        "status": "in_progress",
        "locations": [{"path": "result.txt"}],
    }, working_dir=working, workspace_policy=policy)
    command_events = normalize_cursor_session_update({
        "sessionUpdate": "tool_call",
        "toolCallId": "cmd-1",
        "kind": "execute",
        "title": "Run tests",
        "rawInput": {"command": "python -m pytest"},
    }, working_dir=working, workspace_policy=policy)

    assert file_events[0][0] == "file_change"
    assert file_events[0][1]["operation_id"] == "edit-1"
    assert file_events[0][1]["changes"][0]["relative_path"] == "result.txt"
    assert command_events == [("command_execution", {
        "operation_id": "cmd-1",
        "phase": "started",
        "tool_type": "execute",
        "status": None,
        "title": "Run tests",
        "command": "python -m pytest",
        "cwd": str(working),
    })]


def test_cursor_tool_path_validation_fails_closed_outside_workspace(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    working = policy.resolve_working_dir(None, task_id="TASK-ESCAPE")
    outside = tmp_path / "outside.txt"

    with pytest.raises(AgentSafetyError):
        validate_cursor_tool_paths(
            {
                "kind": "edit",
                "toolCallId": "escape-1",
                "locations": [{"path": str(outside)}],
            },
            working_dir=working,
            workspace_policy=policy,
        )


def test_cursor_model_resolver_preserves_exact_acp_effort_and_refuses_silent_downgrade() -> None:
    config = {
        "id": "model",
        "category": "model",
        "options": [
            {"value": "default[]", "name": "Auto"},
            {"value": "grok-4.6[effort=high,fast=true]", "name": "grok-4.6"},
            {"value": "gpt-5.6-sol[context=272k,reasoning=medium,fast=false]", "name": "gpt-5.6-sol"},
            {"value": "claude-fable-5-1[thinking=true,context=300k,effort=high]", "name": "claude-fable-5-1"},
        ],
    }

    assert _resolve_select_value(config, "auto") == "default[]"
    assert _resolve_select_value(config, "cursor-grok-4.6-high-fast") == "grok-4.6[effort=high,fast=true]"
    assert _resolve_select_value(config, "cursor-grok-4.6-high") is None
    assert _resolve_select_value(config, "cursor-grok-4.6-xhigh") is None
    assert _resolve_select_value(config, "gpt-5.6-sol-medium") == "gpt-5.6-sol[context=272k,reasoning=medium,fast=false]"
    assert _resolve_select_value(config, "gpt-5.6-sol-high") is None
    assert _resolve_select_value(config, "claude-fable-5-1-thinking-high") == "claude-fable-5-1[thinking=true,context=300k,effort=high]"
    assert _resolve_select_value(config, "missing-model") is None


def test_cursor_agent_home_copies_only_auth_state_and_writes_nirai_safety_config(tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True)
    (source / "agent-cli-state.json").write_text('{"opaque":"auth"}\n', encoding="utf-8")
    (source / "cli-config.json").write_text('{"permissions":{"allow":["Shell(*)"]}}\n', encoding="utf-8")
    (source / "skills-cursor").mkdir()
    (source / "skills-cursor" / "unsafe.md").write_text("do not copy", encoding="utf-8")
    (source / "projects").mkdir()

    adapter = CursorAcpAdapter(_policy(tmp_path))
    home = adapter._prepare_cursor_home("AS-ISOLATED")
    try:
        config_dir = home / ".cursor"
        assert (config_dir / "agent-cli-state.json").read_text(encoding="utf-8") == '{"opaque":"auth"}\n'
        assert not (config_dir / "skills-cursor").exists()
        assert not (config_dir / "projects").exists()
        config = json.loads((config_dir / "cli-config.json").read_text(encoding="utf-8"))
        assert config["approvalMode"] == "allowlist"
        assert config["permissions"]["allow"] == []
        assert "WebFetch(*)" in config["permissions"]["deny"]
        assert "Mcp(*:*)" in config["permissions"]["deny"]
        assert any(item.startswith("Read(") and "Users" in item for item in config["permissions"]["deny"])
        assert any(item.startswith("Write(") and "/core/**" in item for item in config["permissions"]["deny"])
        assert config["display"]["showThinkingBlocks"] is False
    finally:
        adapter._cleanup_cursor_home(home)
    assert not home.exists()


def test_cursor_nirai_root_review_does_not_deny_its_staging_copy_while_real_sensitive_roots_stay_denied(tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True)
    (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "core").mkdir()
    (tmp_path / "world").mkdir()
    policy = _policy(tmp_path)
    adapter = CursorAcpAdapter(policy)
    staging = policy.default_workspace_root / ".cursor-stage-AS-ROOT-REVIEW"
    staging.mkdir(parents=True)

    deny = adapter._cursor_permission_denies(staging, extra_denied_paths=())
    root_pattern = tmp_path.resolve().as_posix().rstrip("/") + "/**"
    staging_pattern = staging.resolve().as_posix().rstrip("/") + "/**"

    assert f"Read({root_pattern})" not in deny
    assert f"Write({root_pattern})" not in deny
    assert f"Read({staging_pattern})" not in deny
    assert any(item.startswith("Read(") and "/core/**" in item for item in deny)
    assert any(item.startswith("Write(") and "/world/**" in item for item in deny)
    assert any(item.startswith("Read(") and "/runtime/agent_sessions/**" in item for item in deny)


def test_cursor_agent_home_cleans_stale_credential_homes_before_new_session(tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True)
    (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
    stale = tmp_path / "runtime" / "cursor_agent_homes" / "AS-STALE"
    stale.mkdir(parents=True)
    (stale / "credential-copy.txt").write_text("stale", encoding="utf-8")
    os.utime(stale, (1, 1))

    adapter = CursorAcpAdapter(_policy(tmp_path))
    home = adapter._prepare_cursor_home("AS-NEW")
    try:
        assert not stale.exists()
        assert home.exists()
    finally:
        adapter._release_runtime_id("AS-NEW")
        adapter._cleanup_cursor_home(home)


def test_cursor_agent_home_does_not_reap_young_unowned_home(tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True)
    (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
    young = tmp_path / "runtime" / "cursor_agent_homes" / "AS-OTHER-CORE"
    young.mkdir(parents=True)
    (young / "credential-copy.txt").write_text("live", encoding="utf-8")

    adapter = CursorAcpAdapter(_policy(tmp_path))
    home = adapter._prepare_cursor_home("AS-NEW")
    try:
        assert young.exists()
        assert home.exists()
    finally:
        adapter._release_runtime_id("AS-NEW")
        adapter._cleanup_cursor_home(home)
        adapter._cleanup_cursor_home(young)


def test_cursor_agent_environment_does_not_forward_unrelated_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True)
    (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("NIRAI_SECRET_SENTINEL", "must-not-leak")
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-leak")
    monkeypatch.setenv("PATH", "safe-path")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    monkeypatch.setenv("APPDATA", r"C:\Users\tester\AppData\Roaming")
    adapter = CursorAcpAdapter(_policy(tmp_path))
    home = adapter._prepare_cursor_home("AS-ENV")
    try:
        env = adapter._build_cursor_environment(home)
        assert env["PATH"] == "safe-path"
        assert "NIRAI_SECRET_SENTINEL" not in env
        assert "GEMINI_API_KEY" not in env
        assert env["USERPROFILE"] == str(home)
        assert env["HOME"] == str(home)
        assert env["CURSOR_CONFIG_DIR"] == str(home / ".cursor")
        assert env["LOCALAPPDATA"] == r"C:\Users\tester\AppData\Local"
        assert env["APPDATA"] == r"C:\Users\tester\AppData\Roaming"
        assert env["TEMP"].startswith(str(home))
    finally:
        adapter._cleanup_cursor_home(home)


def test_cursor_review_manifest_keeps_every_path_visible_when_diffs_are_too_large() -> None:
    changes = [
        {
            "path": f"D:/workspace/file-{index}.txt",
            "relative_path": f"file-{index}.txt",
            "change_type": "modify",
            "diff": "x" * 12_000,
        }
        for index in range(10)
    ]

    review = _cursor_review_manifest(changes)
    assert len(review) == len(changes)
    assert [item["relative_path"] for item in review] == [item["relative_path"] for item in changes]
    assert all("diff" not in item for item in review)


def test_cursor_review_manifest_rejects_path_manifest_that_cannot_fit_safely() -> None:
    changes = [
        {
            "path": "D:/workspace/" + ("x" * 700) + f"-{index}.txt",
            "relative_path": ("x" * 700) + f"-{index}.txt",
            "change_type": "modify",
        }
        for index in range(50)
    ]

    with pytest.raises(AgentRuntimeError, match="manifest is too large"):
        _cursor_review_manifest(changes)


def test_cursor_read_only_review_permission_policy_allows_only_local_read_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        request = AgentRunRequest(**{**request.__dict__, "read_only": True})
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        options = [
            {"id": "allow-read", "kind": "allow_once"},
            {"id": "reject-tool", "kind": "reject_once"},
        ]
        read_result = await adapter._handle_read_only_permission_request(
            {
                "toolCall": {
                    "toolCallId": "read-1",
                    "kind": "read",
                    "title": "Read module.py",
                    "locations": [{"path": "module.py"}],
                },
                "options": options,
            },
            request=request,
            emit=emit,
        )
        command_result = await adapter._handle_read_only_permission_request(
            {
                "toolCall": {
                    "toolCallId": "cmd-1",
                    "kind": "execute",
                    "title": "Run tests",
                },
                "options": options,
            },
            request=request,
            emit=emit,
        )
        write_result = await adapter._handle_read_only_permission_request(
            {
                "toolCall": {
                    "toolCallId": "write-1",
                    "kind": "edit",
                    "title": "Edit module.py",
                    "locations": [{"path": "module.py"}],
                },
                "options": options,
            },
            request=request,
            emit=emit,
        )

        assert read_result == {"outcome": {"outcome": "selected", "optionId": "allow-read"}}
        assert command_result == {"outcome": {"outcome": "selected", "optionId": "reject-tool"}}
        assert write_result == {"outcome": {"outcome": "selected", "optionId": "reject-tool"}}
        assert any(payload.get("kind") == "cursor_review_read_allowed" for _, payload in emitted)
        assert sum(payload.get("kind") == "cursor_review_tool_rejected" for _, payload in emitted) == 2

    asyncio.run(scenario())


def test_cursor_read_only_workspace_walk_prunes_ignored_directories_before_descent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "review-root"
    root.mkdir()
    visited_after_root: list[str] = []

    def fake_walk(path, *, topdown, followlinks):
        assert Path(path) == root.resolve()
        assert topdown is True
        assert followlinks is False
        dirnames = ["runtime", "src", "NODE_MODULES"]
        yield str(root), dirnames, ["README.md"]
        visited_after_root.extend(dirnames)
        if "runtime" in dirnames or "NODE_MODULES" in dirnames:
            raise AssertionError("ignored directory was not pruned before descent")
        yield str(root / "src"), [], ["main.py"]

    monkeypatch.setattr(cursor_acp_module.os, "walk", fake_walk)
    files = list(CursorAcpAdapter._iter_workspace_files(
        root,
        ignore_parts=frozenset({"runtime", "node_modules"}),
    ))

    assert visited_after_root == ["src"]
    assert [path.relative_to(root).as_posix() for path in files] == ["README.md", "src/main.py"]


def test_cursor_writable_staging_excludes_dependency_and_generated_trees(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "ProjectA"
    (project / "src").mkdir(parents=True)
    (project / "node_modules" / "dep").mkdir(parents=True)
    (project / ".venv" / "Lib").mkdir(parents=True)
    (project / "build").mkdir(parents=True)
    (project / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (project / "node_modules" / "dep" / "index.js").write_text("// dependency\n", encoding="utf-8")
    (project / ".venv" / "Lib" / "site.py").write_text("# generated\n", encoding="utf-8")
    (project / "build" / "artifact.bin").write_bytes(b"generated")

    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace", "projects\\ProjectA"))
    adapter = CursorAcpAdapter(policy)
    staging, _ = adapter._prepare_staging_workspace(
        "AS-WRITABLE-IGNORE",
        project,
        ignore_parts=cursor_acp_module.CURSOR_WRITABLE_IGNORE_NAMES,
    )
    try:
        assert (staging / "src" / "main.py").is_file()
        assert not (staging / "node_modules").exists()
        assert not (staging / ".venv").exists()
        assert not (staging / "build").exists()
    finally:
        adapter._preparing_ids.discard("AS-WRITABLE-IGNORE")
        adapter._cleanup_staging_workspace(staging)


def test_cursor_read_only_review_rejects_staging_mutation_and_stale_source(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "ProjectA"
    project.mkdir(parents=True)
    source = project / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    policy = AgentWorkspacePolicy(
        tmp_path,
        ("runtime\\workspace", "projects\\ProjectA"),
    )
    adapter = CursorAcpAdapter(policy)
    ignore_parts = adapter._read_only_staging_ignore_parts(project)

    staging, baseline = adapter._prepare_staging_workspace(
        "AS-READONLY-WRITE",
        project,
        ignore_parts=ignore_parts,
    )
    try:
        (staging / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
        with pytest.raises(AgentRuntimeError, match="read-only review attempted to modify"):
            adapter._verify_read_only_review_unchanged(
                project,
                staging,
                baseline,
                ignore_parts=ignore_parts,
            )
        assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    finally:
        adapter._cleanup_staging_workspace(staging)

    staging, baseline = adapter._prepare_staging_workspace(
        "AS-READONLY-STALE",
        project,
        ignore_parts=ignore_parts,
    )
    try:
        source.write_text("VALUE = 3\n", encoding="utf-8")
        with pytest.raises(AgentRuntimeError, match="Review target changed"):
            adapter._verify_read_only_review_unchanged(
                project,
                staging,
                baseline,
                ignore_parts=ignore_parts,
            )
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_native_conversation_load_reuses_session_without_replaying_old_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project = tmp_path / "projects" / "ProjectA"
        project.mkdir(parents=True)
        (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        auth = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        auth.mkdir(parents=True)
        (auth / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        log_path = tmp_path / "cursor-native-log.jsonl"
        fake_server = tmp_path / "fake_cursor_native.py"
        fake_server.write_text(
            r'''import json
from pathlib import Path
import sys

LOG = Path(__LOG_PATH__)

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
    if method == "initialize":
        send({"jsonrpc":"2.0","id":request_id,"result":{
            "protocolVersion":1,
            "authMethods":[{"id":"cursor_login"}],
            "agentCapabilities":{"loadSession":True}
        }})
    elif method == "authenticate":
        send({"jsonrpc":"2.0","id":request_id,"result":{}})
    elif method == "session/new":
        log({"method":method,"cwd":params.get("cwd")})
        send({"jsonrpc":"2.0","id":request_id,"result":{
            "sessionId":"cursor-native-1","configOptions":[]
        }})
    elif method == "session/load":
        log({"method":method,"cwd":params.get("cwd"),"sessionId":params.get("sessionId")})
        send({"jsonrpc":"2.0","method":"session/update","params":{
            "sessionId":"cursor-native-1",
            "update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"OLD-REPLAY-MUST-NOT-RETURN"}}
        }})
        send({"jsonrpc":"2.0","id":request_id,"result":{"configOptions":[]}})
    elif method == "session/prompt":
        prompt = params.get("prompt", [{}])[0].get("text", "")
        log({"method":method,"sessionId":params.get("sessionId"),"prompt":prompt})
        answer = "NEW-SECOND" if "second turn" in prompt else "NEW-FIRST"
        send({"jsonrpc":"2.0","method":"session/update","params":{
            "sessionId":"cursor-native-1",
            "update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":answer}}
        }})
        send({"jsonrpc":"2.0","id":request_id,"result":{"stopReason":"end_turn"}})
    elif request_id is not None:
        send({"jsonrpc":"2.0","id":request_id,"result":{}})
'''.replace("__LOG_PATH__", json.dumps(str(log_path))),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            cursor_acp_module,
            "resolve_cursor_command",
            lambda: (sys.executable, str(fake_server)),
        )
        policy = AgentWorkspacePolicy(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
        )
        adapter = CursorAcpAdapter(policy)

        async def no_master(*_args):
            raise AssertionError("read-only native Conversation must not ask Master")

        async def run_turn(agent_session_id: str, prompt: str, provider_session_id: str | None):
            events: list[tuple[str, dict[str, Any]]] = []

            async def emit(event_type, payload):
                events.append((event_type, payload))

            summary = await adapter.run(
                AgentRunRequest(
                    task_id=f"HC-{agent_session_id}",
                    agent_session_id=agent_session_id,
                    resident="Holo",
                    provider="cursor",
                    prompt=prompt,
                    working_dir=project.resolve(),
                    read_only=True,
                    purpose="consult",
                    conversation_id="CV-CURSOR-NATIVE",
                    provider_session_id=provider_session_id,
                ),
                emit=emit,
                wait_for_master=no_master,
            )
            native = next(
                payload.get("provider_session_id")
                for event_type, payload in events
                if event_type == "run_state" and payload.get("provider_session_id")
            )
            return summary, native

        first_summary, native = await asyncio.wait_for(
            run_turn("AS-CURSOR-NATIVE-1", "first turn", None),
            timeout=10,
        )
        second_summary, resumed_native = await asyncio.wait_for(
            run_turn("AS-CURSOR-NATIVE-2", "second turn", str(native)),
            timeout=10,
        )

        assert first_summary == "NEW-FIRST"
        assert second_summary == "NEW-SECOND"
        assert "OLD-REPLAY-MUST-NOT-RETURN" not in second_summary
        assert native == resumed_native == "cursor-native-1"
        calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        starts = [item for item in calls if item["method"] in {"session/new", "session/load"}]
        assert [item["method"] for item in starts] == ["session/new", "session/load"]
        assert starts[0]["cwd"] == starts[1]["cwd"]
        assert starts[1]["sessionId"] == "cursor-native-1"
        prompts = [item["prompt"] for item in calls if item["method"] == "session/prompt"]
        assert prompts == [
            CursorAcpAdapter._build_agent_prompt(AgentRunRequest(
                task_id="HC-AS-CURSOR-NATIVE-1",
                agent_session_id="AS-CURSOR-NATIVE-1",
                resident="Holo",
                provider="cursor",
                prompt="first turn",
                working_dir=Path(starts[0]["cwd"]),
                read_only=True,
                purpose="consult",
                conversation_id="CV-CURSOR-NATIVE",
            )),
            CursorAcpAdapter._build_agent_prompt(AgentRunRequest(
                task_id="HC-AS-CURSOR-NATIVE-2",
                agent_session_id="AS-CURSOR-NATIVE-2",
                resident="Holo",
                provider="cursor",
                prompt="second turn",
                working_dir=Path(starts[1]["cwd"]),
                read_only=True,
                purpose="consult",
                conversation_id="CV-CURSOR-NATIVE",
                provider_session_id="cursor-native-1",
            )),
        ]
        assert (project / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    asyncio.run(scenario())


def test_cursor_read_only_review_prompt_requires_structured_verdict_and_forbids_mutation(tmp_path: Path) -> None:
    request = AgentRunRequest(
        task_id="HR-CURSOR-REVIEW",
        agent_session_id="AS-CURSOR-REVIEW",
        resident="Holo",
        provider="cursor",
        prompt="Review the current Holo supervisor changes",
        working_dir=tmp_path,
        read_only=True,
    )

    prompt = CursorAcpAdapter._build_agent_prompt(request)

    assert "read-only Cursor reviewer" in prompt
    assert "Do not create, modify, move, or delete any file" in prompt
    assert "Do not run shell or terminal commands" in prompt
    assert "exactly SAFE or NEEDS FIX" in prompt
    assert "Do not fix them" in prompt


def test_cursor_staging_requires_master_approval_before_real_workspace_changes(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("create result.txt\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            (staging / "result.txt").write_text("staged\n", encoding="utf-8")
            emitted: list[tuple[str, dict[str, Any]]] = []

            async def emit(event_type, payload):
                emitted.append((event_type, payload))

            async def approve(request_id, kind, payload):
                assert kind == "approval"
                assert payload["kind"] == "file_change"
                assert payload["options"] == ["approve_once", "reject", "cancel"]
                assert not (request.working_dir / "result.txt").exists()
                return {"decision": "approve_once"}

            await adapter._review_and_apply_staged_changes(
                request,
                staging_dir=staging,
                review_dir=tmp_path / "cursor-review-approved",
                baseline=baseline,
                emit=emit,
                wait_for_master=approve,
            )
            assert (request.working_dir / "result.txt").read_text(encoding="utf-8") == "staged\n"
            assert [kind for kind, _ in emitted] == ["file_change", "approval_request", "file_change"]
            operation_id = emitted[0][1]["operation_id"]
            assert emitted[1][1]["operation_id"] == operation_id
            assert emitted[2][1]["status"] == "completed"
            assert emitted[0][1]["changes"][0]["relative_path"] == "result.txt"
            assert "+++ b/result.txt" in emitted[0][1]["changes"][0]["diff"]
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_approved_apply_never_recreates_deleted_external_workspace_root(tmp_path: Path) -> None:
    async def scenario() -> None:
        project = tmp_path / "projects" / "ProjectA"
        project.mkdir(parents=True)
        policy = AgentWorkspacePolicy(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
        )
        adapter = CursorAcpAdapter(policy)
        request = AgentRunRequest(
            task_id="TASK-CURSOR-EXTERNAL",
            agent_session_id="AS-CURSOR-EXTERNAL",
            resident="Cursor",
            provider="cursor",
            prompt="create result.txt",
            working_dir=project,
            model="cursor-grok-4.6-high",
        )
        staging, baseline = adapter._prepare_staging_workspace(
            request.agent_session_id,
            request.working_dir,
        )
        try:
            (staging / "result.txt").write_text("staged\n", encoding="utf-8")

            async def emit(*args):
                return None

            async def approve_then_delete_workspace(*args):
                project.rmdir()
                return {"decision": "approve_once"}

            with pytest.raises(AgentRuntimeError, match="was rolled back"):
                await adapter._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging,
                    review_dir=tmp_path / "cursor-review-deleted-root",
                    baseline=baseline,
                    emit=emit,
                    wait_for_master=approve_then_delete_workspace,
                )
            assert project.exists() is False
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_staging_refuses_apply_if_staging_changes_during_master_review(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("create result.txt\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            (staging / "result.txt").write_text("reviewed\n", encoding="utf-8")

            async def emit(event_type, payload):
                return None

            async def approve_after_mutation(request_id, kind, payload):
                assert kind == "approval"
                (staging / "result.txt").write_text("tampered-after-review\n", encoding="utf-8")
                return {"decision": "approve_once"}

            with pytest.raises(AgentRuntimeError, match="staging workspace changed after review"):
                await adapter._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging,
                    review_dir=tmp_path / "cursor-review-mutated-staging",
                    baseline=baseline,
                    emit=emit,
                    wait_for_master=approve_after_mutation,
                )
            assert not (request.working_dir / "result.txt").exists()
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_staging_reject_leaves_real_workspace_unchanged(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("create result.txt\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            (staging / "result.txt").write_text("rejected\n", encoding="utf-8")

            async def emit(event_type, payload):
                return None

            async def reject(request_id, kind, payload):
                return {"decision": "reject"}

            with pytest.raises(AgentRuntimeError, match="rejected Cursor staged file changes"):
                await adapter._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging,
                    review_dir=tmp_path / "cursor-review-rejected",
                    baseline=baseline,
                    emit=emit,
                    wait_for_master=reject,
                )
            assert not (request.working_dir / "result.txt").exists()
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_staging_refuses_apply_if_real_workspace_changed_concurrently(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("modify seed.txt\n", encoding="utf-8")
        (request.working_dir / "seed.txt").write_text("before\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            (staging / "seed.txt").write_text("cursor\n", encoding="utf-8")
            (request.working_dir / "seed.txt").write_text("master\n", encoding="utf-8")

            async def emit(event_type, payload):
                return None

            async def approve(request_id, kind, payload):
                return {"decision": "approve_once"}

            with pytest.raises(AgentRuntimeError, match="workspace changed while Cursor was working"):
                await adapter._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging,
                    review_dir=tmp_path / "cursor-review-conflict",
                    baseline=baseline,
                    emit=emit,
                    wait_for_master=approve,
                )
            assert (request.working_dir / "seed.txt").read_text(encoding="utf-8") == "master\n"
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_staging_apply_failure_rolls_back_already_applied_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = CursorAcpAdapter(_policy(tmp_path))
    request = _request(tmp_path)
    (request.working_dir / "task.md").write_text("modify files\n", encoding="utf-8")
    (request.working_dir / "a.txt").write_text("a-before\n", encoding="utf-8")
    (request.working_dir / "b.txt").write_text("b-before\n", encoding="utf-8")
    staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
    try:
        (staging / "a.txt").write_text("a-after\n", encoding="utf-8")
        (staging / "b.txt").write_text("b-after\n", encoding="utf-8")
        changes = adapter._collect_staged_changes(request.working_dir, staging, baseline)
        original_copy = adapter._atomic_copy_file
        failed_once = False

        def fail_second_staged_copy(source: Path, target: Path) -> None:
            nonlocal failed_once
            if source == staging / "b.txt" and not failed_once:
                failed_once = True
                raise OSError("simulated apply failure")
            original_copy(source, target)

        monkeypatch.setattr(adapter, "_atomic_copy_file", fail_second_staged_copy)
        with pytest.raises(AgentRuntimeError, match="was rolled back"):
            adapter._apply_staged_changes(request.working_dir, staging, baseline, changes)

        assert (request.working_dir / "a.txt").read_text(encoding="utf-8") == "a-before\n"
        assert (request.working_dir / "b.txt").read_text(encoding="utf-8") == "b-before\n"
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_staging_never_applies_task_metadata_changes(tmp_path: Path) -> None:
    adapter = CursorAcpAdapter(_policy(tmp_path))
    request = _request(tmp_path)
    (request.working_dir / "task.md").write_text("original\n", encoding="utf-8")
    staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
    try:
        (staging / "task.md").write_text("tampered\n", encoding="utf-8")
        with pytest.raises(AgentRuntimeError, match="protected Task metadata"):
            adapter._collect_staged_changes(request.working_dir, staging, baseline)
        assert (request.working_dir / "task.md").read_text(encoding="utf-8") == "original\n"
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_permission_bridge_maps_master_decision_and_blocks_external_tools(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        source.mkdir(parents=True)
        (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        emitted: list[tuple[str, dict[str, Any]]] = []
        waited: list[tuple[str, str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def wait_for_master(request_id, kind, payload):
            waited.append((request_id, kind, payload))
            return {"decision": "approve_once"}

        result = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "edit-1",
                    "kind": "edit",
                    "title": "Edit result.txt",
                    "locations": [{"path": "result.txt"}],
                },
                "options": [
                    {"optionId": "allow-once"},
                    {"optionId": "allow-always"},
                    {"optionId": "reject-once"},
                ],
            },
            request=request,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        assert result == {"outcome": {"outcome": "selected", "optionId": "allow-once"}}
        assert waited[0][0:2] == ("edit-1", "approval")
        assert [kind for kind, _ in emitted[:2]] == ["file_change", "approval_request"]
        assert emitted[0][1]["operation_id"] == "edit-1"
        assert emitted[0][1]["changes"][0]["relative_path"] == "result.txt"
        assert emitted[1][1]["kind"] == "file_change"
        assert emitted[1][1]["operation_id"] == "edit-1"

        waited.clear()
        external = await adapter._handle_permission_request(
            {
                "toolCall": {"toolCallId": "search-1", "kind": "search", "title": "Web search: current docs"},
                "options": [{"optionId": "allow-once"}, {"optionId": "reject-once"}],
            },
            request=request,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        assert external == {"outcome": {"outcome": "selected", "optionId": "reject-once"}}
        assert waited == []
        assert any(kind == "status_message" and payload.get("kind") == "external_tool_blocked" for kind, payload in emitted)

    asyncio.run(scenario())


def test_cursor_permission_reject_never_falls_back_to_allow_only_option(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def should_not_wait(request_id, kind, payload):
            raise AssertionError("baseline deny must not reach Master approval")

        allow_only = [{"optionId": "allow-once"}]
        external = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "web-allow-only",
                    "kind": "search",
                    "title": "Web search",
                },
                "options": allow_only,
            },
            request=request,
            emit=emit,
            wait_for_master=should_not_wait,
        )
        assert external == {"outcome": {"outcome": "cancelled"}}

        unknown_path = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "edit-allow-only",
                    "kind": "edit",
                    "title": "Edit unknown file",
                },
                "options": allow_only,
            },
            request=request,
            emit=emit,
            wait_for_master=should_not_wait,
        )
        assert unknown_path == {"outcome": {"outcome": "cancelled"}}

        async def reject_master(request_id, kind, payload):
            return {"decision": "reject"}

        rejected = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "cmd-reject-allow-only",
                    "kind": "execute",
                    "title": "Run command",
                    "rawInput": {"command": "echo no"},
                },
                "options": allow_only,
            },
            request=request,
            emit=emit,
            wait_for_master=reject_master,
        )
        assert rejected == {"outcome": {"outcome": "cancelled"}}

        async def cancel_master(request_id, kind, payload):
            return {"decision": "cancel"}

        cancelled = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "cmd-cancel-allow-only",
                    "kind": "execute",
                    "title": "Run command",
                    "rawInput": {"command": "echo no"},
                },
                "options": allow_only,
            },
            request=request,
            emit=emit,
            wait_for_master=cancel_master,
        )
        assert cancelled == {"outcome": {"outcome": "cancelled"}}

    asyncio.run(scenario())


def test_cursor_permission_semantics_use_option_kind_not_option_id() -> None:
    options = [
        {"optionId": "allow", "kind": "allow_once"},
        {"optionId": "deny", "kind": "reject_once"},
    ]

    assert _common_permission_options(options) == ["approve_once", "reject", "cancel"]
    assert _permission_option_for_decision(options, "approve_once") == "allow"
    assert _permission_option_for_decision(options, "reject") == "deny"
    assert _permission_option_for_decision(options, "cancel") == "deny"
    assert _permission_reject_result(options) == {
        "outcome": {"outcome": "selected", "optionId": "deny"}
    }


def test_cursor_permission_bridge_rejects_outside_file_before_master_ui(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        emitted: list[tuple[str, dict[str, Any]]] = []
        waited = False

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def wait_for_master(request_id, kind, payload):
            nonlocal waited
            waited = True
            return {"decision": "approve_once"}

        response = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "escape-approval",
                    "kind": "edit",
                    "title": "Edit outside",
                    "locations": [{"path": str(tmp_path / "outside.txt")}],
                },
                "options": [{"optionId": "allow-once"}, {"optionId": "reject-once"}],
            },
            request=request,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        assert response == {"outcome": {"outcome": "selected", "optionId": "reject-once"}}
        assert waited is False
        assert any(kind == "error" and payload.get("code") == "cursor_tool_outside_workspace" for kind, payload in emitted)
        assert all(kind != "approval_request" for kind, _ in emitted)

    asyncio.run(scenario())


def test_cursor_question_and_plan_extensions_bridge_existing_master_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def answer_question(request_id, kind, payload):
            assert request_id == "q-call"
            assert kind == "question"
            assert payload["questions"][0]["options"][0] == {"id": "agent", "label": "Agent"}
            assert payload["questions"][0]["allow_free_text"] is False
            assert payload["questions"][0]["allow_multiple"] is False
            return {"answers": {"q1": ["Agent"]}}

        question_result = await adapter._handle_question_request(
            {
                "toolCallId": "q-call",
                "title": "Choose mode",
                "questions": [{
                    "id": "q1",
                    "prompt": "Which mode?",
                    "options": [{"id": "agent", "label": "Agent"}, {"id": "plan", "label": "Plan"}],
                    "allowMultiple": False,
                }],
            },
            emit=emit,
            wait_for_master=answer_question,
        )
        assert question_result == {
            "outcome": {
                "outcome": "answered",
                "answers": [{"questionId": "q1", "selectedOptionIds": ["agent"]}],
            }
        }

        async def approve_plan(request_id, kind, payload):
            assert request_id == "plan-call"
            assert kind == "plan"
            assert payload["markdown"] == "1. Edit\n2. Test"
            assert payload["steps"][0]["step"] == "Edit"
            return {"decision": "approve"}

        plan_result = await adapter._handle_plan_request(
            {
                "toolCallId": "plan-call",
                "name": "Small plan",
                "plan": "1. Edit\n2. Test",
                "todos": [{"id": "1", "content": "Edit", "status": "pending"}],
            },
            emit=emit,
            wait_for_master=approve_plan,
        )
        assert plan_result == {"outcome": {"outcome": "accepted"}}
        assert [kind for kind, _ in emitted] == ["question_request", "plan"]

    asyncio.run(scenario())


def test_cursor_process_tree_stop_attempts_taskkill_even_if_parent_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        pytest.skip("Cursor Agent Runtime process-tree contract is Windows-specific")

    async def scenario() -> None:
        calls: list[tuple[object, ...]] = []

        class FakeKiller:
            returncode = 0

            async def wait(self):
                return 0

            def kill(self):
                self.returncode = -9

        class FakeProcess:
            pid = 424242
            returncode = 0

            async def wait(self):
                return self.returncode

            def terminate(self):
                raise AssertionError("already exited parent must not need terminate")

            def kill(self):
                raise AssertionError("already exited parent must not need kill")

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append(args)
            return FakeKiller()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        assert await _stop_process_tree(FakeProcess()) is True
        assert calls
        assert calls[0][0:4] == ("taskkill.exe", "/PID", "424242", "/T")

    asyncio.run(scenario())


def test_cursor_exact_xhigh_work_uses_cli_staging_and_master_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        profile = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        profile.mkdir(parents=True)
        (profile / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda _path: None)
        monkeypatch.setattr(
            cursor_acp_module,
            "resolve_cursor_command",
            lambda: ("node.exe", "cursor-index.js"),
        )
        request = _request(tmp_path)
        request = AgentRunRequest(
            **{
                **request.__dict__,
                "model": "cursor-grok-4.6-xhigh",
            }
        )
        (request.working_dir / "task.md").write_text("create result.txt\n", encoding="utf-8")
        calls: list[dict[str, Any]] = []

        class FakeCliProcessManager:
            async def run(self, invocation_id, argv, *, cwd, timeout_sec, stdin_text=None, env=None):
                assert cwd != request.working_dir
                assert not (request.working_dir / "result.txt").exists()
                (cwd / "result.txt").write_text("xhigh staged\n", encoding="utf-8")
                config = json.loads((Path(env["CURSOR_CONFIG_DIR"]) / "cli-config.json").read_text(encoding="utf-8"))
                calls.append({
                    "invocation_id": invocation_id,
                    "argv": tuple(argv),
                    "cwd": cwd,
                    "timeout_sec": timeout_sec,
                    "stdin_text": stdin_text,
                    "config": config,
                })
                return CompletedInvocation(
                    0,
                    json.dumps({
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "implemented with xhigh",
                        "session_id": "cursor-xhigh-work-1",
                    }),
                    "",
                )

            async def cancel(self, _invocation_id: str) -> bool:
                return False

        adapter._cli_process_manager = FakeCliProcessManager()  # type: ignore[assignment]
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def approve(_request_id, kind, payload):
            assert kind == "approval"
            assert payload["kind"] == "file_change"
            assert not (request.working_dir / "result.txt").exists()
            return {"decision": "approve_once"}

        summary = await adapter.run(request, emit=emit, wait_for_master=approve)

        assert summary == "implemented with xhigh"
        assert (request.working_dir / "result.txt").read_text(encoding="utf-8") == "xhigh staged\n"
        assert len(calls) == 1
        argv = calls[0]["argv"]
        assert argv[argv.index("--model") + 1] == "cursor-grok-4.6-xhigh"
        assert "--force" in argv
        assert "--mode" not in argv
        assert not any("fast" in item.casefold() for item in argv)
        deny = calls[0]["config"]["permissions"]["deny"]
        assert "Shell(*)" in deny
        assert "WebFetch(*)" in deny
        assert "WebSearch(*)" in deny
        assert "Mcp(*:*)" in deny
        assert any(
            event_type == "run_state" and payload.get("provider_session_id") == "cursor-xhigh-work-1"
            for event_type, payload in emitted
        )

    asyncio.run(scenario())


def test_cursor_exact_xhigh_review_uses_read_only_cli_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        profile = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        profile.mkdir(parents=True)
        (profile / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda _path: None)
        monkeypatch.setattr(
            cursor_acp_module,
            "resolve_cursor_command",
            lambda: ("node.exe", "cursor-index.js"),
        )
        working = tmp_path / "runtime" / "workspace" / "HR-XHIGH"
        working.mkdir(parents=True)
        (working / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        request = AgentRunRequest(
            task_id="HR-XHIGH",
            agent_session_id="AS-XHIGH-REVIEW",
            resident="Holo",
            provider="cursor",
            prompt="Review module.py",
            working_dir=working,
            model="cursor-grok-4.6-xhigh",
            read_only=True,
            purpose="review",
            conversation_id="CV-XHIGH-REVIEW",
            provider_session_id="cursor-xhigh-review-1",
        )
        calls: list[dict[str, Any]] = []

        class FakeCliProcessManager:
            async def run(self, invocation_id, argv, *, cwd, timeout_sec, stdin_text=None, env=None):
                calls.append({
                    "invocation_id": invocation_id,
                    "argv": tuple(argv),
                    "cwd": cwd,
                    "stdin_text": stdin_text,
                })
                return CompletedInvocation(
                    0,
                    json.dumps({
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "Checked module.py first.SAFE\nNo blocking finding.",
                        "session_id": "cursor-xhigh-review-1",
                    }),
                    "",
                )

            async def cancel(self, _invocation_id: str) -> bool:
                return False

        adapter._cli_process_manager = FakeCliProcessManager()  # type: ignore[assignment]

        async def emit(_event_type, _payload):
            return None

        async def no_master(*_args):
            raise AssertionError("read-only xhigh review must not ask Master")

        summary = await adapter.run(request, emit=emit, wait_for_master=no_master)

        assert summary.startswith("SAFE")
        assert (working / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        argv = calls[0]["argv"]
        assert argv[argv.index("--model") + 1] == "cursor-grok-4.6-xhigh"
        assert argv[argv.index("--resume") + 1] == "cursor-xhigh-review-1"
        assert argv[argv.index("--mode") + 1] == "ask"
        assert "--force" not in argv
        assert not any("fast" in item.casefold() for item in argv)
        assert "exactly SAFE or NEEDS FIX" in calls[0]["stdin_text"]

    asyncio.run(scenario())


def test_default_agent_runtime_advertises_cursor_agent_work(tmp_path: Path) -> None:
    manager = AgentRuntimeManager(tmp_path, ("runtime\\workspace",))
    assert manager.supports_provider("codex") is True
    assert manager.supports_provider("cursor") is True
    assert manager.supports_provider("gemini") is True
    assert manager.provider_capabilities("cursor") == frozenset({
        "approval",
        "question",
        "plan",
        "todo",
        "subagent",
        "file_diff",
        "command_result",
    })
    assert "artifact" not in manager.provider_capabilities("cursor")
