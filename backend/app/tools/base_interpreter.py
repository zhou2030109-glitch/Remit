"""代码执行后端的抽象接口。

本地 Jupyter、MATLAB、E2B 沙箱等实现统一遵守本契约：
执行代码、归集小节输出、上报 WebSocket、释放资源。
"""

import abc
import re

from app.schemas.response import InterpreterMessage, OutputItem
from app.services.redis_manager import redis_manager
from app.tools.notebook_serializer import NotebookSerializer
from app.utils.log_util import logger

_ANSI_RE = re.compile(r"(\x9B|\x1B\[)[0-?]*[ -/]*[@-~]")


class BaseCodeInterpreter(abc.ABC):
    """执行后端的公共骨架与小节输出管理。"""

    language: str = "python"
    backend_name: str = "Python"

    def __init__(
        self,
        task_id: str,
        work_dir: str,
        notebook_serializer: NotebookSerializer,
    ) -> None:
        self.task_id = task_id
        self.work_dir = work_dir
        self.notebook_serializer = notebook_serializer
        # 小节 -> {"content": [文本输出...], "images": [图路径...]}
        self.section_output: dict[str, dict[str, list[str]]] = {}
        self.current_section = ""
        self.last_created_images: set[str] = set()

    # ---- 子类必须实现的执行语义 ----

    @abc.abstractmethod
    async def initialize(self) -> None:
        """准备执行环境（启动内核 / 沙箱、同步文件等）。"""

    @abc.abstractmethod
    async def _pre_execute_code(self) -> None:
        """环境就绪后跑一段引导代码。"""

    @abc.abstractmethod
    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        """执行代码，返回 ``(输出文本, 是否出错, 错误详情)``。"""

    @abc.abstractmethod
    async def cleanup(self) -> None:
        """释放执行环境占用的资源。"""

    @abc.abstractmethod
    async def get_created_images(self, section: str) -> list[str]:
        """列出指定小节运行期间生成的图片。"""

    # ---- 公共能力 ----

    async def _push_to_websocket(self, content_to_display: list[OutputItem] | None) -> None:
        """把执行结果经 Redis 广播到前端。"""
        logger.info("执行结果已推送到WebSocket")
        message = InterpreterMessage(output=content_to_display)
        logger.debug(f"发送消息: {message.model_dump_json()}")
        await redis_manager.publish_message(self.task_id, message)

    def add_section(self, section_name: str) -> None:
        """切换并确保小节容器存在。"""
        self.current_section = section_name
        self.section_output.setdefault(section_name, {"content": [], "images": []})

    def add_content(self, section: str, text: str) -> None:
        """向小节追加一段文本输出。"""
        self.add_section(section)
        self.section_output[section]["content"].append(text)

    def get_code_output(self, section: str) -> str:
        """取回小节的全部文本输出。"""
        return "\n".join(self.section_output.get(section, {}).get("content", []))

    @staticmethod
    def delete_color_control_char(string: str) -> str:
        """剥离 ANSI 颜色控制符。"""
        return _ANSI_RE.sub("", string)

    @staticmethod
    def _truncate_text(text: str, max_length: int = 1000) -> str:
        """超长文本掐头去尾，保住上下文两端信息。"""
        if len(text) <= max_length:
            return text
        half = max_length // 2
        return f"{text[:half]}\n... (内容已截断) ...\n{text[-half:]}"
