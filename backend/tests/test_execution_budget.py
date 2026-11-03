"""Durable code reservations, checkpoint recovery and actual Coder boundaries."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.setting import settings
from app.core.agents.coder_agent import CoderAgent
from app.core.llm.types import StandardResponse, ToolCall
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.routers.modeling_router import _is_transient_task_failure
from app.routers import modeling_router
from fastapi import BackgroundTasks, HTTPException
from app.schemas.request import Problem
from app.services import call_ledger, task_failure


@pytest.fixture
def task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "project/work_dir/budget-test"
    root.mkdir(parents=True)
    checkpoint = WorkflowCheckpoint(root)
    state = checkpoint.initialize(Problem(task_id="budget-test"))
    state.update(
        questions={"ques1": "first", "ques2": "second"},
        ques_count=2,
        modeler_response={"questions_solution": {"ques1": "first", "ques2": "second"}},
        completed_nodes=[
            "coordinator",
            "research",
            "analysis",
            "modeler",
            "solve:eda",
            "pilot",
        ],
        current_node="solve:ques1",
    )
    checkpoint.save(state)
    monkeypatch.setattr(settings, "MAX_CODE_EXECUTIONS_PER_STAGE", 2)
    monkeypatch.setattr(modeling_router, "_scheduled_tasks", set())
    monkeypatch.setattr(modeling_router, "_active_tasks", {})
    monkeypatch.setattr(
        modeling_router.task_bus, "clear_cancellation_request", AsyncMock()
    )
    return root, checkpoint


def budget():
    return call_ledger.execution_budget("budget-test", "ques1")


def test_reservations_are_atomic_and_survive_new_instances(task):
    def reserve(_):
        try:
            return budget().reserve()
        except call_ledger.ExecutionBudgetExceeded:
            return "blocked"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(reserve, range(12)))
    assert sorted(x for x in results if isinstance(x, int)) == [0, 1]
    assert results.count("blocked") == 10
    with pytest.raises(call_ledger.ExecutionBudgetExceeded) as error:
        budget().remaining()
    assert (error.value.used, error.value.limit) == (2, 2)
    assert not _is_transient_task_failure(error.value)
    report = task_failure.describe(error.value, task[0], retrying=False, attempts=1)
    assert report["code"] == "EXECUTION_BUDGET" and not report["retryable"]
    assert "2/2" in report["reason"]


def test_technical_resume_keeps_count_and_explicit_revision_renews_affected_only(task):
    root, checkpoint = task
    old = budget()
    old.reserve()
    before = checkpoint.load()
    before["execution_budget_epochs"]["solve:eda"] = 7
    checkpoint.save(before)
    checkpoint.mark_status("stopped")
    checkpoint.prepare_resume(checkpoint.load(), "solve:ques1")
    state = checkpoint.load()
    state["execution_id"] = "a" * 32
    checkpoint.save(state)
    assert budget().remaining() == 1
    budget().reserve()
    with pytest.raises(call_ledger.ExecutionBudgetExceeded):
        budget().remaining()
    pending = checkpoint.request_approval(
        state, "solve:ques1", summary="change required", allow_incomplete=True
    )
    revised = checkpoint.request_revision(
        state, pending["checkpoint_id"], "Use the revised user requirement"
    )
    assert revised["execution_budget_epochs"]["solve:eda"] == 7
    assert revised["execution_budget_epochs"]["solve:ques1"] == 1
    assert revised["execution_budget_epochs"]["solve:ques2"] == 1
    assert budget().remaining() == 2
    with pytest.raises(call_ledger.ExecutionBudgetExceeded):
        old.remaining()  # New epoch does not erase or relabel old reservations.
    with sqlite3.connect(root / ".calls.sqlite3") as db:
        assert (
            db.execute("SELECT COUNT(*) FROM execution_reservations").fetchone()[0] == 2
        )


def test_stage_change_has_separate_budget_and_snapshot_is_stable(task):
    _, checkpoint = task
    first = budget()
    first.reserve()
    state = checkpoint.load()
    state["current_node"] = "solve:ques2"
    checkpoint.save(state)
    second = budget()
    assert second.remaining() == 2
    first.reserve()
    assert second.remaining() == 2


def make_agent(root, monkeypatch, failed=False):
    interpreter = MagicMock(language="python", backend_name="test executor")
    interpreter.execute_code = AsyncMock(
        return_value=("saved", failed, "error" if failed else "")
    )
    interpreter.get_created_images = AsyncMock(return_value=[])
    agent = CoderAgent(
        "budget-test", MagicMock(), str(root), code_interpreter=interpreter
    )
    agent._prime_history = AsyncMock()
    agent._inject_user_notes = AsyncMock()
    agent._notify = AsyncMock()
    agent._call_model = AsyncMock(
        side_effect=[
            StandardResponse(
                content="execute",
                tool_calls=[
                    ToolCall(
                        id="fixed-tool-id",
                        name="execute_code",
                        arguments=json.dumps({"code": "print(1)"}),
                    )
                ],
            ),
            StandardResponse(content="review saved evidence"),
        ]
    )
    monkeypatch.setattr("app.core.agents.coder_agent.publish_activity", AsyncMock())
    monkeypatch.setattr(
        "app.core.agents.coder_agent.task_bus.publish_message", AsyncMock()
    )
    return agent


@pytest.mark.parametrize("failed", [False, True])
def test_new_coder_after_last_slot_cannot_call_model_or_executor(
    task, monkeypatch, failed
):
    root, _ = task
    monkeypatch.setattr(settings, "MAX_CODE_EXECUTIONS_PER_STAGE", 1)
    first = make_agent(root, monkeypatch, failed)
    asyncio.run(first.run("compute", "ques1"))
    first.code_interpreter.execute_code.assert_awaited_once()
    assert first._call_model.call_args.kwargs["tool_choice"] == "none"
    fresh = make_agent(root, monkeypatch)
    with pytest.raises(call_ledger.ExecutionBudgetExceeded):
        asyncio.run(fresh.run("technical retry", "ques1"))
    fresh._call_model.assert_not_awaited()
    fresh.code_interpreter.execute_code.assert_not_awaited()


@pytest.mark.parametrize("corrupt", ["ledger", "checkpoint"])
def test_corrupt_storage_stops_before_model_or_execution(task, monkeypatch, corrupt):
    root, _ = task
    target = ".calls.sqlite3" if corrupt == "ledger" else "workflow_state.json"
    (root / target).write_text("invalid data", encoding="utf-8")
    agent = make_agent(root, monkeypatch)
    with pytest.raises(call_ledger.ExecutionBudgetUnavailable):
        asyncio.run(agent.run("compute", "ques1"))
    agent._call_model.assert_not_awaited()
    agent.code_interpreter.execute_code.assert_not_awaited()


def test_reservation_checks_again_after_model_wait(task, monkeypatch):
    root, _ = task
    monkeypatch.setattr(settings, "MAX_CODE_EXECUTIONS_PER_STAGE", 1)
    agent = make_agent(root, monkeypatch)
    response = agent._call_model.side_effect

    # Consume the final slot while the model response is pending in another run.
    async def model(*args, **kwargs):
        budget().reserve()
        return next(response)

    agent._call_model = AsyncMock(side_effect=model)
    with pytest.raises(call_ledger.ExecutionBudgetExceeded):
        asyncio.run(agent.run("compute", "ques1"))
    agent.code_interpreter.execute_code.assert_not_awaited()


def test_cancel_before_execution_does_not_reserve(task, monkeypatch):
    root, _ = task
    agent = make_agent(root, monkeypatch)
    agent.cancel_event = asyncio.Event()

    async def publish(*args, **kwargs):
        agent.cancel_event.set()

    monkeypatch.setattr(
        "app.core.agents.coder_agent.task_bus.publish_message", publish
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run("compute", "ques1"))
    agent.code_interpreter.execute_code.assert_not_awaited()
    assert budget().remaining() == 2


def test_storage_failure_after_model_response_still_prevents_execution(
    task, monkeypatch
):
    root, _ = task
    agent = make_agent(root, monkeypatch)
    responses = agent._call_model.side_effect

    async def model(*args, **kwargs):
        (root / ".calls.sqlite3").write_bytes(b"corrupted after initial budget check")
        return next(responses)

    agent._call_model = AsyncMock(side_effect=model)
    with pytest.raises(call_ledger.ExecutionBudgetUnavailable) as error:
        asyncio.run(agent.run("compute", "ques1"))
    agent.code_interpreter.execute_code.assert_not_awaited()
    assert not _is_transient_task_failure(error.value)
    report = task_failure.describe(error.value, root, retrying=False, attempts=1)
    assert report["code"] == "EXECUTION_BUDGET_STORAGE"


def test_cancel_after_reservation_keeps_unknown_slot(task, monkeypatch):
    root, _ = task
    agent = make_agent(root, monkeypatch)
    agent.cancel_event = asyncio.Event()
    reserve = call_ledger.ExecutionBudget.reserve

    def cancel_after_reservation(self):
        result = reserve(self)
        agent.cancel_event.set()
        return result

    monkeypatch.setattr(
        call_ledger.ExecutionBudget, "reserve", cancel_after_reservation
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run("compute", "ques1"))
    agent.code_interpreter.execute_code.assert_not_awaited()
    assert budget().remaining() == 1


def test_legacy_checkpoint_without_epochs_keeps_new_reservations(task):
    _, checkpoint = task
    state = checkpoint.load()
    state.pop("execution_budget_epochs")
    checkpoint.save(state)
    budget().reserve()
    checkpoint.prepare_resume(checkpoint.load(), "solve:ques1")
    assert budget().remaining() == 1


def reviewed_extension(task):
    budget().reserve()
    budget().reserve()
    task[1].mark_status("failed")
    view = asyncio.run(modeling_router.get_execution_budget("budget-test"))
    return modeling_router.ResumeTaskRequest(
        node_id=view["node_id"],
        execution_budget_extension={
            "confirmed": True,
            "request_id": "b" * 32,
            "stage_key": view["stage_key"],
            "expected_used": view["used"],
            "expected_limit": view["limit"],
            "additional": 3,
        },
    )


def test_reviewed_grant_and_lost_response_retry_are_idempotent(task, monkeypatch):
    request = reviewed_extension(task)
    monkeypatch.setattr(
        modeling_router.task_bus,
        "clear_cancellation_request",
        AsyncMock(side_effect=RuntimeError("temporary redis failure")),
    )
    with pytest.raises(RuntimeError, match="temporary redis"):
        asyncio.run(
            modeling_router.resume_task("budget-test", request, BackgroundTasks())
        )
    assert "budget-test" not in modeling_router._scheduled_tasks
    assert budget().snapshot()["limit"] == 5
    assert budget().snapshot()["used"] == 2
    monkeypatch.setattr(
        modeling_router.task_bus, "clear_cancellation_request", AsyncMock()
    )
    background = BackgroundTasks()
    result = asyncio.run(
        modeling_router.resume_task("budget-test", request, background)
    )
    assert result.success and len(background.tasks) == 1
    assert budget().snapshot()["limit"] == 5
    assert task[1].load()["execution_budget_epochs"] == {}
    # Even after the first worker leaves the active registry, re-delivery of
    # this same confirmation cannot schedule its action a second time.
    modeling_router._scheduled_tasks.discard("budget-test")
    duplicate_background = BackgroundTasks()
    duplicate = asyncio.run(
        modeling_router.resume_task("budget-test", request, duplicate_background)
    )
    assert duplicate.success and not duplicate_background.tasks
    assert not modeling_router._scheduled_tasks


def test_plain_resume_does_not_silently_extend_exhausted_stage(task):
    reviewed_extension(task)
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            modeling_router.resume_task(
                "budget-test",
                modeling_router.ResumeTaskRequest(node_id="solve:ques1"),
                background,
            )
        )
    assert error.value.status_code == 409
    assert not background.tasks and not modeling_router._scheduled_tasks
    assert budget().snapshot()["limit"] == 2


@pytest.mark.parametrize(
    "field,value",
    [("stage_key", "c" * 64), ("expected_used", 1), ("expected_limit", 3)],
)
def test_changed_review_does_not_grant_or_schedule(task, field, value):
    request = reviewed_extension(task)
    setattr(request.execution_budget_extension, field, value)
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        asyncio.run(modeling_router.resume_task("budget-test", request, background))
    assert error.value.status_code == 409
    assert not background.tasks and not modeling_router._scheduled_tasks
    assert budget().snapshot()["limit"] == 2


def test_duplicate_click_while_redis_is_pending_schedules_once(task, monkeypatch):
    request = reviewed_extension(task)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def clear(*args):
            entered.set()
            await release.wait()

        monkeypatch.setattr(
            modeling_router.task_bus, "clear_cancellation_request", clear
        )
        first_background, duplicate_background = BackgroundTasks(), BackgroundTasks()
        first = asyncio.create_task(
            modeling_router.resume_task("budget-test", request, first_background)
        )
        await asyncio.wait_for(entered.wait(), 5)
        try:
            with pytest.raises(HTTPException) as error:
                await modeling_router.resume_task(
                    "budget-test", request, duplicate_background
                )
            assert error.value.status_code == 409
        finally:
            release.set()
            await first
        assert len(first_background.tasks) == 1 and not duplicate_background.tasks

    asyncio.run(run())
    assert budget().snapshot()["limit"] == 5


@pytest.mark.parametrize(
    "payload",
    [
        {"confirmed": False},
        {"additional": 0},
        {"additional": 49},
        {"additional": 1.5},
    ],
)
def test_extension_requires_explicit_confirmation_and_bounded_integer(task, payload):
    from pydantic import ValidationError

    request = reviewed_extension(task).model_dump()
    request["execution_budget_extension"].update(payload)
    with pytest.raises(ValidationError):
        modeling_router.ResumeTaskRequest.model_validate(request)
