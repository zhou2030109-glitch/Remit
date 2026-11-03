"""进程内任务消息总线与消息档案。

后端始终以单进程运行，任务、WebSocket 与本总线同处一个事件循环：
- 实时：消息广播给本进程内订阅该任务的 WebSocket 队列；
- 持久：消息以 SQLite 事务追加到 ``logs/messages/messages.sqlite3``，
  重启后可重建任务索引与状态，WebSocket 也从档案按序号补发；
- 插话与取消标记只在进程内有效，服务重启时运行中的任务本就会被标记为停止。
"""

import asyncio
import os
from pathlib import Path

from app.schemas.response import Message, SystemMessage
from app.services.message_archive import MessageArchive
from app.services.async_io import run_blocking
from app.services.task_state import message_task_status, task_status_from_messages
from app.utils.common_utils import ensure_safe_task_id

_SUBSCRIPTION_BUFFER = 1000


class TaskSubscription:
    """单个 WebSocket 对一个任务的实时订阅。"""

    def __init__(self, bus: "TaskBus", task_id: str) -> None:
        self._bus = bus
        self.task_id = task_id
        self._queue: asyncio.Queue[str] = asyncio.Queue(_SUBSCRIPTION_BUFFER)

    def deliver(self, payload: str) -> None:
        if self._queue.full():
            # 有序事件可从档案补发，丢弃最旧的瞬态播报即可。
            self._queue.get_nowait()
        self._queue.put_nowait(payload)

    async def get_message(
        self, ignore_subscribe_messages: bool = True, timeout: float = 0.0
    ) -> dict | None:
        try:
            if timeout > 0:
                data = await asyncio.wait_for(self._queue.get(), timeout)
            else:
                data = self._queue.get_nowait()
        except (asyncio.QueueEmpty, TimeoutError):
            return None
        return {"type": "message", "data": data}

    async def unsubscribe(self, *_channels: str) -> None:
        self._bus._unsubscribe(self)

    async def aclose(self) -> None:
        self._bus._unsubscribe(self)


class TaskBus:
    """任务消息的总线与档案柜。"""

    def __init__(self, messages_dir: Path | None = None) -> None:
        backend_root = Path(__file__).resolve().parents[2]
        configured = os.environ.get("REMIT_MESSAGES_DIR", "").strip()
        self.messages_dir = (
            messages_dir
            if messages_dir is not None
            else Path(configured).expanduser().resolve()
            if configured
            else backend_root / "logs" / "messages"
        )
        self.messages_dir.mkdir(parents=True, exist_ok=True)
        self.archive = MessageArchive(self.messages_dir)
        self._message_locks: dict[str, asyncio.Lock] = {}
        self._deleting_tasks: set[str] = set()
        self._subscriptions: dict[str, set[TaskSubscription]] = {}
        self._user_notes: dict[str, list[str]] = {}
        self._cancellation_requests: set[str] = set()

    async def close(self) -> None:
        self._subscriptions.clear()

    async def health(self, timeout: float) -> tuple[bool, str]:
        """消息档案可读即视为任务服务可用。"""
        try:
            await asyncio.wait_for(self._archive_call(self.archive.summaries), timeout)
        except TimeoutError:
            return False, "任务消息档案读取超时"
        except Exception as error:
            return False, f"任务消息档案不可用：{error}"
        return True, "任务服务运行正常"

    # ---- 消息落盘 ----

    def _get_message_lock(self, task_id: str) -> asyncio.Lock:
        return self._message_locks.setdefault(task_id, asyncio.Lock())

    def _task_file(self, task_id: str) -> Path:
        return self.messages_dir / f"{ensure_safe_task_id(task_id)}.json"

    def _assert_writable(self, task_id: str) -> None:
        if task_id in self._deleting_tasks:
            raise RuntimeError("任务正在删除，不能继续写入消息")

    async def _archive_call(self, function, *args):
        """线程完成事务后才释放异步锁，避免取消后仍在写入已删除的任务。"""
        return await run_blocking(function, *args)

    async def _save_message_to_file(self, task_id: str, message: Message) -> None:
        """原子追加事件与任务索引，不重写整个历史。"""
        safe_id = ensure_safe_task_id(task_id)
        self._assert_writable(safe_id)
        async with self._get_message_lock(safe_id):
            self._assert_writable(safe_id)
            if status := message_task_status(message.model_dump(mode="json")):
                message.task_status = status
            message.sequence = await self._archive_call(
                self.archive.append, safe_id, message.model_dump(mode="json")
            )

    async def delete_task_record(self, task_id: str) -> bool:
        """删除任务档案与进程内状态；返回是否确有本地档案被删。"""
        task_id = ensure_safe_task_id(task_id)
        target = self._task_file(task_id)
        staging = target.with_suffix(".json.tmp")
        self._deleting_tasks.add(task_id)
        removed = False
        try:
            async with self._get_message_lock(task_id):
                removed = await self._archive_call(self.archive.delete, task_id)
                for path in (target, staging):
                    if path.is_file():
                        path.unlink()
                        removed = True
            self._user_notes.pop(task_id, None)
            self._cancellation_requests.discard(task_id)
            return removed
        finally:
            self._deleting_tasks.discard(task_id)
            self._message_locks.pop(task_id, None)

    # ---- 发布与订阅 ----

    async def publish_message(self, task_id: str, message: Message) -> None:
        """先落盘再广播；activity 是高频瞬态播报，只广播不留档。"""
        from app.services.message_scope import message_sink
        from app.services.team_state import record_message

        sink = message_sink.get()
        await asyncio.to_thread(
            record_message,
            task_id,
            message.model_dump(mode="json"),
            scope="writing" if sink is not None else "modeling",
        )
        if sink is not None:
            await sink(task_id, message)
            return
        if message.msg_type != "activity":
            await self._save_message_to_file(task_id, message)
        payload = message.model_dump_json()
        for subscription in list(self._subscriptions.get(task_id, ())):
            subscription.deliver(payload)

    async def notify(
        self, task_id: str, content: str, type: str = "info", **fields
    ) -> None:
        """发布一条系统消息。"""
        await self.publish_message(
            task_id,
            SystemMessage(content=content, type=type, **fields),  # type: ignore[arg-type]
        )

    async def subscribe_to_task(self, task_id: str) -> TaskSubscription:
        subscription = TaskSubscription(self, task_id)
        self._subscriptions.setdefault(task_id, set()).add(subscription)
        return subscription

    def _unsubscribe(self, subscription: TaskSubscription) -> None:
        subscribers = self._subscriptions.get(subscription.task_id)
        if subscribers is None:
            return
        subscribers.discard(subscription)
        if not subscribers:
            self._subscriptions.pop(subscription.task_id, None)

    # ---- 历史读取与状态推导 ----

    async def load_task_messages(
        self, task_id: str, after: int = 0, limit: int | None = None
    ) -> list[dict]:
        """读取持久化历史；可按序号增量读取，旧 JSON 自动导入一次。"""
        safe_id = ensure_safe_task_id(task_id)
        async with self._get_message_lock(safe_id):
            return await self._archive_call(self.archive.load, safe_id, after, limit)

    async def task_exists(self, task_id: str) -> bool:
        return await self._archive_call(
            self.archive.exists, ensure_safe_task_id(task_id)
        )

    task_status_from_messages = staticmethod(task_status_from_messages)

    # ---- 用户插话 ----

    async def push_user_note(self, task_id: str, content: str) -> None:
        """记下运行中的用户插话，等 Agent 下一轮对话注入。"""
        self._user_notes.setdefault(task_id, []).append(content)

    async def drain_user_notes(self, task_id: str) -> list[str]:
        """取走并清空待注入插话。"""
        from app.services.message_scope import message_sink

        if message_sink.get() is not None:
            return []
        return [v for v in self._user_notes.pop(task_id, []) if v.strip()]

    # ---- 取消标记 ----

    async def request_cancellation(self, task_id: str) -> None:
        """记录取消请求，覆盖后台任务尚未注册的竞态窗口。"""
        self._cancellation_requests.add(task_id)

    async def is_cancellation_requested(self, task_id: str) -> bool:
        return task_id in self._cancellation_requests

    async def clear_cancellation_request(self, task_id: str) -> None:
        self._cancellation_requests.discard(task_id)

    # ---- 启动重建 ----

    async def reconcile_interrupted_tasks(self) -> int:
        """从持久化索引恢复状态，重启前仍运行的任务标记为停止。"""
        count = 0
        for summary in await self.list_task_summaries():
            if summary["status"] in {"running", "stopping"}:
                await self._save_message_to_file(
                    summary["task_id"],
                    SystemMessage(
                        content="服务重启，原运行任务已停止",
                        type="warning",
                        task_status="stopped",
                    ),
                )
                count += 1
        return count

    async def list_task_summaries(self) -> list[dict]:
        """查询持久化任务索引，不再逐任务反序列化全部历史消息。"""
        return await self._archive_call(self.archive.summaries)


task_bus = TaskBus()
