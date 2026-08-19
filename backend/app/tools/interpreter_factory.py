"""计算解释器工厂：本机 MATLAB 优先，Python 仅作可用性回退。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.config.setting import settings
from app.schemas.response import SystemMessage
from app.services.redis_manager import redis_manager
from app.tools.base_interpreter import BaseCodeInterpreter
from app.tools.e2b_interpreter import E2BCodeInterpreter
from app.tools.local_interpreter import LocalCodeInterpreter
from app.tools.matlab_interpreter import MatlabCodeInterpreter, MatlabUnavailableError
from app.tools.notebook_serializer import NotebookSerializer
from app.utils.log_util import logger


async def create_interpreter(
    kind: Literal["remote", "local"] = "local",
    *,
    task_id: str,
    work_dir: str,
    notebook_serializer: NotebookSerializer,
    timeout: float = 3000,
) -> BaseCodeInterpreter:
    """创建计算后端。

    Args:
        kind: 显式 ``remote`` 保留 E2B；工作流的 ``local`` 使用 MATLAB 优先策略。
        task_id: 任务 ID。
        work_dir: 任务工作目录。
        notebook_serializer: 执行记录序列化器。
        timeout: 单次代码执行上限。

    Returns:
        已通过启动探测的 MATLAB 或 Python 解释器。

    Raises:
        MatlabUnavailableError: 强制 MATLAB 且禁用回退时启动失败。
        ValueError: 配置了未知后端。
    """
    if kind == "remote":
        if not settings.E2B_API_KEY:
            raise ValueError("远程解释器需要 E2B_API_KEY")
        remote = await E2BCodeInterpreter.create(
            task_id=task_id,
            work_dir=work_dir,
            notebook_serializer=notebook_serializer,
        )
        await remote.initialize(timeout=timeout)  # type: ignore[reportCallIssue]
        return remote

    preferred = settings.CODE_EXECUTION_BACKEND.strip().lower()
    if preferred not in {"matlab", "python"}:
        raise ValueError(f"未知 CODE_EXECUTION_BACKEND: {preferred}")

    if preferred == "matlab":
        matlab = MatlabCodeInterpreter(
            task_id=task_id,
            work_dir=work_dir,
            notebook_serializer=notebook_serializer,
            timeout=timeout,
        )
        try:
            await matlab.initialize()
            await redis_manager.publish_message(
                task_id,
                SystemMessage(content=f"计算后端已选择 {matlab.backend_name}"),
            )
            return matlab
        except MatlabUnavailableError as exc:
            if not settings.MATLAB_FALLBACK_TO_PYTHON:
                raise
            fallback_reason = str(exc)
            logger.warning(f"MATLAB 不可用，回退本地 Python: {fallback_reason}")
            await redis_manager.publish_message(
                task_id,
                SystemMessage(
                    content=f"MATLAB 不可用，自动回退 Python：{fallback_reason}",
                    type="warning",
                ),
            )
            _write_python_fallback_metadata(work_dir, fallback_reason)

    python = LocalCodeInterpreter(
        task_id=task_id,
        work_dir=work_dir,
        notebook_serializer=notebook_serializer,
    )
    await python.initialize()
    return python


def _write_python_fallback_metadata(work_dir: str, reason: str) -> None:
    """记录为什么没有使用首选 MATLAB，供验收和论文审计。"""
    path = Path(work_dir) / "execution_backend.json"
    path.write_text(
        json.dumps(
            {
                "preferred_backend": "matlab",
                "selected_backend": "python",
                "language": "python",
                "python_fallback": True,
                "fallback_reason": reason,
                "probed_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
