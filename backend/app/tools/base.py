"""Agent 可调用工具的登记与分发基础设施。

用法：在方法上挂 ``@tool(...)`` 装饰器声明 schema，
宿主类继承 :class:`BaseTool` 即可获得发现与调用能力。
"""

import inspect
from collections.abc import Callable
from typing import Any

from app.schemas.tool_result import ToolResult

# 装饰器写入方法的元数据属性名
_META_NAME = "__remit_tool_name__"
_META_SCHEMA = "__remit_tool_schema__"


def tool(
    name: str,
    description: str,
    parameters: dict[str, dict[str, Any]],
    required: list[str],
) -> Callable:
    """把一个方法声明为 LLM 可调用工具。

    Args:
        name: 对外暴露的工具名。
        description: 给模型看的功能说明。
        parameters: JSON Schema 风格的参数定义。
        required: 必填参数名列表。
    """

    def wrap(func: Callable) -> Callable:
        setattr(func, _META_NAME, name)
        setattr(
            func,
            _META_SCHEMA,
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": parameters,
                        "required": required,
                    },
                },
            },
        )
        return func

    return wrap


class BaseTool:
    """工具宿主基类：发现已登记的方法并按名调用。"""

    name: str = ""

    def __init__(self) -> None:
        self._tools_cache: list[dict[str, Any]] | None = None

    def _iter_registered(self):
        """遍历所有带工具元数据的绑定方法。"""
        for _, method in inspect.getmembers(self, inspect.ismethod):
            if hasattr(method, _META_NAME):
                yield method

    def get_tools(self) -> list[dict[str, Any]]:
        """返回全部工具的 function-calling schema（带缓存）。"""
        if self._tools_cache is None:
            self._tools_cache = [
                getattr(method, _META_SCHEMA) for method in self._iter_registered()
            ]
        return self._tools_cache

    def has_function(self, function_name: str) -> bool:
        """判断是否存在同名工具。"""
        return any(
            getattr(method, _META_NAME) == function_name
            for method in self._iter_registered()
        )

    async def invoke_function(self, function_name: str, **kwargs: Any) -> ToolResult:
        """按工具名分发调用。

        Raises:
            ValueError: 找不到对应工具。
        """
        for method in self._iter_registered():
            if getattr(method, _META_NAME) == function_name:
                return await method(**kwargs)
        raise ValueError(f"Tool '{function_name}' not found")
