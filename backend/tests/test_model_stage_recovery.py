"""Replay the exhausted-coding -> interrupted-review -> explicit recovery chain."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.config.setting import settings
from app.core.llm.errors import ModelStageBudgetExceeded
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.routers import modeling_router
from app.schemas.request import Problem
from app.services import call_ledger
from app.services.model_budget import for_task


@pytest.fixture
def task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "project/work_dir/model-recovery"
    root.mkdir(parents=True)
    cp = WorkflowCheckpoint(root)
    state = cp.initialize(Problem(task_id=root.name))
    state.update(
        current_node="solve:eda",
        status="stopped",
        completed_nodes=["coordinator", "research", "analysis", "modeler"],
        questions={"ques1": "solve"},
        ques_count=1,
        modeler_response={"questions_solution": {"ques1": "solve"}},
    )
    cp.save(state)
    monkeypatch.setattr(settings, "LLM_STAGE_CALL_LIMIT", 24)
    monkeypatch.setattr(settings, "LLM_STAGE_API_SECONDS", 900)
    monkeypatch.setattr(settings, "LLM_REVIEW_RESERVED_CALLS", 4)
    monkeypatch.setattr(settings, "LLM_REVIEW_RESERVED_SECONDS", 240)
    monkeypatch.setattr(settings, "LLM_MIN_REQUEST_SECONDS", 60)
    monkeypatch.setattr(modeling_router, "_active_tasks", {})
    monkeypatch.setattr(modeling_router, "_scheduled_tasks", set())
    monkeypatch.setattr(
        modeling_router.task_bus, "clear_cancellation_request", AsyncMock()
    )
    return root, cp


def elapsed(root, attempt, seconds):
    with sqlite3.connect(root / ".calls.sqlite3") as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS calls(attempt_id TEXT PRIMARY KEY,elapsed_seconds REAL)"
        )
        db.execute("INSERT OR REPLACE INTO calls VALUES(?,?)", (attempt, seconds))


def test_coding_cannot_spend_the_last_four_review_calls(task):
    root, _ = task
    for i in range(20):
        call_ledger.reserve(root.name, "CoderAgent", str(i), str(i))
    with pytest.raises(ModelStageBudgetExceeded):
        call_ledger.reserve(root.name, "CoderAgent", "overflow", "overflow")
    with call_ledger.scope(root.name, budget_phase="review"):
        for i in range(4):
            call_ledger.reserve(root.name, "ModelerAgent", f"r{i}", f"r{i}")
        with pytest.raises(ModelStageBudgetExceeded):
            call_ledger.reserve(root.name, "ModelerAgent", "exhausted", "exhausted")
    assert for_task(root.name).snapshot("review")["used"] == 24


def test_coding_time_reserves_review_and_does_not_launch_a_44_second_request(task):
    root, _ = task
    call_ledger.reserve(root.name, "CoderAgent", "c", "c")
    elapsed(root, "c", 660)
    with pytest.raises(ModelStageBudgetExceeded):
        call_ledger.reserve(root.name, "CoderAgent", "blocked", "blocked")
    with call_ledger.scope(root.name, budget_phase="review"):
        assert call_ledger.reserve(root.name, "ModelerAgent", "r", "r") == 240
        elapsed(root, "r", 196)
        with pytest.raises(ModelStageBudgetExceeded, match="44.0"):
            call_ledger.reserve(root.name, "ModelerAgent", "short", "short")
    assert for_task(root.name).snapshot("review")["used"] == 2


def test_parallel_reservation_cannot_take_protected_call_slots(task):
    root, _ = task

    def reserve(i):
        try:
            call_ledger.reserve(root.name, "CoderAgent", str(i), str(i))
            return True
        except ModelStageBudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(30))) == 20


def legacy_exhaustion(task):
    root, cp = task
    budget = for_task(root.name)
    # The exact pre-upgrade three-column ledger, including historic attempts.
    with sqlite3.connect(root / ".calls.sqlite3") as db:
        db.execute(
            "CREATE TABLE reservations(attempt_id TEXT PRIMARY KEY,call_id TEXT,stage_key TEXT)"
        )
        db.executemany(
            "INSERT INTO reservations VALUES(?,?,?)",
            [(str(i), str(i), budget.stage_key) for i in range(24)],
        )
    elapsed(root, "23", 900.015)
    state = cp.load()
    state["pending_model_review"] = "solve:eda"
    cp.save(state)
    return budget


def test_legacy_budget_is_not_reset_and_plain_resume_is_blocked_before_scheduling(task):
    budget = legacy_exhaustion(task)
    snapshot = budget.snapshot("review")
    assert snapshot["used"] == 24 and not snapshot["can_resume"]
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            modeling_router.resume_task(
                task[0].name,
                modeling_router.ResumeTaskRequest(node_id="solve:eda"),
                background,
            )
        )
    assert caught.value.status_code == 409 and not background.tasks
    assert not modeling_router._scheduled_tasks
    assert for_task(task[0].name).snapshot("review")["used"] == 24


def request_for(snapshot):
    return modeling_router.ResumeTaskRequest(
        node_id="solve:eda",
        model_budget_extension={
            "confirmed": True,
            "request_id": "a" * 32,
            "stage_key": snapshot["stage_key"],
            "expected_used": snapshot["used"],
            "expected_limit": snapshot["limit"],
            "expected_seconds": snapshot["seconds_limit"],
            "additional_calls": 6,
            "additional_seconds": 600,
        },
    )


def test_review_recovery_needs_no_code_slots_and_lost_response_cannot_double_grant(
    task, monkeypatch
):
    budget = legacy_exhaustion(task)
    monkeypatch.setattr(settings, "MAX_CODE_EXECUTIONS_PER_STAGE", 1)
    call_ledger.execution_budget(task[0].name, "eda").reserve()
    request = request_for(budget.snapshot("review"))
    background = BackgroundTasks()
    asyncio.run(modeling_router.resume_task(task[0].name, request, background))
    assert len(background.tasks) == 1
    assert budget.snapshot("review")["limit"] == 30
    assert budget.snapshot("review")["seconds_limit"] == 1500
    modeling_router._scheduled_tasks.clear()
    duplicate = BackgroundTasks()
    asyncio.run(modeling_router.resume_task(task[0].name, request, duplicate))
    assert not duplicate.tasks and not modeling_router._scheduled_tasks
    assert budget.snapshot("review")["used"] == 24
    assert budget.snapshot("review")["limit"] == 30


def test_grant_survives_failed_scheduling_without_repeating_allowance(
    task, monkeypatch
):
    budget = legacy_exhaustion(task)
    request = request_for(budget.snapshot("review"))
    monkeypatch.setattr(
        modeling_router.task_bus,
        "clear_cancellation_request",
        AsyncMock(side_effect=ConnectionError("redis")),
    )
    with pytest.raises(ConnectionError):
        asyncio.run(
            modeling_router.resume_task(task[0].name, request, BackgroundTasks())
        )
    assert budget.snapshot("review")["limit"] == 30
    assert not modeling_router._scheduled_tasks
    monkeypatch.setattr(
        modeling_router.task_bus, "clear_cancellation_request", AsyncMock()
    )
    background = BackgroundTasks()
    asyncio.run(modeling_router.resume_task(task[0].name, request, background))
    assert len(background.tasks) == 1 and budget.snapshot("review")["limit"] == 30


@pytest.mark.parametrize(
    "field,value",
    [("stage_key", "b" * 64), ("expected_used", 20), ("expected_seconds", 1200)],
)
def test_stale_confirmation_cannot_modify_or_schedule(task, field, value):
    budget = legacy_exhaustion(task)
    request = request_for(budget.snapshot("review"))
    setattr(request.model_budget_extension, field, value)
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(modeling_router.resume_task(task[0].name, request, background))
    assert caught.value.status_code == 409 and not background.tasks
    assert budget.snapshot("review")["limit"] == 24


def test_old_failed_review_is_discoverable_without_editing_checkpoint(task):
    root, _ = task
    (root / ".runtime-failure.json").write_text(
        json.dumps({"code": "MODEL_STAGE_BUDGET"})
    )
    (root / "eda_quality_report.json").write_text("{}")
    snapshot = asyncio.run(modeling_router.get_model_budget(root.name))
    assert snapshot["phase"] == "review"


def test_refinement_pause_does_not_offer_reserved_review_calls_as_work(task):
    root, cp = task
    budget = legacy_exhaustion(task)
    budget.snapshot("review")
    budget.extend("a" * 32, budget.stage_key, 24, 24, 900, 6, 600)
    budget.reserve("review", "review", "review")
    budget.reserve("work1", "work1", "work")
    budget.reserve("work2", "work2", "work")
    state = cp.load()
    state.pop("pending_model_review")
    state["model_execution_reviews"] = {"eda": [{"review": {"verdict": "refine"}}]}
    cp.save(state)
    (root / "eda_quality_report.json").write_text('{"status":"pass"}')
    (root / ".runtime-failure.json").write_text('{"code":"MODEL_STAGE_BUDGET"}')
    snapshot = asyncio.run(modeling_router.get_model_budget(root.name))
    assert snapshot["phase"] == "work" and not snapshot["can_resume"]
    assert snapshot["used"] == 27 and snapshot["limit"] == 30
    assert budget.snapshot("review")["remaining"] == 3
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(modeling_router.resume_task(root.name, modeling_router.ResumeTaskRequest(node_id="solve:eda"), background))
    assert caught.value.status_code == 409 and not background.tasks


@pytest.mark.parametrize("phase", ["work", "review"])
def test_saved_substep_overrides_legacy_report_inference(task, phase):
    root, cp = task
    state = cp.load()
    state["model_stage_phase"] = {"node": "solve:eda", "phase": phase}
    cp.save(state)
    (root / "eda_quality_report.json").write_text('{}')
    (root / ".runtime-failure.json").write_text('{"code":"MODEL_STAGE_BUDGET"}')
    assert asyncio.run(modeling_router.get_model_budget(root.name))["phase"] == phase


def test_local_stage_timeout_becomes_budget_pause_without_transport_retry(
    task, monkeypatch
):
    from app.core.llm.llm import LLM

    root, _ = task
    monkeypatch.setattr(settings, "LLM_STAGE_API_SECONDS", 0.06)
    monkeypatch.setattr(settings, "LLM_MIN_REQUEST_SECONDS", 0.001)
    model = LLM(api_key="fixture", model="fixture", task_id=root.name)

    async def never_finishes(**kwargs):
        await asyncio.Event().wait()

    model.provider.call = AsyncMock(side_effect=never_finishes)

    async def invoke():
        with call_ledger.scope(root.name, budget_phase="review"):
            await model.chat(publish=False, max_retries=4, retry_delay=0)

    with pytest.raises(ModelStageBudgetExceeded):
        asyncio.run(invoke())
    assert model.provider.call.await_count == 1
    assert for_task(root.name).snapshot("review")["used"] == 1


def test_budget_stop_is_persisted_as_pause_and_never_auto_retried(task, monkeypatch):
    root, checkpoint = task
    error = ModelStageBudgetExceeded(
        used_calls=24,
        call_limit=24,
        elapsed_seconds=900,
        seconds_limit=900,
        phase="review",
    )
    workflow = MagicMock()
    workflow.execute = AsyncMock(side_effect=error)
    workflow.cleanup = AsyncMock()
    workflow.mark_status.side_effect = checkpoint.mark_status
    monkeypatch.setattr(modeling_router, "RemitWorkFlow", lambda: workflow)
    monkeypatch.setattr(
        modeling_router.task_bus,
        "is_cancellation_requested",
        AsyncMock(return_value=False),
    )
    publish = AsyncMock()
    monkeypatch.setattr(modeling_router.task_bus, "publish_message", publish)
    monkeypatch.setattr(modeling_router, "_auto_resume_after_failure", AsyncMock())
    asyncio.run(
        modeling_router.run_modeling_task_async(
            root.name, "fixture", "CHINA", "LaTeX", ""
        )
    )
    assert checkpoint.load()["status"] == "stopped"
    messages = [c.args[1] for c in publish.await_args_list]
    assert any(
        getattr(m, "task_status", None) == "stopped" and "额度暂停" in str(m.content)
        for m in messages
    )
    modeling_router._auto_resume_after_failure.assert_not_called()
    failure = json.loads((root / ".runtime-failure.json").read_text(encoding="utf-8"))
    assert (
        failure["code"] == "MODEL_STAGE_BUDGET" and "resume" not in failure["actions"]
    )
    assert failure["budget_phase"] == "review"
    assert not modeling_router._scheduled_tasks and not modeling_router._active_tasks


def test_writing_recovery_uses_same_revision_key_as_writer_requests(task):
    root, cp = task
    state = cp.load()
    state["current_node"] = "write:ques1"
    cp.save(state)
    (root / "paper").mkdir()
    (root / "paper/input.json").write_text(json.dumps({"revision": 2}))
    writer = for_task(root.name, "WriterAgent:ques1")
    recovered = for_task(root.name)
    assert writer.stage_key == recovered.stage_key
    writer.reserve("writer", "writer", "work")
    assert recovered.snapshot()["used"] == 1
