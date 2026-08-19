"""Agent 工具的 JSON Schema 声明。

description 文案同时是模型指令与测试断言对象，改动需同步
tests/test_matlab_interpreter.py。
"""

from __future__ import annotations

from typing import Any

_EXECUTION_DESCRIPTIONS = {
    "matlab": (
        "Execute MATLAB code with the installed local MATLAB. Use MATLAB syntax only; "
        "the working directory already contains all user files. Text printed by fprintf/disp "
        "is returned, variables are restored between calls when serializable, and figures must "
        "be saved with exportgraphics or print. Do not send Python code."
    ),
    "python": (
        "Execute Python code in the configured Python kernel. The kernel retains variables "
        "between calls. Save plots and result files in the working directory."
    ),
}


def _execution_description(language: str) -> str:
    return _EXECUTION_DESCRIPTIONS.get(language, _EXECUTION_DESCRIPTIONS["python"])


def _code_property(language: str) -> dict[str, Any]:
    return {
        "code": {
            "type": "string",
            "description": f"Executable {language.upper()} source code",
        }
    }


def _openai_tool(name: str, description: str, properties: dict[str, Any]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ["code" if "code" in properties else "query"],
                "additionalProperties": False,
            },
        },
    }


def _anthropic_tool(name: str, description: str, properties: dict[str, Any]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": ["code" if "code" in properties else "query"],
        },
    }


def _make_tools(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    anthropic: bool,
) -> list[dict]:
    factory = _anthropic_tool if anthropic else _openai_tool
    return [factory(name, description, properties)]


def get_coder_tools(language: str, *, anthropic: bool = False) -> list[dict]:
    """按执行后端语言生成代码执行工具的 schema。"""
    return _make_tools(
        "execute_code",
        _execution_description(language),
        _code_property(language),
        anthropic=anthropic,
    )


def get_writer_tools(*, anthropic: bool = False) -> list[dict]:
    """生成写作 Agent 的文献检索工具 schema。"""
    return _make_tools(
        "search_papers",
        "Search for papers using a query string.",
        {"query": {"type": "string", "description": "The query string"}},
        anthropic=anthropic,
    )


coder_tools = get_coder_tools("python")
coder_tools_anthropic = get_coder_tools("python", anthropic=True)
writer_tools = get_writer_tools()
writer_tools_anthropic = get_writer_tools(anthropic=True)
