"""Redis 通道与任务消息持久化。

两条职责线：
- 实时：把任务消息 publish 到 ``task:{id}:messages`` 频道；
- 持久：消息以 SQLite 事务追加到 ``logs/messages/messages.sqlite3``，
  重启后可重建任务索引与状态。
"""

import asyncio
import os
from pathlib import Path

import redis.asyncio as aioredis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.config.setting import settings
from app.schemas.response import Message, SystemMessage
from app.services.message_archive import MessageArchive
from app.services.async_io import run_blocking
from app.services.task_state import message_task_status, task_status_from_messages
from app.utils.common_utils import ensure_safe_task_id
from app.utils.log_util import logger

_KEY_TTL_SECONDS = 36000


class RedisManager:
    """任务消息的总线与档案柜。"""

    def __init__(self, messages_dir: Path | None = None) -> None:
        self.redis_url = settings.REDIS_URL
        self._client: aioredis.Redis | None = None
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

    # ---- 连接 ----

    def _new_client(self) -> aioredis.Redis:
        return aioredis.Redis.from_url(
            self.redis_url,
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry=Retry(NoBackoff(), 0),
        )

    async def get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = self._new_client()
        client = self._client
        try:
            await client.ping()  # type: ignore[reportGeneralTypeIssues]
            logger.debug("Redis 连接正常")
            return client
        except Exception as exc:
            logger.error(f"无法连接到Redis: {exc}")
            raise

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health(self, timeout: float) -> tuple[bool, str]:
        """Return a bounded connectivity result for the UI status indicator."""
        try:
            client = await asyncio.wait_for(self.get_client(), timeout=timeout)
            await asyncio.wait_for(client.ping(), timeout=timeout)
        except TimeoutError:
            return False, "Redis 连接检查超时"
        except Exception as error:
            return False, f"Redis 不可用：{error}"
        return True, "Redis 连接正常"

    async def set(self, key: str, value: str) -> None:
        client = await self.get_client()
        await client.set(key, value)
        await client.expire(key, _KEY_TTL_SECONDS)

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
        """兼容调用入口；原子追加事件与任务索引，不重写整个历史。"""
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
        """删除任务档案与 Redis 临时键；返回是否确有本地档案被删。"""
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
            try:
                client = await self.get_client()
                await client.delete(
                    f"task_id:{task_id}", f"task_cancel_requested:{task_id}"
                )
            except Exception as exc:
                # 本地档案已删，Redis 离线不应让删除动作失败
                logger.warning(f"清理任务 Redis 键失败 {task_id}: {exc}")
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
        try:
            client = await self.get_client()
            await client.publish(f"task:{task_id}:messages", message.model_dump_json())
            logger.debug(
                f"消息已发布: task={task_id} type={message.msg_type} "
                f"content={message.content}"
            )
        except Exception as exc:
            logger.warning(
                "实时消息广播失败，但消息已安全落盘；"
                f"前端刷新后可恢复，任务继续执行: {exc}"
            )

    async def notify(
        self, task_id: str, content: str, type: str = "info", **fields
    ) -> None:
        """发布一条系统消息。"""
        await self.publish_message(
            task_id,
            SystemMessage(content=content, type=type, **fields),  # type: ignore[arg-type]
        )

    async def subscribe_to_task(self, task_id: str):
        client = await self.get_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(f"task:{task_id}:messages")
        return pubsub

    # ---- 历史读取与状态推导 ----

    async def load_task_messages(
        self, task_id: str, after: int = 0, limit: int | None = None
    ) -> list[dict]:
        """读取持久化历史；可按序号增量读取，旧 JSON 自动导入一次。"""
        safe_id = ensure_safe_task_id(task_id)
        async with self._get_message_lock(safe_id):
            return await self._archive_call(self.archive.load, safe_id, after, limit)

    async def task_exists(self, task_id: str) -> bool:
        safe_id = ensure_safe_task_id(task_id)
        if await self._archive_call(self.archive.exists, safe_id):
            return True
        try:
            client = await self.get_client()
            return bool(await client.exists(f"task_id:{safe_id}"))
        except Exception:
            return False

    task_status_from_messages = staticmethod(task_status_from_messages)

    # ---- 用户插话 ----

    async def push_user_note(self, task_id: str, content: str) -> None:
        """记下运行中的用户插话，等 Agent 下一轮对话注入。"""
        client = await self.get_client()
        key = f"task:{task_id}:user_notes"
        await client.rpush(key, content)  # type: ignore[reportGeneralTypeIssues]
        await client.expire(key, _KEY_TTL_SECONDS)

    async def drain_user_notes(self, task_id: str) -> list[str]:
        """原子地取走并清空待注入插话；Redis 故障时安静返回空。"""
        from app.services.message_scope import message_sink

        if message_sink.get() is not None:
            return []
        try:
            client = await self.get_client()
            key = f"task:{task_id}:user_notes"
            pipe = client.pipeline()
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            values, _ = await pipe.execute()
            return [str(v) for v in values if str(v).strip()]
        except Exception as exc:
            logger.warning(f"读取用户插话失败 {task_id}: {exc}")
            return []

    # ---- 取消标记 ----

    async def request_cancellation(self, task_id: str) -> None:
        """写入短期取消标记，覆盖后台任务尚未注册的竞态窗口。"""
        await self.set(f"task_cancel_requested:{task_id}", "1")

    async def is_cancellation_requested(self, task_id: str) -> bool:
        client = await self.get_client()
        return bool(await client.exists(f"task_cancel_requested:{task_id}"))

    async def clear_cancellation_request(self, task_id: str) -> None:
        client = await self.get_client()
        await client.delete(f"task_cancel_requested:{task_id}")

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


redis_manager = RedisManager()
