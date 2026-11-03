import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm.errors import TransientLLMError
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.routers import modeling_router
from app.schemas.request import Problem
from app.services import team_state


@pytest.fixture
def retry_task(tmp_path, monkeypatch):
    root = tmp_path / "retry-diagnostics"
    root.mkdir()
    checkpoint = WorkflowCheckpoint(root)
    state = checkpoint.initialize(Problem(task_id=root.name))
    state.update(current_node="modeler", status="failed")
    checkpoint.save(state)
    monkeypatch.setattr(modeling_router, "create_work_dir", lambda *_: str(root))
    monkeypatch.setattr(modeling_router, "get_work_dir", lambda *_: str(root))
    monkeypatch.setattr(modeling_router, "_AUTO_RESUME_LIMIT", 1)
    monkeypatch.setattr(modeling_router, "_active_tasks", {})
    monkeypatch.setattr(modeling_router, "_scheduled_tasks", set())
    monkeypatch.setattr(modeling_router, "_pending_cancellations", set())
    monkeypatch.setattr(modeling_router, "_auto_resume_counts", {})
    monkeypatch.setattr(
        modeling_router.task_bus,
        "is_cancellation_requested",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(modeling_router.task_bus, "publish_message", AsyncMock())
    monkeypatch.setattr(
        modeling_router.task_bus, "clear_cancellation_request", AsyncMock()
    )
    return root, checkpoint


def test_cleanup_failure_does_not_leave_unscheduled_retry_in_snapshot(
    retry_task, monkeypatch
):
    root, checkpoint = retry_task
    workflow = MagicMock()
    workflow.execute = AsyncMock(
        side_effect=TransientLLMError("fixture transport failure")
    )
    workflow.cleanup = AsyncMock(side_effect=RuntimeError("fixture cleanup failure"))
    workflow.mark_status.side_effect = checkpoint.mark_status
    monkeypatch.setattr(modeling_router, "RemitWorkFlow", lambda: workflow)
    auto_resume = AsyncMock()
    monkeypatch.setattr(modeling_router, "_auto_resume_after_failure", auto_resume)

    asyncio.run(
        modeling_router.run_modeling_task_async(root.name, "fixture", "CHINA", "LaTeX")
    )

    auto_resume.assert_not_called()
    assert not modeling_router._active_tasks and not modeling_router._scheduled_tasks
    snapshot = team_state.snapshot(root)
    assert snapshot["status"] == "failed"
    assert snapshot["failure"]["retrying"] is False
    assert "resume" in snapshot["failure"]["actions"]


def test_retry_dispatch_failure_restores_manual_recovery(retry_task, monkeypatch):
    root, checkpoint = retry_task
    (root / ".runtime-failure.json").write_text(
        json.dumps(
            {
                "code": "TRANSPORT",
                "retrying": True,
                "stage": "modeler",
                "run_id": checkpoint.load().get("execution_id"),
                "reason": "fixture transport failure",
                "actions": ["diagnostics"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        modeling_router.task_bus,
        "is_cancellation_requested",
        AsyncMock(side_effect=RuntimeError("fixture unavailable state service")),
    )
    run = AsyncMock()
    monkeypatch.setattr(modeling_router, "run_modeling_task_async", run)

    asyncio.run(
        modeling_router._auto_resume_after_failure(
            root.name,
            "fixture",
            "CHINA",
            "LaTeX",
            "",
            0,
        )
    )

    run.assert_not_called()
    assert not modeling_router._scheduled_tasks
    snapshot = team_state.snapshot(root)
    assert snapshot["status"] == "failed"
    assert snapshot["failure"]["retrying"] is False
    assert "resume" in snapshot["failure"]["actions"]
