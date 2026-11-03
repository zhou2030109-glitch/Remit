"""任务接收与取消在任务总线故障下的生命周期边界。"""

from __future__ import annotations

import asyncio
from contextlib import chdir
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import BackgroundTasks, HTTPException, UploadFile

from app.routers import common_router
from app.routers import modeling_router as router
from app.schemas.enums import CompTemplate, FormatOutPut
from app.schemas.response import UserMessage


class IntakeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_deletion_rejects_locally_registered_task_with_failed_history(
        self,
    ) -> None:
        task_id = "failed-but-resuming"
        messages = [{"msg_type": "system", "type": "error", "task_status": "failed"}]
        summaries = [
            {"task_id": "already-completed", "status": "completed"},
            {"task_id": task_id, "status": "failed"},
        ]
        for registered in ("scheduled", "active"):
            with self.subTest(registered=registered):
                worker = None
                if registered == "scheduled":
                    router._scheduled_tasks.add(task_id)
                else:
                    worker = asyncio.create_task(asyncio.Event().wait())
                    router._active_tasks[task_id] = (worker, asyncio.Event())
                try:
                    with (
                        patch.object(
                            common_router.task_bus,
                            "load_task_messages",
                            new=AsyncMock(return_value=messages),
                        ),
                        patch.object(
                            common_router.task_bus,
                            "list_task_summaries",
                            new=AsyncMock(return_value=summaries),
                        ),
                        patch.object(
                            common_router.task_bus,
                            "delete_task_record",
                            new=AsyncMock(),
                        ) as delete_record,
                        patch.object(
                            common_router, "_delete_task_work_dir"
                        ) as delete_workspace,
                    ):
                        with self.assertRaises(HTTPException) as single:
                            await common_router.delete_task(task_id)
                        self.assertEqual(single.exception.status_code, 409)
                        with self.assertRaises(HTTPException) as all_tasks:
                            await common_router.clear_task_history()
                        self.assertEqual(all_tasks.exception.status_code, 409)
                        delete_record.assert_not_awaited()
                        delete_workspace.assert_not_called()
                finally:
                    router._scheduled_tasks.discard(task_id)
                    router._active_tasks.pop(task_id, None)
                    if worker is not None:
                        worker.cancel()
                        await asyncio.gather(worker, return_exceptions=True)

    async def test_delayed_retry_does_not_recreate_deleted_workspace(self) -> None:
        task_id = "deleted-before-retry"
        with (
            patch.object(
                router, "get_work_dir", side_effect=FileNotFoundError(task_id)
            ),
            patch.object(router, "create_work_dir") as create_workspace,
            patch.object(router, "WorkflowCheckpoint") as checkpoint,
            patch.object(router, "run_modeling_task_async", new=AsyncMock()) as run,
            patch.object(
                router.task_bus, "is_cancellation_requested", new=AsyncMock()
            ) as lookup,
            patch.object(
                router.task_bus, "publish_message", new=AsyncMock()
            ) as publish,
        ):
            await router._auto_resume_after_failure(
                task_id, "test question", CompTemplate.CHINA, FormatOutPut.LaTeX, "", 0
            )
        create_workspace.assert_not_called()
        checkpoint.assert_not_called()
        run.assert_not_awaited()
        lookup.assert_not_awaited()
        publish.assert_not_awaited()
        self.assertNotIn(task_id, router._scheduled_tasks)
        self.assertNotIn(task_id, router._active_tasks)

    async def asyncTearDown(self) -> None:
        tasks = [task for task, _ in router._active_tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        router._active_tasks.clear()
        router._scheduled_tasks.clear()
        router._pending_cancellations.clear()

    async def test_initial_redis_lookup_failure_releases_running_child(self) -> None:
        task_id = "lookup-failure"
        child_stopped = asyncio.Event()
        child_tasks: list[asyncio.Task] = []

        async def execute(*_args, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                child_stopped.set()

        async def lookup(_task_id):
            child_tasks.append(router._active_tasks[task_id][0])
            await asyncio.sleep(0)
            raise ConnectionError("Redis offline")

        workflow = MagicMock()
        workflow.execute = AsyncMock(side_effect=execute)
        workflow.cleanup = AsyncMock()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(router, "create_work_dir", return_value=directory),
            patch.object(router, "WorkflowCheckpoint"),
            patch.object(router, "RemitWorkFlow", return_value=workflow),
            patch.object(router, "_AUTO_RESUME_LIMIT", 0),
            patch.object(
                router.task_bus,
                "is_cancellation_requested",
                side_effect=lookup,
            ),
            patch.object(router.task_bus, "publish_message", new=AsyncMock()),
            patch.object(
                router.task_bus,
                "clear_cancellation_request",
                new=AsyncMock(side_effect=ConnectionError("Redis still offline")),
            ),
        ):
            router._scheduled_tasks.add(task_id)
            await router.run_modeling_task_async(
                task_id, "test question", CompTemplate.CHINA, FormatOutPut.LaTeX
            )
        self.assertTrue(child_stopped.is_set())
        self.assertTrue(child_tasks[0].cancelled())
        workflow.cleanup.assert_awaited_once()
        self.assertNotIn(task_id, router._active_tasks)
        self.assertNotIn(task_id, router._scheduled_tasks)
        self.assertNotIn(task_id, router._pending_cancellations)

    async def test_running_task_stops_even_if_redis_cancel_write_fails(self) -> None:
        task_id = "cancel-offline"
        task = asyncio.create_task(asyncio.Event().wait())
        cancel_event = asyncio.Event()
        router._active_tasks[task_id] = (task, cancel_event)
        with (
            patch.object(
                router.task_bus,
                "request_cancellation",
                new=AsyncMock(side_effect=ConnectionError("Redis offline")),
            ),
            patch.object(
                router, "_load_workflow_checkpoint", side_effect=HTTPException(404)
            ),
        ):
            result = await router.cancel_task(task_id)
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(result.success)
        self.assertTrue(task.cancelled())
        self.assertTrue(cancel_event.is_set())

    async def test_scheduled_task_stays_cancelled_when_redis_write_fails(self) -> None:
        task_id = "cancel-before-start"
        background = BackgroundTasks()
        workflow = MagicMock()
        workflow.execute = AsyncMock()
        workflow.cleanup = AsyncMock()
        messages = [UserMessage(content="test question").model_dump(mode="json")]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(router, "create_work_dir", return_value=directory),
            patch.object(router, "WorkflowCheckpoint"),
            patch.object(router, "RemitWorkFlow", return_value=workflow),
            patch.object(router.task_bus, "publish_message", new=AsyncMock()),
            patch.object(
                router.task_bus,
                "load_task_messages",
                new=AsyncMock(return_value=messages),
            ),
            patch.object(
                router.task_bus,
                "request_cancellation",
                new=AsyncMock(side_effect=ConnectionError("Redis offline")),
            ),
            patch.object(
                router.task_bus,
                "is_cancellation_requested",
                new=AsyncMock(return_value=False),
            ),
            patch.object(
                router.task_bus, "clear_cancellation_request", new=AsyncMock()
            ),
            patch.object(
                router, "_load_workflow_checkpoint", side_effect=HTTPException(404)
            ),
        ):
            await router._schedule_new_task(
                background,
                task_id=task_id,
                problem_text="test question",
                template=CompTemplate.CHINA,
                output_format=FormatOutPut.LaTeX,
            )
            result = await router.cancel_task(task_id)
            self.assertTrue(result.success)
            await background()
        workflow.execute.assert_not_awaited()
        self.assertNotIn(task_id, router._active_tasks)
        self.assertNotIn(task_id, router._scheduled_tasks)
        self.assertNotIn(task_id, router._pending_cancellations)

    async def test_scheduling_failure_discards_uploaded_new_workspace(self) -> None:
        task_id = "intake-scheduling-failure"
        background = BackgroundTasks()
        upload = UploadFile(filename="input.csv", file=BytesIO(b"x,y\n1,2\n"))
        with (
            tempfile.TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(router, "create_task_id", return_value=task_id),
            patch("app.utils.common_utils._install_fonts"),
            patch.object(
                router.task_bus,
                "publish_message",
                new=AsyncMock(side_effect=OSError("archive unavailable")),
            ),
            patch.object(
                router.task_bus, "delete_task_record", new=AsyncMock()
            ) as discard_record,
        ):
            with self.assertRaises((OSError, HTTPException)):
                await router.submit_modeling(
                    background_tasks=background,
                    ques_all="test question",
                    user_requirements="",
                    comp_template=CompTemplate.CHINA,
                    format_output=FormatOutPut.LaTeX,
                    files=[upload],
                )
            self.assertFalse((Path("project/work_dir") / task_id).exists())
        discard_record.assert_awaited_once_with(task_id)
        self.assertEqual(background.tasks, [])
        self.assertNotIn(task_id, router._scheduled_tasks)

    async def test_initial_publish_failure_cleans_only_the_new_task(self) -> None:
        task_id = "intake-publish-failure"
        background = BackgroundTasks()
        with (
            tempfile.TemporaryDirectory() as directory,
            chdir(directory),
            patch.object(
                router.task_bus,
                "publish_message",
                new=AsyncMock(side_effect=OSError("archive unavailable")),
            ),
            patch.object(
                router.task_bus, "delete_task_record", new=AsyncMock()
            ) as discard_record,
        ):
            workspace = Path("project/work_dir") / task_id
            workspace.mkdir(parents=True)
            sibling = workspace.parent / "keep-existing-task"
            sibling.mkdir()
            sentinel = sibling / "source.csv"
            sentinel.write_bytes(b"keep this input")
            with self.assertRaises(OSError):
                await router._schedule_new_task(
                    background,
                    task_id=task_id,
                    problem_text="test question",
                    template=CompTemplate.CHINA,
                    output_format=FormatOutPut.LaTeX,
                )
            self.assertFalse(workspace.exists())
            self.assertEqual(sentinel.read_bytes(), b"keep this input")
        discard_record.assert_awaited_once_with(task_id)
        self.assertEqual(background.tasks, [])


if __name__ == "__main__":
    unittest.main()
