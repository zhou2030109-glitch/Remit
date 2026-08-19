"""Remit 对外协议枚举。

这里的成员值会通过 REST / WebSocket 传给前端，
属于稳定的线协议常量，修改取值需要前后端同步。
"""

from enum import Enum


class CompTemplate(str, Enum):
    """竞赛论文模板。"""

    CHINA = "CHINA"
    AMERICAN = "AMERICAN"


class FormatOutPut(str, Enum):
    """论文产出格式。"""

    Markdown = "Markdown"
    LaTeX = "LaTeX"


class AgentType(str, Enum):
    """流水线角色标识。"""

    COORDINATOR = "CoordinatorAgent"
    MODELER = "ModelerAgent"
    CODER = "CoderAgent"
    WRITER = "WriterAgent"
    SYSTEM = "SystemAgent"


class AgentStatus(str, Enum):
    """角色运行状态。"""

    START = "start"
    WORKING = "working"
    DONE = "done"
    ERROR = "error"
    SUCCESS = "success"
