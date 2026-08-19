"""Google Gemini generateContent API Provider。"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.config.setting import settings
from app.core.llm.content import ImageBlock, TextBlock, iter_content_blocks
from app.core.llm.providers.base import BaseProvider, DeltaCallback
from app.core.llm.types import StandardResponse, ToolCall, Usage


class GeminiProvider(BaseProvider):
    """Gemini 原生 ``generateContent`` 协议实现。

    该 Provider 不借用 OpenAI 兼容层，因而既可连接 Google 官方端点，也可连接
    提供 Gemini 原生协议的中转。传入的 ``base_url`` 应截止到 ``v1beta``。
    """

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

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
        root = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        url = f"{root}/models/{quote(model, safe='')}:generateContent"
        system_instruction, contents = self._convert_messages(messages)
        payload: dict[str, Any] = {"contents": contents}

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        generation_config: dict[str, Any] = {
            "maxOutputTokens": max_tokens or 8192,
        }
        if top_p is not None:
            generation_config["topP"] = top_p
        payload["generationConfig"] = generation_config

        if tools:
            declarations = self._convert_tools(tools)
            if declarations:
                payload["tools"] = [{"functionDeclarations": declarations}]
                payload["toolConfig"] = {
                    "functionCallingConfig": {
                        "mode": self._convert_tool_choice(tool_choice)
                    }
                }

        timeout = httpx.Timeout(settings.API_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()

        candidates = body.get("candidates") or []
        if not candidates:
            feedback = body.get("promptFeedback") or {}
            reason = feedback.get("blockReason") or "empty candidates"
            raise RuntimeError(f"Gemini 未返回候选内容: {reason}")

        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in parts:
            if isinstance(part.get("text"), str):
                text_parts.append(part["text"])
            function_call = part.get("functionCall")
            if isinstance(function_call, dict) and function_call.get("name"):
                tool_calls.append(
                    ToolCall(
                        id=f"gemini_{uuid4().hex}",
                        name=str(function_call["name"]),
                        arguments=json.dumps(
                            function_call.get("args") or {},
                            ensure_ascii=False,
                        ),
                    )
                )

        usage_metadata = body.get("usageMetadata") or {}
        completion_tokens = int(
            usage_metadata.get("candidatesTokenCount", 0)
            + usage_metadata.get("thoughtsTokenCount", 0)
        )
        return StandardResponse(
            content="".join(text_parts) or None,
            finish_reason=str(candidates[0].get("finishReason") or "") or None,
            tool_calls=tool_calls,
            usage=Usage(
                prompt_tokens=int(usage_metadata.get("promptTokenCount", 0)),
                completion_tokens=completion_tokens,
            ),
        )

    def _convert_messages(
        self, messages: list[dict]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []

        for message in messages:
            role = str(message.get("role", "user"))
            if role == "system":
                text = self._content_text(message.get("content"))
                if text:
                    system_parts.append(text)
                continue

            if role == "tool":
                name = str(message.get("name") or "tool")
                converted.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": name,
                                    "response": {
                                        "result": self._content_text(
                                            message.get("content")
                                        )
                                    },
                                }
                            }
                        ],
                    }
                )
                continue

            parts: list[dict[str, Any]] = self._content_parts(message.get("content"))

            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                arguments = function.get("arguments") or "{}"
                try:
                    parsed_arguments = json.loads(arguments)
                except (TypeError, json.JSONDecodeError):
                    parsed_arguments = {"raw": str(arguments)}
                parts.append(
                    {
                        "functionCall": {
                            "name": function.get("name", "tool"),
                            "args": parsed_arguments,
                        }
                    }
                )

            if not parts:
                continue
            converted.append(
                {"role": "model" if role == "assistant" else "user", "parts": parts}
            )

        if not converted:
            converted = [{"role": "user", "parts": [{"text": "Continue."}]}]
        return "\n\n".join(system_parts) or None, converted

    @staticmethod
    def _content_parts(content: Any) -> list[dict[str, Any]]:
        """把消息内容转成 Gemini 的 text / inlineData parts。"""
        parts: list[dict[str, Any]] = []
        for block in iter_content_blocks(content):
            if isinstance(block, ImageBlock):
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": block.media_type,
                            "data": block.data,
                        }
                    }
                )
            elif isinstance(block, TextBlock) and block.text:
                parts.append({"text": block.text})
        return parts

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
        if content is None:
            return ""
        return str(content)

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict[str, Any]]:
        declarations: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            function = tool.get("function") or {}
            declarations.append(
                {
                    "name": function.get("name", "tool"),
                    "description": function.get("description", ""),
                    "parameters": function.get("parameters") or {
                        "type": "object",
                        "properties": {},
                    },
                }
            )
        return declarations

    @staticmethod
    def _convert_tool_choice(tool_choice: str | None) -> str:
        if tool_choice == "none":
            return "NONE"
        if tool_choice == "required":
            return "ANY"
        return "AUTO"
