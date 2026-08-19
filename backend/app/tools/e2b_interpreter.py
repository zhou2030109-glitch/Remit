"""E2B 云沙箱执行后端。

把代码放进远端沙箱跑，执行产物再同步回本地任务目录，
适合不愿在本机配置 Python 环境的场景。
"""

import json
import os
from pathlib import Path
from typing import Any

from e2b_code_interpreter import AsyncSandbox  # type: ignore[import-unresolved]

from app.config.setting import settings
from app.schemas.response import (
    ErrorModel,
    OutputItem,
    ResultModel,
    StdErrModel,
    StdOutModel,
    SystemMessage,
)
from app.services.redis_manager import redis_manager
from app.tools.base_interpreter import BaseCodeInterpreter
from app.tools.notebook_serializer import NotebookSerializer
from app.utils.file_types import is_sandbox_upload_file
from app.utils.log_util import logger

_SANDBOX_HOME = "/home/user"
# 沙箱里的 shell 启动文件没有同步价值
_SYNC_SKIP = {".bash_logout", ".bashrc", ".profile"}
# 可直接回传给模型阅读的富文本格式
_TEXTUAL_FORMATS = {"text", "html", "markdown", "json"}
# 只提示存在、不回传内容的二进制格式
_BINARY_FORMATS = {"png", "jpeg", "svg", "pdf"}

_FONT_BOOTSTRAP = (
    "import os\n"
    "import matplotlib\n"
    "import matplotlib.pyplot as plt\n"
    "from matplotlib import font_manager\n"
    "import pathlib as _pl, glob as _glob\n"
    "_cache_dir = _pl.Path(matplotlib.get_cachedir())\n"
    "for _cache_file in _glob.glob(str(_cache_dir / 'fontlist*.json')):\n"
    "    _pl.Path(_cache_file).unlink(missing_ok=True)\n"
    "font_manager.fontManager.__init__()\n"
    "_loaded = False\n"
    f"for _f in os.listdir('{_SANDBOX_HOME}'):\n"
    "    if _f.lower().endswith(('.ttf', '.otf', '.ttc')):\n"
    f"        font_manager.fontManager.addfont(os.path.join('{_SANDBOX_HOME}', _f))\n"
    "        _loaded = True\n"
    "if _loaded:\n"
    "    print(f'中文字体已加载，可用字体数: {len(font_manager.fontManager.ttflist)}')\n"
    "plt.rcParams['font.sans-serif'] = ['SimHei', 'Heiti SC', 'STHeiti', "
    "'PingFang SC', 'Noto Sans CJK SC', 'Noto Sans SC', "
    "'WenQuanYi Micro Hei', 'Microsoft YaHei', 'sans-serif']\n"
    "plt.rcParams['axes.unicode_minus'] = False\n"
    "plt.rcParams['font.family'] = 'sans-serif'\n"
)


class E2BCodeInterpreter(BaseCodeInterpreter):
    """以 E2B 沙箱为执行环境的解释器。"""

    def __init__(
        self,
        task_id: str,
        work_dir: str,
        notebook_serializer: NotebookSerializer,
    ) -> None:
        super().__init__(task_id, work_dir, notebook_serializer)
        self.sbx: AsyncSandbox | None = None

    @classmethod
    async def create(
        cls,
        task_id: str,
        work_dir: str,
        notebook_serializer: NotebookSerializer,
    ) -> "E2BCodeInterpreter":
        return cls(task_id, work_dir, notebook_serializer)

    # ---- 生命周期 ----

    async def initialize(self, timeout: int = 3000) -> None:
        """开沙箱、装字体、上传数据文件。"""
        try:
            self.sbx = await AsyncSandbox.create(
                api_key=settings.E2B_API_KEY, timeout=timeout
            )
            logger.info("沙箱环境初始化成功")
            await self._pre_execute_code()
            await self._upload_all_files()
        except Exception as exc:
            logger.error(f"初始化沙箱环境失败: {exc}")
            raise

    async def cleanup(self) -> None:
        """收尾：尽量把产物拉回来，再杀沙箱。"""
        if not self.sbx:
            return
        try:
            if await self.sbx.is_running():
                try:
                    await self.download_all_files_from_sandbox()
                except Exception as exc:
                    logger.error(f"下载文件失败: {exc}")
                finally:
                    await self.sbx.kill()
                    logger.info("成功关闭沙箱环境")
            else:
                logger.warning("沙箱已经关闭，跳过清理步骤")
        except Exception as exc:
            # 清理失败不阻断主流程
            logger.error(f"清理沙箱环境失败: {exc}")

    async def _pre_execute_code(self) -> None:
        await self.execute_code(_FONT_BOOTSTRAP)

    async def _upload_all_files(self) -> None:
        """把任务目录里可上传的文件推到沙箱 home。"""
        assert self.sbx is not None
        if not os.path.isdir(self.work_dir):
            raise FileNotFoundError(f"工作目录不存在: {self.work_dir}")
        for name in os.listdir(self.work_dir):
            if not is_sandbox_upload_file(name):
                continue
            path = os.path.join(self.work_dir, name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                await self.sbx.files.write(f"{_SANDBOX_HOME}/{name}", fh.read())
            logger.info(f"成功上传文件到沙箱: {name}")

    # ---- 代码执行 ----

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        if not self.sbx:
            raise RuntimeError("沙箱环境未初始化")

        logger.info(f"执行代码: {code}")
        self.notebook_serializer.add_code_cell_to_notebook(code)

        await redis_manager.publish_message(
            self.task_id, SystemMessage(content="开始执行代码")
        )
        execution = await self.sbx.run_code(code)
        await redis_manager.publish_message(
            self.task_id, SystemMessage(content="代码执行完成")
        )

        display: list[OutputItem] = []
        failed = False
        error_detail = ""

        if execution.error:
            failed = True
            error_detail = self._truncate_text(
                f"Error: {execution.error.name}: {execution.error.value}\n"
                f"{execution.error.traceback}"
            )
            logger.error(f"执行错误: {error_detail}")
            display.append(
                ErrorModel(
                    name=execution.error.name,
                    value=execution.error.value,
                    traceback=execution.error.traceback,
                )
            )

        if execution.logs:
            if execution.logs.stdout:
                stdout_text = "\n".join(execution.logs.stdout)
                display.append(StdOutModel(msg=stdout_text))
                self.notebook_serializer.add_code_cell_output_to_notebook(
                    self._truncate_text(stdout_text)
                )
            if execution.logs.stderr:
                display.append(StdErrModel(msg="\n".join(execution.logs.stderr)))

        if execution.results:
            for result in execution.results:
                display.extend(self._render_result(result))

        model_text = self._summarize_for_model(display, error_detail)

        # 执行完立即把产物拉回本地，方便后续章节引用
        try:
            await self.download_all_files_from_sandbox()
        except Exception as exc:
            logger.error(f"文件同步失败: {exc}")

        await self._push_to_websocket(display)
        return model_text, failed, error_detail

    @staticmethod
    def _render_result(result: Any) -> list[OutputItem]:
        """把沙箱返回的多模态结果拆成前端可展示的条目。"""
        items: list[OutputItem] = []
        if str(result):
            items.append(ResultModel(res_type="result", format="text", msg=str(result)))
        reprs = (
            ("html", result._repr_html_()),
            ("markdown", result._repr_markdown_()),
            ("png", result._repr_png_()),
            ("jpeg", result._repr_jpeg_()),
            ("svg", result._repr_svg_()),
            ("pdf", result._repr_pdf_()),
            ("latex", result._repr_latex_()),
            ("javascript", result._repr_javascript_()),
        )
        for fmt, payload in reprs:
            if payload:
                items.append(ResultModel(res_type="result", format=fmt, msg=payload))
        if result._repr_json_():
            items.append(
                ResultModel(
                    res_type="result",
                    format="json",
                    msg=json.dumps(result._repr_json_()),
                )
            )
        return items

    def _summarize_for_model(
        self, display: list[OutputItem], error_detail: str
    ) -> str:
        """把展示条目压缩成回传给模型的文本视图。"""
        parts: list[str] = []
        if error_detail:
            parts.append(self.delete_color_control_char(error_detail))
        for item in display:
            if isinstance(item, StdOutModel | StdErrModel):
                parts.append(self._truncate_text(item.msg))
            elif isinstance(item, ErrorModel):
                parts.append(self._truncate_text(item.value or ""))
            elif isinstance(item, ResultModel):
                if item.format in _TEXTUAL_FORMATS:
                    parts.append(self._truncate_text(f"[{item.format}]\n{item.msg}"))
                elif item.format in _BINARY_FORMATS:
                    parts.append(f"[{item.format} 图片已生成，内容为 base64，未展示]")
        return "\n".join(parts)

    # ---- 产物 ----

    async def get_created_images(self, section: str) -> list[str]:
        """列出沙箱里新出现的图片文件。"""
        if not self.sbx:
            logger.warning("沙箱环境未初始化")
            return []
        try:
            names = {
                entry.name
                for entry in await self.sbx.files.list("./")
                if entry.path.endswith((".png", ".jpg"))
            }
        except Exception as exc:
            logger.error(f"获取创建的图片列表失败: {exc}")
            return []

        fresh = sorted(names - self.last_created_images)
        self.last_created_images = names
        self.add_section(section)
        self.section_output[section]["images"].extend(fresh)
        logger.info(f"{section}-获取创建的图片列表: {fresh}")
        return fresh

    async def download_all_files_from_sandbox(self) -> None:
        """把沙箱 home 下的文件全量同步回本地任务目录。"""
        assert self.sbx is not None
        os.makedirs(self.work_dir, exist_ok=True)
        try:
            entries = await self.sbx.files.list(_SANDBOX_HOME)
        except Exception as exc:
            logger.error(f"文件同步失败: {exc}")
            return

        for entry in entries:
            if entry.name in _SYNC_SKIP:
                continue
            try:
                content = await self.sbx.files.read(entry.path, format="bytes")
                Path(self.work_dir, entry.name).write_bytes(content)
                logger.info(f"同步文件: {entry.name}")
            except Exception as exc:
                logger.error(f"同步文件 {entry.name} 失败: {exc}")
