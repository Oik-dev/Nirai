"""Workflow completion uses execution facts, including restart and race paths."""
import asyncio
import json

import pytest

from core.atomic_json import atomic_json
from core.holo.workflow import HoloWorkflow, WorkflowError

DIVE = "11111111-1111-4111-8111-111111111111"
URL = "https://chatgpt.com/c/owner"


async def build():
    return {"fingerprint": "original", "current": True}


async def start(service):
    return (await service.command("start", dive_session_id=DIVE, conversation_url=URL, label="Work"))["workflow"]


@pytest.mark.parametrize("state", ["queued", "starting", "running", "waiting_for_master", "cancelling", "failed", "interrupted", "unknown"])
@pytest.mark.parametrize("prefix", ["T", "HR", "IA"])
def test_incomplete_owned_work_blocks_completion(tmp_path, state, prefix):
    async def scenario():
        task_id = f"{prefix}-TEST"
        service = HoloWorkflow(tmp_path, lambda _: {"state": state}, build)
        workflow = await start(service)
        async with service.lock:
            service.admit(task_id, DIVE, URL, workflow["workflow_id"])
        before = service.path.read_bytes()
        result = await service.command("complete", workflow_id=workflow["workflow_id"])
        assert result["ok"] is False
        assert result["blockers"] == [{"task_id": task_id, "state": state, "agent_session_id": None}]
        assert service.path.read_bytes() == before
    asyncio.run(scenario())


def test_finalization_recovery_and_explicit_resolution(tmp_path):
    async def scenario():
        tasks = {"T-A": {"state": "failed", "agent_session_id": "AS-1"},
                 "IA-B": {"state": "completed", "agent_session_id": "AS-2", "finalizing": True}}
        service = HoloWorkflow(tmp_path, tasks.__getitem__, build)
        workflow = await start(service)
        wid = workflow["workflow_id"]
        async with service.lock:
            for task_id in tasks:
                service.admit(task_id, DIVE, URL, wid)
        with pytest.raises(WorkflowError, match="completed successfully"):
            await service.command("resolve", workflow_id=wid, task_id="T-A", resolution="superseded", replacement_task_id="IA-B", note="Verified repair")
        tasks["IA-B"]["finalizing"] = False
        result = await service.command("resolve", workflow_id=wid, task_id="T-A", resolution="superseded", replacement_task_id="IA-B", note="Verified repair")
        assert result["workflow"]["resolutions"]["T-A"]["agent_session_id"] == "AS-1"
        # Reload only the durable lease. Deleted routing receipts do not remove membership.
        service = HoloWorkflow(tmp_path, tasks.__getitem__, build)
        with pytest.raises(WorkflowError, match="explicitly resolved"):
            service.require_recoverable("T-A", wid)
        tasks["T-A"]["agent_session_id"] = "AS-NEW"
        assert not (await service.command("complete", workflow_id=wid))["ok"]
        tasks["T-A"]["agent_session_id"] = "AS-1"
        result = await service.command("complete", workflow_id=wid)
        assert result["ok"] and result["workflow"]["state"] == "completed"
        assert (await service.command("complete", workflow_id=wid))["workflow"] == result["workflow"]
        with pytest.raises(WorkflowError, match="already completed"):
            service.admit("T-LATE", DIVE, URL, wid)
        with pytest.raises(WorkflowError, match="already completed"):
            service.require_recoverable("T-A", wid)
    asyncio.run(scenario())


def test_completion_serializes_with_admission_and_rechecks_execution(tmp_path):
    async def scenario():
        tasks = {"T-A": {"state": "completed"}}
        service = HoloWorkflow(tmp_path, tasks.__getitem__, build)
        wid = (await start(service))["workflow_id"]
        service.admit("T-A", DIVE, URL, wid)
        checking, release = asyncio.Event(), asyncio.Event()
        async def slow_build():
            checking.set()
            await release.wait()
            return await build()
        service.build_status = slow_build
        completion = asyncio.create_task(service.command("complete", workflow_id=wid))
        await checking.wait()
        async def admit():
            async with service.lock:
                return service.admit("T-B", DIVE, URL, wid)
        admission = asyncio.create_task(admit())
        await asyncio.sleep(0)
        assert not admission.done()
        release.set()
        assert (await completion)["ok"]
        with pytest.raises(WorkflowError):
            await admission
        assert "T-B" not in service.read()["task_ids"]
        # External provider facts can change while the build subprocess awaits.
        service.build_status = build
        wid = (await start(service))["workflow_id"]
        service.admit("T-A", DIVE, URL, wid)
        async def changed_build():
            tasks["T-A"]["state"] = "failed"
            return await build()
        service.build_status = changed_build
        assert not (await service.command("complete", workflow_id=wid))["ok"]
    asyncio.run(scenario())


def test_activity_and_stale_ids_never_resurrect_work(tmp_path):
    async def scenario():
        service = HoloWorkflow(tmp_path, lambda _: {}, build)
        wid = (await start(service))["workflow_id"]
        revisions = await asyncio.gather(*(service.command("activity", workflow_id=wid) for _ in range(5)))
        assert len({item["workflow"]["updated_at"] for item in revisions}) == 5
        assert (await start(service))["workflow_id"] == wid
        await service.command("complete", workflow_id=wid)
        before = service.path.read_bytes()
        assert not (await service.command("activity", workflow_id=wid))["recorded"]
        assert service.path.read_bytes() == before
        new = await start(service)
        before = service.path.read_bytes()
        assert not (await service.command("activity", workflow_id=wid))["recorded"]
        for action in ("complete", "cancel", "heartbeat"):
            with pytest.raises(WorkflowError):
                await service.command(action, workflow_id=wid)
        assert service.path.read_bytes() == before
        assert new["workflow_id"] != wid
        with pytest.raises(WorkflowError, match="different Dive"):
            await service.command("start", dive_session_id="other", conversation_url=URL, label="Wrong")
    asyncio.run(scenario())


def test_standalone_task_can_start_after_workflow_completion(tmp_path):
    async def scenario():
        service = HoloWorkflow(tmp_path, lambda _: {}, build)
        wid = (await start(service))["workflow_id"]
        await service.command("complete", workflow_id=wid)
        before = service.path.read_bytes()
        assert service.admit("T-STANDALONE", DIVE, URL) is None
        assert service.path.read_bytes() == before
        with pytest.raises(WorkflowError, match="already completed"):
            service.admit("T-STALE", DIVE, URL, wid)
    asyncio.run(scenario())


def test_cancel_and_resolution_wait_for_cleanup(tmp_path):
    async def scenario():
        tasks = {"T-A": {"state": "interrupted", "agent_session_id": "AS-A"}}
        stops = []
        async def stop(task_id):
            stops.append(task_id)
            tasks[task_id]["state"] = "cancelled"
        service = HoloWorkflow(tmp_path, tasks.__getitem__, build, stop)
        wid = (await start(service))["workflow_id"]
        service.admit("T-A", DIVE, URL, wid)
        await service.command("resolve", workflow_id=wid, task_id="T-A", resolution="abandoned", note="Master cancelled this work")
        assert stops == ["T-A"]
        tasks["T-B"] = {"state": "running", "finalizing": True}
        service.admit("T-B", DIVE, URL, wid)
        assert not (await service.command("cancel", workflow_id=wid))["ok"]
        assert service.read()["state"] == "active"
        tasks["T-B"]["finalizing"] = False
        result = await service.command("cancel", workflow_id=wid)
        assert result["ok"] and result["workflow"]["completion_reason"] == "cancelled_by_master"
    asyncio.run(scenario())


def test_legacy_membership_is_imported_once_without_inventing_owners(tmp_path):
    async def scenario():
        service = HoloWorkflow(tmp_path, lambda _: {"state": "failed"}, build)
        workflow = await start(service)
        workflow.pop("task_ids")
        atomic_json(service.path, workflow)
        owners = tmp_path / "runtime/holo/task_owners"
        atomic_json(owners / "T-A.json", {"task_id": "T-A", "workflow_id": workflow["workflow_id"]})
        atomic_json(owners / "T-B.json", {"task_id": "T-B", "workflow_id": "old"})
        await service.command("heartbeat", workflow_id=workflow["workflow_id"])
        assert service.read()["task_ids"] == ["T-A"]
        (owners / "T-A.json").unlink()
        result = await service.command("complete", workflow_id=workflow["workflow_id"])
        assert result["blockers"][0]["task_id"] == "T-A"
    asyncio.run(scenario())


def test_build_gate_and_persistence_failure_keep_workflow_active(tmp_path, monkeypatch):
    async def scenario():
        service = HoloWorkflow(tmp_path, lambda _: {}, build)
        wid = (await start(service))["workflow_id"]
        async def stale_build():
            return {"fingerprint": "changed", "current": False}
        service.build_status = stale_build
        with pytest.raises(WorkflowError, match="final World build"):
            await service.command("complete", workflow_id=wid)
        service.build_status = build
        def fail(*args):
            raise OSError("disk full")
        monkeypatch.setattr("core.holo.workflow.atomic_json", fail)
        before = service.path.read_bytes()
        with pytest.raises(OSError):
            await service.command("complete", workflow_id=wid)
        assert service.path.read_bytes() == before
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("task_ids", "T-A"), ("task_ids", [{}]), ("resolutions", []), ("updated_at", "broken")])
def test_invalid_records_fail_closed(tmp_path, field, value):
    async def scenario():
        service = HoloWorkflow(tmp_path, lambda _: {}, build)
        workflow = await start(service)
        workflow[field] = value
        atomic_json(service.path, workflow)
        with pytest.raises(ValueError):
            await service.command("complete", workflow_id=workflow["workflow_id"])
    asyncio.run(scenario())


def test_core_persists_queue_membership_and_pre_agent_failure_replay(tmp_path, monkeypatch):
    from core.server import CoreServer
    from core.tests.test_holo import _make_config, _CaptureWorld
    from core.usage_budget import UsageBudgetService

    async def scenario():
        server = CoreServer(_make_config(tmp_path), port_override=0, usage_budget=UsageBudgetService({}))
        server.holo_workflow.build_status = build
        wid = (await start(server.holo_workflow))["workflow_id"]
        # Stop at the real durable reservation, before assigning an Agent.
        monkeypatch.setattr(server, "_start_task_flow", lambda request: None)
        task = await server._submit_task_request("Work", holo_dive_session_id=DIVE, holo_conversation_url=URL, workflow_id=wid)
        task_id = task["task_id"]
        assert server._active_pre_agent_task.workflow_id == wid
        assert not (await server.holo_workflow.command("complete", workflow_id=wid))["ok"]
        restarted = CoreServer(server.config, port_override=0, usage_budget=UsageBudgetService({}))
        assert restarted._task_queue[0].workflow_id == wid
        assert not (await restarted.holo_workflow.command("complete", workflow_id=wid))["ok"]
        await server._send_task_update(task_id, "failed", "No available worker")
        # Simulate a crash before releasing the old Queue record. Terminal fact wins.
        restarted = CoreServer(server.config, port_override=0, usage_budget=UsageBudgetService({}))
        assert not restarted._task_queue
        assert restarted._task_status(task_id)["phase"] == "failed"
        assert restarted._task_status(task_id)["workflow_id"] == wid
        blocked = await restarted.holo_workflow.command("complete", workflow_id=wid)
        assert blocked["blockers"][0]["state"] == "failed"
        world = _CaptureWorld()
        await restarted._send_pending_pre_agent_task_updates(world)
        assert any(m["type"] == "task_update" and m["payload"]["phase"] == "failed" for m in world.messages)
        # Owner receipt cleanup cannot remove the Workflow's completion evidence.
        restarted._clear_holo_task_owner(task_id)
        assert not (await restarted.holo_workflow.command("complete", workflow_id=wid))["ok"]
    asyncio.run(scenario())


def test_core_recovery_uses_durable_workflow_identity_after_owner_cleanup(tmp_path):
    from core.server import CoreServer
    from core.agents import AgentRuntimeManagerError
    from core.tests.test_holo import _make_config, _handoff_snapshot

    async def scenario():
        server = CoreServer(_make_config(tmp_path), port_override=0)
        server.holo_workflow.build_status = build
        wid = (await start(server.holo_workflow))["workflow_id"]
        original = _handoff_snapshot(server, tmp_path)
        snapshot = original.with_updates(workflow_id=wid)
        server.agent_runtime.store.save_snapshot(snapshot)
        server.agent_runtime._snapshots[snapshot.agent_session_id] = snapshot
        server.holo_workflow.admit(snapshot.task_id, DIVE, URL, wid)
        server._clear_holo_task_owner(snapshot.task_id)
        with pytest.raises(WorkflowError):
            async with server.holo_workflow.lock:
                server.holo_workflow.admit("T-STALE", DIVE, URL, "old")
        # Use the shared retirement boundary, which releases interrupted work.
        await server.holo_workflow.command("resolve", workflow_id=wid, task_id=snapshot.task_id,
                                           resolution="abandoned", note="Master ended this attempt")
        await server.holo_workflow.command("complete", workflow_id=wid)
        restarted = CoreServer(server.config, port_override=0)
        with pytest.raises(AgentRuntimeManagerError, match="already completed"):
            await restarted._recover_workflow_task(snapshot.agent_session_id, "rerun")
    asyncio.run(scenario())
