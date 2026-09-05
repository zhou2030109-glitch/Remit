"""工作流模块，编排多 Agent 协作并提供节点级检查点续跑。"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from app.config.setting import settings
from app.core.agents.coder_agent import CoderAgent
from app.core.agents.coordinator_agent import CoordinatorAgent
from app.core.agents.modeler_agent import ModelerAgent
from app.core.agents.writer_agent import WriterAgent
from app.core.deliverable_contract import (
    DeliverableValidationReport,
    DeliverableValidationError,
    ModelQualityValidationError,
    QuestionDeliverableContract,
    build_question_contract,
    build_repair_prompt,
    collect_grounding_values,
    collect_model_quality_evidence,
    find_reusable_stage_artifacts,
    validate_final_paper,
    validate_question_deliverables,
    validate_writer_section,
)
from app.core.flows import Flows
from app.core.execution_summary import (
    build_execution_summary_message,
    snapshot_code_files,
)
from app.core.llm.llm import LLM
from app.core.llm.llm_factory import LLMFactory
from app.core.activity import publish_activity
from app.core.citations import (
    FINAL_CITATIONS_FILENAME,
    build_citation_brief,
    build_citation_ledger,
    build_citation_table,
    load_citation_ledger,
    persist_citation_ledger,
)
from app.core.data_scout import build_data_profile, summarize_data_profile
from app.core.literature import (
    METHOD_CARDS_FILENAME,
    build_literature_brief,
    build_method_cards,
    run_literature_review,
)
from app.core.method_retrieval import (
    HierarchicalMethodRetriever,
    MethodSelectionEngine,
)
from app.core.model_council import ModelCouncil
from app.core.paper_judge import (
    build_review_explain_numbers,
    judge_paper,
    save_review,
)
from app.core.paper_quality import audit_paper_style
from app.core.pilot import (
    PILOT_RESULTS_FILENAME,
    PilotValidationError,
    build_pilot_coder_prompt,
    build_pilot_table,
    validate_pilot_results,
)
from app.core.progress import build_progress_message, plain_stage
from app.core.project_audit import evaluate_analysis, evaluate_research
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.models.user_output import UserOutput
from app.schemas.A2A import (
    CoderToWriter,
    CoordinatorToModeler,
    ModelCouncilResult,
    ModelExecutionReview,
    ModelScoutProposal,
    ModelerToCoder,
    WriterResponse,
)
from app.schemas.request import Problem
from app.schemas.enums import CompTemplate
from app.schemas.response import SystemMessage
from app.services.redis_manager import redis_manager
from app.tools.base_interpreter import BaseCodeInterpreter
from app.tools.interpreter_factory import create_interpreter
from app.tools.notebook_serializer import NotebookSerializer
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.common_utils import create_work_dir, get_config_template
from app.utils.log_util import logger
from app.utils.paper_polish import (
    PaperRenderError,
    polish_markdown,
    render_paper_deliverables,
)


class WorkFlow:
    """工作流基类。"""

    def execute(self) -> None:
        """执行工作流。"""


class WorkflowApprovalRequired(RuntimeError):
    """节点成果已落盘，工作流必须等待人工明确批准。"""

    def __init__(self, approval: dict[str, Any]):
        self.approval = approval
        super().__init__(f"等待人工审核：{approval.get('node_label', '')}")


def _build_manual_execution_review(
    *,
    key: str,
    contract: QuestionDeliverableContract,
    gate_report: DeliverableValidationReport,
    quality_report: dict[str, Any],
) -> ModelExecutionReview:
    """把人工复核质量报告转换为不会丢失分问模型的写作约束。"""
    selected_value = quality_report.get("selected_model", "")
    selected_models: list[tuple[str, str]] = []
    if isinstance(selected_value, dict):
        selected_models = [
            (str(question).strip(), str(model).strip())
            for question, model in selected_value.items()
            if str(question).strip() and str(model).strip()
        ]
        selected_model = "；".join(
            f"{question}={model}" for question, model in selected_models
        )
    else:
        selected_model = str(selected_value).strip()

    review_reason = gate_report.manual_review_reason or "质量报告要求人工复核"
    evidence = [
        f"{contract.quality_filename} status=manual_review",
        f"人工复核原因：{review_reason}",
    ]
    strengths = ["真实产物已落盘且通过结构与文件完整性检查"]
    writer_guidance = (
        "如需形成阶段说明，只能如实报告冲突、支持范围和局限，不得写成模型已经通过。"
    )

    if selected_models:
        model_context = f"各问入选或回退模型：{selected_model}"
        evidence.append(model_context)
        strengths.append(model_context)
        writer_guidance += (
            f"质量报告给出了分问模型映射，必须分别报告以下入选或回退模型："
            f"{selected_model}。必须把这些模型与失败门禁涉及的被拒绝候选明确"
            "区分，不得把某一问候选失败误写成全部问题只能使用同一个基线。"
        )
        type_specific = quality_report.get("type_specific", {})
        if isinstance(type_specific, dict):
            parameters_tested = type_specific.get("parameters_tested")
            scenarios = type_specific.get("scenarios")
            if parameters_tested is not None and scenarios is not None:
                count_context = (
                    f"敏感性分析覆盖 {parameters_tested} 个参数、"
                    f"{scenarios} 个输入扰动场景"
                )
                evidence.append(count_context)
                writer_guidance += f"正文还必须如实报告：{count_context}。"
    elif (
        selected_model
        and gate_report.primary_metric_name
        and gate_report.model_value is not None
        and gate_report.baseline_value is not None
    ):
        strengths.append(
            f"入选模型 {selected_model} 的 {gate_report.primary_metric_name}="
            f"{gate_report.model_value}，同折基线={gate_report.baseline_value}"
        )
        writer_guidance += (
            "manual_review 不等于没有入选模型；"
            f"必须报告入选模型 {selected_model} 及其"
            f" {gate_report.primary_metric_name}={gate_report.model_value}，"
            f"同折基线={gate_report.baseline_value}，并把它与失败检查涉及的"
            "其他候选模型明确区分；若入选模型优于基线，不得写成只能保留基线。"
        )

    return ModelExecutionReview(
        verdict="manual_review",
        summary=(
            f"{key} 的结构、产物和证据已通过完整性校验，但检测到真实的"
            f"敏感性冲突，需要人工决定后续处理：{review_reason}"
        ),
        evidence=evidence,
        strengths=strengths,
        weaknesses=["关键稳健性检查存在冲突，禁止自动包装为通过"],
        writer_guidance=writer_guidance,
    )


class RemitWorkFlow(WorkFlow):
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
        await self._publish_progress(state)

        llm_factory = LLMFactory(self.task_id)
        coordinator_llm, modeler_llm, coder_llm, writer_llm = llm_factory.get_all_llms()
        model_council: ModelCouncil | None = None
        if settings.MODEL_COUNCIL_ENABLED:
            scout_llm, critic_llm = llm_factory.get_model_council_llms()
            if (
                settings.MODEL_COUNCIL_REQUIRE_DIVERSE_BACKENDS
                and not ModelCouncil.reviewers_are_independent(
                    modeler_llm,
                    scout_llm,
                    critic_llm,
                )
            ):
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=(
                            "模型评审组与主建模手使用完全相同的模型接入点，"
                            "无法提供独立性或故障隔离，本次已自动跳过额外评审调用。"
                        ),
                        type="warning",
                    ),
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
        )
        self._refresh_citation_brief(flows, state)
        config_template = get_config_template(problem.comp_template)

        writer_agent = self._new_writer_agent(writer_llm, problem, scholar)

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
            assert self.code_interpreter is not None
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
        flows.citation_brief = build_citation_brief(ledger)

    async def _publish_progress(self, state: dict[str, Any]) -> None:
        """推送实时进度快照；失败只记日志，不阻断工作流。"""
        assert self.checkpoint is not None
        try:
            message = build_progress_message(self.task_id, self.checkpoint, state)
            await redis_manager.publish_message(self.task_id, message)
        except Exception as exc:
            logger.warning(f"进度推送失败: {exc}")

    async def _start_node(self, state: dict[str, Any], node_id: str) -> None:
        assert self.checkpoint is not None
        self.checkpoint.start_node(state, node_id)
        await self._publish_progress(state)

    async def _complete_node(self, state: dict[str, Any], node_id: str) -> None:
        assert self.checkpoint is not None
        self.checkpoint.complete_node(state, node_id)
        await self._publish_progress(state)

    def _next_step_plain(self, state: dict[str, Any], node_id: str) -> str:
        """给审批卡计算"批准后会发生什么"的大白话描述。"""
        assert self.checkpoint is not None
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
        """按 HIL 配置建立人工闸门；自动模式绝不放行不完整产物。"""
        assert self.checkpoint is not None
        checkpoint_key = self._hil_checkpoint_key(node_id)
        if not self._hil_enabled_for(node_id):
            if allow_incomplete:
                raise RuntimeError(
                    f"{node_id} 自动质量门未通过，且人工审核已关闭；"
                    "任务已明确失败，不会把不完整产物自动放行。"
                )
            logger.info(
                f"人工审核已关闭，节点 {node_id}（{checkpoint_key}）自动继续"
            )
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
        """让关闭 HIL 后恢复的旧任务不再困在历史审核状态。"""
        assert self.checkpoint is not None
        pending = self.checkpoint.pending_approval(state)
        if not pending:
            return state

        node_id = str(pending.get("node_id", ""))
        if self._hil_enabled_for(node_id):
            raise WorkflowApprovalRequired(pending)

        is_incomplete = bool(pending.get("allow_incomplete")) or node_id not in set(
            state.get("completed_nodes", [])
        )
        if is_incomplete:
            raise RuntimeError(
                f"{node_id} 的历史审核来自未通过的质量门，且人工审核已关闭；"
                "任务已明确失败，不会把不完整产物自动放行。"
            )

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

        assert self.checkpoint is not None
        await self._start_node(state, "coordinator")
        opening_notice = SystemMessage(content="正在梳理题意并建立问题结构…")
        await redis_manager.publish_message(self.task_id, opening_notice)
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
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="原题提取与初步结构化理解完成，开始扫描附件和文献"),
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

        assert self.checkpoint is not None
        await self._start_node(state, "modeler")
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="建模手开始建模ing..."),
        )
        await self._check_cancelled()
        revision_feedback = self.checkpoint.consume_revision_feedback(state, "modeler")
        if revision_feedback:
            coordinator_response = coordinator_response.model_copy(
                update={
                    "user_requirements": (
                        f"{coordinator_response.user_requirements}\n\n"
                        "【人工审核退回意见，必须重做总体建模方案并逐条落实】\n"
                        f"{revision_feedback}"
                    ).strip()
                }
            )
        method_artifacts: list[str] = []
        if settings.METHOD_RETRIEVAL_ENABLED:
            cached_methods = (
                None if revision_feedback else state.get("method_recommendations")
            )
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
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content="模型评审组已启动：主建模与独立候选探索并行进行"),
            )
            cached_primary = (
                None if revision_feedback else state.get("modeler_primary_response")
            )
            cached_scout = (
                None if revision_feedback else state.get("model_scout_proposal")
            )
            if cached_primary and cached_scout:
                primary_response = ModelerToCoder.model_validate(cached_primary)
                scout_proposal = ModelScoutProposal.model_validate(cached_scout)
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content="已恢复主建模与独立候选探索的中间成果，继续模型盲审"
                    ),
                )
            else:
                primary_task = asyncio.create_task(
                    modeler_agent.run(coordinator_response)
                )
                scout_task = asyncio.create_task(
                    model_council.propose(coordinator_response)
                )
                primary_response, scout_proposal = await asyncio.gather(
                    primary_task, scout_task
                )
                state["modeler_primary_response"] = primary_response.model_dump(
                    mode="json"
                )
                state["model_scout_proposal"] = scout_proposal.model_dump(mode="json")
                state.pop("model_council", None)
                self.checkpoint.save(state)

            cached_council = None if revision_feedback else state.get("model_council")
            if cached_council:
                council_result = ModelCouncilResult.model_validate(cached_council)
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(content="已恢复模型评审组结论，继续主建模综合"),
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
                    + ("，评审组还提出了备选候选（见下方列表）" if candidates else "")
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
            assert self.checkpoint is not None
            pending = self.checkpoint.pending_approval(state)
            if pending and pending.get("node_id") == "analysis":
                raise WorkflowApprovalRequired(pending)
            await self._submit_analysis_approval(state, refined)

        assert self.checkpoint is not None
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
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=analysis_outcome["summary"],
                type=(
                    "success"
                    if analysis_outcome["status"] == "completed"
                    else "warning"
                ),
            ),
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
        assert self.checkpoint is not None
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
        revision_feedback = self.checkpoint.consume_revision_feedback(state, "research")
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content="开始数据侦察与文献调研：先摸清数据底细和领域方法，再定方案"
            ),
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
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=(
                    f"调研结果：画像 {profiled_files} 个数据文件，"
                    + (
                        f"检索 {paper_count} 篇文献，精读 {full_read} 篇全文，"
                        f"产出 {card_count} 张方法卡，已注入建模选型"
                        if card_count
                        else "文献调研未完成，原因已记录；不得显示为绿色完成"
                    )
                ),
                type=(
                    "success"
                    if research_outcome["status"] == "completed"
                    else "warning"
                ),
            ),
        )
        return coordinator_response.model_copy(
            update={
                "data_profile": data_profile or None,
                "literature_brief": literature_brief,
                "method_cards": method_cards,
            }
        )

    async def _pilot_node(
        self,
        state: dict[str, Any],
        modeler_agent: ModelerAgent,
        coder_agent: CoderAgent,
        modeler_response: ModelerToCoder,
    ) -> None:
        """探索实验：候选方案小样本 PK → 数据定案 → 知情审批。

        成功后把定案方案写回 state["modeler_response"]；开启审核时暂停，
        自动模式由 execute 立即加载新方案。降级跳过时沿用原方案。
        """
        assert self.checkpoint is not None
        assert self.code_interpreter is not None
        node_id = "pilot"
        await self._start_node(state, node_id)
        await self._check_cancelled()
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="探索实验开始：候选方案先在小样本上真实 PK"),
        )
        revision_feedback = self.checkpoint.consume_revision_feedback(state, node_id)
        literature_brief = str(state.get("literature_brief", ""))
        if revision_feedback:
            literature_brief = (
                f"{literature_brief}\n【人工审核退回意见，必须逐条落实】\n"
                f"{revision_feedback}"
            ).strip()
        method_cards = build_method_cards(state.get("literature_review") or {})
        try:
            profile_summary = summarize_data_profile(state.get("data_profile") or {})
            await publish_activity(
                self.task_id,
                "建模手正在根据文献方法卡生成候选方案…",
                category="llm",
            )
            plan = await modeler_agent.design_pilot_plan(
                questions=self.questions,
                questions_solution=modeler_response.questions_solution,
                literature_brief=literature_brief,
                data_profile_summary=profile_summary,
                backend_language=self.code_interpreter.language,
                method_cards=method_cards,
            )
            state["pilot_plan"] = plan.model_dump(mode="json")
            self.checkpoint.save(state)

            # 删除上一轮残留：校验器读磁盘文件，旧结果会冒充新实验
            (Path(self.work_dir) / PILOT_RESULTS_FILENAME).unlink(missing_ok=True)
            feedback_suffix = (
                f"\n\n【人工审核退回意见，必须逐条落实】\n{revision_feedback}"
                if revision_feedback
                else ""
            )
            pilot_prompt = build_pilot_coder_prompt(plan) + feedback_suffix
            results: dict[str, Any] | None = None
            for pilot_attempt in range(1, 3):
                await coder_agent.run(prompt=pilot_prompt, subtask_title="pilot")
                try:
                    results = validate_pilot_results(self.work_dir, plan)
                    break
                except PilotValidationError as error:
                    if pilot_attempt >= 2:
                        raise
                    await redis_manager.publish_message(
                        self.task_id,
                        SystemMessage(
                            content=f"探索实验产物未通过校验，返修一次: {error}",
                            type="warning",
                        ),
                    )
                    pilot_prompt = (
                        f"上一轮探索实验产物未通过校验：{error}\n"
                        f"请修复并重写 {PILOT_RESULTS_FILENAME}；"
                        "已跑通候选的真实结果可直接复用，不要重复计算。\n"
                        + build_pilot_coder_prompt(plan)
                        + feedback_suffix
                    )
            assert results is not None

            await publish_activity(
                self.task_id, "建模手正在基于小实验数据定案…", category="llm"
            )
            decision = await modeler_agent.finalize_with_pilot(
                questions=self.questions,
                questions_solution=modeler_response.questions_solution,
                pilot_results=results,
                literature_brief=literature_brief,
                pilot_plan=plan,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 探索实验是增强环节：失败降级按原方案继续，绝不阻塞主流程
            logger.error(f"探索实验降级跳过: {exc}")
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(
                    content=(f"探索实验未能完成，已降级跳过，按原方案继续求解：{exc}"),
                    type="warning",
                ),
            )
            state["pilot_skipped"] = str(exc)
            # 同一代码手实例将继续正式求解：撤销探索阶段的产物限制指令
            try:
                await coder_agent.append_chat_history(
                    {
                        "role": "user",
                        "content": (
                            "探索实验阶段已结束。此前“不要生成正式交付产物”的"
                            "限制全部作废，后续按正式求解契约执行。"
                        ),
                    }
                )
            except Exception as reset_error:
                logger.warning(f"重置代码手探索上下文失败: {reset_error}")
            await self._complete_node(state, node_id)
            return

        updated_solution = dict(modeler_response.questions_solution)
        for key, item in decision.questions.items():
            if key not in updated_solution or not key.startswith("ques"):
                # 定案只允许覆盖正式小问，防止误改 eda/敏感性方案
                continue
            updated_solution[key] = item.revised_strategy
        updated_response = ModelerToCoder(questions_solution=updated_solution)
        ledger = build_citation_ledger(
            method_cards=method_cards, plan=plan, decision=decision
        )
        persist_citation_ledger(self.work_dir, ledger)
        state["pilot_results"] = results
        state["pilot_decision"] = decision.model_dump(mode="json")
        state["citation_ledger"] = ledger
        # 备份定案前方案：退回 pilot 重跑时恢复真正的"原方案"
        state["modeler_response_pre_pilot"] = modeler_response.model_dump(mode="json")
        state["modeler_response"] = updated_response.model_dump(mode="json")
        await self._complete_node(state, node_id)

        selected_lines = "；".join(
            f"{key}→{item.selected_model}" for key, item in decision.questions.items()
        )
        used_count = int(ledger.get("used_count", 0) or 0)
        judged_count = int(ledger.get("judged_count", 0) or 0)
        citation_note = (
            f" 参考的 {judged_count} 篇文献经代码验证后保留 {used_count} 篇，"
            "只有这些会进论文参考文献。"
            if judged_count
            else ""
        )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=(
                    f"探索实验完成，已按真实数据定案：{selected_lines}。{citation_note}"
                ),
                type="success",
            ),
        )
        artifacts = [PILOT_RESULTS_FILENAME]
        if judged_count:
            artifacts.extend([METHOD_CARDS_FILENAME, FINAL_CITATIONS_FILENAME])
        await self._require_human_approval(
            state,
            node_id,
            summary=(
                "探索实验完成：各候选方案已在小样本、相同数据划分下真实比较，"
                f"并按数据选出每问的正式方案：{selected_lines}。{citation_note}"
            ),
            artifacts=artifacts,
            explain={
                "what_happened": (
                    "每个小问的候选方案先在小样本上真实跑了一遍（相同数据划分公平比较），"
                    "下表是真实结果；已按数据表现选出每问的正式方案"
                    + (
                        "，并逐篇裁决了参考文献是采用、修改还是放弃。"
                        if judged_count
                        else "。"
                    )
                ),
                "next_step": "批准后按入选方案进行正式全量求解",
                "revise_hint": (
                    "对某问的选择不满意，退回本节点并说明方向"
                    "（例如换候选、改抽样规则），会重新做小实验。"
                ),
                "pilot_table": build_pilot_table(results, decision),
                "citation_table": build_citation_table(ledger) if judged_count else {},
                "candidates": [
                    {
                        "question": key,
                        "name": item.selected_model,
                        "role": "selected",
                        "reason": item.justification[:120],
                    }
                    for key, item in decision.questions.items()
                ],
            },
        )

    async def _initialize_interpreter(self, state: dict[str, Any]) -> None:
        """为待执行的求解节点创建新的本地代码环境。"""
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="正在创建代码沙盒环境"),
        )
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
        assert self.checkpoint is not None
        state["execution_backend"] = {
            "language": self.code_interpreter.language,
            "name": self.code_interpreter.backend_name,
        }
        self.checkpoint.save(state)
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=f"计算环境创建完成：{self.code_interpreter.backend_name}"
            ),
        )

    async def _solution_node(
        self,
        *,
        key: str,
        value: dict[str, Any],
        state: dict[str, Any],
        flows: Flows,
        config_template: dict,
        modeler_agent: ModelerAgent,
        coder_agent: CoderAgent,
        writer_agent: WriterAgent,
        user_output: UserOutput,
    ) -> None:
        """执行一个求解节点，并在代码与写作门禁都通过后提交检查点。"""
        assert self.checkpoint is not None
        assert self.code_interpreter is not None
        node_id = f"solve:{key}"
        await self._start_node(state, node_id)
        await self._check_cancelled()
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content=f"代码手开始求解{key}"),
        )

        code_files_before = snapshot_code_files(self.work_dir)
        coder_prompt = value["coder_prompt"]
        question_text = str(value.get("question_text", key))
        model_plan = str(value.get("model_plan", ""))
        revision_feedback = self.checkpoint.consume_revision_feedback(state, node_id)
        if revision_feedback:
            coder_prompt = (
                f"{coder_prompt}\n\n【人工审核退回意见，必须重新运行并逐条落实】\n"
                f"{revision_feedback}"
            )
        contract = value.get("contract")
        recovered_gate_report = None
        if contract is not None and find_reusable_stage_artifacts(
            self.work_dir, contract
        ):
            try:
                recovered_report = validate_question_deliverables(
                    self.work_dir, contract
                )
                recovered_gate_report = recovered_report
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=(
                            f"检测到 {key} 已有完整"
                            + (
                                "人工复核"
                                if recovered_report.manual_review_required
                                else "且已通过质量门禁的"
                            )
                            + "产物，本次续跑直接恢复证据，不再调用代码手重复计算"
                        ),
                        type=(
                            "warning"
                            if recovered_report.manual_review_required
                            else "success"
                        ),
                    ),
                )
            except DeliverableValidationError as interrupted_error:
                coder_prompt = (
                    f"{coder_prompt}\n\n"
                    "【检测到上次中断留下的真实计算产物，优先执行收尾恢复】\n"
                    f"{build_repair_prompt(contract, interrupted_error, self.work_dir)}"
                )
        coder_response: CoderToWriter | None = None
        gate_report = None
        execution_review: ModelExecutionReview | None = None
        final_evidence: dict[str, Any] = {}
        recovered_requires_fresh_review = False
        history_by_key = state.setdefault("model_revision_history", {})
        revision_history = history_by_key.setdefault(key, [])
        reviews_by_key = state.setdefault("model_execution_reviews", {})
        execution_reviews = reviews_by_key.setdefault(key, [])
        if (
            recovered_gate_report is not None
            and revision_history
            and key not in state.get("solution_results", {})
        ):
            pending_revision = revision_history[-1]
            revision_plan = pending_revision.get("revision_plan", {})
            revised_model = (
                str(revision_plan.get("selected_model", "")).strip()
                if isinstance(revision_plan, dict)
                else ""
            )
            recovered_evidence = collect_model_quality_evidence(
                self.work_dir,
                contract,
            )
            recovered_quality = recovered_evidence.get("quality_report", {})
            recovered_model = (
                str(recovered_quality.get("selected_model", "")).strip()
                if isinstance(recovered_quality, dict)
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
            revision_not_materialized = (
                pending_revision.get("trigger") == "modeler_review"
                and revised_normalized
                and revised_normalized not in recovered_normalized
            )
            if revision_not_materialized:
                previous_plan = str(pending_revision.get("previous_plan", "")).strip()
                if previous_plan:
                    # 恢复的旧产物虽通过确定性门禁，但对应的建模手审核曾要求换模；
                    # 未落地的换模被回滚后，必须重新审核旧方案，不能静默自动接收。
                    recovered_requires_fresh_review = True
                    model_plan = previous_plan
                    revision_history.pop()
                    if execution_reviews:
                        execution_reviews.pop()
                    saved_modeler = state.get("modeler_response")
                    if isinstance(saved_modeler, dict):
                        saved_solutions = dict(
                            saved_modeler.get("questions_solution", {})
                        )
                        saved_solutions[key] = previous_plan
                        saved_modeler["questions_solution"] = saved_solutions
                    self.checkpoint.save(state)
                    await redis_manager.publish_message(
                        self.task_id,
                        SystemMessage(
                            content=(
                                f"检测到 {key} 的换模计划尚未生成对应产物，"
                                "已回退到现有通过门禁的模型证据重新复核"
                            ),
                            type="warning",
                        ),
                    )
        for gate_attempt in range(1, 4):
            assert contract is not None, f"{key} 缺少强制质量契约"
            if gate_attempt == 1 and recovered_gate_report is not None:
                coder_response = CoderToWriter(
                    code_response=(
                        f"已从 {contract.quality_filename} 恢复真实执行产物；"
                        + (
                            "该阶段触发人工审核，"
                            if recovered_gate_report.manual_review_required
                            else "现有产物已通过质量门禁，"
                        )
                        + "不重复运行代码。"
                    )
                )
                gate_report = recovered_gate_report
            else:
                coder_response = await coder_agent.run(
                    prompt=coder_prompt,
                    subtask_title=key,
                )
                await publish_activity(
                    self.task_id,
                    f"正在校验 {key} 的交付质量（不看运气，逐项检查产物）…",
                    category="gate",
                )
                try:
                    gate_report = validate_question_deliverables(
                        self.work_dir, contract
                    )
                except DeliverableValidationError as error:
                    if gate_attempt >= 3:
                        # 三连败不再作废任务：保留全部产物，挂起等待人工裁决。
                        await redis_manager.publish_message(
                            self.task_id,
                            SystemMessage(
                                content=(
                                    f"{key} 连续 3 次未通过交付质量门禁，"
                                    f"任务已挂起等待人工处理：{error}"
                                ),
                                type="error",
                            ),
                        )
                        await self._require_human_approval(
                            state,
                            node_id,
                            summary=(
                                f"{key} 连续 3 次未通过交付质量门禁，已保留全部真实产物"
                                f"并暂停任务。最后一次失败原因：{error}\n"
                                "批准后将携带失败上下文重新求解本节点；"
                                "退回本节点并附意见可指导下一轮修复方向。"
                            ),
                            allow_incomplete=True,
                            explain={
                                "what_happened": (
                                    f"{key} 连续 3 次没通过质量检查，已暂停。"
                                    f"最后一次的原因：{error}"
                                ),
                                "next_step": (
                                    "批准 = 带着失败上下文重试本节点；"
                                    "退回 = 按你的意见修复后再跑"
                                ),
                                "revise_hint": (
                                    "如果你能看出问题所在（数据、方法或门槛太严），"
                                    "退回时写一句就能少走很多弯路。"
                                ),
                            },
                        )
                    if isinstance(error, ModelQualityValidationError):
                        evidence = collect_model_quality_evidence(
                            self.work_dir,
                            contract,
                        )
                        rejected_models = [
                            str(item.get("failed_model", ""))
                            for item in revision_history
                            if isinstance(item, dict)
                        ]
                        quality_report = evidence.get("quality_report", {})
                        failed_model = (
                            str(quality_report.get("selected_model", "")).strip()
                            if isinstance(quality_report, dict)
                            else ""
                        )
                        if failed_model and failed_model not in rejected_models:
                            rejected_models.append(failed_model)
                        await redis_manager.publish_message(
                            self.task_id,
                            SystemMessage(
                                content=(
                                    f"{key} 样本外性能、可行性或稳定性未达标，"
                                    f"已退回建模手更换模型 ({gate_attempt}/3): {error}"
                                ),
                                type="warning",
                            ),
                        )
                        revision_plan = await modeler_agent.revise_after_execution(
                            question_key=key,
                            question_text=question_text,
                            original_plan=model_plan,
                            gate_error=str(error),
                            evidence=evidence,
                            rejected_models=rejected_models,
                        )
                        revision_history.append(
                            {
                                "attempt": gate_attempt,
                                "trigger": "quality_gate",
                                "gate_error": str(error),
                                "execution_evidence": evidence,
                                "failed_model": failed_model,
                                "previous_plan": model_plan,
                                "revision_plan": revision_plan.model_dump(mode="json"),
                            }
                        )
                        saved_modeler = state.get("modeler_response")
                        if isinstance(saved_modeler, dict):
                            saved_solutions = dict(
                                saved_modeler.get("questions_solution", {})
                            )
                            saved_solutions[key] = revision_plan.revised_strategy
                            saved_modeler["questions_solution"] = saved_solutions
                        self.checkpoint.save(state)
                        model_plan = revision_plan.revised_strategy
                        coder_prompt = self._build_model_revision_coder_prompt(
                            question_text=question_text,
                            gate_error=str(error),
                            revision_plan=revision_plan.model_dump(mode="json"),
                            contract_prompt=contract.prompt_block(),
                        )
                        await redis_manager.publish_message(
                            self.task_id,
                            SystemMessage(
                                content=(
                                    f"建模手已切换为 {revision_plan.selected_model}，"
                                    f"代码手将按原门槛重新运行并验证 {key}"
                                ),
                            ),
                        )
                    else:
                        await redis_manager.publish_message(
                            self.task_id,
                            SystemMessage(
                                content=(
                                    f"{key} 产物或验证格式未通过，代码手自动返修 "
                                    f"({gate_attempt}/3): {error}"
                                ),
                                type="warning",
                            ),
                        )
                        coder_prompt = build_repair_prompt(
                            contract, error, self.work_dir
                        )
                    continue

            if gate_report.manual_review_required:
                final_evidence = collect_model_quality_evidence(
                    self.work_dir,
                    contract,
                )
                manual_quality = final_evidence.get("quality_report", {})
                execution_review = _build_manual_execution_review(
                    key=key,
                    contract=contract,
                    gate_report=gate_report,
                    quality_report=(
                        manual_quality if isinstance(manual_quality, dict) else {}
                    ),
                )
                execution_reviews.append(
                    {
                        "attempt": gate_attempt,
                        "execution_evidence": final_evidence,
                        "review": execution_review.model_dump(mode="json"),
                    }
                )
                self.checkpoint.save(state)
                break

            final_evidence = collect_model_quality_evidence(
                self.work_dir,
                contract,
            )
            quality_report_for_review = final_evidence.get("quality_report", {})
            current_model = (
                str(quality_report_for_review.get("selected_model", "")).strip()
                if isinstance(quality_report_for_review, dict)
                else ""
            )
            rejected_models = [
                str(item.get("failed_model", "")).strip()
                for item in revision_history
                if isinstance(item, dict) and str(item.get("failed_model", "")).strip()
            ]
            review_rejected_models = list(dict.fromkeys(rejected_models))
            if (
                gate_attempt == 1
                and recovered_gate_report is not None
                and not recovered_requires_fresh_review
            ):
                recovered_limitations = (
                    quality_report_for_review.get("limitations", [])
                    if isinstance(quality_report_for_review, dict)
                    else []
                )
                execution_review = ModelExecutionReview(
                    verdict="accept",
                    summary=(
                        f"{key} 从持久化检查点恢复的产物已通过确定性质量门禁。"
                        "续跑仅恢复已验收证据，不在看到结果后新增验收约束或重复执行计算。"
                    ),
                    evidence=[
                        f"{contract.quality_filename} status="
                        f"{quality_report_for_review.get('status', 'pass')}",
                        f"selected_model={current_model or 'reported_in_quality_file'}",
                        "recovered_artifacts_passed_validate_question_deliverables",
                    ],
                    strengths=[
                        "持久化产物已通过结构、泄漏、可行性和稳健性门禁。",
                        "续跑保留冻结的验证口径和可复现结果。",
                    ],
                    weaknesses=[str(item) for item in recovered_limitations],
                    writer_guidance=(
                        "仅使用质量报告和落盘产物中可核对的数值，"
                        "完整报告局限；不得采纳续跑时临时新增且未执行的后验分析。"
                    ),
                    revision_plan=None,
                )
            else:
                execution_review = await modeler_agent.review_execution_result(
                    question_key=key,
                    question_text=question_text,
                    current_plan=model_plan,
                    evidence=final_evidence,
                    rejected_models=review_rejected_models,
                    remaining_runs=3 - gate_attempt,
                )
            execution_reviews.append(
                {
                    "attempt": gate_attempt,
                    "execution_evidence": final_evidence,
                    "review": execution_review.model_dump(mode="json"),
                }
            )

            if execution_review.verdict == "refine":
                revision_plan = execution_review.revision_plan
                assert revision_plan is not None
                revision_history.append(
                    {
                        "attempt": gate_attempt,
                        "trigger": "modeler_review",
                        "gate_error": execution_review.summary,
                        "execution_evidence": final_evidence,
                        "failed_model": current_model,
                        "previous_plan": model_plan,
                        "revision_plan": revision_plan.model_dump(mode="json"),
                    }
                )
                saved_modeler = state.get("modeler_response")
                if isinstance(saved_modeler, dict):
                    saved_solutions = dict(saved_modeler.get("questions_solution", {}))
                    saved_solutions[key] = revision_plan.revised_strategy
                    saved_modeler["questions_solution"] = saved_solutions
                self.checkpoint.save(state)
                model_plan = revision_plan.revised_strategy
                coder_prompt = self._build_model_revision_coder_prompt(
                    question_text=question_text,
                    gate_error=f"建模手结果复核要求继续改进：{execution_review.summary}",
                    revision_plan=revision_plan.model_dump(mode="json"),
                    contract_prompt=contract.prompt_block(),
                )
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=(
                            f"建模手复核 {key} 后认为仍可改进，已改用 "
                            f"{revision_plan.selected_model} 重新运行 ({gate_attempt + 1}/3)"
                        ),
                        type="warning",
                    ),
                )
                continue

            self.checkpoint.save(state)
            break

        assert coder_response is not None
        assert gate_report is not None
        assert execution_review is not None
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=(
                    f"{key} 代码结果已通过质量门禁和建模手复核"
                    if execution_review.verdict == "accept"
                    else (
                        f"{key} 证据完整性已通过，但关键稳健性检查存在冲突，"
                        "已停止自动返工并转人工审核"
                    )
                ),
                type=("success" if execution_review.verdict == "accept" else "warning"),
            ),
        )
        quality_path = Path(self.work_dir) / contract.quality_filename
        quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
        artifacts = [
            str(item)
            for field in ("artifacts", "paper_ready_images")
            for item in quality_report.get(field, [])
        ]
        paper_images = list(gate_report.paper_ready_images)
        execution_summary = build_execution_summary_message(
            task_id=self.task_id,
            node_id=node_id,
            node_label=self.checkpoint.node_label(node_id, state),
            section=key,
            work_dir=self.work_dir,
            evidence=final_evidence,
            review=execution_review,
            revision_count=len(revision_history),
            artifacts=artifacts,
            paper_ready_images=paper_images,
            files_before=code_files_before,
        )
        await redis_manager.publish_message(self.task_id, execution_summary)
        writer_prompt = flows.get_writer_prompt(
            key,
            coder_response.code_response or "",
            self.code_interpreter,
            config_template,
            model_review=execution_review.model_dump(mode="json"),
        )
        if revision_feedback:
            writer_prompt = (
                f"{writer_prompt}\n\n【人工审核退回意见，必须重写本节并逐条落实】\n"
                f"{revision_feedback}"
            )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content=f"论文手开始写{key}部分"),
        )
        writer_response: WriterResponse | None = None
        for writer_attempt in range(1, 3):
            writer_response = await writer_agent.run(
                writer_prompt,
                available_images=paper_images,
                sub_title=key,
            )
            try:
                validate_writer_section(
                    key,
                    writer_response.response_content,
                    required_images=paper_images,
                    quality_report=(
                        final_evidence.get("quality_report")
                        if isinstance(final_evidence.get("quality_report"), dict)
                        else None
                    ),
                    question_text=question_text,
                    grounding_values=collect_grounding_values(final_evidence),
                )
                break
            except DeliverableValidationError as error:
                if writer_attempt >= 2:
                    # 与结构章节一致：写作失败挂起等人，不作废已通过门禁的计算
                    await redis_manager.publish_message(
                        self.task_id,
                        SystemMessage(
                            content=(
                                f"{key} 论文段落连续 2 次未通过门禁，"
                                f"任务已挂起等待人工处理：{error}"
                            ),
                            type="error",
                        ),
                    )
                    await self._require_human_approval(
                        state,
                        node_id,
                        summary=(
                            f"{key} 的计算已通过质量门禁，但论文段落连续 2 次"
                            f"未通过写作门禁，已暂停任务。最后一次失败原因：{error}\n"
                            "批准后将复用已通过的计算产物重写本节；"
                            "退回本节点并附意见可指导重写方向。"
                        ),
                        allow_incomplete=True,
                    )
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=f"{key} 论文段落未通过门禁，写作手返修: {error}",
                        type="warning",
                    ),
                )
                writer_prompt = (
                    f"上一稿未通过论文门禁：{error}\n"
                    "请基于已经通过代码门禁的真实结果重写本节，不得编造数值，"
                    "必须包含模型公式、可核对数值、验证结论、局限性和指定图片。\n"
                    + writer_prompt
                )

        assert writer_response is not None
        user_output.set_res(key, writer_response)
        state.setdefault("solution_results", {})[key] = {
            "coder_response": coder_response.model_dump(mode="json"),
            "writer_response": writer_response.model_dump(mode="json"),
            "paper_ready_images": paper_images,
            "artifacts": sorted(set(artifacts)),
            "modeler_review": execution_review.model_dump(mode="json"),
            "execution_summary": execution_summary.model_dump(mode="json"),
        }
        await self._complete_node(state, node_id)
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content=f"论文手完成{key}部分"),
        )
        await self._require_human_approval(
            state,
            node_id,
            summary=(
                (
                    f"{self.checkpoint.node_label(node_id, state)}的证据产物已完整落盘，"
                    "但质量报告要求人工裁决；系统不会自动进入下一步。"
                    if execution_review.verdict == "manual_review"
                    else (
                        f"{self.checkpoint.node_label(node_id, state)}已完成，代码结果与论文段落"
                        "均通过自动质量门禁。"
                    )
                )
                + f" 共登记 {len(set(artifacts))} 项产物。"
            ),
            artifacts=sorted(set(artifacts)),
            quality_report=quality_report,
            explain={
                "what_happened": execution_summary.run_summary
                + (
                    "。注意：质量报告要求人工裁决，批准前请看清下方数字。"
                    if execution_review.verdict == "manual_review"
                    else ""
                ),
                "key_numbers": [
                    item.model_dump(mode="json")
                    for item in execution_summary.metric_explanations
                ],
                "next_step": self._next_step_plain(state, node_id),
                "revise_hint": (
                    "对模型效果不满意，退回本节点并说明方向"
                    "（例如换更简单的模型、补充某项验证）。"
                ),
            },
        )

    @staticmethod
    def _build_model_revision_coder_prompt(
        *,
        question_text: str,
        gate_error: str,
        revision_plan: dict[str, Any],
        contract_prompt: str,
    ) -> str:
        """把建模手基于实测结果制定的新方案交给代码手执行。"""
        candidates = revision_plan.get("candidate_models", [])
        candidate_lines = "\n".join(
            f"- {item.get('name', '')} ({item.get('role', '')}): "
            f"{item.get('reason', '')}"
            for item in candidates
            if isinstance(item, dict)
        )
        rejected = "、".join(
            str(item) for item in revision_plan.get("rejected_models", [])
        )
        return f"""
【建模手根据真实运行结果发起换模，必须重新执行代码】
问题：{question_text}
上轮门禁失败：{gate_error}
失败诊断：{revision_plan.get("diagnosis", "")}
已淘汰模型：{rejected or "见上轮质量报告"}

本轮必须在相同独立验证口径下真实运行的候选：
{candidate_lines}

本轮主模型：{revision_plan.get("selected_model", "")}
新建模方案：{revision_plan.get("revised_strategy", "")}
验证方案：{revision_plan.get("validation_plan", "")}
验收标准：{revision_plan.get("acceptance_criteria", "")}

禁止复用上轮失败指标，禁止降低门槛，禁止仅修改 JSON。必须重新训练/求解、重新生成产物，并让程序独立复核。

{contract_prompt}
""".strip()

    def _clone_writer_agent(
        self,
        writer_llm: LLM,
        problem: Problem,
        scholar: OpenAlexScholar,
    ) -> WriterAgent:
        """为并行章节创建独立写作手实例，避免共享对话历史串线。"""
        return self._new_writer_agent(writer_llm, problem, scholar)

    def _new_writer_agent(
        self,
        writer_llm: LLM,
        problem: Problem,
        scholar: OpenAlexScholar,
    ) -> WriterAgent:
        """Build an isolated writing session with the workflow's shared limits."""
        return WriterAgent(
            problem.task_id,
            writer_llm,
            scholar=scholar,
            cancel_event=self.cancel_event,
            format_output=problem.format_output,
            context_window=settings.WRITER_CONTEXT_WINDOW,
            comp_template=problem.comp_template,
        )

    def _new_coder_agent(self, coder_llm: LLM, problem: Problem) -> CoderAgent:
        """Bind the active interpreter to one solver session."""
        assert self.code_interpreter is not None
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
        )

    async def _write_chapters_parallel(
        self,
        pending_write: list[tuple[str, str]],
        state: dict[str, Any],
        writer_llm: LLM,
        problem: Problem,
        scholar: OpenAlexScholar,
        user_output: UserOutput,
    ) -> None:
        """并行撰写结构章节，逐章校验返修，最后合并为一次人工验收。

        各章节输入互不引用（flows 六章 prompt 相互独立），并行不损失一致性；
        合并审批仍保留退回单章的能力（revision_targets 含全部章节）。
        """
        assert self.checkpoint is not None
        await self._start_node(state, f"write:{pending_write[0][0]}")
        await self._check_cancelled()
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=f"论文手开始并行撰写 {len(pending_write)} 个结构章节"
            ),
        )

        # 插话统一广播到全部章节：并行克隆各自 drain 会被随机一章独占
        user_notes = await redis_manager.drain_user_notes(self.task_id)
        note_block = (
            "\n\n【用户实时插话，全部章节都必须遵循】\n"
            + "\n".join(f"- {note}" for note in user_notes)
            if user_notes
            else ""
        )
        prompts: dict[str, str] = {}
        for key, prompt in pending_write:
            feedback = self.checkpoint.consume_revision_feedback(state, f"write:{key}")
            prompts[key] = (
                f"{prompt}\n\n【人工审核退回意见，必须重写本节并逐条落实】\n{feedback}"
                if feedback
                else prompt
            ) + note_block

        # 限制同时在飞的章节数，避免六路并发触发中转限流连锁
        write_semaphore = asyncio.Semaphore(3)

        async def _generate(key: str) -> WriterResponse:
            async with write_semaphore:
                agent = self._clone_writer_agent(writer_llm, problem, scholar)
                return await agent.run(prompt=prompts[key], sub_title=key)

        outcomes = await asyncio.gather(
            *(_generate(key) for key, _ in pending_write),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, asyncio.CancelledError):
                raise asyncio.CancelledError("任务被用户停止")
        await self._check_cancelled()

        failed_chapters: list[tuple[str, str]] = []
        for (key, _), outcome in zip(pending_write, outcomes):
            node_id = f"write:{key}"
            writer_response: WriterResponse | None = (
                outcome if isinstance(outcome, WriterResponse) else None
            )
            retry_prompt = prompts[key]
            if writer_response is None:
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=f"{key} 章节并行生成失败，转入串行重写: {outcome}",
                        type="warning",
                    ),
                )
            last_error = ""
            for writer_attempt in range(1, 3):
                if writer_response is None:
                    agent = self._clone_writer_agent(writer_llm, problem, scholar)
                    writer_response = await agent.run(
                        prompt=retry_prompt, sub_title=key
                    )
                try:
                    validate_writer_section(
                        key,
                        writer_response.response_content,
                        expected_question_count=(
                            self.ques_count if key == "firstPage" else 0
                        ),
                    )
                    break
                except DeliverableValidationError as error:
                    last_error = str(error)
                    writer_response = None
                    if writer_attempt >= 2:
                        break
                    await redis_manager.publish_message(
                        self.task_id,
                        SystemMessage(
                            content=f"{key} 章节未通过门禁，写作手返修: {error}",
                            type="warning",
                        ),
                    )
                    retry_prompt = (
                        f"上一稿未通过章节门禁：{error}\n"
                        "请重写本节并逐条修复上述全部问题。\n" + prompts[key]
                    )
            if writer_response is None:
                # 先完成其余章节再统一挂起，避免恢复后浪费重写成功章节
                failed_chapters.append((key, last_error))
                continue
            user_output.set_res(key, writer_response)
            state.setdefault("write_results", {})[key] = writer_response.model_dump(
                mode="json"
            )
            await self._complete_node(state, node_id)

        if failed_chapters:
            failed_keys = "、".join(key for key, _ in failed_chapters)
            first_key, first_error = failed_chapters[0]
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(
                    content=(
                        f"章节 {failed_keys} 连续 2 次未通过门禁，"
                        f"任务已挂起等待人工处理；其余章节已完成并保留。"
                    ),
                    type="error",
                ),
            )
            await self._require_human_approval(
                state,
                f"write:{first_key}",
                summary=(
                    f"章节 {failed_keys} 连续 2 次未通过章节门禁，已暂停任务；"
                    f"其余章节已完成并保留。最后一次失败原因：{first_error}\n"
                    "批准后将只重写失败章节；退回并附意见可指导重写方向。"
                ),
                allow_incomplete=True,
            )

        chapter_labels = "、".join(
            self.checkpoint.node_label(f"write:{key}", state)
            for key, _ in pending_write
        )
        last_node = f"write:{pending_write[-1][0]}"
        await self._require_human_approval(
            state,
            last_node,
            summary=(
                f"论文 {len(pending_write)} 个结构章节已并行完成并通过章节门禁："
                f"{chapter_labels}。"
            ),
            explain={
                "what_happened": (
                    f"{len(pending_write)} 个结构章节（{chapter_labels}）已并行撰写完成，"
                    "各章均通过自动检查；本次合并为一次验收。"
                ),
                "next_step": self._next_step_plain(state, last_node),
                "revise_hint": (
                    "对某一章不满意，退回时在目标节点里选择该章节，"
                    "只会重写该章及其下游。"
                ),
            },
        )

    async def _review_and_polish_paper(
        self,
        state: dict[str, Any],
        user_output: UserOutput,
        writer_agent: WriterAgent,
        judge_llm: LLM,
    ) -> dict[str, Any] | None:
        """终稿评委评审 + 最弱章节定向重写；失败降级，绝不阻塞交付。"""
        assert self.checkpoint is not None
        existing = state.get("paper_review")
        if isinstance(existing, dict) and existing:
            # 哨兵值表示上次评审已降级跳过：一次 finalize 生命周期内不重试
            return None if existing.get("skipped") else existing
        paper_text = user_output.get_result_to_save()
        if not paper_text.strip():
            return None
        await publish_activity(
            self.task_id, "评委视角正在通读整篇论文并打分…", category="gate"
        )
        try:
            deterministic_audit = audit_paper_style(paper_text)
            review = await judge_paper(
                judge_llm,
                paper_text,
                self.ques_count,
                deterministic_findings=list(deterministic_audit.issues),
            )
        except Exception as exc:
            logger.warning(f"终稿评审降级跳过: {exc}")
            return None
        if not review:
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(
                    content="终稿评审未能完成，跳过评委复审直接进入最终检查",
                    type="warning",
                ),
            )
            state["paper_review"] = {"skipped": True}
            self.checkpoint.save(state)
            return None

        score_line = "、".join(
            f"{label}{review['scores'].get(key, 0):.0f}分"
            for key, label in (
                ("abstract", "摘要"),
                ("modeling", "建模"),
                ("solution_validation", "求解验证"),
                ("evidence", "证据"),
                ("style", "表述"),
                ("writing", "规范"),
                ("innovation", "创新"),
            )
        )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=(
                    f"评委复审完成：总分 {review['overall']:.0f}/10（{score_line}）。"
                    + (
                        f"将定向重写 {len(review['weakest_sections'])} 个最弱章节"
                        if review["weakest_sections"]
                        else "未发现需要重写的章节"
                    )
                ),
            ),
        )
        rewritten_sections: list[str] = []
        for item in review["weakest_sections"][:2]:
            key = str(item.get("section_key", ""))
            if key not in user_output.seq:
                continue
            current = user_output.get_res().get(key, {}).get("response_content", "")
            if not isinstance(current, str) or not current.strip():
                continue
            await publish_activity(
                self.task_id,
                f"评委指出《{key}》需改进，论文手定向重写中…",
                category="llm",
            )
            rewrite_prompt = (
                f"【终稿评委复审重写指令】章节({key})被评委指出问题：\n"
                f"{item.get('problems', '')}\n"
                f"修改要求：{item.get('revision_directive', '')}\n"
                "硬性约束：保留原稿中的所有 Markdown 标题（如 ## 5.2）一字不改；"
                "保留全部真实数值、图片引用与结论不变，只改表达、结构与深度；"
                "只输出重写后的本章节完整正文。\n\n【当前稿件】\n"
                f"{current}"
            )
            # 复原 solve 阶段的门禁强度：重写稿不得引入假数值或丢图
            grounding: set[float] | None = None
            required_images: list[str] | None = None
            rewrite_quality_report: dict[str, Any] | None = None
            question_text = ""
            if key.startswith("ques"):
                question_text = str(self.questions.get(key, ""))
                try:
                    contract = build_question_contract(
                        question_key=key,
                        question_text=question_text,
                        user_requirements=str(
                            (state.get("problem") or {}).get("user_requirements", "")
                        ),
                    )
                    evidence = collect_model_quality_evidence(self.work_dir, contract)
                    grounding = collect_grounding_values(evidence)
                    quality = evidence.get("quality_report")
                    rewrite_quality_report = (
                        quality if isinstance(quality, dict) else None
                    )
                except Exception as exc:
                    logger.warning(f"重写校验证据重建失败 {key}: {exc}")
                required_images = [
                    str(image)
                    for image in (
                        state.get("solution_results", {}).get(key, {}) or {}
                    ).get("paper_ready_images", [])
                ]
            try:
                rewritten = await writer_agent.run(prompt=rewrite_prompt, sub_title=key)
                validate_writer_section(
                    key,
                    rewritten.response_content,
                    required_images=required_images,
                    quality_report=rewrite_quality_report,
                    question_text=question_text,
                    grounding_values=grounding,
                    expected_question_count=(
                        self.ques_count if key == "firstPage" else 0
                    ),
                )
                # 标题保持：重写不得弄丢原稿的编号主标题，否则终稿硬门禁必失败
                for heading in re.findall(r"(?m)^##\s+\d+\.\d+", current):
                    if heading not in (rewritten.response_content or ""):
                        raise DeliverableValidationError(
                            f"{key} 重写稿丢失原稿标题 {heading}"
                        )
            except Exception as exc:
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=f"章节 {key} 定向重写未通过校验，保留原稿：{exc}",
                        type="warning",
                    ),
                )
                continue
            user_output.set_res(key, rewritten)
            if key in state.get("write_results", {}):
                state["write_results"][key] = rewritten.model_dump(mode="json")
            elif key in state.get("solution_results", {}):
                state["solution_results"][key]["writer_response"] = (
                    rewritten.model_dump(mode="json")
                )
            item["rewritten"] = True
            rewritten_sections.append(key)
        if rewritten_sections:
            review["rewritten_sections"] = rewritten_sections
        user_output.save_result()
        state["paper_review"] = review
        self.checkpoint.save(state)
        try:
            save_review(self.work_dir, review)
        except Exception as exc:
            logger.warning(f"评审结果落盘失败: {exc}")
        return review

    async def _finalize_node(
        self,
        state: dict[str, Any],
        user_output: UserOutput,
        writer_agent: WriterAgent,
        judge_llm: LLM,
    ) -> None:
        """合并全文并执行最终硬门禁。"""
        assert self.checkpoint is not None
        await self._start_node(state, "finalize")
        await self._check_cancelled()
        revision_feedback = self.checkpoint.consume_revision_feedback(state, "finalize")
        if revision_feedback:
            # finalize 是确定性合并检查，无法按意见自动修改内容；
            # 明确告知而不是静默吞掉意见。
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(
                    content=(
                        "最终合并节点无法按退回意见自动修改论文内容，"
                        f"意见仅作记录：{revision_feedback}\n"
                        "如需修改正文，请退回具体章节节点。"
                    ),
                    type="warning",
                ),
            )
        user_output.save_result()
        review = await self._review_and_polish_paper(
            state, user_output, writer_agent, judge_llm
        )
        paper_markdown = polish_markdown(
            user_output.get_result_to_save(), Path(self.work_dir)
        )
        try:
            validate_final_paper(
                self.work_dir,
                user_output.get_res(),
                expected_sections=user_output.seq,
                paper_text=paper_markdown,
            )
            comp_template = CompTemplate(
                str((state.get("problem") or {}).get("comp_template", "CHINA"))
            )
            delivery = render_paper_deliverables(
                paper_markdown,
                self.work_dir,
                comp_template,
            )
        except (DeliverableValidationError, PaperRenderError, ValueError) as error:
            # 此时全部计算与写作已完成，损失最大，绝不作废任务。
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(
                    content=f"最终论文未通过硬门禁，任务已挂起等待人工处理：{error}",
                    type="error",
                ),
            )
            await self._require_human_approval(
                state,
                "finalize",
                summary=(
                    f"最终论文未通过硬门禁，已暂停任务。失败原因：{error}\n"
                    "请退回失败原因对应的写作/求解章节重写后自动重新合并"
                    "（退回 finalize 本身只会原样重检，无法修复正文问题）。"
                ),
                allow_incomplete=True,
            )
        await self._complete_node(state, "finalize")
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=(
                    f"整篇论文通过最终硬门禁并完成两次 LaTeX 编译："
                    f"{delivery.tex_path.name}、{delivery.pdf_path.name} "
                    f"（{delivery.page_count} 页）"
                ),
                type="success",
            ),
        )
        review_summary = ""
        review_numbers: list[dict[str, Any]] = []
        if isinstance(review, dict) and review:
            review_summary = (
                f"评委复审总分 {review.get('overall', 0):.0f}/10。"
                f"{str(review.get('summary', ''))[:200]}"
            )
            rewritten_sections = review.get("rewritten_sections") or []
            if rewritten_sections:
                review_summary += (
                    f"（已按评委意见定向重写：{'、'.join(rewritten_sections)}；"
                    "评分为重写前基准）"
                )
            review_numbers = build_review_explain_numbers(review)
        await self._require_human_approval(
            state,
            "finalize",
            summary=(
                "整篇论文已通过内容、表述、LaTeX 编译与 PDF 渲染门禁，"
                "等待你的最终验收。"
            ),
            artifacts=[delivery.pdf_path.name, delivery.tex_path.name],
            explain={
                "what_happened": (
                    "全文已合并为可编译 LaTeX，并由同一源码生成 PDF；"
                    "章节、字数、证据表达、参考文献、图片、纸型、空白页与"
                    "抽样渲染均已检查。" + review_summary
                ),
                "key_numbers": review_numbers,
                "next_step": "批准后直接交付 res.pdf 与 res.tex，任务完成",
                "revise_hint": "对某章不满意，退回对应章节修改后会自动重新合并。",
            },
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
