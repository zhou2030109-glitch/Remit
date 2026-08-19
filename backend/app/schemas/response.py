"""出站消息与执行结果的数据模型。

所有模型最终经 WebSocket / Redis 送达前端，
字段名与 Literal 取值是前后端共享的线协议，改动需双侧同步。
"""

from datetime import datetime, timezone
from typing import Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, Field

from app.schemas.enums import AgentType


class Message(BaseModel):
    """消息公共字段。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    msg_type: str
    content: str | None = None


class ToolMessage(Message):
    """工具调用消息（代码执行、文献检索）。"""

    msg_type: Literal["tool"] = "tool"
    tool_name: Literal["execute_code", "search_scholar"]
    input: dict | None = None
    output: list | None = None


class SystemMessage(Message):
    """系统提示消息。"""

    msg_type: Literal["system"] = "system"
    type: Literal["info", "warning", "success", "error"] = "info"


class UserMessage(Message):
    """用户输入消息。"""

    msg_type: Literal["user"] = "user"


class AgentMessage(Message):
    """各角色 Agent 的产出消息。"""

    msg_type: Literal["agent"] = "agent"
    agent_type: AgentType


class ModelerMessage(AgentMessage):
    agent_type: AgentType = AgentType.MODELER


class CoordinatorMessage(AgentMessage):
    agent_type: AgentType = AgentType.COORDINATOR


class CoderMessage(AgentMessage):
    agent_type: AgentType = AgentType.CODER


class WriterMessage(AgentMessage):
    agent_type: AgentType = AgentType.WRITER
    sub_title: str | None = None


# ---- 代码执行结果 ----


class CodeExecution(BaseModel):
    """执行结果公共字段。"""

    res_type: Literal["stdout", "stderr", "result", "error"]
    msg: str | None = None


class StdOutModel(CodeExecution):
    res_type: Literal["stdout"] = "stdout"


class StdErrModel(CodeExecution):
    res_type: Literal["stderr"] = "stderr"


class ResultModel(CodeExecution):
    res_type: Literal["result"] = "result"
    format: Literal[
        "text",
        "html",
        "markdown",
        "png",
        "jpeg",
        "svg",
        "pdf",
        "latex",
        "json",
        "javascript",
    ]


class ErrorModel(CodeExecution):
    res_type: Literal["error"] = "error"
    name: str
    value: str
    traceback: str


OutputItem = Union[StdOutModel, StdErrModel, ResultModel, ErrorModel]


class ScholarMessage(ToolMessage):
    tool_name: Literal["search_scholar"] = "search_scholar"
    input: dict | None = None  # query
    output: list[str] | None = None  # cites


class InterpreterMessage(ToolMessage):
    tool_name: Literal["execute_code"] = "execute_code"
    input: dict | None = None  # code
    output: list[OutputItem] | None = None  # code_results


class ApprovalMessage(Message):
    """人工审核消息；前端据此渲染强制审阅台。"""

    msg_type: Literal["approval"] = "approval"
    checkpoint_id: str = ""
    node_id: str = ""
    node_label: str = ""
    summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    quality_report: dict[str, Any] = Field(default_factory=dict)
    revision_count: int = 0
    revision_targets: list[dict[str, str]] = Field(default_factory=list)
    options: list[str] = Field(default_factory=lambda: ["approve", "revise"])
    # 知情审批扩展：what_happened / key_numbers / next_step / revise_hint / candidates
    explain: dict[str, Any] = Field(default_factory=dict)


class ActivityMessage(Message):
    """实时活动播报；同任务固定 id，前端原位刷新，不落盘。"""

    msg_type: Literal["activity"] = "activity"
    category: Literal["llm", "code", "gate", "repair", "info"] = "info"
    detail: str = ""


class ProgressStage(BaseModel):
    """工作流中的一个可视化阶段。"""

    node_id: str
    label: str
    plain_label: str
    description: str
    status: Literal["completed", "warning", "failed", "running", "pending"] = "pending"


class ProgressMessage(Message):
    """实时工作流进度；同任务固定 id，前端只保留最新一条。"""

    msg_type: Literal["progress"] = "progress"
    stages: list[ProgressStage] = Field(default_factory=list)
    current_node: str | None = None
    completed_count: int = 0
    total_count: int = 0
    total_known: bool = False
    percent: int = 0


class ExecutionMetric(BaseModel):
    """一项可核对的模型运行指标。"""

    name: str
    model_value: float
    baseline_value: float | None = None
    higher_is_better: bool | None = None
    relative_improvement: float | None = None


class MetricExplanation(BaseModel):
    """一项指标的小白话解释。"""

    name: str
    friendly_name: str
    value_text: str
    meaning: str
    verdict: Literal["good", "ok", "poor", "info"] = "info"


class TablePreview(BaseModel):
    """结果 CSV 的行列预览，直接渲染为网页表格。"""

    filename: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, str]] = Field(default_factory=list)
    preview_limited_to_rows: int = 20


class CodeLocation(BaseModel):
    """代码所在文件及其对应的工作流章节。"""

    path: str
    section: str = ""
    language: str = ""


class ExecutionSummaryMessage(Message):
    """编码阶段每个求解节点的紧凑、持久化运行记录。"""

    msg_type: Literal["execution_summary"] = "execution_summary"
    node_id: str
    node_label: str
    status: Literal["passed", "refined", "needs_review"] = "passed"
    run_summary: str
    selected_model: str = ""
    candidate_models: list[str] = Field(default_factory=list)
    metrics: list[ExecutionMetric] = Field(default_factory=list)
    code_locations: list[CodeLocation] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    paper_ready_images: list[str] = Field(default_factory=list)
    modeler_verdict: Literal["accept", "refine", "manual_review"] = "accept"
    modeler_summary: str = ""
    modeler_evidence: list[str] = Field(default_factory=list)
    modeler_weaknesses: list[str] = Field(default_factory=list)
    writer_guidance: str = ""
    revision_count: int = 0
    metric_explanations: list[MetricExplanation] = Field(default_factory=list)
    table_previews: list[TablePreview] = Field(default_factory=list)


MessageType = Union[
    SystemMessage,
    UserMessage,
    AgentMessage,
    ToolMessage,
    ScholarMessage,
    InterpreterMessage,
    CoderMessage,
    WriterMessage,
    ModelerMessage,
    CoordinatorMessage,
    ApprovalMessage,
    ExecutionSummaryMessage,
    ProgressMessage,
    ActivityMessage,
]
