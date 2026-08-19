"""基于 Rich 的终端展示工具。

仅用于本地开发态的可视化提示，正式日志走 loguru。
"""

from typing import Any

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.utils.log_util import logger

_console = Console()

_LEVEL_STYLE: dict[str, dict[str, str]] = {
    "success": {"emoji": "✅", "color": "green", "prefix": "成功"},
    "error": {"emoji": "❌", "color": "red", "prefix": "错误"},
    "warning": {"emoji": "⚠️", "color": "yellow", "prefix": "警告"},
    "info": {"emoji": "ℹ️", "color": "blue", "prefix": "信息"},
    "debug": {"emoji": "🐞", "color": "magenta", "prefix": "调试"},
}

_AGENT_BADGE: dict[str, str] = {
    "CoderAgent": "bold purple on green",
    "WriterAgent": "bold purple on yellow",
    "test_agent": "bold white on blue",
}


def _styled_text(message: str, level: str, **override: str | None) -> Text:
    """拼装带 emoji / 前缀 / 配色的富文本。"""
    style = _LEVEL_STYLE.get(level, {})
    emoji = override.get("emoji") or style.get("emoji", "")
    color = override.get("color") or style.get("color", "white")
    prefix = override.get("prefix") or style.get("prefix", "")

    text = Text()
    if emoji:
        text.append(f"{emoji} ", style="bold")
    if prefix:
        text.append(f"{prefix}: ", style=f"bold {color}")
    text.append(message, style=color)
    return text


def _banner(icon: str, headline: str, headline_style: str, border: str) -> None:
    """输出居中的阶段横幅，同时写入日志便于事后检索。"""
    _console.print()
    body = Text()
    body.append(f"{icon} ", style="bold")
    body.append(headline, style=headline_style)
    _console.print(Panel.fit(body, border_style=border, padding=(1, 4)))


class RichPrinter:
    """面向终端的美化输出集合（全部为类方法，无实例状态）。"""

    @classmethod
    def _panel(cls, message: str, level: str, **kwargs: Any) -> None:
        text = _styled_text(
            message,
            level,
            color=kwargs.get("color"),
            emoji=kwargs.get("emoji"),
            prefix=kwargs.get("prefix"),
        )
        panel_args = {
            "title": kwargs.get("title") or level.upper(),
            "border_style": kwargs.get("color") or _LEVEL_STYLE[level]["color"],
            "padding": (1, 4),
            **(kwargs.get("panel_kwargs") or {}),
        }
        _console.print(Panel.fit(text, **panel_args))

    @classmethod
    def success(cls, message: str, **kwargs: Any) -> None:
        cls._panel(message, "success", **kwargs)

    @classmethod
    def error(cls, message: str, **kwargs: Any) -> None:
        cls._panel(message, "error", **kwargs)

    @classmethod
    def warning(cls, message: str, **kwargs: Any) -> None:
        cls._panel(message, "warning", **kwargs)

    @staticmethod
    def print_agent_msg(message: str, agent_name: str) -> None:
        logger.info(f"{agent_name}: {message}")
        badge = _AGENT_BADGE.get(agent_name, "bold white")
        rprint(f"[{badge}]{agent_name}[/{badge}]: {message}")

    @classmethod
    def table(
        cls,
        headers: list[str],
        rows: list[list[Any]],
        title: str = "数据表格",
        column_styles: list[str] | None = None,
    ) -> None:
        styles = column_styles or ["magenta"] * len(headers)
        grid = Table(title=title, show_header=True, header_style="bold cyan")
        for header, style in zip(headers, styles, strict=False):
            grid.add_column(header, style=style)
        for row in rows:
            grid.add_row(*(str(cell) for cell in row))
        _console.print(grid)

    @classmethod
    def workflow_start(cls) -> None:
        _banner("🚀", "开始执行工作流", "bold blue", "blue")
        logger.info("workflow | start")

    @classmethod
    def workflow_end(cls) -> None:
        _banner("✨", "工作流执行完成", "bold green", "green")
        logger.info("workflow | end")

    @classmethod
    def agent_start(cls, agent_name: str) -> None:
        _banner("🤖", f"Agent: {agent_name} 开始执行", "bold cyan", "blue")
        logger.info("agent | {} | start", agent_name)

    @classmethod
    def agent_end(cls, agent_name: str) -> None:
        _banner("✨", f"Agent: {agent_name} 执行完成", "bold cyan", "green")
        logger.info("agent | {} | end", agent_name)
