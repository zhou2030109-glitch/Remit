"""Tests for non-predictive and final-paper workflow gates."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.deliverable_contract import (
    DeliverableValidationError,
    build_question_contract,
    build_stage_contract,
    validate_final_paper,
    validate_question_deliverables,
)


class WorkflowQualityGateTests(unittest.TestCase):
    @staticmethod
    def _base_report(problem_type: str) -> dict:
        return {
            "status": "pass",
            "problem_type": problem_type,
            "selected_model": "main",
            "candidate_models": [
                {"name": "simple", "role": "baseline"},
                {"name": "main", "role": "candidate"},
            ],
            "independent_unit": "scenario",
            "data_leakage_checks": {},
            "robustness_checks": [
                {"name": "parameter perturbation", "passed": True},
                {"name": "alternative start", "passed": True},
            ],
            "limitations": ["边界场景样本有限"],
            "artifacts": ["result.json"],
            "paper_ready_images": ["figure.png"],
        }

    def test_infeasible_optimization_cannot_pass(self) -> None:
        contract = build_question_contract(
            "ques2", "建立风险函数并求最佳检测时点，使风险最小化"
        )
        self.assertEqual(contract.problem_type, "optimization")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("result.json").write_text("{}", encoding="utf-8")
            root.joinpath("figure.png").write_bytes(b"not-empty")
            report = self._base_report("optimization")
            report["type_specific"] = {
                "objective": {
                    "model_value": 8.0,
                    "baseline_value": 10.0,
                    "higher_is_better": False,
                },
                "feasible": False,
                "max_constraint_violation": 0.0,
                "constraint_tolerance": 1e-6,
                "sensitivity_scenarios": 5,
            }
            root.joinpath("ques2_quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(DeliverableValidationError, "不可行"):
                validate_question_deliverables(root, contract)

    def test_evidence_backed_baseline_retention_can_pass(self) -> None:
        contract = build_question_contract(
            "ques3", "考虑检测误差并优化分组和检测时点，使风险最小化"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("result.json").write_text("{}", encoding="utf-8")
            root.joinpath("figure.png").write_bytes(b"not-empty")
            report = self._base_report("optimization")
            report.update(
                {
                    "selected_model": "validated_baseline",
                    "gate_decision": (
                        "The executed challenger missed the improvement gate; "
                        "retain the validated baseline."
                    ),
                    "candidate_models": [
                        {
                            "name": "validated_baseline",
                            "role": "baseline",
                            "selected": True,
                            "actually_run": True,
                        },
                        {
                            "name": "challenger",
                            "role": "candidate",
                            "selected": False,
                            "actually_run": True,
                        },
                    ],
                }
            )
            report["type_specific"] = {
                "objective": {
                    "model_value": 12.0,
                    "baseline_value": 10.0,
                    "higher_is_better": False,
                },
                "selection_decision": "baseline_retained",
                "fallback_solution": {
                    "oof_risk": 10.0,
                    "source": "validated_baseline.csv",
                },
                "feasible": True,
                "max_constraint_violation": 0.0,
                "constraint_tolerance": 1e-6,
                "sensitivity_scenarios": [{}, {}, {}],
            }
            root.joinpath("ques3_quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )

            result = validate_question_deliverables(root, contract)

            self.assertTrue(result.passed)

    def test_baseline_retention_rejects_mismatched_fallback_metric(self) -> None:
        contract = build_question_contract(
            "ques3", "考虑检测误差并优化分组和检测时点，使风险最小化"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("result.json").write_text("{}", encoding="utf-8")
            root.joinpath("figure.png").write_bytes(b"not-empty")
            report = self._base_report("optimization")
            report.update(
                {
                    "selected_model": "validated_baseline",
                    "gate_decision": "Retain the validated baseline.",
                    "candidate_models": [
                        {
                            "name": "validated_baseline",
                            "role": "baseline",
                            "selected": True,
                        },
                        {
                            "name": "challenger",
                            "role": "candidate",
                            "actually_run": True,
                        },
                    ],
                }
            )
            report["type_specific"] = {
                "objective": {
                    "model_value": 12.0,
                    "baseline_value": 10.0,
                    "higher_is_better": False,
                },
                "selection_decision": "baseline_retained",
                "fallback_solution": {
                    "oof_risk": 9.0,
                    "source": "validated_baseline.csv",
                },
                "feasible": True,
                "max_constraint_violation": 0.0,
                "constraint_tolerance": 1e-6,
                "sensitivity_scenarios": 3,
            }
            root.joinpath("ques3_quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )

            with self.assertRaisesRegex(
                DeliverableValidationError,
                "fallback_solution.oof_risk",
            ):
                validate_question_deliverables(root, contract)

    def test_eda_requires_independent_unit_and_cleaning_audit(self) -> None:
        contract = build_stage_contract("eda")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("eda.xlsx").write_bytes(b"not-empty")
            report = {
                "status": "pass",
                "problem_type": "eda",
                "selected_model": "not_applicable",
                "candidate_models": [],
                "robustness_checks": [
                    {"name": "row reconciliation", "passed": True}
                ],
                "artifacts": ["eda.xlsx"],
                "paper_ready_images": [],
                "type_specific": {
                    "raw_rows": 100,
                    "cleaned_rows": 90,
                    "missingness_checked": True,
                    "duplicates_checked": True,
                    "outliers_assessed": True,
                    "independent_unit_identified": False,
                },
            }
            root.joinpath("eda_quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(DeliverableValidationError, "必需检查"):
                validate_question_deliverables(root, contract)

    def test_eda_manual_review_returns_human_gate_without_failing(self) -> None:
        contract = build_stage_contract("eda")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("eda.csv").write_text("x\n1\n", encoding="utf-8")
            report = {
                "status": "manual_review",
                "problem_type": "eda",
                "selected_model": "subject_balanced_eda",
                "candidate_models": [],
                "robustness_checks": [
                    {"name": "quality sensitivity", "passed": True}
                ],
                "artifacts": ["eda.csv"],
                "paper_ready_images": [],
                "gate_failures": {
                    "decision": "quality sensitivity exceeded threshold"
                },
                "type_specific": {
                    "raw_rows": 100,
                    "cleaned_rows": 100,
                    "missingness_checked": True,
                    "duplicates_checked": True,
                    "outliers_assessed": True,
                    "independent_unit_identified": True,
                },
            }
            root.joinpath("eda_quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )

            result = validate_question_deliverables(root, contract)

            self.assertFalse(result.passed)
            self.assertTrue(result.manual_review_required)
            self.assertIn("quality sensitivity", result.manual_review_reason or "")

    def test_sensitivity_accepts_conclusion_to_artifact_evidence_map(self) -> None:
        contract = build_stage_contract("sensitivity_analysis")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("result.csv").write_text("metric,value\nrmse,0.1\n")
            root.joinpath("evidence.csv").write_text("check,passed\nrobust,true\n")
            report = {
                "status": "pass",
                "problem_type": "sensitivity",
                "selected_model": "evidence_audit",
                "candidate_models": [],
                "robustness_checks": [
                    {"name": "scenario stability", "passed": True}
                ],
                "artifacts": ["result.csv"],
                "paper_ready_images": [],
                "type_specific": {
                    "covered_questions": ["Q1"],
                    "parameters_tested": 2,
                    "scenarios": 4,
                    "conclusions_grounded": [
                        {
                            "conclusion": "The selected result is stable.",
                            "artifacts": ["evidence.csv"],
                        }
                    ],
                },
            }
            root.joinpath("sensitivity_analysis_quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )

            result = validate_question_deliverables(root, contract)

            self.assertTrue(result.passed)

    def test_sensitivity_rejects_missing_conclusion_evidence_file(self) -> None:
        contract = build_stage_contract("sensitivity_analysis")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("result.csv").write_text("metric,value\nrmse,0.1\n")
            report = {
                "status": "pass",
                "problem_type": "sensitivity",
                "selected_model": "evidence_audit",
                "candidate_models": [],
                "robustness_checks": [
                    {"name": "scenario stability", "passed": True}
                ],
                "artifacts": ["result.csv"],
                "paper_ready_images": [],
                "type_specific": {
                    "covered_questions": ["Q1"],
                    "parameters_tested": 2,
                    "scenarios": 4,
                    "conclusions_grounded": [
                        {
                            "conclusion": "The selected result is stable.",
                            "artifacts": ["missing.csv"],
                        }
                    ],
                },
            }
            root.joinpath("sensitivity_analysis_quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )

            with self.assertRaisesRegex(DeliverableValidationError, "missing.csv"):
                validate_question_deliverables(root, contract)

    def test_sensitivity_manual_review_preserves_failure_reason_list(self) -> None:
        contract = build_stage_contract("sensitivity_analysis")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("result.csv").write_text("metric,value\nrmse,0.1\n")
            report = {
                "status": "manual_review",
                "manual_review_required": True,
                "failure_reasons": [
                    "Q1 interval coverage is below threshold.",
                    "Q2 lag stability failed.",
                ],
                "problem_type": "sensitivity",
                "selected_model": "evidence_audit",
                "candidate_models": [],
                "robustness_checks": [
                    {"name": "scenario stability", "passed": True}
                ],
                "artifacts": ["result.csv"],
                "paper_ready_images": [],
                "type_specific": {
                    "covered_questions": ["Q1", "Q2"],
                    "parameters_tested": 2,
                    "scenarios": 4,
                    "conclusions_grounded": True,
                },
            }
            root.joinpath("sensitivity_analysis_quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )

            result = validate_question_deliverables(root, contract)

            self.assertTrue(result.manual_review_required)
            self.assertIn("Q1 interval coverage", result.manual_review_reason or "")
            self.assertIn("Q2 lag stability", result.manual_review_reason or "")

    def test_short_paper_fails_final_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("res.md").write_text("这是很短的论文。", encoding="utf-8")
            sections = {"ques1": {"response_content": "内容", "footnotes": []}}
            with self.assertRaisesRegex(DeliverableValidationError, "不足9000字"):
                validate_final_paper(root, sections, ["ques1"])

    def test_final_paper_requires_numbered_question_parent_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("res.md").write_text(
                "# 完整论文\n\n### 5.4.1 模型建立\n" + "有效建模证据。" * 2000,
                encoding="utf-8",
            )
            sections = {"ques4": {"response_content": "内容", "footnotes": []}}

            with self.assertRaisesRegex(DeliverableValidationError, "5.4"):
                validate_final_paper(root, sections, ["ques4"])

    def test_final_paper_rejects_writer_process_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("res.md").write_text(
                (
                    "# 完整论文\n\n## 5.4 问题四模型\n\n"
                    "先核对质量报告中的 selected_model，随后直接给出可替换正文。\n"
                    + "有效建模证据。" * 2000
                ),
                encoding="utf-8",
            )
            sections = {"ques4": {"response_content": "内容", "footnotes": []}}

            with self.assertRaisesRegex(DeliverableValidationError, "写作过程话术"):
                validate_final_paper(root, sections, ["ques4"])


if __name__ == "__main__":
    unittest.main()
