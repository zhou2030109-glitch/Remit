"""文献证据链回归测试：方法卡 → 候选溯源 → 代码验证 → 最终引用。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from app.core.agents.modeler_agent import ModelerAgent
from app.core.citations import (
    build_citation_brief,
    build_citation_ledger,
    build_citation_table,
    load_citation_ledger,
    persist_citation_ledger,
)
from app.core.literature import (
    _QUERY_PROMPT,
    _SCREEN_PROMPT,
    _converge_selected_papers,
    build_method_card_brief,
    build_method_cards,
    run_literature_review,
)
from app.core.llm.types import StandardResponse
from app.core.project_audit import evaluate_research
from app.schemas.A2A import MethodCard, PilotDecision, PilotPlan


def _card(card_id: str, question_key: str = "ques1", **overrides) -> MethodCard:
    payload = {
        "card_id": card_id,
        "question_key": question_key,
        "title": f"Paper {card_id}",
        "citation": f"Liu et al. (2023). Paper {card_id}.",
        "publication_year": 2023,
        "doi": f"10.1000/{card_id}",
        "url": f"https://openalex.org/{card_id}",
        "evidence_level": "full_text",
        "problem_solved": "短期负荷预测",
        "method": "CEEMDAN 分解加 LSTM",
        "key_steps": ["分解序列", "逐分量建模", "重构预测"],
        "applicable_conditions": ["序列平稳段足够长"],
        "strengths": ["RMSE 降低 12%"],
        "limitations": ["分解参数敏感"],
        "source_locations": [{"section": "3.2 Model", "page": 5, "quote": "we use"}],
        "competition_adaptation": "改用小样本并去掉 GPU 依赖",
    }
    payload.update(overrides)
    return MethodCard.model_validate(payload)


def _plan(**overrides) -> PilotPlan:
    payload = {
        "questions": {
            "ques1": {
                "candidates": [
                    {
                        "name": "ARIMA 基线",
                        "role": "baseline",
                        "approach": "直接对原序列拟合 ARIMA",
                    },
                    {
                        "name": "CEEMDAN-LSTM",
                        "role": "candidate",
                        "approach": "先分解再逐分量建模",
                        "source_card_id": "ques1-C1",
                        "adaptation": "去掉 GPU 依赖，分量数减半",
                    },
                ],
                "sampling_rule": "按时间取前 20%",
                "primary_metric": "rmse",
                "higher_is_better": False,
                "time_budget_minutes": 5,
            }
        }
    }
    payload.update(overrides)
    return PilotPlan.model_validate(payload)


def _decision(decision: str = "modified") -> PilotDecision:
    return PilotDecision.model_validate(
        {
            "questions": {
                "ques1": {
                    "selected_model": "CEEMDAN-LSTM",
                    "revised_strategy": "使用简化分解加轻量 LSTM 完成全量求解，"
                    "并与 ARIMA 基线在同一划分上比较，报告 RMSE 与区间估计。",
                    "justification": "小样本 RMSE 3.21 优于基线 4.87",
                    "citation_decisions": [
                        {
                            "card_id": "ques1-C1",
                            "decision": decision,
                            "evidence": "pilot RMSE 3.21 对基线 4.87",
                            "influence": "确定了分解加分量建模的主结构",
                        }
                    ],
                }
            }
        }
    )


class MethodCardBriefTests(unittest.TestCase):
    def test_brief_exposes_conditions_and_source_locations(self) -> None:
        brief = build_method_card_brief({"ques1": [_card("ques1-C1")]})

        self.assertIn("ques1-C1", brief)
        self.assertIn("已读全文", brief)
        self.assertIn("序列平稳段足够长", brief)
        self.assertIn("3.2 Model p5", brief)

    def test_abstract_only_card_is_marked(self) -> None:
        brief = build_method_card_brief(
            {"ques1": [_card("ques1-C1", evidence_level="abstract_only")]}
        )

        self.assertIn("仅摘要", brief)

    def test_rebuilds_cards_from_review_and_skips_broken_entries(self) -> None:
        review = {
            "method_cards": [
                _card("ques1-C1").model_dump(mode="json"),
                {"card_id": "broken"},
            ]
        }

        cards = build_method_cards(review)

        self.assertEqual(list(cards), ["ques1"])
        self.assertEqual(len(cards["ques1"]), 1)


class CandidateProvenanceTests(unittest.IsolatedAsyncioTestCase):
    """候选方案必须真实标注引用了哪张方法卡，否则最终引用无从判断。"""

    def _agent(self, *payloads: str) -> ModelerAgent:
        agent = ModelerAgent(task_id="t", model=AsyncMock())
        agent._chat = AsyncMock(  # type: ignore[method-assign]
            side_effect=[StandardResponse(content=payload) for payload in payloads]
        )
        return agent

    async def _design(self, plan_payload: dict, cards: dict) -> PilotPlan:
        agent = self._agent(json.dumps(plan_payload, ensure_ascii=False))
        return await agent.design_pilot_plan(
            questions={"ques1": "预测下个月负荷", "ques_count": 1},
            questions_solution={"ques1": "用时间序列方法"},
            literature_brief="",
            data_profile_summary="",
            backend_language="python",
            method_cards=cards,
        )

    async def test_accepts_plan_that_cites_a_real_card(self) -> None:
        plan = await self._design(
            _plan().model_dump(mode="json"), {"ques1": [_card("ques1-C1")]}
        )

        candidate = plan.questions["ques1"].candidates[1]
        self.assertEqual(candidate.source_card_id, "ques1-C1")
        self.assertIn("GPU", candidate.adaptation)

    async def test_rejects_plan_that_ignores_available_cards(self) -> None:
        payload = _plan().model_dump(mode="json")
        for candidate in payload["questions"]["ques1"]["candidates"]:
            candidate["source_card_id"] = ""
            candidate["adaptation"] = ""

        agent = self._agent(*[json.dumps(payload, ensure_ascii=False)] * 3)
        with self.assertRaisesRegex(ValueError, "没有任何候选引用"):
            await agent.design_pilot_plan(
                questions={"ques1": "预测下个月负荷"},
                questions_solution={"ques1": "用时间序列方法"},
                literature_brief="",
                data_profile_summary="",
                backend_language="python",
                method_cards={"ques1": [_card("ques1-C1")]},
            )

    async def test_rejects_plan_that_cites_unknown_card(self) -> None:
        payload = _plan().model_dump(mode="json")
        payload["questions"]["ques1"]["candidates"][1]["source_card_id"] = "ques9-C9"

        agent = self._agent(*[json.dumps(payload, ensure_ascii=False)] * 3)
        with self.assertRaisesRegex(ValueError, "不存在的方法卡"):
            await agent.design_pilot_plan(
                questions={"ques1": "预测下个月负荷"},
                questions_solution={"ques1": "用时间序列方法"},
                literature_brief="",
                data_profile_summary="",
                backend_language="python",
                method_cards={"ques1": [_card("ques1-C1")]},
            )

    async def test_finalize_requires_a_verdict_for_every_cited_card(self) -> None:
        payload = _decision().model_dump(mode="json")
        payload["questions"]["ques1"]["citation_decisions"] = []
        agent = self._agent(*[json.dumps(payload, ensure_ascii=False)] * 3)

        with self.assertRaisesRegex(ValueError, "缺少文献裁决"):
            await agent.finalize_with_pilot(
                questions={"ques1": "预测下个月负荷"},
                questions_solution={"ques1": "用时间序列方法"},
                pilot_results={
                    "questions": {
                        "ques1": {
                            "candidates": [
                                {"name": "CEEMDAN-LSTM", "ran_ok": True},
                                {"name": "ARIMA 基线", "ran_ok": True},
                            ]
                        }
                    }
                },
                literature_brief="",
                pilot_plan=_plan(),
            )


class CitationLedgerTests(unittest.TestCase):
    def test_adopted_and_modified_enter_final_citations(self) -> None:
        ledger = build_citation_ledger(
            method_cards={"ques1": [_card("ques1-C1")]},
            plan=_plan(),
            decision=_decision("modified"),
        )

        self.assertEqual(ledger["used_count"], 1)
        entry = ledger["used"][0]
        self.assertEqual(entry["decision_label"], "修改后采用")
        self.assertTrue(entry["is_selected_model"])
        self.assertIn("分量数减半", entry["adaptation"])

    def test_rejected_paper_never_enters_final_citations(self) -> None:
        ledger = build_citation_ledger(
            method_cards={"ques1": [_card("ques1-C1")]},
            plan=_plan(),
            decision=_decision("rejected"),
        )

        self.assertEqual(ledger["used"], [])
        self.assertEqual(len(ledger["rejected"]), 1)

        brief = build_citation_brief(ledger)
        self.assertEqual(brief, "")

    def test_brief_states_the_hard_constraint_and_the_modification(self) -> None:
        ledger = build_citation_ledger(
            method_cards={"ques1": [_card("ques1-C1")]},
            plan=_plan(),
            decision=_decision("adopted"),
        )

        brief = build_citation_brief(ledger)

        self.assertIn("清单外的文献一律不得引用", brief)
        self.assertIn("对建模的影响", brief)
        self.assertIn("pilot RMSE 3.21", brief)

    def test_same_paper_serving_two_questions_is_cited_once(self) -> None:
        plan = PilotPlan.model_validate(
            {
                "questions": {
                    key: {
                        "candidates": [
                            {
                                "name": "基线",
                                "role": "baseline",
                                "approach": "直接拟合简单模型",
                            },
                            {
                                "name": f"改进-{key}",
                                "role": "candidate",
                                "approach": "按方法卡实现",
                                "source_card_id": f"{key}-C1",
                                "adaptation": "简化",
                            },
                        ],
                        "sampling_rule": "前 20%",
                        "primary_metric": "rmse",
                    }
                    for key in ("ques1", "ques2")
                }
            }
        )
        decision = PilotDecision.model_validate(
            {
                "questions": {
                    key: {
                        "selected_model": f"改进-{key}",
                        "revised_strategy": "按入选候选完成全量求解，并在完全相同的"
                        "样本外划分上与基线比较，报告主指标、区间估计与稳健性检查。",
                        "justification": "小样本主指标优于基线",
                        "citation_decisions": [
                            {
                                "card_id": f"{key}-C1",
                                "decision": "adopted",
                                "evidence": "pilot RMSE 更低",
                            }
                        ],
                    }
                    for key in ("ques1", "ques2")
                }
            }
        )
        # 两问引用同一 DOI 的论文
        cards = {
            "ques1": [_card("ques1-C1", "ques1", doi="10.1000/shared")],
            "ques2": [_card("ques2-C1", "ques2", doi="10.1000/shared")],
        }

        ledger = build_citation_ledger(method_cards=cards, plan=plan, decision=decision)

        self.assertEqual(ledger["judged_count"], 2)
        self.assertEqual(ledger["used_count"], 1)

    def test_ledger_round_trips_through_disk(self) -> None:
        ledger = build_citation_ledger(
            method_cards={"ques1": [_card("ques1-C1")]},
            plan=_plan(),
            decision=_decision(),
        )
        with tempfile.TemporaryDirectory() as work_dir:
            persist_citation_ledger(work_dir, ledger)

            self.assertEqual(load_citation_ledger(work_dir), ledger)
        self.assertEqual(load_citation_ledger(tempfile.gettempdir()), {})

    def test_table_renders_one_row_per_verdict(self) -> None:
        ledger = build_citation_ledger(
            method_cards={"ques1": [_card("ques1-C1")]},
            plan=_plan(),
            decision=_decision(),
        )

        table = build_citation_table(ledger)

        self.assertEqual(len(table["rows"]), 1)
        self.assertEqual(table["rows"][0]["证据"], "全文")
        self.assertEqual(table["rows"][0]["候选方案"], "CEEMDAN-LSTM")


class ResearchAuditTests(unittest.TestCase):
    def test_papers_without_method_cards_are_not_green(self) -> None:
        outcome = evaluate_research(
            {
                "data_profile": {"files": ["a.csv"], "status": "completed"},
                "literature_review": {
                    "status": "completed",
                    "paper_count": 8,
                    "method_cards": [],
                },
            }
        )

        self.assertEqual(outcome["status"], "warning")
        self.assertTrue(any("方法卡" in issue for issue in outcome["issues"]))

    def test_abstract_only_cards_are_flagged_but_still_complete(self) -> None:
        outcome = evaluate_research(
            {
                "data_profile": {"files": ["a.csv"], "status": "completed"},
                "literature_review": {
                    "status": "completed",
                    "paper_count": 8,
                    "method_cards": [_card("ques1-C1").model_dump(mode="json")],
                    "fulltext_stats": {"attempted": 2, "succeeded": 0},
                },
            }
        )

        self.assertEqual(outcome["status"], "completed")
        self.assertTrue(any("只基于摘要" in issue for issue in outcome["issues"]))


class LiteratureDegradationTests(unittest.TestCase):
    def test_missing_questions_still_writes_auditable_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as work_dir:
            review = asyncio.run(
                run_literature_review(
                    task_id="t",
                    llm=AsyncMock(),
                    scholar=AsyncMock(),
                    questions={"ques_count": 0},
                    work_dir=work_dir,
                )
            )

            self.assertEqual(review["status"], "failed")
            self.assertEqual(review["method_cards"], [])
            cards_file = Path(work_dir) / "method_cards.json"
            self.assertEqual(
                json.loads(cards_file.read_text(encoding="utf-8")),
                {"method_cards": []},
            )


class LiteratureRelevanceFilterTests(unittest.TestCase):
    """文献必须经过相关性收敛：无关命中不展示、被过滤的可审计。"""

    def _papers(self) -> list[dict]:
        return [
            {
                "title": "A routing algorithm for VLSI global placement",
                "matched_query": "VLSI global placement routing",
            },
            {
                "title": "Traffic congestion prediction in urban networks",
                "matched_query": "network congestion optimization",
            },
            {
                "title": "RSMT-aware density constraint for chip layout",
                "matched_query": "RSMT density constraint layout",
            },
            {
                "title": "Cybersecurity intrusion detection with deep learning",
                "matched_query": "network security optimization",
            },
        ]

    def test_kept_papers_only_include_selected_ones(self) -> None:
        selected = {
            "ques1": [
                {
                    **self._papers()[0],
                    "relevance_reason": "直接可迁移到布局路由",
                },
                {
                    **self._papers()[2],
                    "relevance_reason": "RSMT 密度约束正是问题三所需",
                },
            ]
        }

        kept, filtered = _converge_selected_papers(self._papers(), selected)

        self.assertEqual(
            [paper["title"] for paper in kept],
            [
                "A routing algorithm for VLSI global placement",
                "RSMT-aware density constraint for chip layout",
            ],
        )
        self.assertEqual(filtered["count"], 2)
        self.assertTrue(
            any("Traffic congestion" in item["title"] for item in filtered["items"])
        )
        self.assertTrue(
            any("Cybersecurity" in item["title"] for item in filtered["items"])
        )
        self.assertEqual(kept[0]["relevance_reason"], "直接可迁移到布局路由")

    def test_same_paper_selected_for_two_questions_is_kept_once(self) -> None:
        papers = self._papers()
        selected = {
            "ques1": [{**papers[0], "relevance_reason": "问一"}],
            "ques2": [{**papers[0], "relevance_reason": "问二"}],
        }

        kept, filtered = _converge_selected_papers(papers, selected)

        self.assertEqual(len(kept), 1)
        self.assertEqual(filtered["count"], 3)

    def test_prompts_require_domain_relevance(self) -> None:
        self.assertIn("领域限定词", _QUERY_PROMPT)
        self.assertIn("跨领域", _QUERY_PROMPT)
        self.assertIn("明显无关", _SCREEN_PROMPT)
        self.assertIn("允许某问 0 篇", _SCREEN_PROMPT)


if __name__ == "__main__":
    unittest.main()
