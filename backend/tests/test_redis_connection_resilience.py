"""Regression tests for Redis/WebSocket connection exhaustion."""

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
from app.routers.ws_router import websocket_endpoint
from app.schemas.enums import AgentType
from app.schemas.response import SystemMessage
from app.services.redis_manager import RedisManager


class _FakeWebSocket:
    client_state = WebSocketState.CONNECTED
    application_state = WebSocketState.CONNECTED
    client = SimpleNamespace(host="127.0.0.1", port=12345)


class _DisconnectingWebSocket(_FakeWebSocket):
    async def receive(self):
        return {"type": "websocket.disconnect", "code": 1000}


class RedisConnectionResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_releases_pubsub_connection_after_disconnect(self) -> None:
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = WebSocketDisconnect(code=1000)
        websocket = _FakeWebSocket()

        with (
            patch(
                "app.routers.ws_router.redis_manager.task_exists",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.routers.ws_router.redis_manager.subscribe_to_task",
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

        pubsub.unsubscribe.assert_awaited_once()
        pubsub.aclose.assert_awaited_once()

    async def test_client_disconnect_is_consumed_and_releases_pubsub(self) -> None:
        pubsub = AsyncMock()
        pubsub.get_message.return_value = None
        websocket = _DisconnectingWebSocket()

        with (
            patch(
                "app.routers.ws_router.redis_manager.task_exists",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.routers.ws_router.redis_manager.subscribe_to_task",
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

        pubsub.unsubscribe.assert_awaited_once()
        pubsub.aclose.assert_awaited_once()

    async def test_websocket_accepts_task_from_durable_history_after_restart(
        self,
    ) -> None:
        pubsub = AsyncMock()
        pubsub.get_message.side_effect = WebSocketDisconnect(code=1000)
        websocket = _FakeWebSocket()

        with (
            patch(
                "app.routers.ws_router.redis_manager.task_exists",
                new=AsyncMock(return_value=True),
            ) as task_exists,
            patch(
                "app.routers.ws_router.redis_manager.get_client",
                new=AsyncMock(
                    side_effect=AssertionError(
                        "WebSocket existence checks must not require a Redis key"
                    )
                ),
            ),
            patch(
                "app.routers.ws_router.redis_manager.subscribe_to_task",
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

    async def test_publish_keeps_durable_message_when_realtime_channel_is_full(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RedisManager(messages_dir=Path(tmp))
            redis_client = AsyncMock()
            redis_client.publish.side_effect = ConnectionError("Too many connections")

            with patch.object(
                manager,
                "get_client",
                new=AsyncMock(return_value=redis_client),
            ):
                await manager.publish_message(
                    "20260716-052635-87215f64",
                    SystemMessage(content="result persisted"),
                )

            messages = await manager.load_task_messages(
                "20260716-052635-87215f64"
            )
            self.assertEqual(messages[-1]["content"], "result persisted")

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
