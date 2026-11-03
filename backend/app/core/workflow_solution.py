"""求解节点：代码手执行、质量门禁、建模手复核与返修循环。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.agents.coder_agent import CoderAgent
from app.core.agents.modeler_agent import ModelerAgent
from app.core.agents.writer_agent import WriterAgent
from app.core.deliverable_contract import (
    DeliverableValidationReport,
    DeliverableValidationError,
    ModelQualityValidationError,
    QuestionDeliverableContract,
    build_repair_prompt,
    collect_grounding_values,
    collect_model_quality_evidence,
    find_reusable_stage_artifacts,
    get_repair_execution_limit,
    validate_question_deliverables,
    validate_writer_section,
)
from app.core.flows import Flows
from app.core.execution_summary import (
    build_execution_summary_message,
    snapshot_code_files,
)
from app.core.activity import publish_activity
from app.models.user_output import UserOutput
from app.schemas.A2A import (
    CoderToWriter,
    ModelExecutionReview,
    WriterResponse,
)
from app.services.task_bus import task_bus


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


@dataclass
class _SolutionRun:
    """一个求解节点在门禁循环中逐步演进的状态。"""

    key: str
    node_id: str
    question_text: str
    model_plan: str
    coder_prompt: str
    revision_feedback: str | None
    contract: QuestionDeliverableContract | None
    revision_history: list[dict[str, Any]]
    execution_reviews: list[dict[str, Any]]
    recovered_gate_report: DeliverableValidationReport | None = None
    repair_execution_limit: int | None = None


@dataclass
class _SolutionOutcome:
    coder_response: CoderToWriter
    gate_report: DeliverableValidationReport
    execution_review: ModelExecutionReview
    final_evidence: dict[str, Any]


def _normalized_model(name: str) -> str:
    return "".join(character.casefold() for character in name if character.isalnum())


def _file_version(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


class SolutionNodeMixin:
    """由 RemitWorkFlow 继承；依赖其检查点与节点生命周期方法。"""

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
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        if self.code_interpreter is None:
            raise RuntimeError("code interpreter is not initialized")
        node_id = f"solve:{key}"
        await self._start_node(state, node_id)
        await self._check_cancelled()
        await task_bus.notify(self.task_id, f"代码手开始求解{key}")

        code_files_before = snapshot_code_files(self.work_dir)
        run = await self._prepare_solution_run(key, node_id, value, state)
        outcome = await self._run_quality_gates(run, state, modeler_agent, coder_agent)
        await self._deliver_solution(
            run,
            outcome,
            state,
            flows=flows,
            config_template=config_template,
            writer_agent=writer_agent,
            user_output=user_output,
            code_files_before=code_files_before,
        )

    async def _prepare_solution_run(
        self, key: str, node_id: str, value: dict[str, Any], state: dict[str, Any]
    ) -> _SolutionRun:
        """组装代码手提示词，并识别可复用的断点产物与未落实的返修方案。"""
        from app.core.project_audit import pilot_evidence_notice

        coder_prompt = value["coder_prompt"] + "\n" + pilot_evidence_notice(state)
        revision_feedback = self.checkpoint.consume_revision_feedback(state, node_id)
        if revision_feedback:
            coder_prompt = (
                f"{coder_prompt}\n\n【人工审核退回意见，必须重新运行并逐条落实】\n"
                f"{revision_feedback}"
            )
            backup = state.get("revision_artifact_backups", {}).get(key, {})
            if backup.get("directory"):
                coder_prompt += (
                    "\n【返修旧文件归档】旧源码和结果位于任务内 "
                    f"{backup['directory']}。可读取其中源码作局部修改，"
                    "无需在项目外搜索。该目录中的旧报告、图表与数值已失效，"
                    "不能直接恢复为本轮证据；修改后的脚本须保存到当前任务目录，"
                    "重新运行相关步骤并生成当前报告。"
                )
        run = _SolutionRun(
            key=key,
            node_id=node_id,
            question_text=str(value.get("question_text", key)),
            model_plan=str(value.get("model_plan", "")),
            coder_prompt=coder_prompt,
            revision_feedback=revision_feedback,
            contract=value.get("contract"),
            revision_history=state.setdefault("model_revision_history", {}).setdefault(
                key, []
            ),
            execution_reviews=state.setdefault(
                "model_execution_reviews", {}
            ).setdefault(key, []),
        )
        await self._recover_existing_deliverables(run, state)
        self._resume_unmaterialized_revision(run, state)
        return run

    async def _recover_existing_deliverables(
        self, run: _SolutionRun, state: dict[str, Any]
    ) -> None:
        """已有完整产物时直接复用；不完整时切换为断点返修提示。"""
        contract = run.contract
        if contract is None:
            return
        reusable_stage_artifacts = find_reusable_stage_artifacts(
            self.work_dir, contract
        )
        quality_report_exists = (
            Path(self.work_dir) / contract.quality_filename
        ).is_file()
        incomplete_revision = bool(
            run.revision_feedback
            and any(
                isinstance(item, dict)
                and item.get("decision") == "revise"
                and item.get("allow_incomplete") is True
                and str(
                    item.get("revision_target_node_id") or item.get("node_id") or ""
                )
                == run.node_id
                for item in reversed(state.get("approval_history", []))
            )
        )
        if not (
            reusable_stage_artifacts or quality_report_exists or incomplete_revision
        ):
            return
        try:
            recovered_report = validate_question_deliverables(self.work_dir, contract)
            if run.revision_feedback and recovered_report.manual_review_required:
                raise DeliverableValidationError(
                    "本轮明确要求返修，已有报告仍需人工复核；保留证据并落实修改意见，不能直接复用旧结论。"
                )
            run.recovered_gate_report = recovered_report
            await task_bus.notify(
                self.task_id,
                f"检测到 {run.key} 已有完整"
                + (
                    "人工复核"
                    if recovered_report.manual_review_required
                    else "且已通过质量门禁的"
                )
                + "产物，将结合待办返修意见恢复当前步骤",
                "warning" if recovered_report.manual_review_required else "success",
            )
        except DeliverableValidationError as interrupted_error:
            run.repair_execution_limit = get_repair_execution_limit(
                self.work_dir, contract, interrupted_error
            )
            repair_prompt = build_repair_prompt(
                contract,
                interrupted_error,
                self.work_dir,
                task_context=f"{run.question_text}\n{run.model_plan}\n{run.revision_feedback or ''}",
            )
            run.coder_prompt = (
                "【断点返修模式：保留已有证据，补齐未完成步骤】\n"
                "先根据下面的磁盘状态判断哪些步骤已完成；无交付物时允许完成原任务所需的数据读取与处理。"
                "禁止安装依赖、重跑与报错无关的模型或重建已经存在的图表。\n"
                + (
                    f"\n【人工审核退回意见，优先级最高】\n{run.revision_feedback}\n"
                    if run.revision_feedback
                    else ""
                )
                + f"\n{repair_prompt}"
            )

    def _resume_unmaterialized_revision(
        self, run: _SolutionRun, state: dict[str, Any]
    ) -> None:
        """技术暂停不撤销科学复核：磁盘产物未体现已保存的换模方案时继续执行该方案。"""
        if (
            run.recovered_gate_report is None
            or not run.revision_history
            or run.key in state.get("solution_results", {})
        ):
            return
        pending_revision = run.revision_history[-1]
        revision_plan = pending_revision.get("revision_plan", {})
        revised_model = (
            str(revision_plan.get("selected_model", "")).strip()
            if isinstance(revision_plan, dict)
            else ""
        )
        recovered_quality = collect_model_quality_evidence(
            self.work_dir, run.contract
        ).get("quality_report", {})
        recovered_model = (
            str(recovered_quality.get("selected_model", "")).strip()
            if isinstance(recovered_quality, dict)
            else ""
        )
        revised_normalized = _normalized_model(revised_model)
        if (
            pending_revision.get("trigger") == "modeler_review"
            and revised_normalized
            and revised_normalized not in _normalized_model(recovered_model)
        ):
            run.recovered_gate_report = None
            run.model_plan = str(revision_plan.get("revised_strategy", run.model_plan))
            run.coder_prompt = self._build_model_revision_coder_prompt(
                question_text=run.question_text,
                gate_error=str(
                    pending_revision.get("gate_error", "继续未完成的结果补件")
                ),
                revision_plan=revision_plan,
                contract_prompt=run.contract.prompt_block(),
            )
            run.repair_execution_limit = None

    async def _run_quality_gates(
        self,
        run: _SolutionRun,
        state: dict[str, Any],
        modeler_agent: ModelerAgent,
        coder_agent: CoderAgent,
    ) -> _SolutionOutcome:
        """最多三轮：代码执行 → 交付门禁 → 建模手复核，失败时换模或返修。"""
        coder_response: CoderToWriter | None = None
        gate_report: DeliverableValidationReport | None = None
        execution_review: ModelExecutionReview | None = None
        final_evidence: dict[str, Any] = {}
        first_gate_attempt = min(
            3,
            1
            + sum(
                item.get("trigger") == "modeler_review" for item in run.revision_history
            ),
        )
        for gate_attempt in range(first_gate_attempt, 4):
            if run.contract is None:
                raise RuntimeError(f"{run.key} 缺少强制质量契约")
            if (
                gate_attempt == first_gate_attempt
                and run.recovered_gate_report is not None
            ):
                coder_response = CoderToWriter(
                    code_response=(
                        f"已从 {run.contract.quality_filename} 恢复真实执行产物；"
                        + (
                            "该阶段触发人工审核，"
                            if run.recovered_gate_report.manual_review_required
                            else "现有产物已通过质量门禁，"
                        )
                        + "不重复运行代码。"
                    )
                )
                gate_report = run.recovered_gate_report
            else:
                coder_response, previous_version = await self._execute_coder(
                    run, state, coder_agent
                )
                try:
                    gate_report = self._validate_fresh_deliverables(
                        run, previous_version
                    )
                except DeliverableValidationError as error:
                    await self._handle_gate_failure(
                        run, state, error, gate_attempt, modeler_agent
                    )
                    continue

            if gate_report.manual_review_required:
                final_evidence = collect_model_quality_evidence(
                    self.work_dir, run.contract
                )
                manual_quality = final_evidence.get("quality_report", {})
                execution_review = _build_manual_execution_review(
                    key=run.key,
                    contract=run.contract,
                    gate_report=gate_report,
                    quality_report=(
                        manual_quality if isinstance(manual_quality, dict) else {}
                    ),
                )
                run.execution_reviews.append(
                    {
                        "attempt": gate_attempt,
                        "execution_evidence": final_evidence,
                        "review": execution_review.model_dump(mode="json"),
                    }
                )
                self.checkpoint.save(state)
                break

            execution_review, final_evidence = await self._review_result(
                run, state, gate_attempt, modeler_agent
            )
            if execution_review.verdict == "refine":
                state["model_stage_phase"] = {"node": run.node_id, "phase": "work"}
                revision_plan = execution_review.revision_plan
                if revision_plan is None:
                    raise RuntimeError(
                        "execution review did not provide a revision plan"
                    )
                quality = final_evidence.get("quality_report", {})
                # 通过格式门禁后的建模手 refine 同样代表换模重算，不能沿用
                # 断点格式返修的两次执行上限。
                self._apply_revision(
                    run,
                    state,
                    attempt=gate_attempt,
                    trigger="modeler_review",
                    gate_error=execution_review.summary,
                    prompt_gate_error=f"建模手结果复核要求继续改进：{execution_review.summary}",
                    evidence=final_evidence,
                    failed_model=(
                        str(quality.get("selected_model", "")).strip()
                        if isinstance(quality, dict)
                        else ""
                    ),
                    revision_plan=revision_plan,
                )
                await task_bus.notify(
                    self.task_id,
                    f"建模手复核 {run.key} 后认为仍可改进，已改用 "
                    f"{revision_plan.selected_model} 重新运行 ({gate_attempt + 1}/3)",
                    "warning",
                )
                continue

            self.checkpoint.save(state)
            break

        if coder_response is None:
            raise RuntimeError("coder response is not available")
        if gate_report is None:
            raise RuntimeError("quality gate report is not available")
        if execution_review is None:
            raise RuntimeError("execution review is not available")
        return _SolutionOutcome(
            coder_response, gate_report, execution_review, final_evidence
        )

    async def _execute_coder(
        self, run: _SolutionRun, state: dict[str, Any], coder_agent: CoderAgent
    ) -> tuple[CoderToWriter, tuple[int, int] | None]:
        """运行代码手；同时返回运行前的质量报告版本，供门禁判断是否已更新。"""
        contract = run.contract
        previous_version = _file_version(
            Path(self.work_dir) / contract.quality_filename
        )

        def check_current_deliverables() -> bool:
            # A manual-review result also ends coding, but still goes
            # through the existing human gate below. Never auto-accept it.
            validate_question_deliverables(self.work_dir, contract)
            return True

        state.pop("pending_model_review", None)
        state["model_stage_phase"] = {"node": run.node_id, "phase": "work"}
        self.checkpoint.save(state)
        coder_response = await coder_agent.run(
            prompt=run.coder_prompt,
            subtask_title=run.key,
            max_code_executions=run.repair_execution_limit,
            required_files=(contract.quality_filename,),
            completion_check=check_current_deliverables,
        )
        await publish_activity(
            self.task_id,
            f"正在校验 {run.key} 的交付质量（不看运气，逐项检查产物）…",
            category="gate",
        )
        return coder_response, previous_version

    def _validate_fresh_deliverables(
        self, run: _SolutionRun, previous_version: tuple[int, int] | None
    ) -> DeliverableValidationReport:
        """本轮必须重新写出质量报告，旧报告不能送审。"""
        contract = run.contract
        current = _file_version(Path(self.work_dir) / contract.quality_filename)
        if current is not None and current == previous_version:
            raise DeliverableValidationError(
                f"本轮执行后 {contract.quality_filename} 仍是旧版本。"
                "本轮技术修复尚未落盘，不能用旧报告送审或宣称验证完成；"
                "请沿最近执行错误修复并保存、回读当前真实结果。"
            )
        return validate_question_deliverables(self.work_dir, contract)

    async def _handle_gate_failure(
        self,
        run: _SolutionRun,
        state: dict[str, Any],
        error: DeliverableValidationError,
        gate_attempt: int,
        modeler_agent: ModelerAgent,
    ) -> None:
        """三连败挂起人工；模型质量不达标退回建模手换模；其余交代码手返修。"""
        key, contract = run.key, run.contract
        if gate_attempt >= 3:
            # 三连败不再作废任务：保留全部产物，挂起等待人工裁决。
            await task_bus.notify(
                self.task_id,
                f"{key} 连续 3 次未通过交付质量门禁，任务已挂起等待人工处理：{error}",
                "error",
            )
            await self._require_human_approval(
                state,
                run.node_id,
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
                        "批准 = 带着失败上下文重试本节点；退回 = 按你的意见修复后再跑"
                    ),
                    "revise_hint": (
                        "如果你能看出问题所在（数据、方法或门槛太严），"
                        "退回时写一句就能少走很多弯路。"
                    ),
                },
            )
        if not isinstance(error, ModelQualityValidationError):
            await task_bus.notify(
                self.task_id,
                f"{key} 产物或验证格式未通过，代码手自动返修 ({gate_attempt}/3): {error}",
                "warning",
            )
            run.coder_prompt = build_repair_prompt(
                contract,
                error,
                self.work_dir,
                task_context=f"{run.question_text}\n{run.model_plan}\n{run.revision_feedback or ''}",
            )
            run.repair_execution_limit = get_repair_execution_limit(
                self.work_dir, contract, error
            )
            return

        evidence = collect_model_quality_evidence(self.work_dir, contract)
        rejected_models = [
            str(item.get("failed_model", ""))
            for item in run.revision_history
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
        await task_bus.notify(
            self.task_id,
            f"{key} 样本外性能、可行性或稳定性未达标，"
            f"已退回建模手更换模型 ({gate_attempt}/3): {error}",
            "warning",
        )
        revision_plan = await modeler_agent.revise_after_execution(
            question_key=key,
            question_text=run.question_text,
            original_plan=run.model_plan,
            gate_error=str(error),
            evidence=evidence,
            rejected_models=rejected_models,
        )
        # 换模是一次新的真实求解，不是只修 JSON/CSV 的格式返修；
        # 必须恢复完整执行预算，否则新方案会在两次工具调用后再次
        # 被截断，永远无法形成可比较的模型证据。
        self._apply_revision(
            run,
            state,
            attempt=gate_attempt,
            trigger="quality_gate",
            gate_error=str(error),
            prompt_gate_error=str(error),
            evidence=evidence,
            failed_model=failed_model,
            revision_plan=revision_plan,
        )
        await task_bus.notify(
            self.task_id,
            f"建模手已切换为 {revision_plan.selected_model}，"
            f"代码手将按原门槛重新运行并验证 {key}",
        )

    def _apply_revision(
        self,
        run: _SolutionRun,
        state: dict[str, Any],
        *,
        attempt: int,
        trigger: str,
        gate_error: str,
        prompt_gate_error: str,
        evidence: dict[str, Any],
        failed_model: str,
        revision_plan: Any,
    ) -> None:
        """登记换模方案、同步总体方案，并以完整执行额度交给代码手重算。"""
        plan = revision_plan.model_dump(mode="json")
        run.revision_history.append(
            {
                "attempt": attempt,
                "trigger": trigger,
                "gate_error": gate_error,
                "execution_evidence": evidence,
                "failed_model": failed_model,
                "previous_plan": run.model_plan,
                "revision_plan": plan,
            }
        )
        saved_modeler = state.get("modeler_response")
        if isinstance(saved_modeler, dict):
            saved_solutions = dict(saved_modeler.get("questions_solution", {}))
            saved_solutions[run.key] = revision_plan.revised_strategy
            saved_modeler["questions_solution"] = saved_solutions
        self.checkpoint.save(state)
        run.model_plan = revision_plan.revised_strategy
        run.coder_prompt = self._build_model_revision_coder_prompt(
            question_text=run.question_text,
            gate_error=prompt_gate_error,
            revision_plan=plan,
            contract_prompt=run.contract.prompt_block(),
        )
        run.repair_execution_limit = None

    async def _review_result(
        self,
        run: _SolutionRun,
        state: dict[str, Any],
        gate_attempt: int,
        modeler_agent: ModelerAgent,
    ) -> tuple[ModelExecutionReview, dict[str, Any]]:
        """建模手基于真实证据复核结果；恢复的磁盘产物同样必须实际复核。"""
        final_evidence = collect_model_quality_evidence(self.work_dir, run.contract)
        problem = state.get("problem") or {}
        original_problem = str(problem.get("ques_all", ""))
        if original_problem:
            final_evidence["original_problem"] = original_problem[:40000]
            final_evidence["original_problem_truncated"] = len(original_problem) > 40000
        rejected_models = [
            str(item.get("failed_model", "")).strip()
            for item in run.revision_history
            if isinstance(item, dict) and str(item.get("failed_model", "")).strip()
        ]
        state["pending_model_review"] = run.node_id
        state["model_stage_phase"] = {"node": run.node_id, "phase": "review"}
        self.checkpoint.save(state)
        from app.services.call_ledger import scope as model_call_scope

        with model_call_scope(self.task_id, budget_phase="review"):
            execution_review = await modeler_agent.review_execution_result(
                question_key=run.key,
                question_text=run.question_text,
                current_plan=run.model_plan,
                evidence=final_evidence,
                rejected_models=list(dict.fromkeys(rejected_models)),
                remaining_runs=3 - gate_attempt,
                task_purpose=problem.get("task_purpose", "modeling"),
                task_constraints=str(problem.get("user_requirements", "")),
            )
        state.pop("pending_model_review", None)
        run.execution_reviews.append(
            {
                "attempt": gate_attempt,
                "execution_evidence": final_evidence,
                "review": execution_review.model_dump(mode="json"),
            }
        )
        return execution_review, final_evidence

    async def _deliver_solution(
        self,
        run: _SolutionRun,
        outcome: _SolutionOutcome,
        state: dict[str, Any],
        *,
        flows: Flows,
        config_template: dict,
        writer_agent: WriterAgent,
        user_output: UserOutput,
        code_files_before: Any,
    ) -> None:
        """发布执行摘要，交付写作（或写作素材），并提交人工审核。"""
        key, node_id = run.key, run.node_id
        review = outcome.execution_review
        await task_bus.notify(
            self.task_id,
            f"{key} 代码结果已通过质量门禁和建模手复核"
            if review.verdict == "accept"
            else (
                f"{key} 证据完整性已通过，但关键稳健性检查存在冲突，"
                "已停止自动返工并转人工审核"
            ),
            "success" if review.verdict == "accept" else "warning",
        )
        quality_path = Path(self.work_dir) / run.contract.quality_filename
        quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
        artifacts = [
            str(item)
            for field in ("artifacts", "paper_ready_images")
            for item in quality_report.get(field, [])
        ]
        paper_images = list(outcome.gate_report.paper_ready_images)
        execution_summary = build_execution_summary_message(
            task_id=self.task_id,
            node_id=node_id,
            node_label=self.checkpoint.node_label(node_id, state),
            section=key,
            work_dir=self.work_dir,
            evidence=outcome.final_evidence,
            review=review,
            revision_count=len(run.revision_history),
            artifacts=artifacts,
            paper_ready_images=paper_images,
            files_before=code_files_before,
        )
        await task_bus.publish_message(self.task_id, execution_summary)
        writer_prompt = flows.get_writer_prompt(
            key,
            outcome.coder_response.code_response or "",
            self.code_interpreter,
            config_template,
            model_review=review.model_dump(mode="json"),
        )
        if run.revision_feedback:
            writer_prompt = (
                f"{writer_prompt}\n\n【人工审核退回意见，必须重写本节并逐条落实】\n"
                f"{run.revision_feedback}"
            )
        result: dict[str, Any] = {
            "coder_response": outcome.coder_response.model_dump(mode="json"),
            "paper_ready_images": paper_images,
            "artifacts": sorted(set(artifacts)),
            "modeler_review": review.model_dump(mode="json"),
            "execution_summary": execution_summary.model_dump(mode="json"),
        }
        if "separate_writing" in state.get("workflow_features", []):
            result.update(
                writer_prompt=writer_prompt,
                question_text=run.question_text,
                grounding_values=sorted(
                    collect_grounding_values(outcome.final_evidence)
                ),
                quality_report=quality_report,
            )
            state.setdefault("solution_results", {})[key] = result
            await self._complete_node(state, node_id)
        else:
            await task_bus.notify(self.task_id, f"论文手开始写{key}部分")
            writer_response = await self._write_solution_section(
                run, state, outcome, writer_agent, writer_prompt, paper_images
            )
            user_output.set_res(key, writer_response)
            result["writer_response"] = writer_response.model_dump(mode="json")
            state.setdefault("solution_results", {})[key] = result
            await self._complete_node(state, node_id)
            await task_bus.notify(self.task_id, f"论文手完成{key}部分")
        node_label = self.checkpoint.node_label(node_id, state)
        manual = review.verdict == "manual_review"
        await self._require_human_approval(
            state,
            node_id,
            summary=(
                (
                    f"{node_label}的证据产物已完整落盘，"
                    "但质量报告要求人工裁决；系统不会自动进入下一步。"
                    if manual
                    else f"{node_label}已完成，计算结果通过自动质量门禁。"
                )
                + f" 共登记 {len(set(artifacts))} 项产物。"
            ),
            artifacts=sorted(set(artifacts)),
            quality_report=quality_report,
            explain={
                "what_happened": execution_summary.run_summary
                + (
                    "。注意：质量报告要求人工裁决，批准前请看清下方数字。"
                    if manual
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

    async def _write_solution_section(
        self,
        run: _SolutionRun,
        state: dict[str, Any],
        outcome: _SolutionOutcome,
        writer_agent: WriterAgent,
        writer_prompt: str,
        paper_images: list[str],
    ) -> WriterResponse:
        """论文手撰写本节，最多两稿；仍未通过时挂起等待人工。"""
        key = run.key
        quality = outcome.final_evidence.get("quality_report")
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
                    omitted_images=writer_response.omitted_images,
                    quality_report=quality if isinstance(quality, dict) else None,
                    question_text=run.question_text,
                    grounding_values=collect_grounding_values(outcome.final_evidence),
                )
                break
            except DeliverableValidationError as error:
                if writer_attempt >= 2:
                    # 与结构章节一致：写作失败挂起等人，不作废已通过门禁的计算
                    await task_bus.notify(
                        self.task_id,
                        f"{key} 论文段落连续 2 次未通过门禁，任务已挂起等待人工处理：{error}",
                        "error",
                    )
                    await self._require_human_approval(
                        state,
                        run.node_id,
                        summary=(
                            f"{key} 的计算已通过质量门禁，但论文段落连续 2 次"
                            f"未通过写作门禁，已暂停任务。最后一次失败原因：{error}\n"
                            "批准后将复用已通过的计算产物重写本节；"
                            "退回本节点并附意见可指导重写方向。"
                        ),
                        allow_incomplete=True,
                    )
                await task_bus.notify(
                    self.task_id,
                    f"{key} 论文段落未通过门禁，写作手返修: {error}",
                    "warning",
                )
                writer_prompt = (
                    f"上一稿未通过论文门禁：{error}\n"
                    "请基于已经通过代码门禁的真实结果重写本节，不得编造数值，"
                    "必须包含模型公式、可核对数值、验证结论、局限性和指定图片。\n"
                    + writer_prompt
                )
        if writer_response is None:
            raise RuntimeError("writer response is not available")
        return writer_response

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
【按已保存的结果复核意见继续返修】
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

禁止复用上轮失败指标，禁止降低门槛，禁止仅修改 JSON。按上述方案执行并独立复核相关步骤；若仅要求补件，保留已通过核验的计算，只读取、补齐和核验缺失证据，不重训或重算无关部分。若要求更换模型，则必须真实运行新模型并生成对应产物。

{contract_prompt}
""".strip()
