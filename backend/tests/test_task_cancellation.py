"""Regression tests for immediate and durable task cancellation."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core.workflow import RemitWorkFlow
from app.routers.modeling_router import _active_tasks, cancel_task
from app.schemas.response import SystemMessage, UserMessage
from app.services.redis_manager import RedisManager


class TaskCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        for task, _ in list(_active_tasks.values()):
            task.cancel()
        _active_tasks.clear()
        await asyncio.sleep(0)

    async def test_active_task_is_cancelled_immediately(self) -> None:
        task_id = "cancel-active-task"
        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        cancel_event = asyncio.Event()
        _active_tasks[task_id] = (task, cancel_event)

        with patch(
            "app.routers.modeling_router.redis_manager.request_cancellation",
            new=AsyncMock(),
        ):
            response = await cancel_task(task_id)
        await asyncio.sleep(0)

        self.assertTrue(response.success)
        self.assertTrue(cancel_event.is_set())
        self.assertTrue(task.cancelled())

    async def test_stale_running_task_is_marked_stopped(self) -> None:
        task_id = "cancel-stale-task"
        messages = [
            UserMessage(content="赛题").model_dump(mode="json"),
            SystemMessage(content="任务开始处理").model_dump(mode="json"),
        ]
        with (
            patch(
                "app.routers.modeling_router.redis_manager.request_cancellation",
                new=AsyncMock(),
            ),
            patch(
                "app.routers.modeling_router.redis_manager.load_task_messages",
                new=AsyncMock(return_value=messages),
            ),
            patch(
                "app.routers.modeling_router.redis_manager.publish_message",
                new=AsyncMock(),
            ) as publish,
        ):
            response = await cancel_task(task_id)

        self.assertTrue(response.success)
        published = publish.await_args.args[1]
        self.assertEqual(published.type, "warning")
        self.assertIn("停止", published.content)

    async def test_workflow_cleanup_is_idempotent(self) -> None:
        workflow = RemitWorkFlow()
        interpreter = AsyncMock()
        workflow.code_interpreter = interpreter

        await workflow.cleanup()
        await workflow.cleanup()

        interpreter.cleanup.assert_awaited_once()

    async def test_restart_reconciles_nonterminal_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RedisManager(messages_dir=Path(tmp))
            task_id = "interrupted-by-restart"
            await manager._save_message_to_file(
                task_id,
                UserMessage(content="赛题"),
            )
            await manager._save_message_to_file(
                task_id,
                SystemMessage(content="任务开始处理"),
            )

            count = await manager.reconcile_interrupted_tasks()
            messages = await manager.load_task_messages(task_id)

            self.assertEqual(count, 1)
            self.assertEqual(messages[-1]["type"], "warning")
            self.assertIn("服务重启", messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
