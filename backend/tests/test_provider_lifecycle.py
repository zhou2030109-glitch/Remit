import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.config.setting import settings
from app.core.llm.llm import LLM, _is_retryable_gateway_error, _retry_delay_seconds
from app.core.llm.providers.anthropic import AnthropicProvider
from app.core.llm.providers.base import close_client
from app.core.llm.providers.openai_chat import OpenAIChatProvider
from app.core.llm.providers.openai_responses import OpenAIResponsesProvider
from app.services import conversation_errors, task_failure


@pytest.mark.parametrize(
    "provider_name", ["openai_chat", "openai_responses", "anthropic"]
)
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
@pytest.mark.parametrize("close_fails", [False, True])
def test_provider_closes_transport_on_every_outcome(
    monkeypatch, provider_name, outcome, close_fails
):
    async def run():
        entered = asyncio.Event()

        async def respond(request):
            entered.set()
            if outcome == "cancel":
                await asyncio.Event().wait()
            if outcome == "error":
                return httpx.Response(503, json={"error": {"message": "unavailable"}})
            if provider_name == "openai_chat":
                body = {
                    "id": "chat-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "fixture",
                    "usage": None,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ],
                }
            elif provider_name == "openai_responses":
                body = {
                    "id": "resp-test",
                    "object": "response",
                    "created_at": 0,
                    "model": "fixture",
                    "status": "completed",
                    "usage": None,
                    "output": [
                        {
                            "type": "message",
                            "id": "msg-test",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {"type": "output_text", "text": "ok", "annotations": []}
                            ],
                        }
                    ],
                }
            else:
                body = {
                    "id": "msg-test",
                    "type": "message",
                    "role": "assistant",
                    "model": "fixture",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            return httpx.Response(200, json=body)

        transport = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        client_class = AsyncAnthropic if provider_name == "anthropic" else AsyncOpenAI
        provider = {
            "openai_chat": OpenAIChatProvider,
            "openai_responses": OpenAIResponsesProvider,
            "anthropic": AnthropicProvider,
        }[provider_name]()
        if provider_name == "openai_responses":
            provider._use_non_streaming = True
        client = client_class(api_key="fixture", max_retries=0, http_client=transport)
        original_close = client.close
        if close_fails:

            async def failing_close():
                await original_close()
                raise httpx.ReadError("fixture private cleanup detail")

            monkeypatch.setattr(client, "close", failing_close)
        monkeypatch.setattr(
            f"app.core.llm.providers.{provider_name}.{client_class.__name__}",
            lambda **_: client,
        )
        try:
            task = asyncio.create_task(
                provider.call(
                    messages=[{"role": "user", "content": "test"}],
                    model="fixture",
                    api_key="fixture",
                )
            )
            if outcome == "cancel":
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == "error":
                with pytest.raises(Exception, match="503"):
                    await task
            else:
                assert (await task).content == "ok"
            assert transport.is_closed
        finally:
            await original_close()

    asyncio.run(run())


def test_close_does_not_swallow_cancellation():
    client = SimpleNamespace(close=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(close_client(client))


def test_cleanup_diagnostics_do_not_expose_upstream_detail(monkeypatch):
    warning = Mock()
    monkeypatch.setattr("app.core.llm.providers.base.logger.warning", warning)
    client = SimpleNamespace(
        close=AsyncMock(side_effect=httpx.ReadError("fixture private upstream detail"))
    )
    asyncio.run(close_client(client))
    assert "ReadError" in str(warning.call_args)
    assert "fixture private upstream detail" not in str(warning.call_args)


def test_stream_cleanup_failure_preserves_completed_response(monkeypatch):
    async def run():
        terminal = {
            "type": "response.completed",
            "response": {
                "id": "resp-test",
                "status": "completed",
                "usage": None,
                "output": [
                    {
                        "type": "message",
                        "id": "msg-test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "ok", "annotations": []}
                        ],
                    }
                ],
            },
        }

        class BrokenCloseStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield ("data: " + json.dumps(terminal) + "\n\n").encode()

            async def aclose(self):
                raise httpx.ReadError("fixture private stream cleanup detail")

        transport = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=BrokenCloseStream(),
                )
            )
        )
        client = AsyncOpenAI(api_key="fixture", max_retries=0, http_client=transport)
        monkeypatch.setattr(
            "app.core.llm.providers.openai_responses.AsyncOpenAI", lambda **_: client
        )
        result = await OpenAIResponsesProvider().call(
            messages=[], model="fixture", api_key="fixture"
        )
        assert result.content == "ok"
        assert client.is_closed()

    asyncio.run(run())


def test_responses_invalid_payload_does_not_create_transport(monkeypatch):
    create_client = Mock()
    monkeypatch.setattr(
        "app.core.llm.providers.openai_responses.AsyncOpenAI", create_client
    )
    with pytest.raises(KeyError):
        asyncio.run(
            OpenAIResponsesProvider().call(
                messages=[{"role": "user", "content": "test"}],
                model="fixture",
                api_key="fixture",
                tools=[{"type": "function"}],
            )
        )
    create_client.assert_not_called()


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_cleanup_failure_does_not_retry_or_change_call_outcome(monkeypatch, outcome):
    response = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None, refusal=None),
                finish_reason="stop",
            )
        ],
    )
    create = AsyncMock(return_value=response)
    original_error = None
    if outcome == "error":
        request = httpx.Request("POST", "https://fixture.invalid/chat/completions")
        original_error = httpx.HTTPStatusError(
            "fixture authentication rejected",
            request=request,
            response=httpx.Response(401, request=request),
        )
        create.side_effect = original_error
    elif outcome == "cancel":
        create.side_effect = asyncio.CancelledError
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        close=AsyncMock(side_effect=httpx.ReadError("fixture private cleanup detail")),
    )
    monkeypatch.setattr(
        "app.core.llm.providers.openai_chat.AsyncOpenAI", lambda **_: client
    )
    monkeypatch.setattr(settings, "FALLBACK_ENABLED", False)
    monkeypatch.setattr("app.services.team_state.context_for", lambda *_: "")
    monkeypatch.setattr("app.services.competitions.context_for", lambda *_: "")
    monkeypatch.setattr("app.core.llm.llm._retry_delay_seconds", lambda *_: 0)
    model = LLM(api_key="fixture", model="fixture")
    if outcome == "error":
        with pytest.raises(httpx.HTTPStatusError) as caught:
            asyncio.run(model.chat(publish=False, max_retries=2, retry_delay=0))
        assert caught.value is original_error
    elif outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(model.chat(publish=False, max_retries=2, retry_delay=0))
    else:
        result = asyncio.run(model.chat(publish=False, max_retries=2, retry_delay=0))
        assert result.content == "ok"
    create.assert_awaited_once()
    client.close.assert_awaited_once()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_native_http_configuration_error_is_not_retried(monkeypatch, status):
    request = httpx.Request("POST", "https://fixture.invalid/models/test")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError("rejected", request=request, response=response)
    model = LLM(api_key="fixture", model="fixture")
    model.provider.call = AsyncMock(side_effect=error)
    monkeypatch.setattr(settings, "FALLBACK_ENABLED", False)
    monkeypatch.setattr("app.services.team_state.context_for", lambda *_: "")
    monkeypatch.setattr("app.services.competitions.context_for", lambda *_: "")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(model.chat(publish=False, max_retries=3, retry_delay=0))
    assert model.provider.call.await_count == 1


def test_native_http_rate_limit_uses_server_retry_delay():
    request = httpx.Request("POST", "https://fixture.invalid/models/test")
    response = httpx.Response(429, request=request, headers={"retry-after": "12"})
    error = httpx.HTTPStatusError("limited", request=request, response=response)
    assert _is_retryable_gateway_error(error)
    assert _retry_delay_seconds(error, 1, 0) == 12


@pytest.mark.parametrize(
    "status,code,hint",
    [
        (401, "AUTHENTICATION", "密钥"),
        (429, "RATE_LIMIT", "限流"),
        (503, "TRANSPORT", "暂时不可用"),
    ],
)
def test_native_http_error_has_actionable_diagnostics(tmp_path, status, code, hint):
    request = httpx.Request("POST", "https://fixture.invalid/models/test")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError(
        "private-upstream-detail", request=request, response=response
    )
    failure = task_failure.describe(error, tmp_path, retrying=False, attempts=1)
    assert failure["code"] == code
    assert hint in conversation_errors.explain(error)
    assert "private-upstream-detail" not in str(failure)
