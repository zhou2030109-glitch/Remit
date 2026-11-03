"""Regression tests for task bus / WebSocket resource release and bounds."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.config.setting import ApiType
from app.core.llm.llm import LLM
from app.core.llm.types import StandardResponse
from app.routers import common_router
from app.routers.ws_router import websocket_endpoint
from app.schemas.enums import AgentType
from app.schemas.response import SystemMessage
from app.services.task_bus import TaskBus


class _FakeWebSocket:
    client_state = WebSocketState.CONNECTED
    application_state = WebSocketState.CONNECTED
    client = SimpleNamespace(host="127.0.0.1", port=12345)


class _DisconnectingWebSocket(_FakeWebSocket):
    async def receive(self):
        return {"type": "websocket.disconnect", "code": 1000}


class TaskBusResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_status_bounds_a_slow_archive_probe(self) -> None:
        """档案探测卡住时状态接口仍应及时返回，而不是让前端超时。"""

        async def slow_archive(*_args):
            await asyncio.sleep(1)

        with (
            patch.object(common_router.task_bus, "_archive_call", new=slow_archive),
            patch.object(common_router, "_STATUS_TIMEOUT_SECONDS", 0.01),
        ):
            status = await asyncio.wait_for(
                common_router.get_service_status(),
                timeout=0.2,
            )

        self.assertEqual(status["backend"]["status"], "running")
        self.assertEqual(status["task_bus"]["status"], "error")

    async def test_websocket_releases_pubsub_connection_after_disconnect(self) -> None:
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = WebSocketDisconnect(code=1000)
        websocket = _FakeWebSocket()

        with (
            patch(
                "app.routers.ws_router.task_bus.task_exists",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.routers.ws_router.task_bus.subscribe_to_task",
                new=AsyncMock(return_value=pubsub),
            ),
            patch(
                "app.routers.ws_router.ws_manager.connect",
                new=AsyncMock(),
            ),
            patch(
                "app.routers.ws_router.ws_manager.disconnect",
                new=MagicMock(),
            ),
        ):
            await websocket_endpoint(websocket, "20260716-052635-87215f64")

        pubsub.aclose.assert_awaited_once()

    async def test_client_disconnect_is_consumed_and_releases_pubsub(self) -> None:
        pubsub = AsyncMock()
        pubsub.get_message.return_value = None
        websocket = _DisconnectingWebSocket()

        with (
            patch(
                "app.routers.ws_router.task_bus.task_exists",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.routers.ws_router.task_bus.subscribe_to_task",
                new=AsyncMock(return_value=pubsub),
            ),
            patch(
                "app.routers.ws_router.ws_manager.connect",
                new=AsyncMock(),
            ),
            patch(
                "app.routers.ws_router.ws_manager.disconnect",
                new=MagicMock(),
            ),
        ):
            await asyncio.wait_for(
                websocket_endpoint(
                    websocket,
                    "20260716-052635-87215f64",
                ),
                timeout=0.5,
            )

        pubsub.aclose.assert_awaited_once()

    async def test_websocket_accepts_task_from_durable_history_after_restart(
        self,
    ) -> None:
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = WebSocketDisconnect(code=1000)
        websocket = _FakeWebSocket()

        with (
            patch(
                "app.routers.ws_router.task_bus.task_exists",
                new=AsyncMock(return_value=True),
            ) as task_exists,
            patch(
                "app.routers.ws_router.task_bus.subscribe_to_task",
                new=AsyncMock(return_value=pubsub),
            ),
            patch(
                "app.routers.ws_router.ws_manager.connect",
                new=AsyncMock(),
            ),
            patch(
                "app.routers.ws_router.ws_manager.disconnect",
                new=MagicMock(),
            ),
        ):
            await websocket_endpoint(websocket, "20260716-052635-87215f64")

        task_exists.assert_awaited_once_with("20260716-052635-87215f64")
        pubsub.aclose.assert_awaited_once()

    async def test_full_subscription_keeps_durable_messages_and_newest_payloads(
        self,
    ) -> None:
        task_id = "20260716-052635-87215f64"
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("app.services.task_bus._SUBSCRIPTION_BUFFER", 2),
        ):
            manager = TaskBus(messages_dir=Path(tmp))
            subscription = await manager.subscribe_to_task(task_id)
            for index in range(3):
                await manager.publish_message(
                    task_id, SystemMessage(content=f"result {index}")
                )
            messages = await manager.load_task_messages(task_id)
            self.assertEqual(
                [m["content"] for m in messages], ["result 0", "result 1", "result 2"]
            )
            buffered = [(await subscription.get_message())["data"] for _ in range(2)]
            self.assertIn("result 1", buffered[0])
            self.assertIn("result 2", buffered[1])
            self.assertIsNone(await subscription.get_message())

    async def test_successful_model_response_is_not_retried_when_publish_fails(
        self,
    ) -> None:
        response = StandardResponse(content="model completed")

        class SuccessfulProvider:
            def __init__(self) -> None:
                self.calls = 0

            async def call(self, **kwargs):
                self.calls += 1
                return response

        llm = LLM(
            api_type=ApiType.OPENAI_RESPONSES,
            api_key="test-key",
            model="gpt-5.6-sol",
        )
        provider = SuccessfulProvider()
        llm.provider = provider

        with patch.object(
            llm,
            "send_message",
            new=AsyncMock(side_effect=ConnectionError("Too many connections")),
        ):
            result = await llm.chat(
                history=[],
                max_retries=3,
                retry_delay=0,
                agent_name=AgentType.MODELER,
            )

        self.assertIs(result, response)
        self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
