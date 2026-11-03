"""运行错误契约：给出可操作原因，原始异常不进入诊断导出。"""

from pathlib import Path
from uuid import uuid4

from app.core.llm.errors import TransientLLMError, exception_chain, http_status_code


def describe(error: Exception, root: Path, *, retrying: bool, attempts: int) -> dict:
    """按可验证的错误信号分类，不把科学门禁伪装成技术成功。"""
    from app.services.writing_workspace import read_json

    state = read_json(root / "workflow_state.json")
    fallback = None
    for cause in exception_chain(error):
        report = _describe_error(cause, root, state, retrying, attempts)
        if fallback is None:
            fallback = report
        if report["code"] != "UNCLASSIFIED":
            return report
    return fallback


def _describe_error(error, root, state, retrying, attempts):
    status = http_status_code(error)
    name = type(error).__name__
    code, layer, reason, retryable = (
        "UNCLASSIFIED",
        "workflow",
        "当前步骤未完成，请检查配置与已保存成果。",
        False,
    )
    if status in {401, 403}:
        code, layer, reason = (
            "AUTHENTICATION",
            "configuration",
            "模型服务拒绝访问，请检查密钥、账户权限和余额。",
        )
    elif status in {400, 404, 422}:
        code, layer, reason = (
            "MODEL_CONFIGURATION",
            "configuration",
            "模型服务不接受当前模型、协议或请求参数，请修改配置。",
        )
    elif status == 429:
        code, layer, reason, retryable = (
            "RATE_LIMIT",
            "transport",
            "模型服务正在限流，已保留当前成果。",
            True,
        )
    elif isinstance(error, TransientLLMError) or status in {408, 500, 502, 503, 504} or any(
        token in name
        for token in ("Timeout", "Connection", "Transport", "StreamInterrupted")
    ):
        code, layer, reason, retryable = (
            "TRANSPORT",
            "transport",
            "模型请求中断或超时，结果及费用可能未知。",
            True,
        )
    elif isinstance(error, (FileNotFoundError, PermissionError)):
        code, layer, reason = (
            "LOCAL_FILE",
            "storage",
            "文件缺失或不可访问，请核对附件和目录权限。",
        )
    elif name == "ExecutionBudgetExceeded":
        code, layer, reason = (
            "EXECUTION_BUDGET",
            "execution",
            f"本阶段代码执行额度已用完（已预占 {error.used}/{error.limit} 次）。"
            "成果已保留，普通恢复不会重置额度；请检查结果，按需退回修改或调整阶段执行上限。",
        )
    elif name == "ExecutionBudgetUnavailable":
        code, layer, reason = (
            "EXECUTION_BUDGET_STORAGE",
            "storage",
            "代码执行额度记录不可读或不可写，已停止执行并保留成果；请检查项目目录与磁盘。",
        )
    elif name == "ModelStageBudgetExceeded":
        code, layer, reason = "MODEL_STAGE_BUDGET", "model", str(error)
    elif name == "NonRetryableLLMError":
        code, layer, reason = (
            "REQUEST_CONTRACT",
            "model",
            "模型请求预算或能力要求未满足，请检查模型设置并拆分当前步骤。",
        )
    elif name in {"WorkflowPausedForReview", "WorkflowApprovalRequired"}:
        code, layer, reason = "REVIEW_REQUIRED", "science", "当前证据需要人工核验。"
    return {
        "code": code,
        "layer": layer,
        "task_id": root.name,
        "run_id": state.get("execution_id") or state.get("run_id"),
        "attempt_id": uuid4().hex,
        "stage": state.get("current_node"),
        "reason": reason,
        "technical_detail": name,
        "budget_phase": getattr(error, "phase", None),
        "retryable": retryable,
        "retrying": retrying,
        "attempts_used": getattr(error, "used_calls", attempts),
        "checkpoint": (state.get("completed_nodes") or [None])[-1],
        "saved_result_count": len(state.get("solution_results") or {}),
        "actions": (
            ["model_budget", "results", "diagnostics"]
            if code == "MODEL_STAGE_BUDGET"
            else ["settings", "results", "paper", "diagnostics"]
        )
        + ([] if retrying or code == "MODEL_STAGE_BUDGET" else ["resume"]),
    }


def withdraw_retry(root: Path) -> None:
    """自动续跑未能真正调度时撤回 retrying，恢复手动续跑入口。"""
    from app.services.writing_workspace import read_json, write_json

    path = root / ".runtime-failure.json"
    if not path.is_file():
        return
    failure = read_json(path)
    if not failure.get("retrying"):
        return
    failure["retrying"] = False
    actions = list(failure.get("actions") or [])
    if failure.get("code") != "MODEL_STAGE_BUDGET" and "resume" not in actions:
        actions.append("resume")
    failure["actions"] = actions
    write_json(path, failure)
