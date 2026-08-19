"""层级建模方法检索的行为测试。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.agents.modeler_agent import ModelerAgent
from app.core.method_retrieval import (
    HierarchicalMethodRetriever,
    MethodSelectionEngine,
)
from app.core.model_council import ModelCouncil
from app.core.workflow import RemitWorkFlow
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.schemas.A2A import (
    CoordinatorToModeler,
    ModelerToCoder,
    ModelScoutProposal,
)


TEST_LIBRARY = [
    {
        "id": "prediction",
        "name": "预测",
        "description": "根据历史样本预测未知连续数值或未来趋势",
        "keywords": ["预测", "回归", "未来"],
        "subdomains": [
            {
                "id": "tabular-regression",
                "name": "表格回归",
                "description": "多个特征解释或预测连续目标",
                "keywords": ["连续目标", "监督学习", "特征"],
                "methods": [
                    {
                        "id": "ridge-regression",
                        "name": "岭回归",
                        "summary": "带二范数正则的可解释线性回归基线",
                        "keywords": [
                            "小样本",
                            "样本较少",
                            "共线性",
                            "共线",
                            "连续值",
                        ],
                        "assumptions": ["关系近似线性"],
                        "failure_modes": ["强非线性时欠拟合"],
                        "validation": ["分组交叉验证"],
                    },
                    {
                        "id": "gradient-boosting",
                        "name": "梯度提升树",
                        "summary": "拟合复杂非线性和特征交互的树模型",
                        "keywords": ["非线性", "表格数据", "特征交互"],
                        "assumptions": ["训练样本具有代表性"],
                        "failure_modes": ["小样本时容易过拟合"],
                        "validation": ["嵌套交叉验证"],
                    },
                ],
            }
        ],
    },
    {
        "id": "optimization",
        "name": "优化",
        "description": "在约束条件下寻找最大或最小目标",
        "keywords": ["最优", "约束", "决策变量"],
        "subdomains": [
            {
                "id": "mathematical-programming",
                "name": "数学规划",
                "description": "显式目标函数与约束",
                "keywords": ["目标函数", "可行域"],
                "methods": [
                    {
                        "id": "linear-programming",
                        "name": "线性规划",
                        "summary": "在线性目标和线性约束下求全局最优解",
                        "keywords": ["资源分配", "线性约束"],
                        "assumptions": ["目标与约束均为线性"],
                        "failure_modes": ["非线性关系会导致模型失真"],
                        "validation": ["约束残差审计"],
                    }
                ],
            }
        ],
    },
]


class HierarchicalMethodRetrieverTests(unittest.TestCase):
    def test_returns_the_most_relevant_method_with_hierarchy_scores(self) -> None:
        retriever = HierarchicalMethodRetriever(TEST_LIBRARY)

        recommendations = retriever.retrieve(
            "根据多个特征预测连续目标，样本较少且变量存在共线性",
            top_k=1,
        )

        self.assertEqual(len(recommendations), 1)
        best = recommendations[0]
        self.assertEqual(best.method_id, "ridge-regression")
        self.assertEqual(best.domain_name, "预测")
        self.assertEqual(best.subdomain_name, "表格回归")
        self.assertGreater(best.score, 0)
        self.assertGreater(best.domain_score, 0)
        self.assertGreater(best.subdomain_score, 0)
        self.assertGreater(best.method_score, 0)

    def test_retrieves_top_k_independently_for_each_formal_question(self) -> None:
        retriever = HierarchicalMethodRetriever(TEST_LIBRARY)

        by_question = retriever.retrieve_for_questions(
            {
                "ques1": "预测连续数值，变量可能共线且样本较少",
                "ques2": "在线性约束下安排有限资源，使总收益最大",
                "ques_count": 2,
            },
            shared_context={"data_profile": "表格数据，包含多个数值特征"},
            top_k=2,
        )

        self.assertEqual(set(by_question), {"ques1", "ques2"})
        self.assertEqual(by_question["ques1"][0].method_id, "ridge-regression")
        self.assertEqual(by_question["ques2"][0].method_id, "linear-programming")
        self.assertLessEqual(len(by_question["ques1"]), 2)
        self.assertLessEqual(len(by_question["ques2"]), 2)

    def test_top_k_contains_unique_method_ids(self) -> None:
        library = copy.deepcopy(TEST_LIBRARY)
        duplicate = copy.deepcopy(library[0]["subdomains"][0]["methods"][0])
        library[1]["subdomains"][0]["methods"].append(duplicate)
        retriever = HierarchicalMethodRetriever(library)

        recommendations = retriever.retrieve(
            "小样本共线性连续值预测",
            top_k=3,
        )

        method_ids = [item.method_id for item in recommendations]
        self.assertEqual(len(method_ids), len(set(method_ids)))

    def test_serialized_payload_explains_hierarchy_and_validation(self) -> None:
        retriever = HierarchicalMethodRetriever(TEST_LIBRARY)
        recommendations = retriever.retrieve_for_questions(
            {"ques1": "小样本共线性连续值预测"},
            top_k=1,
        )

        payload = retriever.to_payload(recommendations)

        first = payload["ques1"][0]
        self.assertEqual(first["rank"], 1)
        self.assertEqual(first["hierarchy"], ["预测", "表格回归", "岭回归"])
        self.assertEqual(first["validation"], ["分组交叉验证"])
        self.assertIn("method", first["score_breakdown"])
        json.dumps(payload, ensure_ascii=False)

    def test_default_library_has_broad_modeling_coverage(self) -> None:
        retriever = HierarchicalMethodRetriever.from_default_library()

        self.assertGreaterEqual(retriever.method_count, 50)
        self.assertGreaterEqual(retriever.domain_count, 6)
        self.assertEqual(
            retriever.retrieve("线性目标与约束下的资源分配", top_k=1)[0].method_id,
            "linear-programming",
        )
        self.assertEqual(
            retriever.retrieve("具有季节性的单变量时间序列预测", top_k=1)[0].method_id,
            "sarima",
        )

    def test_selection_engine_persists_the_exact_top_k_payload(self) -> None:
        engine = MethodSelectionEngine(
            HierarchicalMethodRetriever(TEST_LIBRARY),
            top_k=1,
        )

        with tempfile.TemporaryDirectory() as tmp:
            payload = engine.select(
                {"ques1": "小样本共线性连续值预测", "ques_count": 1},
                shared_context={"user_requirements": "结果必须可解释"},
                work_dir=tmp,
            )

            artifact = Path(tmp) / "method_recommendations.json"
            self.assertTrue(artifact.is_file())
            self.assertEqual(
                json.loads(artifact.read_text(encoding="utf-8")),
                payload,
            )
            self.assertEqual(len(payload["ques1"]), 1)


class MethodRetrievalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_modeler_receives_retrieved_methods_as_selection_evidence(
        self,
    ) -> None:
        agent = ModelerAgent("method-task", MagicMock())
        agent._chat = AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps(
                    {
                        "eda": (
                            "检查数据质量、分布与变量关系，区分真实独立主体，"
                            "形成可复现的数据画像、缺失审计和基础图表。"
                        ),
                        "ques1": (
                            "以岭回归为简单可解释基线，按主体分组交叉验证，"
                            "并与非线性候选公平比较，报告样本外误差、区间和失败回退条件。"
                        ),
                        "sensitivity_analysis": (
                            "扰动正则系数、分组方式和关键输入，比较样本外误差变化，"
                            "报告稳定区间、极端情景和结论是否改变。"
                        ),
                    },
                    ensure_ascii=False,
                ),
                reasoning_content=None,
            )
        )
        coordinator = CoordinatorToModeler(
            questions={"ques1": "根据多个变量预测连续目标", "ques_count": 1},
            ques_count=1,
            method_recommendations={
                "ques1": [
                    {
                        "rank": 1,
                        "method_name": "岭回归",
                        "hierarchy": ["预测", "表格回归", "岭回归"],
                        "score": 0.82,
                        "validation": ["分组交叉验证"],
                    }
                ]
            },
        )

        await agent.run(coordinator)

        modeler_input = json.loads(agent.chat_history[1]["content"])
        self.assertEqual(
            modeler_input["method_recommendations"]["ques1"][0]["method_name"],
            "岭回归",
        )

    async def test_independent_scout_receives_the_same_retrieved_candidates(
        self,
    ) -> None:
        council = ModelCouncil(
            task_id="method-task",
            scout_llm=MagicMock(),
            critic_llm=MagicMock(),
        )
        proposal = ModelScoutProposal.model_validate(
            {
                "questions": {
                    "ques1": {
                        "candidate_models": [
                            {
                                "name": "线性回归",
                                "role": "baseline",
                                "reason": "简单可解释的预测基线",
                            },
                            {
                                "name": "岭回归",
                                "role": "candidate",
                                "reason": "适合共线变量",
                            },
                        ],
                        "recommended_model": "岭回归",
                        "strategy": (
                            "比较线性回归与岭回归，使用完全相同的特征和主体分组折，"
                            "在训练折内完成调参后评估样本外误差与模型稳定性。"
                        ),
                        "validation_plan": (
                            "使用主体分组交叉验证，报告逐折误差、总体样本外指标、"
                            "置信区间和折间稳定性。"
                        ),
                        "failure_risks": ["样本过少"],
                    }
                },
                "global_data_risks": ["主体泄漏"],
                "cross_question_strategy": "固定分组折并仅共享折内完成的预处理信息。",
            }
        )
        council._json_call = AsyncMock(return_value=proposal)
        coordinator = CoordinatorToModeler(
            questions={"ques1": "预测连续目标", "ques_count": 1},
            ques_count=1,
            analysis_summary="附件核验后确认必须按主体分组验证。",
            question_analyses={
                "ques1": {
                    "objective": "预测连续目标",
                    "input_data": ["附件表格变量"],
                    "decision_variables": ["模型参数"],
                    "constraints": ["禁止主体泄漏"],
                    "expected_outputs": ["样本外预测"],
                    "dependencies": ["为下一问提供预测值"],
                    "risks": ["重复测量泄漏"],
                    "validation_requirements": ["主体分组交叉验证"],
                    "data_evidence": ["附件存在主体ID列"],
                }
            },
            method_recommendations={
                "ques1": [
                    {
                        "rank": 1,
                        "method_name": "岭回归",
                        "hierarchy": ["预测", "表格回归", "岭回归"],
                        "score": 0.82,
                    }
                ]
            },
        )

        with patch(
            "app.core.model_council.redis_manager.publish_message",
            new=AsyncMock(),
        ):
            await council.propose(coordinator)

        payload = council._json_call.await_args.kwargs["payload"]
        self.assertEqual(
            payload["method_recommendations"]["ques1"][0]["method_name"],
            "岭回归",
        )
        self.assertEqual(
            payload["question_analyses"]["ques1"]["validation_requirements"],
            ["主体分组交叉验证"],
        )

    async def test_workflow_retrieves_persists_and_injects_methods_before_modeling(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow = RemitWorkFlow()
            workflow.task_id = "workflow-method-task"
            workflow.work_dir = tmp
            workflow.checkpoint = WorkflowCheckpoint(tmp)
            workflow._require_human_approval = AsyncMock()
            state = {
                "status": "running",
                "completed_nodes": [],
                "revision_feedback": {},
                "revision_counts": {},
            }
            workflow.checkpoint.save(state)
            modeler = MagicMock()
            modeler.run = AsyncMock(
                return_value=ModelerToCoder(
                    questions_solution={
                        "eda": "检查输入参数、量纲和约束边界，形成可复现的基础审计与图表。",
                        "ques1": "建立线性规划并逐项检查约束残差，与简单可行分配基线比较目标值。",
                        "sensitivity_analysis": "扰动资源上限、成本和目标权重，检查可行性、目标值及结论稳定性。",
                    }
                )
            )
            coordinator = CoordinatorToModeler(
                questions={
                    "ques1": "在线性目标和约束下分配有限资源，使总收益最大",
                    "ques_count": 1,
                },
                ques_count=1,
                user_requirements="所有方案必须满足物理边界",
            )

            with patch(
                "app.core.workflow.redis_manager.publish_message",
                new=AsyncMock(),
            ):
                await workflow._modeler_node(
                    state,
                    coordinator,
                    modeler,
                    None,
                )

            injected = modeler.run.await_args.args[0].method_recommendations
            self.assertEqual(
                injected["ques1"][0]["method_id"],
                "linear-programming",
            )
            self.assertEqual(state["method_recommendations"], injected)
            self.assertTrue((Path(tmp) / "method_recommendations.json").is_file())
            approval = workflow._require_human_approval.await_args.kwargs
            self.assertIn("method_recommendations.json", approval["artifacts"])
            method_candidates = [
                candidate
                for candidate in approval["explain"]["candidates"]
                if candidate["role"] == "method_library"
            ]
            self.assertEqual(len(method_candidates), 6)
            self.assertEqual(
                approval["explain"]["candidates"][0]["role"],
                "method_library",
            )

    async def test_checkpoint_reuses_candidates_and_repairs_missing_artifact(
        self,
    ) -> None:
        cached = {
            "ques1": [
                {
                    "rank": 1,
                    "method_id": "cached-method",
                    "method_name": "已冻结候选",
                    "hierarchy": ["预测", "基线", "已冻结候选"],
                    "summary": "恢复时必须原样复用",
                    "score": 0.75,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            workflow = RemitWorkFlow()
            workflow.task_id = "resume-method-task"
            workflow.work_dir = tmp
            workflow.checkpoint = WorkflowCheckpoint(tmp)
            workflow._require_human_approval = AsyncMock()
            state = {
                "status": "running",
                "completed_nodes": [],
                "revision_feedback": {},
                "revision_counts": {},
                "method_recommendations": cached,
            }
            workflow.checkpoint.save(state)
            modeler = MagicMock()
            modeler.run = AsyncMock(
                return_value=ModelerToCoder(
                    questions_solution={
                        "eda": "复用既有数据画像并核对输入文件、缺失模式和独立样本单位是否保持一致。",
                        "ques1": "复用已冻结候选继续完成分组验证、基线比较和样本外误差评估，不重新检索。",
                        "sensitivity_analysis": "复用既有候选并扰动关键参数，检查误差、区间和核心结论的稳定性。",
                    }
                )
            )
            coordinator = CoordinatorToModeler(
                questions={"ques1": "预测连续目标", "ques_count": 1},
                ques_count=1,
            )

            with (
                patch(
                    "app.core.workflow.redis_manager.publish_message",
                    new=AsyncMock(),
                ),
                patch(
                    "app.core.workflow.HierarchicalMethodRetriever.from_default_library",
                    side_effect=AssertionError("恢复时不应重新检索"),
                ),
            ):
                await workflow._modeler_node(
                    state,
                    coordinator,
                    modeler,
                    None,
                )

            injected = modeler.run.await_args.args[0].method_recommendations
            self.assertEqual(injected, cached)
            artifact = Path(tmp) / "method_recommendations.json"
            self.assertEqual(
                json.loads(artifact.read_text(encoding="utf-8")),
                cached,
            )


if __name__ == "__main__":
    unittest.main()
