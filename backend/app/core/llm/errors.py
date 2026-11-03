"""LLM Provider 的可分类错误。"""


def http_status_code(error: BaseException) -> int | None:
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def exception_chain(error: BaseException):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or (
            None if error.__suppress_context__ else error.__context__
        )


class NonRetryableLLMError(RuntimeError):
    """重复发送相同请求不会改善的上游错误。"""


class ModelStageBudgetExceeded(NonRetryableLLMError):
    """Durable stage allowance exhausted; provider configuration may be valid."""

    def __init__(
        self,
        *,
        used_calls,
        call_limit,
        elapsed_seconds,
        seconds_limit,
        phase="work",
        remaining_seconds=None,
        minimum_seconds=0,
    ):
        self.used_calls = used_calls
        self.call_limit = call_limit
        self.elapsed_seconds = elapsed_seconds
        self.seconds_limit = seconds_limit
        self.phase = phase
        if used_calls >= call_limit:
            detail = f"本阶段模型调用预算已用完（{used_calls}/{call_limit} 次）"
        elif elapsed_seconds >= seconds_limit:
            detail = (
                f"本阶段累计模型等待时间已达上限（{elapsed_seconds:.1f}/"
                f"{seconds_limit:.1f} 秒）"
            )
        else:
            detail = "本阶段可用模型额度不足，已暂停并保留结果审查所需额度"
            if remaining_seconds is not None and remaining_seconds < minimum_seconds:
                detail += f"（剩余 {remaining_seconds:.1f} 秒，新请求至少需要 {minimum_seconds:.0f} 秒）"
        super().__init__(
            detail + "；成果已保留，请查看本阶段模型额度后继续，普通恢复不会重置计数。"
        )


class TransientLLMError(RuntimeError):
    """供应商响应或传输异常，可在有限次数内重新请求。"""


class ResponseStreamInterruptedError(TransientLLMError):
    """响应流结束但没有终态；部分文本不能作为完整结果执行。"""


class ProviderRefusalError(NonRetryableLLMError):
    """模型因安全策略拒绝处理当前请求。"""

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        self.provider = provider
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        super().__init__(f"{provider} 模型 {model} 拒绝处理当前请求")
