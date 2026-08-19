"""OpenAI Chat Completions 端点（/v1/chat/completions）。"""

from typing import Any

from openai import AsyncOpenAI

from app.core.llm.providers.base import BaseProvider, DeltaCallback
from app.core.llm.types import StandardResponse, ToolCall, Usage


class OpenAIChatProvider(BaseProvider):
    """覆盖 OpenAI 及兼容该端点的中转服务。"""

    async def call(
        self,
        messages: list[dict],
        model: str,
        api_key: str,
        base_url: str | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        on_delta: DeltaCallback | None = None,
        reasoning_effort: str | None = None,
    ) -> StandardResponse:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        payload = self._build_payload(
            messages, model, tools, tool_choice, max_tokens, top_p
        )
        response = await client.chat.completions.create(**payload)
        return self._normalize(response)

    @staticmethod
    def _build_payload(
        messages: list[dict],
        model: str,
        tools: list[dict] | None,
        tool_choice: str | None,
        max_tokens: int | None,
        top_p: float | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if top_p is not None:
            payload["top_p"] = top_p
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        return payload

    @staticmethod
    def _normalize(response: Any) -> StandardResponse:
        choice = response.choices[0]
        message = choice.message
        return StandardResponse(
            content=message.content,
            reasoning_content=getattr(message, "reasoning_content", None),
            finish_reason=choice.finish_reason,
            tool_calls=[
                ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
                for tc in message.tool_calls or []
            ],
            usage=Usage(
                prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
                completion_tokens=response.usage.completion_tokens
                if response.usage
                else 0,
            ),
        )
