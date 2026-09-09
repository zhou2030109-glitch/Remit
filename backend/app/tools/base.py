"""Agent 可调用工具的登记与分发基础设施。

用法：在方法上挂 ``@tool(...)`` 装饰器声明 schema，
宿主类继承 :class:`BaseTool` 即可获得发现与调用能力。
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.schemas.tool_result import ToolResult

# 装饰器写入方法的元数据属性名
_META_SPEC = "__remit_tool_spec__"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    properties: dict[str, dict[str, Any]]
    required: tuple[str, ...]

    def wire_schema(self) -> dict[str, Any]:
        parameters = {
            "type": "object",
            "properties": self.properties,
            "required": list(self.required),
        }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


def tool(
    name: str,
    description: str,
    parameters: dict[str, dict[str, Any]],
    required: list[str],
) -> Callable:
    """Attach an immutable function-calling specification to a method."""

    spec = ToolSpec(name, description, parameters, tuple(required))

    def wrap(func: Callable) -> Callable:
        setattr(func, _META_SPEC, spec)
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
            if hasattr(method, _META_SPEC):
                yield method

    @staticmethod
    def _spec(method: Callable) -> ToolSpec:
        return getattr(method, _META_SPEC)

    def get_tools(self) -> list[dict[str, Any]]:
        """返回全部工具的 function-calling schema（带缓存）。"""
        if self._tools_cache is None:
            self._tools_cache = [
                self._spec(method).wire_schema() for method in self._iter_registered()
            ]
        return self._tools_cache

    def has_function(self, function_name: str) -> bool:
        """判断是否存在同名工具。"""
        return any(
            self._spec(method).name == function_name
            for method in self._iter_registered()
        )

    async def invoke_function(self, function_name: str, **kwargs: Any) -> ToolResult:
        """按工具名分发调用。

        Raises:
            ValueError: 找不到对应工具。
        """
        for method in self._iter_registered():
            if self._spec(method).name == function_name:
                return await method(**kwargs)
        raise ValueError(f"Tool '{function_name}' not found")
