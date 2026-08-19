"""LLM Provider 的可分类错误。"""


class NonRetryableLLMError(RuntimeError):
    """重复发送相同请求不会改善的上游错误。"""


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
