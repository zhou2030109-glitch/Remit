"""Redis 通道与任务消息持久化。

两条职责线：
- 实时：把任务消息 publish 到 ``task:{id}:messages`` 频道；
- 持久：同一任务的消息追加到 ``logs/messages/{id}.json``，
  重启后可重建任务索引与状态。
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import redis.asyncio as aioredis

from app.config.setting import settings
from app.schemas.response import Message, SystemMessage
from app.utils.log_util import logger

_KEY_TTL_SECONDS = 36000


class RedisManager:
    """任务消息的总线与档案柜。"""

    def __init__(self, messages_dir: Path | None = None) -> None:
        self.redis_url = settings.REDIS_URL
        self._client: aioredis.Redis | None = None
        backend_root = Path(__file__).resolve().parents[2]
        self.messages_dir = messages_dir or backend_root / "logs" / "messages"
        self.messages_dir.mkdir(parents=True, exist_ok=True)
        self._message_locks: dict[str, asyncio.Lock] = {}
        self._deleting_tasks: set[str] = set()

    # ---- 连接 ----

    async def get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
            )
        try:
            await self._client.ping()  # type: ignore[reportGeneralTypeIssues]
            logger.info(f"Redis 连接建立成功: {self.redis_url}")
            return self._client
        except Exception as exc:
            logger.error(f"无法连接到Redis: {exc}")
            raise

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def set(self, key: str, value: str) -> None:
        client = await self.get_client()
        await client.set(key, value)
        await client.expire(key, _KEY_TTL_SECONDS)

    # ---- 消息落盘 ----

    def _get_message_lock(self, task_id: str) -> asyncio.Lock:
        return self._message_locks.setdefault(task_id, asyncio.Lock())

    def _task_file(self, task_id: str) -> Path:
        return self.messages_dir / f"{task_id}.json"

    def _assert_writable(self, task_id: str) -> None:
        if task_id in self._deleting_tasks:
            raise RuntimeError("任务正在删除，不能继续写入消息")

    async def _save_message_to_file(self, task_id: str, message: Message) -> None:
        """把一条消息追加进任务档案；临时文件 + 原子替换防止半截写入。"""
        try:
            self._assert_writable(task_id)
            self.messages_dir.mkdir(parents=True, exist_ok=True)
            async with self._get_message_lock(task_id):
                self._assert_writable(task_id)
                target = self._task_file(task_id)
                history: list[dict] = []
                if target.exists():
                    loaded = json.loads(target.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        history = loaded

                history.append(message.model_dump(mode="json"))
                staging = target.with_suffix(".json.tmp")
                staging.write_text(
                    json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                staging.replace(target)
            logger.debug(f"消息已追加到文件: {target}")
        except Exception as exc:
            logger.error(f"保存消息到文件失败: {exc}")
            raise

    async def delete_task_record(self, task_id: str) -> bool:
        """删除任务档案与 Redis 临时键；返回是否确有本地档案被删。"""
        target = self._task_file(task_id)
        staging = target.with_suffix(".json.tmp")
        self._deleting_tasks.add(task_id)
        removed = False
        try:
            async with self._get_message_lock(task_id):
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
        if message.msg_type != "activity":
            await self._save_message_to_file(task_id, message)
        try:
            client = await self.get_client()
            await client.publish(
                f"task:{task_id}:messages", message.model_dump_json()
            )
            logger.debug(
                f"消息已发布: task={task_id} type={message.msg_type} "
                f"content={message.content}"
            )
        except Exception as exc:
            logger.warning(
                "实时消息广播失败，但消息已安全落盘；"
                f"前端刷新后可恢复，任务继续执行: {exc}"
            )

    async def subscribe_to_task(self, task_id: str):
        client = await self.get_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(f"task:{task_id}:messages")
        return pubsub

    # ---- 历史读取与状态推导 ----

    async def load_task_messages(self, task_id: str) -> list[dict]:
        target = self._task_file(task_id)
        if not target.is_file():
            return []
        try:
            async with self._get_message_lock(task_id):
                data = json.loads(target.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(f"读取任务消息失败 {task_id}: {exc}")
            return []

    async def task_exists(self, task_id: str) -> bool:
        if self._task_file(task_id).is_file():
            return True
        try:
            client = await self.get_client()
            return bool(await client.exists(f"task_id:{task_id}"))
        except Exception:
            return False

    @staticmethod
    def task_status_from_messages(messages: list[dict]) -> str:
        """从消息尾部推导任务状态；中途的 success/warning 不算终态。"""
        for item in reversed(messages):
            if item.get("msg_type") == "approval":
                return "awaiting_approval"
            if item.get("msg_type") != "system":
                continue
            kind = item.get("type")
            content = str(item.get("content", ""))
            if kind == "success" and content == "任务处理完成":
                return "completed"
            if kind == "error" and content.startswith("任务执行失败"):
                return "failed"
            if kind == "warning" and (
                "任务已停止" in content or "服务重启" in content
            ):
                return "stopped"
            if (
                content == "任务开始处理"
                or content.startswith("任务从节点 ")
                or content.startswith("任务继续处理")
            ):
                return "running"
        return "running"

    # ---- 用户插话 ----

    async def push_user_note(self, task_id: str, content: str) -> None:
        """记下运行中的用户插话，等 Agent 下一轮对话注入。"""
        client = await self.get_client()
        key = f"task:{task_id}:user_notes"
        await client.rpush(key, content)  # type: ignore[reportGeneralTypeIssues]
        await client.expire(key, _KEY_TTL_SECONDS)

    async def drain_user_notes(self, task_id: str) -> list[str]:
        """原子地取走并清空待注入插话；Redis 故障时安静返回空。"""
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
        """把档案里仍显示 running 的旧任务标记为因重启中断。"""
        count = 0
        for file_path in self.messages_dir.glob("*.json"):
            task_id = file_path.stem
            messages = await self.load_task_messages(task_id)
            if messages and self.task_status_from_messages(messages) == "running":
                await self._save_message_to_file(
                    task_id,
                    SystemMessage(content="服务重启，原运行任务已停止", type="warning"),
                )
                count += 1
        return count

    @staticmethod
    def _infer_title(task_id: str, messages: list[dict]) -> str:
        """优先取用户首条输入，其次取协调者产出的题目，最后退回 task_id。"""
        first_user = next(
            (
                str(item.get("content", "")).strip()
                for item in messages
                if item.get("msg_type") == "user"
                and str(item.get("content", "")).strip()
            ),
            "",
        )
        coordinator_title = ""
        if not first_user:
            for item in messages:
                if item.get("agent_type") != "CoordinatorAgent":
                    continue
                try:
                    payload = json.loads(
                        str(item.get("content", ""))
                        .replace("```json", "")
                        .replace("```", "")
                        .strip()
                    )
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(payload, dict) and payload.get("title"):
                    coordinator_title = str(payload["title"]).strip()
                    break
        return " ".join((first_user or coordinator_title or task_id).split())[:80]

    async def list_task_summaries(self) -> list[dict]:
        """从消息档案构建可跨进程重启恢复的任务索引。"""
        summaries: list[dict] = []
        for file_path in self.messages_dir.glob("*.json"):
            task_id = file_path.stem
            messages = await self.load_task_messages(task_id)
            if not messages:
                continue
            summaries.append(
                {
                    "task_id": task_id,
                    "title": self._infer_title(task_id, messages),
                    "updated_at": datetime.fromtimestamp(
                        file_path.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "status": self.task_status_from_messages(messages),
                    "message_count": len(messages),
                }
            )
        return sorted(summaries, key=lambda s: str(s["updated_at"]), reverse=True)


redis_manager = RedisManager()
