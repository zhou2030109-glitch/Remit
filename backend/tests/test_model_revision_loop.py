"""建模手根据代码运行结果自动换模的回归测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.agents.modeler_agent import (
    ModelerAgent,
    _normalize_model_plan,
    repair_json,
)
from app.core.deliverable_contract import (
    DeliverableValidationReport,
    ModelQualityValidationError,
    build_question_contract,
    build_stage_contract,
)
from app.core import workflow as workflow_module
from app.core.workflow import RemitWorkFlow
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.models.user_output import UserOutput
from app.schemas.A2A import (
    CoderToWriter,
    ModelExecutionReview,
    ModelRevisionPlan,
    WriterResponse,
)
from app.schemas.response import ExecutionSummaryMessage
from app.schemas.request import Problem


def _revision_plan(selected_model: str = "GBRT") -> ModelRevisionPlan:
    """创建满足结构门禁的换模方案。"""
    return ModelRevisionPlan(
        diagnosis="分组样本外 R² 为负，原线性假设无法表达非线性与交互效应。",
        rejected_models=["LinearRegression"],
        candidate_models=[
            {
                "name": "MedianBaseline",
                "role": "baseline",
                "reason": "作为相同验证折上的稳健简单基线",
            },
            {
                "name": selected_model,
                "role": "candidate",
                "reason": "捕获非线性和变量交互，同时限制复杂度",
            },
        ],
        selected_model=selected_model,
        revised_strategy=(
            "按孕妇代码执行 GroupKFold，折内完成缺失处理和特征工程，"
            f"比较 MedianBaseline 与 {selected_model}，只保留样本外表现更好的模型。"
        ),
        validation_plan=(
            "使用相同的五折 GroupKFold，比较 OOF RMSE、MAE、R²，"
            "并做主体聚类 Bootstrap 与特征扰动检查。"
        ),
        acceptance_criteria="OOF R²>0 且 RMSE 至少优于同折基线5%。",
    )


def _accepted_review(
    summary: str = "模型结果已通过复核，可以进入论文写作阶段。",
) -> ModelExecutionReview:
    return ModelExecutionReview(
        verdict="accept",
        summary=summary,
        evidence=["分组样本外指标优于相同验证折上的简单基线"],
        strengths=["验证口径一致"],
        weaknesses=["仍需在论文中披露样本范围限制"],
        writer_guidance="如实报告样本外指标、基线比较和适用范围。",
    )


class ModelPlanParsingTests(unittest.TestCase):
    def test_prefixed_json_keeps_every_modeling_stage(self) -> None:
        content = (
            "下面是建模方案。"
            '{"eda":"完整探索方案内容足够长，用于验证前缀后的 JSON 仍可解析。",'
            '"ques1":"问题一完整方案内容足够长，需要保留基线、候选、验证与回退路径。",'
            '"ques2":"问题二完整方案内容足够长，需要保留基线、候选、验证与回退路径。",'
            '"sensitivity_analysis":"完整敏感性分析内容足够长，覆盖参数扰动和稳健性验证。"}'
        )

        parsed = repair_json(content)

        self.assertEqual(
            set(parsed or {}),
            {"eda", "ques1", "ques2", "sensitivity_analysis"},
        )

    def test_missing_formal_questions_fail_structure_gate(self) -> None:
        with self.assertRaisesRegex(ValueError, "缺少方案键"):
            _normalize_model_plan(
                {"eda": "只有探索分析，正式问题都被错误丢失。" * 4},
                ["eda", "ques1", "ques2", "sensitivity_analysis"],
            )


class ManualReviewContextTests(unittest.TestCase):
    """验证全局人工复核不会丢失分问入选/回退模型。"""

    def test_sensitivity_review_guidance_lists_every_selected_model(self) -> None:
        builder = getattr(workflow_module, "_build_manual_execution_review", None)
        self.assertIsNotNone(builder, "工作流缺少可测试的人工复核上下文构造入口")
        assert builder is not None
        review = builder(
            key="sensitivity_analysis",
            contract=build_stage_contract("sensitivity_analysis"),
            gate_report=DeliverableValidationReport(
                passed=False,
                manual_review_required=True,
                manual_review_reason="四问均有需要人工裁决的真实质量冲突",
            ),
            quality_report={
                "selected_model": {
                    "Q1": "DirectHorizonRidge_Level (simple fallback)",
                    "Q2": "Persistence_72h_gap (predictive fallback)",
                    "Q3": "Robust_state_6h",
                    "Q4": "Fixed_event_rule_matrix",
                }
            },
        )

        combined_context = "\n".join(
            [*review.evidence, *review.strengths, review.writer_guidance]
        )
        for model_name in (
            "DirectHorizonRidge_Level",
            "Persistence_72h_gap",
            "Robust_state_6h",
            "Fixed_event_rule_matrix",
        ):
            self.assertIn(model_name, combined_context)
            self.assertIn(model_name, review.writer_guidance)
        self.assertIn("分别报告", review.writer_guidance)
        self.assertIn("入选或回退模型", review.writer_guidance)


class ModelRevisionAgentTests(unittest.IsolatedAsyncioTestCase):
    """验证建模手不会再次选择已经失败的模型。"""

    async def test_modeler_rejects_failed_model_then_selects_new_candidate(
        self,
    ) -> None:
        agent = ModelerAgent("task", MagicMock())
        invalid = _revision_plan("LinearRegression").model_dump_json()
        valid = _revision_plan("GBRT").model_dump_json()
        agent._chat = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                SimpleNamespace(content=invalid, reasoning_content=None),
                SimpleNamespace(content=valid, reasoning_content=None),
            ]
        )

        result = await agent.revise_after_execution(
            question_key="ques1",
            question_text="建立浓度关系模型",
            original_plan="使用线性回归完成拟合与检验。",
            gate_error="分组 OOF R² 必须大于0，当前为 -0.3",
            evidence={"prediction_metrics": {"secondary_metrics": {"r2": -0.3}}},
            rejected_models=["LinearRegression"],
        )

        self.assertEqual(result.selected_model, "GBRT")
        self.assertIn("LinearRegression", result.rejected_models)
        self.assertEqual(agent._chat.await_count, 2)

    async def test_execution_review_refinement_cannot_reuse_rejected_model(
        self,
    ) -> None:
        agent = ModelerAgent("task", MagicMock())

        def review_payload(plan: ModelRevisionPlan) -> str:
            return ModelExecutionReview(
                verdict="refine",
                summary="实测表现虽然达到最低门槛，但仍明显弱于可用候选，需要继续换模。",
                evidence=["样本外改善幅度不足"],
                strengths=["验证方式正确"],
                weaknesses=["模型表达能力不足"],
                writer_guidance="等待新模型运行后再形成最终结论。",
                revision_plan=plan,
            ).model_dump_json()

        agent._chat = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                SimpleNamespace(
                    content=review_payload(_revision_plan("LinearRegression")),
                    reasoning_content=None,
                ),
                SimpleNamespace(
                    content=review_payload(_revision_plan("GBRT")),
                    reasoning_content=None,
                ),
            ]
        )

        result = await agent.review_execution_result(
            question_key="ques1",
            question_text="建立预测模型",
            current_plan="使用线性回归",
            evidence={"prediction_metrics": {"primary_metric": {"name": "rmse"}}},
            rejected_models=["LinearRegression"],
            remaining_runs=2,
        )

        self.assertEqual(result.verdict, "refine")
        self.assertIsNotNone(result.revision_plan)
        self.assertEqual(result.revision_plan.selected_model, "GBRT")  # type: ignore[union-attr]
        self.assertEqual(agent._chat.await_count, 2)

    async def test_manual_review_ignores_non_applicable_revision_plan(
        self,
    ) -> None:
        """人工审核不应被无意义的返修计划结构阻断。"""
        agent = ModelerAgent("task", MagicMock())
        invalid_manual_review = json.dumps(
            {
                "verdict": "manual_review",
                "summary": "机器门禁已经通过，但核心统计推断仍需人工核验后才能进入论文写作。",
                "evidence": ["分组样本外指标已完成，剩余运行次数为零。"],
                "strengths": ["验证折按独立主体隔离。"],
                "weaknesses": ["显著性推断证据需要人工复核。"],
                "writer_guidance": "仅报告已核验指标，并明确等待人工审核。",
                "revision_plan": {},
            },
            ensure_ascii=False,
        )
        agent._chat = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                SimpleNamespace(
                    content=invalid_manual_review,
                    reasoning_content=None,
                )
                for _ in range(3)
            ]
        )

        result = await agent.review_execution_result(
            question_key="ques1",
            question_text="建立预测模型",
            current_plan="使用两阶段回归模型",
            evidence={"quality_report": {"selected_model": "TwoStageModel"}},
            rejected_models=[],
            remaining_runs=0,
        )

        self.assertEqual(result.verdict, "manual_review")
        self.assertIsNone(result.revision_plan)
        self.assertEqual(agent._chat.await_count, 1)

    async def test_no_remaining_runs_drops_refinement_plan(self) -> None:
        """运行额度耗尽时转人工审核，并移除不可执行的返修计划。"""
        agent = ModelerAgent("task", MagicMock())
        refine_review = ModelExecutionReview(
            verdict="refine",
            summary="当前结果虽然通过最低门槛，但仍有明确的非线性模型改进空间。",
            evidence=["样本外指标仅略优于简单基线。"],
            strengths=["分组验证方式正确。"],
            weaknesses=["当前模型表达能力仍然不足。"],
            writer_guidance="等待新模型完成后再形成最终论文结论。",
            revision_plan=_revision_plan(),
        )
        agent._chat = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(
                content=refine_review.model_dump_json(),
                reasoning_content=None,
            )
        )

        result = await agent.review_execution_result(
            question_key="ques1",
            question_text="建立预测模型",
            current_plan="使用岭回归",
            evidence={"quality_report": {"selected_model": "Ridge"}},
            rejected_models=[],
            remaining_runs=0,
        )

        self.assertEqual(result.verdict, "manual_review")
        self.assertIsNone(result.revision_plan)

    async def test_invalid_execution_reviews_fall_back_without_recomputing(self) -> None:
        agent = ModelerAgent("task", MagicMock())
        agent._chat = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(
                content=json.dumps({"ques2": "free-form review"}),
                reasoning_content=None,
            )
        )

        result = await agent.review_execution_result(
            question_key="ques2",
            question_text="determine the testing week",
            current_plan="use the validated discrete-time policy",
            evidence={
                "quality_report": {
                    "status": "pass",
                    "selected_model": "validated_policy",
                }
            },
            rejected_models=[],
            remaining_runs=2,
        )

        self.assertEqual(result.verdict, "manual_review")
        self.assertIsNone(result.revision_plan)
        self.assertEqual(agent._chat.await_count, 3)
        self.assertIn("quality_report.status=pass", result.evidence)


class WorkflowModelRevisionTests(unittest.IsolatedAsyncioTestCase):
    """验证质量失败会在工作流内形成可恢复的换模闭环。"""

    async def test_passing_interrupted_artifacts_skip_coder_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = checkpoint.initialize(
                Problem(task_id="resume-pass-task", ques_all="建立预测模型")
            )
            state["questions"] = {"ques1": "建立预测模型"}
            state["ques_count"] = 1
            state["modeler_response"] = {
                "questions_solution": {"ques1": "使用分组交叉验证模型。"}
            }
            checkpoint.save(state)

            root.joinpath("ques1_support.csv").write_text(
                "metric,value\nrmse,0.8\n",
                encoding="utf-8",
            )
            root.joinpath("figure.png").write_bytes(b"figure")
            root.joinpath("ques1_quality_report.json").write_text(
                json.dumps(
                    {
                        "status": "pass",
                        "problem_type": "regression",
                        "selected_model": "GBRT",
                        "candidate_models": [
                            {"name": "mean", "role": "baseline"},
                            {"name": "GBRT", "role": "candidate"},
                        ],
                        "independent_unit": "subject",
                        "data_leakage_checks": {
                            "preprocessing_inside_folds": True,
                            "group_isolation": True,
                            "target_leakage_checked": True,
                        },
                        "robustness_checks": [
                            {"name": "bootstrap", "passed": True},
                            {"name": "holdout", "passed": True},
                        ],
                        "limitations": ["样本支持范围有限"],
                        "artifacts": ["ques1_support.csv"],
                        "paper_ready_images": ["figure.png"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            workflow = RemitWorkFlow()
            workflow.task_id = "resume-pass-task"
            workflow.work_dir = str(root)
            workflow.checkpoint = checkpoint
            workflow.code_interpreter = SimpleNamespace(
                get_code_output=lambda _key: "恢复的真实代码输出"
            )
            workflow._require_human_approval = AsyncMock()  # type: ignore[method-assign]

            contract = build_question_contract("ques1", "建立预测模型")
            coder_agent = MagicMock()
            coder_agent.run = AsyncMock(
                return_value=CoderToWriter(code_response="不应重新执行的代码结果")
            )
            modeler_agent = MagicMock()
            modeler_agent.review_execution_result = AsyncMock(
                return_value=_accepted_review()
            )
            modeler_agent.revise_after_execution = AsyncMock()
            writer_agent = MagicMock()
            writer_agent.run = AsyncMock(
                return_value=WriterResponse(
                    response_content="模型公式 $y=f(x)$，OOF R²=0.42，结果通过复核。"
                    * 30
                )
            )
            flows = MagicMock()
            flows.get_writer_prompt.return_value = "根据恢复产物写作"
            user_output = UserOutput(str(root), 1)

            with (
                patch(
                    "app.core.workflow.validate_question_deliverables",
                    return_value=DeliverableValidationReport(
                        passed=True,
                        paper_ready_images=("figure.png",),
                    ),
                ),
                patch("app.core.workflow.validate_writer_section"),
                patch(
                    "app.core.workflow.redis_manager.publish_message",
                    new=AsyncMock(),
                ),
            ):
                await workflow._solution_node(
                    key="ques1",
                    value={
                        "contract": contract,
                        "question_text": "建立预测模型",
                        "model_plan": "使用分组交叉验证模型。",
                        "coder_prompt": "重新执行预测模型",
                    },
                    state=state,
                    flows=flows,
                    config_template={},
                    modeler_agent=modeler_agent,
                    coder_agent=coder_agent,
                    writer_agent=writer_agent,
                    user_output=user_output,
                )

            coder_agent.run.assert_not_awaited()
            modeler_agent.review_execution_result.assert_not_awaited()
            writer_agent.run.assert_awaited_once()
            self.assertEqual(
                state["solution_results"]["ques1"]["modeler_review"]["verdict"],
                "accept",
            )

    async def test_unfinished_review_revision_rolls_back_to_passing_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = checkpoint.initialize(
                Problem(task_id="resume-stale-revision", ques_all="建立预测模型")
            )
            original_plan = "使用分组交叉验证的稳健线性关系模型"
            revised_plan = "改用新的GEE关系模型重新计算"
            state["questions"] = {"ques1": "建立预测模型"}
            state["ques_count"] = 1
            state["modeler_response"] = {"questions_solution": {"ques1": revised_plan}}
            state["model_revision_history"] = {
                "ques1": [
                    {
                        "attempt": 1,
                        "trigger": "modeler_review",
                        "previous_plan": original_plan,
                        "revision_plan": {
                            "selected_model": "new_gee_model",
                            "revised_strategy": revised_plan,
                        },
                    }
                ]
            }
            state["model_execution_reviews"] = {
                "ques1": [{"attempt": 1, "review": {"verdict": "refine"}}]
            }
            checkpoint.save(state)

            root.joinpath("ques1_support.csv").write_text(
                "metric,value\nrmse,0.8\n",
                encoding="utf-8",
            )
            root.joinpath("figure.png").write_bytes(b"figure")
            root.joinpath("ques1_quality_report.json").write_text(
                json.dumps(
                    {
                        "status": "pass",
                        "problem_type": "regression",
                        "selected_model": "existing_robust_linear_model",
                        "candidate_models": [
                            {"name": "mean", "role": "baseline"},
                            {
                                "name": "existing_robust_linear_model",
                                "role": "candidate",
                            },
                        ],
                        "independent_unit": "subject",
                        "data_leakage_checks": {
                            "preprocessing_inside_folds": True,
                            "group_isolation": True,
                            "target_leakage_checked": True,
                        },
                        "robustness_checks": [
                            {"name": "bootstrap", "passed": True},
                            {"name": "holdout", "passed": True},
                        ],
                        "limitations": ["样本支持范围有限"],
                        "artifacts": ["ques1_support.csv"],
                        "paper_ready_images": ["figure.png"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            workflow = RemitWorkFlow()
            workflow.task_id = "resume-stale-revision"
            workflow.work_dir = str(root)
            workflow.checkpoint = checkpoint
            workflow.code_interpreter = SimpleNamespace(
                get_code_output=lambda _key: "恢复的真实代码输出"
            )
            workflow._require_human_approval = AsyncMock()  # type: ignore[method-assign]

            contract = build_question_contract("ques1", "建立预测模型")
            coder_agent = MagicMock()
            coder_agent.run = AsyncMock()
            modeler_agent = MagicMock()
            modeler_agent.review_execution_result = AsyncMock(
                return_value=_accepted_review()
            )
            writer_agent = MagicMock()
            writer_agent.run = AsyncMock(
                return_value=WriterResponse(
                    response_content=(
                        "模型公式 $y=f(x)$，OOF R²=0.42，结果通过复核。" * 30
                    )
                )
            )
            flows = MagicMock()
            flows.get_writer_prompt.return_value = "根据恢复产物写作"
            user_output = UserOutput(str(root), 1)

            with (
                patch(
                    "app.core.workflow.validate_question_deliverables",
                    return_value=DeliverableValidationReport(
                        passed=True,
                        paper_ready_images=("figure.png",),
                    ),
                ),
                patch("app.core.workflow.validate_writer_section"),
                patch(
                    "app.core.workflow.redis_manager.publish_message",
                    new=AsyncMock(),
                ),
            ):
                await workflow._solution_node(
                    key="ques1",
                    value={
                        "contract": contract,
                        "question_text": "建立预测模型",
                        "model_plan": revised_plan,
                        "coder_prompt": "重新运行预测模型",
                    },
                    state=state,
                    flows=flows,
                    config_template={},
                    modeler_agent=modeler_agent,
                    coder_agent=coder_agent,
                    writer_agent=writer_agent,
                    user_output=user_output,
                )

            coder_agent.run.assert_not_awaited()
            review_call = modeler_agent.review_execution_result.await_args.kwargs
            self.assertEqual(review_call["current_plan"], original_plan)
            self.assertEqual(state["model_revision_history"]["ques1"], [])
            self.assertEqual(state["model_execution_reviews"]["ques1"][0]["attempt"], 1)
            self.assertEqual(
                state["modeler_response"]["questions_solution"]["ques1"],
                original_plan,
            )

    async def test_poor_result_returns_to_modeler_and_persists_new_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = checkpoint.initialize(
                Problem(task_id="revision-task", ques_all="建立预测模型")
            )
            state["questions"] = {"ques1": "建立浓度关系模型"}
            state["ques_count"] = 1
            state["modeler_response"] = {
                "questions_solution": {"ques1": "使用线性回归作为主模型。"}
            }
            checkpoint.save(state)

            workflow = RemitWorkFlow()
            workflow.task_id = "revision-task"
            workflow.work_dir = str(root)
            workflow.checkpoint = checkpoint
            workflow.code_interpreter = SimpleNamespace(
                get_code_output=lambda _key: "真实代码输出"
            )
            workflow._require_human_approval = AsyncMock()  # type: ignore[method-assign]

            contract = build_question_contract(
                "ques1", "建立浓度关系模型并检验预测性能"
            )
            call_count = 0

            async def run_coder(**_kwargs) -> CoderToWriter:
                nonlocal call_count
                call_count += 1
                selected = "LinearRegression" if call_count == 1 else "GBRT"
                root.joinpath("result.txt").write_text("result", encoding="utf-8")
                root.joinpath("ques1_quality_report.json").write_text(
                    json.dumps(
                        {
                            "status": "pass",
                            "problem_type": "regression",
                            "selected_model": selected,
                            "candidate_models": [],
                            "artifacts": ["result.txt"],
                            "paper_ready_images": [],
                        }
                    ),
                    encoding="utf-8",
                )
                root.joinpath("ques1_prediction_metrics.json").write_text(
                    json.dumps(
                        {
                            "primary_metric": {
                                "name": "rmse",
                                "model_value": 2.0 if call_count == 1 else 0.8,
                                "baseline_value": 1.0,
                                "higher_is_better": False,
                            },
                            "secondary_metrics": {
                                "r2": -0.3 if call_count == 1 else 0.55
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return CoderToWriter(code_response=f"run-{call_count}")

            coder_agent = MagicMock()
            coder_agent.run = AsyncMock(side_effect=run_coder)
            modeler_agent = MagicMock()
            modeler_agent.revise_after_execution = AsyncMock(
                return_value=_revision_plan()
            )
            modeler_agent.review_execution_result = AsyncMock(
                return_value=_accepted_review()
            )
            writer_agent = MagicMock()
            writer_agent.run = AsyncMock(
                return_value=WriterResponse(
                    response_content="模型公式 $y=f(x)$，OOF R²=0.55，结果通过验证。"
                    * 30
                )
            )
            flows = MagicMock()
            flows.get_writer_prompt.return_value = "根据真实结果写作"
            user_output = UserOutput(str(root), 1)

            with (
                patch(
                    "app.core.workflow.validate_question_deliverables",
                    side_effect=[
                        ModelQualityValidationError(
                            "分组 OOF R² 必须大于0，当前为 -0.3"
                        ),
                        DeliverableValidationReport(passed=True),
                    ],
                ),
                patch("app.core.workflow.validate_writer_section"),
                patch(
                    "app.core.workflow.redis_manager.publish_message",
                    new=AsyncMock(),
                ),
            ):
                await workflow._solution_node(
                    key="ques1",
                    value={
                        "contract": contract,
                        "question_text": "建立浓度关系模型",
                        "model_plan": "使用线性回归作为主模型。",
                        "coder_prompt": "执行原线性回归方案",
                    },
                    state=state,
                    flows=flows,
                    config_template={},
                    modeler_agent=modeler_agent,
                    coder_agent=coder_agent,
                    writer_agent=writer_agent,
                    user_output=user_output,
                )

            self.assertEqual(coder_agent.run.await_count, 2)
            second_prompt = coder_agent.run.await_args_list[1].kwargs["prompt"]
            self.assertIn("建模手根据真实运行结果发起换模", second_prompt)
            self.assertIn("GBRT", second_prompt)
            self.assertIn("禁止降低门槛", second_prompt)
            modeler_kwargs = modeler_agent.revise_after_execution.await_args.kwargs
            self.assertIn("LinearRegression", modeler_kwargs["rejected_models"])
            self.assertEqual(
                modeler_kwargs["evidence"]["prediction_metrics"]["secondary_metrics"][
                    "r2"
                ],
                -0.3,
            )
            persisted = checkpoint.load()
            history = persisted["model_revision_history"]["ques1"]
            self.assertEqual(len(history), 1)
            self.assertEqual(
                persisted["modeler_response"]["questions_solution"]["ques1"],
                _revision_plan().revised_strategy,
            )
            self.assertIn("solve:ques1", persisted["completed_nodes"])

    async def test_passing_result_is_still_reviewed_and_refined(self) -> None:
        """机器门禁通过但建模手认为仍弱时，也必须换模重跑。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = checkpoint.initialize(
                Problem(task_id="review-task", ques_all="建立预测模型")
            )
            state["questions"] = {"ques1": "建立预测模型"}
            state["ques_count"] = 1
            state["modeler_response"] = {
                "questions_solution": {"ques1": "使用岭回归。"}
            }
            checkpoint.save(state)

            workflow = RemitWorkFlow()
            workflow.task_id = "review-task"
            workflow.work_dir = str(root)
            workflow.checkpoint = checkpoint
            workflow.code_interpreter = SimpleNamespace(
                get_code_output=lambda _key: "真实执行输出"
            )
            workflow._require_human_approval = AsyncMock()  # type: ignore[method-assign]
            contract = build_question_contract("ques1", "建立预测模型")

            call_count = 0

            async def run_coder(**_kwargs) -> CoderToWriter:
                nonlocal call_count
                call_count += 1
                selected = "Ridge" if call_count == 1 else "GBRT"
                root.joinpath("notebook.ipynb").write_text("{}", encoding="utf-8")
                root.joinpath("ques1_quality_report.json").write_text(
                    json.dumps(
                        {
                            "status": "pass",
                            "problem_type": "regression",
                            "selected_model": selected,
                            "candidate_models": ["MedianBaseline", selected],
                            "artifacts": ["ques1_predictions.csv"],
                            "paper_ready_images": [],
                        }
                    ),
                    encoding="utf-8",
                )
                root.joinpath("ques1_prediction_metrics.json").write_text(
                    json.dumps(
                        {
                            "primary_metric": {
                                "name": "rmse",
                                "model_value": 0.92 if call_count == 1 else 0.72,
                                "baseline_value": 1.0,
                                "higher_is_better": False,
                            },
                            "secondary_metrics": {
                                "r2": 0.08 if call_count == 1 else 0.42
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return CoderToWriter(code_response=f"run-{call_count}")

            refine_review = ModelExecutionReview(
                verdict="refine",
                summary="当前结果虽越过最低门槛，但相对基线改善有限，仍有明确换模空间。",
                evidence=["RMSE 仅较基线改善 8%"],
                strengths=["分组验证口径正确"],
                weaknesses=["非线性关系未被充分表达"],
                writer_guidance="暂不将岭回归写成最终模型。",
                revision_plan=_revision_plan(),
            )
            modeler_agent = MagicMock()
            modeler_agent.review_execution_result = AsyncMock(
                side_effect=[refine_review, _accepted_review()]
            )
            modeler_agent.revise_after_execution = AsyncMock()
            coder_agent = MagicMock()
            coder_agent.run = AsyncMock(side_effect=run_coder)
            writer_agent = MagicMock()
            writer_agent.run = AsyncMock(
                return_value=WriterResponse(
                    response_content="模型公式 $y=f(x)$，OOF R²=0.42，结果通过复核。"
                    * 30
                )
            )
            flows = MagicMock()
            flows.get_writer_prompt.return_value = "根据最终复核结果写作"
            user_output = UserOutput(str(root), 1)
            publish = AsyncMock()

            with (
                patch(
                    "app.core.workflow.validate_question_deliverables",
                    side_effect=[
                        DeliverableValidationReport(passed=True),
                        DeliverableValidationReport(passed=True),
                    ],
                ),
                patch("app.core.workflow.validate_writer_section"),
                patch(
                    "app.core.workflow.redis_manager.publish_message",
                    new=publish,
                ),
            ):
                await workflow._solution_node(
                    key="ques1",
                    value={
                        "contract": contract,
                        "question_text": "建立预测模型",
                        "model_plan": "使用岭回归。",
                        "coder_prompt": "执行岭回归方案",
                    },
                    state=state,
                    flows=flows,
                    config_template={},
                    modeler_agent=modeler_agent,
                    coder_agent=coder_agent,
                    writer_agent=writer_agent,
                    user_output=user_output,
                )

            self.assertEqual(coder_agent.run.await_count, 2)
            self.assertEqual(modeler_agent.review_execution_result.await_count, 2)
            self.assertEqual(modeler_agent.revise_after_execution.await_count, 0)
            self.assertIn("GBRT", coder_agent.run.await_args_list[1].kwargs["prompt"])
            summary_messages = [
                call.args[1]
                for call in publish.await_args_list
                if isinstance(call.args[1], ExecutionSummaryMessage)
            ]
            self.assertEqual(len(summary_messages), 1)
            self.assertEqual(summary_messages[0].status, "refined")
            self.assertEqual(summary_messages[0].selected_model, "GBRT")
            self.assertEqual(summary_messages[0].metrics[0].name, "rmse")
            self.assertEqual(summary_messages[0].revision_count, 1)
            persisted = checkpoint.load()
            self.assertEqual(
                persisted["modeler_response"]["questions_solution"]["ques1"],
                _revision_plan().revised_strategy,
            )
            self.assertEqual(
                len(persisted["model_execution_reviews"]["ques1"]),
                2,
            )

    async def test_revision_prompt_preserves_original_quality_contract(self) -> None:
        prompt = RemitWorkFlow._build_model_revision_coder_prompt(
            question_text="预测问题",
            gate_error="OOF R² 为负",
            revision_plan=_revision_plan().model_dump(mode="json"),
            contract_prompt="原质量门槛",
        )

        self.assertIn("GBRT", prompt)
        self.assertIn("原质量门槛", prompt)
        self.assertNotIn("降低门槛", _revision_plan().acceptance_criteria)


if __name__ == "__main__":
    unittest.main()
