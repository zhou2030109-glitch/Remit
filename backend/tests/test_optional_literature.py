"""Explicit literature opt-out keeps data checks and survives project startup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from app.core.literature import run_literature_review, build_literature_brief
from app.core.project_audit import evaluate_research
from app.core.workflow import RemitWorkFlow
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.routers import project_router, team_router, modeling_router
from app.schemas.A2A import CoordinatorToModeler
from app.schemas.request import Problem
from app.services.task_bus import task_bus
from app.services.writing_workspace import write_json


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_opt_out_persists_honest_record_without_model_or_search(tmp_path):
    llm = SimpleNamespace(chat=AsyncMock(side_effect=AssertionError("model called")))
    scholar = SimpleNamespace(
        search_papers=AsyncMock(side_effect=AssertionError("search called"))
    )
    review = await run_literature_review(
        task_id="no-search",
        llm=llm,
        scholar=scholar,
        questions={"ques1": "核对已有数据"},
        work_dir=tmp_path,
        enabled=False,
    )
    assert review["status"] == "not_requested"
    assert review["paper_count"] == 0
    assert (tmp_path / "literature_review.json").is_file()
    assert (tmp_path / "method_cards.json").is_file()
    assert "未" in build_literature_brief(review)
    llm.chat.assert_not_called()
    scholar.search_papers.assert_not_called()


@pytest.mark.parametrize(
    "enabled,data_status,expected",
    [
        (False, "completed", "completed"),
        (False, "failed", "warning"),
        (True, "completed", "warning"),
        (None, "completed", "warning"),
    ],
)
def test_opt_out_cannot_mask_data_failure_or_replace_user_choice(
    enabled, data_status, expected
):
    result = evaluate_research(
        {
            "problem": {} if enabled is None else {"literature_enabled": enabled},
            "data_profile": {
                "status": data_status,
                "files": [{"file": "observed.csv"}],
            },
            "literature_review": {"status": "not_requested", "paper_count": 0},
        }
    )
    assert result["status"] == expected
    if expected == "warning":
        assert result["issues"]
    else:
        assert "没有新增文献证据" in result["summary"]


@pytest.mark.anyio
async def test_research_still_profiles_real_csv_and_resume_does_not_call_model(
    tmp_path, monkeypatch
):
    (tmp_path / "observed.csv").write_text("x,y\n0,1\n1,3\n2,5\n", encoding="utf-8")
    cp = WorkflowCheckpoint(tmp_path)
    state = cp.initialize(Problem(task_id=tmp_path.name, literature_enabled=False))
    state["questions"] = {"ques1": "核对数据"}
    flow = RemitWorkFlow()
    flow.task_id, flow.work_dir, flow.checkpoint = tmp_path.name, str(tmp_path), cp
    monkeypatch.setattr(task_bus, "publish_message", AsyncMock())
    llm = SimpleNamespace(
        chat=AsyncMock(side_effect=AssertionError("unexpected model call"))
    )
    scholar = SimpleNamespace(
        search_papers=AsyncMock(side_effect=AssertionError("unexpected search"))
    )
    response = CoordinatorToModeler(questions={"ques1": "核对数据"}, ques_count=1)
    result = await flow._research_node(state, response, llm, scholar)
    assert result.data_profile["status"] == "completed"
    assert result.data_profile["files"][0]["rows"] == 3
    assert state["node_outcomes"]["research"]["status"] == "completed"
    restored = cp.load()
    assert restored["problem"]["literature_enabled"] is False
    resumed = await flow._research_node(restored, response, llm, scholar)
    assert resumed.data_profile == result.data_profile
    llm.chat.assert_not_called()
    scholar.search_papers.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("value,expected", [(None, True), ("false", False)])
@pytest.mark.parametrize("purpose", ["modeling", "numerical_verification"])
async def test_http_creation_and_start_preserve_choice(
    tmp_path, monkeypatch, value, expected, purpose
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(team_router, "send_message", AsyncMock())
    runner = AsyncMock()
    monkeypatch.setattr(modeling_router, "run_modeling_task_async", runner)
    app = FastAPI()
    app.include_router(project_router.router)
    form = {"ques_all": "核对已给数据", "task_purpose": purpose}
    if value is not None:
        form["literature_enabled"] = value
    with TestClient(app) as client:
        response = client.post("/api/projects", data=form)
    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    root = tmp_path / "project" / "work_dir" / task_id
    meta = project_router.metadata(root)
    assert meta["problem"]["literature_enabled"] is expected
    assert meta["problem"]["task_purpose"] == purpose
    meta.update(
        status="ready",
        preflight={
            "id": "approved-plan",
            "steps": ["核验数据"],
            "questions": [],
            "configuration_digest": project_router.configuration_digest(meta),
        },
    )
    write_json(root / ".project.json", meta)
    try:
        background = BackgroundTasks()
        await project_router.start(root, "approved-plan", background)
        assert (
            WorkflowCheckpoint(root).load()["problem"]["literature_enabled"] is expected
        )
        assert WorkflowCheckpoint(root).load()["problem"]["task_purpose"] == purpose
        await background()
        runner.assert_awaited_once()
    finally:
        modeling_router._scheduled_tasks.discard(task_id)
