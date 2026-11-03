"""审批返修、恢复与自动探索定案的行为回归，全部使用本地假模型。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.setting import settings
from app.core import workflow as workflow_module
from app.core import workflow_solution
from app.core.llm.errors import NonRetryableLLMError
from app.core.deliverable_contract import build_stage_contract
from app.core.pilot import PilotValidationError
from app.core.workflow import RemitWorkFlow, WorkflowApprovalRequired
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.models.user_output import UserOutput
from app.schemas.A2A import (
    CoordinatorToModeler,
    CoderToWriter,
    ModelExecutionReview,
    ModelerToCoder,
    PilotDecision,
    PilotPlan,
    WriterResponse,
)
from app.schemas.request import Problem


def _planning_state(checkpoint: WorkflowCheckpoint) -> dict:
    state = checkpoint.initialize(Problem(task_id="revision-execution"))
    coordinator = CoordinatorToModeler(questions={"ques1": "评价方案"}, ques_count=1)
    state.update(
        questions=coordinator.questions,
        ques_count=1,
        coordinator_response=coordinator.model_dump(mode="json"),
        analysis_response=coordinator.model_dump(mode="json"),
        modeler_response=ModelerToCoder(
            questions_solution={"ques1": "OLD_MODEL"}
        ).model_dump(mode="json"),
        completed_nodes=["coordinator", "research", "analysis", "modeler"],
        approved_nodes=["analysis", "modeler"],
    )
    checkpoint.save(state)
    return state


class WorkflowRevisionExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_result_review_resumes_without_reexecuting_valid_code(
        self,
    ):
        from app.core.llm.errors import ModelStageBudgetExceeded
        from app.services.call_ledger import budget_phase

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = _planning_state(checkpoint)
            workflow = RemitWorkFlow()
            workflow.task_id = "revision-execution"
            workflow.work_dir = tmp
            workflow.checkpoint = checkpoint
            workflow.code_interpreter = SimpleNamespace(get_code_output=lambda _: "")
            root.joinpath("eda_cleaned.csv").write_text("x\n1\n")
            report = {
                "status": "pass",
                "problem_type": "eda",
                "selected_model": "cleaning",
                "candidate_models": [],
                "robustness_checks": [{"name": "rows", "passed": True}],
                "artifacts": ["eda_cleaned.csv"],
                "paper_ready_images": [],
                "type_specific": {
                    "raw_rows": 1,
                    "cleaned_rows": 1,
                    "missingness_checked": True,
                    "duplicates_checked": True,
                    "outliers_assessed": True,
                    "independent_unit_identified": True,
                },
            }
            root.joinpath("eda_quality_report.json").write_text(json.dumps(report))
            coder = SimpleNamespace(run=AsyncMock())
            accepted = ModelExecutionReview(
                verdict="accept",
                summary="实际落盘的数据检查与质量报告均已通过核验，可以继续后续阶段。",
                evidence=["数据已核验"],
                strengths=["数据完整"],
                weaknesses=[],
                writer_guidance="仅按实际已经核验的数据结果写作，保留证据局限。",
            )
            phases = []

            async def review(**kwargs):
                phases.append(budget_phase(workflow.task_id))
                if len(phases) == 1:
                    raise ModelStageBudgetExceeded(
                        used_calls=24,
                        call_limit=24,
                        elapsed_seconds=900,
                        seconds_limit=900,
                    )
                return accepted

            modeler = SimpleNamespace(
                review_execution_result=AsyncMock(side_effect=review)
            )
            flows = MagicMock()
            flows.get_writer_prompt.return_value = "write actual evidence"
            arguments = dict(
                key="eda",
                value={
                    "contract": build_stage_contract("eda"),
                    "question_text": "核验数据",
                    "model_plan": "核验",
                    "coder_prompt": "计算",
                },
                flows=flows,
                config_template={},
                modeler_agent=modeler,
                coder_agent=coder,
                writer_agent=SimpleNamespace(run=AsyncMock()),
                user_output=UserOutput(tmp, 1),
            )
            with (
                patch.object(settings, "HIL_ENABLED", False),
                patch.object(
                    workflow_module.redis_manager, "publish_message", new=AsyncMock()
                ),
            ):
                with self.assertRaises(ModelStageBudgetExceeded):
                    await workflow._solution_node(state=state, **arguments)
                self.assertEqual(checkpoint.load()["pending_model_review"], "solve:eda")
                self.assertNotIn("solve:eda", checkpoint.load()["completed_nodes"])
                checkpoint.mark_status("stopped")
                resumed = checkpoint.prepare_resume(checkpoint.load(), "solve:eda")
                await workflow._solution_node(state=resumed, **arguments)
            coder.run.assert_not_awaited()
            self.assertEqual(phases, ["review", "review"])
            self.assertEqual(budget_phase(workflow.task_id), "work")
            self.assertIn("solve:eda", checkpoint.load()["completed_nodes"])
            self.assertNotIn("pending_model_review", checkpoint.load())

    async def test_pilot_config_error_is_not_skipped_as_completed(self):
        error = NonRetryableLLMError("missing workspaceid")
        workflow = RemitWorkFlow()
        workflow.checkpoint = MagicMock()
        workflow.checkpoint.consume_revision_feedback.return_value = ""
        workflow.code_interpreter = SimpleNamespace(language="matlab")
        workflow.questions = {"ques1": "test"}
        workflow.task_id = "test-config-error"
        modeler = SimpleNamespace(design_pilot_plan=AsyncMock(side_effect=error))
        coder = SimpleNamespace(run=AsyncMock())
        state = {}
        with (
            patch.object(workflow, "_start_node", new=AsyncMock()),
            patch.object(workflow, "_check_cancelled", new=AsyncMock()),
            patch.object(workflow_module, "publish_activity", new=AsyncMock()),
            patch.object(
                workflow_module.redis_manager, "publish_message", new=AsyncMock()
            ),
        ):
            with self.assertRaises(NonRetryableLLMError):
                await workflow._pilot_node(
                    state,
                    modeler,
                    coder,
                    ModelerToCoder(questions_solution={"ques1": "plan"}),
                )
        self.assertNotIn("pilot_skipped", state)
        coder.run.assert_not_awaited()

    async def test_review_revision_reexecutes_solver_then_can_be_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = _planning_state(checkpoint)
            # 兼容旧检查点的写作中断恢复。
            state["workflow_features"].remove("separate_writing")
            workflow = RemitWorkFlow()
            workflow.task_id = "revision-execution"
            workflow.work_dir = tmp
            workflow.checkpoint = checkpoint
            workflow.code_interpreter = SimpleNamespace(get_code_output=lambda _: "")
            run_count = 0

            async def run_code(**_kwargs):
                nonlocal run_count
                run_count += 1
                root.joinpath("eda_clean.csv").write_text(f"x\n{run_count}\n")
                report = {
                    "status": "pass",
                    "problem_type": "eda",
                    "selected_model": f"cleaning_rule_{run_count}",
                    "candidate_models": [],
                    "robustness_checks": [
                        {"name": "row reconciliation", "passed": True}
                    ],
                    "artifacts": ["eda_clean.csv"],
                    "paper_ready_images": [],
                    "type_specific": {
                        "raw_rows": 100,
                        "cleaned_rows": 90,
                        "missingness_checked": True,
                        "duplicates_checked": True,
                        "outliers_assessed": True,
                        "independent_unit_identified": True,
                    },
                }
                root.joinpath("eda_quality_report.json").write_text(json.dumps(report))
                return CoderToWriter(
                    code_response=f"executed cleaning rule {run_count}"
                )

            coder = SimpleNamespace(run=AsyncMock(side_effect=run_code))
            modeler = SimpleNamespace(
                review_execution_result=AsyncMock(
                    return_value=ModelExecutionReview(
                        verdict="accept",
                        summary="清洗结果已通过独立分析单位和缺失重复检查，可以写入论文。",
                        evidence=["独立分析单位及缺失和重复检查完成"],
                        strengths=["有完整证据"],
                        weaknesses=[],
                        writer_guidance="如实报告已执行的清洗规则及局限。",
                    )
                )
            )
            writer_interrupted = False

            async def write_result(*_args, **_kwargs):
                nonlocal writer_interrupted
                if run_count == 2 and not writer_interrupted:
                    writer_interrupted = True
                    raise RuntimeError("writer connection interrupted")
                return WriterResponse(response_content="清洗结果已根据落盘证据报告。")

            writer = SimpleNamespace(run=AsyncMock(side_effect=write_result))
            flows = MagicMock()
            flows.get_writer_prompt.return_value = "根据执行结果撰写EDA"
            value = {
                "contract": build_stage_contract("eda"),
                "question_text": "数据清洗与探索性分析",
                "model_plan": "clean data",
                "coder_prompt": "执行数据清洗",
            }

            with (
                patch.object(settings, "HIL_ENABLED", True),
                patch.object(settings, "HIL_CHECKPOINTS", {"code_review": True}),
                patch.object(
                    workflow_module.redis_manager, "publish_message", new=AsyncMock()
                ),
                patch.object(workflow_solution, "validate_writer_section"),
            ):
                for revision in range(2):
                    if revision == 1:
                        with self.assertRaisesRegex(RuntimeError, "writer connection"):
                            await workflow._solution_node(
                                key="eda",
                                value=value,
                                state=state,
                                flows=flows,
                                config_template={},
                                modeler_agent=modeler,
                                coder_agent=coder,
                                writer_agent=writer,
                                user_output=UserOutput(tmp, 1),
                            )
                        checkpoint.mark_status("failed")
                        state = checkpoint.prepare_resume(
                            checkpoint.load(), "solve:eda"
                        )
                        # 保留的是返修后新生成的证据，续跑只需恢复写作。
                        self.assertTrue(
                            root.joinpath("eda_quality_report.json").is_file()
                        )
                    with self.assertRaises(WorkflowApprovalRequired):
                        await workflow._solution_node(
                            key="eda",
                            value=value,
                            state=state,
                            flows=flows,
                            config_template={},
                            modeler_agent=modeler,
                            coder_agent=coder,
                            writer_agent=writer,
                            user_output=UserOutput(tmp, 1),
                        )
                    state = checkpoint.load()
                    pending = state["pending_approval"]
                    if revision == 0:
                        checkpoint.request_revision(
                            state,
                            pending["checkpoint_id"],
                            "改用第二种规则重新清洗",
                        )
                        self.assertFalse(
                            root.joinpath("eda_quality_report.json").exists()
                        )
                        self.assertFalse(root.joinpath("eda_clean.csv").exists())
                        # 从落盘状态重新载入，模拟审批请求结束后的后台续跑。
                        state = checkpoint.load()
                    else:
                        checkpoint.approve(state, pending["checkpoint_id"])

            self.assertEqual(run_count, 2)
            self.assertIn(
                "改用第二种规则重新清洗", coder.run.await_args.kwargs["prompt"]
            )
            self.assertEqual(root.joinpath("eda_clean.csv").read_text(), "x\n2\n")
            saved = checkpoint.load()
            self.assertIn("solve:eda", saved["approved_nodes"])
            self.assertNotIn("solve:eda", saved["revision_feedback"])
            self.assertEqual(saved["approval_history"][-1]["decision"], "approve")

    async def test_incomplete_revision_uses_focused_repair_prompt(self):
        """未完成节点续跑时应先修磁盘产物，不得重新执行完整探索任务。"""
        await self._assert_incomplete_revision_repair(has_evidence=True)

    async def test_empty_eda_revision_reserves_execution_for_debugging(self):
        """零交付不能套用两次小修预算，否则读取后首次报错就无法修复。"""
        await self._assert_incomplete_revision_repair(has_evidence=False)

    async def test_manual_review_revision_preserves_files_but_runs_requested_fix(self):
        await self._assert_incomplete_revision_repair(
            has_evidence=True, manual_review=True
        )

    async def test_failed_repair_cannot_submit_stale_manual_review_report(self):
        await self._assert_incomplete_revision_repair(
            has_evidence=True, manual_review=True, leave_stale_once=True
        )

    async def _assert_incomplete_revision_repair(
        self, *, has_evidence, manual_review=False, leave_stale_once=False
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = _planning_state(checkpoint)
            workflow = RemitWorkFlow()
            workflow.task_id = "revision-execution"
            workflow.work_dir = tmp
            workflow.checkpoint = checkpoint
            workflow.code_interpreter = SimpleNamespace(get_code_output=lambda _: "")
            if has_evidence:
                root.joinpath("eda_cleaned.csv").write_text("x\n1\n", encoding="utf-8")
            report = {
                "status": "pass",
                "problem_type": "错误类型",
                "selected_model": "cleaning_rule",
                "candidate_models": [],
                "robustness_checks": [{"name": "row reconciliation", "passed": True}],
                "artifacts": ["eda_cleaned.csv"],
                "paper_ready_images": [],
                "type_specific": {
                    "raw_rows": 100,
                    "cleaned_rows": 90,
                    "missingness_checked": True,
                    "duplicates_checked": True,
                    "outliers_assessed": True,
                    "independent_unit_identified": True,
                },
            }
            report_path = root / "eda_quality_report.json"
            if manual_review:
                report.update(
                    status="manual_review",
                    problem_type="eda",
                    manual_review_required=True,
                    failure_reason="巡航核验失败",
                )
                report_path.write_text(json.dumps(report), encoding="utf-8")
            checkpoint.start_node(state, "solve:eda")
            if manual_review:
                state["completed_nodes"].append("solve:eda")
            pending = checkpoint.request_approval(
                state,
                "solve:eda",
                summary="缺少 eda_quality_report.json",
                artifacts=["eda_cleaned.csv"] if has_evidence else [],
                allow_incomplete=not manual_review,
                quality_report=report if manual_review else {},
            )
            state = checkpoint.request_revision(
                state,
                pending["checkpoint_id"],
                "只把 problem_type 修复为 eda。",
            )
            if manual_review:
                self.assertTrue(root.joinpath("eda_cleaned.csv").is_file())
                self.assertTrue(report_path.is_file())

            attempts = 0

            async def repair_code(**_kwargs):
                nonlocal attempts
                attempts += 1
                if leave_stale_once and attempts == 1:
                    return CoderToWriter(code_response="代码报错，报告未更新")
                root.joinpath("eda_cleaned.csv").write_text("x\n1\n", encoding="utf-8")
                report["problem_type"] = "eda"
                report["status"] = "pass"
                report.pop("manual_review_required", None)
                report.pop("failure_reason", None)
                report_path.write_text(json.dumps(report), encoding="utf-8")
                return CoderToWriter(code_response="已增量补齐质量报告")

            coder = SimpleNamespace(run=AsyncMock(side_effect=repair_code))
            modeler = SimpleNamespace(
                review_execution_result=AsyncMock(
                    return_value=ModelExecutionReview(
                        verdict="accept",
                        summary="现有数据清洗证据完整，质量报告与落盘产物已经通过复核。",
                        evidence=["质量报告通过"],
                        strengths=["复用了已落盘证据"],
                        weaknesses=[],
                        writer_guidance="如实报告已执行的清洗规则、核验结果和局限。",
                    )
                )
            )
            writer = SimpleNamespace(
                run=AsyncMock(
                    return_value=WriterResponse(response_content="清洗结果已核验。")
                )
            )
            flows = MagicMock()
            flows.get_writer_prompt.return_value = "根据执行结果撰写EDA"
            value = {
                "contract": build_stage_contract("eda"),
                "question_text": "数据清洗与探索性分析",
                "model_plan": "clean data",
                "coder_prompt": "从头执行完整数据清洗与探索",
            }

            with (
                patch.object(settings, "HIL_ENABLED", False),
                patch.object(
                    workflow_module.redis_manager, "publish_message", new=AsyncMock()
                ),
                patch.object(workflow_solution, "validate_writer_section"),
            ):
                await workflow._solution_node(
                    key="eda",
                    value=value,
                    state=state,
                    flows=flows,
                    config_template={},
                    modeler_agent=modeler,
                    coder_agent=coder,
                    writer_agent=writer,
                    user_output=UserOutput(tmp, 1),
                )

            repair_prompt = coder.run.await_args_list[0].kwargs["prompt"]
            self.assertTrue(repair_prompt.startswith("【断点返修模式"))
            self.assertIn("只把 problem_type 修复为 eda", repair_prompt)
            self.assertNotIn("从头执行完整数据清洗与探索", repair_prompt)
            self.assertIn(
                "第一次 execute_code"
                if has_evidence and not manual_review
                else "至少预留一次报错修复",
                repair_prompt,
            )
            self.assertIn(value["question_text"], repair_prompt)
            self.assertIn(value["model_plan"], repair_prompt)
            self.assertIn("不新增预测目标", repair_prompt)
            self.assertEqual(
                coder.run.await_args.kwargs["max_code_executions"],
                2 if has_evidence and not manual_review else 4,
            )
            self.assertEqual(coder.run.await_count, 2 if leave_stale_once else 1)
            if leave_stale_once:
                self.assertIn("仍是旧版本", coder.run.await_args.kwargs["prompt"])
                modeler.review_execution_result.assert_awaited_once()

    async def test_pilot_selection_reaches_solver_without_an_approval_restart(self):
        for hil_enabled, pilot_fails in ((False, False), (True, False), (False, True)):
            with self.subTest(hil_enabled=hil_enabled, pilot_fails=pilot_fails):
                await self._assert_pilot_plan_reaches_solver(hil_enabled, pilot_fails)

    async def _assert_pilot_plan_reaches_solver(self, hil_enabled, pilot_fails):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = WorkflowCheckpoint(root)
            state = _planning_state(checkpoint)
            checkpoint.complete_node(state, "solve:eda")
            selected_strategy = "NEW_SELECTED_MODEL: 按主体分组，采用同一验证划分比较候选模型，选择通过稳健性检查的方案正式求解。"
            pilot_plan = PilotPlan.model_validate(
                {
                    "questions": {
                        "ques1": {
                            "candidates": [
                                {
                                    "name": "OLD_MODEL",
                                    "role": "baseline",
                                    "approach": "使用原始简单方案作为基线",
                                },
                                {
                                    "name": "NEW_SELECTED_MODEL",
                                    "role": "candidate",
                                    "approach": "采用新的候选方案进行求解",
                                },
                            ],
                            "sampling_rule": "按主体取相同样本",
                            "primary_metric": "rmse",
                        }
                    }
                }
            )
            pilot_decision = PilotDecision.model_validate(
                {
                    "questions": {
                        "ques1": {
                            "selected_model": "NEW_SELECTED_MODEL",
                            "revised_strategy": selected_strategy,
                            "justification": "真实探索结果优于原方案",
                        }
                    }
                }
            )
            modeler = SimpleNamespace(
                design_pilot_plan=AsyncMock(return_value=pilot_plan),
                finalize_with_pilot=AsyncMock(return_value=pilot_decision),
            )
            if pilot_fails:
                modeler.design_pilot_plan.side_effect = PilotValidationError(
                    "候选比较未满足最低要求"
                )

            async def run_pilot(**_kwargs):
                root.joinpath(_kwargs["required_files"][0]).write_text(
                    json.dumps(
                        {
                            "questions": {
                                "ques1": {
                                    "sample_description": "same independent subjects",
                                    "candidates": [
                                        {
                                            "name": name,
                                            "metric_name": "rmse",
                                            "metric_value": metric,
                                            "runtime_seconds": 1,
                                            "ran_ok": True,
                                        }
                                        for name, metric in (
                                            ("OLD_MODEL", 2.0),
                                            ("NEW_SELECTED_MODEL", 1.0),
                                        )
                                    ],
                                }
                            }
                        }
                    )
                )

            coder = SimpleNamespace(
                run=AsyncMock(side_effect=run_pilot), append_chat_history=AsyncMock()
            )
            interpreter = SimpleNamespace(language="python", cleanup=AsyncMock())
            workflow = RemitWorkFlow()

            async def initialize(_state):
                workflow.code_interpreter = interpreter

            solve = AsyncMock()
            with (
                patch.object(settings, "HIL_ENABLED", hil_enabled),
                patch.object(settings, "HIL_CHECKPOINTS", {"model_selection": False}),
                patch.object(settings, "MODEL_COUNCIL_ENABLED", False),
                patch.object(workflow_module, "create_work_dir", return_value=tmp),
                patch.object(workflow_module, "LLMFactory") as factory,
                patch.object(workflow_module, "ModelerAgent", return_value=modeler),
                patch.object(
                    workflow_module.redis_manager, "publish_message", new=AsyncMock()
                ),
                patch.object(
                    workflow,
                    "_initialize_interpreter",
                    new=AsyncMock(side_effect=initialize),
                ),
                patch.object(workflow, "_new_coder_agent", return_value=coder),
                patch.object(
                    workflow, "_new_writer_agent", return_value=SimpleNamespace()
                ),
                patch.object(workflow, "_solution_node", new=solve),
                patch.object(workflow, "_write_chapters_parallel", new=AsyncMock()),
                patch.object(workflow, "_finalize_node", new=AsyncMock()),
            ):
                factory.return_value.get_modeling_llms.return_value = (None, None, None)
                factory.return_value.get_all_llms.return_value = (
                    None,
                    None,
                    None,
                    None,
                )
                await workflow.execute(
                    Problem(task_id="revision-execution"), continue_existing=True
                )

            question_call = next(
                call.kwargs
                for call in solve.await_args_list
                if call.kwargs["key"] == "ques1"
            )
            expected = "OLD_MODEL" if pilot_fails else selected_strategy
            self.assertEqual(question_call["value"]["model_plan"], expected)
            self.assertIn(expected, question_call["value"]["coder_prompt"])
            persisted = checkpoint.load()
            if pilot_fails:
                self.assertEqual(
                    persisted["node_outcomes"]["pilot"]["status"], "skipped"
                )
            self.assertEqual(
                persisted["modeler_response"]["questions_solution"]["ques1"], expected
            )
            self.assertIsNone(persisted["pending_approval"])
            interpreter.cleanup.assert_awaited_once()
