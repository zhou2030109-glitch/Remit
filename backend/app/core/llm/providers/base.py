"""Provider 接口定义。

每家模型厂商一个实现，把各自的 API 差异关在本层之内。
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from app.core.llm.types import StandardResponse

DeltaCallback = Callable[[str], Awaitable[None]]


class BaseProvider(ABC):
    """统一的模型调用契约。"""

    @abstractmethod
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
        """发起一次对话调用。

        Args:
            messages: OpenAI 风格的消息历史。
            model: 模型 ID。
            api_key: 厂商密钥。
            base_url: 自定义接入点（中转 / 私有部署）。
            tools: OpenAI 风格的工具声明。
            tool_choice: 工具选择策略。
            max_tokens: 输出上限。
            top_p: 核采样参数。
            on_delta: 流式增量回调；不支持的实现可忽略。
            reasoning_effort: 推理档位；不支持的实现可忽略。

        Returns:
            归一化响应。
        """
