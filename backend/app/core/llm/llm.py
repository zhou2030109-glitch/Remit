"""模型调用门面：配置校验、限流重试、备用模型切换与消息播报。"""

import asyncio
import random
import time
from typing import Any

import httpx

from app.config.setting import ApiType, settings
from app.core.activity import AGENT_LABELS, publish_activity
from app.core.llm.errors import NonRetryableLLMError
from app.core.llm.providers.anthropic import AnthropicProvider
from app.core.llm.providers.base import BaseProvider
from app.core.llm.providers.gemini import GeminiProvider
from app.core.llm.providers.openai_chat import OpenAIChatProvider
from app.core.llm.providers.openai_responses import OpenAIResponsesProvider
from app.core.llm.types import StandardResponse
from app.schemas.enums import AgentType
from app.schemas.response import (
    CoderMessage,
    CoordinatorMessage,
    ModelerMessage,
    SystemMessage,
    WriterMessage,
)
from app.services.redis_manager import redis_manager
from app.utils.common_utils import split_footnotes, transform_link
from app.utils.log_util import logger

# 网关 / 限流类状态码：值得进入加长重试窗口
RETRYABLE_GATEWAY_STATUS_CODES = {408, 429, 500, 502, 503, 504, 520, 522, 524}

_PROVIDER_REGISTRY: dict[ApiType, type[BaseProvider]] = {}


def _register(api_type: ApiType, provider_cls: type[BaseProvider]) -> None:
    _PROVIDER_REGISTRY[api_type] = provider_cls


_register(ApiType.OPENAI_RESPONSES, OpenAIResponsesProvider)
_register(ApiType.ANTHROPIC, AnthropicProvider)
_register(ApiType.GEMINI, GeminiProvider)


def _resolve_provider(api_type: ApiType | None) -> BaseProvider:
    """按接入类型实例化 Provider；缺省走 OpenAI Chat 兼容端点。"""
    provider_cls = _PROVIDER_REGISTRY.get(api_type) if api_type else None
    return (provider_cls or OpenAIChatProvider)()


def _is_retryable_connection_error(error: Exception) -> bool:
    """传输层故障（中转掐断、连接重置、握手失败）同样值得加长重试。"""
    if isinstance(error, httpx.TransportError):
        return True
    if isinstance(getattr(error, "__cause__", None), httpx.TransportError):
        return True
    return any(cls.__name__ == "APIConnectionError" for cls in type(error).__mro__)


def _is_retryable_gateway_error(error: Exception) -> bool:
    """上游 / 网络故障是否够格进入加长重试窗口。"""
    if getattr(error, "status_code", None) in RETRYABLE_GATEWAY_STATUS_CODES:
        return True
    return _is_retryable_connection_error(error)


def _server_hinted_delay(error: Exception) -> float | None:
    """读取服务端建议的 retry_after（body 或响应头）。"""
    body = getattr(error, "body", None)
    hint = body.get("retry_after") if isinstance(body, dict) else None
    response = getattr(error, "response", None)
    if hint is None and response is not None:
        hint = response.headers.get("retry-after")
    if hint is None:
        return None
    try:
        # 封顶 5 分钟，防止异常响应头把任务钉死几个小时
        return min(max(float(hint), 1.0), 300.0)
    except (TypeError, ValueError):
        return None


def _retry_delay_seconds(error: Exception, attempt: int, base_delay: float) -> float:
    """限流 / 网关故障优先服从服务端节奏，其余线性退避。"""
    if getattr(error, "status_code", None) in RETRYABLE_GATEWAY_STATUS_CODES:
        hinted = _server_hinted_delay(error)
        if hinted is not None:
            return hinted
        delay = min(5.0 * (2 ** (attempt - 1)), 60.0)
        # 加抖动：并行章节同时被限流时错开重试波峰
        return delay + random.uniform(0, delay / 4)

    if _is_retryable_connection_error(error):
        # 中转瞬断通常几秒内恢复；指数退避而非三连打就放弃
        delay = min(2.0 * (2 ** (attempt - 1)), 30.0)
        return delay + random.uniform(0, delay / 4)

    return base_delay * min(attempt, 10)


def _repair_tool_call_chain(history: list) -> list:
    """清洗消息历史：丢弃没有配对响应的工具调用及其孤儿响应。

    中断恢复 / 历史压缩后容易出现半截的 tool_call 链，
    直接发给模型会被 API 拒收。
    """
    if not history:
        return history

    # 第一遍：收集所有有响应的 tool_call id
    answered_ids = {
        msg["tool_call_id"]
        for msg in history
        if isinstance(msg, dict)
        and msg.get("role") == "tool"
        and msg.get("tool_call_id")
    }

    cleaned: list[dict] = []
    for msg in history:
        if not isinstance(msg, dict):
            cleaned.append(msg)
            continue

        if msg.get("tool_calls"):
            surviving = [
                tc for tc in msg["tool_calls"] if tc.get("id") in answered_ids
            ]
            if surviving:
                cleaned.append({**msg, "tool_calls": surviving})
            elif msg.get("content"):
                stripped = {k: v for k, v in msg.items() if k != "tool_calls"}
                cleaned.append(stripped)
            continue

        if msg.get("role") == "tool":
            call_id = msg.get("tool_call_id")
            has_call = any(
                call_id in {tc.get("id") for tc in prev.get("tool_calls", [])}
                for prev in cleaned
            )
            if has_call:
                cleaned.append(msg)
            continue

        cleaned.append(msg)

    return cleaned


class LLM:
    """单个模型接入点：一次实例对应一套 密钥/模型/中转 组合。"""

    def __init__(
        self,
        api_type: ApiType | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        task_id: str = "",
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.api_type = api_type
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.task_id = task_id
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.chat_count = 0
        self._fallback_active = False
        self.provider = _resolve_provider(api_type)

    # ---- 配置与备用模型 ----

    def _validate_config(self, agent_name: str) -> None:
        if not self.model or not str(self.model).strip():
            raise ValueError(f"{agent_name} 未配置模型 ID，请设置对应的 *_MODEL")
        if not self.api_key or not str(self.api_key).strip():
            raise ValueError(f"{agent_name} 未配置 API Key，请设置对应的 *_API_KEY")

    def _activate_fallback(self) -> bool:
        """主模型重试耗尽后切换备用模型；未配置或已切换过则放弃。"""
        if (
            self._fallback_active
            or not settings.FALLBACK_MODEL
            or not settings.FALLBACK_API_KEY
        ):
            return False
        self._fallback_active = True
        self.api_type = settings.FALLBACK_API_TYPE or self.api_type
        self.api_key = settings.FALLBACK_API_KEY
        self.model = settings.FALLBACK_MODEL
        self.base_url = settings.FALLBACK_BASE_URL or self.base_url
        # 备用模型不一定支持原档位，改用其专属配置（未设则回落全局）
        self.reasoning_effort = settings.FALLBACK_REASONING_EFFORT
        self.provider = _resolve_provider(self.api_type)
        logger.warning(f"主模型连接持续失败，已切换备用模型 {self.model}")
        return True

    # ---- 主调用 ----

    async def chat(
        self,
        history: list | None = None,
        tools: list | None = None,
        tool_choice: str | None = None,
        max_retries: int | None = None,
        retry_delay: float = 1.0,
        top_p: float | None = None,
        agent_name: str = "SystemAgent",
        sub_title: str | None = None,
        publish: bool = True,
    ) -> StandardResponse:
        """发起一次对话调用，内建重试、备用模型与前端播报。"""
        self._validate_config(agent_name)
        messages = _repair_tool_call_chain(history) if history else []

        retry_limit = max_retries if max_retries is not None else settings.MAX_RETRIES
        if retry_limit is None:
            retry_limit = 3

        on_delta = self._make_delta_hook(agent_name, sub_title, publish)

        attempt = 0
        while True:
            try:
                response = await self.provider.call(
                    messages=messages,
                    model=self.model,  # type: ignore[arg-type]
                    api_key=self.api_key,  # type: ignore[arg-type]
                    base_url=self.base_url,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_tokens=self.max_tokens,
                    top_p=top_p,
                    on_delta=on_delta,
                    reasoning_effort=self.reasoning_effort,
                )
            except Exception as error:
                attempt += 1
                retry_limit_now = self._handle_failure(error, agent_name, attempt, retry_limit)
                if attempt >= retry_limit_now:
                    if await self._switch_to_fallback_quietly():
                        attempt = 0
                        continue
                    raise
                await asyncio.sleep(_retry_delay_seconds(error, attempt, retry_delay))
                continue

            logger.info(
                "API返回: content_chars={}, tool_calls={}, prompt_tokens={}, completion_tokens={}",
                len(response.content or ""),
                len(response.tool_calls),
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
            self.chat_count += 1
            if publish:
                try:
                    await self.send_message(response, agent_name, sub_title)
                except Exception as error:
                    # 响应已经拿到；消息通道故障不能让同一付费请求重做
                    logger.error(
                        "模型响应已成功返回，但消息发布失败；"
                        f"不会重试模型请求: {error}"
                    )
            return response

    def _handle_failure(
        self, error: Exception, agent_name: str, attempt: int, retry_limit: int
    ) -> int:
        """记录失败并返回本次适用的重试上限；不可重试错误直接抛出。"""
        if isinstance(error, NonRetryableLLMError):
            logger.error(
                "API非重试错误: model={}, prompt_tokens={}, completion_tokens={}",
                self.model,
                getattr(error, "prompt_tokens", 0),
                getattr(error, "completion_tokens", 0),
            )
            logger.error(f"第{attempt}次重试: {error}")
            raise
        logger.error(f"第{attempt}次重试: {error}")
        if _is_retryable_gateway_error(error):
            return max(retry_limit, settings.GATEWAY_MAX_RETRIES)
        return retry_limit

    async def _switch_to_fallback_quietly(self) -> bool:
        """切换到备用模型并向前端播报；播报失败不影响切换本身。"""
        if not self._activate_fallback():
            return False
        if self.task_id:
            try:
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=(
                            "主模型连接持续失败，"
                            f"已切换备用模型 {self.model} 继续"
                        ),
                        type="warning",
                    ),
                )
            except Exception:
                pass
        return True

    def _make_delta_hook(
        self, agent_name: str, sub_title: str | None, publish: bool
    ):
        """构造流式增量回调：节流到每秒一条尾部预览。"""
        if not (publish and self.task_id):
            return None

        display = AGENT_LABELS.get(agent_name, agent_name)
        if sub_title:
            display = f"{display}({sub_title})"
        buffer: list[str] = []
        last_emit = 0.0

        async def _hook(delta: str) -> None:
            nonlocal last_emit
            buffer.append(delta)
            now = time.monotonic()
            if now - last_emit < 1.0:
                return
            last_emit = now
            await publish_activity(
                self.task_id,
                f"{display}正在输出…",
                category="llm",
                detail="".join(buffer)[-160:],
            )

        return _hook

    # ---- 消息播报 ----

    async def send_message(
        self,
        response: StandardResponse,
        agent_name: str,
        sub_title: str | None = None,
    ) -> None:
        """把响应内容按角色封装后经 Redis 推给前端。"""
        content = response.content
        if content is None:
            return

        agent_msg: Any
        match agent_name:
            case AgentType.CODER:
                agent_msg = CoderMessage(content=content)
            case AgentType.WRITER:
                body, _ = split_footnotes(content)
                agent_msg = WriterMessage(
                    content=transform_link(self.task_id, body), sub_title=sub_title
                )
            case AgentType.MODELER:
                agent_msg = ModelerMessage(content=content)
            case AgentType.SYSTEM:
                agent_msg = SystemMessage(content=content)
            case AgentType.COORDINATOR:
                agent_msg = CoordinatorMessage(content=content)
            case _:
                raise ValueError(f"不支持的agent类型: {agent_name}")

        await redis_manager.publish_message(self.task_id, agent_msg)


async def simple_chat(model: LLM, history: list) -> str:
    """单轮静默对话；复用 chat 的重试 / 档位 / 备用模型机制。"""
    response = await model.chat(history=history, max_retries=2, publish=False)
    return response.content or ""
