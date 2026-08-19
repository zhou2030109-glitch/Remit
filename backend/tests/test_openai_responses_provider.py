import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.setting import settings
from app.config.setting import ApiType
from app.core.llm.llm import LLM
from app.core.llm.providers.openai_responses import OpenAIResponsesProvider


class OpenAIResponsesProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_reasoning_effort_and_disables_storage(self):
        final_response = SimpleNamespace(output=[], usage=None)

        class FakeStream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def get_final_response(self):
                return final_response

        fake_client = SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(side_effect=AssertionError("non-streaming call")),
                stream=MagicMock(return_value=FakeStream()),
            )
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

        request = fake_client.responses.stream.call_args.kwargs
        fake_client.responses.create.assert_not_awaited()
        fake_client.responses.stream.assert_called_once()
        self.assertEqual(request["reasoning"], {"effort": "xhigh"})
        self.assertFalse(request["store"])
        self.assertEqual(
            client_factory.call_args.kwargs["default_headers"]["User-Agent"],
            "Remit/1.0",
        )
        self.assertGreaterEqual(
            client_factory.call_args.kwargs["timeout"],
            300,
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
