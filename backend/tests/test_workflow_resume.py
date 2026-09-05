"""节点级工作流检查点和续跑接口回归测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException

from app.core.workflow import RemitWorkFlow
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.routers.modeling_router import (
    ResumeTaskRequest,
    _active_tasks,
    _scheduled_tasks,
    get_resume_options,
    resume_task,
)
from app.schemas.request import Problem
from app.schemas.response import SystemMessage, UserMessage
from app.services.redis_manager import RedisManager


class WorkflowResumeTests(unittest.IsolatedAsyncioTestCase):
    """验证检查点只暴露具备完整前置成果的节点。"""

    def setUp(self) -> None:
        _active_tasks.clear()
        _scheduled_tasks.clear()

    def tearDown(self) -> None:
        _active_tasks.clear()
        _scheduled_tasks.clear()

    @staticmethod
    def _problem(task_id: str = "resume-task") -> Problem:
        return Problem(task_id=task_id, ques_all="包含三个小问的国赛题")

    @staticmethod
    def _complete_planning(checkpoint: WorkflowCheckpoint, state: dict) -> None:
        questions = {
            "background": "题目背景",
            "ques1": "建立预测模型",
            "ques2": "优化决策方案",
            "ques3": "评价方案稳定性",
        }
        state["questions"] = questions
        state["ques_count"] = 3
        state["coordinator_response"] = {
            "questions": questions,
            "ques_count": 3,
            "user_requirements": "",
        }
        checkpoint.complete_node(state, "coordinator")
        checkpoint.complete_node(state, "research")
        state["analysis_response"] = state["coordinator_response"]
        checkpoint.complete_node(state, "analysis")
        state["modeler_response"] = {
            "questions_solution": {
                "ques1": "预测",
                "ques2": "优化",
                "ques3": "评价",
            }
        }
        checkpoint.complete_node(state, "modeler")

    def test_interrupted_node_is_latest_safe_resume_option(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            self._complete_planning(checkpoint, state)
            checkpoint.complete_node(state, "solve:eda")
            checkpoint.complete_node(state, "pilot")
            checkpoint.complete_node(state, "solve:ques1")
            checkpoint.start_node(state, "solve:ques2")
            checkpoint.mark_status("stopped")
            state = checkpoint.load()

            nodes = checkpoint.resume_nodes(state)

            self.assertEqual(nodes[-1]["node_id"], "solve:ques2")
            self.assertEqual(nodes[-1]["status"], "interrupted")
            self.assertNotIn("solve:ques3", {node["node_id"] for node in nodes})

    def test_project_execution_backend_survives_checkpoint_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            checkpoint.initialize(
                Problem(
                    task_id="python-project",
                    ques_all="建立预测模型",
                    execution_backend="python",
                )
            )

            restored = Problem.model_validate(checkpoint.load()["problem"])

            self.assertEqual(restored.execution_backend, "python")

    async def test_interpreter_uses_backend_saved_in_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(
                Problem(
                    task_id="python-project",
                    ques_all="建立预测模型",
                    execution_backend="python",
                )
            )
            workflow = RemitWorkFlow()
            workflow.task_id = "python-project"
            workflow.work_dir = tmp
            workflow.checkpoint = checkpoint
            interpreter = SimpleNamespace(language="python", backend_name="本地 Python")

            with (
                patch(
                    "app.core.workflow.create_interpreter",
                    new=AsyncMock(return_value=interpreter),
                ) as create,
                patch(
                    "app.core.workflow.redis_manager.publish_message",
                    new=AsyncMock(),
                ),
            ):
                await workflow._initialize_interpreter(state)

            self.assertEqual(create.await_args.kwargs["preferred_backend"], "python")

    def test_resuming_earlier_node_invalidates_downstream_without_deleting_inputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.csv").write_text("source", encoding="utf-8")
            checkpoint = WorkflowCheckpoint(root)
            state = checkpoint.initialize(self._problem())
            self._complete_planning(checkpoint, state)

            (root / "clean.csv").write_text("clean", encoding="utf-8")
            state["solution_results"]["eda"] = {
                "writer_response": {"response_content": "EDA"},
                "artifacts": ["clean.csv"],
            }
            checkpoint.complete_node(state, "solve:eda")
            checkpoint.complete_node(state, "pilot")

            (root / "q1.png").write_bytes(b"q1")
            state["solution_results"]["ques1"] = {
                "writer_response": {"response_content": "Q1"},
                "artifacts": ["clean.csv", "q1.png"],
            }
            checkpoint.complete_node(state, "solve:ques1")

            (root / "q2.png").write_bytes(b"q2")
            (root / "ques2_quality_report.json").write_text(
                '{"artifacts":["q2.png"],"paper_ready_images":[]}',
                encoding="utf-8",
            )
            checkpoint.start_node(state, "solve:ques2")
            checkpoint.mark_status("stopped")

            resumed = checkpoint.prepare_resume(checkpoint.load(), "solve:ques1")

            self.assertTrue((root / "input.csv").is_file())
            self.assertTrue((root / "clean.csv").is_file())
            self.assertFalse((root / "q1.png").exists())
            self.assertFalse((root / "q2.png").exists())
            self.assertEqual(set(resumed["solution_results"]), {"eda"})
            self.assertEqual(resumed["current_node"], "solve:ques1")

    def test_modeler_resume_invalidates_stale_council_but_preserves_fable_budget(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            self._complete_planning(checkpoint, state)
            state["modeler_primary_response"] = {"questions_solution": {}}
            state["model_scout_proposal"] = {"questions": {}}
            state["model_council"] = {"critic_model": "claude-fable-5"}
            checkpoint.save(state)

            resumed = checkpoint.prepare_resume(checkpoint.load(), "modeler")

            self.assertNotIn("modeler_primary_response", resumed)
            self.assertNotIn("model_scout_proposal", resumed)
            self.assertNotIn("model_council", resumed)
            self.assertEqual(resumed["fable_critic_calls_used"], 1)

    def test_revising_upstream_removes_stale_solver_evidence(self) -> None:
        for target in ("coordinator", "research", "analysis", "modeler", "pilot"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                root.joinpath("input.csv").write_text("original input")
                checkpoint = WorkflowCheckpoint(root)
                state = checkpoint.initialize(self._problem())
                self._complete_planning(checkpoint, state)
                for node in ("solve:eda", "pilot", "solve:ques1"):
                    checkpoint.complete_node(state, node)
                for key in ("eda", "ques1"):
                    artifact = f"{key}_evidence.csv"
                    root.joinpath(artifact).write_text("old evidence")
                    root.joinpath(f"{key}_quality_report.json").write_text(
                        json.dumps({"artifacts": [artifact, "input.csv"]})
                    )
                    state["solution_results"][key] = {
                        "artifacts": [artifact, "input.csv"]
                    }
                pending = checkpoint.request_approval(
                    state, "solve:ques1", summary="review result"
                )

                checkpoint.request_revision(
                    checkpoint.load(), pending["checkpoint_id"],
                    "Use a different model and rerun the evidence", target,
                )

                self.assertTrue(root.joinpath("input.csv").is_file())
                self.assertFalse(root.joinpath("ques1_evidence.csv").exists())
                self.assertFalse(root.joinpath("ques1_quality_report.json").exists())
                self.assertEqual(
                    root.joinpath("eda_evidence.csv").exists(), target == "pilot"
                )
                self.assertEqual(
                    root.joinpath("eda_quality_report.json").exists(), target == "pilot"
                )

    def test_legacy_manual_review_artifacts_survive_resume_for_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = checkpoint.initialize(self._problem())
            self._complete_planning(checkpoint, state)
            root.joinpath("eda.csv").write_text("x\n1\n", encoding="utf-8")
            root.joinpath("eda_quality_report.json").write_text(
                '{"status":"manual_review","artifacts":["eda.csv"]}',
                encoding="utf-8",
            )
            checkpoint.start_node(state, "solve:eda")
            checkpoint.mark_status("failed")

            resumed = checkpoint.prepare_resume(checkpoint.load(), "solve:eda")

            self.assertTrue(root.joinpath("eda.csv").is_file())
            self.assertTrue(root.joinpath("eda_quality_report.json").is_file())
            self.assertEqual(resumed["current_node"], "solve:eda")

    def test_current_interrupted_passing_artifacts_survive_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = checkpoint.initialize(self._problem())
            self._complete_planning(checkpoint, state)
            checkpoint.complete_node(state, "solve:eda")
            checkpoint.complete_node(state, "pilot")
            root.joinpath("ques1_predictions.csv").write_text(
                "sample_id,actual,predicted\n1,1,1\n",
                encoding="utf-8",
            )
            root.joinpath("ques1_prediction_metrics.json").write_text(
                '{"primary_metric":{"name":"rmse","model_value":0.1}}',
                encoding="utf-8",
            )
            root.joinpath("ques1_evidence.csv").write_text(
                "metric,value\nrmse,0.1\n",
                encoding="utf-8",
            )
            root.joinpath("ques1_quality_report.json").write_text(
                (
                    '{"status":"pass","artifacts":["ques1_evidence.csv"],'
                    '"paper_ready_images":[]}'
                ),
                encoding="utf-8",
            )
            checkpoint.start_node(state, "solve:ques1")
            checkpoint.mark_status("failed")

            resumed = checkpoint.prepare_resume(checkpoint.load(), "solve:ques1")

            self.assertTrue(root.joinpath("ques1_quality_report.json").is_file())
            self.assertTrue(root.joinpath("ques1_predictions.csv").is_file())
            self.assertTrue(root.joinpath("ques1_prediction_metrics.json").is_file())
            self.assertTrue(root.joinpath("ques1_evidence.csv").is_file())
            self.assertEqual(resumed["current_node"], "solve:ques1")

    def test_resume_restores_plan_when_review_revision_never_materialized(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = checkpoint.initialize(self._problem())
            self._complete_planning(checkpoint, state)
            checkpoint.complete_node(state, "solve:eda")
            checkpoint.complete_node(state, "pilot")
            original_plan = "使用稳健线性关系模型"
            revised_plan = "改用GEE重新建模"
            state["modeler_response"]["questions_solution"]["ques1"] = revised_plan
            state["model_revision_history"] = {
                "ques1": [
                    {
                        "trigger": "modeler_review",
                        "previous_plan": original_plan,
                        "revision_plan": {
                            "selected_model": "new_gee_model",
                            "revised_strategy": revised_plan,
                        },
                    }
                ]
            }
            root.joinpath("ques1_quality_report.json").write_text(
                (
                    '{"status":"pass",'
                    '"selected_model":"existing_robust_linear_model",'
                    '"artifacts":[],"paper_ready_images":[]}'
                ),
                encoding="utf-8",
            )
            checkpoint.start_node(state, "solve:ques1")
            checkpoint.mark_status("failed")

            resumed = checkpoint.prepare_resume(checkpoint.load(), "solve:ques1")

            self.assertEqual(
                resumed["modeler_response"]["questions_solution"]["ques1"],
                original_plan,
            )
            self.assertNotIn("ques1", resumed["model_revision_history"])

    async def test_resume_start_message_overrides_old_stopped_status(self) -> None:
        manager = RedisManager(messages_dir=Path(tempfile.mkdtemp()))
        messages = [
            UserMessage(content="赛题").model_dump(mode="json"),
            SystemMessage(content="任务已停止", type="warning").model_dump(mode="json"),
            SystemMessage(content="任务从节点 solve:ques1 继续处理").model_dump(
                mode="json"
            ),
        ]

        self.assertEqual(manager.task_status_from_messages(messages), "running")
        messages.append(
            SystemMessage(content="任务处理完成", type="success").model_dump(
                mode="json"
            )
        )
        self.assertEqual(manager.task_status_from_messages(messages), "completed")

    async def test_resume_endpoint_reserves_task_before_background_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            checkpoint.initialize(self._problem())
            checkpoint.mark_status("stopped")
            background_tasks = BackgroundTasks()

            with (
                patch(
                    "app.routers.modeling_router._load_workflow_checkpoint",
                    return_value=(checkpoint, checkpoint.load()),
                ),
                patch(
                    "app.routers.modeling_router.redis_manager.clear_cancellation_request",
                    new=AsyncMock(),
                ) as clear_request,
            ):
                response = await resume_task(
                    "resume-task",
                    ResumeTaskRequest(node_id="coordinator"),
                    background_tasks,
                )

            self.assertTrue(response.success)
            self.assertIn("resume-task", _scheduled_tasks)
            self.assertEqual(len(background_tasks.tasks), 1)
            clear_request.assert_awaited_once_with("resume-task")

            with self.assertRaises(HTTPException) as raised:
                await resume_task(
                    "resume-task",
                    ResumeTaskRequest(node_id="coordinator"),
                    BackgroundTasks(),
                )
            self.assertEqual(raised.exception.status_code, 409)

    async def test_resume_options_endpoint_exposes_interrupted_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            checkpoint.start_node(state, "coordinator")
            checkpoint.mark_status("stopped")
            stopped_messages = [
                SystemMessage(content="任务已停止", type="warning").model_dump(
                    mode="json"
                )
            ]

            with (
                patch(
                    "app.routers.modeling_router._load_workflow_checkpoint",
                    return_value=(checkpoint, checkpoint.load()),
                ),
                patch(
                    "app.routers.modeling_router.redis_manager.load_task_messages",
                    new=AsyncMock(return_value=stopped_messages),
                ),
            ):
                response = await get_resume_options("resume-task")

            self.assertTrue(response.resumable)
            self.assertEqual(response.nodes[-1].node_id, "coordinator")
            self.assertEqual(response.nodes[-1].status, "interrupted")

    async def test_failed_task_can_resume_from_interrupted_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            checkpoint.start_node(state, "coordinator")
            checkpoint.mark_status("failed")
            failed_messages = [
                SystemMessage(content="任务执行失败", type="error").model_dump(
                    mode="json"
                )
            ]

            with (
                patch(
                    "app.routers.modeling_router._load_workflow_checkpoint",
                    return_value=(checkpoint, checkpoint.load()),
                ),
                patch(
                    "app.routers.modeling_router.redis_manager.load_task_messages",
                    new=AsyncMock(return_value=failed_messages),
                ),
                patch(
                    "app.routers.modeling_router.redis_manager.clear_cancellation_request",
                    new=AsyncMock(),
                ),
            ):
                options = await get_resume_options("resume-task")
                response = await resume_task(
                    "resume-task",
                    ResumeTaskRequest(node_id="coordinator"),
                    BackgroundTasks(),
                )

            self.assertTrue(options.resumable)
            self.assertEqual(options.status, "failed")
            self.assertTrue(response.success)
