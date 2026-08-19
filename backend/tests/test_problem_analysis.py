"""题目理解与数据核验工作流的行为测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.agents.coordinator_agent import CoordinatorAgent
from app.core.agents.modeler_agent import ModelerAgent
from app.core.llm.types import StandardResponse
from app.core.workflow import RemitWorkFlow, WorkflowApprovalRequired
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.schemas.A2A import CoordinatorToModeler, QuestionAnalysis
from app.schemas.request import Problem


class ProblemAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_coordinator_retries_when_any_question_analysis_is_missing(
        self,
    ) -> None:
        base = {
            "title": "两问测试",
            "background": "验证结构完整性",
            "ques_count": 2,
            "ques1": "问题1：预测。",
            "ques2": "问题2：优化。",
            "analysis_summary": "先预测再优化。",
        }
        complete_analysis = {
            "objective": "形成可验证结果",
            "input_data": ["附件数据"],
            "decision_variables": ["模型参数"],
            "constraints": ["满足物理边界"],
            "expected_outputs": ["结果表"],
            "dependencies": ["依赖上一问"],
            "risks": ["过拟合"],
            "validation_requirements": ["样本外验证"],
            "data_evidence": [],
        }
        llm = MagicMock()
        llm.chat = AsyncMock(
            side_effect=[
                StandardResponse(
                    content=json.dumps(
                        {**base, "question_analyses": {"ques1": complete_analysis}},
                        ensure_ascii=False,
                    )
                ),
                StandardResponse(
                    content=json.dumps(
                        {
                            **base,
                            "question_analyses": {
                                "ques1": complete_analysis,
                                "ques2": complete_analysis,
                            },
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )

        result = await CoordinatorAgent("analysis-task", llm).run("两道原题")

        self.assertEqual(set(result.question_analyses), {"ques1", "ques2"})
        self.assertEqual(llm.chat.await_count, 2)

    async def test_modeler_receives_data_verified_question_analysis(self) -> None:
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=StandardResponse(
                content=json.dumps(
                    {
                        "eda": (
                            "核验附件字段、缺失值、独立样本单位和目标分布，识别异常记录与重复测量，"
                            "并形成包含统计摘要、可视化和处理规则的可复现数据质量报告。"
                        ),
                        "ques1": (
                            "依据校正后的目标、变量、约束和风险建立简单基线与候选模型，采用主体分组验证，"
                            "比较相同划分下的样本外误差、稳定性与计算成本，再确定最终路线。"
                        ),
                        "sensitivity_analysis": (
                            "系统扰动关键输入、模型参数与数据切分，重复计算主要指标和置信区间，"
                            "报告结论稳定区间、边界条件和失败情景，避免结论依赖单次随机划分。"
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        )
        agent = ModelerAgent("analysis-task", llm)
        coordinator = CoordinatorToModeler(
            questions={"ques_count": 1, "ques1": "问题1：预测RSMT。"},
            ques_count=1,
            analysis_summary="附件核验后确认应按连接组分组验证。",
            question_analyses={
                "ques1": QuestionAnalysis(
                    objective="预测RSMT",
                    input_data=["附件1坐标"],
                    decision_variables=["代理参数"],
                    constraints=["低复杂度"],
                    expected_outputs=["逐组预测值"],
                    dependencies=["问题2复用目标函数"],
                    risks=["连接组泄漏"],
                    validation_requirements=["按连接组分组交叉验证"],
                    data_evidence=["附件1包含500个连接组"],
                )
            },
        )

        await agent.run(coordinator)

        modeler_input = json.loads(agent.chat_history[1]["content"])
        self.assertEqual(
            modeler_input["question_analyses"]["ques1"]["data_evidence"],
            ["附件1包含500个连接组"],
        )
        self.assertIn("连接组分组验证", modeler_input["analysis_summary"])

    async def test_coordinator_preserves_source_and_returns_analysis_per_question(
        self,
    ) -> None:
        original = "问题1：利用附件坐标建立线长代理模型，并验证误差。"
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=StandardResponse(
                content=json.dumps(
                    {
                        "title": "VLSI 自动布局",
                        "background": "研究全局布局阶段。",
                        "ques_count": 1,
                        "ques1": original,
                        "analysis_summary": "先建立线长代理，再供布局优化调用。",
                        "question_analyses": {
                            "ques1": {
                                "objective": "学习接口坐标到 RSMT 线长的近似映射。",
                                "input_data": ["附件1接口坐标", "HPWL", "RSMT"],
                                "decision_variables": ["代理模型参数"],
                                "constraints": ["预测复杂度应低于直接构造 RSMT"],
                                "expected_outputs": ["逐组估计线长", "估计总线长"],
                                "dependencies": ["为问题2提供线长目标函数"],
                                "risks": ["多引脚组误差可能具有异方差"],
                                "validation_requirements": [
                                    "报告交叉验证 MAE 和相对误差"
                                ],
                                "data_evidence": ["题面明确附件1同时提供坐标与RSMT"],
                            }
                        },
                    },
                    ensure_ascii=False,
                )
            )
        )
        agent = CoordinatorAgent("analysis-task", llm)

        result = await agent.run(original)

        self.assertEqual(result.original_problem, original)
        self.assertEqual(result.questions["ques1"], original)
        self.assertNotIn("question_analyses", result.questions)
        analysis = result.question_analyses["ques1"]
        self.assertEqual(analysis.objective, "学习接口坐标到 RSMT 线长的近似映射。")
        self.assertEqual(analysis.expected_outputs, ["逐组估计线长", "估计总线长"])
        self.assertEqual(
            analysis.validation_requirements,
            ["报告交叉验证 MAE 和相对误差"],
        )

    async def test_data_refinement_preserves_extraction_and_adds_file_evidence(
        self,
    ) -> None:
        initial = CoordinatorToModeler(
            original_problem="问题1：建立线长代理模型。",
            questions={"ques_count": 1, "ques1": "问题1：建立线长代理模型。"},
            ques_count=1,
            analysis_summary="建立代理模型。",
            question_analyses={
                "ques1": QuestionAnalysis(
                    objective="预测 RSMT。",
                    input_data=["附件1"],
                    decision_variables=["模型参数"],
                    constraints=["低计算复杂度"],
                    expected_outputs=["预测线长"],
                    dependencies=["问题2"],
                    risks=["过拟合"],
                    validation_requirements=["留出验证"],
                )
            },
        )
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=StandardResponse(
                content=json.dumps(
                    {
                        "analysis_summary": "附件字段已核验，可建立监督学习代理。",
                        "question_analyses": {
                            "ques1": {
                                "objective": "根据真实附件字段预测 RSMT。",
                                "input_data": ["附件1.txt的坐标、HPWL和RSMT列"],
                                "decision_variables": ["代理模型参数"],
                                "constraints": ["推理复杂度低于精确RSMT"],
                                "expected_outputs": ["逐组预测", "总线长"],
                                "dependencies": ["问题2调用同一目标函数"],
                                "risks": ["引脚数量分布不均衡"],
                                "validation_requirements": ["分层交叉验证MAE"],
                                "data_evidence": ["附件1.txt包含500个连接组"],
                            }
                        },
                    },
                    ensure_ascii=False,
                )
            )
        )
        agent = CoordinatorAgent("analysis-task", llm)

        refined = await agent.refine_analysis(
            initial,
            data_profile={
                "files": [
                    {
                        "name": "附件1.txt",
                        "row_count": 500,
                        "columns": ["coordinates", "HPWL", "RSMT"],
                    }
                ]
            },
            literature_brief="RSMT代理通常需要样本外误差验证。",
        )

        self.assertEqual(refined.original_problem, initial.original_problem)
        self.assertEqual(refined.questions, initial.questions)
        self.assertEqual(
            refined.question_analyses["ques1"].data_evidence,
            ["附件1.txt包含500个连接组"],
        )
        sent_prompt = llm.chat.await_args.kwargs["history"][-1]["content"]
        self.assertIn("附件1.txt", sent_prompt)
        self.assertIn("预测 RSMT", sent_prompt)

    def test_checkpoint_returns_all_revision_feedback_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(
                Problem(task_id="analysis-task", ques_all="问题1：预测RSMT")
            )
            state["questions"] = {"ques_count": 1, "ques1": "预测RSMT"}
            state["ques_count"] = 1
            state["coordinator_response"] = {
                "questions": state["questions"],
                "ques_count": 1,
            }
            checkpoint.complete_node(state, "coordinator")
            first = checkpoint.request_approval(
                state,
                "coordinator",
                summary="第一版",
            )
            state = checkpoint.request_revision(
                state,
                first["checkpoint_id"],
                "第一条：补充决策变量。",
            )
            state["questions"] = {"ques_count": 1, "ques1": "预测RSMT"}
            state["ques_count"] = 1
            state["coordinator_response"] = {
                "questions": state["questions"],
                "ques_count": 1,
            }
            checkpoint.complete_node(state, "coordinator")
            second = checkpoint.request_approval(
                state,
                "coordinator",
                summary="第二版",
            )
            state = checkpoint.request_revision(
                state,
                second["checkpoint_id"],
                "第二条：补充数据验证。",
            )

            self.assertEqual(
                checkpoint.cumulative_revision_feedback(state, "coordinator"),
                ["第一条：补充决策变量。", "第二条：补充数据验证。"],
            )

    async def test_reanalysis_receives_previous_analysis_and_all_feedback(
        self,
    ) -> None:
        previous = CoordinatorToModeler(
            original_problem="问题1：预测RSMT。",
            questions={"ques_count": 1, "ques1": "问题1：预测RSMT。"},
            ques_count=1,
            analysis_summary="旧版理解",
            question_analyses={
                "ques1": QuestionAnalysis(
                    objective="旧目标",
                    input_data=["附件1"],
                    decision_variables=["旧参数"],
                    constraints=["旧约束"],
                    expected_outputs=["旧输出"],
                    dependencies=["问题2"],
                    risks=["旧风险"],
                    validation_requirements=["旧验证"],
                )
            },
        )
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=StandardResponse(
                content=json.dumps(
                    {
                        "title": "VLSI布局",
                        "background": "全局布局",
                        "ques_count": 1,
                        "ques1": "被模型改写的题目，不得覆盖原题。",
                        "analysis_summary": "新版理解",
                        "question_analyses": {
                            "ques1": {
                                "objective": "新目标",
                                "input_data": ["附件1"],
                                "decision_variables": ["新参数"],
                                "constraints": ["新约束"],
                                "expected_outputs": ["新输出"],
                                "dependencies": ["问题2"],
                                "risks": ["新风险"],
                                "validation_requirements": ["新验证"],
                                "data_evidence": [],
                            }
                        },
                    },
                    ensure_ascii=False,
                )
            )
        )
        agent = CoordinatorAgent("analysis-task", llm)

        revised = await agent.run(
            previous.original_problem,
            previous_analysis=previous,
            cumulative_feedback=["第一条意见", "第二条意见"],
        )

        self.assertEqual(revised.original_problem, previous.original_problem)
        self.assertEqual(revised.questions, previous.questions)
        sent_prompt = llm.chat.await_args.kwargs["history"][-1]["content"]
        self.assertIn("旧版理解", sent_prompt)
        self.assertIn("旧目标", sent_prompt)
        self.assertIn("第一条意见", sent_prompt)
        self.assertIn("第二条意见", sent_prompt)

    def test_workflow_orders_data_refinement_before_model_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(
                Problem(task_id="analysis-task", ques_all="问题1：预测RSMT")
            )
            state["questions"] = {"ques_count": 1, "ques1": "预测RSMT"}
            state["ques_count"] = 1
            state["coordinator_response"] = {
                "questions": state["questions"],
                "ques_count": 1,
            }

            order = checkpoint.node_order(state)

            self.assertEqual(
                order[:4],
                ["coordinator", "research", "analysis", "modeler"],
            )

    async def test_analysis_approval_exposes_data_verified_fields(self) -> None:
        initial = CoordinatorToModeler(
            original_problem="问题1：预测RSMT。",
            questions={"ques_count": 1, "ques1": "问题1：预测RSMT。"},
            ques_count=1,
            analysis_summary="初步理解",
            question_analyses={
                "ques1": QuestionAnalysis(
                    objective="预测RSMT",
                    input_data=["附件1"],
                    decision_variables=["代理参数"],
                    constraints=["低复杂度"],
                    expected_outputs=["预测值"],
                    dependencies=["问题2"],
                    risks=["过拟合"],
                    validation_requirements=["交叉验证"],
                )
            },
        )
        refined = initial.model_copy(
            update={
                "analysis_summary": "经附件核验的递进理解",
                "question_analyses": {
                    "ques1": initial.question_analyses["ques1"].model_copy(
                        update={"data_evidence": ["附件1.txt共500组"]}
                    )
                },
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(
                Problem(task_id="analysis-task", ques_all=initial.original_problem)
            )
            state["questions"] = initial.questions
            state["ques_count"] = 1
            state["coordinator_response"] = initial.model_dump(mode="json")
            state["data_profile"] = {"files": [{"name": "附件1.txt"}]}
            state["literature_brief"] = "代理模型需要样本外检验。"
            checkpoint.complete_node(state, "coordinator")
            checkpoint.complete_node(state, "research")
            workflow = RemitWorkFlow()
            workflow.task_id = "analysis-task"
            workflow.work_dir = tmp
            workflow.checkpoint = checkpoint
            coordinator = MagicMock()
            coordinator.refine_analysis = AsyncMock(return_value=refined)

            with (
                patch(
                    "app.core.workflow.redis_manager.publish_message",
                    new=AsyncMock(),
                ),
                self.assertRaises(WorkflowApprovalRequired) as raised,
            ):
                await workflow._analysis_node(state, initial, coordinator)

            approval = raised.exception.approval
            self.assertEqual(approval["node_id"], "analysis")
            self.assertEqual(approval["quality_report"]["status"], "warning")
            self.assertIn("证据核验尚未完整", approval["summary"])
            displayed = approval["explain"]["question_analyses"]["ques1"]
            self.assertEqual(displayed["objective"], "预测RSMT")
            self.assertEqual(displayed["data_evidence"], ["附件1.txt共500组"])
            self.assertIn("problem_analysis.json", approval["artifacts"])
            artifact = json.loads(
                (Path(tmp) / "problem_analysis.json").read_text(encoding="utf-8")
            )
            self.assertEqual(artifact["original_problem"], initial.original_problem)
            self.assertEqual(
                artifact["question_analyses"]["ques1"]["objective"],
                "预测RSMT",
            )

    async def test_completed_but_unapproved_analysis_restores_approval_gate(
        self,
    ) -> None:
        refined = CoordinatorToModeler(
            original_problem="问题1：预测RSMT。",
            questions={"ques_count": 1, "ques1": "问题1：预测RSMT。"},
            ques_count=1,
            analysis_summary="附件核验完成。",
            question_analyses={
                "ques1": QuestionAnalysis(
                    objective="预测RSMT",
                    input_data=["附件1"],
                    decision_variables=["代理参数"],
                    constraints=["低复杂度"],
                    expected_outputs=["预测值"],
                    dependencies=["问题2"],
                    risks=["泄漏"],
                    validation_requirements=["分组验证"],
                    data_evidence=["附件1存在连接组ID"],
                )
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(
                Problem(task_id="analysis-task", ques_all=refined.original_problem)
            )
            state["questions"] = refined.questions
            state["ques_count"] = refined.ques_count
            state["coordinator_response"] = refined.model_dump(mode="json")
            state["analysis_response"] = refined.model_dump(mode="json")
            checkpoint.complete_node(state, "coordinator")
            checkpoint.complete_node(state, "research")
            checkpoint.complete_node(state, "analysis")
            workflow = RemitWorkFlow()
            workflow.task_id = "analysis-task"
            workflow.work_dir = tmp
            workflow.checkpoint = checkpoint
            coordinator = MagicMock()
            coordinator.refine_analysis = AsyncMock(
                side_effect=AssertionError("不应重复调用模型")
            )

            with self.assertRaises(WorkflowApprovalRequired) as raised:
                await workflow._analysis_node(state, refined, coordinator)

            self.assertEqual(raised.exception.approval["node_id"], "analysis")
            coordinator.refine_analysis.assert_not_awaited()

    async def test_initial_understanding_continues_to_research_without_approval(
        self,
    ) -> None:
        response = CoordinatorToModeler(
            original_problem="问题1：预测RSMT。",
            questions={"ques_count": 1, "ques1": "问题1：预测RSMT。"},
            ques_count=1,
            analysis_summary="初步理解",
            question_analyses={
                "ques1": QuestionAnalysis(
                    objective="预测RSMT",
                    input_data=["附件1"],
                    decision_variables=["代理参数"],
                    constraints=["低复杂度"],
                    expected_outputs=["预测值"],
                    dependencies=["问题2"],
                    risks=["过拟合"],
                    validation_requirements=["交叉验证"],
                )
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(
                Problem(task_id="analysis-task", ques_all=response.original_problem)
            )
            workflow = RemitWorkFlow()
            workflow.task_id = "analysis-task"
            workflow.work_dir = tmp
            workflow.checkpoint = checkpoint
            coordinator = MagicMock()
            coordinator.run = AsyncMock(return_value=response)

            with patch(
                "app.core.workflow.redis_manager.publish_message",
                new=AsyncMock(),
            ):
                actual = await workflow._coordinator_node(
                    Problem(
                        task_id="analysis-task",
                        ques_all=response.original_problem,
                    ),
                    state,
                    coordinator,
                )

            self.assertEqual(actual.analysis_summary, "初步理解")
            self.assertIsNone(state["pending_approval"])
            self.assertIn("coordinator", state["completed_nodes"])

    def test_analysis_revision_keeps_previous_version_and_research_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(
                Problem(task_id="analysis-task", ques_all="问题1：预测RSMT")
            )
            initial = {
                "questions": {"ques_count": 1, "ques1": "预测RSMT"},
                "ques_count": 1,
                "analysis_summary": "初步理解",
            }
            refined = {
                **initial,
                "analysis_summary": "数据校正版理解",
            }
            state["questions"] = initial["questions"]
            state["ques_count"] = 1
            state["coordinator_response_pre_analysis"] = initial
            state["coordinator_response"] = refined
            state["analysis_response"] = refined
            state["data_profile"] = {"files": [{"name": "附件1.txt"}]}
            state["literature_brief"] = "代理模型需要样本外检验"
            checkpoint.complete_node(state, "coordinator")
            checkpoint.complete_node(state, "research")
            checkpoint.complete_node(state, "analysis")
            pending = checkpoint.request_approval(
                state,
                "analysis",
                summary="等待验收",
            )

            revised = checkpoint.request_revision(
                state,
                pending["checkpoint_id"],
                "补充异方差风险。",
            )

            self.assertEqual(
                revised["previous_analysis_response"]["analysis_summary"],
                "数据校正版理解",
            )
            self.assertEqual(
                revised["coordinator_response"]["analysis_summary"],
                "初步理解",
            )
            self.assertEqual(
                revised["data_profile"],
                {"files": [{"name": "附件1.txt"}]},
            )
            self.assertNotIn("analysis", revised["completed_nodes"])

    def test_legacy_task_waiting_after_coordinator_is_upgraded_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(
                Problem(task_id="legacy-task", ques_all="问题1：预测RSMT")
            )
            state["workflow_features"] = ["research", "pilot"]
            state["questions"] = {"ques_count": 1, "ques1": "预测RSMT"}
            state["ques_count"] = 1
            state["coordinator_response"] = {
                "questions": state["questions"],
                "ques_count": 1,
            }

            upgraded = checkpoint.upgrade_problem_analysis(state)

            self.assertIn("analysis", upgraded["workflow_features"])
            self.assertEqual(
                checkpoint.node_order(upgraded)[:4],
                ["coordinator", "research", "analysis", "modeler"],
            )


if __name__ == "__main__":
    unittest.main()
