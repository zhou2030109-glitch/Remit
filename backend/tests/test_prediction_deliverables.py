"""Regression tests for championship-level workflow quality gates."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from app.core.deliverable_contract import (
    DeliverableValidationError,
    ModelQualityValidationError,
    build_repair_prompt,
    build_question_contract,
    collect_model_quality_evidence,
    validate_question_deliverables,
    validate_writer_section,
)
from app.core.flows import Flows
from app.schemas.A2A import ModelerToCoder
from app.schemas.request import Problem


class PredictionDeliverableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = build_question_contract(
            question_key="ques1",
            question_text="给出 Y 染色体浓度与孕周和 BMI 的关系模型",
            user_requirements="问题1必须输出逐样本预测值，并严格检查预测质量",
        )

    @staticmethod
    def _regression_metrics(
        actual: list[float], predicted: list[float]
    ) -> dict[str, float]:
        errors = [a - p for a, p in zip(actual, predicted, strict=True)]
        mean_actual = sum(actual) / len(actual)
        total = sum((value - mean_actual) ** 2 for value in actual)
        return {
            "rmse": math.sqrt(sum(error**2 for error in errors) / len(errors)),
            "r2": 1 - sum(error**2 for error in errors) / total,
        }

    def _write_quality_report(self, root: Path, artifacts: list[str]) -> None:
        root.joinpath("result.txt").write_text("verified result", encoding="utf-8")
        root.joinpath("figure.png").write_bytes(b"not-empty")
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
                    "independent_unit": "pregnant woman",
                    "data_leakage_checks": {
                        "preprocessing_inside_folds": True,
                        "group_isolation": True,
                        "target_leakage_checked": True,
                    },
                    "robustness_checks": [
                        {"name": "cluster bootstrap", "passed": True},
                        {"name": "leave-one-group-out", "passed": True},
                    ],
                    "limitations": ["极端区间样本较少"],
                    "artifacts": artifacts,
                    "paper_ready_images": ["figure.png"],
                    "type_specific": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _write_predictions(
        self,
        root: Path,
        predicted: list[float] | None = None,
        baseline: list[float] | None = None,
        fold_for_group: list[int] | None = None,
        reported_model_rmse: float | None = None,
    ) -> None:
        actual = [float(value) for value in range(1, 11)]
        predicted = predicted or [
            value + (0.1 if index % 2 else -0.1) for index, value in enumerate(actual)
        ]
        baseline = baseline or [5.5] * 10
        fold_for_group = fold_for_group or [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
        lines = ["sample_id,group_id,fold,actual,predicted,baseline_predicted"]
        for index, (a, p, b, fold) in enumerate(
            zip(actual, predicted, baseline, fold_for_group, strict=True), start=1
        ):
            lines.append(f"E{index},G{index},{fold},{a},{p},{b}")
        root.joinpath("ques1_predictions.csv").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        model_metrics = self._regression_metrics(actual, predicted)
        baseline_metrics = self._regression_metrics(actual, baseline)
        root.joinpath("ques1_prediction_metrics.json").write_text(
            json.dumps(
                {
                    "validation_strategy": "GroupKFold by pregnant woman",
                    "primary_metric": {
                        "name": "rmse",
                        "model_value": (
                            model_metrics["rmse"]
                            if reported_model_rmse is None
                            else reported_model_rmse
                        ),
                        "baseline_value": baseline_metrics["rmse"],
                        "higher_is_better": False,
                    },
                    "secondary_metrics": {"r2": model_metrics["r2"]},
                }
            ),
            encoding="utf-8",
        )

    def test_problem_preserves_user_requirements(self) -> None:
        problem = Problem(
            task_id="task-1",
            ques_all="完整赛题",
            user_requirements="问题1必须输出预测值",
        )
        self.assertEqual(
            problem.model_dump()["user_requirements"], "问题1必须输出预测值"
        )

    def test_relationship_question_automatically_activates_strict_gate(self) -> None:
        contract = build_question_contract(
            question_key="ques1",
            question_text="分析浓度与孕周和BMI的相关特性，给出关系模型",
        )
        self.assertEqual(contract.problem_type, "regression")
        self.assertTrue(contract.requires_prediction_values)
        self.assertEqual(contract.minimum_relative_improvement, 0.05)

    def test_dynamic_lag_question_uses_system_identification_contract(self) -> None:
        contract = build_question_contract(
            question_key="ques2",
            question_text=(
                "建立动态\n\n数学模型，描述输入如何影响输出，给出输入变量的时滞参数，"
                "并进行参数估计与验证"
            ),
        )

        self.assertEqual(contract.problem_type, "system_identification")
        self.assertTrue(contract.requires_prediction_values)
        self.assertFalse(contract.require_baseline_outperformance)
        self.assertIn("系统辨识", contract.prompt_block())
        self.assertNotIn("主指标必须比同折基线至少改善 5%", contract.prompt_block())

    def test_regression_prompt_allows_evidence_backed_manual_review(self) -> None:
        prompt = self.contract.prompt_block()

        self.assertIn("manual_review", prompt)
        self.assertIn("manual_review_required", prompt)
        self.assertIn("失败原因", prompt)

    def test_question_scoped_requirement_does_not_leak_to_other_questions(self) -> None:
        question_two = build_question_contract(
            question_key="ques2",
            question_text="给出最早达标时间",
            user_requirements="问题1必须输出逐样本预测值",
        )
        self.assertFalse(question_two.requires_prediction_values)

    def test_flow_prompt_contains_global_quality_contract(self) -> None:
        flows = Flows(
            {
                "background": "NIPT",
                "ques_count": 1,
                "ques1": "分析 Y 染色体浓度关系",
            },
            user_requirements="问题1必须输出逐样本预测值",
        )
        result = flows.get_solution_flows(
            flows.questions,
            ModelerToCoder(questions_solution={"ques1": "使用回归模型"}),
        )

        prompt = result["ques1"]["coder_prompt"]
        self.assertIn("ques1_quality_report.json", prompt)
        self.assertIn("baseline_predicted", prompt)
        self.assertIn("至少改善 5%", prompt)
        self.assertIn('"preprocessing_inside_folds": true', prompt)
        self.assertIn('"group_isolation": true', prompt)
        self.assertIn('"target_leakage_checked": true', prompt)
        self.assertIn("eda_quality_report.json", result["eda"]["coder_prompt"])
        self.assertIn(
            "sensitivity_analysis_quality_report.json",
            result["sensitivity_analysis"]["coder_prompt"],
        )

    def test_missing_prediction_files_fail_instead_of_marking_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_quality_report(root, ["result.txt"])
            with self.assertRaisesRegex(
                DeliverableValidationError, "ques1_predictions.csv"
            ):
                validate_question_deliverables(root, self.contract)

    def test_repair_prompt_lists_all_missing_files_and_reusable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("ques1_model_metrics.csv").write_text(
                "Model,RMSE\nBaggedTrees,0.1\n", encoding="utf-8"
            )

            prompt = build_repair_prompt(
                self.contract,
                DeliverableValidationError("missing delivery files"),
                work_dir=root,
            )

            self.assertIn("ques1_quality_report.json", prompt)
            self.assertIn("ques1_predictions.csv", prompt)
            self.assertIn("ques1_prediction_metrics.json", prompt)
            self.assertIn("ques1_model_metrics.csv", prompt)

    def test_prediction_model_must_beat_grouped_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root, predicted=[5.6] * 10)
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )
            with self.assertRaisesRegex(ModelQualityValidationError, "未优于基线"):
                validate_question_deliverables(root, self.contract)

    def test_reported_metrics_are_recalculated_from_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root, reported_model_rmse=0.0001)
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )
            with self.assertRaisesRegex(DeliverableValidationError, "独立重算不一致"):
                validate_question_deliverables(root, self.contract)

    def test_manual_review_still_recalculates_prediction_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root, reported_model_rmse=0.0001)
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )
            report_path = root / "ques1_quality_report.json"
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            payload.update(
                {
                    "status": "manual_review",
                    "manual_review_required": True,
                    "failure_reason": "所有候选均未稳定超过分组基线，需要人工复核。",
                }
            )
            payload["robustness_checks"].append(
                {"name": "group bootstrap advantage", "passed": False}
            )
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            with self.assertRaisesRegex(DeliverableValidationError, "独立重算不一致"):
                validate_question_deliverables(root, self.contract)

    def test_valid_manual_review_skips_quality_thresholds_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root, predicted=[5.6] * 10)
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )
            report_path = root / "ques1_quality_report.json"
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            payload.update(
                {
                    "status": "manual_review",
                    "selected_model": "none_manual_review_required",
                    "manual_review_required": True,
                    "failure_reason": "所有候选均未稳定超过分组基线，需要人工复核。",
                }
            )
            payload["robustness_checks"].append(
                {"name": "group bootstrap advantage", "passed": False}
            )
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            report = validate_question_deliverables(root, self.contract)

            self.assertFalse(report.passed)
            self.assertTrue(report.manual_review_required)
            self.assertEqual(report.prediction_rows, 10)
            self.assertEqual(report.primary_metric_name, "rmse")

    def test_group_cannot_cross_validation_folds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root)
            text = root.joinpath("ques1_predictions.csv").read_text(encoding="utf-8")
            text = text.replace("E2,G2,2", "E2,G1,2")
            root.joinpath("ques1_predictions.csv").write_text(text, encoding="utf-8")
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )
            with self.assertRaisesRegex(DeliverableValidationError, "跨验证折"):
                validate_question_deliverables(root, self.contract)

    def test_valid_grouped_oof_predictions_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root)
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )
            report = validate_question_deliverables(root, self.contract)
            self.assertTrue(report.passed)
            self.assertEqual(report.prediction_rows, 10)
            self.assertEqual(report.paper_ready_images, ("figure.png",))

    def test_forward_time_origin_language_counts_as_grouped_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root)
            metrics_path = root / "ques1_prediction_metrics.json"
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            metrics["validation_strategy"] = (
                "3个前向连续时间折；独立单位为每日07:00多步预测起点；"
                "同一预测起点的多个时域目标只属于一个折"
            )
            metrics_path.write_text(
                json.dumps(metrics, ensure_ascii=False), encoding="utf-8"
            )
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )

            report = validate_question_deliverables(root, self.contract)

            self.assertTrue(report.passed)
            self.assertEqual(report.prediction_rows, 10)

    def test_fold_count_without_grouping_language_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root)
            metrics_path = root / "ques1_prediction_metrics.json"
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            metrics["validation_strategy"] = "3个前向连续时间折"
            metrics_path.write_text(
                json.dumps(metrics, ensure_ascii=False), encoding="utf-8"
            )
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )

            with self.assertRaisesRegex(
                DeliverableValidationError, "独立单位如何进入验证折"
            ):
                validate_question_deliverables(root, self.contract)

    def test_explicit_leakage_check_records_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root)
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )
            report_path = root / "ques1_quality_report.json"
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            payload["data_leakage_checks"] = [
                {
                    "name": "within_fold_preprocessing",
                    "category": "fold_preprocessing",
                    "passed": True,
                    "confirmed": True,
                },
                {
                    "name": "independent_subject_isolation",
                    "category": "subject_isolation",
                    "passed": True,
                    "confirmed": True,
                },
                {
                    "name": "target_leakage_check",
                    "category": "target_leakage",
                    "passed": True,
                    "confirmed": True,
                },
            ]
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            report = validate_question_deliverables(root, self.contract)
            self.assertTrue(report.passed)

    def test_regression_report_does_not_require_unused_type_specific(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root)
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )
            report_path = root / "ques1_quality_report.json"
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            payload.pop("type_specific")
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            report = validate_question_deliverables(root, self.contract)
            self.assertTrue(report.passed)

    def test_modeler_evidence_preserves_inference_and_small_csv_previews(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("ques1_coefficients_cluster_robust.csv").write_text(
                (
                    "term,estimate,holm_p,bootstrap_ci_low,bootstrap_ci_high\n"
                    "gw_c,0.00132,0.000159,0.000744,0.001955\n"
                    "bmi_c,-0.00220,0.003556,-0.003393,-0.000964\n"
                ),
                encoding="utf-8",
            )
            root.joinpath("ques1_quality_report.json").write_text(
                json.dumps(
                    {
                        "status": "pass",
                        "problem_type": "regression",
                        "selected_model": "robust linear relationship model",
                        "candidate_models": [],
                        "independent_unit": {"name": "subject"},
                        "data_leakage_checks": {},
                        "robustness_checks": [],
                        "limitations": ["limited support"],
                        "key_inference": {
                            "gestational_week_effect_per_week": 0.00132,
                            "gestational_week_holm_p": 0.000159,
                            "bmi_effect_per_unit": -0.00220,
                            "bmi_holm_p": 0.003556,
                        },
                        "validation_summary": {"oof_r2": 0.0935},
                        "artifacts": [
                            "ques1_coefficients_cluster_robust.csv",
                        ],
                        "paper_ready_images": ["effect.png"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            evidence = collect_model_quality_evidence(root, self.contract)

            quality = evidence["quality_report"]
            self.assertEqual(
                quality["key_inference"]["gestational_week_holm_p"],
                0.000159,
            )
            self.assertEqual(quality["validation_summary"]["oof_r2"], 0.0935)
            preview = evidence["supporting_artifact_previews"][
                "ques1_coefficients_cluster_robust.csv"
            ]
            self.assertEqual(preview["rows"][0]["term"], "gw_c")
            self.assertEqual(preview["rows"][1]["bootstrap_ci_high"], "-0.000964")

    def test_unconfirmed_leakage_check_record_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_predictions(root)
            self._write_quality_report(
                root,
                [
                    "result.txt",
                    "ques1_predictions.csv",
                    "ques1_prediction_metrics.json",
                ],
            )
            report_path = root / "ques1_quality_report.json"
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            payload["data_leakage_checks"] = [
                {
                    "name": "within_fold_preprocessing",
                    "category": "fold_preprocessing",
                    "passed": True,
                    "confirmed": True,
                },
                {
                    "name": "independent_subject_isolation",
                    "category": "subject_isolation",
                    "passed": True,
                    "confirmed": True,
                },
                {
                    "name": "target_leakage_check",
                    "category": "target_leakage",
                    "passed": True,
                    "confirmed": False,
                },
            ]
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                DeliverableValidationError,
                "data_leakage_checks",
            ):
                validate_question_deliverables(root, self.contract)

    def test_writer_failure_text_cannot_be_marked_complete(self) -> None:
        with self.assertRaisesRegex(DeliverableValidationError, "失败/占位标记"):
            validate_writer_section(
                "ques1",
                "任务失败，超过最大尝试次数。" * 100,
            )

    def test_writer_must_name_selected_model_from_quality_evidence(self) -> None:
        content = (
            "本节采用 Gamma-HRT 候选模型进行比较，公式为 $y=f(x)$。"
            "三折验证给出 RMSE=0.2501，并讨论参数不确定性和适用局限。"
        ) * 30

        with self.assertRaisesRegex(DeliverableValidationError, "Robust_state_6h"):
            validate_writer_section(
                "ques3",
                content,
                quality_report={"selected_model": "Robust_state_6h"},
            )

    def test_writer_must_name_each_selected_model_from_mapping(self) -> None:
        content = (
            "本节采用 Model_A 完成稳健性分析，公式为 $y=f(x)$。"
            "三折验证给出 RMSE=0.1778，并讨论预测局限与不确定性。"
        ) * 35

        with self.assertRaisesRegex(DeliverableValidationError, "Model_B"):
            validate_writer_section(
                "sensitivity_analysis",
                content,
                quality_report={
                    "selected_model": {
                        "Q1": "Model_A (fallback)",
                        "Q2": "Model_B",
                    }
                },
            )

    def test_writer_accepts_all_selected_models_from_mapping(self) -> None:
        content = (
            "本节分别采用 Model_A 与 Model_B 完成稳健性分析，公式为 $y=f(x)$。"
            "三折验证给出 RMSE=0.1778，并讨论预测局限与不确定性。"
        ) * 35

        validate_writer_section(
            "sensitivity_analysis",
            content,
            quality_report={
                "selected_model": {
                    "Q1": "Model_A (fallback)",
                    "Q2": "Model_B",
                }
            },
        )

    def test_writer_must_cover_requested_sensitivity_analysis(self) -> None:
        content = (
            "本节建立稳健状态模型，公式为 $y=f(x)$。"
            "三折验证给出 RMSE=0.1778，并讨论预测局限与不确定性。"
        ) * 35

        with self.assertRaisesRegex(DeliverableValidationError, "敏感性分析"):
            validate_writer_section(
                "ques3",
                content,
                question_text="分析不同输入变量对预测结果的敏感性。",
            )

    def test_writer_must_cover_requested_forecast_horizon(self) -> None:
        content = (
            "本节建立稳健状态模型并完成敏感性分析，公式为 $y=f(x)$。"
            "三折验证给出 RMSE=0.1778，并讨论预测局限与不确定性。"
        ) * 35

        with self.assertRaisesRegex(DeliverableValidationError, "1~12小时"):
            validate_writer_section(
                "ques3",
                content,
                question_text="建立模型预测未来1~12小时的出厂水浊度。",
            )


if __name__ == "__main__":
    unittest.main()
