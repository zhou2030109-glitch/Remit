"""Remit 日志设施。

基于 loguru 提供统一的控制台 + 文件日志，
其他模块统一 ``from app.utils.log_util import logger``。
"""

import sys
from datetime import date
from pathlib import Path

from loguru import logger as _logger  # type: ignore[import-unresolved]

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def _configure() -> "_logger":
    """装配 loguru：stderr 彩色输出 + 按天滚动的错误日志文件。"""
    log_dir = Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    _logger.remove()
    _logger.add(sys.stderr, format=_FORMAT, enqueue=False)
    _logger.add(
        log_dir / f"{date.today():%Y-%m-%d}_error.log",
        format=_FORMAT,
        rotation="50 MB",
        encoding="utf-8",
        compression="zip",
        enqueue=False,
    )
    return _logger


logger = _configure()
