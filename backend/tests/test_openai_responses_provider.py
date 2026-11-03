import unittest
import json
import httpx
from openai import AsyncOpenAI
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config.setting import ApiType, effective_api_timeout_seconds, settings
from app.core.llm.llm import LLM
from app.core.llm.providers.openai_responses import OpenAIResponsesProvider
from app.core.llm.types import StandardResponse
from app.core.llm.errors import (
    NonRetryableLLMError,
    ResponseStreamInterruptedError,
    TransientLLMError,
)


class EventStream:
    def __init__(self, events, final=None):
        self.events = iter(events)
        self.final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def close(self):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.events)
        except StopIteration:
            raise StopAsyncIteration

    async def get_final_response(self):
        if self.final is None:
            raise RuntimeError("Didn't receive a `response.completed` event.")
        return self.final


class OpenAIResponsesProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_tool_choice_reaches_http_as_required(self):
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            self.assertEqual(payload["tool_choice"], "required")
            terminal = {
                "type": "response.completed",
                "response": {
                    "id": "resp_tool",
                    "status": "completed",
                    "usage": None,
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_tool",
                            "name": "probe",
                            "arguments": "{}",
                        }
                    ],
                },
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="data: " + json.dumps(terminal) + "\n\n",
            )

        client = AsyncOpenAI(
            api_key="test-key",
            base_url="https://example.invalid/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        with patch(
            "app.core.llm.providers.openai_responses.AsyncOpenAI", return_value=client
        ):
            result = await OpenAIResponsesProvider().call(
                messages=[{"role": "user", "content": "Call probe"}],
                model="test",
                api_key="test",
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "probe",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
                tool_choice="required",
            )
        self.assertEqual(len(requests), 1)
        self.assertEqual(result.tool_calls[0].name, "probe")

    async def test_real_sdk_preserves_untyped_gateway_error_without_retries(self):
        for event in [
            {
                "code": "InvalidParameter",
                "message": "Missing required parameter: 'workspaceid'. private-key-secret",
            },
            {
                "error": {
                    "code": "InvalidParameter",
                    "message": "Missing required parameter: 'workspaceid'. private-key-secret",
                }
            },
        ]:
            with self.subTest(event=event):
                requests = []

                def respond(request):
                    requests.append(request)
                    if not json.loads(request.content).get("stream"):
                        return httpx.Response(200, json=event)
                    return httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        content="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
                    )

                client = AsyncOpenAI(
                    api_key="test-key",
                    base_url="https://example.invalid/v1",
                    http_client=httpx.AsyncClient(
                        transport=httpx.MockTransport(respond)
                    ),
                )
                llm = LLM(
                    api_type=ApiType.OPENAI_RESPONSES,
                    api_key="test-key",
                    model="test-model",
                )
                with patch(
                    "app.core.llm.providers.openai_responses.AsyncOpenAI",
                    return_value=client,
                ):
                    with self.assertRaises(NonRetryableLLMError) as caught:
                        await llm.chat(history=[], publish=False)
                self.assertIn("workspaceid", str(caught.exception))
                self.assertNotIn("private-key-secret", str(caught.exception))
                self.assertEqual(len(requests), 1 if "error" in event else 2)
                self.assertTrue(client.is_closed())

    async def test_untyped_stream_error_falls_back_once_and_retains_mode(self):
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if payload.get("stream"):
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content="data: "
                    + json.dumps(
                        {
                            "code": "InvalidParameter",
                            "message": "Missing required parameter: workspaceid",
                        }
                    )
                    + "\n\n",
                )
            return httpx.Response(
                200,
                json={
                    "id": "resp_ok",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_ok",
                            "name": "execute_code",
                            "arguments": "{}",
                        }
                    ],
                    "usage": None,
                },
            )

        def make_client(**kwargs):
            return AsyncOpenAI(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
            )

        provider = OpenAIResponsesProvider()
        with patch(
            "app.core.llm.providers.openai_responses.AsyncOpenAI",
            side_effect=make_client,
        ):
            for _ in range(2):
                result = await provider.call(messages=[], model="test", api_key="test")
                self.assertEqual(result.tool_calls[0].id, "call_ok")
        self.assertEqual(
            [p.get("stream", False) for p in requests], [True, False, False]
        )

    async def test_real_sdk_terminal_tool_call_is_preserved_without_created_event(self):
        terminal = {
            "type": "response.completed",
            "response": {
                "id": "resp_test",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_test",
                        "call_id": "call_test",
                        "name": "execute_code",
                        "arguments": '{"code":"disp(1)"}',
                    }
                ],
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            },
        }
        client = AsyncOpenAI(
            api_key="test-key",
            base_url="https://example.invalid/v1",
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        content="data: " + json.dumps(terminal) + "\n\n",
                    )
                )
            ),
        )
        with patch(
            "app.core.llm.providers.openai_responses.AsyncOpenAI", return_value=client
        ):
            result = await OpenAIResponsesProvider().call(
                messages=[], model="test", api_key="test"
            )
        self.assertEqual(result.tool_calls[0].id, "call_test")
        self.assertEqual(
            json.loads(result.tool_calls[0].arguments), {"code": "disp(1)"}
        )
        self.assertEqual(result.usage.completion_tokens, 3)

    async def test_incomplete_terminal_preserves_budget_reason(self):
        response = SimpleNamespace(
            output=[],
            usage=None,
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        )
        client = SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(
                    return_value=EventStream(
                        [SimpleNamespace(type="response.incomplete", response=response)]
                    )
                )
            )
        )
        result = await OpenAIResponsesProvider._stream_collect(client, {}, None)
        self.assertEqual(
            OpenAIResponsesProvider._normalize(result).finish_reason,
            "max_output_tokens",
        )

    async def test_missing_terminal_discards_partial_and_retries_without_stream(self):
        partial = SimpleNamespace(
            type="response.output_text.delta", delta="incomplete JSON"
        )
        complete = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="output_text", text='{"ok":true}')],
                )
            ],
            usage=None,
            status="completed",
        )
        client = SimpleNamespace(
            close=AsyncMock(),
            responses=SimpleNamespace(
                create=AsyncMock(side_effect=[EventStream([partial]), complete]),
            ),
        )
        llm = LLM(
            api_type=ApiType.OPENAI_RESPONSES, api_key="test-key", model="test-model"
        )
        with (
            patch(
                "app.core.llm.providers.openai_responses.AsyncOpenAI",
                return_value=client,
            ),
            patch("app.core.llm.llm.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await llm.chat(history=[], publish=False)
        self.assertEqual(response.content, '{"ok":true}')
        self.assertEqual(client.responses.create.await_count, 2)
        self.assertTrue(client.responses.create.await_args_list[0].kwargs["stream"])
        self.assertNotIn("stream", client.responses.create.await_args_list[1].kwargs)
        self.assertEqual(client.close.await_count, 2)

    async def test_missing_terminal_reports_event_without_exposing_partial_text(self):
        client = SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(
                    return_value=EventStream(
                        [
                            SimpleNamespace(
                                type="response.output_text.delta",
                                delta="private partial",
                            )
                        ]
                    )
                )
            )
        )
        with self.assertRaises(ResponseStreamInterruptedError) as raised:
            await OpenAIResponsesProvider._stream_collect(client, {}, None)
        self.assertIn("response.output_text.delta", str(raised.exception))
        self.assertNotIn("private partial", str(raised.exception))

    async def test_non_streaming_fallback_obeys_hard_retry_limit(self):
        client = SimpleNamespace(
            close=AsyncMock(),
            responses=SimpleNamespace(
                create=AsyncMock(
                    side_effect=[
                        EventStream([]),
                        TransientLLMError("upstream unavailable"),
                        TransientLLMError("upstream unavailable"),
                    ]
                ),
            ),
        )
        llm = LLM(
            api_type=ApiType.OPENAI_RESPONSES, api_key="test-key", model="test-model"
        )
        with (
            patch(
                "app.core.llm.providers.openai_responses.AsyncOpenAI",
                return_value=client,
            ),
            patch("app.core.llm.llm.asyncio.sleep", new_callable=AsyncMock),
            patch.object(settings, "MAX_RETRIES", 2),
            patch.object(settings, "GATEWAY_MAX_RETRIES", 4),
            patch.object(settings, "LLM_HARD_RETRY_LIMIT", 3),
        ):
            with self.assertRaisesRegex(TransientLLMError, "upstream unavailable"):
                await llm.chat(history=[], publish=False)
        self.assertEqual(client.responses.create.await_count, 3)
        self.assertEqual(client.close.await_count, 3)

    async def test_failed_terminal_preserves_service_failure_class(self):
        for code, expected in [
            ("server_error", TransientLLMError),
            ("invalid_api_key", NonRetryableLLMError),
        ]:
            response = SimpleNamespace(
                status="failed", error=SimpleNamespace(code=code)
            )
            client = SimpleNamespace(
                responses=SimpleNamespace(
                    create=AsyncMock(
                        return_value=EventStream(
                            [SimpleNamespace(type="response.failed", response=response)]
                        )
                    )
                )
            )
            result = await OpenAIResponsesProvider._stream_collect(client, {}, None)
            with self.assertRaises(expected):
                OpenAIResponsesProvider._normalize(result)

    async def test_per_call_output_budget_overrides_role_default(self):
        class CapturingProvider:
            kwargs = None

            async def call(self, **kwargs):
                self.kwargs = kwargs
                return StandardResponse(content="{}")

        llm = LLM(
            api_key="test-key",
            model="test-model",
            max_tokens=8192,
        )
        provider = CapturingProvider()
        llm.provider = provider

        await llm.chat(history=[], max_tokens=16384, publish=False)

        self.assertEqual(provider.kwargs["max_tokens"], 16384)

    def test_incomplete_response_exposes_truncation_reason(self):
        response = SimpleNamespace(
            output=[],
            usage=None,
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        )

        normalized = OpenAIResponsesProvider._normalize(response)

        self.assertEqual(normalized.finish_reason, "max_output_tokens")

    async def test_forwards_reasoning_effort_and_disables_storage(self):
        final_response = SimpleNamespace(output=[], usage=None)

        fake_client = SimpleNamespace(
            close=AsyncMock(),
            responses=SimpleNamespace(
                create=AsyncMock(
                    return_value=EventStream(
                        [
                            SimpleNamespace(
                                type="response.completed", response=final_response
                            )
                        ]
                    )
                ),
            ),
        )

        with (
            patch(
                "app.core.llm.providers.openai_responses.AsyncOpenAI",
                return_value=fake_client,
            ) as client_factory,
            patch.object(settings, "MODEL_REASONING_EFFORT", "xhigh", create=True),
            patch.object(settings, "DISABLE_RESPONSE_STORAGE", True, create=True),
        ):
            await OpenAIResponsesProvider().call(
                messages=[{"role": "user", "content": "hello"}],
                model="gpt-5.6-sol",
                api_key="test-key",
                base_url="https://example.invalid/",
            )

        request = fake_client.responses.create.call_args.kwargs
        fake_client.responses.create.assert_awaited_once()
        self.assertTrue(request["stream"])
        self.assertEqual(request["reasoning"], {"effort": "xhigh"})
        self.assertFalse(request["store"])
        self.assertEqual(
            client_factory.call_args.kwargs["default_headers"]["User-Agent"],
            "Remit/1.0",
        )
        self.assertEqual(
            client_factory.call_args.kwargs["timeout"],
            effective_api_timeout_seconds(),
        )

    async def test_default_retry_limit_uses_project_setting(self):
        class FailingProvider:
            def __init__(self):
                self.calls = 0

            async def call(self, **kwargs):
                self.calls += 1
                raise RuntimeError("gateway unavailable")

        llm = LLM(
            api_type=ApiType.OPENAI_RESPONSES,
            api_key="test-key",
            model="gpt-5.6-sol",
        )
        provider = FailingProvider()
        llm.provider = provider

        with patch.object(settings, "MAX_RETRIES", 2):
            with self.assertRaisesRegex(RuntimeError, "gateway unavailable"):
                await llm.chat(history=[], retry_delay=0)

        self.assertEqual(provider.calls, 2)

    async def test_explicit_attempt_limit_caps_gateway_retries(self):
        class GatewayError(RuntimeError):
            status_code = 500

        for limit in (1, 2):
            with self.subTest(limit=limit):
                llm = LLM(api_type=ApiType.OPENAI_RESPONSES, api_key="fixture", model="fixture")
                provider = SimpleNamespace(call=AsyncMock(side_effect=GatewayError("upstream error")))
                llm.provider = provider
                with (
                    patch.object(settings, "GATEWAY_MAX_RETRIES", 4),
                    patch.object(settings, "LLM_HARD_RETRY_LIMIT", 4),
                    patch.object(settings, "FALLBACK_ENABLED", False),
                    patch("app.core.llm.llm.asyncio.sleep", new_callable=AsyncMock) as sleep,
                ):
                    with self.assertRaises(GatewayError):
                        await llm.chat(history=[], publish=False, max_retries=limit)
                self.assertEqual(provider.call.await_count, limit)
                self.assertEqual(sleep.await_count, limit - 1)

    async def test_gateway_retry_uses_extended_limit_and_honors_retry_after(self):
        class GatewayError(RuntimeError):
            status_code = 502
            body = {"retry_after": 60}

        class FlakyProvider:
            def __init__(self):
                self.calls = 0

            async def call(self, **kwargs):
                self.calls += 1
                raise GatewayError("bad gateway")

        llm = LLM(
            api_type=ApiType.OPENAI_RESPONSES,
            api_key="test-key",
            model="gpt-5.6-sol",
        )
        provider = FlakyProvider()
        llm.provider = provider

        with (
            patch.object(settings, "MAX_RETRIES", 2),
            patch.object(settings, "GATEWAY_MAX_RETRIES", 4, create=True),
            patch("app.core.llm.llm.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            with self.assertRaisesRegex(GatewayError, "bad gateway"):
                await llm.chat(history=[])

        self.assertEqual(provider.calls, 4)
        self.assertEqual(sleep.await_count, 3)
        sleep.assert_awaited_with(60.0)


if __name__ == "__main__":
    unittest.main()
