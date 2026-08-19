"""命令行启动时的横幅输出。"""

import shutil
from textwrap import dedent

_BANNER = r"""
========================================
  ____                _ _
 |  _ \ ___ _ __ ___ (_) |_
 | |_) / _ \ '_ ` _ \| | __|
 |  _ <  __/ | | | | | | |_
 |_| \_\___|_| |_| |_|_|\__|
========================================
"""


def center_cli_str(text: str, width: int | None = None) -> str:
    """把多行文本按终端宽度整体居中。"""
    columns = width or shutil.get_terminal_size().columns
    lines = text.split("\n")
    pad_to = max(len(line) for line in lines)
    return "\n".join(line.ljust(pad_to).center(columns) for line in lines)


def get_ascii_banner(center: bool = True) -> str:
    """返回 Remit 的 ASCII 横幅。"""
    banner = dedent(_BANNER).strip()
    return center_cli_str(banner) if center else banner
