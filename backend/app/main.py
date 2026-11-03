"""Remit 后端入口：装配 FastAPI 应用、中间件与静态资源。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config.setting import settings
from app.routers import (
    common_router,
    files_router,
    modeling_router,
    ws_router,
    writing_router,
)
from app.routers import team_router, project_router
from app.services import paper_proposals, competitions
from app.services.task_bus import task_bus
from app.utils.cli import get_ascii_banner
from app.utils.log_util import logger

_PROJECT_ROOT = Path("project")
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时重建任务索引，退出时清理实时订阅。"""
    print(get_ascii_banner())
    logger.info("Starting Remit")

    interrupted = await task_bus.reconcile_interrupted_tasks()
    if interrupted:
        logger.warning(f"已将 {interrupted} 个重启前未结束的任务标记为停止")

    _PROJECT_ROOT.mkdir(exist_ok=True)
    team_router.recover_commands()
    try:
        yield
    finally:
        logger.info("Stopping Remit")
        await team_router.shutdown_team()
        await writing_router.shutdown_writers()
        await task_bus.close()


app = FastAPI(
    title="Remit",
    description="Local-first multi-agent workbench for mathematical modeling",
    version="2.0.1",
    lifespan=lifespan,
)

for module in (
    modeling_router,
    ws_router,
    writing_router,
    paper_proposals,
    competitions,
    team_router,
    project_router,
    common_router,
    files_router,
):
    app.include_router(module.router)

cors_origins = list(settings.CORS_ALLOW_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials="*" not in cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Disposition"],
)

# 任务产物（图表、论文、数据）按 task_id 挂载
(_PROJECT_ROOT / "work_dir").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory="project/work_dir"), name="static")


# 生产模式：后端直接托管前端构建产物，
# 深链接按 Vue Router history 模式回退 index.html
if _FRONTEND_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIST / "assets"),
        name="frontend-assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str) -> Response:
        """命中静态文件直接返回；页面路由回退到 index.html。"""
        candidate = (_FRONTEND_DIST / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(_FRONTEND_DIST):
            return FileResponse(candidate)
        if "." in Path(full_path).name:
            raise HTTPException(status_code=404)
        return FileResponse(_FRONTEND_DIST / "index.html")
