"""模型调用门面：配置校验、限流重试、备用模型切换与消息播报。"""

import asyncio
import random
import sqlite3
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from uuid import uuid4
from urllib.parse import urlsplit
from typing import Any

import httpx

from app.config.setting import ApiType, settings, effective_api_timeout_seconds
from app.core.activity import AGENT_LABELS, publish_activity
from app.core.llm.errors import (
    NonRetryableLLMError,
    TransientLLMError,
    http_status_code,
)
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
from app.services.call_ledger import CallPurpose
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
    if isinstance(error, (httpx.TransportError, TransientLLMError)):
        return True
    if isinstance(getattr(error, "__cause__", None), httpx.TransportError):
        return True
    return any(cls.__name__ == "APIConnectionError" for cls in type(error).__mro__)


def _is_retryable_gateway_error(error: Exception) -> bool:
    """上游 / 网络故障是否够格进入加长重试窗口。"""
    if http_status_code(error) in RETRYABLE_GATEWAY_STATUS_CODES:
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
        return min(
            max(float(hint), 1.0),
            float(settings.LLM_RETRY_AFTER_MAX_SECONDS),
        )
    except (TypeError, ValueError):
        try:
            seconds = (
                parsedate_to_datetime(str(hint)) - datetime.now(timezone.utc)
            ).total_seconds()
            return min(max(seconds, 1.0), float(settings.LLM_RETRY_AFTER_MAX_SECONDS))
        except (TypeError, ValueError, OverflowError):
            return None


def _retry_delay_seconds(error: Exception, attempt: int, base_delay: float) -> float:
    """限流 / 网关故障优先服从服务端节奏，其余线性退避。"""
    if http_status_code(error) in RETRYABLE_GATEWAY_STATUS_CODES:
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
            surviving = [tc for tc in msg["tool_calls"] if tc.get("id") in answered_ids]
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
        context_window: int = 128000,
    ) -> None:
        self.api_type = api_type
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.task_id = task_id
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.context_window = context_window
        self.capabilities: dict = {}
        self.capability_role: str | None = None
        self.chat_count = 0
        self._fallback_active = False
        self.provider = _resolve_provider(api_type)

    # ---- 配置与备用模型 ----

    def _validate_config(self, agent_name: str) -> None:
        if not self.model or not str(self.model).strip():
            raise ValueError(f"{agent_name} 未配置模型 ID，请设置对应的 *_MODEL")
        if not self.api_key or not str(self.api_key).strip():
            raise ValueError(f"{agent_name} 未配置 API Key，请设置对应的 *_API_KEY")

    def _activate_fallback(self, messages, tools, max_tokens) -> bool:
        """主模型重试耗尽后切换备用模型；未配置或已切换过则放弃。"""
        if (
            self._fallback_active
            or not settings.FALLBACK_ENABLED
            or not settings.FALLBACK_MODEL
            or not settings.FALLBACK_API_KEY
        ):
            return False

        def origin(url):
            return urlsplit(url or "https://api.openai.com/v1").netloc.casefold()

        if (settings.FALLBACK_API_TYPE or self.api_type) != self.api_type or origin(
            settings.FALLBACK_BASE_URL or self.base_url
        ) != origin(self.base_url):
            logger.warning("备用模型使用不同服务地址或协议，请先单独配置与验证后重试")
            return False
        from app.services.model_capabilities import fallback_config, load_profile
        from app.core.llm.request_budget import check_request

        if self.capability_role is None:
            raise NonRetryableLLMError(
                "备用模型尚无对应角色的能力验证，请在设置中验证后重试。"
            )
        config = fallback_config(self.capability_role)
        # The profile must describe the exact effective connection of this run.
        if config["api_type"] != (
            settings.FALLBACK_API_TYPE or self.api_type
        ) or config["base_url"] != (settings.FALLBACK_BASE_URL or self.base_url):
            raise NonRetryableLLMError("备用模型连接已变更，请重新验证对应角色后重试。")
        profile = load_profile(
            self.capability_role, fallback=True, effective_config=config
        )
        if self.capability_role != "vision":
            # A tool role may later receive an image. Reuse only a vision probe
            # bound to this exact fallback connection and parameter fingerprint.
            vision = load_profile("vision", fallback=True, effective_config=config)
            profile = {**profile, "vision": vision.get("vision", "unknown")}
        self._check_capabilities(profile, messages, tools, strict=True)
        output = int(max_tokens or config["max_tokens"])
        if output > config["max_tokens"]:
            raise NonRetryableLLMError(
                "本次请求超过备用模型输出上限，请调整配置并重新验证。"
            )
        check_request(messages, tools, output, config["context_window"])
        self._fallback_active = True
        self.api_type = config["api_type"]
        self.api_key = config["api_key"]
        self.model = config["model_id"]
        self.base_url = config["base_url"]
        self.reasoning_effort = config["reasoning_effort"]
        self.context_window = config["context_window"]
        self.max_tokens = config["max_tokens"]
        self.capabilities = profile
        self.provider = _resolve_provider(self.api_type)
        logger.warning(f"主模型连接持续失败，已切换备用模型 {self.model}")
        return True

    @staticmethod
    def _check_capabilities(profile, messages, tools, *, strict=False):
        from app.core.llm.content import has_image_content

        required = {"connection", "text", "structured_output"} if strict else set()
        if tools:
            required.update(("tools", "tool_result"))
        if has_image_content(messages):
            required.add("vision")
        missing = [
            key
            for key in sorted(required)
            if (
                profile.get(key) != "supported"
                if strict
                else profile.get(key) == "unsupported"
            )
        ]
        if missing:
            if strict:
                raise NonRetryableLLMError(
                    "备用模型的本次请求所需能力尚未验证通过，请在设置中验证对应角色后重试："
                    + ", ".join(missing)
                )
            reason = "识图" if "vision" in missing else "工具调用"
            raise NonRetryableLLMError(
                f"当前配置的{reason}能力验证未通过，请修改模型配置或重新验证。"
            )

    # ---- 主调用 ----

    async def chat(
        self,
        history: list | None = None,
        tools: list | None = None,
        tool_choice: str | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
        retry_delay: float = 1.0,
        top_p: float | None = None,
        agent_name: str = "SystemAgent",
        sub_title: str | None = None,
        publish: bool = True,
        parallel_tool_calls: bool | None = None,
        purpose: CallPurpose | None = None,
    ) -> StandardResponse:
        """发起一次对话调用，内建重试、备用模型与前端播报。"""
        self._validate_config(agent_name)
        self._check_capabilities(
            self.capabilities, history or [], tools, strict=self._fallback_active
        )
        if self._fallback_active and max_tokens and max_tokens > self.max_tokens:
            raise NonRetryableLLMError(
                "本次请求超过备用模型输出上限，请调整配置并重新验证。"
            )
        messages = _repair_tool_call_chain(history) if history else []
        from app.services.team_state import context_for

        shared_context = await asyncio.to_thread(context_for, self.task_id, agent_name)
        from app.services.competitions import context_for as competition_context

        contest_context = await asyncio.to_thread(
            competition_context, self.task_id, agent_name
        )
        if contest_context:
            messages = [*messages, {"role": "user", "content": contest_context}]
        if shared_context:
            # 不改原始历史；下一轮再次读取最新公共状态，避免陈旧副本累积。
            messages = [*messages, {"role": "user", "content": shared_context}]

        retry_limit = max_retries if max_retries is not None else settings.MAX_RETRIES
        if retry_limit is None:
            retry_limit = 3
        retry_limit = min(max(int(retry_limit), 1), settings.LLM_HARD_RETRY_LIMIT)

        from app.core.llm.request_budget import check_request

        check_request(
            messages,
            tools,
            int(max_tokens or self.max_tokens or 4096),
            self.context_window,
        )

        on_delta = self._make_delta_hook(agent_name, sub_title, publish)

        attempt = 0
        call_id = uuid4().hex
        from app.services.call_ledger import context, reserve

        metadata = await asyncio.to_thread(
            context, self.task_id, sub_title, str(agent_name)
        )
        if purpose is not None:
            metadata["logical_purpose"] = purpose
        request_index = 0
        while True:
            attempt_id = uuid4().hex
            request_index += 1

            stage_seconds = await asyncio.to_thread(
                reserve,
                self.task_id,
                f"{agent_name}:{sub_title or 'default'}",
                call_id,
                attempt_id,
            )
            started = time.perf_counter()
            started_at = datetime.now(timezone.utc).isoformat()
            elapsed_seconds = None
            retry_wait_seconds = 0.0
            finished_at = None

            async def record_attempt(status, response=None, error=None):
                from app.services.call_ledger import record

                usage = getattr(response, "usage", None)
                known = bool(getattr(usage, "known", False))
                entry = {
                    **metadata,
                    "purpose": "transport_retry"
                    if request_index > 1
                    else metadata["logical_purpose"],
                    "request_index": request_index,
                    "is_fallback": self._fallback_active,
                    "retry_wait_seconds": retry_wait_seconds,
                    "finished_at": finished_at,
                    "attempt_id": attempt_id,
                    "call_id": call_id,
                    "role": str(agent_name),
                    "model": self.model,
                    "status": status,
                    "started_at": started_at,
                    "elapsed_seconds": round(elapsed_seconds or 0.0, 6),
                    "prompt_tokens": usage.prompt_tokens if known else None,
                    "completion_tokens": usage.completion_tokens if known else None,
                    "cost": None,
                    "currency": None,
                    "error_code": str(http_status_code(error) or type(error).__name__)
                    if error
                    else None,
                }
                try:
                    await asyncio.to_thread(record, self.task_id, entry)
                except (OSError, ValueError, RuntimeError, sqlite3.Error):
                    logger.warning("调用账本暂时不可写；模型响应保持有效，不重新调用")

            try:
                await record_attempt("started_outcome_unknown")
                response = await asyncio.wait_for(
                    self.provider.call(
                        messages=messages,
                        model=self.model,  # type: ignore[arg-type]
                        api_key=self.api_key,  # type: ignore[arg-type]
                        base_url=self.base_url,
                        tools=tools,
                        tool_choice=tool_choice,
                        max_tokens=self.max_tokens
                        if max_tokens is None
                        else max_tokens,
                        top_p=top_p,
                        on_delta=on_delta,
                        reasoning_effort=self.reasoning_effort,
                        parallel_tool_calls=parallel_tool_calls,
                    ),
                    timeout=min(effective_api_timeout_seconds(), stage_seconds)
                    if stage_seconds is not None
                    else effective_api_timeout_seconds(),
                )
            except asyncio.CancelledError:
                elapsed_seconds = time.perf_counter() - started
                finished_at = datetime.now(timezone.utc).isoformat()
                await record_attempt("cancelled_outcome_unknown")
                raise
            except Exception as error:
                elapsed_seconds = time.perf_counter() - started
                finished_at = datetime.now(timezone.utc).isoformat()
                await record_attempt("failed_outcome_unknown", error=error)
                if (
                    isinstance(error, TimeoutError)
                    and stage_seconds is not None
                    and elapsed_seconds >= stage_seconds - 0.05
                ):
                    # Our stage timer fired: pause as a budget event rather than
                    # blaming the provider and starting an impossible retry.
                    from app.services.model_budget import for_task
                    from app.services.call_ledger import budget_phase

                    budget = await asyncio.to_thread(
                        for_task, self.task_id, f"{agent_name}:{sub_title or 'default'}"
                    )
                    if budget is not None:
                        snapshot = await asyncio.to_thread(
                            budget.snapshot, budget_phase(self.task_id)
                        )
                        budget.require(snapshot)
                attempt += 1
                retry_limit_now = self._handle_failure(
                    error, agent_name, attempt, retry_limit
                )
                # Explicit per-call limits must also cap gateway retries. Default
                # long-running jobs retain the configured extended retry policy.
                if max_retries is not None:
                    retry_limit_now = min(retry_limit_now, retry_limit)
                if attempt >= retry_limit_now:
                    if await self._switch_to_fallback_quietly(
                        messages, tools, max_tokens
                    ):
                        attempt = 0
                        continue
                    raise
                waiting = time.perf_counter()
                try:
                    await asyncio.sleep(
                        _retry_delay_seconds(error, attempt, retry_delay)
                    )
                finally:
                    # 记录实际退避，包括中途取消；不把等待再次计入模型请求耗时。
                    retry_wait_seconds = round(time.perf_counter() - waiting, 6)
                    await record_attempt("failed_outcome_unknown", error=error)
                continue

            elapsed_seconds = time.perf_counter() - started
            finished_at = datetime.now(timezone.utc).isoformat()
            await record_attempt("completed", response=response)
            logger.info(
                "API返回: content_chars={}, tool_calls={}, finish_reason={}, "
                "prompt_tokens={}, completion_tokens={}",
                len(response.content or ""),
                len(response.tool_calls),
                response.finish_reason,
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
                        f"模型响应已成功返回，但消息发布失败；不会重试模型请求: {error}"
                    )
            return response

    def _handle_failure(
        self, error: Exception, agent_name: str, attempt: int, retry_limit: int
    ) -> int:
        """记录失败并返回本次适用的重试上限；不可重试错误直接抛出。"""
        status_code = http_status_code(error)
        if isinstance(error, NonRetryableLLMError) or status_code in {
            400,
            401,
            403,
            404,
            405,
            413,
            415,
            422,
        }:
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
            return min(
                max(retry_limit, settings.GATEWAY_MAX_RETRIES),
                settings.LLM_HARD_RETRY_LIMIT,
            )
        if isinstance(error, (ValueError, TypeError)):
            raise error
        return retry_limit

    async def _switch_to_fallback_quietly(self, messages, tools, max_tokens) -> bool:
        """切换到备用模型并向前端播报；播报失败不影响切换本身。"""
        if not self._activate_fallback(messages, tools, max_tokens):
            return False
        if self.task_id:
            try:
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=(
                            f"主模型连接持续失败，已切换备用模型 {self.model} 继续"
                        ),
                        type="warning",
                    ),
                )
            except Exception:
                pass
        return True

    def _make_delta_hook(self, agent_name: str, sub_title: str | None, publish: bool):
        """构造流式增量回调：节流到每秒一条尾部预览。"""
        if not (publish and self.task_id):
            return None

        display = AGENT_LABELS.get(agent_name, agent_name)
        if sub_title:
            display = f"{display}({sub_title})"
        tail = ""
        last_emit = 0.0

        async def _hook(delta: str) -> None:
            nonlocal last_emit, tail
            tail = (tail + delta)[-160:]
            now = time.monotonic()
            if now - last_emit < 1.0:
                return
            last_emit = now
            try:
                await publish_activity(
                    self.task_id, f"{display}正在输出…", category="llm", detail=tail
                )
            except Exception:
                # 流式进度丢失也不能使已被供应商处理的请求重做。
                logger.warning("输出进度暂时无法同步，继续接收模型响应")

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

        if agent_name == AgentType.WRITER:
            body, _footnotes = split_footnotes(content)
            agent_msg: Any = WriterMessage(
                content=transform_link(self.task_id, body),
                sub_title=sub_title,
            )
        else:
            message_types = {
                AgentType.CODER: CoderMessage,
                AgentType.MODELER: ModelerMessage,
                AgentType.SYSTEM: SystemMessage,
                AgentType.COORDINATOR: CoordinatorMessage,
            }
            message_type = message_types.get(agent_name)
            if message_type is None:
                raise ValueError(f"不支持的 Agent 类型：{agent_name}")
            agent_msg = message_type(content=content)

        await redis_manager.publish_message(self.task_id, agent_msg)


async def simple_chat(model: LLM, history: list) -> str:
    """单轮静默对话；复用 chat 的重试 / 档位 / 备用模型机制。"""
    response = await model.chat(history=history, max_retries=2, publish=False)
    return response.content or ""
