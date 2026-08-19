"""任务文件服务：下载链接、清单、CSV 预览与目录定位。"""

import csv
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config.setting import settings
from app.utils.common_utils import ensure_safe_task_id, get_current_files, get_work_dir

router = APIRouter()

_CSV_PREVIEW_MAX_BYTES = 5 * 1024 * 1024
_CSV_PREVIEW_MAX_ROWS = 50
_CSV_PREVIEW_MAX_COLUMNS = 30


def _resolve_task_file(task_id: str, filename: str) -> Path:
    """把 (task_id, filename) 解析为工作目录内的安全绝对路径。"""
    try:
        safe_id = ensure_safe_task_id(task_id)
        work_dir = Path(get_work_dir(safe_id)).resolve()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法任务ID") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="任务工作目录不存在") from exc

    target = (work_dir / filename).resolve()
    if not target.is_relative_to(work_dir):
        raise HTTPException(status_code=400, detail="文件路径越出工作目录")
    return target


@router.get("/download_url")
async def get_download_url(task_id: str, filename: str) -> dict:
    return {"download_url": f"{settings.SERVER_HOST}/static/{task_id}/{filename}"}


@router.get("/download_all_url")
async def get_download_all_url(task_id: str) -> dict:
    return {"download_url": f"{settings.SERVER_HOST}/static/{task_id}/all.zip"}


@router.get("/files")
async def get_files(task_id: str) -> list[dict]:
    work_dir = get_work_dir(task_id)
    return [
        {"filename": name, "file_type": name.split(".")[-1]}
        for name in get_current_files(work_dir, "all")
    ]


@router.get("/preview_csv")
async def preview_csv(task_id: str, filename: str, max_rows: int = 20) -> dict:
    """返回 CSV 的列名与前若干行，供前端直接渲染表格。"""
    target = _resolve_task_file(task_id, filename)

    if target.suffix.lower() != ".csv":
        raise HTTPException(status_code=400, detail="仅支持预览 CSV 文件")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.stat().st_size > _CSV_PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=413, detail="文件过大，请下载后查看")

    row_limit = max(1, min(max_rows, _CSV_PREVIEW_MAX_ROWS))
    try:
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])[:_CSV_PREVIEW_MAX_COLUMNS]
            rows: list[dict[str, str]] = []
            truncated = False
            for index, row in enumerate(reader):
                if index >= row_limit:
                    truncated = True
                    break
                rows.append({col: str(row.get(col) or "") for col in columns})
    except (OSError, csv.Error, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail=f"CSV 解析失败: {exc}") from exc

    return {
        "filename": filename,
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
    }


@router.get("/open_folder")
async def open_folder(task_id: str) -> dict:
    """在系统文件管理器中打开任务工作目录。"""
    work_dir = get_work_dir(task_id)
    if os.name == "nt":
        subprocess.run(["explorer", work_dir], check=False)
    elif os.name == "posix":
        subprocess.run(["open", work_dir], check=False)
    else:
        raise HTTPException(status_code=500, detail=f"不支持的操作系统: {os.name}")
    return {"message": "打开工作目录成功", "work_dir": work_dir}
