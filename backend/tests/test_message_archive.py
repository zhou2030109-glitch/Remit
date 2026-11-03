"""SQLite 档案迁移、并发游标、生命周期与线程取消的行为测试。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.schemas.response import SystemMessage, UserMessage
from app.services.message_archive import MessageArchive
from app.services.task_bus import TaskBus
from app.services.task_state import message_task_status, task_status_from_messages


class MessageArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = MessageArchive(self.root)

    def test_legacy_json_imports_once_and_survives_restart(self):
        legacy = self.root / "legacy-task.json"
        legacy.write_text(
            json.dumps(
                [
                    {"msg_type": "user", "content": "原始赛题", "sequence": 999},
                    {
                        "msg_type": "system",
                        "type": "success",
                        "content": "任务处理完成",
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8-sig",
        )
        original_bytes = legacy.read_bytes()
        first = self.archive.load("legacy-task")
        self.assertEqual(len(first), 2)
        self.assertEqual(first[-1]["task_status"], "completed")
        self.assertNotEqual(first[0]["sequence"], 999)
        sequence = self.archive.append(
            "legacy-task",
            {
                "msg_type": "system",
                "content": "续跑的新提示",
                "task_status": "running",
            },
        )
        self.assertEqual(legacy.read_bytes(), original_bytes)
        # 已导入文件不再解析；即使旧文件改变也不能重复计数或覆盖新记录。
        legacy.write_text("not valid JSON", encoding="utf-8")
        restarted = MessageArchive(self.root)
        history = restarted.load("legacy-task")
        self.assertEqual(
            [item["sequence"] for item in history],
            [
                first[0]["sequence"],
                first[1]["sequence"],
                sequence,
            ],
        )
        summary = restarted.summaries()[0]
        self.assertEqual(summary["title"], "原始赛题")
        self.assertEqual(summary["message_count"], 3)
        self.assertEqual(summary["status"], "running")

    def test_failed_import_can_be_corrected_without_partial_or_duplicate_events(self):
        legacy = self.root / "repairable-task.json"
        legacy.write_text(json.dumps([{"content": "partial"}, "invalid message"]))
        with self.assertRaises(ValueError):
            self.archive.load("repairable-task")
        legacy.write_text(json.dumps([{"msg_type": "user", "content": "corrected"}]))
        history = self.archive.load("repairable-task")
        self.assertEqual([item["content"] for item in history], ["corrected"])
        self.assertEqual(self.archive.summaries()[0]["message_count"], 1)

    def test_corrupt_or_unsafe_legacy_files_do_not_hide_healthy_tasks(self):
        self.root.joinpath("healthy-task.json").write_text(
            json.dumps(
                [
                    {"msg_type": "user", "content": "healthy task"},
                ]
            )
        )
        self.root.joinpath("broken-task.json").write_text("{broken")
        self.root.joinpath("invalid task name.json").write_text("[]")
        summaries = self.archive.summaries()
        self.assertEqual([item["task_id"] for item in summaries], ["healthy-task"])
        self.assertEqual(
            self.archive.load("healthy-task")[0]["content"], "healthy task"
        )

    def test_concurrent_appends_from_multiple_archive_instances_do_not_lose_events(
        self,
    ):
        archives = (self.archive, MessageArchive(self.root))
        # 先建立模式，测试重点是并发事务和全局序号。
        self.archive.load("concurrent-task")

        def append(index):
            return archives[index % len(archives)].append(
                "concurrent-task",
                {
                    "msg_type": "user",
                    "content": f"event-{index}",
                },
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            sequences = list(executor.map(append, range(80)))
        self.assertEqual(len(set(sequences)), 80)
        history = MessageArchive(self.root).load("concurrent-task")
        self.assertEqual(len(history), 80)
        self.assertEqual(
            {item["content"] for item in history}, {f"event-{i}" for i in range(80)}
        )
        self.assertEqual([item["sequence"] for item in history], sorted(sequences))
        self.assertEqual(self.archive.summaries()[0]["message_count"], 80)

    def test_cursor_pages_are_exclusive_and_ignore_other_tasks(self):
        task_sequences = []
        for index in range(5):
            task_sequences.append(
                self.archive.append(
                    "paged-task",
                    {
                        "msg_type": "user",
                        "content": str(index),
                    },
                )
            )
            self.archive.append("other-task", {"content": "other"})
        page = self.archive.load("paged-task", after=task_sequences[0], limit=2)
        self.assertEqual([item["sequence"] for item in page], task_sequences[1:3])
        last = self.archive.load("paged-task", after=page[-1]["sequence"], limit=2)
        self.assertEqual([item["sequence"] for item in last], task_sequences[3:])
        self.assertEqual(
            self.archive.load("paged-task", after=last[-1]["sequence"]), []
        )
        self.assertEqual(self.archive.load("paged-task", limit=0), [])

    def test_deleted_legacy_task_does_not_reappear_after_restart_or_index_scan(self):
        legacy = self.root / "deleted-task.json"
        legacy.write_text(json.dumps([{"msg_type": "user", "content": "old task"}]))
        self.assertTrue(self.archive.delete("deleted-task"))
        self.assertTrue(legacy.exists())
        restarted = MessageArchive(self.root)
        self.assertFalse(restarted.exists("deleted-task"))
        self.assertEqual(restarted.load("deleted-task"), [])
        self.assertEqual(restarted.summaries(), [])
        self.assertFalse(restarted.delete("never-created-task"))

    def test_index_uses_explicit_status_and_first_user_title(self):
        self.archive.append(
            "indexed-task",
            {
                "agent_type": "CoordinatorAgent",
                "content": '{"title":"coordinator title"}',
            },
        )
        self.archive.append(
            "indexed-task", {"msg_type": "user", "content": " First\n  user title "}
        )
        self.archive.append(
            "indexed-task",
            {
                "msg_type": "system",
                "content": "Wording can change completely",
                "task_status": "completed",
            },
        )
        self.archive.append(
            "indexed-task", {"msg_type": "user", "content": "later feedback"}
        )
        self.archive.append(
            "indexed-task",
            {"msg_type": "system", "content": "ordinary progress", "type": "success"},
        )
        summary = self.archive.summaries()[0]
        self.assertEqual(summary["title"], "First user title")
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["message_count"], 5)
        history = self.archive.load("indexed-task")
        self.assertEqual(task_status_from_messages(history), summary["status"])

    def test_explicit_lifecycle_is_independent_of_message_wording(self):
        for status in (
            "running",
            "awaiting_approval",
            "completed",
            "failed",
            "stopped",
        ):
            with self.subTest(status=status):
                self.assertEqual(
                    message_task_status(
                        {
                            "msg_type": "system",
                            "type": "success",
                            "content": "任务处理完成",
                            "task_status": status,
                        }
                    ),
                    status,
                )


class AsyncMessageArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = TaskBus(self.root)

    async def test_broadcast_carries_the_already_committed_sequence(self):
        message = SystemMessage(
            content="Finished with different wording", task_status="completed"
        )
        subscription = await self.manager.subscribe_to_task("broadcast-task")
        await self.manager.publish_message("broadcast-task", message)
        broadcast = json.loads((await subscription.get_message())["data"])
        committed = await self.manager.load_task_messages("broadcast-task")
        self.assertEqual(committed[-1]["sequence"], broadcast["sequence"])
        self.assertEqual(committed[-1]["task_status"], "completed")
        self.assertIsInstance(message.sequence, int)
        await subscription.aclose()
        self.assertFalse(self.manager._subscriptions)

    async def test_restart_reconciles_only_explicitly_running_tasks(self):
        for status in ("running", "completed", "awaiting_approval"):
            await self.manager._save_message_to_file(
                f"restart-{status}",
                SystemMessage(content="state wording", task_status=status),
            )
        restarted = TaskBus(self.root)
        self.assertEqual(await restarted.reconcile_interrupted_tasks(), 1)
        self.assertEqual(await restarted.reconcile_interrupted_tasks(), 0)
        summaries = {
            row["task_id"]: row for row in await restarted.list_task_summaries()
        }
        self.assertEqual(summaries["restart-running"]["status"], "stopped")
        self.assertEqual(summaries["restart-completed"]["status"], "completed")
        self.assertEqual(
            summaries["restart-awaiting_approval"]["status"], "awaiting_approval"
        )

    async def test_archive_work_runs_off_event_loop_and_does_not_rewrite_legacy_history(
        self,
    ):
        started = threading.Event()
        release = threading.Event()
        worker_threads = []
        original_append = self.manager.archive.append
        loop_thread = threading.get_ident()

        def blocked_append(*args):
            worker_threads.append(threading.get_ident())
            started.set()
            if not release.wait(2):
                raise TimeoutError("test did not release archive thread")
            return original_append(*args)

        with patch.object(self.manager.archive, "append", side_effect=blocked_append):
            writer = asyncio.create_task(
                self.manager._save_message_to_file(
                    "threaded-task",
                    UserMessage(content="payload"),
                )
            )
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                await asyncio.sleep(0)
                self.assertFalse(writer.done())
                self.assertNotEqual(worker_threads, [loop_thread])
            finally:
                release.set()
                await writer
        self.assertEqual(len(await self.manager.load_task_messages("threaded-task")), 1)
        self.assertFalse(self.root.joinpath("threaded-task.json").exists())

    async def test_repeated_writer_cancellation_cannot_recreate_a_deleted_task(self):
        append_started = threading.Event()
        release_append = threading.Event()
        delete_committed = threading.Event()
        original_append = self.manager.archive.append
        original_delete = self.manager.archive.delete

        def delayed_append(*args):
            # 在线程取得 SQLite 锁之前暂停，能暴露取消提前释放异步锁的竞态。
            append_started.set()
            if not release_append.wait(3):
                raise TimeoutError("test did not release append")
            return original_append(*args)

        def observe_delete(*args):
            removed = original_delete(*args)
            delete_committed.set()
            return removed

        with (
            patch.object(self.manager.archive, "append", side_effect=delayed_append),
            patch.object(self.manager.archive, "delete", side_effect=observe_delete),
        ):
            writer = asyncio.create_task(
                self.manager._save_message_to_file(
                    "cancel-delete-task",
                    UserMessage(content="late write"),
                )
            )
            deletion = None
            try:
                self.assertTrue(await asyncio.to_thread(append_started.wait, 1))
                writer.cancel()
                await asyncio.sleep(0)
                writer.cancel()
                await asyncio.sleep(0)
                deletion = asyncio.create_task(
                    self.manager.delete_task_record("cancel-delete-task")
                )
                deleted_before_append_finished = await asyncio.to_thread(
                    delete_committed.wait, 0.15
                )
            finally:
                release_append.set()
                results = await asyncio.gather(
                    writer,
                    *([deletion] if deletion else []),
                    return_exceptions=True,
                )
            self.assertFalse(deleted_before_append_finished)
            self.assertIsInstance(results[0], asyncio.CancelledError)
            self.assertEqual(results[1], True)
        self.assertEqual(
            await self.manager.load_task_messages("cancel-delete-task"), []
        )
        self.assertEqual(await self.manager.list_task_summaries(), [])

    async def test_messages_arriving_during_delete_are_rejected(self):
        await self.manager._save_message_to_file(
            "deleting-task", UserMessage(content="old")
        )
        cleanup_started = threading.Event()
        release_cleanup = threading.Event()
        original_delete = self.manager.archive.delete

        def slow_delete(task_id):
            cleanup_started.set()
            release_cleanup.wait(2)
            return original_delete(task_id)

        self.manager.archive.delete = slow_delete
        deletion = asyncio.create_task(self.manager.delete_task_record("deleting-task"))
        await asyncio.to_thread(cleanup_started.wait, 2)
        try:
            with self.assertRaisesRegex(RuntimeError, "任务正在删除"):
                await self.manager._save_message_to_file(
                    "deleting-task", UserMessage(content="late")
                )
        finally:
            release_cleanup.set()
            await deletion
        self.assertEqual(await self.manager.load_task_messages("deleting-task"), [])

    async def test_corrupt_legacy_archive_can_be_deleted_without_importing_it(self):
        legacy = self.root / "corrupt-delete-task.json"
        legacy.write_text("{broken history")
        self.assertTrue(await self.manager.delete_task_record("corrupt-delete-task"))
        self.assertFalse(legacy.exists())
        self.assertEqual(
            await self.manager.load_task_messages("corrupt-delete-task"), []
        )
        self.assertEqual(await self.manager.list_task_summaries(), [])
        self.assertFalse(await self.manager.delete_task_record("corrupt-delete-task"))

    async def test_cancelled_write_stays_cancelled_when_storage_also_fails(self):
        started = threading.Event()
        release = threading.Event()

        def fail_after_cancel(*_args):
            started.set()
            if not release.wait(2):
                raise TimeoutError("test did not release archive thread")
            raise OSError("disk unavailable after cancellation")

        with patch.object(
            self.manager.archive, "append", side_effect=fail_after_cancel
        ):
            writer = asyncio.create_task(
                self.manager._save_message_to_file(
                    "cancel-error-task",
                    UserMessage(content="payload"),
                )
            )
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                writer.cancel()
                await asyncio.sleep(0)
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await writer
        self.assertEqual(await self.manager.load_task_messages("cancel-error-task"), [])
