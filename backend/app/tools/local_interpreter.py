"""本地执行后端：通过本机 Jupyter 内核跑 Python。"""

import asyncio
import os
import queue
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any

import jupyter_client
import psutil

from app.schemas.response import OutputItem, ResultModel, StdErrModel
from app.config.setting import settings
from app.services.redis_manager import redis_manager
from app.tools.base_interpreter import BaseCodeInterpreter
from app.tools.execution_guard import assess_code_execution
from app.tools.notebook_serializer import NotebookSerializer
from app.utils.log_util import logger

# iopub 消息分类：文本类输出
_TEXT_MARKS = {"stdout", "execute_result_text", "display_text"}
# A 1,000-character head/tail window hid the middle of ordinary file listings
# and short scripts, causing repeated reads. Bound the whole tool result once.
_TOOL_TEXT_LIMIT = 8000
# iopub 消息分类：图片类输出
_IMAGE_MARKS = {
    "execute_result_png": "png",
    "execute_result_jpeg": "jpeg",
    "display_png": "png",
    "display_jpeg": "jpeg",
}


class _KernelInterrupted(RuntimeError):
    """The caller stopped this execution before a complete result was received."""


async def _settle_owned_task(task: asyncio.Task) -> Any:
    """取消后的清理仍须等自身线程结束，重复停止不能把线程遗留在后台。"""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise


def _kernel_env() -> dict[str, str]:
    """Windows 中文系统下强制 UTF-8，避免 GBK 乱码。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


class LocalCodeInterpreter(BaseCodeInterpreter):
    """以本机 python3 内核为执行环境的解释器。"""

    language = "python"
    backend_name = "Python（本地 Jupyter 回退）"

    def __init__(
        self,
        task_id: str,
        work_dir: str,
        notebook_serializer: NotebookSerializer,
        timeout: float | None = None,
    ) -> None:
        super().__init__(task_id, work_dir, notebook_serializer)
        requested_timeout = timeout or settings.PYTHON_EXECUTION_TIMEOUT_SECONDS
        self.timeout = max(
            0.01,
            min(
                float(requested_timeout),
                float(settings.CODE_EXECUTION_HARD_LIMIT_SECONDS),
            ),
        )
        self.km = None
        self.kc = None
        self._execution_abort = threading.Event()
        self._execution_lock = asyncio.Lock()
        self._owned_children: set[psutil.Process] = set()

    # ---- 生命周期 ----

    async def initialize(self) -> None:
        logger.info("初始化本地内核")
        self._execution_abort.clear()
        worker = asyncio.create_task(asyncio.to_thread(self._initialize_kernel))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            self.send_interrupt_signal()
            try:
                await _settle_owned_task(worker)
            except Exception:
                pass
            await self.cleanup()
            raise

    def _initialize_kernel(self) -> None:
        try:
            self._start_kernel()
            self._pre_execute_code()
        except BaseException:
            self._shutdown_kernel(True)
            raise

    def _start_kernel(self) -> None:
        os.makedirs(self.work_dir, exist_ok=True)
        self.km = jupyter_client.KernelManager(kernel_name="python3")
        # 必须使用产品当前运行时，不能被开发机的全局 kernelspec 指向其他 Python。
        self.km.kernel_spec.argv = [
            sys.executable,
            "-m",
            "ipykernel_launcher",
            "-f",
            "{connection_file}",
        ]
        self.km.start_kernel(env=_kernel_env(), cwd=os.path.abspath(self.work_dir))
        self.kc = self.km.blocking_client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=25)

    def _pre_execute_code(self) -> None:
        """切到任务目录并装载中文字体，保证图表渲染正常。"""
        bootstrap = (
            "import os\n"
            f"work_dir = {os.path.abspath(self.work_dir)!r}\n"
            "os.makedirs(work_dir, exist_ok=True)\n"
            "os.chdir(work_dir)\n"
        )
        from app.tools.plot_fonts import bootstrap as font_bootstrap

        output = self._run_raw(
            bootstrap + "\n" + font_bootstrap(os.path.abspath(self.work_dir)),
            timeout=30,
        )
        if any(kind == "error" for kind, _ in output):
            raise RuntimeError(
                "Python 绘图环境初始化失败，请运行本地自检确认字体与依赖。"
            )

    async def cleanup(self) -> None:
        self._execution_abort.set()
        worker = asyncio.create_task(asyncio.to_thread(self._shutdown_kernel, True))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            await _settle_owned_task(worker)
            raise
        logger.info("关闭内核")

    def _shutdown_kernel(self, now: bool) -> None:
        if self.km is not None:
            pid = getattr(self.km.provisioner, "pid", None)
            if pid:
                try:
                    self._owned_children.update(
                        psutil.Process(pid).children(recursive=True)
                    )
                except psutil.NoSuchProcess:
                    pass
        # 保存进程对象（含创建时间），只回收此内核的后代，不按名称查杀。
        try:
            if self.km is not None:
                self.km.shutdown_kernel(now=now)
        finally:
            try:
                for child in self._owned_children:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(list(self._owned_children), timeout=3)
                self._owned_children = set(alive)
            finally:
                if self.kc is not None:
                    self.kc.stop_channels()
        if self._owned_children:
            raise RuntimeError("Python 子进程尚未停止，不能将任务标记为已停止。")
        self.km = self.kc = None

    def send_interrupt_signal(self) -> None:
        self._execution_abort.set()
        if self.km is not None:
            try:
                self.km.interrupt_kernel()
            except Exception as exc:
                logger.warning(f"中断 Python 内核失败: {exc}")

    # ---- 代码执行 ----

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        from app.services.work_timing import ameasure

        acquired = False
        try:
            async with ameasure(self.task_id, "queue"):
                await self._execution_lock.acquire()
                acquired = True
            return await self._execute_code_locked(code)
        finally:
            if acquired:
                self._execution_lock.release()

    async def _execute_code_locked(self, code: str) -> tuple[str, bool, str]:
        logger.info(f"执行代码: {code}")
        assessment = assess_code_execution(
            code,
            language=self.language,
            work_dir=self.work_dir,
        )
        if not assessment.allowed:
            return await self._reject_execution(assessment.reason)

        self.notebook_serializer.add_code_cell_to_notebook(code)

        await redis_manager.notify(self.task_id, "开始执行代码")
        self._execution_abort.clear()
        worker = asyncio.create_task(asyncio.to_thread(self._run_raw, code))
        try:
            async with self.execution_heartbeat("Python 代码"):
                marks = await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=self.timeout,
                )
        except asyncio.TimeoutError:
            await self._recover_kernel_after_timeout(worker)
            error = f"Python 代码执行超过 {self.timeout:g} 秒，内核已中断并重启"
            self.notebook_serializer.add_code_cell_error_to_notebook(error)
            await redis_manager.notify(self.task_id, error, "error")
            await self._push_to_websocket([StdErrModel(msg=error)])
            return error, True, error
        except asyncio.CancelledError:
            self.send_interrupt_signal()
            try:
                await self.cleanup()
            finally:
                try:
                    await _settle_owned_task(worker)
                except Exception:
                    pass
            raise

        await redis_manager.notify(self.task_id, "代码执行完成")

        text_parts: list[str] = []
        display: list[OutputItem] = []
        failed = False
        error_detail = ""

        for mark, payload in marks:
            if mark in _TEXT_MARKS:
                text_parts.append(f"[{mark}]\n{payload}")
                display.append(
                    ResultModel(res_type="result", format="text", msg=payload)
                )
                self.notebook_serializer.add_code_cell_output_to_notebook(payload)
            elif mark in _IMAGE_MARKS:
                fmt = _IMAGE_MARKS[mark]
                text_parts.append(f"[{mark} 图片已生成，内容为 base64，未展示]")
                self.notebook_serializer.add_image_to_notebook(payload, f"image/{fmt}")
                display.append(ResultModel(res_type="result", format=fmt, msg=payload))
            elif mark == "error":
                failed = True
                error_detail = self._truncate_text(payload)
                logger.error(f"执行错误: {error_detail}")
                text_parts.append(error_detail)
                self.notebook_serializer.add_code_cell_error_to_notebook(payload)
                display.append(StdErrModel(msg=payload))

        combined = self._truncate_text("\n".join(text_parts), _TOOL_TEXT_LIMIT)
        if self.current_section and combined:
            self.add_content(self.current_section, combined)

        await self._push_to_websocket(display)
        return combined, failed, error_detail

    async def _reject_execution(self, reason: str) -> tuple[str, bool, str]:
        self.notebook_serializer.add_code_cell_error_to_notebook(reason)
        await redis_manager.notify(self.task_id, reason, "warning")
        await self._push_to_websocket([StdErrModel(msg=reason)])
        return reason, True, reason

    async def _recover_kernel_after_timeout(
        self,
        worker: asyncio.Task[list[tuple[str, str]]],
    ) -> None:
        """先软中断，再强制重建内核，避免失控代码留在后台继续跑。"""
        self.send_interrupt_signal()
        grace = max(float(settings.CODE_EXECUTION_CANCEL_GRACE_SECONDS), 0.01)
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=grace)
        except _KernelInterrupted:
            pass
        except Exception as exc:
            logger.warning(f"Python 内核未在宽限期内停止，将强制重启: {exc}")
        finally:
            try:
                await self.cleanup()
            finally:
                # 旧消息线程终止后才清除中断标记并创建新内核。
                try:
                    await _settle_owned_task(worker)
                except Exception:
                    pass
        await self.initialize()

    def _run_raw(
        self, code: str, *, timeout: float | None = None
    ) -> list[tuple[str, str]]:
        """把代码发给内核，收割 iopub 消息并归类为 ``(标记, 内容)``。"""
        if self.kc is None or self.km is None:
            raise RuntimeError("Jupyter kernel client is not initialized")
        kc, km = self.kc, self.km
        if self._execution_abort.is_set():
            raise _KernelInterrupted("Python 执行已停止，部分输出不能视为完成。")
        request_id = kc.execute(code, allow_stdin=False)
        collected: list[tuple[str, str]] = []
        for msg in self._harvest_iopub(kc, km, request_id, timeout=timeout):
            collected.extend(self._classify(msg))
        return collected

    def _harvest_iopub(
        self, kc: Any, km: Any, request_id: str, *, timeout: float | None = None
    ) -> Iterator[dict]:
        """阻塞读取 iopub 直到内核回到 idle；收到中断信号时打断内核。"""
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            if self._execution_abort.is_set():
                raise _KernelInterrupted("Python 执行已停止，部分输出不能视为完成。")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Python 内核初始化执行超时")
            try:
                msg = kc.get_iopub_msg(timeout=1)
            except queue.Empty:
                if not km.is_alive():
                    raise RuntimeError("Python 内核已退出，当前执行没有完整结果。")
                continue
            if msg.get("parent_header", {}).get("msg_id") != request_id:
                continue
            yield msg
            if (
                msg["msg_type"] == "status"
                and msg["content"].get("execution_state") == "idle"
            ):
                return

    def _classify(self, msg: dict) -> list[tuple[str, str]]:
        """把一条 iopub 消息映射为零到多条输出标记。"""
        msg_type = msg["msg_type"]
        content = msg["content"]

        if msg_type == "stream":
            if content.get("name") == "stdout":
                return [("stdout", content["text"])]
            return []

        if msg_type in ("execute_result", "display_data"):
            data = content.get("data", {})
            prefix = "execute_result" if msg_type == "execute_result" else "display"
            out = []
            for key, mime in (
                ("text", "text/plain"),
                ("html", "text/html"),
                ("png", "image/png"),
                ("jpeg", "image/jpeg"),
            ):
                if mime in data:
                    out.append((f"{prefix}_{key}", data[mime]))
            return out

        if msg_type == "error":
            traceback = "\n".join(content.get("traceback", []))
            return [("error", self.delete_color_control_char(traceback))]

        return []

    # ---- 产物 ----

    async def get_created_images(self, section: str) -> list[str]:
        """对比上次调用，列出工作目录里新出现的图片。"""
        current = {
            name
            for name in os.listdir(self.work_dir)
            if name.endswith((".png", ".jpg", ".jpeg"))
        }
        fresh = current - self.last_created_images
        self.last_created_images = current
        logger.info(f"新创建的图片列表: {fresh}")
        return list(fresh)
