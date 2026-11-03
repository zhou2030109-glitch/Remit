"""WebSocket 历史重放、进程内实时广播与订阅故障补偿行为测试。"""

import asyncio

from starlette.websockets import WebSocketState

from app.routers import ws_router
from app.schemas.response import (
    ActivityMessage,
    ApprovalMessage,
    SystemMessage,
    UserMessage,
)
from app.services.task_bus import TaskBus
from app.services.ws_manager import WebSocketManager


class SocketProbe:
    def __init__(self, after=0):
        self.query_params = {"after": str(after)}
        self.client_state = WebSocketState.CONNECTED
        self.application_state = WebSocketState.CONNECTED
        self.messages = asyncio.Queue()
        self.disconnected = asyncio.Event()

    async def accept(self):
        pass

    async def send_json(self, payload):
        await self.messages.put(payload)

    async def receive(self):
        await self.disconnected.wait()
        return {"type": "websocket.disconnect", "code": 1000}


def test_replay_live_delivery_and_missing_broadcast(tmp_path, monkeypatch):
    async def scenario():
        bus = TaskBus(tmp_path / "history")
        monkeypatch.setattr(ws_router, "task_bus", bus)
        sockets = WebSocketManager()
        monkeypatch.setattr(ws_router, "ws_manager", sockets)
        first = UserMessage(content="synthetic task")
        await bus.publish_message("task", first)
        approval = ApprovalMessage(checkpoint_id="approval-1", node_id="analysis")
        await bus.publish_message("task", approval)
        ws = SocketProbe(after=first.sequence)
        endpoint = asyncio.create_task(ws_router.websocket_endpoint(ws, "task"))
        try:
            replay = await asyncio.wait_for(ws.messages.get(), 3)
            assert replay["id"] == approval.id
            assert replay["task_status"] == "awaiting_approval"
            running = SystemMessage(content="new wording", task_status="running")
            await bus.publish_message("task", running)
            live = await asyncio.wait_for(ws.messages.get(), 3)
            assert live["id"] == running.id
            assert live["sequence"] > replay["sequence"]
            activity = ActivityMessage(content="transient progress")
            await bus.publish_message("task", activity)
            transient = await asyncio.wait_for(ws.messages.get(), 3)
            assert transient["id"] == activity.id
            # 模拟已落盘但广播丢失：活动连接应从持久化补偿恢复终态。
            finished = SystemMessage(
                content="different wording", task_status="completed"
            )
            await bus._save_message_to_file("task", finished)
            recovered = await asyncio.wait_for(ws.messages.get(), 3)
            assert recovered["id"] == finished.id
            assert recovered["task_status"] == "completed"
            assert ws.messages.empty()
        finally:
            ws.disconnected.set()
            await asyncio.wait_for(endpoint, 3)
            assert sockets.active_connections == []
            assert not bus._subscriptions
            await bus.close()

    asyncio.run(scenario())


def test_subscription_failure_still_replays_durable_approval(tmp_path, monkeypatch):
    async def scenario():
        bus = TaskBus(tmp_path / "offline")
        await bus._save_message_to_file(
            "task", ApprovalMessage(checkpoint_id="offline")
        )

        async def unavailable(_task_id):
            raise RuntimeError("subscription unavailable")

        monkeypatch.setattr(bus, "subscribe_to_task", unavailable)
        monkeypatch.setattr(ws_router, "task_bus", bus)
        sockets = WebSocketManager()
        monkeypatch.setattr(ws_router, "ws_manager", sockets)
        ws = SocketProbe()
        endpoint = asyncio.create_task(ws_router.websocket_endpoint(ws, "task"))
        try:
            payload = await asyncio.wait_for(ws.messages.get(), 3)
            assert payload["checkpoint_id"] == "offline"
        finally:
            ws.disconnected.set()
            await asyncio.wait_for(endpoint, 3)
            assert not sockets.active_connections
            await bus.close()

    asyncio.run(scenario())
