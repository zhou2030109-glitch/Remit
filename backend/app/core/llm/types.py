"""模型调用的归一化结果类型。

不同厂商 API 的响应在 Provider 层就被摊平成这里的结构，
上层 Agent 只面对这一套类型。
"""

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """一次函数调用请求。"""

    id: str
    name: str
    arguments: str  # JSON 字符串


@dataclass
class Usage:
    """一次调用的 token 计量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class StandardResponse:
    """归一化后的模型响应。"""

    content: str | None = None
    reasoning_content: str | None = None
    finish_reason: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
