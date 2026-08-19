"""通过 MATLAB Engine 在任务内复用一个本机 MATLAB 会话。"""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import re
import shutil
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.setting import settings
from app.schemas.response import OutputItem, ResultModel, StdErrModel, SystemMessage
from app.services.redis_manager import redis_manager
from app.tools.base_interpreter import BaseCodeInterpreter
from app.tools.notebook_serializer import NotebookSerializer
from app.utils.log_util import logger


class MatlabUnavailableError(RuntimeError):
    """MATLAB 未安装、Engine 不可加载、无许可证或启动失败。"""


class MatlabCodeInterpreter(BaseCodeInterpreter):
    """以本机 MATLAB 为主执行后端，并在同一任务中保留工作区变量。"""

    language = "matlab"
    backend_name = "MATLAB"

    def __init__(
        self,
        task_id: str,
        work_dir: str,
        notebook_serializer: NotebookSerializer,
        executable: str | None = None,
        timeout: float | None = None,
    ):
        super().__init__(task_id, work_dir, notebook_serializer)
        self.work_path = Path(work_dir).resolve()
        self.executable = executable or self.discover_executable()
        self.timeout = timeout or settings.MATLAB_EXECUTION_TIMEOUT_SECONDS
        self.calls_dir = self.work_path / "matlab_calls"
        self.metadata_path = self.work_path / "execution_backend.json"
        self.call_index = 0
        self.version = ""
        self.engine_module: Any | None = None
        self.engine: Any | None = None
        self._active_future: Any | None = None
        self._execution_lock = asyncio.Lock()
        self._dll_handles: list[Any] = []

    @classmethod
    def discover_executable(cls) -> str | None:
        """按显式配置、PATH 和常见安装目录查找 MATLAB。"""
        configured = (settings.MATLAB_EXECUTABLE or "").strip().strip('"')
        if configured and Path(configured).is_file():
            return str(Path(configured).resolve())

        path_match = shutil.which("matlab")
        if path_match:
            return str(Path(path_match).resolve())

        roots = [
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "MATLAB",
            Path("D:/Program Files/MATLAB"),
        ]
        candidates = [
            executable
            for root in roots
            if root.is_dir()
            for executable in root.glob("R*/bin/matlab.exe")
            if executable.is_file()
        ]
        return str(sorted(candidates, reverse=True)[0]) if candidates else None

    @property
    def matlab_root(self) -> Path:
        """从 ``<MATLAB>/bin/matlab.exe`` 解析 MATLAB 根目录。"""
        if not self.executable:
            raise MatlabUnavailableError("未找到 MATLAB 可执行文件")
        executable = Path(self.executable).resolve()
        if executable.parent.name.lower() != "bin":
            raise MatlabUnavailableError(f"无法从路径识别 MATLAB 根目录: {executable}")
        return executable.parent.parent

    async def initialize(self) -> None:
        """加载 MATLAB Engine 并真实启动一个可复用的 MATLAB 会话。"""
        if not self.executable:
            raise MatlabUnavailableError("未找到 MATLAB 可执行文件")

        self.work_path.mkdir(parents=True, exist_ok=True)
        self.calls_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.engine_module = await asyncio.to_thread(self._load_engine_module)
            startup_future = await asyncio.to_thread(
                self.engine_module.start_matlab,
                "-nodesktop -nosplash",
                background=True,
            )
            self._active_future = startup_future
            try:
                self.engine = await asyncio.to_thread(
                    startup_future.result,
                    settings.MATLAB_STARTUP_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                if not self._is_timeout_error(exc):
                    raise
                startup_future.cancel()
                raise MatlabUnavailableError(
                    "MATLAB Engine 启动超过 "
                    f"{settings.MATLAB_STARTUP_TIMEOUT_SECONDS:.0f} 秒"
                ) from exc
            finally:
                self._active_future = None

            self.version = await asyncio.to_thread(self._configure_engine)
        except asyncio.CancelledError:
            self._cancel_active_future()
            await self.cleanup()
            raise
        except MatlabUnavailableError:
            await self.cleanup()
            raise
        except Exception as exc:
            await self.cleanup()
            raise MatlabUnavailableError(f"MATLAB Engine 启动失败: {exc}") from exc

        self.backend_name = f"MATLAB {self.version}（常驻 Engine）"
        self.last_created_images = self._current_images()
        self._write_metadata()
        logger.info(f"MATLAB 常驻计算后端可用: {self.executable} ({self.version})")

    def _load_engine_module(self) -> Any:
        """直接加载当前 MATLAB 安装附带的 Engine，不要求全局 pip 安装。"""
        root = self.matlab_root
        arch = "win64" if os.name == "nt" else "glnxa64"
        engine_dist = root / "extern" / "engines" / "python" / "dist"
        engine_bin = engine_dist / "matlab" / "engine" / arch
        extern_bin = root / "extern" / "bin" / arch
        runtime_bin = root / "bin" / arch
        required = (engine_dist, engine_bin, extern_bin, runtime_bin)
        missing = [str(path) for path in required if not path.is_dir()]
        if missing:
            raise MatlabUnavailableError(
                "MATLAB Engine 组件不完整，缺少: " + ", ".join(missing)
            )

        os.environ["MWE_INSTALL"] = str(root)
        for path in (engine_dist, engine_bin, extern_bin):
            path_text = str(path)
            if path_text not in sys.path:
                sys.path.insert(0, path_text)

        path_prefixes = [str(runtime_bin), str(extern_bin), str(engine_bin)]
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(path_prefixes + [current_path])
        if hasattr(os, "add_dll_directory"):
            for path in (runtime_bin, extern_bin, engine_bin):
                self._dll_handles.append(os.add_dll_directory(str(path)))

        try:
            with warnings.catch_warnings():
                # R2025b 自带 abi3 二进制，已在本机 Python 3.13 实测通过。
                warnings.filterwarnings(
                    "ignore",
                    message=r".*Python.*3\.9.*3\.10.*3\.11.*3\.12.*",
                    category=UserWarning,
                )
                return importlib.import_module("matlab.engine")
        except Exception as exc:
            raise MatlabUnavailableError(f"无法加载 MATLAB Engine: {exc}") from exc

    def _configure_engine(self) -> str:
        if self.engine is None:
            raise MatlabUnavailableError("MATLAB Engine 未创建")
        self.engine.cd(str(self.work_path), nargout=0)
        self.engine.eval(
            "set(groot, 'defaultFigureVisible', 'off');",
            nargout=0,
        )
        return str(self.engine.version())

    async def _pre_execute_code(self) -> None:
        """常驻会话已在 initialize 中完成初始化。"""

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        """在常驻 MATLAB 会话中执行脚本，保留变量并捕获文本输出。"""
        async with self._execution_lock:
            if self.engine is None:
                error = "MATLAB Engine 未初始化或已经关闭"
                return error, True, error

            self.call_index += 1
            self.notebook_serializer.add_code_cell_to_notebook(code)
            user_path = self.calls_dir / f"call_{self.call_index:04d}.m"
            user_path.write_text(
                self._build_user_script(code),
                encoding="utf-8",
            )
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content="开始执行 MATLAB 代码"),
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            exception_text = ""
            try:
                await asyncio.to_thread(
                    self.engine.cd,
                    str(self.work_path),
                    nargout=0,
                )
                expression = f"run('{self._matlab_quote(user_path)}');"
                future = await asyncio.to_thread(
                    self.engine.eval,
                    expression,
                    nargout=0,
                    stdout=stdout,
                    stderr=stderr,
                    background=True,
                )
                self._active_future = future
                await asyncio.to_thread(future.result, self.timeout)
            except asyncio.CancelledError:
                self._cancel_active_future()
                raise
            except Exception as exc:
                if self._is_timeout_error(exc):
                    self._cancel_active_future()
                    exception_text = (
                        f"MATLAB 代码执行超过 {self.timeout:.0f} 秒，已中断"
                    )
                else:
                    exception_text = str(exc)
            finally:
                self._active_future = None

            cleaned_stdout = self._clean_output(stdout.getvalue())
            cleaned_stderr = self._clean_output(stderr.getvalue())
            combined = "\n".join(
                part
                for part in (cleaned_stdout, cleaned_stderr, exception_text)
                if part
            )
            combined = self._truncate_text(combined, 12000)
            error_occurred = bool(exception_text or cleaned_stderr)
            error_message = combined if error_occurred else ""
            content_to_display: list[OutputItem] = []

            if error_occurred:
                output = combined or "MATLAB 代码执行失败"
                self.notebook_serializer.add_code_cell_error_to_notebook(output)
                content_to_display.append(StdErrModel(msg=output))
                logger.error(f"MATLAB 代码执行失败: {output}")
            else:
                output = combined or "MATLAB 代码执行完成（无文本输出）"
                self.notebook_serializer.add_code_cell_output_to_notebook(output)
                content_to_display.append(
                    ResultModel(res_type="result", format="text", msg=output)
                )
                if self.current_section:
                    self.add_content(self.current_section, output)

            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(
                    content=(
                        "MATLAB 代码执行失败"
                        if error_occurred
                        else "MATLAB 代码执行完成"
                    ),
                    type="error" if error_occurred else "info",
                ),
            )
            await self._push_to_websocket(content_to_display)
            return combined, error_occurred, error_message

    def _build_user_script(self, code: str) -> str:
        """确保 run 临时切换到审计目录时，用户产物仍写入任务根目录。"""
        root = self._matlab_quote(self.work_path)
        return (
            "% Remit generated execution wrapper\n"
            f"cd('{root}');\n"
            "% Original agent code follows\n"
            f"{code}\n"
        )

    def _cancel_active_future(self) -> None:
        future = self._active_future
        if future is None:
            return
        try:
            future.cancel()
        except Exception as exc:
            logger.warning(f"中断 MATLAB 执行失败: {exc}")

    def _is_timeout_error(self, exc: BaseException) -> bool:
        """同时识别 Python 与 MATLAB Engine 各自定义的超时异常。"""
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return True
        engine_timeout = getattr(self.engine_module, "TimeoutError", None)
        return isinstance(engine_timeout, type) and isinstance(exc, engine_timeout)

    async def get_created_images(self, section: str) -> list[str]:
        """返回本节点新生成的论文图片。"""
        current_images = self._current_images()
        new_images = sorted(current_images - self.last_created_images)
        self.last_created_images = current_images
        logger.info(f"MATLAB 新创建图片: {new_images}")
        return new_images

    async def cleanup(self) -> None:
        """关闭当前任务独占的 MATLAB 会话。"""
        self._cancel_active_future()
        engine, self.engine = self.engine, None
        if engine is not None:
            try:
                # Windows 会锁定进程当前目录；先离开任务目录，保证临时任务可删除。
                await asyncio.to_thread(
                    engine.cd,
                    str(self.matlab_root),
                    nargout=0,
                )
                await asyncio.to_thread(engine.quit)
                logger.info("MATLAB 常驻会话已关闭")
            except Exception as exc:
                logger.warning(f"关闭 MATLAB 会话失败: {exc}")

    def send_interrupt_signal(self) -> None:
        """兼容本地解释器的主动中断接口。"""
        self._cancel_active_future()

    def _current_images(self) -> set[str]:
        suffixes = {".png", ".jpg", ".jpeg", ".svg", ".pdf"}
        return {
            str(path.relative_to(self.work_path))
            for path in self.work_path.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        }

    def _write_metadata(self) -> None:
        metadata = {
            "preferred_backend": "matlab",
            "selected_backend": "matlab",
            "language": self.language,
            "backend_mode": "persistent_engine",
            "executable": self.executable,
            "version": self.version,
            "bridge_python": sys.version.split()[0],
            "python_fallback": False,
            "probed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _matlab_quote(path: str | Path) -> str:
        return str(path).replace("'", "''").replace("\\", "/")

    @staticmethod
    def _clean_output(output: str) -> str:
        without_links = re.sub(r'<a\s+href="[^"]*">(.*?)</a>', r"\1", output)
        return without_links.strip()
