"""Anthropic Messages 端点（/v1/messages）。"""

import json
from typing import Any

from anthropic import AsyncAnthropic

from app.core.llm.content import ImageBlock, iter_content_blocks
from app.core.llm.errors import ProviderRefusalError
from app.core.llm.providers.base import BaseProvider, DeltaCallback
from app.core.llm.types import StandardResponse, ToolCall, Usage


class AnthropicProvider(BaseProvider):
    """Claude 系列模型接入。"""

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
        client = AsyncAnthropic(api_key=api_key, base_url=base_url)
        system_prompt, converted = self._convert_messages(messages)

        payload: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": max_tokens or 4096,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if top_p is not None:
            payload["top_p"] = top_p
        if tools:
            payload["tools"] = [self._convert_tool(t) for t in tools]
            if tool_choice:
                payload["tool_choice"] = self._convert_tool_choice(tool_choice)

        response = await client.messages.create(**payload)

        if response.stop_reason == "refusal":
            # 拒答照样计费，用量必须随异常带回，不能显示为 0
            raise ProviderRefusalError(
                "Anthropic",
                model,
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
            )

        return self._normalize(response)

    @staticmethod
    def _normalize(response: Any) -> StandardResponse:
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=json.dumps(block.input))
                )
        return StandardResponse(
            content="".join(texts) or None,
            finish_reason=response.stop_reason,
            tool_calls=calls,
            usage=Usage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
            ),
        )

    # ---- 格式转换：OpenAI messages -> Anthropic messages ----

    def _convert_messages(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """抽出首条 system 消息，其余按 Anthropic 结构转换。"""
        system_prompt: str | None = None
        converted: list[dict] = []

        for msg in messages:
            role = msg.get("role", "user")

            if role == "system" and system_prompt is None:
                system_prompt = msg["content"]
                continue

            if role == "assistant" and msg.get("tool_calls"):
                converted.append(self._assistant_with_tools(msg))
                continue

            if role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_call_id", ""),
                                "content": msg.get("content", ""),
                            }
                        ],
                    }
                )
                continue

            converted.append({**msg, "content": self._convert_content(msg.get("content"))})

        return system_prompt, converted

    @staticmethod
    def _assistant_with_tools(msg: dict) -> dict:
        blocks: list[dict] = []
        if msg.get("content"):
            blocks.append({"type": "text", "text": msg["content"]})
        blocks.extend(
            {
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["function"]["name"],
                "input": json.loads(tc["function"]["arguments"]),
            }
            for tc in msg["tool_calls"]
        )
        return {"role": "assistant", "content": blocks}

    @staticmethod
    def _convert_content(content: object) -> object:
        """多模态内容块转成 Anthropic 的 text / image 结构。"""
        if isinstance(content, str) or content is None:
            return content
        blocks = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.media_type,
                    "data": block.data,
                },
            }
            if isinstance(block, ImageBlock)
            else {"type": "text", "text": block.text}
            for block in iter_content_blocks(content)
        ]
        return blocks or content

    @staticmethod
    def _convert_tool(tool: dict) -> dict:
        if tool.get("type") != "function":
            return tool
        func = tool["function"]
        return {
            "name": func["name"],
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {}),
        }

    @staticmethod
    def _convert_tool_choice(tool_choice: str) -> dict:
        mapping = {"auto": "auto", "none": "none", "required": "any"}
        return {"type": mapping.get(tool_choice, "auto")}
