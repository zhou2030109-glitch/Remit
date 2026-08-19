"""工作流检查点持久化，支持停止后从指定节点安全续跑。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.schemas.request import Problem


CHECKPOINT_FILENAME = "workflow_state.json"
WRITE_NODE_KEYS = [
    "firstPage",
    "RepeatQues",
    "analysisQues",
    "modelAssumption",
    "symbol",
    "judge",
]
NODE_LABELS = {
    "coordinator": "题意识别与问题拆解",
    "research": "数据侦察与文献调研",
    "analysis": "题目理解与数据核验",
    "modeler": "总体建模方案",
    "pilot": "候选方案探索实验",
    "solve:eda": "数据清洗与探索分析",
    "solve:sensitivity_analysis": "灵敏度与稳健性分析",
    "write:firstPage": "标题、摘要与关键词",
    "write:RepeatQues": "问题重述",
    "write:analysisQues": "问题分析",
    "write:modelAssumption": "模型假设",
    "write:symbol": "符号说明",
    "write:judge": "模型评价、改进与推广",
    "finalize": "论文合并与最终质量门禁",
}

NodeStatus = Literal["completed", "interrupted", "available"]
WorkflowStatus = Literal[
    "running",
    "awaiting_approval",
    "stopped",
    "failed",
    "completed",
]


class WorkflowCheckpointError(ValueError):
    """检查点不存在、损坏或不满足续跑条件。"""


class WorkflowCheckpoint:
    """以原子 JSON 文件维护一个任务的可恢复执行状态。"""

    def __init__(self, work_dir: str | Path):
        self.work_dir = Path(work_dir).resolve()
        self.path = self.work_dir / CHECKPOINT_FILENAME

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def initialize(self, problem: Problem) -> dict[str, Any]:
        """为新任务创建初始状态。

        Args:
            problem: 完整任务配置。

        Returns:
            新建并已落盘的状态字典。
        """
        state: dict[str, Any] = {
            "version": 1,
            "problem": problem.model_dump(mode="json"),
            "status": "running",
            "current_node": None,
            # 新任务启用调研与探索实验节点；旧任务缺失该字段时保持旧节点序
            "workflow_features": ["research", "analysis", "pilot"],
            "completed_nodes": [],
            "questions": {},
            "ques_count": 0,
            "coordinator_response": None,
            "modeler_response": None,
            "fable_critic_calls_used": 0,
            "model_revision_history": {},
            "model_execution_reviews": {},
            "solution_results": {},
            "write_results": {},
            "pending_approval": None,
            "approval_history": [],
            "revision_feedback": {},
            "revision_counts": {},
            "protected_input_files": sorted(
                str(path.relative_to(self.work_dir))
                for path in self.work_dir.rglob("*")
                if path.is_file() and path.name != CHECKPOINT_FILENAME
            ),
            "updated_at": self._now(),
        }
        self.save(state)
        return state

    def load(self) -> dict[str, Any]:
        """读取并验证检查点。

        Returns:
            检查点状态。

        Raises:
            WorkflowCheckpointError: 文件缺失、损坏或版本不支持。
        """
        if not self.path.is_file():
            raise WorkflowCheckpointError("该任务没有可恢复检查点")
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowCheckpointError("任务检查点损坏，无法安全续跑") from exc
        if not isinstance(state, dict) or state.get("version") != 1:
            raise WorkflowCheckpointError("任务检查点版本不受支持")
        return state

    def save(self, state: dict[str, Any]) -> None:
        """原子写入检查点，避免进程中断留下半个 JSON 文件。

        Args:
            state: 待持久化状态。
        """
        self.work_dir.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = self._now()
        temp_path = self.path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    @staticmethod
    def solution_keys(state: dict[str, Any]) -> list[str]:
        """从协调结果生成稳定的求解节点顺序。"""
        questions = state.get("questions")
        if not isinstance(questions, dict) or not questions:
            return []
        question_keys = [
            key
            for key in questions
            if str(key).startswith("ques") and key != "ques_count"
        ]
        return ["eda", *question_keys, "sensitivity_analysis"]

    @classmethod
    def node_order(cls, state: dict[str, Any]) -> list[str]:
        """返回当前检查点已知的完整节点顺序。"""
        features = set(state.get("workflow_features") or [])
        order = ["coordinator"]
        if state.get("coordinator_response") is not None or state.get("questions"):
            if "research" in features:
                order.append("research")
            if "analysis" in features:
                order.append("analysis")
            order.append("modeler")
        if state.get("modeler_response") is not None:
            for key in cls.solution_keys(state):
                order.append(f"solve:{key}")
                if key == "eda" and "pilot" in features:
                    order.append("pilot")
            order.extend(f"write:{key}" for key in WRITE_NODE_KEYS)
            order.append("finalize")
        return order

    @staticmethod
    def node_label(node_id: str, state: dict[str, Any]) -> str:
        """生成面向用户的节点名称。"""
        if node_id.startswith("solve:ques"):
            key = node_id.split(":", 1)[1]
            question = str(state.get("questions", {}).get(key, "")).strip()
            number = key.removeprefix("ques")
            suffix = f"：{' '.join(question.split())[:36]}" if question else ""
            return f"问题 {number} 求解与论文段落{suffix}"
        return NODE_LABELS.get(node_id, node_id)

    @classmethod
    def resume_nodes(cls, state: dict[str, Any]) -> list[dict[str, str]]:
        """列出当前状态下具备完整前置产物的可选续跑节点。"""
        order = cls.node_order(state)
        if not order:
            return []
        completed = set(state.get("completed_nodes", []))
        first_incomplete = next(
            (index for index, node_id in enumerate(order) if node_id not in completed),
            len(order),
        )
        last_index = min(first_incomplete, len(order) - 1)
        current_node = state.get("current_node")
        nodes: list[dict[str, str]] = []
        for node_id in order[: last_index + 1]:
            status: NodeStatus = "available"
            if node_id in completed:
                status = "completed"
            elif node_id == current_node:
                status = "interrupted"
            nodes.append(
                {
                    "node_id": node_id,
                    "label": cls.node_label(node_id, state),
                    "status": status,
                }
            )
        return nodes

    def start_node(self, state: dict[str, Any], node_id: str) -> None:
        """在执行节点前先记录当前位置。"""
        state["status"] = "running"
        state["current_node"] = node_id
        self.save(state)

    def complete_node(self, state: dict[str, Any], node_id: str) -> None:
        """节点产物保存后再提交完成标记。"""
        completed = list(state.get("completed_nodes", []))
        if node_id not in completed:
            completed.append(node_id)
        state["completed_nodes"] = completed
        state["current_node"] = None
        self.save(state)

    def request_approval(
        self,
        state: dict[str, Any],
        node_id: str,
        *,
        summary: str,
        artifacts: list[str] | None = None,
        quality_report: dict[str, Any] | None = None,
        allow_incomplete: bool = False,
        explain: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """持久化节点审核请求；未明确批准前工作流不得继续。

        Args:
            allow_incomplete: 允许对未完成节点挂起审批（门禁多次失败时
                任务挂起等待人工处理，而不是直接作废）。此时失败节点本身
                也是有效的返修目标。
            explain: 知情审批扩展（做了什么/关键数字/下一步/退回建议）。
        """
        if not allow_incomplete and node_id not in state.get("completed_nodes", []):
            raise WorkflowCheckpointError("节点尚未完成，不能提交人工审核")
        if state.get("pending_approval"):
            raise WorkflowCheckpointError("已有节点正在等待人工审核")

        revision_counts = dict(state.get("revision_counts", {}))
        eligible_statuses = (
            {"completed", "interrupted", "available"}
            if allow_incomplete
            else {"completed"}
        )
        pending = {
            "checkpoint_id": str(uuid4()),
            "node_id": node_id,
            "node_label": self.node_label(node_id, state),
            "summary": summary.strip(),
            "artifacts": sorted(set(artifacts or [])),
            "quality_report": quality_report or {},
            "revision_count": int(revision_counts.get(node_id, 0)),
            "revision_targets": [
                {"node_id": item["node_id"], "label": item["label"]}
                for item in self.resume_nodes(state)
                if item["status"] in eligible_statuses
            ],
            "explain": explain or {},
            "requested_at": self._now(),
        }
        state["pending_approval"] = pending
        state["status"] = "awaiting_approval"
        state["current_node"] = node_id
        self.save(state)
        return pending

    def pending_approval(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """返回规范化后的待审核记录。"""
        pending = state.get("pending_approval")
        return dict(pending) if isinstance(pending, dict) else None

    def approve(self, state: dict[str, Any], checkpoint_id: str) -> dict[str, Any]:
        """记录人工批准并解锁下一个节点。"""
        pending = self._validate_pending_approval(state, checkpoint_id)
        state.setdefault("approval_history", []).append(
            {
                **pending,
                "decision": "approve",
                "feedback": "",
                "decided_at": self._now(),
            }
        )
        approved = list(state.get("approved_nodes", []))
        if pending["node_id"] not in approved:
            approved.append(pending["node_id"])
        state["approved_nodes"] = approved
        feedback_by_node = dict(state.get("revision_feedback", {}))
        feedback_by_node.pop(str(pending["node_id"]), None)
        state["revision_feedback"] = feedback_by_node
        state["pending_approval"] = None
        state["status"] = "running"
        state["current_node"] = None
        self.save(state)
        return state

    def request_revision(
        self,
        state: dict[str, Any],
        checkpoint_id: str,
        feedback: str,
        target_node_id: str | None = None,
    ) -> dict[str, Any]:
        """记录人工修改意见，使当前审核节点及其下游成果失效。"""
        normalized_feedback = feedback.strip()
        if not normalized_feedback:
            raise WorkflowCheckpointError("退回修改时必须填写具体修改意见")
        pending = self._validate_pending_approval(state, checkpoint_id)
        node_id = target_node_id or str(pending["node_id"])
        valid_targets = {
            str(item.get("node_id"))
            for item in pending.get("revision_targets", [])
            if isinstance(item, dict)
        }
        if node_id not in valid_targets:
            raise WorkflowCheckpointError("所选返修节点不在本次审核的有效上游范围内")
        state.setdefault("approval_history", []).append(
            {
                **pending,
                "decision": "revise",
                "feedback": normalized_feedback,
                "revision_target_node_id": node_id,
                "decided_at": self._now(),
            }
        )
        state["pending_approval"] = None
        state = self.prepare_resume(state, node_id)
        feedback_by_node = dict(state.get("revision_feedback", {}))
        feedback_by_node[node_id] = normalized_feedback
        state["revision_feedback"] = feedback_by_node
        revision_counts = dict(state.get("revision_counts", {}))
        revision_counts[node_id] = int(revision_counts.get(node_id, 0)) + 1
        state["revision_counts"] = revision_counts
        state["status"] = "running"
        self.save(state)
        return state

    @staticmethod
    def consume_revision_feedback(state: dict[str, Any], node_id: str) -> str:
        """读取节点返修意见；保留到新结果通过审核，便于异常重试。"""
        feedback = state.get("revision_feedback", {}).get(node_id, "")
        return str(feedback).strip()

    @staticmethod
    def cumulative_revision_feedback(
        state: dict[str, Any],
        *node_ids: str,
    ) -> list[str]:
        """按发生顺序返回指定节点的全部返修意见。"""
        targets = {str(node_id) for node_id in node_ids if str(node_id).strip()}
        feedback_items: list[str] = []
        for item in state.get("approval_history", []):
            if not isinstance(item, dict) or item.get("decision") != "revise":
                continue
            target = str(
                item.get("revision_target_node_id") or item.get("node_id") or ""
            )
            feedback = str(item.get("feedback", "")).strip()
            if target in targets and feedback and feedback not in feedback_items:
                feedback_items.append(feedback)
        active_feedback = state.get("revision_feedback", {})
        if isinstance(active_feedback, dict):
            for node_id in node_ids:
                feedback = str(active_feedback.get(node_id, "")).strip()
                if feedback and feedback not in feedback_items:
                    feedback_items.append(feedback)
        return feedback_items

    @staticmethod
    def _validate_pending_approval(
        state: dict[str, Any], checkpoint_id: str
    ) -> dict[str, Any]:
        pending = state.get("pending_approval")
        if state.get("status") != "awaiting_approval" or not isinstance(pending, dict):
            raise WorkflowCheckpointError("当前没有等待处理的人工审核")
        if pending.get("checkpoint_id") != checkpoint_id:
            raise WorkflowCheckpointError("审核请求已变化，请刷新后再操作")
        return dict(pending)

    def mark_status(self, status: WorkflowStatus) -> None:
        """更新任务生命周期状态；文件不存在时保持无操作。"""
        if not self.path.is_file():
            return
        state = self.load()
        state["status"] = status
        if status in {"stopped", "failed", "completed"}:
            state["pending_approval"] = None
        self.save(state)

    def upgrade_problem_analysis(self, state: dict[str, Any]) -> dict[str, Any]:
        """为尚未进入建模阶段的旧任务补上数据校正版题目理解节点。"""
        features = list(state.get("workflow_features") or [])
        if "analysis" in features or state.get("modeler_response") is not None:
            return state
        if "research" in features:
            features.insert(features.index("research") + 1, "analysis")
        else:
            features.insert(0, "analysis")
        state["workflow_features"] = features
        self.save(state)
        return state

    def _invalidate_pilot_state(self, state: dict[str, Any]) -> None:
        """作废探索实验的全部痕迹：状态键、定案覆盖的方案与磁盘旧结果。

        必须删除磁盘上的 pilot_results.json——校验器读磁盘现存文件，
        旧结果会冒充新实验通过校验，使用户的退回意见静默失效。
        """
        state.pop("pilot_plan", None)
        state.pop("pilot_results", None)
        state.pop("pilot_decision", None)
        state.pop("pilot_skipped", None)
        # 引用台账由候选溯源加定案裁决推导而来，两者作废时它必须一起作废，
        # 否则论文会引用一批本轮根本没验证过的文献
        state.pop("citation_ledger", None)
        pre_pilot = state.pop("modeler_response_pre_pilot", None)
        if pre_pilot is not None:
            state["modeler_response"] = pre_pilot
        (self.work_dir / "pilot_results.json").unlink(missing_ok=True)
        (self.work_dir / "final_citations.json").unlink(missing_ok=True)

    def prepare_resume(self, state: dict[str, Any], node_id: str) -> dict[str, Any]:
        """校验续跑节点并使该节点及其下游旧产物失效。

        Args:
            state: 当前检查点。
            node_id: 用户选择的节点 ID。

        Returns:
            已完成失效处理并落盘的状态。

        Raises:
            WorkflowCheckpointError: 节点不可用或缺少前置结果。
        """
        available = {item["node_id"] for item in self.resume_nodes(state)}
        if node_id not in available:
            raise WorkflowCheckpointError("所选节点缺少完整前置成果，不能从这里续跑")

        # 兼容升级前已经执行过 Fable、但尚未记录独立预算字段的任务。
        # 只要评审结论已经落盘，就视为本任务额度已使用，避免续跑再次扣费。
        legacy_council = state.get("model_council")
        if isinstance(legacy_council, dict) and legacy_council.get("critic_model"):
            recorded_calls = legacy_council.get("critic_calls_used", 1)
            try:
                recorded_calls = max(1, int(recorded_calls))
            except (TypeError, ValueError):
                recorded_calls = 1
            state["fable_critic_calls_used"] = max(
                int(state.get("fable_critic_calls_used", 0) or 0),
                recorded_calls,
            )

        order = self.node_order(state)
        selected_index = order.index(node_id)
        invalidated = set(order[selected_index:])
        if node_id.startswith("write:"):
            # 结构章节相互独立：退回单章只作废该章与最终合并，
            # 不牵连其他已完成章节
            invalidated = {node_id, "finalize"}
        state["pending_approval"] = None
        state["completed_nodes"] = [
            item for item in state.get("completed_nodes", []) if item not in invalidated
        ]
        state["approved_nodes"] = [
            item for item in state.get("approved_nodes", []) if item not in invalidated
        ]
        if "pilot" in invalidated:
            self._invalidate_pilot_state(state)
        # 任何续跑都会改动论文内容，终稿评审必须重做
        state.pop("paper_review", None)

        if node_id == "coordinator":
            previous_coordinator = state.get("coordinator_response")
            if isinstance(previous_coordinator, dict):
                state["previous_coordinator_response"] = previous_coordinator
            state["questions"] = {}
            state["ques_count"] = 0
            state["coordinator_response"] = None
            state.pop("coordinator_response_pre_analysis", None)
            state.pop("analysis_response", None)
            state.pop("previous_analysis_response", None)
            state.pop("data_profile", None)
            state.pop("literature_review", None)
            state.pop("literature_brief", None)
            state.pop("method_recommendations", None)
            state["modeler_response"] = None
            state.pop("modeler_primary_response", None)
            state.pop("model_scout_proposal", None)
            state.pop("model_council", None)
            state["model_revision_history"] = {}
            state["model_execution_reviews"] = {}
            state["solution_results"] = {}
            state["write_results"] = {}
            (self.work_dir / "problem_analysis.json").unlink(missing_ok=True)
            (self.work_dir / "method_recommendations.json").unlink(missing_ok=True)
            (self.work_dir / "method_cards.json").unlink(missing_ok=True)
        elif node_id == "research":
            previous_analysis = state.get("analysis_response")
            if isinstance(previous_analysis, dict):
                state["previous_analysis_response"] = previous_analysis
            pre_analysis = state.get("coordinator_response_pre_analysis")
            if isinstance(pre_analysis, dict):
                state["coordinator_response"] = pre_analysis
            state.pop("analysis_response", None)
            state.pop("data_profile", None)
            state.pop("literature_review", None)
            state.pop("literature_brief", None)
            state.pop("method_recommendations", None)
            state["modeler_response"] = None
            state.pop("modeler_primary_response", None)
            state.pop("model_scout_proposal", None)
            state.pop("model_council", None)
            state["model_revision_history"] = {}
            state["model_execution_reviews"] = {}
            # 换调研方向意味着方案会变：旧求解产物必须物理作废，
            # 否则续跑时会被"已有产物快速恢复"路径静默复用
            results = dict(state.get("solution_results", {}))
            self._purge_solution_artifacts(self.solution_keys(state), state, results)
            state["solution_results"] = {}
            state["write_results"] = {}
            (self.work_dir / "literature_review.json").unlink(missing_ok=True)
            (self.work_dir / "method_cards.json").unlink(missing_ok=True)
            (self.work_dir / "problem_analysis.json").unlink(missing_ok=True)
            (self.work_dir / "method_recommendations.json").unlink(missing_ok=True)
        elif node_id == "analysis":
            previous_analysis = state.get("analysis_response")
            if isinstance(previous_analysis, dict):
                state["previous_analysis_response"] = previous_analysis
            pre_analysis = state.get("coordinator_response_pre_analysis")
            if isinstance(pre_analysis, dict):
                state["coordinator_response"] = pre_analysis
            state.pop("analysis_response", None)
            state.pop("method_recommendations", None)
            state["modeler_response"] = None
            state.pop("modeler_primary_response", None)
            state.pop("model_scout_proposal", None)
            state.pop("model_council", None)
            state["model_revision_history"] = {}
            state["model_execution_reviews"] = {}
            state["solution_results"] = {}
            state["write_results"] = {}
            (self.work_dir / "problem_analysis.json").unlink(missing_ok=True)
            (self.work_dir / "method_recommendations.json").unlink(missing_ok=True)
        elif node_id == "modeler":
            state["modeler_response"] = None
            state.pop("modeler_primary_response", None)
            state.pop("model_scout_proposal", None)
            state.pop("model_council", None)
            state["model_revision_history"] = {}
            state["model_execution_reviews"] = {}
            state["solution_results"] = {}
            state["write_results"] = {}
        elif node_id == "pilot":
            # 探索实验重跑会重新定案各问方案；作废 eda 之后的求解与写作结果
            # （pilot 自身状态已由 _invalidate_pilot_state 统一清理）
            solution_keys = self.solution_keys(state)
            invalid_solution_keys = [key for key in solution_keys if key != "eda"]
            results = dict(state.get("solution_results", {}))
            self._purge_solution_artifacts(invalid_solution_keys, state, results)
            for key in invalid_solution_keys:
                results.pop(key, None)
            state["solution_results"] = results
            state["write_results"] = {}
            revision_history = dict(state.get("model_revision_history", {}))
            execution_reviews = dict(state.get("model_execution_reviews", {}))
            for key in invalid_solution_keys:
                revision_history.pop(key, None)
                execution_reviews.pop(key, None)
            state["model_revision_history"] = revision_history
            state["model_execution_reviews"] = execution_reviews
        elif node_id.startswith("solve:"):
            selected_key = node_id.split(":", 1)[1]
            solution_keys = self.solution_keys(state)
            selected_solution_index = solution_keys.index(selected_key)
            invalid_solution_keys = solution_keys[selected_solution_index:]
            preserve_selected_artifacts = state.get("current_node") == node_id
            results = dict(state.get("solution_results", {}))
            self._purge_solution_artifacts(
                (
                    invalid_solution_keys[1:]
                    if preserve_selected_artifacts
                    else invalid_solution_keys
                ),
                state,
                results,
            )
            for key in invalid_solution_keys:
                results.pop(key, None)
            state["solution_results"] = results
            revision_history = dict(state.get("model_revision_history", {}))
            if preserve_selected_artifacts:
                selected_history = revision_history.get(selected_key, [])
                if isinstance(selected_history, list) and selected_history:
                    pending_revision = selected_history[-1]
                    revision_plan = pending_revision.get("revision_plan", {})
                    revised_model = (
                        str(revision_plan.get("selected_model", "")).strip()
                        if isinstance(revision_plan, dict)
                        else ""
                    )
                    quality_path = self.work_dir / f"{selected_key}_quality_report.json"
                    try:
                        quality_report = json.loads(
                            quality_path.read_text(encoding="utf-8-sig")
                        )
                    except (OSError, json.JSONDecodeError):
                        quality_report = {}
                    recovered_model = (
                        str(quality_report.get("selected_model", "")).strip()
                        if isinstance(quality_report, dict)
                        else ""
                    )
                    revised_normalized = "".join(
                        character.casefold()
                        for character in revised_model
                        if character.isalnum()
                    )
                    recovered_normalized = "".join(
                        character.casefold()
                        for character in recovered_model
                        if character.isalnum()
                    )
                    if (
                        pending_revision.get("trigger") == "modeler_review"
                        and revised_normalized
                        and recovered_normalized
                        and revised_normalized not in recovered_normalized
                    ):
                        previous_plan = str(
                            pending_revision.get("previous_plan", "")
                        ).strip()
                        modeler_response = state.get("modeler_response")
                        if previous_plan and isinstance(modeler_response, dict):
                            questions_solution = dict(
                                modeler_response.get("questions_solution", {})
                            )
                            questions_solution[selected_key] = previous_plan
                            modeler_response["questions_solution"] = questions_solution
            for key in invalid_solution_keys:
                revision_history.pop(key, None)
            state["model_revision_history"] = revision_history
            execution_reviews = dict(state.get("model_execution_reviews", {}))
            for key in invalid_solution_keys:
                execution_reviews.pop(key, None)
            state["model_execution_reviews"] = execution_reviews
            state["write_results"] = {}
        elif node_id.startswith("write:"):
            selected_key = node_id.split(":", 1)[1]
            results = dict(state.get("write_results", {}))
            results.pop(selected_key, None)
            state["write_results"] = results

        if node_id != "finalize":
            for filename in ("res.json", "res.md", "workflow_quality_gate.json"):
                path = self.work_dir / filename
                if path.is_file():
                    path.unlink()
        state["status"] = "running"
        state["current_node"] = node_id
        self.save(state)
        return state

    def _purge_solution_artifacts(
        self,
        keys: list[str],
        state: dict[str, Any],
        solution_results: dict[str, Any],
    ) -> None:
        """删除失效节点声明的产物，防止旧报告让新一轮门禁误通过。"""
        root = self.work_dir
        protected = {
            (root / str(value)).resolve()
            for value in state.get("protected_input_files", [])
        }
        retained = {
            (root / str(value)).resolve()
            for key, result in solution_results.items()
            if key not in keys and isinstance(result, dict)
            for value in result.get("artifacts", [])
        }
        candidates: set[Path] = set()
        for key in keys:
            saved_result = solution_results.get(key, {})
            if isinstance(saved_result, dict):
                for value in saved_result.get("artifacts", []):
                    candidate = (root / str(value)).resolve()
                    if candidate != root and root in candidate.parents:
                        candidates.add(candidate)
            report_path = root / f"{key}_quality_report.json"
            if report_path.is_file():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    report = {}
                for field in ("artifacts", "paper_ready_images"):
                    values = report.get(field, []) if isinstance(report, dict) else []
                    if isinstance(values, list):
                        for value in values:
                            candidate = (root / str(value)).resolve()
                            if candidate != root and root in candidate.parents:
                                candidates.add(candidate)
            candidates.update(
                {
                    report_path,
                    root / f"{key}_predictions.csv",
                    root / f"{key}_prediction_metrics.json",
                }
            )
        for path in candidates:
            resolved = path.resolve()
            if (
                resolved not in protected
                and resolved not in retained
                and path.is_file()
            ):
                path.unlink()
