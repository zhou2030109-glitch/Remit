"""把工作流检查点状态转换为小白可读的实时进度消息。"""

import re
from typing import Any

from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.core.project_audit import evaluated_node_status
from app.schemas.response import ProgressMessage, ProgressStage

# 结构章节固定 6 节 + coordinator/research/analysis/modeler/eda/pilot/sensitivity/finalize
_FIXED_NODE_COUNT = 14

_PLAIN_STAGES = {
    "coordinator": ("读题拆题", "AI 正在理解题目，把大题拆成几个小问"),
    "research": ("查文献摸数据", "检索该领域主流方法，同时摸清数据底细"),
    "analysis": ("核验题目理解", "结合附件画像和文献证据修正逐题理解"),
    "modeler": ("设计方案", "结合文献和数据，为每个小问挑选数学方法"),
    "pilot": ("小实验选模型", "候选方案先跑小样本 PK，用真实数据挑最优"),
    "solve:eda": ("检查数据", "清洗数据、摸清数据的基本情况"),
    "solve:sensitivity_analysis": (
        "稳定性测试",
        "换些条件再算一遍，看结论稳不稳",
    ),
    "write:firstPage": ("写论文：标题与摘要", "起标题、写全文摘要和关键词"),
    "write:RepeatQues": ("写论文：问题重述", "用自己的话把题目重新讲一遍"),
    "write:analysisQues": ("写论文：问题分析", "说明每问的思路和切入点"),
    "write:modelAssumption": ("写论文：模型假设", "列出建模用到的简化假设"),
    "write:symbol": ("写论文：符号说明", "整理论文里用到的数学符号"),
    "write:judge": ("写论文：模型评价", "总结模型的优缺点和改进方向"),
    "finalize": ("最终检查", "合并全文，做最后的质量把关"),
}


def plain_stage(node_id: str) -> tuple[str, str]:
    """返回节点的小白话名称与说明，供进度条和审批卡共用。"""
    return _plain_stage(node_id)


def _plain_stage(node_id: str) -> tuple[str, str]:
    known = _PLAIN_STAGES.get(node_id)
    if known:
        return known
    ques_match = re.match(r"solve:ques(\d+)", node_id)
    if ques_match:
        number = ques_match.group(1)
        return (
            f"解第{number}问",
            f"写代码求解第{number}问，并用数据验证结果",
        )
    return (node_id, "")


def build_progress_message(
    task_id: str,
    checkpoint: WorkflowCheckpoint,
    state: dict[str, Any],
) -> ProgressMessage:
    """从检查点状态构建进度快照；同任务固定 id 便于前端替换。"""
    order = checkpoint.node_order(state)
    completed = set(state.get("completed_nodes", []))
    current = state.get("current_node")

    stages: list[ProgressStage] = []
    for node_id in order:
        if node_id in completed:
            status = evaluated_node_status(state, node_id)
        elif node_id == current:
            status = "running"
        else:
            status = "pending"
        plain_label, description = _plain_stage(node_id)
        stages.append(
            ProgressStage(
                node_id=node_id,
                label=checkpoint.node_label(node_id, state),
                plain_label=plain_label,
                description=description,
                status=status,  # type: ignore[arg-type]
            )
        )

    # node_order 是渐进披露的：modeler 完成前总数还会增长，只能估算
    total_known = bool(state.get("modeler_response"))
    if total_known:
        total = len(order)
    else:
        ques_count = int(state.get("ques_count", 0) or 0)
        features = set(state.get("workflow_features") or [])
        fixed_count = (
            _FIXED_NODE_COUNT
            - ("research" not in features)
            - ("analysis" not in features)
            - ("pilot" not in features)
        )
        total = max(len(order), (ques_count or 4) + fixed_count)
    completed_count = len([node_id for node_id in order if node_id in completed])
    percent = round(100 * completed_count / total) if total else 0

    return ProgressMessage(
        id=f"progress:{task_id}",
        content=None,
        stages=stages,
        current_node=current,
        completed_count=completed_count,
        total_count=total,
        total_known=total_known,
        percent=min(percent, 100),
    )
