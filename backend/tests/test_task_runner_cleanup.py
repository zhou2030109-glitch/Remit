import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import httpx

from app.core.agents.coder_agent import CoderAgentUnavailableError
from app.routers import modeling_router as router
from app.schemas.request import Problem
from app.core.workflow_checkpoint import WorkflowCheckpoint


@pytest.mark.parametrize(
    "status,retryable", [(401, False), (403, False), (429, True), (503, True)]
)
def test_httpx_gateway_status_controls_task_retry(status, retryable):
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://fixture.invalid")
    )
    error = httpx.HTTPStatusError(
        "provider failed", request=response.request, response=response
    )
    assert router._is_transient_task_failure(error) is retryable


@pytest.fixture
def runner(tmp_path, monkeypatch):
    task_id = "runner-cleanup"
    checkpoint = WorkflowCheckpoint(tmp_path)
    checkpoint.initialize(Problem(task_id=task_id))
    workflow = MagicMock()
    workflow.execute = AsyncMock()
    workflow.cleanup = AsyncMock()
    workflow.mark_status.side_effect = checkpoint.mark_status
    monkeypatch.setattr(router, "RemitWorkFlow", lambda: workflow)
    monkeypatch.setattr(router, "create_work_dir", lambda _: str(tmp_path))
    monkeypatch.setattr(router, "_auto_resume_counts", {})
    monkeypatch.setattr(router, "_auto_resume_handles", set())
    monkeypatch.setattr(router.settings, "TASK_AUTO_RESUME_BASE_DELAY_SECONDS", 0)
    monkeypatch.setattr(router, "_AUTO_RESUME_LIMIT", 1)
    monkeypatch.setattr(router.task_bus, "publish_message", AsyncMock())
    monkeypatch.setattr(
        router.task_bus, "is_cancellation_requested", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(router.task_bus, "clear_cancellation_request", AsyncMock())
    return task_id, workflow, checkpoint


def test_retry_is_started_only_after_interpreter_cleanup_and_registry_release(
    runner, monkeypatch
):
    async def scenario():
        task_id, workflow, _ = runner
        cleaning = asyncio.Event()
        release = asyncio.Event()
        observations = []

        async def cleanup():
            cleaning.set()
            await release.wait()

        async def retry(*args):
            observations.append(task_id in router._active_tasks)

        workflow.execute.side_effect = CoderAgentUnavailableError(
            "provider unavailable"
        )
        workflow.cleanup.side_effect = cleanup
        monkeypatch.setattr(router, "_auto_resume_after_failure", retry)
        task = asyncio.create_task(
            router.run_modeling_task_async(task_id, "test", "CHINA", "LaTeX")
        )
        try:
            await asyncio.wait_for(cleaning.wait(), 2)
            await asyncio.sleep(0)
            assert not observations, "retry timer must not run while cleanup owns task"
        finally:
            release.set()
            await task
            await asyncio.gather(*router._auto_resume_handles)
        assert observations == [False]

    asyncio.run(scenario())


def test_failed_final_status_publish_still_releases_task_registry(runner, monkeypatch):
    async def scenario():
        task_id, workflow, checkpoint = runner
        workflow.execute.side_effect = asyncio.CancelledError()

        async def publish(_, message):
            if getattr(message, "task_status", None) == "stopped":
                raise OSError("archive temporarily unavailable")

        monkeypatch.setattr(router.task_bus, "publish_message", publish)
        router._pending_cancellations.add(task_id)
        try:
            await router.run_modeling_task_async(task_id, "test", "CHINA", "LaTeX")
        except OSError:
            pass
        assert checkpoint.load()["status"] == "stopped"
        assert task_id not in router._active_tasks
        assert task_id not in router._scheduled_tasks
        assert task_id not in router._pending_cancellations
        router.task_bus.clear_cancellation_request.assert_awaited_once_with(
            task_id
        )

    asyncio.run(scenario())


def test_user_stop_during_cleanup_prevents_automatic_retry(runner, monkeypatch):
    async def scenario():
        task_id, workflow, _ = runner
        workflow.execute.side_effect = CoderAgentUnavailableError(
            "provider unavailable"
        )

        async def cleanup():
            router._pending_cancellations.add(task_id)

        workflow.cleanup.side_effect = cleanup
        retry = AsyncMock()
        monkeypatch.setattr(router, "_auto_resume_after_failure", retry)
        await router.run_modeling_task_async(task_id, "test", "CHINA", "LaTeX")
        await asyncio.gather(*router._auto_resume_handles)
        retry.assert_not_awaited()

    asyncio.run(scenario())


def test_cleanup_failure_does_not_start_new_execution(runner, monkeypatch):
    async def scenario():
        task_id, workflow, _ = runner
        workflow.execute.side_effect = CoderAgentUnavailableError(
            "provider unavailable"
        )
        workflow.cleanup.side_effect = RuntimeError("kernel did not exit")
        retry = AsyncMock()
        monkeypatch.setattr(router, "_auto_resume_after_failure", retry)
        await router.run_modeling_task_async(task_id, "test", "CHINA", "LaTeX")
        await asyncio.gather(*router._auto_resume_handles)
        retry.assert_not_awaited()
        assert task_id not in router._active_tasks

    asyncio.run(scenario())


def test_cancel_request_cannot_revert_stopped_state_after_cleanup(runner, monkeypatch):
    async def scenario():
        task_id, _, checkpoint = runner
        started = asyncio.Event()
        finished = asyncio.Event()

        async def worker():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                checkpoint.mark_status("stopped")
                router._active_tasks.pop(task_id, None)
                finished.set()

        async def persist(_):
            await finished.wait()

        monkeypatch.setattr(
            router,
            "_load_workflow_checkpoint",
            lambda _: (checkpoint, checkpoint.load()),
        )
        monkeypatch.setattr(router.task_bus, "request_cancellation", persist)
        task = asyncio.create_task(worker())
        router._active_tasks[task_id] = (task, asyncio.Event())
        await started.wait()
        response = await router.cancel_task(task_id)
        await asyncio.gather(task, return_exceptions=True)
        assert response.success
        assert checkpoint.load()["status"] == "stopped"
        assert task_id not in router._active_tasks

    asyncio.run(scenario())
