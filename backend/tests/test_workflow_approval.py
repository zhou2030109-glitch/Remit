"""逐节点人工审核闸门回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException

from app.core.workflow import RemitWorkFlow, WorkflowApprovalRequired
from app.core.workflow_checkpoint import (
    WorkflowCheckpoint,
    WorkflowCheckpointError,
)
from app.routers.modeling_router import (
    SubmitApprovalRequest,
    _active_tasks,
    _scheduled_tasks,
    get_pending_approval,
    submit_approval,
)
from app.schemas.request import Problem
from app.schemas.response import ApprovalMessage, SystemMessage, UserMessage
from app.services.redis_manager import RedisManager


class WorkflowApprovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _active_tasks.clear()
        _scheduled_tasks.clear()

    def tearDown(self) -> None:
        _active_tasks.clear()
        _scheduled_tasks.clear()

    @staticmethod
    def _problem(task_id: str = "approval-task") -> Problem:
        return Problem(task_id=task_id, ques_all="国赛题：建立并验证预测模型")

    @staticmethod
    def _completed_node(
        checkpoint: WorkflowCheckpoint, state: dict, node_id: str = "coordinator"
    ) -> None:
        if node_id == "coordinator":
            state["questions"] = {"ques1": "建立预测模型"}
            state["ques_count"] = 1
            state["coordinator_response"] = {
                "questions": state["questions"],
                "ques_count": 1,
                "user_requirements": "",
            }
        checkpoint.complete_node(state, node_id)
        if node_id == "coordinator":
            # 新工作流在 coordinator 后先完成调研和数据校正版题意
            checkpoint.complete_node(state, "research")
            state["analysis_response"] = state["coordinator_response"]
            checkpoint.complete_node(state, "analysis")

    async def test_workflow_pause_is_persisted_and_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            self._completed_node(checkpoint, state)
            workflow = RemitWorkFlow()
            workflow.checkpoint = checkpoint

            with self.assertRaises(WorkflowApprovalRequired) as raised:
                await workflow._require_human_approval(
                    state,
                    "coordinator",
                    summary="已拆分 1 个小问",
                    artifacts=["plan.json"],
                )

            reloaded = checkpoint.load()
            self.assertEqual(reloaded["status"], "awaiting_approval")
            self.assertEqual(
                reloaded["pending_approval"]["checkpoint_id"],
                raised.exception.approval["checkpoint_id"],
            )
            self.assertEqual(reloaded["current_node"], "coordinator")

    async def test_approval_unlocks_next_node_but_stale_request_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            self._completed_node(checkpoint, state)
            pending = checkpoint.request_approval(
                state, "coordinator", summary="已完成拆题"
            )

            approved = checkpoint.approve(state, pending["checkpoint_id"])

            self.assertEqual(approved["status"], "running")
            self.assertIsNone(approved["pending_approval"])
            self.assertIn("coordinator", approved["completed_nodes"])
            self.assertEqual(
                approved["approval_history"][-1]["decision"], "approve"
            )
            with self.assertRaises(WorkflowCheckpointError):
                checkpoint.approve(approved, pending["checkpoint_id"])

    async def test_revision_invalidates_reviewed_node_and_persists_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            self._completed_node(checkpoint, state)
            state["modeler_response"] = {"questions_solution": {"ques1": "回归"}}
            checkpoint.complete_node(state, "modeler")
            pending = checkpoint.request_approval(
                state, "modeler", summary="总体方案已完成"
            )

            revised = checkpoint.request_revision(
                state,
                pending["checkpoint_id"],
                "存在数据泄漏，必须按主体分组验证。",
            )

            self.assertIn("coordinator", revised["completed_nodes"])
            self.assertNotIn("modeler", revised["completed_nodes"])
            self.assertIsNone(revised["modeler_response"])
            self.assertEqual(revised["current_node"], "modeler")
            self.assertEqual(
                checkpoint.consume_revision_feedback(revised, "modeler"),
                "存在数据泄漏，必须按主体分组验证。",
            )
            self.assertEqual(revised["revision_counts"]["modeler"], 1)

    async def test_reviewer_can_return_to_an_earlier_approved_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            self._completed_node(checkpoint, state)
            first_gate = checkpoint.request_approval(
                state, "coordinator", summary="拆题完成"
            )
            state = checkpoint.approve(state, first_gate["checkpoint_id"])
            state["modeler_response"] = {"questions_solution": {"ques1": "回归"}}
            checkpoint.complete_node(state, "modeler")
            pending = checkpoint.request_approval(
                state, "modeler", summary="总体方案完成"
            )

            revised = checkpoint.request_revision(
                state,
                pending["checkpoint_id"],
                "小问拆分遗漏了约束条件，请从拆题开始重做。",
                target_node_id="coordinator",
            )

            self.assertNotIn("coordinator", revised["completed_nodes"])
            self.assertNotIn("coordinator", revised["approved_nodes"])
            self.assertIsNone(revised["coordinator_response"])
            self.assertEqual(revised["current_node"], "coordinator")
            self.assertEqual(
                checkpoint.consume_revision_feedback(revised, "coordinator"),
                "小问拆分遗漏了约束条件，请从拆题开始重做。",
            )

    async def test_get_pending_approval_uses_checkpoint_not_message_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            self._completed_node(checkpoint, state)
            checkpoint.request_approval(state, "coordinator", summary="等待验收")

            with patch(
                "app.routers.modeling_router._load_workflow_checkpoint",
                return_value=(checkpoint, checkpoint.load()),
            ):
                response = await get_pending_approval("approval-task")

            self.assertEqual(response.status, "awaiting_approval")
            self.assertIsNotNone(response.pending)
            assert response.pending is not None
            self.assertEqual(response.pending.node_id, "coordinator")

    async def test_submit_approval_reserves_continuation_before_returning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            self._completed_node(checkpoint, state)
            pending = checkpoint.request_approval(
                state, "coordinator", summary="等待验收"
            )
            background_tasks = BackgroundTasks()

            with (
                patch(
                    "app.routers.modeling_router._load_workflow_checkpoint",
                    return_value=(checkpoint, checkpoint.load()),
                ),
                patch(
                    "app.routers.modeling_router.redis_manager.clear_cancellation_request",
                    new=AsyncMock(),
                ),
                patch(
                    "app.routers.modeling_router.redis_manager.publish_message",
                    new=AsyncMock(),
                ),
            ):
                response = await submit_approval(
                    "approval-task",
                    SubmitApprovalRequest(
                        checkpoint_id=pending["checkpoint_id"],
                        decision="approve",
                    ),
                    background_tasks,
                )

            self.assertTrue(response.success)
            self.assertIn("approval-task", _scheduled_tasks)
            self.assertEqual(len(background_tasks.tasks), 1)
            self.assertIsNone(checkpoint.load()["pending_approval"])

    async def test_revision_endpoint_requires_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(self._problem())
            self._completed_node(checkpoint, state)
            pending = checkpoint.request_approval(
                state, "coordinator", summary="等待验收"
            )

            with patch(
                "app.routers.modeling_router._load_workflow_checkpoint",
                return_value=(checkpoint, checkpoint.load()),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await submit_approval(
                        "approval-task",
                        SubmitApprovalRequest(
                            checkpoint_id=pending["checkpoint_id"],
                            decision="revise",
                            feedback="   ",
                        ),
                        BackgroundTasks(),
                    )
            self.assertEqual(raised.exception.status_code, 422)

    async def test_message_status_distinguishes_waiting_from_running(self) -> None:
        manager = RedisManager(messages_dir=Path(tempfile.mkdtemp()))
        messages = [
            UserMessage(content="赛题").model_dump(mode="json"),
            SystemMessage(content="任务开始处理").model_dump(mode="json"),
            ApprovalMessage(
                checkpoint_id="gate-1",
                node_id="coordinator",
                node_label="题意识别与问题拆解",
                summary="已完成",
            ).model_dump(mode="json"),
        ]
        self.assertEqual(
            manager.task_status_from_messages(messages), "awaiting_approval"
        )


if __name__ == "__main__":
    unittest.main()
