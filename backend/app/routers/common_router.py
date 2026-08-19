"""通用路由模块，提供配置查询、消息获取和健康检查等接口。"""

import json
import shutil
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.config.setting import settings
from app.core.llm.llm_factory import LLMFactory
from app.core.progress import build_progress_message
from app.core.project_audit import evaluate_analysis, evaluate_research
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.utils.common_utils import ensure_safe_task_id, get_config_template
from app.schemas.enums import AgentType, CompTemplate
from app.schemas.response import ModelerMessage, UserMessage
from app.services.redis_manager import redis_manager
from app.utils.log_util import logger

router = APIRouter()
TASK_WORK_DIR_ROOT = Path(__file__).resolve().parents[2] / "project" / "work_dir"


class TaskMessageRequest(BaseModel):
    """用户在任务页追加的持久化消息。"""

    content: str = Field(min_length=1, max_length=10000)


class TaskCopilotRequest(BaseModel):
    """用户主动请求、不改变工作流状态的只读结果解释。"""

    action: Literal["解释当前模型", "分析当前结果", "检查模型局限"]


class TaskCopilotResponse(BaseModel):
    """同时返回已落盘的用户请求和建模手回复。"""

    request: UserMessage
    response: ModelerMessage


class DeleteTaskResponse(BaseModel):
    """删除历史任务后的响应。"""

    success: bool
    task_id: str
    message: str


class ClearTaskHistoryResponse(BaseModel):
    """清空全部历史任务后的响应。"""

    success: bool
    deleted_count: int
    message: str


def _require_safe_task_id(task_id: str) -> str:
    """验证并返回安全的任务 ID。

    Args:
        task_id: 待验证的任务 ID。

    Returns:
        验证通过的任务 ID。

    Raises:
        HTTPException: 任务 ID 非法时返回 400。
    """
    try:
        return ensure_safe_task_id(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法任务ID") from exc


def _analysis_slice(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"analysis_summary": "", "question_analyses": {}}
    analyses = payload.get("question_analyses")
    return {
        "analysis_summary": str(payload.get("analysis_summary", "")),
        "question_analyses": analyses if isinstance(analyses, dict) else {},
    }


@router.get("/tasks/{task_id}/workspace")
async def get_task_workspace(task_id: str) -> dict[str, Any]:
    """返回主页面逐阶段审计快照，不依赖 Copilot 消息猜测产物。"""
    safe_task_id = _require_safe_task_id(task_id)
    root = TASK_WORK_DIR_ROOT.resolve()
    work_dir = (root / safe_task_id).resolve()
    if work_dir.parent != root or not work_dir.is_dir():
        raise HTTPException(status_code=404, detail="任务工作目录不存在")
    checkpoint = WorkflowCheckpoint(work_dir)
    try:
        state = checkpoint.load()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="任务检查点不可读") from exc

    preliminary_payload = state.get("coordinator_response_pre_analysis")
    if not isinstance(preliminary_payload, dict):
        preliminary_payload = state.get("coordinator_response")
    refined_payload = state.get("analysis_response")
    fixed_payload = (
        preliminary_payload
        if isinstance(preliminary_payload, dict)
        else refined_payload
        if isinstance(refined_payload, dict)
        else {}
    )
    fixed_questions = fixed_payload.get("questions")
    fixed_questions = (
        fixed_questions
        if isinstance(fixed_questions, dict)
        else state.get("questions")
        if isinstance(state.get("questions"), dict)
        else {}
    )
    source_questions = {
        str(key): value
        for key, value in fixed_questions.items()
        if str(key).startswith("ques") and str(key) != "ques_count"
    }
    original_problem = str(fixed_payload.get("original_problem", ""))
    if not original_problem:
        problem = state.get("problem")
        if isinstance(problem, dict):
            original_problem = str(problem.get("ques_all", ""))

    research_outcome = evaluate_research(state)
    analysis_outcome = evaluate_analysis(state)
    progress = build_progress_message(safe_task_id, checkpoint, state)
    return {
        "task_id": safe_task_id,
        "status": str(state.get("status", "unknown")),
        "source": {
            "original_problem": original_problem,
            "title": str(fixed_questions.get("title", "")),
            "background": str(fixed_questions.get("background", "")),
            "ques_count": int(state.get("ques_count", 0) or 0),
            "questions": source_questions,
        },
        "preliminary_analysis": _analysis_slice(preliminary_payload),
        "research": {
            "outcome": research_outcome,
            "data_profile": state.get("data_profile") or {},
            "literature_review": state.get("literature_review") or {},
            "literature_brief": str(state.get("literature_brief", "")),
        },
        "refined_analysis": {
            **_analysis_slice(refined_payload),
            "outcome": analysis_outcome,
        },
        "method_evidence": _method_evidence_slice(state),
        "method_recommendations": state.get("method_recommendations") or {},
        "modeler_response": state.get("modeler_response") or {},
        "progress": progress.model_dump(mode="json"),
    }


def _method_evidence_slice(state: dict) -> dict:
    """汇总"文献 → 方法卡 → 候选 → 验证 → 引用"这条证据链。

    前端"文献与方法"面板据此展示每篇文献最终是被采用、修改还是放弃。
    """
    review = state.get("literature_review")
    review = review if isinstance(review, dict) else {}
    plan = state.get("pilot_plan")
    plan = plan if isinstance(plan, dict) else {}
    ledger = state.get("citation_ledger")
    ledger = ledger if isinstance(ledger, dict) else {}

    candidates: list[dict] = []
    for question_key, question_plan in (plan.get("questions") or {}).items():
        if not isinstance(question_plan, dict):
            continue
        for candidate in question_plan.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            candidates.append(
                {
                    "question_key": str(question_key),
                    "name": str(candidate.get("name", "")),
                    "role": str(candidate.get("role", "candidate")),
                    "approach": str(candidate.get("approach", "")),
                    "source_card_id": str(candidate.get("source_card_id", "")),
                    "adaptation": str(candidate.get("adaptation", "")),
                }
            )

    return {
        "method_cards": review.get("method_cards") or [],
        "selected_papers": review.get("selected_papers") or {},
        "fulltext_stats": review.get("fulltext_stats") or {},
        "candidates": candidates,
        "citation_entries": ledger.get("entries") or [],
        "final_citations": ledger.get("used") or [],
    }


async def _load_task_messages_from_file(task_id: str) -> list[dict]:
    """从文件加载指定任务的历史消息。

    Args:
        task_id: 任务 ID。

    Returns:
        消息列表，文件不存在时返回空列表。
    """
    safe_task_id = _require_safe_task_id(task_id)
    return await redis_manager.load_task_messages(safe_task_id)


_FRONTEND_INDEX = (
    Path(__file__).resolve().parents[3] / "frontend" / "dist" / "index.html"
)


@router.get("/")
async def root():
    """API 健康检查；打包模式下根路径直接返回前端页面。"""
    if _FRONTEND_INDEX.is_file():
        return FileResponse(_FRONTEND_INDEX)
    return {"message": "Hello World"}


@router.get("/config")
async def config():
    return {
        "environment": settings.ENV,
        "deepseek_model": settings.DEEPSEEK_MODEL,
        "deepseek_base_url": settings.DEEPSEEK_BASE_URL,
        "max_chat_turns": settings.MAX_CHAT_TURNS,
        "max_retries": settings.MAX_RETRIES,
        "CORS_ALLOW_ORIGINS": settings.CORS_ALLOW_ORIGINS,
    }


@router.get("/writer_seque")
async def get_writer_seque():
    # 返回论文顺序
    config_template: dict = get_config_template(CompTemplate.CHINA)
    return list(config_template.keys())


@router.get("/messages")
async def get_task_messages(task_id: str):
    return await _load_task_messages_from_file(task_id)


@router.post("/tasks/{task_id}/messages", response_model=UserMessage)
async def post_task_message(task_id: str, request: TaskMessageRequest):
    """保存并实时广播任务页中的用户补充消息。"""
    safe_task_id = _require_safe_task_id(task_id)
    if not await redis_manager.task_exists(safe_task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="消息不能为空")
    message = UserMessage(content=content)
    await redis_manager.publish_message(safe_task_id, message)
    # 同时进入插话队列：正在运行的 Agent 会在下一轮对话时注入并遵循
    try:
        await redis_manager.push_user_note(safe_task_id, content)
    except Exception as error:
        logger.warning(f"用户插话入队失败 {safe_task_id}: {error}")
    return message


def _compact_value(value: Any, limit: int = 1600) -> Any:
    """限制 Copilot 上下文体积，同时保留可复核的结构化证据。"""
    if isinstance(value, str):
        normalized = " ".join(value.split())
        return normalized if len(normalized) <= limit else normalized[:limit] + "…"
    if isinstance(value, list):
        return [_compact_value(item, limit) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item, limit)
            for key, item in list(value.items())[:40]
        }
    return value


async def _build_task_copilot_context(task_id: str) -> dict[str, Any]:
    """收集冻结产物和近期事件；不把密钥、完整代码或工具输出交给模型。"""
    work_dir = (TASK_WORK_DIR_ROOT / task_id).resolve()
    context: dict[str, Any] = {
        "task_id": task_id,
        "workflow": {},
        "quality_reports": {},
        "prediction_metrics": {},
        "recent_events": [],
    }
    if work_dir.parent == TASK_WORK_DIR_ROOT.resolve() and work_dir.is_dir():
        state_path = work_dir / "workflow_state.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                state = {}
            if isinstance(state, dict):
                context["workflow"] = {
                    key: _compact_value(state.get(key))
                    for key in (
                        "status",
                        "current_node",
                        "completed_nodes",
                        "approved_nodes",
                        "model_revision_history",
                        "model_execution_reviews",
                    )
                }

        for path in sorted(work_dir.glob("*_quality_report.json"))[-8:]:
            try:
                report = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(report, dict):
                allowed = {
                    "status",
                    "problem_type",
                    "selected_model",
                    "candidate_models",
                    "independent_unit",
                    "data_leakage_checks",
                    "robustness_checks",
                    "limitations",
                    "type_specific",
                    "gate_failures",
                }
                context["quality_reports"][path.name] = _compact_value(
                    {key: report[key] for key in allowed if key in report}
                )

        for path in sorted(work_dir.glob("*_prediction_metrics.json"))[-8:]:
            try:
                metrics = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(metrics, dict):
                context["prediction_metrics"][path.name] = _compact_value(metrics)

    messages = await redis_manager.load_task_messages(task_id)
    for message in messages[-80:]:
        if not isinstance(message, dict) or message.get("msg_type") == "tool":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        context["recent_events"].append(
            {
                "type": message.get("msg_type"),
                "agent": message.get("agent_type"),
                "level": message.get("type"),
                "content": _compact_value(content, 900),
            }
        )
    context["recent_events"] = context["recent_events"][-24:]
    return context


@router.post("/tasks/{task_id}/copilot", response_model=TaskCopilotResponse)
async def post_task_copilot(task_id: str, request: TaskCopilotRequest):
    """让主建模模型只读分析当前冻结证据，并把答复保存到任务历史。"""
    safe_task_id = _require_safe_task_id(task_id)
    if not await redis_manager.task_exists(safe_task_id):
        raise HTTPException(status_code=404, detail="任务不存在")

    user_message = UserMessage(content=request.action)
    await redis_manager.publish_message(safe_task_id, user_message)
    context = await _build_task_copilot_context(safe_task_id)
    _, modeler_llm, _, _ = LLMFactory(safe_task_id).get_all_llms()
    action_guidance = {
        "解释当前模型": "解释当前实际选择或计划中的模型、输入输出、验证方式及是否已经真实运行。",
        "分析当前结果": "逐项分析真实指标、相对基线、稳健性和门禁状态；若尚无最终指标，必须直接说明。",
        "检查模型局限": "按影响最终成绩的严重程度列出数据、验证、模型与外推局限，并给出下一项最小验证实验。",
    }[request.action]
    try:
        llm_response = await modeler_llm.chat(
            history=[
                {
                    "role": "system",
                    "content": (
                        "你是 Project Copilot 的只读建模审稿人。只依据真实落盘证据回答，"
                        "严格区分已运行结果、候选方案和推测；禁止虚构指标。"
                        "本次回答不得改变工作流、不得调用代码工具，也不得调用 Fable。"
                        "用简洁中文先给结论，再列证据与下一步。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "action": request.action,
                            "guidance": action_guidance,
                            "frozen_task_evidence": context,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            agent_name=AgentType.MODELER,
            publish=False,
            max_retries=2,
        )
    except Exception as exc:
        logger.error(f"Project Copilot 调用失败 {safe_task_id}: {exc}")
        raise HTTPException(
            status_code=502,
            detail=f"Project Copilot 暂时无法分析：{exc}",
        ) from exc
    content = str(llm_response.content or "").strip()
    if not content:
        raise HTTPException(status_code=502, detail="Project Copilot 返回了空结果")
    agent_message = ModelerMessage(content=content)
    await redis_manager.publish_message(safe_task_id, agent_message)
    return TaskCopilotResponse(request=user_message, response=agent_message)


@router.get("/tasks")
async def list_tasks():
    """列出所有具有持久化消息记录的历史任务。"""
    return await redis_manager.list_task_summaries()


def _delete_task_work_dir(task_id: str) -> bool:
    """删除固定工作目录根路径下的任务目录。"""
    root = TASK_WORK_DIR_ROOT.resolve()
    task_dir = (root / task_id).resolve()
    if task_dir.parent != root:
        raise HTTPException(status_code=400, detail="非法任务ID")
    if not task_dir.exists():
        return False
    if not task_dir.is_dir():
        raise HTTPException(status_code=500, detail="任务文件目录异常，删除失败")
    try:
        shutil.rmtree(task_dir)
    except OSError as exc:
        logger.error(f"删除任务工作目录失败 {task_id}: {exc}")
        raise HTTPException(
            status_code=500,
            detail="任务文件正在使用或无法删除，请稍后重试",
        ) from exc
    return True


@router.delete("/tasks", response_model=ClearTaskHistoryResponse)
async def clear_task_history():
    """永久清空全部已结束任务；存在运行中任务时不删除任何内容。"""
    summaries = await redis_manager.list_task_summaries()
    running_tasks = [
        str(summary["task_id"])
        for summary in summaries
        if summary.get("status") in {"running", "awaiting_approval"}
    ]
    if running_tasks:
        raise HTTPException(
            status_code=409,
            detail=(
                f"仍有 {len(running_tasks)} 个任务正在运行，请先停止后再清空历史记录"
            ),
        )

    task_ids = [_require_safe_task_id(str(summary["task_id"])) for summary in summaries]
    for task_id in task_ids:
        _delete_task_work_dir(task_id)
        await redis_manager.delete_task_record(task_id)

    return ClearTaskHistoryResponse(
        success=True,
        deleted_count=len(task_ids),
        message=(
            f"已永久删除 {len(task_ids)} 条历史任务"
            if task_ids
            else "当前没有可清空的历史任务"
        ),
    )


@router.delete("/tasks/{task_id}", response_model=DeleteTaskResponse)
async def delete_task(task_id: str):
    """永久删除一个已结束任务的历史消息和全部生成文件。"""
    safe_task_id = _require_safe_task_id(task_id)
    messages = await redis_manager.load_task_messages(safe_task_id)
    if not messages:
        raise HTTPException(status_code=404, detail="历史任务不存在")
    if redis_manager.task_status_from_messages(messages) in {
        "running",
        "awaiting_approval",
    }:
        raise HTTPException(status_code=409, detail="任务仍在运行，请先停止后再删除")

    _delete_task_work_dir(safe_task_id)
    await redis_manager.delete_task_record(safe_task_id)
    return DeleteTaskResponse(
        success=True,
        task_id=safe_task_id,
        message="历史任务已永久删除",
    )


@router.get("/track")
async def track(task_id: str):
    # 获取任务的token使用情况

    pass


@router.get("/status")
async def get_service_status():
    """获取后端和 Redis 的运行状态。"""
    status = {
        "backend": {"status": "running", "message": "Backend service is running"},
        "redis": {"status": "unknown", "message": "Redis connection status unknown"},
    }

    # 检查Redis连接状态
    try:
        redis_client = await redis_manager.get_client()
        await redis_client.ping()  # type: ignore[reportGeneralTypeIssues]
        status["redis"] = {
            "status": "running",
            "message": "Redis connection is healthy",
        }
    except Exception as e:
        logger.error(f"Redis connection failed: {str(e)}")
        status["redis"] = {
            "status": "error",
            "message": f"Redis connection failed: {str(e)}",
        }

    return status
