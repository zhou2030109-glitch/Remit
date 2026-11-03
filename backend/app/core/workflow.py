"""工作流模块，编排多 Agent 协作并提供节点级检查点续跑。"""

import asyncio

import json
from pathlib import Path
from typing import Any

from app.config.setting import settings
from app.core.agents.coder_agent import CoderAgent
from app.core.agents.coordinator_agent import CoordinatorAgent
from app.core.agents.modeler_agent import ModelerAgent
from app.core.prompts.modeler import get_modeler_prompt
from app.core.flows import Flows
from app.core.llm.llm import LLM
from app.core.llm.llm_factory import LLMFactory
from app.core.activity import publish_activity
from app.core.citations import (
    build_citation_brief,
    load_citation_ledger,
)
from app.core.data_scout import build_data_profile
from app.core.literature import (
    build_literature_brief,
    build_method_cards,
    run_literature_review,
)
from app.core.method_retrieval import (
    HierarchicalMethodRetriever,
    MethodSelectionEngine,
)
from app.core.model_council import ModelCouncil
from app.core.progress import build_progress_message, plain_stage
from app.core.project_audit import evaluate_analysis, evaluate_research
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.models.user_output import UserOutput
from app.schemas.A2A import (
    CoordinatorToModeler,
    ModelCouncilResult,
    ModelScoutProposal,
    ModelerToCoder,
    WriterResponse,
)
from app.schemas.request import Problem
from app.schemas.enums import CompTemplate
from app.schemas.response import SystemMessage
from app.services.task_bus import task_bus
from app.tools.base_interpreter import BaseCodeInterpreter
from app.tools.interpreter_factory import create_interpreter
from app.tools.notebook_serializer import NotebookSerializer
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.common_utils import create_work_dir, get_config_template
from app.utils.log_util import logger

from app.core.workflow_pilot import PilotNodeMixin
from app.core.workflow_solution import SolutionNodeMixin
from app.core.workflow_writing import WritingNodeMixin


class WorkFlow:
    """工作流基类。"""

    def execute(self) -> None:
        """执行工作流。"""


class WorkflowApprovalRequired(RuntimeError):
    """节点成果已落盘，工作流必须等待人工明确批准。"""

    def __init__(self, approval: dict[str, Any]):
        self.approval = approval
        super().__init__(f"等待人工审核：{approval.get('node_label', '')}")


class WorkflowPausedForReview(RuntimeError):
    """节点质量门未通过、HIL 已关闭，工作流不应被强制标 failed。

    调用方已把当前节点和原因写入 pending_approval，并把 status 改为
    ``awaiting_approval``。任务在等待用户回到页面：
    - 批准 → 携带现有不完整产物继续；
    - 退回 → 带着用户意见从该节点重新跑；
    - 续跑 → 从该节点起重新生成产物。
    """

    def __init__(self, approval: dict[str, Any]):
        self.approval = approval
        super().__init__(
            f"质量门未通过，已挂起等待人工裁决：{approval.get('node_label', '')}"
        )


class RemitWorkFlow(PilotNodeMixin, SolutionNodeMixin, WritingNodeMixin, WorkFlow):
    """数学建模工作流，按持久化节点协调四类 Agent。"""

    def __init__(self) -> None:
        self.task_id = ""
        self.work_dir = ""
        self.ques_count = 0
        self.questions: dict[str, str | int] = {}
        self.cancel_event: asyncio.Event | None = None
        self.code_interpreter: BaseCodeInterpreter | None = None
        self.checkpoint: WorkflowCheckpoint | None = None

    async def cleanup(self) -> None:
        """幂等清理代码解释器，确保取消和异常路径不会遗留内核。"""
        interpreter = self.code_interpreter
        if interpreter is None:
            return
        self.code_interpreter = None
        await interpreter.cleanup()

    def mark_status(self, status: str) -> None:
        """将运行结果同步到持久化检查点。

        Args:
            status: stopped、failed 或 completed。
        """
        if self.checkpoint is not None and status in {
            "running",
            "stopping",
            "awaiting_approval",
            "stopped",
            "failed",
            "completed",
        }:
            self.checkpoint.mark_status(status)  # type: ignore[arg-type]

    async def _check_cancelled(self) -> None:
        """收到取消信号时中断；终态消息由任务运行器统一发布。"""
        if self.cancel_event and self.cancel_event.is_set():
            raise asyncio.CancelledError("任务被用户停止")

    async def execute(  # type: ignore[reportIncompatibleMethodOverride]
        self,
        problem: Problem,
        resume_from: str | None = None,
        continue_existing: bool = False,
    ) -> None:
        """执行新任务或从指定节点恢复。

        Args:
            problem: 完整题目信息和输出配置。
            resume_from: 需要重新执行的节点 ID；为空表示新任务。
        """
        self.task_id = problem.task_id
        self.work_dir = create_work_dir(self.task_id)
        self.checkpoint = WorkflowCheckpoint(self.work_dir)
        if resume_from:
            state = self.checkpoint.prepare_resume(self.checkpoint.load(), resume_from)
        elif continue_existing:
            state = self.checkpoint.load()
            state = self._resolve_pending_approval_on_resume(state)
            state["status"] = "running"
            self.checkpoint.save(state)
        else:
            state = self.checkpoint.initialize(problem)
        state = self.checkpoint.upgrade_problem_analysis(state)
        from uuid import uuid4

        state["execution_id"] = uuid4().hex
        self.checkpoint.save(state)
        # The durable problem includes creation-only settings absent from the
        # legacy background runner's arguments. Reuse it on start and resume.
        problem = Problem.model_validate(state["problem"])
        await self._publish_progress(state)

        llm_factory = LLMFactory(self.task_id)
        separate_writing = "separate_writing" in state.get("workflow_features", [])
        writer_llm = None
        if separate_writing:
            coordinator_llm, modeler_llm, coder_llm = llm_factory.get_modeling_llms()
        else:
            coordinator_llm, modeler_llm, coder_llm, writer_llm = (
                llm_factory.get_all_llms()
            )
        model_council: ModelCouncil | None = None
        if (
            settings.MODEL_COUNCIL_ENABLED
            and problem.task_purpose != "numerical_verification"
        ):
            scout_llm, critic_llm = llm_factory.get_model_council_llms()
            if (
                settings.MODEL_COUNCIL_REQUIRE_DIVERSE_BACKENDS
                and not ModelCouncil.reviewers_are_independent(
                    modeler_llm,
                    scout_llm,
                    critic_llm,
                )
            ):
                await task_bus.notify(
                    self.task_id,
                    "模型评审组与主建模手使用完全相同的模型接入点，"
                    "无法提供独立性或故障隔离，本次已自动跳过额外评审调用。",
                    "warning",
                )
            else:
                model_council = ModelCouncil(
                    task_id=self.task_id,
                    scout_llm=scout_llm,
                    critic_llm=critic_llm,
                    cancel_event=self.cancel_event,
                )
        coordinator_agent = CoordinatorAgent(
            self.task_id,
            coordinator_llm,
            context_window=settings.COORDINATOR_CONTEXT_WINDOW,
            cancel_event=self.cancel_event,
        )
        modeler_agent = ModelerAgent(
            self.task_id,
            modeler_llm,
            context_window=settings.MODELER_CONTEXT_WINDOW,
            cancel_event=self.cancel_event,
            system_prompt=get_modeler_prompt(problem.task_purpose),
        )

        coordinator_response = await self._coordinator_node(
            problem,
            state,
            coordinator_agent,
        )

        scholar = OpenAlexScholar(
            task_id=self.task_id,
            email=settings.OPENALEX_EMAIL,
            api_key=settings.OPENALEX_API_KEY,
        )
        coordinator_response = await self._research_node(
            state,
            coordinator_response,
            modeler_llm,
            scholar,
        )

        coordinator_response = await self._analysis_node(
            state,
            coordinator_response,
            coordinator_agent,
        )

        if problem.task_purpose == "numerical_verification":
            from app.core.task_purpose import NUMERICAL_VERIFICATION_SCOPE

            coordinator_response = coordinator_response.model_copy(
                update={
                    "task_purpose": problem.task_purpose,
                    "user_requirements": coordinator_response.user_requirements
                    + "\n"
                    + NUMERICAL_VERIFICATION_SCOPE,
                }
            )
        modeler_response = await self._modeler_node(
            state,
            coordinator_response,
            modeler_agent,
            model_council,
        )

        self.questions = coordinator_response.questions
        self.ques_count = coordinator_response.ques_count
        user_output = self._restore_user_output(state)
        flows = Flows(
            self.questions,
            user_requirements=problem.user_requirements,
            task_purpose=problem.task_purpose,
        )
        self._refresh_citation_brief(flows, state)
        config_template = get_config_template(
            problem.comp_template
        ) or get_config_template(CompTemplate.CHINA)

        writer_agent = (
            self._new_writer_agent(writer_llm, problem, scholar) if writer_llm else None
        )

        solution_flows = flows.get_solution_flows(self.questions, modeler_response)
        pending_solution_keys = [
            key
            for key in solution_flows
            if f"solve:{key}" not in state.get("completed_nodes", [])
        ]
        pilot_pending = "pilot" in self.checkpoint.node_order(
            state
        ) and "pilot" not in state.get("completed_nodes", [])
        if pending_solution_keys or pilot_pending:
            await self._initialize_interpreter(state)
            if self.code_interpreter is None:
                raise RuntimeError("code interpreter is not initialized")
            coder_agent = self._new_coder_agent(coder_llm, problem)
            # EDA 先行：探索实验依赖清洗后的数据
            if "eda" in solution_flows and "solve:eda" not in state.get(
                "completed_nodes", []
            ):
                await self._solution_node(
                    key="eda",
                    value=solution_flows["eda"],
                    state=state,
                    flows=flows,
                    config_template=config_template,
                    modeler_agent=modeler_agent,
                    coder_agent=coder_agent,
                    writer_agent=writer_agent,
                    user_output=user_output,
                )
            if pilot_pending:
                await self._pilot_node(
                    state,
                    modeler_agent,
                    coder_agent,
                    modeler_response,
                )
                # 自动模式不会经过审批重启，必须在本轮立即把定案方案
                # 送入正式求解；探索降级时保存的方案仍是原方案。
                modeler_response = ModelerToCoder.model_validate(
                    state["modeler_response"]
                )
                solution_flows = flows.get_solution_flows(
                    self.questions, modeler_response
                )
                self._refresh_citation_brief(flows, state)
            for key, value in solution_flows.items():
                if key == "eda":
                    continue
                node_id = f"solve:{key}"
                if node_id in state.get("completed_nodes", []):
                    continue
                await self._solution_node(
                    key=key,
                    value=value,
                    state=state,
                    flows=flows,
                    config_template=config_template,
                    modeler_agent=modeler_agent,
                    coder_agent=coder_agent,
                    writer_agent=writer_agent,
                    user_output=user_output,
                )
        await self.cleanup()

        if separate_writing:
            from app.services.writing_workspace import sync_results

            if "critical_review" in state.get(
                "workflow_features", []
            ) and "review_results" not in state.get("approved_nodes", []):
                if "review_results" not in state.get("completed_nodes", []):
                    await self._complete_node(state, "review_results")
                await self._require_human_approval(
                    state,
                    "review_results",
                    summary="各小问的计算与自动检查已结束，请检查最终结果。批准后将同步到论文写作。",
                    artifacts=[
                        name
                        for result in state.get("solution_results", {}).values()
                        for name in result.get("artifacts", [])
                        if isinstance(name, str)
                    ],
                )
            await self._start_node(state, "sync_writing")
            await asyncio.to_thread(sync_results, Path(self.work_dir), state)
            await self._complete_node(state, "sync_writing")
            self.checkpoint.mark_status("completed")
            await task_bus.notify(
                self.task_id,
                "建模与计算已完成，写作素材已交接。论文正文尚未生成，可在论文区启动墨墨撰写初稿。",
                "success",
            )
            return

        write_flows = flows.get_write_flows(
            user_output,
            config_template,
            problem.ques_all,
        )
        pending_write = [
            (key, prompt)
            for key, prompt in write_flows.items()
            if f"write:{key}" not in state.get("completed_nodes", [])
        ]
        if pending_write:
            await self._write_chapters_parallel(
                pending_write,
                state,
                writer_llm,
                problem,
                scholar,
                user_output,
            )

        if "finalize" not in state.get("completed_nodes", []):
            await self._finalize_node(state, user_output, writer_agent, modeler_llm)
        self.checkpoint.mark_status("completed")

    def _refresh_citation_brief(self, flows: Flows, state: dict[str, Any]) -> None:
        """把最新的引用台账注入写作提示词。

        台账在探索实验定案时才生成，续跑时则从检查点或磁盘恢复；缺失时留空，
        论文手回退到原有的自主检索行为。
        """
        ledger = state.get("citation_ledger")
        if not isinstance(ledger, dict) or not ledger:
            ledger = load_citation_ledger(self.work_dir)
        from app.core.project_audit import pilot_evidence_notice

        flows.citation_brief = (
            build_citation_brief(ledger) + "\n" + pilot_evidence_notice(state)
        ).strip()

    async def _publish_progress(self, state: dict[str, Any]) -> None:
        """推送实时进度快照；失败只记日志，不阻断工作流。"""
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        try:
            message = build_progress_message(self.task_id, self.checkpoint, state)
            await task_bus.publish_message(self.task_id, message)
        except Exception as exc:
            logger.warning(f"进度推送失败: {exc}")

    async def _start_node(self, state: dict[str, Any], node_id: str) -> None:
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        self.checkpoint.start_node(state, node_id)
        await self._publish_progress(state)

    async def _complete_node(self, state: dict[str, Any], node_id: str) -> None:
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        self.checkpoint.complete_node(state, node_id)
        await self._publish_progress(state)

    def _next_step_plain(self, state: dict[str, Any], node_id: str) -> str:
        """给审批卡计算"批准后会发生什么"的大白话描述。"""
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        order = self.checkpoint.node_order(state)
        try:
            index = order.index(node_id)
        except ValueError:
            return ""
        if index + 1 >= len(order):
            return "全部步骤完成，导出论文交付"
        label, description = plain_stage(order[index + 1])
        return f"{label}（{description}）" if description else label

    async def _require_human_approval(
        self,
        state: dict[str, Any],
        node_id: str,
        *,
        summary: str,
        artifacts: list[str] | None = None,
        quality_report: dict[str, Any] | None = None,
        allow_incomplete: bool = False,
        explain: dict[str, Any] | None = None,
    ) -> None:
        """按 HIL 配置建立人工闸门。

        自动模式（HIL 关闭）下，未完成的节点必须挂起等待人工裁决，
        而不是被强制标 failed 让用户看不到下一步在哪里。
        """
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        checkpoint_key = self._hil_checkpoint_key(node_id)
        critical = "critical_review" in state.get("workflow_features", [])
        enabled = (
            (
                node_id in {"modeler", "review_results"}
                or allow_incomplete
                or (quality_report or {}).get("status") == "manual_review"
            )
            if critical
            else self._hil_enabled_for(node_id)
        )
        if node_id in state.get("approved_nodes", []):
            return None
        if not enabled:
            if allow_incomplete:
                # 不再抛 RuntimeError 把任务推入 failed：把它落到
                # ``awaiting_approval`` 并挂起 pending_approval，让用户
                # 在页面上批准/退回/续跑，保留对不完整产物的处置权。
                logger.warning(
                    f"人工审核已关闭且 {node_id}（{checkpoint_key}）质量门"
                    "未通过；挂起等待人工裁决而非作废任务"
                )
                pending = self.checkpoint.request_approval(
                    state,
                    node_id,
                    summary=summary,
                    artifacts=artifacts,
                    quality_report=quality_report,
                    allow_incomplete=True,
                    explain=explain,
                )
                raise WorkflowPausedForReview(pending)
            logger.info(f"人工审核已关闭，节点 {node_id}（{checkpoint_key}）自动继续")
            return None

        approval = self.checkpoint.request_approval(
            state,
            node_id,
            summary=summary,
            artifacts=artifacts,
            quality_report=quality_report,
            allow_incomplete=allow_incomplete,
            explain=explain,
        )
        raise WorkflowApprovalRequired(approval)

    @staticmethod
    def _hil_checkpoint_key(node_id: str) -> str:
        if node_id in {"coordinator", "analysis"}:
            return "problem_split"
        if node_id in {"modeler", "pilot"}:
            return "model_selection"
        if node_id.startswith("solve:"):
            return "code_review"
        return "paper_review"

    @classmethod
    def _hil_enabled_for(cls, node_id: str) -> bool:
        checkpoint_key = cls._hil_checkpoint_key(node_id)
        return settings.HIL_ENABLED and bool(
            settings.HIL_CHECKPOINTS.get(checkpoint_key, True)
        )

    def _resolve_pending_approval_on_resume(
        self, state: dict[str, Any]
    ) -> dict[str, Any]:
        """让关闭 HIL 后恢复的旧任务不再困在历史审核状态。

        历史 pending 来自质量门失败且 HIL 已关闭时，把任务挂回
        ``awaiting_approval`` 让用户接管，而不是抛 RuntimeError 把任务推到
        ``failed``。这条路径仅在 ``run_modeling_task_async`` 重新触发流程
        时生效；用户已经主动通过 UI 进入项目的场景由 resume_task 直接读取
        pending_approval，不会走这里。
        """
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        pending = self.checkpoint.pending_approval(state)
        if not pending:
            return state

        node_id = str(pending.get("node_id", ""))
        if "critical_review" in state.get(
            "workflow_features", []
        ) or self._hil_enabled_for(node_id):
            raise WorkflowApprovalRequired(pending)

        is_incomplete = bool(pending.get("allow_incomplete")) or node_id not in set(
            state.get("completed_nodes", [])
        )
        if is_incomplete:
            # 复用现有 pending_approval 即可，状态已经是 awaiting_approval。
            # 调用方捕获 WorkflowPausedForReview 完成页面通知与状态稳定化。
            logger.warning(
                f"{node_id} 历史审核来自未通过质量门且 HIL 已关闭；"
                "挂起等待人工裁决而非作废任务"
            )
            raise WorkflowPausedForReview(pending)

        logger.info(f"人工审核已关闭，自动释放历史审核节点 {node_id}")
        return self.checkpoint.auto_continue(
            state, str(pending.get("checkpoint_id", ""))
        )

    async def _coordinator_node(
        self,
        problem: Problem,
        state: dict[str, Any],
        coordinator_agent: CoordinatorAgent,
    ) -> CoordinatorToModeler:
        """执行或恢复协调节点。"""
        saved = state.get("coordinator_response")
        if "coordinator" in state.get("completed_nodes", []) and saved:
            response = CoordinatorToModeler.model_validate(saved)
            self.questions = response.questions
            self.ques_count = response.ques_count
            return response

        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        await self._start_node(state, "coordinator")
        opening_notice = SystemMessage(content="正在梳理题意并建立问题结构…")
        await task_bus.publish_message(self.task_id, opening_notice)
        await self._check_cancelled()
        try:
            cumulative_feedback = self.checkpoint.cumulative_revision_feedback(
                state,
                "coordinator",
            )
            requirements = problem.user_requirements
            if cumulative_feedback:
                requirements = (
                    f"{requirements}\n\n【人工审核退回意见，必须逐条落实】\n"
                    + "\n\n".join(cumulative_feedback)
                ).strip()
            previous_payload = state.get("previous_coordinator_response")
            previous_analysis = (
                CoordinatorToModeler.model_validate(previous_payload)
                if isinstance(previous_payload, dict)
                else None
            )
            response = await coordinator_agent.run(
                problem.ques_all,
                requirements,
                previous_analysis=previous_analysis,
                cumulative_feedback=cumulative_feedback,
            )
        except Exception as exc:
            logger.error(f"CoordinatorAgent 执行失败: {exc}")
            raise
        self.questions = response.questions
        self.ques_count = response.ques_count
        state["questions"] = response.questions
        state["ques_count"] = response.ques_count
        state["coordinator_response"] = response.model_dump(mode="json")
        state.pop("previous_coordinator_response", None)
        await self._complete_node(state, "coordinator")
        await task_bus.notify(
            self.task_id, "原题提取与初步结构化理解完成，开始扫描附件和文献"
        )
        return response

    async def _modeler_node(
        self,
        state: dict[str, Any],
        coordinator_response: CoordinatorToModeler,
        modeler_agent: ModelerAgent,
        model_council: ModelCouncil | None,
    ) -> ModelerToCoder:
        """执行或恢复总体建模节点。"""
        saved = state.get("modeler_response")
        if "modeler" in state.get("completed_nodes", []) and saved:
            return ModelerToCoder.model_validate(saved)

        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        await self._start_node(state, "modeler")
        await task_bus.notify(self.task_id, "建模手开始建模ing...")
        await self._check_cancelled()
        revision_feedback = self.checkpoint.consume_revision_feedback(state, "modeler")
        cumulative_feedback = self.checkpoint.cumulative_revision_feedback(
            state, "modeler"
        )
        if cumulative_feedback:
            coordinator_response = coordinator_response.model_copy(
                update={
                    "user_requirements": (
                        f"{coordinator_response.user_requirements}\n\n"
                        "【本节点历次审核意见，按时间顺序逐条落实；"
                        "只有较新意见明确改变的部分才替代旧要求】\n"
                        + "\n\n".join(cumulative_feedback)
                    ).strip()
                }
            )
        if revision_feedback:
            cache_revision = {
                "count": state.get("revision_counts", {}).get("modeler", 0),
                "feedback": revision_feedback,
            }
            if state.get("modeler_cache_revision") != cache_revision:
                for cached_key in (
                    "method_recommendations",
                    "modeler_primary_response",
                    "model_scout_proposal",
                    "model_council",
                ):
                    state.pop(cached_key, None)
                state["modeler_cache_revision"] = cache_revision
                self.checkpoint.save(state)
        method_artifacts: list[str] = []
        if (
            settings.METHOD_RETRIEVAL_ENABLED
            and coordinator_response.task_purpose != "numerical_verification"
        ):
            cached_methods = state.get("method_recommendations")
            if isinstance(cached_methods, dict) and cached_methods:
                method_recommendations = cached_methods
                MethodSelectionEngine.persist_payload(
                    method_recommendations,
                    self.work_dir,
                )
            else:
                retriever = HierarchicalMethodRetriever.from_default_library(
                    settings.METHOD_LIBRARY_PATH
                )
                selection_engine = MethodSelectionEngine(
                    retriever,
                    top_k=settings.METHOD_RETRIEVAL_TOP_K,
                )
                method_recommendations = selection_engine.select(
                    coordinator_response.questions,
                    shared_context={
                        "user_requirements": coordinator_response.user_requirements,
                        "data_profile": coordinator_response.data_profile or {},
                        "literature_brief": coordinator_response.literature_brief,
                        "analysis_summary": coordinator_response.analysis_summary,
                        "question_analyses": {
                            key: value.model_dump(mode="json")
                            for key, value in coordinator_response.question_analyses.items()
                        },
                    },
                    work_dir=self.work_dir,
                )
                state["method_recommendations"] = method_recommendations
                self.checkpoint.save(state)
            coordinator_response = coordinator_response.model_copy(
                update={"method_recommendations": method_recommendations}
            )
            method_artifacts.append(MethodSelectionEngine.artifact_name)
        council_artifacts: list[str] = []
        if model_council is None:
            response = await modeler_agent.run(coordinator_response)
        else:
            await task_bus.notify(
                self.task_id, "模型评审组已启动：主建模与独立候选探索并行进行"
            )
            cached_primary = state.get("modeler_primary_response")
            cached_scout = state.get("model_scout_proposal")
            if cached_primary and cached_scout:
                primary_response = ModelerToCoder.model_validate(cached_primary)
                scout_proposal = ModelScoutProposal.model_validate(cached_scout)
                await task_bus.notify(
                    self.task_id, "已恢复主建模与独立候选探索的中间成果，继续模型盲审"
                )
            else:
                state.pop("model_council", None)
                self.checkpoint.save(state)

                async def primary_plan():
                    if cached_primary:
                        return ModelerToCoder.model_validate(cached_primary)
                    response = await modeler_agent.run(coordinator_response)
                    state["modeler_primary_response"] = response.model_dump(mode="json")
                    self.checkpoint.save(state)
                    return response

                async def scout_plan():
                    if cached_scout:
                        return ModelScoutProposal.model_validate(cached_scout)
                    response = await model_council.propose(coordinator_response)
                    state["model_scout_proposal"] = response.model_dump(mode="json")
                    self.checkpoint.save(state)
                    return response

                planning_tasks = [
                    asyncio.create_task(primary_plan()),
                    asyncio.create_task(scout_plan()),
                ]
                planning_result = asyncio.gather(*planning_tasks)
                try:
                    primary_response, scout_proposal = await asyncio.shield(
                        planning_result
                    )
                finally:
                    for planning_task in planning_tasks:
                        if not planning_task.done():
                            planning_task.cancel()
                    cleanup = asyncio.gather(*planning_tasks, return_exceptions=True)
                    cancelled = False
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            cancelled = True
                    if not planning_result.cancelled():
                        planning_result.exception()
                    if cancelled:
                        raise asyncio.CancelledError

            cached_council = state.get("model_council")
            if cached_council:
                council_result = ModelCouncilResult.model_validate(cached_council)
                await task_bus.notify(
                    self.task_id, "已恢复模型评审组结论，继续主建模综合"
                )
            else:
                # 在请求发出前持久化预留额度。即使进程在上游响应后崩溃，
                # 同一任务续跑也不会再次自动调用 Fable。
                persisted_critic_calls = max(
                    0, int(state.get("fable_critic_calls_used", 0) or 0)
                )
                model_council.critic_calls_used = min(
                    persisted_critic_calls,
                    model_council.critic_call_limit,
                )
                if persisted_critic_calls < model_council.critic_call_limit:
                    state["fable_critic_calls_used"] = persisted_critic_calls + 1
                    self.checkpoint.save(state)
                council_review, label_map = await model_council.review(
                    coordinator=coordinator_response,
                    primary=primary_response,
                    scout=scout_proposal,
                )
                if (
                    model_council.critic_status == "fallback"
                    and "审稿超时" in model_council.fallback_reasons.values()
                ):
                    # 超时不消耗持久化预算：下次续跑仍可重试一次真正的盲审。
                    # 同时回滚 council 内存计数，否则 build_result 会把已用额度
                    # 写进 model_council 产物，被 prepare_resume 的兼容块重新
                    # 恢复成"预算已耗尽"。
                    state["fable_critic_calls_used"] = persisted_critic_calls
                    model_council.critic_calls_used = persisted_critic_calls
                council_result = model_council.build_result(
                    scout=scout_proposal,
                    review=council_review,
                    label_map=label_map,
                )
                state["model_council"] = council_result.model_dump(mode="json")
                self.checkpoint.save(state)

            council_artifacts = model_council.save_result(self.work_dir, council_result)
            response = await modeler_agent.reconcile_with_council(
                coordinator_to_modeler=coordinator_response,
                primary_plan=primary_response,
                council_result=council_result,
            )
        state["modeler_response"] = response.model_dump(mode="json")
        await self._complete_node(state, "modeler")
        plan_keys = list(response.questions_solution.keys())
        method_candidates: list[dict[str, Any]] = []
        for (
            question_key,
            methods,
        ) in coordinator_response.method_recommendations.items():
            for method in methods:
                hierarchy = " → ".join(
                    str(item) for item in method.get("hierarchy", [])
                )
                rank = method.get("rank", "-")
                summary = str(method.get("summary", ""))
                method_candidates.append(
                    {
                        "question": str(question_key),
                        "name": str(method.get("method_name", "")),
                        "role": "method_library",
                        "reason": f"Top-{rank} · {hierarchy} · {summary}"[:180],
                    }
                )
        council_candidates: list[dict[str, Any]] = []
        council_state = state.get("model_council")
        if isinstance(council_state, dict):
            scout_payload = council_state.get("scout_proposal", {})
            questions_payload = (
                scout_payload.get("questions", {})
                if isinstance(scout_payload, dict)
                else {}
            )
            for question_key, plan in questions_payload.items():
                if not isinstance(plan, dict):
                    continue
                for candidate in plan.get("candidate_models", [])[:3]:
                    if not isinstance(candidate, dict):
                        continue
                    council_candidates.append(
                        {
                            "question": str(question_key),
                            "name": str(candidate.get("name", "")),
                            "role": str(candidate.get("role", "")),
                            "reason": str(candidate.get("reason", ""))[:120],
                        }
                    )
                if len(council_candidates) >= 12:
                    break
        candidates = [*method_candidates, *council_candidates]
        await self._require_human_approval(
            state,
            "modeler",
            summary=(
                f"建模手已完成总体方案，覆盖：{'、'.join(plan_keys) or '全部小问'}。"
                + (
                    " 模型评审组已完成逐题审查；具体审稿路由、回退原因、"
                    "候选矩阵和实验设计均已写入评审产物，请重点验收。"
                    if council_artifacts
                    else ""
                )
                + (
                    f" 方法库已为每个正式小问返回 Top-{settings.METHOD_RETRIEVAL_TOP_K} 候选。"
                    if method_artifacts
                    else ""
                )
            ),
            artifacts=[*method_artifacts, *council_artifacts],
            explain={
                "what_happened": (
                    f"建模手为 {'、'.join(plan_keys) or '每个小问'} 各定了一套数学方法"
                    + ("，可供参考的方法见下方列表" if candidates else "")
                ),
                "next_step": self._next_step_plain(state, "modeler"),
                "revise_hint": (
                    "对某个候选不满意，点它旁边的“否决”会自动生成退回意见；"
                    "也可以退回本节点手写要求（例如换更简单/更可解释的方法）。"
                ),
                "candidates": candidates,
            },
        )
        return response

    async def _analysis_node(
        self,
        state: dict[str, Any],
        coordinator_response: CoordinatorToModeler,
        coordinator_agent: CoordinatorAgent,
    ) -> CoordinatorToModeler:
        """结合附件画像和文献证据校正逐题理解并提交人工验收。"""
        saved = state.get("analysis_response")
        if "analysis" in state.get("completed_nodes", []) and saved:
            refined = CoordinatorToModeler.model_validate(saved)
            if "analysis" in state.get("approved_nodes", []):
                return refined
            if self.checkpoint is None:
                raise RuntimeError("workflow checkpoint is not initialized")
            pending = self.checkpoint.pending_approval(state)
            if pending and pending.get("node_id") == "analysis":
                raise WorkflowApprovalRequired(pending)
            await self._submit_analysis_approval(state, refined)

            # 关键节点审核模式不会单独审核 analysis；续跑时复用已完成产物。
            return refined

        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        await self._start_node(state, "analysis")
        await self._check_cancelled()
        cumulative_feedback = self.checkpoint.cumulative_revision_feedback(
            state,
            "coordinator",
            "analysis",
        )
        previous_payload = state.get("previous_analysis_response")
        previous = (
            CoordinatorToModeler.model_validate(previous_payload)
            if isinstance(previous_payload, dict)
            else coordinator_response
        )
        refined = await coordinator_agent.refine_analysis(
            previous,
            data_profile=state.get("data_profile") or {},
            literature_brief=str(state.get("literature_brief", "")),
            cumulative_feedback=cumulative_feedback,
        )
        state.setdefault(
            "coordinator_response_pre_analysis",
            coordinator_response.model_dump(mode="json"),
        )
        state["coordinator_response"] = refined.model_dump(mode="json")
        state["analysis_response"] = refined.model_dump(mode="json")
        state["questions"] = refined.questions
        state["ques_count"] = refined.ques_count
        state.pop("previous_analysis_response", None)
        analysis_outcome = evaluate_analysis(state)
        outcomes = dict(state.get("node_outcomes") or {})
        outcomes["analysis"] = analysis_outcome
        state["node_outcomes"] = outcomes
        self.questions = refined.questions
        self.ques_count = refined.ques_count
        await self._complete_node(state, "analysis")
        await task_bus.notify(
            self.task_id,
            analysis_outcome["summary"],
            "success" if analysis_outcome["status"] == "completed" else "warning",
        )
        await self._submit_analysis_approval(state, refined)
        return refined

    async def _submit_analysis_approval(
        self,
        state: dict[str, Any],
        refined: CoordinatorToModeler,
    ) -> None:
        """持久化结构化题意，并按配置决定是否建立人工审批闸门。"""
        displayed = {
            key: value.model_dump(mode="json")
            for key, value in refined.question_analyses.items()
        }
        analysis_artifact = "problem_analysis.json"
        (Path(self.work_dir) / analysis_artifact).write_text(
            json.dumps(refined.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        outcome = evaluate_analysis(state)
        evidence_complete = outcome["status"] == "completed"
        await self._require_human_approval(
            state,
            "analysis",
            summary=(
                (
                    f"已生成 {len(displayed)} 个小问的结构化理解，附件与文献证据完整。"
                    if evidence_complete
                    else f"已生成 {len(displayed)} 个小问的结构化理解，但证据核验尚未完整。"
                )
                + f" {refined.analysis_summary}"
            ),
            explain={
                "what_happened": (
                    "已忠实拆题，并使用附件画像和真实文献形成数据校正版逐题理解。"
                    if evidence_complete
                    else "逐题理解已经生成，但附件画像或文献调研仍有缺口；下方明确列出，不按完成处理。"
                ),
                "question_analyses": displayed,
                "evidence_issues": outcome["issues"],
                "next_step": self._next_step_plain(state, "analysis"),
                "revise_hint": (
                    "如目标、变量、约束、依赖或验证要求仍有偏差，"
                    "请退回本节点；系统会保留上一版并累计全部意见。"
                ),
            },
            artifacts=[analysis_artifact],
            quality_report=outcome,
        )

    async def _research_node(
        self,
        state: dict[str, Any],
        coordinator_response: CoordinatorToModeler,
        research_llm: LLM,
        scholar: OpenAlexScholar,
    ) -> CoordinatorToModeler:
        """数据侦察 + 文献调研；信息性节点，失败降级，不设审批暂停。"""
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        if "research" not in self.checkpoint.node_order(state):
            # 旧任务没有该节点，保持原行为
            return coordinator_response
        if "research" in state.get("completed_nodes", []):
            return coordinator_response.model_copy(
                update={
                    "data_profile": state.get("data_profile") or None,
                    "literature_brief": str(state.get("literature_brief", "")),
                    "method_cards": build_method_cards(
                        state.get("literature_review") or {}
                    ),
                }
            )

        await self._start_node(state, "research")
        await self._check_cancelled()
        literature_enabled = (state.get("problem") or {}).get(
            "literature_enabled", True
        )
        revision_feedback = self.checkpoint.consume_revision_feedback(state, "research")
        await task_bus.notify(
            self.task_id,
            "开始数据侦察与文献调研：先摸清数据底细和领域方法，再定方案"
            if literature_enabled
            else "开始附件数据核验；本项目未启用建模前文献检索。",
        )
        await publish_activity(
            self.task_id, "正在扫描数据文件，生成数据画像…", category="info"
        )
        try:
            data_profile = await asyncio.to_thread(build_data_profile, self.work_dir)
        except Exception as exc:
            logger.warning(f"数据画像失败，降级跳过: {exc}")
            data_profile = {}

        literature_review = await run_literature_review(
            task_id=self.task_id,
            llm=research_llm,
            scholar=scholar,
            questions=coordinator_response.questions,
            work_dir=self.work_dir,
            extra_guidance=revision_feedback,
            openalex_email=settings.OPENALEX_EMAIL or "",
            enabled=literature_enabled,
        )
        literature_brief = build_literature_brief(literature_review)
        method_cards = build_method_cards(literature_review)

        state["data_profile"] = data_profile
        state["literature_review"] = literature_review
        state["literature_brief"] = literature_brief
        research_outcome = evaluate_research(state)
        outcomes = dict(state.get("node_outcomes") or {})
        outcomes["research"] = research_outcome
        state["node_outcomes"] = outcomes
        if revision_feedback:
            # research 无审批环节，意见已按要求执行完毕，就地清除
            feedback_by_node = dict(state.get("revision_feedback", {}))
            feedback_by_node.pop("research", None)
            state["revision_feedback"] = feedback_by_node
        await self._complete_node(state, "research")

        profiled_files = len(data_profile.get("files") or [])
        paper_count = int(literature_review.get("paper_count", 0) or 0)
        card_count = sum(len(items) for items in method_cards.values())
        fulltext_stats = literature_review.get("fulltext_stats") or {}
        full_read = int(fulltext_stats.get("succeeded", 0) or 0)
        await task_bus.notify(
            self.task_id,
            f"调研结果：画像 {profiled_files} 个数据文件，"
            + (
                "未启用建模前文献检索，没有新增文献证据。"
                if not literature_enabled
                else f"检索 {paper_count} 篇文献，精读 {full_read} 篇全文，"
                f"产出 {card_count} 张方法卡，已注入建模选型"
                if card_count
                else "文献调研未完成，原因已记录；不得显示为绿色完成"
            ),
            "success" if research_outcome["status"] == "completed" else "warning",
        )
        return coordinator_response.model_copy(
            update={
                "data_profile": data_profile or None,
                "literature_brief": literature_brief,
                "method_cards": method_cards,
            }
        )

    async def _initialize_interpreter(self, state: dict[str, Any]) -> None:
        """为待执行的求解节点创建新的本地代码环境。"""
        await task_bus.notify(self.task_id, "正在创建代码沙盒环境")
        notebook_serializer = NotebookSerializer(work_dir=self.work_dir)
        problem_data = state.get("problem")
        preferred_backend = (
            problem_data.get("execution_backend")
            if isinstance(problem_data, dict)
            else None
        )
        self.code_interpreter = await create_interpreter(
            kind="local",
            task_id=self.task_id,
            work_dir=self.work_dir,
            notebook_serializer=notebook_serializer,
            timeout=int(settings.MATLAB_EXECUTION_TIMEOUT_SECONDS),
            preferred_backend=preferred_backend,
        )
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        state["execution_backend"] = {
            "language": self.code_interpreter.language,
            "name": self.code_interpreter.backend_name,
        }
        self.checkpoint.save(state)
        await task_bus.notify(
            self.task_id, f"计算环境创建完成：{self.code_interpreter.backend_name}"
        )

    def _new_coder_agent(self, coder_llm: LLM, problem: Problem) -> CoderAgent:
        """Bind the active interpreter to one solver session."""
        if self.code_interpreter is None:
            raise RuntimeError("code interpreter is not initialized")
        return CoderAgent(
            problem.task_id,
            coder_llm,
            self.work_dir,
            code_interpreter=self.code_interpreter,
            cancel_event=self.cancel_event,
            context_window=settings.CODER_CONTEXT_WINDOW,
            max_retries=settings.MAX_RETRIES,
            max_chat_turns=settings.MAX_CHAT_TURNS,
            max_code_executions=settings.MAX_CODE_EXECUTIONS_PER_RUN,
            task_purpose=problem.task_purpose,
        )

    def _restore_user_output(self, state: dict[str, Any]) -> UserOutput:
        """把已完成节点的写作产物恢复到论文聚合器。"""
        user_output = UserOutput(
            work_dir=self.work_dir,
            ques_count=int(state.get("ques_count", self.ques_count)),
        )
        for key, result in state.get("solution_results", {}).items():
            if isinstance(result, dict) and result.get("writer_response"):
                user_output.set_res(
                    key,
                    WriterResponse.model_validate(result["writer_response"]),
                )
        for key, result in state.get("write_results", {}).items():
            user_output.set_res(key, WriterResponse.model_validate(result))
        return user_output
