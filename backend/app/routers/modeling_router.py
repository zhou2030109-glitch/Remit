"""建模任务路由模块，提供任务创建、API 验证和配置管理等接口。"""

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile
from app.core.workflow import (
    RemitWorkFlow,
    WorkflowApprovalRequired,
    WorkflowPausedForReview,
)
from app.core.agents.coder_agent import CoderAgentUnavailableError
from app.core.workflow_checkpoint import (
    WorkflowCheckpoint,
    WorkflowCheckpointError,
)
from app.schemas.enums import CompTemplate, FormatOutPut
from app.utils.log_util import logger
from app.services.redis_manager import redis_manager
from app.services import call_ledger
from app.services.async_io import run_blocking
from app.schemas.request import ExecutionBackend, Problem
from app.schemas.response import ApprovalMessage, SystemMessage, UserMessage
from app.utils.common_utils import (
    create_task_id,
    create_work_dir,
    get_work_dir,
    ensure_safe_task_id,
)
from app.core.llm.llm_factory import LLMFactory
from app.core.llm.errors import (
    TransientLLMError,
    ModelStageBudgetExceeded,
    http_status_code,
)
from app.core.problem_vision import (
    VisionResult,
    build_vision_supplement,
    describe_problem_figures,
)
from app.utils.pdf_figures import extract_problem_figures
from app.utils.pdf_parser import (
    MAX_PROBLEM_PDF_BYTES,
    PdfParseError,
    parse_problem_pdf_bytes,
)
import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict, Literal, Tuple
from fastapi import HTTPException
from app.schemas.request import ExampleRequest
from pydantic import BaseModel, Field
from app.config.setting import USER_CONFIG_PATH, settings, ApiType
from app.config.user_config import persist_user_config
from app.schemas.api_config import (
    AgentApiConfigStatus,
    ApiConfigStatusResponse,
    ProblemPdfParseResponse,
    SaveApiConfigRequest,
    ValidateApiKeyRequest,
    ValidateApiKeyResponse,
    ValidateOpenalexEmailRequest,
    ValidateOpenalexEmailResponse,
)
from app.services.api_probe import check_model_connection, check_openalex_identity
from app.services.task_intake import UploadLimitError, persist_uploads, seed_example
from app.routers.dependencies import http_task_id

router = APIRouter()

# 任务注册表: task_id -> (asyncio.Task, asyncio.Event)
_active_tasks: Dict[str, Tuple[asyncio.Task, asyncio.Event]] = {}
_scheduled_tasks: set[str] = set()
_pending_cancellations: set[str] = set()

# 失败自动续跑: task_id -> 已用次数；成功、审批或人工干预后清零
_auto_resume_counts: Dict[str, int] = {}
_AUTO_RESUME_LIMIT = settings.TASK_AUTO_RESUME_LIMIT
# 事件循环只持任务弱引用，必须自持强引用防止延迟续跑任务被回收
_auto_resume_handles: set[asyncio.Task] = set()

# UI 单次验证请求会等待 60 秒。后端必须更早结束，才能把供应商网络
# 超时转换成结构化错误，而不是让浏览器误报“验证服务不可达”。
API_VALIDATION_TIMEOUT_SECONDS = 45.0

_TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504, 520, 522, 524}


def _exception_message(error: BaseException) -> str:
    """TimeoutError 等异常字符串为空时仍给用户可定位的信息。"""
    detail = str(error).strip()
    return detail or type(error).__name__


def _is_transient_task_failure(error: BaseException) -> bool:
    """只有供应商/网络瞬断允许整任务自动续跑，计算失败不得原样重放。"""
    if isinstance(error, CoderAgentUnavailableError):
        return True
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, TransientLLMError):
            return True
        if http_status_code(current) in _TRANSIENT_STATUS_CODES:
            return True
        if type(current).__name__ in {
            "APIConnectionError",
            "ConnectError",
            "ConnectionError",
            "ReadError",
            "RemoteProtocolError",
        }:
            return True
        current = current.__cause__ or current.__context__
    return False


_runtime_configured_agents: set[str] = set()
_USER_CONFIG_PATH = USER_CONFIG_PATH


def _update_agent_config(prefix: str, config: dict) -> None:
    """用非空的 UI 字段覆盖环境配置，空字段保留后端默认值。"""
    api_identity_updated = False
    fields = {
        "apiKey": "API_KEY",
        "modelId": "MODEL",
        "baseUrl": "BASE_URL",
    }
    for request_key, setting_suffix in fields.items():
        value = config.get(request_key)
        if isinstance(value, str) and value.strip():
            setattr(settings, f"{prefix}_{setting_suffix}", value.strip())
            api_identity_updated = True

    api_type = config.get("apiType")
    if isinstance(api_type, str) and api_type.strip():
        setattr(settings, f"{prefix}_API_TYPE", ApiType(api_type.strip()))
        api_identity_updated = True

    context_window = config.get("contextWindow")
    if context_window not in (None, ""):
        setattr(settings, f"{prefix}_CONTEXT_WINDOW", int(context_window))
    max_tokens = config.get("maxTokens")
    if max_tokens not in (None, ""):
        if int(max_tokens) < 1:
            raise ValueError("模型输出上限必须为正整数")
        setattr(settings, f"{prefix}_MAX_TOKENS", int(max_tokens))

    if api_identity_updated:
        _runtime_configured_agents.add(prefix)


_NO_TEXT_DETAIL = "未检测到可提取文字，且识图也没能读出内容，请确认这是赛题 PDF"


async def _run_problem_vision(content: bytes) -> tuple[VisionResult, str]:
    """对赛题 PDF 识图；任何失败都降级为空补充文本。

    Returns:
        (识图结果, 追加到题面末尾的 Markdown)。
    """
    if not settings.PDF_VISION_ENABLED:
        return VisionResult(status="disabled", insights=[], figure_count=0), ""

    figures = await asyncio.to_thread(
        extract_problem_figures,
        content,
        max_figures=settings.PDF_VISION_MAX_FIGURES,
    )
    if not figures:
        return VisionResult(status="skipped", insights=[], figure_count=0), ""

    try:
        vision_llm = LLMFactory(task_id="").get_vision_llm()
        result = await describe_problem_figures(figures, vision_llm)
    except Exception as exc:
        logger.warning(f"赛题识图失败，降级为纯文本导入: {exc}")
        return (
            VisionResult(
                status="failed",
                insights=[],
                figure_count=len(figures),
                error=str(exc)[:300],
            ),
            "",
        )
    return result, build_vision_supplement(result)


@router.post("/parse-problem-document", response_model=ProblemPdfParseResponse)
@router.post("/parse-problem-pdf", response_model=ProblemPdfParseResponse)
async def parse_problem_pdf(file: UploadFile = File(...)) -> ProblemPdfParseResponse:
    """解析赛题 PDF：提取结构化文本，并用多模态模型补全图像信息。"""
    filename = (file.filename or "").strip()
    if Path(filename).suffix.lower() not in {".pdf", ".docx", ".doc"}:
        raise HTTPException(
            status_code=400, detail="请上传 PDF、DOCX 或 DOC 格式的赛题文件"
        )

    content = await file.read(MAX_PROBLEM_PDF_BYTES + 1)
    if len(content) > MAX_PROBLEM_PDF_BYTES:
        raise HTTPException(status_code=413, detail="赛题文件不能超过 25MB")

    if Path(filename).suffix.lower() in {".docx", ".doc"}:
        from app.utils.document_parser import parse_word_bytes

        try:
            parsed = await asyncio.to_thread(
                parse_word_bytes, content, Path(filename).suffix.lower()
            )
        except PdfParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ProblemPdfParseResponse(
            filename=filename,
            text=parsed.text,
            page_count=0,
            char_count=parsed.char_count,
            figure_count=0,
            vision_status="skipped",
            vision_error=(
                "Word 中的插图尚未识别，请核对原文；可另存为 PDF 进行识图。"
                if "[导入提示：Word 包含插图" in parsed.text
                else ""
            ),
            figures=[],
        )

    # 扫描版 PDF 提不出文字，但识图能整页转录，因此先放行再判断
    try:
        parsed = parse_problem_pdf_bytes(content, require_text=False)
    except PdfParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    vision, supplement = await _run_problem_vision(content)
    text = "\n\n".join(part for part in (parsed.text, supplement) if part).strip()
    if not text:
        raise HTTPException(status_code=422, detail=_NO_TEXT_DETAIL)

    return ProblemPdfParseResponse(
        filename=filename,
        text=text,
        page_count=parsed.page_count,
        char_count=len(text),
        figure_count=len(vision.informative_insights),
        vision_status=vision.status,  # type: ignore[arg-type]
        vision_error=vision.error,
        figures=[insight.to_dict() for insight in vision.informative_insights],
    )


def _agent_is_configured(prefix: str) -> bool:
    return bool(
        getattr(settings, f"{prefix}_API_KEY")
        and getattr(settings, f"{prefix}_MODEL")
        and getattr(settings, f"{prefix}_API_TYPE")
    )


def _agent_config_status(prefix: str) -> AgentApiConfigStatus:
    if prefix == "FALLBACK":
        from app.services.model_capabilities import fallback_config

        config = fallback_config("coordinator")
        return AgentApiConfigStatus(
            configured=bool(
                config["api_key"] and config["api_type"] and config["model_id"]
            ),
            api_key_configured=bool(config["api_key"]),
            api_type=config["api_type"],
            model_id=config["model_id"],
            base_url=config["base_url"],
            context_window=config["context_window"],
            max_tokens=config["max_tokens"],
            source="runtime"
            if prefix in _runtime_configured_agents
            else "environment"
            if config["model_id"]
            else "missing",
        )
    api_key_configured = bool(getattr(settings, f"{prefix}_API_KEY"))
    api_type = getattr(settings, f"{prefix}_API_TYPE")
    model_id = getattr(settings, f"{prefix}_MODEL")
    base_url = getattr(settings, f"{prefix}_BASE_URL")
    configured = _agent_is_configured(prefix)
    return AgentApiConfigStatus(
        configured=configured,
        api_key_configured=api_key_configured,
        api_type=api_type.value if isinstance(api_type, ApiType) else api_type,
        model_id=model_id,
        base_url=base_url,
        context_window=int(getattr(settings, f"{prefix}_CONTEXT_WINDOW")),
        max_tokens=getattr(settings, f"{prefix}_MAX_TOKENS"),
        source=(
            "runtime"
            if prefix in _runtime_configured_agents
            else "environment"
            if configured
            else "missing"
        ),
    )


@router.get("/api-config-status", response_model=ApiConfigStatusResponse)
async def get_api_config_status():
    """Return safe metadata for the effective configuration, never API keys."""
    agents = {
        "coordinator": _agent_config_status("COORDINATOR"),
        "modeler": _agent_config_status("MODELER"),
        "coder": _agent_config_status("CODER"),
        "writer": _agent_config_status("WRITER"),
        "model_scout": _agent_config_status("MODEL_SCOUT"),
        "model_critic": _agent_config_status("MODEL_CRITIC"),
        "fallback": _agent_config_status("FALLBACK"),
    }
    required_keys = ["coordinator", "modeler", "coder"]
    if settings.MODEL_COUNCIL_ENABLED:
        required_keys.extend(["model_scout", "model_critic"])
    return ApiConfigStatusResponse(
        configured=all(agents[key].configured for key in required_keys),
        model_council_enabled=settings.MODEL_COUNCIL_ENABLED,
        shared_core=settings.MODEL_SHARED_CORE,
        fallback_enabled=bool(settings.FALLBACK_ENABLED and settings.FALLBACK_MODEL),
        agents=agents,
    )


@router.post("/save-api-config")
async def store_api_configuration(request: SaveApiConfigRequest):
    """Persist the validated role settings used by subsequent workflows."""
    previous = settings.model_dump()
    previous_roles = set(_runtime_configured_agents)
    try:
        for prefix, payload in request.role_payloads():
            _update_agent_config(prefix, payload)
        if request.shared_core:
            for prefix in ("MODELER", "CODER", "WRITER"):
                for suffix in (
                    "API_KEY",
                    "API_TYPE",
                    "MODEL",
                    "BASE_URL",
                    "CONTEXT_WINDOW",
                    "MAX_TOKENS",
                    "REASONING_EFFORT",
                ):
                    setattr(
                        settings,
                        f"{prefix}_{suffix}",
                        getattr(settings, f"COORDINATOR_{suffix}"),
                    )
                _runtime_configured_agents.add(prefix)
        settings.MODEL_SHARED_CORE = request.shared_core
        if request.fallback_enabled is not None:
            settings.FALLBACK_ENABLED = request.fallback_enabled
        if request.model_council_enabled is not None:
            settings.MODEL_COUNCIL_ENABLED = request.model_council_enabled

        if request.openalex_email:
            settings.OPENALEX_EMAIL = request.openalex_email

        persist_user_config(settings, _USER_CONFIG_PATH)

        return {"success": True, "message": "配置已保存，重启后仍然生效"}
    except Exception as e:
        for key, value in previous.items():
            setattr(settings, key, value)
        _runtime_configured_agents.clear()
        _runtime_configured_agents.update(previous_roles)
        logger.error("保存配置失败（{}），保留原配置", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail="配置未保存，请检查参数与配置目录写入权限；原配置已保留。",
        ) from e


@router.post("/validate-api-key", response_model=ValidateApiKeyResponse)
async def validate_api_key(request: ValidateApiKeyRequest):
    valid, message = await check_model_connection(
        api_type=request.api_type,
        api_key=request.api_key,
        model_id=request.model_id,
        base_url=request.base_url,
        timeout=API_VALIDATION_TIMEOUT_SECONDS,
    )
    return ValidateApiKeyResponse(valid=valid, message=message)


@router.post("/validate-openalex-email", response_model=ValidateOpenalexEmailResponse)
async def validate_openalex_email(request: ValidateOpenalexEmailRequest):
    valid, message = check_openalex_identity(request.email)
    return ValidateOpenalexEmailResponse(valid=valid, message=message)


async def _schedule_new_task(
    background_tasks: BackgroundTasks,
    *,
    task_id: str,
    problem_text: str,
    template: CompTemplate,
    output_format: FormatOutPut,
    user_requirements: str = "",
    execution_backend: ExecutionBackend | None = None,
) -> dict[str, str]:
    """Publish the initial event and hand execution to FastAPI's task runner."""
    # 继续接受旧客户端的 Markdown 枚举值，但最终交付契约固定为 PDF + LaTeX。
    output_format = FormatOutPut.LaTeX
    try:
        await redis_manager.set(f"task_id:{task_id}", task_id)
        visible_prompt = problem_text.strip()
        if user_requirements.strip():
            visible_prompt = (
                f"{visible_prompt}\n\n【额外交付要求】\n{user_requirements.strip()}"
            )
        await redis_manager.publish_message(
            task_id, UserMessage(content=visible_prompt)
        )
        from app.services.work_timing import Span
        from app.services.async_io import run_blocking

        queue_span = Span(task_id, "queue")
        await run_blocking(queue_span.start)
    except BaseException:
        _scheduled_tasks.discard(task_id)
        _pending_cancellations.discard(task_id)
        try:
            await redis_manager.delete_task_record(task_id)
        except Exception as cleanup_error:
            logger.warning(f"清理未入队任务档案失败: {cleanup_error}")
        await _discard_failed_intake(
            Path("project/work_dir") / ensure_safe_task_id(task_id)
        )
        raise
    _scheduled_tasks.add(task_id)
    background_tasks.add_task(
        run_modeling_task_async,
        task_id,
        problem_text,
        template,
        output_format,
        user_requirements,
        execution_backend=execution_backend,
        queue_span=queue_span,
    )
    logger.info(f"任务 {task_id} 已进入后台执行队列")
    return {"task_id": task_id, "status": "processing"}


async def _discard_failed_intake(workspace: Path) -> None:
    """只清理当前新请求创建的任务目录，拒绝删除其他路径。"""
    root = Path("project/work_dir").resolve()
    target = workspace.resolve()
    if target.parent == root and target.is_dir():
        await asyncio.to_thread(shutil.rmtree, target)


@router.post("/example")
async def submit_bundled_example(
    example_request: ExampleRequest,
    background_tasks: BackgroundTasks,
):
    task_id = create_task_id()
    workspace = Path(create_work_dir(task_id))
    try:
        ques_all = seed_example(example_request.example_id, workspace)
    except ValueError as error:
        await _discard_failed_intake(workspace)
        raise HTTPException(status_code=404, detail=str(error)) from error
    return await _schedule_new_task(
        background_tasks,
        task_id=task_id,
        problem_text=ques_all,
        template=CompTemplate.CHINA,
        output_format=FormatOutPut.LaTeX,
    )


@router.post("/modeling")
async def submit_modeling(
    background_tasks: BackgroundTasks,
    ques_all: str = Form(...),  # 从表单获取
    user_requirements: str = Form(""),  # 用户额外交付要求，独立于题目原文
    comp_template: CompTemplate = Form(...),  # 从表单获取
    format_output: FormatOutPut = Form(...),  # 从表单获取
    execution_backend: ExecutionBackend | None = Form(None),
    files: list[UploadFile] = File(default=None),
):
    task_id = create_task_id()
    workspace = Path(create_work_dir(task_id))

    if files:
        logger.info(f"开始处理上传的文件，工作目录: {workspace}")
        try:
            saved = await persist_uploads(files, workspace)
            logger.info(f"已保存 {len(saved)} 个任务附件: {saved}")
        except (OSError, ValueError) as error:
            await _discard_failed_intake(workspace)
            logger.error(f"保存任务附件失败: {error}")
            raise HTTPException(
                status_code=413 if isinstance(error, UploadLimitError) else 400,
                detail=str(error),
            ) from error
        except asyncio.CancelledError:
            await _discard_failed_intake(workspace)
            raise
    else:
        logger.warning("没有上传文件")

    return await _schedule_new_task(
        background_tasks,
        task_id=task_id,
        problem_text=ques_all,
        template=comp_template,
        output_format=format_output,
        user_requirements=user_requirements,
        execution_backend=execution_backend,
    )


async def run_modeling_task_async(
    task_id: str,
    ques_all: str,
    comp_template: CompTemplate,
    format_output: FormatOutPut,
    user_requirements: str = "",
    resume_from: str | None = None,
    continue_existing: bool = False,
    execution_backend: ExecutionBackend | None = None,
    queue_span=None,
):
    """Execute a fresh or resumed workflow outside the request lifecycle."""
    logger.info(f"开始后台建模任务: {task_id}")

    problem = Problem(
        task_id=task_id,
        ques_all=ques_all,
        user_requirements=user_requirements,
        comp_template=comp_template,
        format_output=format_output,
        execution_backend=execution_backend,
    )

    # 创建取消信号
    cancel_event = asyncio.Event()

    # 在第一次 await 前注册真实 asyncio.Task，消除“刚提交就无法停止”的竞态。
    workflow = RemitWorkFlow()
    workflow.cancel_event = cancel_event
    workflow.task_id = task_id
    workflow.work_dir = create_work_dir(task_id)
    workflow.checkpoint = WorkflowCheckpoint(workflow.work_dir)
    if not resume_from and not workflow.checkpoint.path.is_file():
        workflow.checkpoint.initialize(problem)
    task = asyncio.create_task(
        workflow.execute(
            problem,
            resume_from=resume_from,
            continue_existing=continue_existing,
        )
    )
    _active_tasks[task_id] = (task, cancel_event)
    _scheduled_tasks.discard(task_id)

    cancelled = False
    retry_delay = None
    try:
        if (
            task_id in _pending_cancellations
            or await redis_manager.is_cancellation_requested(task_id)
        ):
            cancel_event.set()
            task.cancel()

        if queue_span is not None:
            from app.services.async_io import run_blocking

            await run_blocking(
                queue_span.finish, "cancelled" if cancel_event.is_set() else "completed"
            )

        # 发送任务开始状态
        await redis_manager.publish_message(
            task_id,
            SystemMessage(
                content=(
                    f"任务从节点 {resume_from} 继续处理"
                    if resume_from
                    else (
                        "任务继续处理（人工审核已完成）"
                        if continue_existing
                        else "任务开始处理"
                    )
                ),
                task_status="running",
            ),
        )
        await asyncio.wait_for(task, timeout=settings.TASK_TIMEOUT_SECONDS)
        workflow.mark_status("completed")
        _auto_resume_counts.pop(task_id, None)

        # 发送任务完成状态
        await redis_manager.publish_message(
            task_id,
            SystemMessage(
                content="任务处理完成", type="success", task_status="completed"
            ),
        )
    except WorkflowApprovalRequired as pause:
        workflow.mark_status("awaiting_approval")
        _auto_resume_counts.pop(task_id, None)
        approval = pause.approval
        quality_status = str(dict(approval.get("quality_report", {})).get("status", ""))
        if approval.get("allow_incomplete"):
            approval_content = (
                f"“{approval['node_label']}”尚未完成，已暂停，等待你决定如何修复"
            )
        elif quality_status in {"warning", "failed", "manual_review"}:
            approval_content = f"“{approval['node_label']}”的结果仍需核验，等待你的审核"
        else:
            approval_content = (
                f"“{approval['node_label']}”已生成可核对产物，等待你的审核"
            )
        await redis_manager.publish_message(
            task_id,
            ApprovalMessage(
                content=approval_content,
                checkpoint_id=str(approval["checkpoint_id"]),
                node_id=str(approval["node_id"]),
                node_label=str(approval["node_label"]),
                summary=str(approval["summary"]),
                artifacts=[str(item) for item in approval.get("artifacts", [])],
                quality_report=dict(approval.get("quality_report", {})),
                revision_count=int(approval.get("revision_count", 0)),
                revision_targets=list(approval.get("revision_targets", [])),
                explain=dict(approval.get("explain", {}) or {}),
            ),
        )
    except WorkflowPausedForReview as pause:
        # 质量门未通过 + HIL 已关闭：把任务挂回 awaiting_approval 让用户接管。
        # pending_approval 已经在调用方写入，状态由 checkpoint.request_approval
        # 持久化为 awaiting_approval；这里只补一条系统提示便于前端区分。
        workflow.mark_status("awaiting_approval")
        _auto_resume_counts.pop(task_id, None)
        approval = pause.approval
        quality_status = str(dict(approval.get("quality_report", {})).get("status", ""))
        if quality_status == "manual_review":
            hint = "该节点结果需要人工裁决（HIL 已关闭，已自动挂起）。"
        else:
            hint = "该节点质量门未通过（HIL 已关闭，已自动挂起）。"
        await redis_manager.publish_message(
            task_id,
            SystemMessage(
                content=(
                    f"任务挂起等待人工裁决：{approval.get('node_label', approval.get('node_id', ''))}。"
                    f"{hint}请在项目页批准、放行或退回以继续。"
                ),
                type="warning",
                task_status="awaiting_approval",
            ),
        )
    except asyncio.CancelledError:
        logger.info(f"任务 {task_id} 被取消")
        cancelled = True
        workflow.mark_status("stopping")
        _auto_resume_counts.pop(task_id, None)
        stopped = SystemMessage(
            content="正在停止，等待计算与资源释放",
            type="warning",
            task_status="stopping",
        )
        await redis_manager.publish_message(task_id, stopped)
    except Exception as e:
        error_message = _exception_message(e)
        from app.core.llm.errors import ModelStageBudgetExceeded

        budget_pause = isinstance(
            e, (ModelStageBudgetExceeded, call_ledger.ExecutionBudgetExceeded)
        )
        task_status = "stopped" if budget_pause else "failed"
        if budget_pause:
            logger.warning(f"任务 {task_id} 额度暂停: {error_message}")
        else:
            logger.exception(f"任务 {task_id} 执行失败: {error_message}")
        workflow.mark_status(task_status)
        attempt = _auto_resume_counts.get(task_id, 0) + 1
        transient = _is_transient_task_failure(e)
        will_retry = transient and attempt <= _AUTO_RESUME_LIMIT
        from app.services.task_failure import describe
        from app.services.writing_workspace import write_json

        try:
            failure = describe(
                e, Path(workflow.work_dir), retrying=will_retry, attempts=attempt - 1
            )
            write_json(Path(workflow.work_dir) / ".runtime-failure.json", failure)
        except (OSError, ValueError):
            logger.warning("错误摘要暂时无法保存；原有检查点保持不变")
        delay_seconds = settings.TASK_AUTO_RESUME_BASE_DELAY_SECONDS * attempt
        await redis_manager.publish_message(
            task_id,
            SystemMessage(
                content=(
                    f"{'任务额度暂停' if budget_pause else '任务执行失败'}: {error_message}"
                    + (
                        f"；将在 {delay_seconds} 秒后从检查点自动续跑"
                        f"（第 {attempt}/{_AUTO_RESUME_LIMIT} 次）"
                        if will_retry
                        else (
                            "；请查看额度与已保存成果后继续"
                            if budget_pause
                            else "；已停止自动重试，请查看错误原因后续跑"
                            if not transient
                            else "；自动续跑次数已用完，请人工在页面上续跑或退回"
                        )
                    )
                ),
                type="warning" if budget_pause else "error",
                task_status=task_status,
            ),
        )
        if will_retry:
            _auto_resume_counts[task_id] = attempt
            retry_delay = delay_seconds
    finally:
        try:
            if not task.done():
                cancel_event.set()
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            try:
                await workflow.cleanup()
            except Exception as cleanup_error:
                retry_delay = None
                logger.exception(
                    f"清理任务 {task_id} 的代码解释器失败: "
                    f"{_exception_message(cleanup_error)}"
                )
            else:
                if cancelled or task_id in _pending_cancellations:
                    retry_delay = None
                    _auto_resume_counts.pop(task_id, None)
                    workflow.mark_status("stopped")
                    await redis_manager.publish_message(
                        task_id,
                        SystemMessage(
                            content="建模任务已停止",
                            type="warning",
                            task_status="stopped",
                        ),
                    )
        finally:
            _active_tasks.pop(task_id, None)
            _scheduled_tasks.discard(task_id)
            _pending_cancellations.discard(task_id)
            try:
                await redis_manager.clear_cancellation_request(task_id)
            except Exception as cleanup_error:
                logger.warning(f"清理任务取消标记失败: {cleanup_error}")
        if retry_delay is not None:
            resume_handle = asyncio.get_running_loop().create_task(
                _auto_resume_after_failure(
                    task_id,
                    ques_all,
                    comp_template,
                    format_output,
                    user_requirements,
                    retry_delay,
                    execution_backend,
                )
            )
            _auto_resume_handles.add(resume_handle)
            resume_handle.add_done_callback(_auto_resume_handles.discard)


async def _auto_resume_after_failure(
    task_id: str,
    ques_all: str,
    comp_template: CompTemplate,
    format_output: FormatOutPut,
    user_requirements: str,
    delay_seconds: int,
    execution_backend: ExecutionBackend | None = None,
) -> None:
    """失败后延迟自动续跑；用户已手动干预或状态变化时放弃。"""
    await asyncio.sleep(delay_seconds)
    if task_id in _active_tasks or task_id in _scheduled_tasks:
        return
    # 先占位再做任何 await，避免与手动 resume 的竞态双跑同一任务
    _scheduled_tasks.add(task_id)
    try:
        checkpoint = WorkflowCheckpoint(get_work_dir(task_id))
        if not checkpoint.path.is_file():
            _scheduled_tasks.discard(task_id)
            return
        state = checkpoint.load()
    except Exception as exc:
        logger.error(f"任务 {task_id} 自动续跑前读取检查点失败: {exc}")
        _scheduled_tasks.discard(task_id)
        return
    if state.get("status") != "failed":
        _scheduled_tasks.discard(task_id)
        return
    try:
        if (
            task_id in _pending_cancellations
            or await redis_manager.is_cancellation_requested(task_id)
        ):
            _scheduled_tasks.discard(task_id)
            return
        await redis_manager.publish_message(
            task_id,
            SystemMessage(
                content="自动续跑开始：从检查点恢复未完成节点", task_status="running"
            ),
        )
        await run_modeling_task_async(
            task_id,
            ques_all,
            comp_template,
            format_output,
            user_requirements,
            continue_existing=True,
            execution_backend=execution_backend,
        )
    except Exception as exc:
        logger.error(f"任务 {task_id} 自动续跑调度失败: {exc}")
    finally:
        _scheduled_tasks.discard(task_id)
        _pending_cancellations.discard(task_id)


class CancelTaskResponse(BaseModel):
    success: bool
    message: str


class ResumeNodeResponse(BaseModel):
    """一个具备完整前置成果的可续跑节点。"""

    node_id: str
    label: str
    status: str


class ResumeOptionsResponse(BaseModel):
    """任务续跑能力和节点列表。"""

    task_id: str
    status: str
    resumable: bool
    current_node: str | None = None
    nodes: list[ResumeNodeResponse]


class ExecutionBudgetExtension(BaseModel):
    confirmed: Literal[True]
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    stage_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_used: int = Field(ge=0, strict=True)
    expected_limit: int = Field(ge=1, strict=True)
    additional: int = Field(ge=1, le=48, strict=True)


class ModelBudgetExtension(BaseModel):
    confirmed: Literal[True]
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    stage_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_used: int = Field(ge=0, strict=True)
    expected_limit: int = Field(ge=1, strict=True)
    expected_seconds: float = Field(gt=0, allow_inf_nan=False)
    additional_calls: int = Field(ge=1, le=24, strict=True)
    additional_seconds: int = Field(ge=60, le=1800, strict=True)


class ResumeTaskRequest(BaseModel):
    """从指定节点继续任务。"""

    node_id: str
    execution_budget_extension: ExecutionBudgetExtension | None = None
    model_budget_extension: ModelBudgetExtension | None = None


class ResumeTaskResponse(BaseModel):
    """续跑任务调度结果。"""

    success: bool
    task_id: str
    node_id: str
    message: str


class ApprovalDetail(BaseModel):
    """当前节点待人工验收的持久化内容。"""

    checkpoint_id: str
    node_id: str
    node_label: str
    summary: str
    artifacts: list[str]
    quality_report: dict[str, Any]
    revision_count: int
    revision_targets: list[dict[str, str]]
    explain: dict[str, Any] = Field(default_factory=dict)
    requested_at: str


class ApprovalStatusResponse(BaseModel):
    task_id: str
    status: str
    pending: ApprovalDetail | None = None


class SubmitApprovalRequest(BaseModel):
    checkpoint_id: str
    decision: Literal["approve", "revise"]
    feedback: str = ""
    target_node_id: str | None = None


class SubmitApprovalResponse(BaseModel):
    success: bool
    task_id: str
    decision: Literal["approve", "revise"]
    node_id: str
    message: str


def _load_workflow_checkpoint(task_id: str) -> tuple[WorkflowCheckpoint, dict]:
    """安全加载指定任务的工作流检查点。"""
    try:
        safe_task_id = ensure_safe_task_id(task_id)
        work_dir = get_work_dir(safe_task_id)
        checkpoint = WorkflowCheckpoint(work_dir)
        return checkpoint, checkpoint.load()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法任务ID") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="任务工作目录不存在") from exc
    except WorkflowCheckpointError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/modeling/{task_id}/approval",
    response_model=ApprovalStatusResponse,
)
async def get_pending_approval(task_id: str):
    """返回当前待审核节点；页面刷新和应用重启后仍可恢复。"""
    checkpoint, state = _load_workflow_checkpoint(task_id)
    pending = checkpoint.pending_approval(state)
    return ApprovalStatusResponse(
        task_id=task_id,
        status=str(state.get("status", "unknown")),
        pending=ApprovalDetail(**pending) if pending else None,
    )


@router.post(
    "/modeling/{task_id}/approval",
    response_model=SubmitApprovalResponse,
)
async def submit_approval(
    task_id: str,
    request: SubmitApprovalRequest,
    background_tasks: BackgroundTasks,
):
    """批准当前成果或带具体意见退回重做；两种决定都会持久化。"""
    if task_id in _active_tasks or task_id in _scheduled_tasks:
        raise HTTPException(status_code=409, detail="任务状态正在变化，请稍后重试")
    checkpoint, state = _load_workflow_checkpoint(task_id)
    pending = checkpoint.pending_approval(state)
    if not pending:
        raise HTTPException(status_code=409, detail="当前没有等待处理的人工审核")
    if request.decision == "revise" and not request.feedback.strip():
        raise HTTPException(status_code=422, detail="退回修改时必须填写具体修改意见")

    node_id = str(pending["node_id"])
    try:
        if request.decision == "approve":
            checkpoint.approve(state, request.checkpoint_id)
            decision_message = f"你已批准“{pending['node_label']}”，即将进入下一步"
        else:
            checkpoint.request_revision(
                state,
                request.checkpoint_id,
                request.feedback,
                request.target_node_id,
            )
            decision_message = (
                f"你已退回“{pending['node_label']}”，Agent 将按意见重做本步"
            )
    except WorkflowCheckpointError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        problem = Problem.model_validate(checkpoint.load()["problem"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail="任务原始配置损坏，无法继续"
        ) from exc

    await redis_manager.clear_cancellation_request(task_id)
    await redis_manager.publish_message(
        task_id,
        SystemMessage(
            content=decision_message,
            type="success" if request.decision == "approve" else "warning",
        ),
    )
    _auto_resume_counts.pop(task_id, None)
    _scheduled_tasks.add(task_id)
    background_tasks.add_task(
        run_modeling_task_async,
        task_id,
        problem.ques_all,
        problem.comp_template,
        problem.format_output,
        problem.user_requirements,
        None,
        True,
        execution_backend=problem.execution_backend,
    )
    return SubmitApprovalResponse(
        success=True,
        task_id=task_id,
        decision=request.decision,
        node_id=node_id,
        message=decision_message,
    )


@router.get(
    "/modeling/{task_id}/resume-options",
    response_model=ResumeOptionsResponse,
)
async def get_resume_options(task_id: str):
    """返回停止任务当前可安全选择的续跑节点。"""
    checkpoint, state = _load_workflow_checkpoint(task_id)
    messages = await redis_manager.load_task_messages(task_id)
    durable_status = (
        redis_manager.task_status_from_messages(messages) if messages else "unknown"
    )
    status = str(state.get("status", durable_status))
    if durable_status == "stopped" and status in {"running", "stopping"}:
        checkpoint.mark_status("stopped")
        state = checkpoint.load()
        status = "stopped"
    nodes = [ResumeNodeResponse(**item) for item in checkpoint.resume_nodes(state)]
    resumable = (
        status in {"stopped", "failed"}
        and task_id not in _active_tasks
        and task_id not in _scheduled_tasks
        and bool(nodes)
    )
    return ResumeOptionsResponse(
        task_id=task_id,
        status=status,
        resumable=resumable,
        current_node=state.get("current_node"),
        nodes=nodes,
    )


@router.get("/modeling/{task_id}/execution-budget")
async def get_execution_budget(task_id: str):
    checkpoint, state = _load_workflow_checkpoint(task_id)
    if state.get("status") not in {"stopped", "failed"}:
        raise HTTPException(409, "任务状态已变化，请刷新后查看")
    node = state.get("current_node")
    if not node:
        raise HTTPException(409, "没有可追加额度的当前阶段")
    try:
        budget = await run_blocking(call_ledger.execution_budget, task_id, node)
        if budget is None:
            raise HTTPException(409, "任务执行额度不可用")
        return {
            **await run_blocking(budget.snapshot),
            "node_id": node,
            "label": checkpoint.node_label(node, state),
        }
    except call_ledger.ExecutionBudgetUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/modeling/{task_id}/model-budget")
async def get_model_budget(task_id: str):
    from app.services.model_budget import for_task, resume_phase

    checkpoint, state = _load_workflow_checkpoint(task_id)
    if state.get("status") not in {"stopped", "failed"}:
        raise HTTPException(409, "任务状态已变化，请刷新后查看")
    budget = await run_blocking(for_task, task_id)
    if budget is None or not state.get("current_node"):
        raise HTTPException(409, "没有可恢复的模型阶段")
    phase = resume_phase(budget.root, state)
    return {
        **await run_blocking(budget.snapshot, phase),
        "node_id": state["current_node"],
        "label": checkpoint.node_label(state["current_node"], state),
    }


@router.post(
    "/modeling/{task_id}/resume",
    response_model=ResumeTaskResponse,
)
async def resume_task(
    task_id: str,
    request: ResumeTaskRequest,
    background_tasks: BackgroundTasks,
):
    """从用户选择的检查点重新执行，并复用它之前的已验收成果。"""
    if task_id in _active_tasks or task_id in _scheduled_tasks:
        raise HTTPException(status_code=409, detail="任务已在运行或等待启动")
    checkpoint, state = _load_workflow_checkpoint(task_id)
    if state.get("status") not in {"stopped", "failed"}:
        raise HTTPException(status_code=409, detail="只有已停止或失败任务可以续跑")
    available = {item["node_id"] for item in checkpoint.resume_nodes(state)}
    if request.node_id not in available:
        raise HTTPException(
            status_code=422,
            detail="所选节点缺少完整前置成果，不能从这里续跑",
        )
    try:
        problem = Problem.model_validate(state["problem"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail="任务原始配置损坏，无法续跑"
        ) from exc

    # Reserve scheduling before the first await; double clicks cannot queue two
    # workers while Redis or the durable budget transaction is pending.
    _scheduled_tasks.add(task_id)
    try:
        extension = request.execution_budget_extension
        model_extension = request.model_budget_extension
        if extension and model_extension:
            raise HTTPException(422, "请分别确认执行额度或模型额度")
        same_stage = request.node_id == state.get(
            "current_node"
        ) and request.node_id not in state.get("completed_nodes", [])
        if (extension or model_extension) and not same_stage:
            raise HTTPException(409, "只能为当前未完成阶段追加执行次数")
        if same_stage:
            from app.services.model_budget import for_task, resume_phase

            model_budget = await run_blocking(for_task, task_id)
            phase = resume_phase(model_budget.root, state) if model_budget else "work"
            if model_extension:
                if model_budget is None:
                    raise HTTPException(409, "模型额度不可用")
                await run_blocking(
                    model_budget.extend,
                    model_extension.request_id,
                    model_extension.stage_key,
                    model_extension.expected_used,
                    model_extension.expected_limit,
                    model_extension.expected_seconds,
                    model_extension.additional_calls,
                    model_extension.additional_seconds,
                )
            if model_budget:
                model_budget.require(await run_blocking(model_budget.snapshot, phase))
            budget = await run_blocking(
                call_ledger.execution_budget, task_id, request.node_id
            )
            if budget is not None:
                if extension:
                    await run_blocking(
                        budget.extend,
                        extension.request_id,
                        extension.stage_key,
                        extension.expected_used,
                        extension.expected_limit,
                        extension.additional,
                    )
                if phase != "review":
                    await run_blocking(budget.remaining)
            elif extension:
                raise HTTPException(409, "任务执行额度不可用")
        await redis_manager.clear_cancellation_request(task_id)
        _auto_resume_counts.pop(task_id, None)
        if model_extension and not await run_blocking(
            model_budget.mark_resume_scheduled, model_extension.request_id
        ):
            _scheduled_tasks.discard(task_id)
            return ResumeTaskResponse(
                success=True,
                task_id=task_id,
                node_id=request.node_id,
                message="该模型额度确认已提交恢复，请查看当前进度。",
            )
        if extension and not await run_blocking(
            budget.mark_resume_scheduled, extension.request_id
        ):
            _scheduled_tasks.discard(task_id)
            return ResumeTaskResponse(
                success=True,
                task_id=task_id,
                node_id=request.node_id,
                message="该确认请求已提交过恢复；追加次数未重复，请查看当前任务进度。",
            )
        background_tasks.add_task(
            run_modeling_task_async,
            task_id,
            problem.ques_all,
            problem.comp_template,
            problem.format_output,
            problem.user_requirements,
            request.node_id,
            execution_backend=problem.execution_backend,
        )
    except (
        ModelStageBudgetExceeded,
        call_ledger.ExecutionBudgetExceeded,
        call_ledger.ExecutionBudgetConflict,
        call_ledger.ExecutionBudgetUnavailable,
    ) as exc:
        _scheduled_tasks.discard(task_id)
        raise HTTPException(409, str(exc)) from exc
    except BaseException:
        _scheduled_tasks.discard(task_id)
        raise
    return ResumeTaskResponse(
        success=True,
        task_id=task_id,
        node_id=request.node_id,
        message=f"任务将从“{checkpoint.node_label(request.node_id, state)}”继续运行",
    )


@router.post("/modeling/{task_id}/cancel", response_model=CancelTaskResponse)
async def cancel_task(task_id: str):
    """取消正在运行的任务。"""
    task_id = http_task_id(task_id)
    active = _active_tasks.get(task_id)
    if active is not None or task_id in _scheduled_tasks:
        _pending_cancellations.add(task_id)
    if active is not None:
        task, cancel_event = active
        cancel_event.set()
        task.cancel()
        try:
            checkpoint, _ = _load_workflow_checkpoint(task_id)
            checkpoint.mark_status("stopping")
        except HTTPException:
            pass
    try:
        await redis_manager.request_cancellation(task_id)
    except Exception as exc:
        logger.warning(f"保存取消标记失败，本机运行任务已直接中断: {exc}")

    if active is not None:
        still_stopping = _active_tasks.get(task_id) is active
        logger.info(f"已处理任务 {task_id} 的取消请求")
        return CancelTaskResponse(
            success=True,
            message=(
                "正在停止，等待当前调用与执行器退出"
                if still_stopping
                else "停止请求已处理，原运行已退出"
            ),
        )

    try:
        checkpoint, state = _load_workflow_checkpoint(task_id)
        if state.get("status") == "awaiting_approval":
            checkpoint.mark_status("stopped")
            await redis_manager.publish_message(
                task_id,
                SystemMessage(
                    content="任务已停止", type="warning", task_status="stopped"
                ),
            )
            return CancelTaskResponse(
                success=True,
                message="待审核任务已停止",
            )
    except HTTPException:
        pass

    messages = await redis_manager.load_task_messages(task_id)
    if messages and redis_manager.task_status_from_messages(messages) == "running":
        await redis_manager.publish_message(
            task_id,
            SystemMessage(
                content="任务已停止（运行进程已不存在）",
                type="warning",
                task_status="stopped",
            ),
        )
        try:
            checkpoint, _ = _load_workflow_checkpoint(task_id)
            checkpoint.mark_status("stopped")
        except HTTPException:
            pass
        logger.info(f"已修正无运行进程的任务状态: {task_id}")
        return CancelTaskResponse(
            success=True,
            message="任务已停止",
        )

    if not messages:
        return CancelTaskResponse(
            success=False,
            message="任务不存在",
        )

    return CancelTaskResponse(
        success=False,
        message="任务已经结束",
    )
