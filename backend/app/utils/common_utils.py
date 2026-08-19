"""后端共享的小工具：任务目录、模板加载、链接改写等。"""

import hashlib
import os
import re
import shutil
import tomllib
from datetime import datetime
from pathlib import Path

from app.config.setting import settings
from app.schemas.enums import CompTemplate
from app.utils.file_types import is_data_file
from app.utils.log_util import logger
from app.utils.paper_polish import render_paper_docx

# task_id 直接参与拼路径，必须排除分隔符与父目录引用
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FONTS_DIR = Path(__file__).resolve().parent.parent.parent / "fonts"
_FONT_SUFFIXES = {".ttf", ".otf", ".ttc"}
_IMAGE_LINK_RE = re.compile(r"!\[(.*?)\]\((.*?\.(?:png|jpg|jpeg|gif|bmp|webp))\)")
_FOOTNOTE_DEF_RE = re.compile(r"\[\^(\d+)\]:\s*(.+?)(?=\n\[\^|\n\n|\Z)", re.DOTALL)


def create_task_id() -> str:
    """生成 ``时间戳-短哈希`` 形式的任务 ID。"""
    now = datetime.now()
    digest = hashlib.md5(str(now).encode()).hexdigest()[:8]
    return f"{now:%Y%m%d-%H%M%S}-{digest}"


def ensure_safe_task_id(task_id: str) -> str:
    """校验任务 ID，拒绝路径遍历等非法取值。

    Raises:
        ValueError: ID 为空或含非法字符。
    """
    normalized = (task_id or "").strip()
    if not normalized or not _TASK_ID_RE.fullmatch(normalized):
        raise ValueError("非法 task_id")
    return normalized


def _task_dir(task_id: str) -> Path:
    return Path("project") / "work_dir" / task_id


def _install_fonts(work_dir: Path) -> None:
    """把内置中文字体放进任务目录，保证图表字体可用。"""
    if not _FONTS_DIR.is_dir():
        logger.warning(f"字体目录不存在: {_FONTS_DIR}")
        return
    for font in _FONTS_DIR.iterdir():
        if font.suffix.lower() not in _FONT_SUFFIXES:
            continue
        try:
            shutil.copy2(font, work_dir / font.name)
        except OSError as exc:
            logger.warning(f"复制字体 {font.name} 失败: {exc}")


def create_work_dir(task_id: str) -> str:
    """创建任务工作目录并预置字体，返回相对路径。"""
    work_dir = _task_dir(task_id)
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        _install_fonts(work_dir)
    except OSError as exc:
        logger.error(f"创建工作目录失败: {exc}")
        raise
    return str(work_dir)


def get_work_dir(task_id: str) -> str:
    """返回已存在的任务工作目录。

    Raises:
        FileNotFoundError: 目录不存在。
    """
    work_dir = _task_dir(task_id)
    if not work_dir.exists():
        logger.error(f"工作目录不存在: {work_dir}")
        raise FileNotFoundError(f"工作目录不存在: {work_dir}")
    return str(work_dir)


def get_config_template(comp_template: CompTemplate = CompTemplate.CHINA) -> dict:
    """读取竞赛模板对应的论文骨架配置。"""
    if comp_template == CompTemplate.CHINA:
        return load_toml(os.path.join("app", "config", "md_template.toml"))
    return {}


def load_toml(path: str) -> dict:
    """读取 TOML 文件为字典。"""
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_markdown(path: str) -> str:
    """读取 Markdown 文件全文。"""
    return Path(path).read_text(encoding="utf-8")


def get_current_files(folder_path: str, type: str = "all") -> list[str]:
    """按类别列出目录内的文件名。

    Args:
        folder_path: 目标目录。
        type: ``all`` / ``md`` / ``ipynb`` / ``data`` / ``image``。
    """
    entries = os.listdir(folder_path)
    match type:
        case "all":
            return entries
        case "md":
            return [f for f in entries if f.endswith(".md")]
        case "ipynb":
            return [f for f in entries if f.endswith(".ipynb")]
        case "data":
            return [f for f in entries if is_data_file(f)]
        case "image":
            return [f for f in entries if f.endswith((".png", ".jpg"))]
        case _:
            return []


def transform_link(task_id: str, content: str) -> str:
    """把 Markdown 里的相对图片路径改写为静态资源 URL。"""
    return _IMAGE_LINK_RE.sub(
        lambda m: f"![{m.group(1)}]({settings.SERVER_HOST}/static/{task_id}/{m.group(2)})",
        content,
    )


def md_2_docx(task_id: str) -> None:
    """把任务产出的 Markdown 论文导出为 DOCX。"""
    docx_path = render_paper_docx(task_id)
    logger.info(f"DOCX 导出完成: {docx_path}")


def split_footnotes(text: str) -> tuple[str, list[tuple[str, str]]]:
    """拆出正文与脚注定义，返回 ``(正文, [(编号, 内容)])``。"""
    main_text = re.sub(
        r"\n\[\^\d+\]:.*?(?=\n\[\^|\n\n|\Z)", "", text, flags=re.DOTALL
    ).strip()
    footnotes = _FOOTNOTE_DEF_RE.findall(text)
    logger.info(f"main_text:{main_text} \n footnotes:{footnotes}")
    return main_text, footnotes
