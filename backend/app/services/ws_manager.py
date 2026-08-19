"""WebSocket 连接注册与消息分发。"""

import asyncio

from fastapi import WebSocket


class WebSocketManager:
    """登记所有活跃连接，支持单发与广播。

    连接集合只在本类内部维护，外部通过方法操作。
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket) -> None:
        await websocket.send_text(message)

    async def send_personal_message_json(self, message: dict, websocket: WebSocket) -> None:
        await websocket.send_json(message)

    async def broadcast(self, message: str) -> None:
        # 快照当前连接，发送期间新增/移除的连接不影响本轮广播
        targets = list(self._connections)
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                self._connections.discard(ws)

    @property
    def active_connections(self) -> list[WebSocket]:
        return list(self._connections)


ws_manager = WebSocketManager()
