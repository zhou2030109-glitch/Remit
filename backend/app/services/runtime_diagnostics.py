"""本地检查与允许清单诊断；绝不导出提示词、附件、密钥或原始日志。"""

import asyncio
import hashlib
import json
import platform
import sqlite3
import sys
import tempfile
from contextlib import closing
import time
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache

from app.config.setting import settings


@lru_cache(maxsize=1)
def _source_digest() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_info() -> dict:
    """只返回运行环境元数据，构建包使用打包时固化的标识。"""
    manifest = Path(__file__).resolve().parents[2] / "build-info.json"
    build = (
        json.loads(manifest.read_text(encoding="utf-8"))
        if manifest.is_file()
        else {"source_sha256": _source_digest()}
    )
    return {
        "version": "0.1.0",
        "build": {
            key: build.get(key, "unknown")
            for key in ("commit", "built_at", "source_sha256")
        },
        "launch_mode": "package" if manifest.is_file() else "source",
        "python": platform.python_version(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "executor": settings.CODE_EXECUTION_BACKEND,
        "data_directory": str(Path("project").resolve()),
    }


async def local_check() -> dict:
    """真实运行固定检查代码；不读用户项目，不调用模型。"""
    from app.services.task_bus import task_bus

    started = time.monotonic()
    checks = []
    root = Path("project")
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="remit-check-", dir=root) as directory:
        try:
            with closing(sqlite3.connect(Path(directory) / "check.sqlite3")) as db:
                db.execute("CREATE TABLE probe(value INTEGER)")
                db.execute("INSERT INTO probe VALUES(7)")
                db.commit()
                assert db.execute("SELECT value FROM probe").fetchone()[0] == 7
            checks.append(
                {
                    "id": "storage",
                    "label": "数据目录与数据库读写",
                    "required": True,
                    "status": "passed",
                }
            )
        except (OSError, sqlite3.Error, AssertionError):
            checks.append(
                {
                    "id": "storage",
                    "label": "数据目录与数据库读写",
                    "required": True,
                    "status": "failed",
                    "detail": "数据目录无法完成读写，请检查空间与权限。",
                }
            )
        try:
            healthy, _ = await asyncio.wait_for(task_bus.health(1.5), 4)
        except (TimeoutError, OSError):
            healthy = False
        checks.append(
            {
                "id": "task_bus",
                "label": "任务服务",
                "required": True,
                "status": "passed" if healthy else "failed",
                "detail": ""
                if healthy
                else "任务消息档案不可读写，请检查数据目录后重新启动。",
            }
        )
        worker = Path(__file__).with_name("runtime_check_worker.py")
        if not worker.is_file():
            worker = worker.with_suffix(".pyc")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(worker),
            str(Path(directory).resolve()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 100)
            checks.extend(json.loads(output.decode("utf-8")))
        except (TimeoutError, ValueError, UnicodeError):
            checks.append(
                {
                    "id": "python",
                    "label": "Python 内核与工具",
                    "required": True,
                    "status": "failed",
                    "detail": "检查超时或工具未正常退出；请检查本地运行时。",
                }
            )
        finally:
            if process.returncode is None:
                # 只回收本次检查创建的进程树，绝不按进程名终止程序。
                import psutil

                try:
                    owned = psutil.Process(process.pid)
                    for child in owned.children(recursive=True):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    owned.kill()
                except psutil.NoSuchProcess:
                    pass
                await process.wait()
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "checks": checks,
        "ready": all(c["status"] == "passed" for c in checks if c["required"]),
        "model_verified": False,
        "runtime": runtime_info(),
        "scope": "固定代码环境检查，不是 AI 解题；MATLAB 需另行检查许可证与启动。",
    }


def diagnostic_preview(root: Path | None = None) -> dict:
    """创建可以原样导出的预览，严格只收集允许字段。"""
    info = runtime_info()
    info.pop("data_directory", None)
    report = {
        "schema_version": 1,
        "environment": info,
        "model_calls": "not_included",
        "content_policy": "不包含密钥、地址、聊天、论文、代码、附件或原始错误文本。",
    }
    if root is None:
        return report
    from app.services.team_state import read_json
    from app.services.call_ledger import summary as call_summary

    state = read_json(root / "workflow_state.json")
    report["model_calls"] = call_summary(root)
    from app.services.work_timing import summary as work_summary

    report["work_timing"] = work_summary(root)
    statuses = {
        "preparing",
        "running",
        "stopping",
        "stopped",
        "failed",
        "completed",
        "awaiting_approval",
    }
    report["task"] = {
        "id": root.name,
        "status": state.get("status") if state.get("status") in statuses else "unknown",
        "completed_step_count": len(state.get("completed_nodes") or []),
        "approval_pending": bool(state.get("pending_approval")),
    }
    files = []
    # 不泄露附件名称，也不读取其内容；仅登记工作流拥有的元数据文件。
    for name in ("workflow_state.json", "paper/workspace.json", "paper/compile.json"):
        path = root / name
        if path.is_file() and path.resolve().is_relative_to(root.resolve()):
            files.append(
                {
                    "name": name,
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    report["metadata_files"] = files
    return report
