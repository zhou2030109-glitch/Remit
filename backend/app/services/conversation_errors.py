"""Safe, actionable messages for failed interactive requests."""

import asyncio

from fastapi import HTTPException

from app.core.llm.errors import (
    ModelStageBudgetExceeded,
    TransientLLMError,
    exception_chain,
    http_status_code,
)


def explain(error: Exception) -> str:
    if isinstance(error, HTTPException):
        return str(error.detail)
    for cause in exception_chain(error):
        message = _explain(cause)
        if message is not None:
            return message
    return "这条消息未能处理完成，请检查模型连接或查看本地诊断后重试。"


def _explain(error) -> str | None:
    if isinstance(error, ModelStageBudgetExceeded):
        return "当前阶段模型额度不足，成果已保留；请查看模型额度后继续。"
    if isinstance(error, TransientLLMError):
        return "模型服务暂时不可用，这次没有收到完整回复。请稍后再试。"
    status = http_status_code(error)
    if status in {401, 403}:
        return "模型服务拒绝访问，请在设置中检查密钥和账户权限。"
    if status == 429:
        return "模型服务正在限流，请稍后再试。"
    if isinstance(status, int) and 500 <= status < 600:
        return f"模型服务暂时不可用（HTTP {status}），这次没有收到回复。请稍后再试；若持续失败，请在设置中检查模型连接。"
    if isinstance(error, asyncio.TimeoutError) or "Timeout" in type(error).__name__:
        return "等待模型回复超时，请稍后重试，或在设置中检查模型连接。"
    if isinstance(error, ConnectionError) or "Connection" in type(error).__name__:
        return "无法连接模型服务，请检查网络和服务地址后重试。"
    if status in {400, 404, 405, 413, 415, 422}:
        return "模型服务不接受当前请求，请在设置中核对模型名称、接入协议和参数。"
    return None
