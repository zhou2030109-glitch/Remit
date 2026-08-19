"""工具执行结果的统一封装。"""

from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    """工具执行结束后返回给调用方的结果载体。"""

    success: bool
    message: str | None = None
    data: Any | None = None
