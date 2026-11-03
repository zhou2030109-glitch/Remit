import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config.setting import settings
from app.core.workflow import RemitWorkFlow
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.schemas.A2A import CoordinatorToModeler, ModelerToCoder, ModelScoutProposal
from app.schemas.request import Problem
from app.services.task_bus import task_bus


@pytest.fixture
def modeling(tmp_path, monkeypatch):
    workflow = RemitWorkFlow()
    workflow.task_id = "parallel-recovery"
    workflow.work_dir = str(tmp_path)
    workflow.checkpoint = WorkflowCheckpoint(tmp_path)
    state = workflow.checkpoint.initialize(Problem(task_id=workflow.task_id))
    coordinator = CoordinatorToModeler(questions={"ques1": "solve"}, ques_count=1)
    state.update(
        questions=coordinator.questions,
        ques_count=1,
        coordinator_response=coordinator.model_dump(mode="json"),
        analysis_response=coordinator.model_dump(mode="json"),
        completed_nodes=["coordinator", "research", "analysis"],
    )
    workflow.checkpoint.start_node(state, "modeler")
    monkeypatch.setattr(settings, "METHOD_RETRIEVAL_ENABLED", False)
    monkeypatch.setattr(task_bus, "publish_message", AsyncMock())
    primary = ModelerToCoder(questions_solution={"ques1": "saved plan"})
    scout = ModelScoutProposal(
        questions={},
        global_data_risks=["small sample"],
        cross_question_strategy="Reuse verified input data for each independent question.",
    )
    agent = SimpleNamespace(run=AsyncMock(return_value=primary))
    council = SimpleNamespace(
        propose=AsyncMock(return_value=scout),
        review=AsyncMock(side_effect=RuntimeError("review reached")),
        critic_call_limit=1,
    )
    return workflow, state, coordinator, agent, council, primary, scout


def run_node(modeling):
    workflow, state, coordinator, agent, council, *_ = modeling
    return workflow._modeler_node(state, coordinator, agent, council)


def test_failed_parallel_branch_is_drained_before_return(modeling):
    async def scenario():
        *_, agent, council, primary, scout = modeling
        started = asyncio.Event()
        finished = asyncio.Event()

        async def fail(_):
            await started.wait()
            raise RuntimeError("primary failed")

        async def blocked(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        agent.run.side_effect = fail
        council.propose.side_effect = blocked
        with pytest.raises(RuntimeError, match="primary failed"):
            await run_node(modeling)
        assert finished.is_set(), "orphaned scout keeps using provider after task fails"

    asyncio.run(scenario())


def test_successful_branch_is_saved_when_its_peer_fails(modeling):
    async def scenario():
        workflow, _, _, agent, council, primary, _ = modeling
        completed = asyncio.Event()

        async def success(_):
            completed.set()
            return primary

        async def fail(_):
            await completed.wait()
            raise RuntimeError("scout failed")

        agent.run.side_effect = success
        council.propose.side_effect = fail
        with pytest.raises(RuntimeError, match="scout failed"):
            await run_node(modeling)
        assert workflow.checkpoint.load()[
            "modeler_primary_response"
        ] == primary.model_dump(mode="json")

    asyncio.run(scenario())


def test_revised_plan_retry_keeps_completed_branch_until_next_revision(modeling):
    async def scenario():
        workflow, state, _, agent, council, primary, scout = modeling
        state["revision_feedback"] = {"modeler": "Use the corrected source data."}
        state["revision_counts"] = {"modeler": 1}
        state["modeler_primary_response"] = {"questions_solution": {"ques1": "stale"}}
        council.propose.side_effect = RuntimeError("scout failed")
        with pytest.raises(RuntimeError, match="scout failed"):
            await run_node(modeling)
        assert agent.run.await_count == 1
        assert state["modeler_primary_response"] == primary.model_dump(mode="json")
        resumed = workflow.checkpoint.prepare_resume(
            workflow.checkpoint.load(), "modeler"
        )
        state.clear()
        state.update(resumed)
        council.propose.side_effect = None
        council.propose.return_value = scout
        with pytest.raises(RuntimeError, match="review reached"):
            await run_node(modeling)
        assert agent.run.await_count == 1
        assert council.propose.await_count == 2
        state["revision_counts"]["modeler"] = 2
        with pytest.raises(RuntimeError, match="review reached"):
            await run_node(modeling)
        assert agent.run.await_count == 2
        assert council.propose.await_count == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("cached", ["primary", "scout"])
def test_partial_checkpoint_only_repeats_missing_request(modeling, cached):
    async def scenario():
        workflow, state, _, agent, council, primary, scout = modeling
        key, value, call = (
            ("modeler_primary_response", primary, agent.run)
            if cached == "primary"
            else ("model_scout_proposal", scout, council.propose)
        )
        state[key] = value.model_dump(mode="json")
        workflow.checkpoint.save(state)
        with pytest.raises(RuntimeError, match="review reached"):
            await run_node(modeling)
        call.assert_not_awaited()
        assert workflow.checkpoint.load()[key] == value.model_dump(mode="json")

    asyncio.run(scenario())


def test_cancellation_waits_for_both_branch_cleanups(modeling):
    async def scenario():
        _, _, _, agent, council, *_ = modeling
        started = [asyncio.Event(), asyncio.Event()]
        finished = [asyncio.Event(), asyncio.Event()]

        async def blocked(index):
            started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                finished[index].set()

        async def primary(_):
            return await blocked(0)

        async def scout(_):
            return await blocked(1)

        agent.run.side_effect = primary
        council.propose.side_effect = scout
        task = asyncio.create_task(run_node(modeling))
        await asyncio.gather(*(event.wait() for event in started))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(event.is_set() for event in finished)

    asyncio.run(scenario())


@pytest.mark.parametrize("initial_failure", [True, False])
def test_repeated_cancellation_cannot_abandon_branch_cleanup(modeling, initial_failure):
    async def scenario():
        _, _, _, agent, council, *_ = modeling
        started = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def primary(_):
            await started.wait()
            if initial_failure:
                raise RuntimeError("primary failed")
            await asyncio.Event().wait()

        async def scout(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                finished.set()

        agent.run.side_effect = primary
        council.propose.side_effect = scout
        task = asyncio.create_task(run_node(modeling))
        try:
            await asyncio.wait_for(started.wait(), 2)
            if not initial_failure:
                task.cancel()
            await asyncio.wait_for(cleaning.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "outer task must retain ownership during cleanup"
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert finished.is_set()
        assert task.cancelled()

    asyncio.run(scenario())


@pytest.mark.parametrize("technical_resume", [True, False])
def test_interrupted_modeling_preserves_only_technical_resume_cache(
    modeling, technical_resume
):
    workflow, state, _, _, _, primary, scout = modeling
    state.update(
        modeler_primary_response=primary.model_dump(mode="json"),
        model_scout_proposal=scout.model_dump(mode="json"),
        model_council={"critic_model": "saved-reviewer", "critic_calls_used": 1},
    )
    workflow.checkpoint.save(state)
    resumed = workflow.checkpoint.prepare_resume(
        workflow.checkpoint.load(),
        "modeler",
        preserve_interrupted_artifacts=technical_resume,
    )
    for key in ("modeler_primary_response", "model_scout_proposal", "model_council"):
        assert (key in resumed) == technical_resume
    assert resumed["fable_critic_calls_used"] == 1


def test_stopping_status_is_durable(modeling):
    workflow, *_ = modeling
    workflow.mark_status("stopping")
    assert workflow.checkpoint.load()["status"] == "stopping"
