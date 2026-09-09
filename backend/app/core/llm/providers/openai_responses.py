"""OpenAI Responses 端点（/v1/responses）。

走流式读取再取最终结果：部分中转代理会在长请求上
触发无数据超时，持续的事件流可以避免这个问题。
"""

from typing import Any

from openai import AsyncOpenAI

from app.config.setting import effective_api_timeout_seconds, settings
from app.core.llm.content import ImageBlock, iter_content_blocks
from app.core.llm.providers.base import BaseProvider, DeltaCallback, ProviderRequest
from app.core.llm.types import StandardResponse, ToolCall, Usage


class OpenAIResponsesProvider(BaseProvider):
    """新版 Responses API，支持推理档位与服务端存储开关。"""

    async def send(self, request: ProviderRequest) -> StandardResponse:
        client = AsyncOpenAI(
            api_key=request.api_key,
            base_url=request.base_url,
            timeout=effective_api_timeout_seconds(),
            max_retries=0,
            default_headers={"User-Agent": "Remit/1.0"},
        )
        payload = self._build_payload(
            request.messages,
            request.model,
            request.tools,
            request.tool_choice,
            request.max_tokens,
            request.top_p,
            request.reasoning_effort,
        )
        response = await self._stream_collect(client, payload, request.on_delta)
        return self._normalize(response)

    def _build_payload(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None,
        tool_choice: str | None,
        max_tokens: int | None,
        top_p: float | None,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "input": self._messages_to_input(messages),
            "store": not settings.DISABLE_RESPONSE_STORAGE,
        }
        effort = reasoning_effort or settings.MODEL_REASONING_EFFORT
        if effort:
            payload["reasoning"] = {"effort": effort}
        if max_tokens:
            payload["max_output_tokens"] = max_tokens
        if top_p is not None:
            payload["top_p"] = top_p
        if tools:
            payload["tools"] = [self._convert_tool(t) for t in tools]
            if tool_choice:
                payload["tool_choice"] = self._convert_tool_choice(tool_choice)
        return payload

    @staticmethod
    async def _stream_collect(
        client: AsyncOpenAI, payload: dict[str, Any], on_delta: DeltaCallback | None
    ) -> Any:
        async with client.responses.stream(**payload) as stream:
            async for event in stream:
                if on_delta is not None and event.type == "response.output_text.delta":
                    try:
                        await on_delta(event.delta)
                    except Exception:
                        # 播报回调异常不能拖垮一次付费请求
                        pass
            return await stream.get_final_response()

    @staticmethod
    def _normalize(response: Any) -> StandardResponse:
        texts: list[str] = []
        calls: list[ToolCall] = []
        for item in response.output:
            if item.type == "message":
                texts.extend(
                    part.text for part in item.content if part.type == "output_text"
                )
            elif item.type == "function_call":
                calls.append(
                    ToolCall(id=item.call_id, name=item.name, arguments=item.arguments)
                )
        incomplete_details = getattr(response, "incomplete_details", None)
        incomplete_reason = getattr(incomplete_details, "reason", None)
        return StandardResponse(
            content="".join(texts) or None,
            finish_reason=incomplete_reason or getattr(response, "status", None),
            tool_calls=calls,
            usage=Usage(
                prompt_tokens=response.usage.input_tokens if response.usage else 0,
                completion_tokens=response.usage.output_tokens if response.usage else 0,
            ),
        )

    # ---- 格式转换：Chat messages -> Responses input ----

    def _messages_to_input(self, messages: list[dict]) -> list[dict]:
        items: list[dict] = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                items.append({"role": "developer", "content": msg["content"]})
            elif role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg.get("tool_call_id", ""),
                        "output": msg.get("content", ""),
                    }
                )
            elif role == "assistant" and "tool_calls" in msg:
                items.extend(self._assistant_tool_calls(msg))
            else:
                items.append(
                    {"role": role, "content": self._convert_content(msg, role)}
                )
        return items

    @staticmethod
    def _assistant_tool_calls(msg: dict) -> list[dict]:
        items = [
            {
                "type": "function_call",
                "call_id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": tc["function"]["arguments"],
            }
            for tc in msg["tool_calls"]
        ]
        if msg.get("content"):
            items.append({"role": "assistant", "content": msg["content"]})
        return items

    @staticmethod
    def _convert_content(msg: dict, role: str) -> str | list[dict]:
        """纯文本保持字符串；多模态才展开成块，避免无谓改变请求形状。"""
        content = msg.get("content", "")
        if isinstance(content, str):
            return content

        # 助手历史只能用 output_text，输入侧才是 input_text
        text_type = "output_text" if role == "assistant" else "input_text"
        parts = [
            {"type": "input_image", "image_url": block.data_url}
            if isinstance(block, ImageBlock)
            else {"type": text_type, "text": block.text}
            for block in iter_content_blocks(content)
        ]
        return parts or ""

    @staticmethod
    def _convert_tool(tool: dict) -> dict:
        if tool.get("type") != "function":
            return tool
        func = tool["function"]
        return {
            "type": "function",
            "name": func["name"],
            "description": func.get("description", ""),
            "parameters": func.get("parameters", {}),
            "strict": func.get("strict", True),
        }

    @staticmethod
    def _convert_tool_choice(tool_choice: str) -> str | dict:
        match tool_choice:
            case "auto" | "none":
                return tool_choice
            case "required":
                return {"type": "function"}
            case _:
                return tool_choice
