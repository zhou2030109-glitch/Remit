"""Regression tests for durable task messages and task history."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException

from app.routers.common_router import (
    TaskMessageRequest,
    clear_task_history,
    delete_task,
    post_task_message,
)
from app.routers.modeling_router import modeling
from app.schemas.enums import CompTemplate, FormatOutPut
from app.schemas.response import SystemMessage, UserMessage
from app.services.redis_manager import RedisManager


class TaskMessageHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_messages_survive_a_new_manager_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = RedisManager(messages_dir=Path(tmp))
            await first._save_message_to_file(
                "20260711-120000-abcd1234",
                UserMessage(content="问题1必须输出预测值"),
            )

            second = RedisManager(messages_dir=Path(tmp))
            messages = await second.load_task_messages("20260711-120000-abcd1234")

            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["content"], "问题1必须输出预测值")
            self.assertIn("created_at", messages[0])

    async def test_task_history_is_built_from_durable_message_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RedisManager(messages_dir=Path(tmp))
            task_id = "20260711-120000-abcd1234"
            await manager._save_message_to_file(
                task_id,
                UserMessage(content="2025 高教社杯 C 题 NIPT"),
            )
            await manager._save_message_to_file(
                task_id,
                SystemMessage(content="任务处理完成", type="success"),
            )

            history = await manager.list_task_summaries()

            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["task_id"], task_id)
            self.assertEqual(history[0]["title"], "2025 高教社杯 C 题 NIPT")
            self.assertEqual(history[0]["status"], "completed")
            self.assertEqual(history[0]["message_count"], 2)

    async def test_delete_task_record_removes_history_and_redis_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RedisManager(messages_dir=Path(tmp))
            task_id = "20260711-120000-abcd1234"
            await manager._save_message_to_file(
                task_id,
                UserMessage(content="待删除的历史任务"),
            )
            redis_client = AsyncMock()

            with patch.object(
                manager,
                "get_client",
                new=AsyncMock(return_value=redis_client),
            ):
                deleted = await manager.delete_task_record(task_id)

            self.assertTrue(deleted)
            self.assertFalse((Path(tmp) / f"{task_id}.json").exists())
            self.assertEqual(await manager.list_task_summaries(), [])
            redis_client.delete.assert_awaited_once_with(
                f"task_id:{task_id}",
                f"task_cancel_requested:{task_id}",
            )

    async def test_delete_task_rejects_running_task(self) -> None:
        task_id = "20260711-120000-abcd1234"
        running_messages = [
            UserMessage(content="仍在运行的任务").model_dump(mode="json")
        ]

        with (
            patch(
                "app.routers.common_router.redis_manager.load_task_messages",
                new=AsyncMock(return_value=running_messages),
            ),
            patch(
                "app.routers.common_router.redis_manager.delete_task_record",
                new=AsyncMock(),
            ) as delete_record,
        ):
            with self.assertRaises(HTTPException) as raised:
                await delete_task(task_id)

        self.assertEqual(raised.exception.status_code, 409)
        delete_record.assert_not_awaited()

    async def test_delete_task_removes_work_directory_and_message_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_id = "20260711-120000-abcd1234"
            work_root = Path(tmp) / "work_dir"
            task_dir = work_root / task_id
            task_dir.mkdir(parents=True)
            (task_dir / "res.md").write_text("temporary output", encoding="utf-8")
            completed_messages = [
                UserMessage(content="已完成任务").model_dump(mode="json"),
                SystemMessage(
                    content="任务处理完成",
                    type="success",
                ).model_dump(mode="json"),
            ]

            with (
                patch(
                    "app.routers.common_router.TASK_WORK_DIR_ROOT",
                    work_root,
                ),
                patch(
                    "app.routers.common_router.redis_manager.load_task_messages",
                    new=AsyncMock(return_value=completed_messages),
                ),
                patch(
                    "app.routers.common_router.redis_manager.delete_task_record",
                    new=AsyncMock(return_value=True),
                ) as delete_record,
            ):
                response = await delete_task(task_id)

            self.assertTrue(response.success)
            self.assertEqual(response.task_id, task_id)
            self.assertFalse(task_dir.exists())
            delete_record.assert_awaited_once_with(task_id)

    async def test_clear_task_history_removes_all_records_and_work_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_ids = [
                "20260711-120000-abcd1234",
                "20260711-120100-efgh5678",
            ]
            work_root = Path(tmp) / "work_dir"
            for task_id in task_ids:
                task_dir = work_root / task_id
                task_dir.mkdir(parents=True)
                (task_dir / "res.md").write_text("output", encoding="utf-8")

            summaries = [
                {"task_id": task_id, "status": "completed"}
                for task_id in task_ids
            ]
            with (
                patch(
                    "app.routers.common_router.TASK_WORK_DIR_ROOT",
                    work_root,
                ),
                patch(
                    "app.routers.common_router.redis_manager.list_task_summaries",
                    new=AsyncMock(return_value=summaries),
                ),
                patch(
                    "app.routers.common_router.redis_manager.delete_task_record",
                    new=AsyncMock(return_value=True),
                ) as delete_record,
            ):
                response = await clear_task_history()

            self.assertTrue(response.success)
            self.assertEqual(response.deleted_count, 2)
            self.assertTrue(all(not (work_root / task_id).exists() for task_id in task_ids))
            self.assertEqual(
                [call.args[0] for call in delete_record.await_args_list],
                task_ids,
            )

    async def test_clear_task_history_is_all_or_nothing_when_task_is_running(
        self,
    ) -> None:
        summaries = [
            {"task_id": "20260711-120000-abcd1234", "status": "completed"},
            {"task_id": "20260711-120100-efgh5678", "status": "running"},
        ]
        with (
            patch(
                "app.routers.common_router.redis_manager.list_task_summaries",
                new=AsyncMock(return_value=summaries),
            ),
            patch(
                "app.routers.common_router._delete_task_work_dir"
            ) as delete_work_dir,
            patch(
                "app.routers.common_router.redis_manager.delete_task_record",
                new=AsyncMock(),
            ) as delete_record,
        ):
            with self.assertRaises(HTTPException) as raised:
                await clear_task_history()

        self.assertEqual(raised.exception.status_code, 409)
        delete_work_dir.assert_not_called()
        delete_record.assert_not_awaited()

    async def test_clear_empty_task_history_succeeds(self) -> None:
        with patch(
            "app.routers.common_router.redis_manager.list_task_summaries",
            new=AsyncMock(return_value=[]),
        ):
            response = await clear_task_history()

        self.assertTrue(response.success)
        self.assertEqual(response.deleted_count, 0)

    async def test_posted_user_message_is_persisted_and_returned(self) -> None:
        task_id = "20260711-120000-abcd1234"
        persisted = UserMessage(content="请补充逐样本预测值")
        with (
            patch(
                "app.routers.common_router.redis_manager.task_exists",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.routers.common_router.redis_manager.publish_message",
                new=AsyncMock(),
            ) as publish,
        ):
            response = await post_task_message(
                task_id,
                TaskMessageRequest(content="  请补充逐样本预测值  "),
            )

        self.assertEqual(response.content, persisted.content)
        publish.assert_awaited_once()
        published = publish.await_args.args[1]
        self.assertEqual(published.msg_type, "user")
        self.assertEqual(published.content, persisted.content)

    async def test_modeling_submission_persists_initial_question_under_task_id(
        self,
    ) -> None:
        task_id = "20260711-120000-abcd1234"
        background_tasks = BackgroundTasks()
        with (
            patch(
                "app.routers.modeling_router.create_task_id",
                return_value=task_id,
            ),
            patch(
                "app.routers.modeling_router.create_work_dir",
                return_value="ignored",
            ),
            patch(
                "app.routers.modeling_router.redis_manager.set",
                new=AsyncMock(),
            ),
            patch(
                "app.routers.modeling_router.redis_manager.publish_message",
                new=AsyncMock(),
            ) as publish,
        ):
            response = await modeling(
                background_tasks=background_tasks,
                ques_all="完整赛题内容",
                user_requirements="问题1输出预测值",
                comp_template=CompTemplate.CHINA,
                format_output=FormatOutPut.Markdown,
                files=None,
            )

        self.assertEqual(response["task_id"], task_id)
        publish.assert_awaited_once()
        initial = publish.await_args.args[1]
        self.assertEqual(initial.msg_type, "user")
        self.assertIn("完整赛题内容", initial.content)
        self.assertIn("问题1输出预测值", initial.content)


if __name__ == "__main__":
    unittest.main()
