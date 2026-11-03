"""OpenAI Responses 端点（/v1/responses）。

走流式读取再取最终结果：部分中转代理会在长请求上
触发无数据超时，持续的事件流可以避免这个问题。
"""

import re
from typing import Any

from openai import APIError, AsyncOpenAI

from app.config.setting import effective_api_timeout_seconds, settings
from app.core.llm.content import ImageBlock, iter_content_blocks
from app.core.llm.errors import (
    NonRetryableLLMError,
    ResponseStreamInterruptedError,
    TransientLLMError,
)
from app.core.llm.providers.base import (
    BaseProvider,
    DeltaCallback,
    ProviderRequest,
    close_client,
)
from app.core.llm.types import StandardResponse, ToolCall, Usage
from app.utils.log_util import logger


class _UntypedStreamError(NonRetryableLLMError):
    """A gateway rejected streaming before emitting a valid response."""


class OpenAIResponsesProvider(BaseProvider):
    """新版 Responses API，支持推理档位与服务端存储开关。"""

    def __init__(self) -> None:
        self._use_non_streaming = False

    async def send(self, request: ProviderRequest) -> StandardResponse:
        payload = self._build_payload(
            request.messages,
            request.model,
            request.tools,
            request.tool_choice,
            request.max_tokens,
            request.top_p,
            request.reasoning_effort,
        )
        if request.tools and request.parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = request.parallel_tool_calls
        client = AsyncOpenAI(
            api_key=request.api_key,
            base_url=request.base_url,
            timeout=effective_api_timeout_seconds(),
            max_retries=0,
            default_headers={"User-Agent": "Remit/1.0"},
        )
        try:
            if self._use_non_streaming:
                response = await client.responses.create(**payload)
            else:
                try:
                    response = await self._stream_collect(
                        client, payload, request.on_delta
                    )
                except _UntypedStreamError:
                    # Some relays reject streaming with an untyped error but
                    # support the same request as JSON. Try that mode once;
                    # a repeated configuration error remains non-retryable.
                    self._use_non_streaming = True
                    logger.warning("接入服务返回非标准流错误，改用完整响应模式验证一次")
                    response = await client.responses.create(**payload)
                except ResponseStreamInterruptedError:
                    # 下次有界重试改用完整响应，避免对不兼容的流协议原样重放。
                    self._use_non_streaming = True
                    logger.warning("模型响应流缺少结束事件；下次重试将使用完整响应模式")
                    raise
            return self._normalize(response)
        except APIError as exc:
            if getattr(exc, "status_code", None) in {
                408,
                429,
                500,
                502,
                503,
                504,
                520,
                522,
                524,
            }:
                raise
            body = exc.body
            if isinstance(body, dict):
                error = body.get("error", body)
                if isinstance(error, dict) and error.get("code"):
                    self._raise_response_error(error.get("code"), error.get("message"))
            raise
        finally:
            await close_client(client)

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
        terminal = None
        last_event = "none"
        text_chars = 0
        # Read raw SDK events: its accumulator requires response.created and can
        # hide a gateway's untyped error behind an unrelated RuntimeError.
        stream = await client.responses.create(**payload, stream=True)
        try:
            async for event in stream:
                kind = getattr(event, "type", None)
                data = getattr(event, "data", None)
                error = getattr(event, "error", None)
                if isinstance(data, dict):
                    error = data.get("error", data)
                code = (
                    error.get("code")
                    if isinstance(error, dict)
                    else getattr(error, "code", None)
                ) or getattr(event, "code", None)
                message = (
                    error.get("message")
                    if isinstance(error, dict)
                    else getattr(error, "message", None)
                ) or getattr(event, "message", None)
                if kind == "error" or (not kind and code):
                    try:
                        OpenAIResponsesProvider._raise_response_error(code, message)
                    except NonRetryableLLMError as exc:
                        if not kind and text_chars == 0:
                            raise _UntypedStreamError(str(exc)) from exc
                        raise
                last_event = kind or "unknown"
                if kind in {
                    "response.completed",
                    "response.incomplete",
                    "response.failed",
                }:
                    terminal = event.response
                    # The terminal event contains the authoritative full output,
                    # including tool calls. Partial argument deltas are never run.
                    return terminal
                if kind == "response.output_text.delta":
                    text_chars += len(event.delta)
                    if on_delta is not None:
                        try:
                            await on_delta(event.delta)
                        except Exception:
                            pass
        finally:
            await close_client(stream)
        raise ResponseStreamInterruptedError(
            f"模型响应流提前结束，未收到结束事件（最后事件：{last_event}，已接收 {text_chars} 字）。"
            "本次不使用未完成内容，可重试模型请求。"
        )

    @staticmethod
    def _raise_response_error(code: str | None, message: str | None = None) -> None:
        code = str(code or "unknown_error")
        safe_code = (
            code if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code) else "unknown_error"
        )
        # Only surface the parameter name, never arbitrary upstream messages
        # which may contain credentials, request text or internal endpoints.
        missing = re.search(
            r"Missing required parameter:\s*['\"]([A-Za-z0-9_.-]{1,64})['\"]",
            str(message or ""),
        )
        if missing:
            raise NonRetryableLLMError(
                f"模型接入服务缺少必需参数 {missing.group(1)}（{safe_code}）。"
                "请检查该接入点的参数或联系服务提供方；重复运行无法修复这个配置错误。已有文件和进度已保留。"
            )
        if code.lower() in {
            "server_error",
            "rate_limit_exceeded",
            "overloaded",
            "timeout",
            "internalerror",
            "serviceunavailable",
            "throttling",
        }:
            raise TransientLLMError(
                f"模型服务暂时无法完成响应（{safe_code}），请稍后重试。"
            )
        raise NonRetryableLLMError(
            f"模型服务返回失败（{safe_code}），请检查模型配置或请求内容。"
        )

    @staticmethod
    def _normalize(response: Any) -> StandardResponse:
        error = getattr(response, "error", None)
        code = (
            error.get("code")
            if isinstance(error, dict)
            else getattr(error, "code", None)
        ) or getattr(response, "code", None)
        message = (
            error.get("message")
            if isinstance(error, dict)
            else getattr(error, "message", None)
        ) or getattr(response, "message", None)
        if getattr(response, "status", None) == "failed" or code:
            OpenAIResponsesProvider._raise_response_error(code, message)
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
                known=response.usage is not None,
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
            case "auto" | "none" | "required":
                return tool_choice
            case _:
                return tool_choice
