"""任务实时通道：Redis pub/sub → WebSocket 转发。"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.schemas.response import SystemMessage
from app.services.redis_manager import redis_manager
from app.services.ws_manager import ws_manager
from app.utils.common_utils import ensure_safe_task_id
from app.utils.log_util import logger

router = APIRouter()

_POLL_INTERVAL = 0.1


def _link_closed(websocket: WebSocket) -> bool:
    return WebSocketState.DISCONNECTED in (
        websocket.client_state,
        websocket.application_state,
    )


def _is_closed_send_error(error: Exception) -> bool:
    text = str(error)
    return (
        'Cannot call "send" once a close message has been sent' in text
        or "Unexpected ASGI message 'websocket.send'" in text
    )


async def _watch_client(websocket: WebSocket) -> None:
    """空转消费 ASGI 事件，让客户端被动关闭可被及时察觉。"""
    try:
        while True:
            event = await websocket.receive()
            if event.get("type") == "websocket.disconnect":
                return
    except WebSocketDisconnect:
        return
    except RuntimeError as exc:
        if _is_closed_send_error(exc) or _link_closed(websocket):
            return
        raise


async def _try_send(websocket: WebSocket, payload: dict) -> bool:
    """发送一条消息；连接已死返回 False，其他异常照旧抛出。"""
    try:
        await ws_manager.send_personal_message_json(payload, websocket)
        return True
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        if _is_closed_send_error(exc):
            return False
        raise


@router.websocket("/task/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str) -> None:
    # task_id 进入 Redis 键名与频道名，先做合法性校验
    try:
        safe_task_id = ensure_safe_task_id(task_id)
    except ValueError:
        logger.warning(f"WebSocket task_id 非法: {task_id}")
        await websocket.close(code=1008, reason="Invalid task id")
        return

    logger.info(f"WebSocket 尝试连接 task_id: {safe_task_id}")
    # 持久化消息档案在即视为有效任务；重启后 Redis 临时键可能已消失
    if not await redis_manager.task_exists(safe_task_id):
        logger.warning(f"Task not found: {safe_task_id}")
        await websocket.close(code=1008, reason="Task not found")
        return

    await ws_manager.connect(websocket)
    pubsub = await redis_manager.subscribe_to_task(safe_task_id)
    watcher = asyncio.create_task(_watch_client(websocket))
    channel = f"task:{safe_task_id}:messages"
    logger.info(f"WebSocket connected for task: {safe_task_id}")

    try:
        while True:
            if watcher.done():
                if (err := watcher.exception()) is not None:
                    raise err
                logger.info(f"客户端已断开，停止转发 task_id: {safe_task_id}")
                break
            if _link_closed(websocket):
                break

            try:
                incoming = await pubsub.get_message(ignore_subscribe_messages=True)
            except Exception as exc:
                if _is_closed_send_error(exc) or _link_closed(websocket):
                    break
                logger.error(f"Error in websocket loop: {exc}")
                await asyncio.sleep(1)
                continue

            if incoming:
                try:
                    payload = json.loads(incoming["data"])
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.error(f"Error parsing websocket payload: {exc}")
                    if _link_closed(websocket):
                        break
                    payload = SystemMessage(
                        content="实时消息解析失败，已忽略异常数据。", type="error"
                    ).model_dump()
                if not await _try_send(websocket, payload):
                    logger.info(f"发送失败（连接已关），结束转发 {safe_task_id}")
                    break

            await asyncio.sleep(_POLL_INTERVAL)
    except Exception as exc:
        logger.error(f"WebSocket error: {exc}")
    finally:
        if not watcher.done():
            watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        try:
            await pubsub.unsubscribe(channel)
        except Exception as exc:
            logger.warning(f"WebSocket Redis 退订失败 task_id={safe_task_id}: {exc}")
        try:
            # unsubscribe 不会把 pubsub 专用连接还给连接池，需要显式关闭
            await pubsub.aclose()
        except Exception as exc:
            logger.warning(f"WebSocket Redis 连接关闭失败 task_id={safe_task_id}: {exc}")
        ws_manager.disconnect(websocket)
        logger.info(f"WebSocket connection closed for task: {safe_task_id}")
