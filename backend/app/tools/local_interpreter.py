"""本地执行后端：通过本机 Jupyter 内核跑 Python。"""

import asyncio
import os
import threading
from collections.abc import Iterator
from typing import Any

import jupyter_client

from app.schemas.response import OutputItem, ResultModel, StdErrModel, SystemMessage
from app.config.setting import settings
from app.services.redis_manager import redis_manager
from app.tools.base_interpreter import BaseCodeInterpreter
from app.tools.execution_guard import assess_code_execution
from app.tools.notebook_serializer import NotebookSerializer
from app.utils.log_util import logger

# iopub 消息分类：文本类输出
_TEXT_MARKS = {"stdout", "execute_result_text", "display_text"}
# iopub 消息分类：图片类输出
_IMAGE_MARKS = {
    "execute_result_png": "png",
    "execute_result_jpeg": "jpeg",
    "display_png": "png",
    "display_jpeg": "jpeg",
}
_MIME_BY_MARK = {
    "execute_result_text": "text/plain",
    "execute_result_html": "text/html",
    "execute_result_png": "image/png",
    "execute_result_jpeg": "image/jpeg",
    "display_text": "text/plain",
    "display_html": "text/html",
    "display_png": "image/png",
    "display_jpeg": "image/jpeg",
}


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
        self.interrupt_signal = False
        self._execution_abort = threading.Event()
        self._execution_lock = asyncio.Lock()

    # ---- 生命周期 ----

    async def initialize(self) -> None:
        logger.info("初始化本地内核")
        await asyncio.to_thread(self._start_kernel)
        await asyncio.to_thread(self._pre_execute_code)

    def _start_kernel(self) -> None:
        self.km, self.kc = jupyter_client.manager.start_new_kernel(
            kernel_name="python3", env=_kernel_env()
        )

    def _pre_execute_code(self) -> None:
        """切到任务目录并装载中文字体，保证图表渲染正常。"""
        bootstrap = (
            "import os\n"
            f"work_dir = r'{self.work_dir}'\n"
            "os.makedirs(work_dir, exist_ok=True)\n"
            "os.chdir(work_dir)\n"
            "print('当前工作目录:', os.getcwd())\n"
            # 清掉 matplotlib 字体缓存，避免旧缓存让 addfont 失效
            "import matplotlib\n"
            "import matplotlib.pyplot as plt\n"
            "from matplotlib import font_manager\n"
            "import glob as _glob, pathlib as _pl\n"
            "_cache_dir = _pl.Path(matplotlib.get_cachedir())\n"
            "for _cache_file in _glob.glob(str(_cache_dir / 'fontlist*.json')):\n"
            "    _pl.Path(_cache_file).unlink(missing_ok=True)\n"
            "font_manager.fontManager.__init__()\n"
            "_loaded = False\n"
            "for _f in os.listdir(work_dir):\n"
            "    if _f.lower().endswith(('.ttf', '.otf', '.ttc')):\n"
            "        font_manager.fontManager.addfont(os.path.join(work_dir, _f))\n"
            "        _loaded = True\n"
            "if _loaded:\n"
            "    print(f'中文字体已加载，可用字体数: {len(font_manager.fontManager.ttflist)}')\n"
            "plt.rcParams['font.sans-serif'] = ['SimHei', 'Heiti SC', 'STHeiti', "
            "'PingFang SC', 'Noto Sans CJK SC', 'Noto Sans SC', "
            "'WenQuanYi Micro Hei', 'Microsoft YaHei', 'sans-serif']\n"
            "plt.rcParams['axes.unicode_minus'] = False\n"
            "plt.rcParams['font.family'] = 'sans-serif'\n"
        )
        self._run_raw(bootstrap)

    async def cleanup(self) -> None:
        if self.kc is None or self.km is None:
            return
        await asyncio.to_thread(self._shutdown_kernel, False)
        logger.info("关闭内核")

    def _shutdown_kernel(self, now: bool) -> None:
        if self.kc is not None:
            try:
                self.kc.stop_channels()
            except Exception:
                pass
        if self.km is not None:
            self.km.shutdown_kernel(now=now)

    def restart_jupyter_kernel(self) -> None:
        """关掉旧内核、起新内核并重跑引导代码。"""
        self._shutdown_kernel(True)
        self._start_kernel()
        self.interrupt_signal = False
        self._execution_abort.clear()
        os.makedirs(self.work_dir, exist_ok=True)
        self._pre_execute_code()

    def send_interrupt_signal(self) -> None:
        self.interrupt_signal = True
        self._execution_abort.set()
        if self.km is not None:
            try:
                self.km.interrupt_kernel()
            except Exception as exc:
                logger.warning(f"中断 Python 内核失败: {exc}")

    # ---- 代码执行 ----

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        async with self._execution_lock:
            return await self._execute_code_locked(code)

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

        await redis_manager.publish_message(
            self.task_id, SystemMessage(content="开始执行代码")
        )
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
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content=error, type="error"),
            )
            await self._push_to_websocket([StdErrModel(msg=error)])
            return error, True, error
        except asyncio.CancelledError:
            self.send_interrupt_signal()
            raise

        await redis_manager.publish_message(
            self.task_id, SystemMessage(content="代码执行完成")
        )

        text_parts: list[str] = []
        display: list[OutputItem] = []
        failed = False
        error_detail = ""

        for mark, payload in marks:
            if mark in _TEXT_MARKS:
                text_parts.append(self._truncate_text(f"[{mark}]\n{payload}"))
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

        combined = "\n".join(text_parts)
        if self.current_section and combined:
            self.add_content(self.current_section, combined)

        await self._push_to_websocket(display)
        return combined, failed, error_detail

    async def _reject_execution(self, reason: str) -> tuple[str, bool, str]:
        self.notebook_serializer.add_code_cell_error_to_notebook(reason)
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content=reason, type="warning"),
        )
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
        except Exception as exc:
            logger.warning(f"Python 内核未在宽限期内停止，将强制重启: {exc}")
        await asyncio.to_thread(self.restart_jupyter_kernel)

    def _run_raw(self, code: str) -> list[tuple[str, str]]:
        """把代码发给内核，收割 iopub 消息并归类为 ``(标记, 内容)``。"""
        if self.kc is None or self.km is None:
            raise RuntimeError("Jupyter kernel client is not initialized")
        kc, km = self.kc, self.km
        kc.execute(code)
        collected: list[tuple[str, str]] = []
        for msg in self._harvest_iopub(kc, km):
            collected.extend(self._classify(msg))
        return collected

    def _harvest_iopub(self, kc: Any, km: Any) -> Iterator[dict]:
        """阻塞读取 iopub 直到内核回到 idle；收到中断信号时打断内核。"""
        while True:
            if self._execution_abort.is_set():
                return
            if self.interrupt_signal:
                km.interrupt_kernel()
                self.interrupt_signal = False
            try:
                msg = kc.get_iopub_msg(timeout=1)
            except Exception:
                if self._execution_abort.is_set():
                    return
                if self.interrupt_signal:
                    km.interrupt_kernel()
                    self.interrupt_signal = False
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
