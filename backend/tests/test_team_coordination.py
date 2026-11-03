"""共享状态与对话调度回归：验证真实副作用边界、并发与断线恢复。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.core.llm.llm import LLM
from app.core.llm.types import StandardResponse
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.routers import modeling_router, team_router as router, writing_router
from app.schemas.request import Problem
from app.services import team_state as team
from app.services.task_bus import task_bus
from app.services import writing_workspace as paper


def test_explanation_accepts_null_instruction_without_relaxing_action_validation():
    from pydantic import ValidationError

    plan = router.Plan.model_validate(
        {
            "action": "reply",
            "instruction": None,
            "reply": "目前只有方案，还没有计算结果。",
        }
    )
    assert plan.action == "reply"
    assert plan.instruction == ""
    with pytest.raises(ValidationError):
        router.Plan.model_validate({"action": "revise", "instruction": None})


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "project/work_dir/team-test"
    root.mkdir(parents=True)
    checkpoint = WorkflowCheckpoint(root)
    state = checkpoint.initialize(
        Problem(task_id="team-test", ques_all="根据数据预测温度")
    )
    state.update(
        questions={"ques1": "预测温度"}, coordinator_response={}, modeler_response={}
    )
    checkpoint.save(state)
    monkeypatch.setattr(task_bus, "load_task_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(task_bus, "clear_cancellation_request", AsyncMock())
    monkeypatch.setattr(task_bus, "publish_message", AsyncMock())
    router._locks.clear()
    router._interruptions.clear()
    yield root, checkpoint
    modeling_router._scheduled_tasks.discard("team-test")
    router._workers.clear()


def test_shared_table_reflects_checkpoints_and_independent_writer(project):
    root, checkpoint = project
    state = checkpoint.load()
    checkpoint.start_node(state, "modeler")
    snapshot = team.snapshot(root)
    assert (
        next(step for step in snapshot["steps"] if step["id"] == "modeler")["status"]
        == "running"
    )
    checkpoint.complete_node(state, "modeler")
    pending = checkpoint.request_approval(state, "modeler", summary="候选模型已完成")
    assert (
        team.snapshot(root)["pending_approval"]["checkpoint_id"]
        == pending["checkpoint_id"]
    )
    checkpoint.approve(state, pending["checkpoint_id"])
    checkpoint.mark_status("completed")
    writing_root = paper.ensure_workspace(root)
    writing_router._set_generation(writing_root, "failed", error="Writer failed")
    snapshot = team.snapshot(root)
    assert snapshot["status"] == "completed"
    assert snapshot["writing"]["status"] == "failed"
    assert any(item["kind"] == "writing" for item in team.events(root))
    phases = [
        item["data"] for item in team.events(root) if item["kind"] == "checkpoint"
    ]
    assert any(
        item["current_node"] == "modeler" and item["status"] == "running"
        for item in phases
    )
    assert any(item["status"] == "awaiting_approval" for item in phases)


def test_shared_table_preserves_research_warning_and_running_precedence(project):
    root, checkpoint = project
    state = checkpoint.load()
    state["completed_nodes"] = ["coordinator", "research"]
    state["current_node"] = "analysis"
    state["data_profile"] = {
        "status": "partial",
        "files": [{"file": "data.mat"}],
        "notes": ["需要 MATLAB 读取对象"],
    }
    state["literature_review"] = {
        "status": "completed",
        "paper_count": 2,
        "method_cards": [{}],
        "fulltext_stats": {"succeeded": 1},
    }
    checkpoint.save(state)
    steps = {step["id"]: step for step in team.snapshot(root)["steps"]}
    assert steps["research"]["status"] == "warning"
    assert steps["analysis"]["status"] == "running"
    assert steps["coordinator"]["status"] == "completed"


def test_shared_requirements_are_read_by_every_role_without_consumption(project):
    root, _ = project
    team.directive(root, "all", "所有数值须来自执行结果", "common")
    team.directive(root, "coder", "保存逐样本预测表", "code")
    with ThreadPoolExecutor(max_workers=3) as pool:
        contexts = list(
            pool.map(
                lambda role: team.context_for("team-test", role),
                ["CoderAgent", "ModelerAgent", "WriterAgent"],
            )
        )
    assert all("所有数值须来自执行结果" in value for value in contexts)
    state = team.snapshot(root)
    shared = next(item for item in state["directives"] if item["id"] == "common")
    assert set(shared["seen"]) == {"coder", "modeler", "writer"}
    private = next(item for item in state["directives"] if item["id"] == "code")
    assert private["seen"] == ["coder"]
    before = len([item for item in team.events(root) if item["kind"] == "receipt"])
    team.context_for("team-test", "CoderAgent")
    assert (
        len([item for item in team.events(root) if item["kind"] == "receipt"]) == before
    )


def test_large_shared_results_keep_requirements_and_attachment_manifest(project):
    root, checkpoint = project
    state = checkpoint.load()
    summary = {
        "status": "needs_review",
        "run_summary": "计算结束但证据待复核",
        "content": "计算结束但证据待复核",
        "modeler_weaknesses": ["尚未通过独立检验"],
        "writer_guidance": "不得声称已经验收",
        "metrics": [{"name": "误差", "model_value": 0.123456789}],
        "table_previews": [
            {
                "filename": "result.csv",
                "columns": ["说明"],
                "rows": [{"说明": "详细输出" * 6000}],
            }
        ],
    }
    state["solution_results"] = {
        "ques1": {"execution_summary": summary, "artifacts": ["result.csv"]}
    }
    checkpoint.save(state)
    team.directive(root, "all", "必须保留不确定性", "all-constraint")
    before = (root / "workflow_state.json").read_bytes()
    context = team.context_for("team-test", "CoderAgent")
    brief = json.loads(context[context.index("{") :])
    assert brief["my_instructions"] == ["必须保留不确定性"]
    assert brief["attachment_manifest"] == ".remit-inputs.json"
    result = brief["results"][0]
    assert result["artifacts"] == ["result.csv"]
    for field in (
        "status",
        "run_summary",
        "modeler_weaknesses",
        "writer_guidance",
        "metrics",
    ):
        assert result["summary"][field] == summary[field]
    assert result["summary_source"] == {
        "path": "workflow_state.json",
        "result_key": "ques1",
        "field": "execution_summary",
    }
    preview = result["summary"]["table_previews"][0]
    assert preview["filename"] == "result.csv"
    assert preview["rows_omitted"] == 1
    assert len(context) < 18000
    assert (root / "workflow_state.json").read_bytes() == before
    assert team.snapshot(root)["results"][0]["summary"] == summary


def test_long_shared_requirement_is_complete_and_checked_before_provider(project):
    from app.core.llm.errors import NonRetryableLLMError

    root, _ = project
    instruction = "必须遵守的完整约束；" * 2200 + "末尾约束不可丢失"
    team.directive(root, "coder", instruction, "long-constraint")
    context = team.context_for("team-test", "CoderAgent")
    brief = json.loads(context[context.index("{") :])
    assert brief["my_instructions"] == [instruction]
    llm = LLM(api_key="test", model="test", task_id="team-test")
    llm.context_window = 2000
    llm.provider.call = AsyncMock(return_value=StandardResponse(content="unexpected"))
    with pytest.raises(NonRetryableLLMError, match="最终请求超出"):
        asyncio.run(llm.chat(agent_name="CoderAgent", max_tokens=100, publish=False))
    llm.provider.call.assert_not_awaited()


def test_llm_refreshes_shared_context_each_call_without_growing_history(project):
    root, _ = project
    llm = LLM(api_key="test", model="test", task_id="team-test")
    llm.provider.call = AsyncMock(return_value=StandardResponse(content="done"))
    history = [
        {"role": "system", "content": "系统要求"},
        {"role": "user", "content": "计算"},
    ]

    async def run():
        await llm.chat(history=history, agent_name="CoderAgent", publish=False)
        team.directive(root, "coder", "新增要求：输出误差分布图", "later")
        await llm.chat(history=history, agent_name="CoderAgent", publish=False)

    asyncio.run(run())
    calls = llm.provider.call.call_args_list
    assert "误差分布图" not in calls[0].kwargs["messages"][-1]["content"]
    assert "误差分布图" in calls[1].kwargs["messages"][-1]["content"]
    assert len(history) == 2


def test_duplicate_requests_only_dispatch_once_and_payload_cannot_change(
    project, monkeypatch
):
    root, _ = project
    planner = AsyncMock(
        return_value=router.Plan(
            action="instruct", role="coder", instruction="保存预测表"
        )
    )
    monkeypatch.setattr(router, "make_plan", planner)
    body = router.ChatRequest(request_id="request-123", content="代码手保存预测表")

    async def run():
        await asyncio.gather(
            router.send_message("team-test", body),
            router.send_message("team-test", body),
        )
        await asyncio.gather(*list(router._workers.values()))
        result = await router.send_message("team-test", body)
        assert result["status"] == "completed"
        with pytest.raises(HTTPException) as error:
            await router.send_message(
                "team-test", body.model_copy(update={"action": "stop"})
            )
        assert error.value.status_code == 409

    asyncio.run(run())
    assert planner.await_count == 1
    assert len(team.snapshot(root)["directives"]) == 1
    assert len([item for item in team.events(root) if item["kind"] == "chat"]) == 1


def test_stale_or_ambiguous_approval_cannot_continue(project, monkeypatch):
    root, checkpoint = project
    state = checkpoint.load()
    checkpoint.complete_node(state, "modeler")
    pending = checkpoint.request_approval(state, "modeler", summary="验收")
    run = AsyncMock()
    monkeypatch.setattr(modeling_router, "run_modeling_task_async", run)

    async def check():
        background = BackgroundTasks()
        plan = router.Plan(action="approve")
        with pytest.raises(HTTPException) as error:
            await router.execute_plan(
                "team-test",
                router.ChatRequest(
                    request_id="approve-1", content="批准当前步骤", checkpoint_id="old"
                ),
                plan,
                root,
                background,
            )
        assert error.value.status_code == 409
        body = router.ChatRequest(
            request_id="approve-2",
            content="如果正确就批准",
            checkpoint_id=pending["checkpoint_id"],
        )
        result = await router.execute_plan("team-test", body, plan, root, background)
        assert "明确验收" in result["message"]
        assert checkpoint.load()["pending_approval"] is not None
        body = body.model_copy(update={"content": "批准当前步骤"})
        await router.execute_plan("team-test", body, plan, root, background)
        assert checkpoint.load()["pending_approval"] is None
        await background()

    asyncio.run(check())
    run.assert_awaited_once()


def test_completed_model_revision_dispatches_actual_workflow_with_shared_feedback(
    project, monkeypatch
):
    root, checkpoint = project
    state = checkpoint.load()
    state["completed_nodes"] = checkpoint.node_order(state)
    state["status"] = "completed"
    checkpoint.save(state)
    runner = AsyncMock()
    monkeypatch.setattr(modeling_router, "run_modeling_task_async", runner)

    async def run():
        background = BackgroundTasks()
        await router.execute_plan(
            "team-test",
            router.ChatRequest(request_id="revise-123", content="建模手重新设计验证"),
            router.Plan(
                action="revise",
                role="modeler",
                node_id="modeler",
                instruction="改为时间切分验证",
            ),
            root,
            background,
        )
        await background()

    asyncio.run(run())
    assert runner.call_args.args[5] == "modeler"
    assert "时间切分验证" in team.context_for("team-test", "ModelerAgent")


def test_writer_dispatch_waits_for_modeling_and_calls_real_entrypoint(
    project, monkeypatch
):
    root, checkpoint = project
    sync = AsyncMock()
    generate = AsyncMock()
    monkeypatch.setattr(writing_router, "sync", sync)
    monkeypatch.setattr(writing_router, "generate", generate)
    body = router.ChatRequest(request_id="write-123", content="论文手强调模型局限")

    async def run():
        with pytest.raises(HTTPException):
            await router.execute_plan(
                "team-test", body, router.Plan(action="write"), root, BackgroundTasks()
            )
        checkpoint.mark_status("completed")
        result = await router.execute_plan(
            "team-test",
            body,
            router.Plan(action="write", instruction="强调模型局限"),
            root,
            BackgroundTasks(),
        )
        assert result["link"] == "/writing/team-test"

    asyncio.run(run())
    sync.assert_awaited_once_with("team-test")
    generate.assert_awaited_once_with("team-test")
    assert "强调模型局限" in team.context_for("team-test", "WriterAgent")


def test_stop_bypasses_pending_planner_and_withdraws_old_action(project, monkeypatch):
    root, _ = project
    stop = AsyncMock(
        return_value=modeling_router.CancelTaskResponse(success=True, message="已停止")
    )
    monkeypatch.setattr(modeling_router, "cancel_task", stop)

    async def run():
        planning = asyncio.Event()
        release = asyncio.Event()

        async def planner(_id, body, _root):
            if body.action == "stop":
                return router.Plan(action="stop")
            planning.set()
            await release.wait()
            return router.Plan(action="instruct", instruction="过时要求")

        monkeypatch.setattr(router, "make_plan", planner)
        first = asyncio.create_task(
            router._process(
                "team-test",
                router.ChatRequest(request_id="slow-123", content="帮我调整"),
                root,
            )
        )
        await planning.wait()
        await asyncio.wait_for(
            router._process(
                "team-test",
                router.ChatRequest(
                    request_id="stop-123", content="停止建模", action="stop"
                ),
                root,
            ),
            1,
        )
        release.set()
        await first

    asyncio.run(run())
    stop.assert_awaited_once()
    assert not team.snapshot(root)["directives"]
    assert any("动作已撤回" in item["content"] for item in team.events(root))


def test_sse_reconnect_resumes_after_last_delivered_event(project, monkeypatch):
    root, _ = project
    monkeypatch.setattr(router, "import_history", AsyncMock())
    team.record(root, "coder", "tool", "首次执行")
    cursor = team.events(root)[-1]["seq"]
    team.record(root, "writer", "writing", "新章节")

    class Request:
        headers = {"last-event-id": str(cursor)}
        is_disconnected = AsyncMock(return_value=False)

    async def run():
        response = await router.stream("team-test", Request(), 0)
        item = await anext(response.body_iterator)
        await response.body_iterator.aclose()
        payload = json.loads(item.split("data: ", 1)[1])
        assert [event["content"] for event in payload["events"]] == ["新章节"]
        assert payload["state"]["task_id"] == "team-test"

    asyncio.run(run())


def test_invalid_planner_output_is_recorded_without_dispatch(project, monkeypatch):
    root, _ = project
    monkeypatch.setattr(
        router, "make_plan", AsyncMock(side_effect=ValueError("无法解析调度结果"))
    )
    asyncio.run(
        router._process(
            "team-test",
            router.ChatRequest(request_id="invalid-1", content="做点事情"),
            root,
        )
    )
    assert not team.snapshot(root)["directives"]
    assert team.events(root)[-1]["kind"] == "error"


def test_chat_call_scope_does_not_inherit_active_solver_or_leak_to_dispatch(
    project, monkeypatch
):
    from app.services.call_ledger import context

    root, checkpoint = project
    state = checkpoint.load()
    state.update(status="running", current_node="solve:ques1", execution_id="a" * 32)
    checkpoint.save(state)
    observed = []

    async def plan(*args):
        observed.append(context("team-test", agent_name="TeamCoordinator"))
        return router.Plan(action="reply", reply="计算尚未完成")

    async def dispatch(*args):
        observed.append(context("team-test", agent_name="Coder"))
        return {"message": "计算尚未完成"}

    monkeypatch.setattr(router, "make_plan", plan)
    monkeypatch.setattr(router, "execute_plan", dispatch)
    asyncio.run(
        router._process(
            "team-test",
            router.ChatRequest(request_id="chat-scope", content="进度如何"),
            root,
        )
    )
    assert observed[0]["stage_id"] == "coordinator"
    assert observed[0]["question_id"] is None
    assert len(observed[0]["run_id"]) == 32 and observed[0]["run_id"] != "a" * 32
    assert observed[1]["stage_id"] == "solve:ques1"
    assert observed[1]["question_id"] == "ques1"
    assert observed[1]["run_id"] == "a" * 32


@pytest.mark.parametrize(
    "role,name",
    [("all", "团团"), ("modeler", "灵灵"), ("coder", "点点"), ("writer", "墨墨")],
)
@pytest.mark.parametrize(
    "conversation_only,status", [(False, "running"), (True, "completed")]
)
def test_progress_question_returns_model_answer_without_workflow_actions(
    project, monkeypatch, conversation_only, status, role, name
):
    from app.routers import common_router

    root, checkpoint = project
    state = checkpoint.load()
    state["status"] = status
    checkpoint.save(state)
    before = (root / "workflow_state.json").read_bytes()
    chat = AsyncMock(
        return_value=StandardResponse(content="目前还在计算第一问，还没有完成验证。")
    )
    monkeypatch.setattr(
        router,
        "LLMFactory",
        lambda _: SimpleNamespace(
            get_modeling_llms=lambda: (SimpleNamespace(chat=chat), None, None)
        ),
    )
    evidence = {"workflow": {"status": status, "current_node": "solve:ques1"}}
    monkeypatch.setattr(
        common_router, "_build_task_copilot_context", AsyncMock(return_value=evidence)
    )
    cancel = AsyncMock()
    resume = AsyncMock()
    monkeypatch.setattr(modeling_router, "cancel_task", cancel)
    monkeypatch.setattr(modeling_router, "resume_task", resume)
    asyncio.run(
        router._process(
            "team-test",
            router.ChatRequest(
                request_id="progress-question",
                content="现在到底进行得怎么样了",
                conversation_only=conversation_only,
                role=role,
            ),
            root,
        )
    )
    assert team.events(root)[-1]["kind"] == "reply"
    assert "还没有完成验证" in team.events(root)[-1]["content"]
    cancel.assert_not_awaited()
    resume.assert_not_awaited()
    assert not team.snapshot(root)["directives"]
    assert (root / "workflow_state.json").read_bytes() == before
    context = json.loads(chat.call_args.kwargs["history"][1]["content"])
    assert context["evidence"] == evidence
    assert context["latest_user_request"] == "现在到底进行得怎么样了"
    assert team.events(root)[-1]["role"] == ("coordinator" if role == "all" else role)
    assert name in chat.call_args.kwargs["history"][0]["content"]


def _stub_coordinator(monkeypatch, chat):
    from app.routers import common_router

    monkeypatch.setattr(
        router,
        "LLMFactory",
        lambda _: SimpleNamespace(
            get_modeling_llms=lambda: (SimpleNamespace(chat=chat), None, None)
        ),
    )
    monkeypatch.setattr(
        common_router, "_build_task_copilot_context", AsyncMock(return_value={})
    )


def test_planner_json_embedded_in_prose_is_recovered(project, monkeypatch):
    root, checkpoint = project
    state = checkpoint.load()
    state["status"] = "completed"
    checkpoint.save(state)
    chat = AsyncMock(
        return_value=StandardResponse(
            content='好的，我来说明一下。{"action": "reply", "reply": "目前完成了问题一的计算，问题二还在排队。"} 希望对你有帮助。'
        )
    )
    _stub_coordinator(monkeypatch, chat)

    plan = asyncio.run(
        router.make_plan(
            "team-test",
            router.ChatRequest(request_id="prose-json", content="目前的结果是什么"),
            root,
        )
    )

    assert plan.action == "reply"
    assert "问题一" in plan.reply


def test_broken_planner_json_falls_back_to_conversational_answer(project, monkeypatch):
    root, checkpoint = project
    state = checkpoint.load()
    state["status"] = "completed"
    checkpoint.save(state)
    chat = AsyncMock(
        side_effect=[
            StandardResponse(
                content='{"action": "revise", "instruction": "重做第二问，结果被截断'
            ),
            StandardResponse(content="问题二已完成计算，23 个架次，能耗较基线降 48%。"),
        ]
    )
    _stub_coordinator(monkeypatch, chat)

    plan = asyncio.run(
        router.make_plan(
            "team-test",
            router.ChatRequest(
                request_id="broken-json", content="每一问的指标结果是什么"
            ),
            root,
        )
    )

    assert plan.action == "reply"
    assert "48%" in plan.reply
    assert "没能读懂" not in plan.reply
    assert chat.await_count == 2


def test_conversation_empty_reply_retries_then_static_fallback(project, monkeypatch):
    root, _ = project
    chat = AsyncMock(return_value=StandardResponse(content=""))
    _stub_coordinator(monkeypatch, chat)

    plan = asyncio.run(
        router.make_plan(
            "team-test",
            router.ChatRequest(
                request_id="empty-reply", content="进展如何", conversation_only=True
            ),
            root,
        )
    )

    assert plan.action == "reply"
    assert "换个说法" in plan.reply
    assert chat.await_count == 2


@pytest.mark.parametrize("status", ["interrupted", "completed", "running"])
def test_resume_button_routes_to_writer_without_duplicate_generation(
    project, monkeypatch, status
):
    root, checkpoint = project
    checkpoint.mark_status("completed")
    writing_router._set_generation(paper.ensure_workspace(root), status)
    sync, generate, resume = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(writing_router, "sync", sync)
    monkeypatch.setattr(writing_router, "generate", generate)
    monkeypatch.setattr(modeling_router, "resume_task", resume)
    if status == "running":
        monkeypatch.setitem(writing_router._generations, "team-test", object())
    body = router.ChatRequest(
        request_id="continue-button", content="继续", action="resume"
    )
    asyncio.run(
        router.execute_plan(
            "team-test", body, router.Plan(action="resume"), root, BackgroundTasks()
        )
    )
    assert generate.await_count == (1 if status == "interrupted" else 0)
    resume.assert_not_awaited()


@pytest.mark.parametrize("status", ["interrupted", "failed", "cancelled", "idle"])
def test_continue_after_modeling_resumes_writer_without_model_call(
    project, monkeypatch, status
):
    root, checkpoint = project
    checkpoint.mark_status("completed")
    writing_router._set_generation(paper.ensure_workspace(root), status)
    sync, generate, resume = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(writing_router, "sync", sync)
    monkeypatch.setattr(writing_router, "generate", generate)
    monkeypatch.setattr(modeling_router, "resume_task", resume)
    body = router.ChatRequest(request_id="continue-paper", content="继续")

    async def run():
        plan = await router.make_plan("team-test", body, root)
        assert plan.action == "write"
        await router.execute_plan("team-test", body, plan, root, BackgroundTasks())

    asyncio.run(run())
    generate.assert_awaited_once_with("team-test")
    resume.assert_not_awaited()


def test_paper_resume_cannot_bypass_pending_approval(project, monkeypatch):
    root, checkpoint = project
    state = checkpoint.load()
    checkpoint.complete_node(state, "review_results")
    checkpoint.request_approval(state, "review_results", summary="等待验收")
    generate = AsyncMock()
    monkeypatch.setattr(writing_router, "generate", generate)
    body = router.ChatRequest(request_id="continue-gate", content="继续")
    with pytest.raises(HTTPException):
        asyncio.run(
            router.execute_plan(
                "team-test", body, router.Plan(action="resume"), root, BackgroundTasks()
            )
        )
    generate.assert_not_awaited()
    assert checkpoint.load()["pending_approval"]
