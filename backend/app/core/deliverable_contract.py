"""Deterministic, task-type-aware quality gates for solver and paper outputs."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_REGRESSION_PATTERNS = (
    "预测",
    "估计",
    "回归",
    "关系模型",
    "相关特性",
    "变化规律",
    "拟合",
    "趋势",
    "浓度",
    "forecast",
    "regression",
)
_CLASSIFICATION_PATTERNS = (
    "分类",
    "判定",
    "识别",
    "异常检测",
    "诊断",
    "风险等级",
    "classification",
)
_OPTIMIZATION_PATTERNS = (
    "最优",
    "最佳",
    "优化",
    "最大化",
    "最小化",
    "调度",
    "路径规划",
    "决策方案",
    "optimization",
)
_EVALUATION_PATTERNS = (
    "综合评价",
    "评价体系",
    "排序",
    "排名",
    "评分",
    "evaluation",
)
_SIMULATION_PATTERNS = (
    "仿真",
    "模拟系统",
    "机理模型",
    "微分方程",
    "蒙特卡洛",
    "simulation",
)
_MANUAL_REVIEW_PROBLEM_TYPES = frozenset(
    {
        "eda",
        "sensitivity",
        "regression",
        "classification",
        "system_identification",
        "optimization",
        "evaluation",
        "simulation",
    }
)
_PREDICTIVE_PROBLEM_TYPES = frozenset(
    {"regression", "classification", "system_identification"}
)
_MODEL_QUALITY_PROBLEM_TYPES = frozenset(
    {
        "regression",
        "classification",
        "system_identification",
        "optimization",
        "evaluation",
        "simulation",
    }
)
_CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_FAILURE_MARKERS = (
    "任务失败",
    "执行过程中遇到错误",
    "超过最大尝试次数",
    "搜索文献失败",
    "NAType",
    "TODO",
    "PLACEHOLDER",
    "待补充",
    "待续写",
    "无法生成详细总结",
    "�",
)
_LEAKAGE_CHECK_ALIASES = {
    "preprocessing_inside_folds": {
        "preprocessing_inside_folds",
        "within_fold_preprocessing",
        "fold_preprocessing",
    },
    "group_isolation": {
        "group_isolation",
        "independent_subject_isolation",
        "subject_isolation",
    },
    "target_leakage_checked": {
        "target_leakage_checked",
        "target_leakage_check",
        "target_leakage",
    },
}


class DeliverableValidationError(RuntimeError):
    """Raised when a required artifact fails a machine-verifiable gate."""


class ModelQualityValidationError(DeliverableValidationError):
    """模型性能、可行性或稳定性不达标，必须交回建模手换模。"""


@dataclass(frozen=True)
class QuestionDeliverableContract:
    """Machine-checkable output contract for one workflow stage."""

    question_key: str
    user_requirements: str
    problem_type: str = "analysis"
    requires_quality_report: bool = True
    requires_prediction_values: bool = False
    require_grouped_validation: bool = False
    require_baseline_outperformance: bool = False
    minimum_relative_improvement: float = 0.05
    require_positive_oof_r2: bool = False
    minimum_candidate_models: int = 2
    minimum_robustness_checks: int = 2

    @property
    def prediction_filename(self) -> str:
        """Return the required OOF prediction filename."""
        return f"{self.question_key}_predictions.csv"

    @property
    def metrics_filename(self) -> str:
        """Return the required prediction-metrics filename."""
        return f"{self.question_key}_prediction_metrics.json"

    @property
    def quality_filename(self) -> str:
        """Return the required stage quality-report filename."""
        return f"{self.question_key}_quality_report.json"

    def prompt_block(self) -> str:
        """Render the non-optional contract injected into the coder prompt."""
        status_rule = (
            "所有检查真实通过后写 `pass`；若完整性检查通过、但真实质量冲突在本轮无法"
            "自动裁决，可写 `manual_review`，同时必须写 "
            "`manual_review_required=true`、非空失败原因、至少一项 `passed=false` 的"
            "真实检查并保留全部证据产物；禁止写未定义的 `fail` 状态"
            if self.problem_type in _MANUAL_REVIEW_PROBLEM_TYPES
            else "只能在所有检查真实通过后写 `pass`"
        )
        common = f"""
【冠军级硬门禁：未通过时禁止写论文、禁止进入下一问】
1. 本阶段类型固定为 `{self.problem_type}`，必须在工作目录根目录生成 `{self.quality_filename}`。
2. 质量报告必须包含：
   - `status`（{status_rule}）、`problem_type`、`selected_model`；
   - `candidate_models`：至少 {self.minimum_candidate_models} 个真实运行的方法，其中至少一个 `role` 为 `baseline`；
   - `independent_unit` 和 `data_leakage_checks`；
   - `robustness_checks`：至少 {self.minimum_robustness_checks} 项真实执行且通过的检查；
   - `limitations`：至少一条基于结果的局限；
   - `artifacts`：支撑结果的真实文件相对路径；
   - `paper_ready_images`：仅列入适合论文使用、支持核心结论的图片，不要把失败图和无效图交给写作手。
3. 复杂模型必须与简单基线在完全相同的数据划分和指标下比较。复杂模型不占优时，优先选择更简单模型；所有候选均不达标时必须报告门禁失败，不得包装成“完成”。
4. 禁止使用训练集 R²、条件随机效应 R²、全数据预处理后的交叉验证或肉眼看图代替独立验证。
5. 所有 JSON 数值、候选模型和产物路径都会被程序复核；虚构文件、虚构检验或指标不一致会直接失败。
""".strip()

        if self.problem_type == "eda":
            return (
                common
                + f"""

`{self.quality_filename}` 的 `type_specific` 还必须包含：
`raw_rows, cleaned_rows, missingness_checked, duplicates_checked, outliers_assessed, independent_unit_identified`。
数据驱动题六项必须真实完成；机理题可将不适用项说明为 `not_applicable`，但必须完成量纲和物理一致性检查并写入 `robustness_checks`。
"""
            )
        if self.problem_type == "sensitivity":
            return (
                common
                + f"""

`{self.quality_filename}` 的 `type_specific` 还必须包含：
`covered_questions, parameters_tested, scenarios, conclusions_grounded`。至少覆盖一个核心参数、3个扰动场景。
`conclusions_grounded` 可为兼容旧报告的 `true`；推荐写为可审计数组：
`[{{"conclusion":"...", "artifacts":["evidence.csv"]}}]`。数组中每条结论必须对应至少一个真实非空产物。
"""
            )
        if self.requires_prediction_values:
            metric_hint = (
                "classification 可使用 accuracy、balanced_accuracy 或 f1_macro"
                if self.problem_type == "classification"
                else "regression 首选 rmse，也可使用 mae 或 r2"
            )
            if self.require_baseline_outperformance:
                quality_rule = (
                    "主指标必须比同折基线至少改善 "
                    f"{self.minimum_relative_improvement:.0%}；回归任务的分组 OOF R² "
                    "必须大于0。分类任务另有豁免口径：误差率相对下降≥10%，或基线"
                    "准确率≥0.95 时不劣于基线，均视为通过。"
                    "否则必须如实进入 `manual_review`，不得伪造通过。"
                )
            else:
                quality_rule = (
                    "系统辨识的主门槛是时滞/参数可辨识性及不确定性，不强制用预测指标"
                    "击败持续性基线；预测指标仍须在同折数据上真实重算。主验证应覆盖"
                    "全部合法的时间原点，固定多步 free-run 只能作为次级压力测试。"
                )
            identification_rule = (
                "\n6. `type_specific` 必须记录 `validation_goal=system_identification`、"
                "`lag_identifiability`、`parameter_uncertainty_reported`、"
                "`all_valid_origins_primary` 和 `free_run_is_secondary`；无法辨识的变量"
                "必须明确写为 unidentifiable，不得伪造唯一时滞。"
                if self.problem_type == "system_identification"
                else ""
            )
            return (
                common
                + f"""

【逐样本独立验证契约】
1. `{self.quality_filename}` 中的 `data_leakage_checks` 必须至少包含以下布尔键，不要改成检查记录数组：
   {{"preprocessing_inside_folds": true, "group_isolation": true, "target_leakage_checked": true}}
2. 生成 `{self.prediction_filename}`，UTF-8 CSV，至少包含：
   `sample_id,group_id,fold,actual,predicted,baseline_predicted`。至少10行、3个验证折；同一 `group_id` 只能属于一个折。
3. 所有预处理、特征选择、调参都必须只使用对应训练折。重复测量、时间序列或空间数据必须按真实独立主体/时间/区域隔离。
4. 生成 `{self.metrics_filename}`：
   {{"validation_strategy":"按 group_id 分组的3折交叉验证...", "grouping_key":"group_id", "group_isolation":true, "primary_metric":{{"name":"rmse", "model_value":0.0, "baseline_value":0.0, "higher_is_better":false}}, "secondary_metrics":{{"r2":0.0}}}}
   `validation_strategy` 必须说明独立单位如何进入验证折；也可使用“独立单位为...，同一...只属于一个折”等等价表述。
   {metric_hint}。程序会从 CSV 独立重算并核对，不能只相信报告值。
5. {quality_rule}{identification_rule}
"""
            )
        if self.problem_type == "optimization":
            return (
                common
                + """

质量报告的 `type_specific` 必须包含：
`objective`（model_value、baseline_value、higher_is_better）、`feasible`、`max_constraint_violation`、`constraint_tolerance`、`sensitivity_scenarios`。
约束违反不得超过容差，至少3个敏感性场景，最终方案必须优于可行基线至少1%。
若所有真实候选都未达到1%门槛，可以保留已经验证的可行基线，但不得伪造改善：
`status` 仍写 `pass`（表示交付物通过），同时写
`type_specific.selection_decision="baseline_retained"` 和 `type_specific.fallback_solution`；
基线候选必须标记 `selected=true`，至少一个非基线候选必须 `actually_run=true`，
且 `fallback_solution.oof_risk` 必须与 `objective.baseline_value` 一致。
"""
            )
        if self.problem_type == "evaluation":
            return (
                common
                + """

质量报告的 `type_specific` 必须包含：
`rank_stability, alternative_method_agreement, sensitivity_scenarios`。排序稳定性和替代方法一致性均不得低于0.7，敏感性场景不少于3个。
"""
            )
        if self.problem_type == "simulation":
            return (
                common
                + """

质量报告的 `type_specific` 必须包含：
`calibration_error, baseline_error, physical_checks_passed, sensitivity_scenarios`。校准误差必须优于基线、物理检查通过，敏感性场景不少于3个。
"""
            )
        return (
            common
            + """

质量报告的 `type_specific` 必须包含：
`evidence_checks, uncertainty_reported, effect_size_reported`。至少两项独立证据检查，并报告不确定性和效应量。
"""
        )


@dataclass(frozen=True)
class DeliverableValidationReport:
    """Successful gate result with evidence used by the writer stage."""

    passed: bool
    prediction_rows: int = 0
    primary_metric_name: str | None = None
    model_value: float | None = None
    baseline_value: float | None = None
    paper_ready_images: tuple[str, ...] = ()
    manual_review_required: bool = False
    manual_review_reason: str | None = None


def _question_number(question_key: str) -> int | None:
    match = re.fullmatch(r"ques(\d+)", question_key)
    return int(match.group(1)) if match else None


def _scoped_question_numbers(requirements: str) -> set[int]:
    numbers: set[int] = set()
    pattern = re.compile(r"(?:问题|第)\s*([一二三四五六七八九十]|\d+)\s*(?:问|题)?")
    for token in pattern.findall(requirements):
        if token.isdigit():
            numbers.add(int(token))
        elif token in _CHINESE_NUMBERS:
            numbers.add(_CHINESE_NUMBERS[token])
    return numbers


def _detect_problem_type(text: str) -> str:
    lower = text.lower()
    compact = re.sub(r"\s+", "", lower)
    dynamic_lag_model = "时滞" in compact and any(
        token in compact
        for token in ("动态模型", "动态数学模型", "系统辨识", "传递函数")
    )
    if dynamic_lag_model:
        return "system_identification"
    if any(token in lower for token in _CLASSIFICATION_PATTERNS):
        return "classification"
    if any(token in lower for token in _OPTIMIZATION_PATTERNS):
        return "optimization"
    if any(token in lower for token in _EVALUATION_PATTERNS):
        return "evaluation"
    if any(token in lower for token in _SIMULATION_PATTERNS):
        return "simulation"
    if any(token in lower for token in _REGRESSION_PATTERNS):
        return "regression"
    return "analysis"


def build_question_contract(
    question_key: str,
    question_text: str,
    user_requirements: str = "",
) -> QuestionDeliverableContract:
    """Build a strict contract without relying on an LLM remembering rules."""
    requirements = user_requirements.strip()
    scoped_numbers = _scoped_question_numbers(requirements)
    number = _question_number(question_key)
    applies = not scoped_numbers or number in scoped_numbers
    combined = f"{question_text}\n{requirements if applies else ''}"
    problem_type = _detect_problem_type(combined)
    predictive = problem_type in _PREDICTIVE_PROBLEM_TYPES
    return QuestionDeliverableContract(
        question_key=question_key,
        user_requirements=requirements,
        problem_type=problem_type,
        requires_prediction_values=predictive,
        require_grouped_validation=predictive,
        require_baseline_outperformance=problem_type
        in {"regression", "classification"},
        minimum_relative_improvement=0.05,
        require_positive_oof_r2=problem_type == "regression",
    )


def build_stage_contract(stage_key: str) -> QuestionDeliverableContract:
    """Build strict contracts for EDA and sensitivity stages."""
    if stage_key not in {"eda", "sensitivity_analysis"}:
        raise ValueError(f"不支持的阶段: {stage_key}")
    problem_type = "eda" if stage_key == "eda" else "sensitivity"
    return QuestionDeliverableContract(
        question_key=stage_key,
        user_requirements="",
        problem_type=problem_type,
        minimum_candidate_models=0,
        minimum_robustness_checks=1,
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliverableValidationError(f"{path.name} 不是有效 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DeliverableValidationError(f"{path.name} 顶层必须是 JSON 对象")
    return payload


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DeliverableValidationError(f"{label} 必须是数值") from exc
    if not math.isfinite(number):
        raise DeliverableValidationError(f"{label} 必须是有限数值")
    return number


def _classification_gate_satisfied(
    problem_type: str,
    metric_name: str,
    model_value: float,
    baseline_value: float,
) -> bool:
    """分类高基线时 5% 相对改善在数学上不可达，改用误差余量口径豁免。

    豁免条件（模型不得劣于基线）：基线≥0.95 时持平即可；
    否则误差率相对下降≥10% 视为真实改进。
    """
    if problem_type != "classification":
        return False
    if metric_name not in {"accuracy", "balanced_accuracy", "f1_macro"}:
        return False
    if model_value < baseline_value:
        return False
    headroom = 1.0 - baseline_value
    if headroom <= 1e-9 or baseline_value >= 0.95:
        return True
    return (model_value - baseline_value) / headroom >= 0.10 - 1e-9


def _relative_improvement(
    model_value: float, baseline_value: float, higher_is_better: bool
) -> float:
    beats_baseline = (
        model_value > baseline_value
        if higher_is_better
        else model_value < baseline_value
    )
    if not beats_baseline:
        return -math.inf
    denominator = abs(baseline_value)
    if denominator == 0:
        return math.inf
    return (
        (model_value - baseline_value) / denominator
        if higher_is_better
        else (baseline_value - model_value) / denominator
    )


def _resolve_artifact(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise DeliverableValidationError(
            f"产物路径越出工作目录: {relative_path}"
        ) from exc
    return candidate


def _normalize_check_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _leakage_checks_confirmed(value: Any) -> bool:
    if isinstance(value, dict):
        return all(value.get(key) is True for key in _LEAKAGE_CHECK_ALIASES)
    if not isinstance(value, list):
        return False

    confirmations: dict[str, list[bool]] = defaultdict(list)
    for item in value:
        if not isinstance(item, dict):
            continue
        labels = {
            _normalize_check_label(item.get("name", "")),
            _normalize_check_label(item.get("category", "")),
        }
        confirmed = item.get("passed") is True and item.get("confirmed") is True
        for required, aliases in _LEAKAGE_CHECK_ALIASES.items():
            if labels & aliases:
                confirmations[required].append(confirmed)

    return all(
        confirmations[required] and all(confirmations[required])
        for required in _LEAKAGE_CHECK_ALIASES
    )


def _run_checks_collecting(
    checks: list[Callable[[], None]],
) -> list[DeliverableValidationError]:
    """逐项执行检查并收集全部违规，避免 fail-fast 每轮只暴露一个错误。"""
    errors: list[DeliverableValidationError] = []
    for check in checks:
        try:
            check()
        except DeliverableValidationError as exc:
            errors.append(exc)
    return errors


def _raise_collected(errors: list[DeliverableValidationError]) -> None:
    """把收集到的违规一次性抛出；含质量类违规时按换模路径处理。"""
    if not errors:
        return
    if len(errors) == 1:
        raise errors[0]
    error_type = (
        ModelQualityValidationError
        if any(isinstance(item, ModelQualityValidationError) for item in errors)
        else DeliverableValidationError
    )
    details = "；".join(f"[{index}] {item}" for index, item in enumerate(errors, 1))
    raise error_type(f"共 {len(errors)} 项未通过，必须在本轮全部修复：{details}")


def _peek_quality_report(
    root: Path, contract: QuestionDeliverableContract
) -> dict[str, Any]:
    """尽力读取质量报告，在校验失败时仍能判定 manual_review 口径。"""
    path = root / contract.quality_filename
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _validate_quality_report(
    root: Path,
    contract: QuestionDeliverableContract,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    path = root / contract.quality_filename
    if not path.is_file():
        raise DeliverableValidationError(f"缺少 {contract.quality_filename}")
    report = _load_json_object(path)
    status = str(report.get("status", "")).strip().lower()
    manual_review = (
        status == "manual_review"
        and contract.problem_type in _MANUAL_REVIEW_PROBLEM_TYPES
    )
    checked_images: list[str] = []

    def check_status() -> None:
        if status != "pass" and not manual_review:
            error_type = (
                ModelQualityValidationError
                if status in {"fail", "failed", "refine"}
                and contract.problem_type in _MODEL_QUALITY_PROBLEM_TYPES
                else DeliverableValidationError
            )
            raise error_type(
                f"{contract.quality_filename} status 必须为 pass"
                + (
                    " 或 manual_review"
                    if contract.problem_type in _MANUAL_REVIEW_PROBLEM_TYPES
                    else ""
                )
            )

    def check_problem_type() -> None:
        if report.get("problem_type") != contract.problem_type:
            raise DeliverableValidationError(
                f"problem_type 应为 {contract.problem_type}，实际为 {report.get('problem_type')}"
            )

    def check_selected_model() -> None:
        if not contract.minimum_candidate_models:
            return
        if not str(report.get("selected_model", "")).strip():
            raise DeliverableValidationError("selected_model 不能为空")

    def check_candidate_models() -> None:
        if not contract.minimum_candidate_models:
            return
        candidates = report.get("candidate_models")
        if (
            not isinstance(candidates, list)
            or len(candidates) < contract.minimum_candidate_models
        ):
            raise DeliverableValidationError(
                f"candidate_models 至少需要 {contract.minimum_candidate_models} 个真实候选"
            )
        if not any(
            isinstance(item, dict) and item.get("role") == "baseline"
            for item in candidates
        ):
            raise DeliverableValidationError("candidate_models 必须包含 baseline")

    def check_robustness() -> None:
        robustness = report.get("robustness_checks")
        passed_robustness = (
            [
                item
                for item in robustness
                if isinstance(item, dict) and item.get("passed") is True
            ]
            if isinstance(robustness, list)
            else []
        )
        if len(passed_robustness) < contract.minimum_robustness_checks:
            raise DeliverableValidationError(
                f"至少需要 {contract.minimum_robustness_checks} 项已通过的稳健性检查"
            )

    def check_limitations() -> None:
        if contract.problem_type in {"eda", "sensitivity"}:
            return
        limitations = report.get("limitations")
        if not isinstance(limitations, list) or not any(
            str(item).strip() for item in limitations
        ):
            raise DeliverableValidationError("limitations 至少需要一条真实局限")

    def check_artifacts() -> None:
        artifacts = report.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise DeliverableValidationError("artifacts 至少需要一个真实产物路径")
        missing = []
        for item in artifacts:
            artifact = _resolve_artifact(root, str(item))
            if not artifact.is_file() or artifact.stat().st_size == 0:
                missing.append(str(item))
        if missing:
            raise DeliverableValidationError(
                f"产物不存在或为空: {', '.join(missing)}"
            )

    def check_images() -> None:
        images = report.get("paper_ready_images", [])
        if not isinstance(images, list):
            raise DeliverableValidationError("paper_ready_images 必须是数组")
        problems = []
        for item in images:
            image = _resolve_artifact(root, str(item))
            if image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
                problems.append(f"论文图片格式不支持: {item}")
                continue
            if not image.is_file() or image.stat().st_size == 0:
                problems.append(f"论文图片不存在或为空: {item}")
                continue
            checked_images.append(str(item))
        if problems:
            raise DeliverableValidationError("；".join(problems))
        if (
            contract.problem_type in _MODEL_QUALITY_PROBLEM_TYPES
            and not checked_images
            and not manual_review
        ):
            raise DeliverableValidationError("本问题至少需要一张通过筛选的论文核心图")

    def check_leakage() -> None:
        if contract.problem_type not in _PREDICTIVE_PROBLEM_TYPES:
            return
        leakage = report.get("data_leakage_checks")
        if not _leakage_checks_confirmed(leakage):
            raise DeliverableValidationError(
                "data_leakage_checks 必须确认折内预处理、主体隔离和目标泄露检查；"
                "标准布尔键为 preprocessing_inside_folds、group_isolation、"
                "target_leakage_checked"
            )

    def check_independent_unit() -> None:
        if contract.problem_type not in _PREDICTIVE_PROBLEM_TYPES:
            return
        if not str(report.get("independent_unit", "")).strip():
            raise DeliverableValidationError("independent_unit 不能为空")

    def check_type_specific() -> None:
        type_specific = report.get("type_specific")
        if type_specific is None and contract.problem_type in {
            "regression",
            "classification",
        }:
            type_specific = {}
        if not isinstance(type_specific, dict):
            raise DeliverableValidationError("type_specific 必须是 JSON 对象")
        _validate_type_specific(type_specific, contract, report, root=root)

    def check_manual_review_flag() -> None:
        if not (manual_review and contract.problem_type in _PREDICTIVE_PROBLEM_TYPES):
            return
        if report.get("manual_review_required") is not True:
            raise DeliverableValidationError(
                "预测型任务进入 manual_review 时必须写 manual_review_required=true"
            )

    def check_manual_review_reason() -> None:
        if not (manual_review and contract.problem_type in _PREDICTIVE_PROBLEM_TYPES):
            return
        reason = str(
            report.get("gate_failure_reason")
            or report.get("failure_reason")
            or ""
        ).strip()
        if not reason:
            raise DeliverableValidationError(
                "预测型任务进入 manual_review 时必须提供非空失败原因"
            )

    def check_manual_review_failed_checks() -> None:
        if not (manual_review and contract.problem_type in _PREDICTIVE_PROBLEM_TYPES):
            return
        failed_checks = [
            item
            for item in report.get("robustness_checks", [])
            if isinstance(item, dict) and item.get("passed") is False
        ]
        if not failed_checks:
            raise DeliverableValidationError(
                "预测型任务进入 manual_review 时至少需要一项 passed=false 的真实检查"
            )

    _raise_collected(
        _run_checks_collecting(
            [
                check_status,
                check_problem_type,
                check_selected_model,
                check_candidate_models,
                check_robustness,
                check_limitations,
                check_artifacts,
                check_images,
                check_leakage,
                check_independent_unit,
                check_type_specific,
                check_manual_review_flag,
                check_manual_review_reason,
                check_manual_review_failed_checks,
            ]
        )
    )
    return report, tuple(checked_images)


def _validate_type_specific(
    values: dict[str, Any],
    contract: QuestionDeliverableContract,
    report: dict[str, Any] | None = None,
    root: Path | None = None,
) -> None:
    problem_type = contract.problem_type
    if problem_type in {"regression", "classification"}:
        return
    checks: list[Callable[[], None]] = []
    if problem_type == "system_identification":

        def check_goal() -> None:
            if values.get("validation_goal") != "system_identification":
                raise DeliverableValidationError(
                    "system_identification 必须声明 validation_goal"
                )

        def check_lag_identifiability() -> None:
            lag_identifiability = values.get("lag_identifiability")
            if not isinstance(lag_identifiability, dict) or not lag_identifiability:
                raise DeliverableValidationError(
                    "system_identification 必须提供 lag_identifiability"
                )
            allowed_statuses = {
                "identifiable",
                "partially_identifiable",
                "unidentifiable",
            }
            for variable, evidence in lag_identifiability.items():
                if not isinstance(evidence, dict):
                    raise DeliverableValidationError(
                        f"lag_identifiability.{variable} 必须是对象"
                    )
                if evidence.get("status") not in allowed_statuses:
                    raise DeliverableValidationError(
                        f"lag_identifiability.{variable}.status 无效"
                    )
                if not str(evidence.get("evidence", "")).strip():
                    raise DeliverableValidationError(
                        f"lag_identifiability.{variable} 必须引用证据"
                    )

        def check_bool_fields() -> None:
            invalid = [
                field
                for field in (
                    "parameter_uncertainty_reported",
                    "all_valid_origins_primary",
                    "free_run_is_secondary",
                )
                if not isinstance(values.get(field), bool)
            ]
            if invalid:
                raise DeliverableValidationError(
                    f"{', '.join(invalid)} 必须是布尔值"
                )

        def check_pass_condition() -> None:
            fields = (
                "parameter_uncertainty_reported",
                "all_valid_origins_primary",
                "free_run_is_secondary",
            )
            if not all(isinstance(values.get(field), bool) for field in fields):
                # 字段缺失/类型错由 check_bool_fields 报格式错，不误判为质量失败
                return
            status = str((report or {}).get("status", "")).strip().lower()
            if status == "pass" and not all(
                values.get(field) is True for field in fields
            ):
                raise ModelQualityValidationError(
                    "系统辨识主验证、参数不确定性或 free-run 分层未达到通过条件"
                )

        checks = [
            check_goal,
            check_lag_identifiability,
            check_bool_fields,
            check_pass_condition,
        ]
    elif problem_type == "eda":

        def check_rows() -> None:
            raw_rows = int(_finite_number(values.get("raw_rows"), "raw_rows"))
            cleaned_rows = int(
                _finite_number(values.get("cleaned_rows"), "cleaned_rows")
            )
            if raw_rows <= 0 or cleaned_rows <= 0 or cleaned_rows > raw_rows:
                raise DeliverableValidationError(
                    "EDA 行数必须满足 0 < cleaned_rows <= raw_rows"
                )

        def check_eda_flags() -> None:
            flags = (
                "missingness_checked",
                "duplicates_checked",
                "outliers_assessed",
                "independent_unit_identified",
            )
            if not all(values.get(key) in {True, "not_applicable"} for key in flags):
                raise DeliverableValidationError("EDA 必需检查未完整执行")

        checks = [check_rows, check_eda_flags]
    elif problem_type == "sensitivity":

        def check_covered() -> None:
            covered = values.get("covered_questions")
            if not isinstance(covered, list) or not covered:
                raise DeliverableValidationError("敏感性分析必须覆盖至少一个问题")

        def check_parameters() -> None:
            if (
                int(
                    _finite_number(
                        values.get("parameters_tested"), "parameters_tested"
                    )
                )
                < 1
            ):
                raise DeliverableValidationError("敏感性分析至少需要一个核心参数")

        def check_scenarios() -> None:
            if int(_finite_number(values.get("scenarios"), "scenarios")) < 3:
                raise DeliverableValidationError("敏感性分析至少需要3个扰动场景")

        def check_grounded() -> None:
            grounded = values.get("conclusions_grounded")
            if grounded is True:
                return
            if not isinstance(grounded, list) or not grounded:
                raise DeliverableValidationError(
                    "敏感性结论必须为 true 或非空的结论-产物证据数组"
                )
            for index, item in enumerate(grounded, start=1):
                if not isinstance(item, dict):
                    raise DeliverableValidationError(
                        f"第 {index} 条敏感性结论证据必须是 JSON 对象"
                    )
                if not str(item.get("conclusion", "")).strip():
                    raise DeliverableValidationError(
                        f"第 {index} 条敏感性结论不能为空"
                    )
                evidence_files = item.get("artifacts")
                if not isinstance(evidence_files, list) or not evidence_files:
                    raise DeliverableValidationError(
                        f"第 {index} 条敏感性结论至少需要一个证据产物"
                    )
                if root is None:
                    raise DeliverableValidationError(
                        "无法定位敏感性结论的证据根目录"
                    )
                for evidence_file in evidence_files:
                    evidence_path = _resolve_artifact(root, str(evidence_file))
                    if (
                        not evidence_path.is_file()
                        or evidence_path.stat().st_size == 0
                    ):
                        raise DeliverableValidationError(
                            f"敏感性结论证据不存在或为空: {evidence_file}"
                        )

        checks = [check_covered, check_parameters, check_scenarios, check_grounded]
    elif problem_type == "optimization":

        def check_objective() -> None:
            objective = values.get("objective")
            if not isinstance(objective, dict):
                raise DeliverableValidationError("optimization objective 必须是对象")
            model = _finite_number(
                objective.get("model_value"), "objective.model_value"
            )
            baseline = _finite_number(
                objective.get("baseline_value"), "objective.baseline_value"
            )
            higher = objective.get("higher_is_better")
            if not isinstance(higher, bool):
                raise DeliverableValidationError(
                    "objective.higher_is_better 必须是布尔值"
                )
            improvement = _relative_improvement(model, baseline, higher)
            baseline_retained = (
                values.get("selection_decision") == "baseline_retained"
            )
            if improvement < 0.01 and not baseline_retained:
                raise ModelQualityValidationError("优化方案未比可行基线改善至少1%")
            if baseline_retained:
                if improvement >= 0.01:
                    raise DeliverableValidationError(
                        "候选已达到1%改善时不得标记 baseline_retained"
                    )
                fallback = values.get("fallback_solution")
                if not isinstance(fallback, dict):
                    raise DeliverableValidationError(
                        "baseline_retained 必须提供 fallback_solution"
                    )
                fallback_value = _finite_number(
                    fallback.get("oof_risk"), "fallback_solution.oof_risk"
                )
                if not math.isclose(
                    fallback_value,
                    baseline,
                    rel_tol=1e-6,
                    abs_tol=1e-9,
                ):
                    raise DeliverableValidationError(
                        "fallback_solution.oof_risk 必须与 objective.baseline_value 一致"
                    )
                if not str(fallback.get("source", "")).strip():
                    raise DeliverableValidationError(
                        "baseline_retained 必须注明 fallback_solution.source"
                    )
                candidates = (report or {}).get("candidate_models")
                if not isinstance(candidates, list):
                    raise DeliverableValidationError(
                        "baseline_retained 必须提供 candidate_models"
                    )
                selected_baseline = any(
                    isinstance(item, dict)
                    and item.get("role") == "baseline"
                    and item.get("selected") is True
                    for item in candidates
                )
                executed_challenger = any(
                    isinstance(item, dict)
                    and item.get("role") != "baseline"
                    and item.get("actually_run") is True
                    for item in candidates
                )
                if not selected_baseline:
                    raise DeliverableValidationError(
                        "baseline_retained 必须将一个 baseline 标记为 selected=true"
                    )
                if not executed_challenger:
                    raise DeliverableValidationError(
                        "baseline_retained 至少需要一个 actually_run=true 的非基线候选"
                    )
                if not str((report or {}).get("gate_decision", "")).strip():
                    raise DeliverableValidationError(
                        "baseline_retained 必须在 gate_decision 中解释候选失败与回退"
                    )

        def check_feasible() -> None:
            feasible = values.get("feasible")
            if not isinstance(feasible, bool):
                raise DeliverableValidationError("type_specific.feasible 必须是布尔值")
            if feasible is not True:
                raise ModelQualityValidationError("优化方案不可行")

        def check_constraints() -> None:
            violation = _finite_number(
                values.get("max_constraint_violation"), "max_constraint_violation"
            )
            tolerance = _finite_number(
                values.get("constraint_tolerance"), "constraint_tolerance"
            )
            if violation > tolerance:
                raise ModelQualityValidationError("约束违反超过容差")

        def check_opt_scenarios() -> None:
            sensitivity_scenarios = values.get("sensitivity_scenarios")
            scenario_count = (
                len(sensitivity_scenarios)
                if isinstance(sensitivity_scenarios, list)
                else int(
                    _finite_number(sensitivity_scenarios, "sensitivity_scenarios")
                )
            )
            if scenario_count < 3:
                raise DeliverableValidationError("优化问题至少需要3个敏感性场景")

        checks = [check_objective, check_feasible, check_constraints, check_opt_scenarios]
    elif problem_type == "evaluation":

        def check_rank_stability() -> None:
            if _finite_number(values.get("rank_stability"), "rank_stability") < 0.7:
                raise ModelQualityValidationError("排序稳定性低于0.7")

        def check_agreement() -> None:
            if (
                _finite_number(
                    values.get("alternative_method_agreement"),
                    "alternative_method_agreement",
                )
                < 0.7
            ):
                raise ModelQualityValidationError("替代方法一致性低于0.7")

        def check_eval_scenarios() -> None:
            if (
                int(
                    _finite_number(
                        values.get("sensitivity_scenarios"), "sensitivity_scenarios"
                    )
                )
                < 3
            ):
                raise DeliverableValidationError("评价问题至少需要3个敏感性场景")

        checks = [check_rank_stability, check_agreement, check_eval_scenarios]
    elif problem_type == "simulation":

        def check_calibration() -> None:
            calibration = _finite_number(
                values.get("calibration_error"), "calibration_error"
            )
            baseline = _finite_number(values.get("baseline_error"), "baseline_error")
            if calibration >= baseline:
                raise ModelQualityValidationError("仿真校准误差未优于基线")

        def check_physical() -> None:
            physical = values.get("physical_checks_passed")
            if not isinstance(physical, bool):
                raise DeliverableValidationError(
                    "type_specific.physical_checks_passed 必须是布尔值"
                )
            if physical is not True:
                raise ModelQualityValidationError("物理一致性检查未通过")

        def check_sim_scenarios() -> None:
            if (
                int(
                    _finite_number(
                        values.get("sensitivity_scenarios"), "sensitivity_scenarios"
                    )
                )
                < 3
            ):
                raise DeliverableValidationError("仿真问题至少需要3个敏感性场景")

        checks = [check_calibration, check_physical, check_sim_scenarios]
    else:

        def check_evidence() -> None:
            if (
                int(_finite_number(values.get("evidence_checks"), "evidence_checks"))
                < 2
            ):
                raise DeliverableValidationError("分析问题至少需要两项独立证据检查")

        def check_uncertainty() -> None:
            if values.get("uncertainty_reported") is not True:
                raise DeliverableValidationError("分析问题必须报告不确定性")

        def check_effect_size() -> None:
            if values.get("effect_size_reported") is not True:
                raise DeliverableValidationError("分析问题必须报告效应量")

        checks = [check_evidence, check_uncertainty, check_effect_size]
    _raise_collected(_run_checks_collecting(checks))


def _load_prediction_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {
            "sample_id",
            "group_id",
            "fold",
            "actual",
            "predicted",
            "baseline_predicted",
        }
        fields = set(reader.fieldnames or [])
        missing = sorted(required - fields)
        if missing:
            raise DeliverableValidationError(
                f"{path.name} 缺少必需列: {', '.join(missing)}"
            )
        rows = list(reader)

    if len(rows) < 10:
        raise DeliverableValidationError(f"{path.name} 至少需要10行独立 OOF 预测")
    sample_ids: set[str] = set()
    group_folds: dict[str, set[str]] = defaultdict(set)
    folds: set[str] = set()
    for index, row in enumerate(rows, start=2):
        for field in ("sample_id", "group_id", "fold"):
            if not str(row.get(field, "")).strip():
                raise DeliverableValidationError(
                    f"{path.name} 第 {index} 行的 {field} 为空"
                )
        sample_id = str(row["sample_id"])
        if sample_id in sample_ids:
            raise DeliverableValidationError(f"sample_id 重复: {sample_id}")
        sample_ids.add(sample_id)
        group_id = str(row["group_id"])
        fold = str(row["fold"])
        group_folds[group_id].add(fold)
        folds.add(fold)
        for field in ("actual", "predicted", "baseline_predicted"):
            _finite_number(row.get(field), f"{path.name} 第 {index} 行的 {field}")
    leaking = sorted(
        group for group, assigned in group_folds.items() if len(assigned) > 1
    )
    if leaking:
        raise DeliverableValidationError(
            f"同一 group_id 跨验证折，存在数据泄露: {', '.join(leaking[:5])}"
        )
    if len(folds) < 3:
        raise DeliverableValidationError("OOF 验证至少需要3个折")
    return rows


def _regression_metrics(
    actual: list[float], predicted: list[float]
) -> dict[str, float]:
    errors = [a - p for a, p in zip(actual, predicted, strict=True)]
    mse = sum(error * error for error in errors) / len(errors)
    mae = sum(abs(error) for error in errors) / len(errors)
    mean_actual = sum(actual) / len(actual)
    total = sum((value - mean_actual) ** 2 for value in actual)
    r2 = (
        1.0 - sum(error * error for error in errors) / total if total > 0 else -math.inf
    )
    return {"rmse": math.sqrt(mse), "mae": mae, "r2": r2}


def _classification_metrics(
    actual: list[float], predicted: list[float]
) -> dict[str, float]:
    labels = sorted(set(actual) | set(predicted))
    accuracy = sum(a == p for a, p in zip(actual, predicted, strict=True)) / len(actual)
    recalls: list[float] = []
    f1_scores: list[float] = []
    for label in labels:
        true_positive = sum(
            a == label and p == label for a, p in zip(actual, predicted, strict=True)
        )
        false_positive = sum(
            a != label and p == label for a, p in zip(actual, predicted, strict=True)
        )
        false_negative = sum(
            a == label and p != label for a, p in zip(actual, predicted, strict=True)
        )
        recall_denominator = true_positive + false_negative
        precision_denominator = true_positive + false_positive
        recall = true_positive / recall_denominator if recall_denominator else 0.0
        precision = (
            true_positive / precision_denominator if precision_denominator else 0.0
        )
        recalls.append(recall)
        f1_scores.append(
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )
    return {
        "accuracy": accuracy,
        "balanced_accuracy": sum(recalls) / len(recalls),
        "f1_macro": sum(f1_scores) / len(f1_scores),
    }


def _assert_reported_metric(reported: float, calculated: float, label: str) -> None:
    if not math.isclose(reported, calculated, rel_tol=1e-3, abs_tol=1e-6):
        raise DeliverableValidationError(
            f"{label} 与CSV独立重算不一致: report={reported}, calculated={calculated}"
        )


def _documents_grouped_validation(metrics: dict[str, Any]) -> bool:
    """Return whether metrics document how independent units enter folds."""
    strategy = re.sub(
        r"\s+", "", str(metrics.get("validation_strategy", "")).lower()
    )
    has_fold_language = any(
        term in strategy
        for term in ("fold", "交叉验证", "验证折", "时间折", "外折")
    ) or bool(re.search(r"(?:\d+|[一二三四五六七八九十]+)个?折", strategy))
    has_group_language = any(
        term in strategy
        for term in ("group", "分组", "主体", "独立单位", "预测起点")
    )
    grouping_key = metrics.get("grouping_key")
    has_structured_grouping = (
        isinstance(grouping_key, str)
        and bool(grouping_key.strip())
        and metrics.get("group_isolation") is True
    )
    return has_fold_language and (has_group_language or has_structured_grouping)


def _validate_predictions(
    root: Path,
    contract: QuestionDeliverableContract,
    *,
    enforce_quality: bool = True,
) -> tuple[int, str, float, float]:
    prediction_path = root / contract.prediction_filename
    metrics_path = root / contract.metrics_filename
    if not prediction_path.is_file():
        raise DeliverableValidationError(f"缺少 {contract.prediction_filename}")
    if not metrics_path.is_file():
        raise DeliverableValidationError(f"缺少 {contract.metrics_filename}")

    rows = _load_prediction_rows(prediction_path)
    metrics = _load_json_object(metrics_path)
    if contract.require_grouped_validation and not _documents_grouped_validation(
        metrics
    ):
        raise DeliverableValidationError(
            "validation_strategy 必须明确记录独立单位如何进入验证折，或提供"
            " grouping_key + group_isolation=true"
        )

    actual = [float(row["actual"]) for row in rows]
    predicted = [float(row["predicted"]) for row in rows]
    baseline_predicted = [float(row["baseline_predicted"]) for row in rows]
    if contract.problem_type == "classification":
        model_metrics = _classification_metrics(actual, predicted)
        baseline_metrics = _classification_metrics(actual, baseline_predicted)
        allowed = set(model_metrics)
    else:
        model_metrics = _regression_metrics(actual, predicted)
        baseline_metrics = _regression_metrics(actual, baseline_predicted)
        allowed = {"rmse", "mae", "r2"}

    primary = metrics.get("primary_metric")
    if not isinstance(primary, dict):
        raise DeliverableValidationError("primary_metric 必须是 JSON 对象")
    name = str(primary.get("name", "")).strip().lower()
    if name not in allowed:
        raise DeliverableValidationError(
            f"{contract.problem_type} 不支持主指标 {name or '<empty>'}"
        )
    model_value = _finite_number(primary.get("model_value"), "model_value")
    baseline_value = _finite_number(primary.get("baseline_value"), "baseline_value")
    higher = primary.get("higher_is_better")
    if not isinstance(higher, bool):
        raise DeliverableValidationError("higher_is_better 必须是布尔值")
    expected_higher = name not in {"rmse", "mae"}
    if higher != expected_higher:
        raise DeliverableValidationError(f"{name} 的 higher_is_better 设置错误")
    _assert_reported_metric(model_value, model_metrics[name], f"model {name}")
    _assert_reported_metric(baseline_value, baseline_metrics[name], f"baseline {name}")

    improvement = _relative_improvement(model_value, baseline_value, higher)
    if (
        enforce_quality
        and contract.require_baseline_outperformance
        and improvement == -math.inf
        and not _classification_gate_satisfied(
            contract.problem_type, name, model_value, baseline_value
        )
    ):
        raise ModelQualityValidationError(
            f"模型未优于基线: {name} model={model_value}, baseline={baseline_value}"
        )
    if (
        enforce_quality
        and contract.require_baseline_outperformance
        and improvement < contract.minimum_relative_improvement
        and not _classification_gate_satisfied(
            contract.problem_type, name, model_value, baseline_value
        )
    ):
        raise ModelQualityValidationError(
            "模型相对基线改善不足: "
            f"{improvement:.2%} < {contract.minimum_relative_improvement:.2%}"
            + (
                "；分类豁免口径同样未满足（误差率相对下降<10% 且非高基线持平）"
                if contract.problem_type == "classification"
                else ""
            )
        )

    secondary = metrics.get("secondary_metrics")
    if contract.require_positive_oof_r2:
        if not isinstance(secondary, dict) or "r2" not in secondary:
            raise DeliverableValidationError("secondary_metrics 必须包含分组 OOF r2")
        reported_r2 = _finite_number(secondary.get("r2"), "secondary_metrics.r2")
        calculated_r2 = model_metrics["r2"]
        _assert_reported_metric(reported_r2, calculated_r2, "OOF r2")
        if enforce_quality and calculated_r2 <= 0:
            raise ModelQualityValidationError(
                f"分组 OOF R² 必须大于0，当前为 {calculated_r2}"
            )
    return len(rows), name, model_value, baseline_value


def validate_question_deliverables(
    work_dir: str | Path,
    contract: QuestionDeliverableContract,
) -> DeliverableValidationReport:
    """Validate one stage and reject weak, ungrounded or incomplete results."""
    root = Path(work_dir)
    errors: list[DeliverableValidationError] = []
    quality_report: dict[str, Any] = {}
    images: tuple[str, ...] = ()
    report_readable = True
    try:
        quality_report, images = _validate_quality_report(root, contract)
    except DeliverableValidationError as exc:
        # 质量报告失败时仍继续校验预测产物，把两边的违规一次性报全。
        errors.append(exc)
        quality_report = _peek_quality_report(root, contract)
        # 报告缺失/损坏属于格式问题：此时不强推质量阈值，
        # 避免把格式错误误诊为需要换模的质量失败。
        report_readable = bool(quality_report)
    manual_review = quality_report.get("status") == "manual_review"
    if not contract.requires_prediction_values:
        _raise_collected(errors)
        return DeliverableValidationReport(
            passed=not manual_review,
            paper_ready_images=images,
            manual_review_required=manual_review,
            manual_review_reason=(
                _manual_review_reason(quality_report)
                if manual_review
                else None
            ),
        )
    rows, name, model, baseline = 0, None, None, None
    try:
        rows, name, model, baseline = _validate_predictions(
            root,
            contract,
            enforce_quality=not manual_review and report_readable,
        )
    except DeliverableValidationError as exc:
        errors.append(exc)
    _raise_collected(errors)
    return DeliverableValidationReport(
        passed=not manual_review,
        prediction_rows=rows,
        primary_metric_name=name,
        model_value=model,
        baseline_value=baseline,
        paper_ready_images=images,
        manual_review_required=manual_review,
        manual_review_reason=(
            _manual_review_reason(quality_report)
            if manual_review
            else None
        ),
    )


def _manual_review_reason(quality_report: dict[str, Any]) -> str:
    """Return the most specific persisted reason for a manual-review gate."""
    direct = quality_report.get("gate_failure_reason") or quality_report.get(
        "failure_reason"
    )
    if str(direct or "").strip():
        return str(direct).strip()
    reasons = quality_report.get("failure_reasons")
    if isinstance(reasons, list):
        joined = "；".join(str(item).strip() for item in reasons if str(item).strip())
        if joined:
            return joined
    type_specific = quality_report.get("type_specific")
    if isinstance(type_specific, dict) and str(
        type_specific.get("gate_failure_reason", "")
    ).strip():
        return str(type_specific["gate_failure_reason"]).strip()
    gate_failures = quality_report.get("gate_failures")
    if isinstance(gate_failures, dict) and str(
        gate_failures.get("decision", "")
    ).strip():
        return str(gate_failures["decision"]).strip()
    return "质量报告要求人工复核"


def collect_model_quality_evidence(
    work_dir: str | Path,
    contract: QuestionDeliverableContract,
) -> dict[str, Any]:
    """收集建模手换模所需的真实、有限执行证据。

    Args:
        work_dir: 当前任务工作目录。
        contract: 当前问题的质量契约。

    Returns:
        质量报告和独立验证指标的紧凑字典；缺失或损坏的文件会被明确标记。
    """
    root = Path(work_dir)
    evidence: dict[str, Any] = {"problem_type": contract.problem_type}
    quality_payload: dict[str, Any] | None = None
    targets = {
        "quality_report": root / contract.quality_filename,
        "prediction_metrics": root / contract.metrics_filename,
    }
    for label, path in targets.items():
        if not path.is_file():
            evidence[label] = {"missing": True, "path": path.name}
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            evidence[label] = {"invalid": True, "path": path.name, "error": str(exc)}
            continue
        if not isinstance(payload, dict):
            evidence[label] = {"invalid": True, "path": path.name}
            continue
        if label == "quality_report":
            allowed = {
                "status",
                "problem_type",
                "selected_model",
                "candidate_models",
                "independent_unit",
                "data_leakage_checks",
                "robustness_checks",
                "limitations",
                "type_specific",
                "key_inference",
                "validation_summary",
                "artifacts",
                "paper_ready_images",
            }
            evidence[label] = {
                key: value for key, value in payload.items() if key in allowed
            }
            quality_payload = payload
        else:
            evidence[label] = payload

    if quality_payload is not None:
        artifact_names = quality_payload.get("artifacts", [])
        if isinstance(artifact_names, list):
            previews: dict[str, Any] = {}
            root_resolved = root.resolve()
            preview_hints = (
                "coefficient",
                "fixed_effect",
                "bootstrap",
                "metric",
                "robustness",
                "sensitivity",
                "audit",
            )
            for raw_name in artifact_names:
                if len(previews) >= 8 or not isinstance(raw_name, str):
                    break
                name = raw_name.strip()
                if not name or not name.casefold().endswith(".csv"):
                    continue
                if not any(hint in name.casefold() for hint in preview_hints):
                    continue
                path = (root / name).resolve()
                try:
                    path.relative_to(root_resolved)
                except ValueError:
                    continue
                if (
                    not path.is_file()
                    or path.stat().st_size <= 0
                    or path.stat().st_size > 256_000
                ):
                    continue
                try:
                    with path.open(
                        "r",
                        encoding="utf-8-sig",
                        newline="",
                    ) as handle:
                        reader = csv.DictReader(handle)
                        columns = list(reader.fieldnames or [])[:20]
                        rows = [
                            {column: row.get(column) for column in columns}
                            for _, row in zip(range(20), reader, strict=False)
                        ]
                except (OSError, csv.Error, UnicodeError):
                    continue
                previews[name] = {
                    "columns": columns,
                    "rows": rows,
                    "preview_limited_to_rows": 20,
                }
            if previews:
                evidence["supporting_artifact_previews"] = previews
    return evidence


_METRIC_KEYWORD_PATTERN = re.compile(
    r"(?:RMSE|MAE|MAPE|R2|R²|F1|AUC|准确率|正确率|精度|误差|偏差|改善|提升|"
    r"降低|得分|稳定性|一致性|可决系数|拟合优度)",
    re.IGNORECASE,
)
# 负号仅在非数字前生效：避免把区间 "0.75-0.85" 的右端点解析成负数
_NUMBER_PATTERN = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?")
# 显著性水平、常见比例等惯用常数不参与溯源，避免误杀
_GROUNDING_COMMON_CONSTANTS = {
    0.0, 0.01, 0.05, 0.1, 0.5, 0.9, 0.95, 0.99, 1.0, 5.0, 10.0, 95.0, 100.0,
}


def collect_grounding_values(evidence: dict[str, Any]) -> set[float]:
    """递归收集证据里的全部有限数值，作为正文指标数字的溯源集合。"""
    values: set[float] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            number = float(node)
            if math.isfinite(number):
                values.add(number)
            return
        if isinstance(node, str):
            for match in _NUMBER_PATTERN.findall(node[:2000]):
                try:
                    number = float(match)
                except ValueError:
                    continue
                if math.isfinite(number):
                    values.add(number)
            return
        if isinstance(node, dict):
            for item in node.values():
                _walk(item)
            return
        if isinstance(node, list):
            for item in node[:200]:
                _walk(item)

    _walk(evidence)
    return values


def _is_grounded_number(
    text_number: float, decimals: int, grounding: set[float]
) -> bool:
    # 正文数字可能是真实值的原样或百分数写法；允许 1% 相对差或同精度舍入。
    # 容差必须随 /100 候选同尺度缩放，否则整数百分数编造会全部漏杀。
    tolerance_floor = 0.5 * 10 ** (-decimals)
    for candidate, floor in (
        (text_number, tolerance_floor),
        (text_number / 100.0, tolerance_floor / 100.0),
    ):
        for value in grounding:
            if abs(candidate - value) <= max(abs(value) * 0.01, floor):
                return True
    return False


def validate_writer_section(
    section_key: str,
    content: Any,
    required_images: list[str] | tuple[str, ...] | None = None,
    quality_report: dict[str, Any] | None = None,
    question_text: str = "",
    grounding_values: set[float] | None = None,
    expected_question_count: int = 0,
) -> None:
    """Reject empty, failed or evidence-free writer sections."""
    if not isinstance(content, str):
        raise DeliverableValidationError(f"{section_key} 写作结果必须是文本")

    def check_length() -> None:
        compact = re.sub(r"\s+", "", content)
        minimum = 600 if section_key.startswith("ques") else 250
        if len(compact) < minimum:
            raise DeliverableValidationError(
                f"{section_key} 正文过短: {len(compact)} < {minimum}"
            )

    def check_markers() -> None:
        found = [
            marker for marker in _FAILURE_MARKERS if marker.lower() in content.lower()
        ]
        if found:
            raise DeliverableValidationError(
                f"{section_key} 正文包含失败/占位标记: {', '.join(found)}"
            )

    def check_numbers() -> None:
        if section_key.startswith("ques") and not re.search(r"\d", content):
            raise DeliverableValidationError(f"{section_key} 没有任何可核对数值")

    def check_model_mention() -> None:
        if section_key.startswith("ques") and not any(
            token in content for token in ("模型", "算法", "回归", "函数", "优化")
        ):
            raise DeliverableValidationError(f"{section_key} 缺少模型或算法说明")

    def check_formula() -> None:
        if section_key.startswith("ques") and not any(
            token in content for token in ("$", "\\(", "式（", "公式")
        ):
            raise DeliverableValidationError(f"{section_key} 缺少核心公式")

    compact_question = re.sub(r"\s+", "", question_text)

    def check_sensitivity_coverage() -> None:
        if "敏感性" in compact_question and "敏感性" not in content:
            raise DeliverableValidationError(
                f"{section_key} 未覆盖题目要求的敏感性分析"
            )

    def check_horizon() -> None:
        horizon_match = re.search(
            r"(\d+)\s*[~～\-—–至到]\s*(\d+)\s*小时", compact_question
        )
        if not horizon_match:
            return
        lower, upper = horizon_match.groups()
        horizon_pattern = re.compile(
            rf"{re.escape(lower)}\s*[~～\-—–至到]\s*"
            rf"{re.escape(upper)}\s*小时"
        )
        if not horizon_pattern.search(content):
            raise DeliverableValidationError(
                f"{section_key} 未覆盖题目要求的 {lower}~{upper}小时预测范围"
            )

    def check_selected_models() -> None:
        selected_value = (quality_report or {}).get("selected_model", "")
        if isinstance(selected_value, dict):
            selected_values = list(selected_value.values())
        elif isinstance(selected_value, list):
            selected_values = selected_value
        else:
            selected_values = [selected_value]

        def normalize_model_name(value: str) -> str:
            return re.sub(r"[\W_]+", "", value.casefold())

        normalized_content = normalize_model_name(content)
        missing_models: list[str] = []
        for value in selected_values:
            selected_model = str(value).strip()
            if not selected_model or selected_model.casefold() in {
                "none",
                "none_manual_review_required",
                "not_applicable",
            }:
                continue
            concise_name = selected_model.split("(", maxsplit=1)[0].strip()
            accepted_names = {selected_model, concise_name}
            if not any(
                normalize_model_name(name) in normalized_content
                for name in accepted_names
                if name
            ):
                missing_models.append(concise_name or selected_model)
        if missing_models:
            raise DeliverableValidationError(
                f"{section_key} 未说明质量报告中的入选模型: "
                + ", ".join(missing_models)
            )

    def check_images() -> None:
        missing = [
            str(image)
            for image in required_images or ()
            if Path(image).name not in content and str(image) not in content
        ]
        if missing:
            raise DeliverableValidationError(
                f"{section_key} 未引用门禁选定的论文图片: {', '.join(missing)}"
            )

    def check_metric_grounding() -> None:
        # 指标关键词附近的数字必须能在真实产物中溯源，防止编造/错误舍入
        if not grounding_values or not section_key.startswith("ques"):
            return
        keyword_spans = [
            (match.end(), match.group())
            for match in _METRIC_KEYWORD_PATTERN.finditer(content)
        ]
        if not keyword_spans:
            return
        ungrounded: list[str] = []
        # 在全文上匹配数字再按距离筛选，避免固定窗口把数字拦腰截断
        for number_match in _NUMBER_PATTERN.finditer(content):
            start = number_match.start()
            keyword = next(
                (
                    text
                    for end, text in keyword_spans
                    if 0 <= start - end <= 40
                ),
                None,
            )
            if keyword is None:
                continue
            raw = number_match.group()
            try:
                number = float(raw)
            except ValueError:
                continue
            if number in _GROUNDING_COMMON_CONSTANTS:
                continue
            if "." not in raw and abs(number) <= 12:
                # 问题编号、折数等小整数不参与溯源
                continue
            decimals = len(raw.split(".")[1]) if "." in raw else 0
            if not _is_grounded_number(number, decimals, grounding_values):
                snippet = f"{keyword}…{raw}"
                if snippet not in ungrounded:
                    ungrounded.append(snippet)
        if ungrounded:
            raise DeliverableValidationError(
                f"{section_key} 中以下指标数值在真实产物中找不到来源，"
                "疑似编造或错误舍入，必须改用质量报告/指标文件中的真实数值: "
                + "；".join(ungrounded[:8])
            )

    def check_abstract_structure() -> None:
        if section_key != "firstPage" or expected_question_count <= 0:
            return
        problems: list[str] = []
        if not any(
            token in content for token in ("关键词", "Keywords", "keywords")
        ):
            problems.append("缺少关键词部分")
        cn_numbers = ["一", "二", "三", "四", "五", "六", "七", "八"]
        for index in range(1, expected_question_count + 1):
            tokens = [f"问题{index}", f"Problem {index}", f"Question {index}"]
            if index <= len(cn_numbers):
                tokens.append(f"问题{cn_numbers[index - 1]}")
            if not any(token in content for token in tokens):
                problems.append(f"摘要必须逐问覆盖：缺少针对问题{index}的内容")
        # 剔除"问题N/Problem N"标签本身的数字，否则计数被标签自我满足
        stripped = re.sub(
            r"(?:问题|Problem\s*|Question\s*)\d+", "", content
        )
        digits = _NUMBER_PATTERN.findall(stripped)
        if len(digits) < expected_question_count:
            problems.append("摘要必须给出各问的核心求解数值")
        if problems:
            raise DeliverableValidationError(f"{section_key} " + "；".join(problems))

    _raise_collected(
        _run_checks_collecting(
            [
                check_length,
                check_markers,
                check_numbers,
                check_model_mention,
                check_formula,
                check_sensitivity_coverage,
                check_horizon,
                check_selected_models,
                check_images,
                check_metric_grounding,
                check_abstract_structure,
            ]
        )
    )


def validate_final_paper(
    work_dir: str | Path,
    sections: dict[str, dict],
    expected_sections: list[str],
) -> Path:
    """Validate the assembled Markdown paper and persist a gate report."""
    root = Path(work_dir)
    paper = root / "res.md"
    if not paper.is_file() or paper.stat().st_size == 0:
        raise DeliverableValidationError("缺少非空最终论文 res.md")
    missing = [key for key in expected_sections if key not in sections]
    if missing:
        raise DeliverableValidationError(f"最终论文缺少章节: {', '.join(missing)}")
    text = paper.read_text(encoding="utf-8")
    for marker in _FAILURE_MARKERS:
        if marker.lower() in text.lower():
            raise DeliverableValidationError(f"最终论文包含失败/占位标记: {marker}")

    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    if chinese_chars > 0:
        if chinese_chars < 9000:
            raise DeliverableValidationError(
                f"中文论文实质内容不足9000字: {chinese_chars}"
            )
        length_mode = "chinese_chars"
        length_value = chinese_chars
    else:
        words = len(re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", text))
        if words < 3500:
            raise DeliverableValidationError(f"英文论文实质内容不足3500词: {words}")
        length_mode = "english_words"
        length_value = words

    if "\ufffd" in text:
        raise DeliverableValidationError("最终论文包含 Unicode 替代字符，存在编码损坏")

    for section_key in expected_sections:
        question_number = _question_number(section_key)
        if question_number is None:
            continue
        heading_pattern = re.compile(
            rf"(?m)^##\s+5\.{question_number}(?:\s|$)"
        )
        if not heading_pattern.search(text):
            raise DeliverableValidationError(
                f"最终论文缺少问题{question_number}的 5.{question_number} 主标题"
            )

    process_markers = (
        "先核对质量报告中的",
        "随后直接给出可替换正文",
        "本次写作任务未提供核心实现",
        "未提供通过门禁的核心实现",
        "本阶段未提供可直接引用并经门禁确认的核心实现",
    )
    leaked_marker = next((marker for marker in process_markers if marker in text), None)
    if leaked_marker:
        raise DeliverableValidationError(
            f"最终论文包含 Writer 写作过程话术: {leaked_marker}"
        )

    image_paths = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    for image_path in image_paths:
        cleaned = image_path.strip().strip("<>")
        image = _resolve_artifact(root, cleaned)
        if not image.is_file() or image.stat().st_size == 0:
            raise DeliverableValidationError(f"论文引用图片不存在: {cleaned}")

    report_path = root / "workflow_quality_gate.json"
    report_path.write_text(
        json.dumps(
            {
                "status": "pass",
                "length_mode": length_mode,
                "length_value": length_value,
                "sections": expected_sections,
                "referenced_images": image_paths,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path


def _required_contract_files(contract: QuestionDeliverableContract) -> list[str]:
    required = [contract.quality_filename]
    if contract.requires_prediction_values:
        required.extend([contract.prediction_filename, contract.metrics_filename])
    return required


def find_reusable_stage_artifacts(
    work_dir: str | Path,
    contract: QuestionDeliverableContract,
) -> list[str]:
    """Return real non-contract files from an interrupted stage for reuse."""
    root = Path(work_dir)
    required = set(_required_contract_files(contract))
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_file()
        and path.stat().st_size > 0
        and path.name.startswith(f"{contract.question_key}_")
        and path.name not in required
    )[:30]


def build_repair_prompt(
    contract: QuestionDeliverableContract,
    error: DeliverableValidationError,
    work_dir: str | Path | None = None,
) -> str:
    """Build a focused prompt for one automatic correction attempt."""
    disk_status = ""
    if work_dir is not None:
        root = Path(work_dir)
        required = _required_contract_files(contract)
        existing = [name for name in required if (root / name).is_file()]
        missing = [name for name in required if name not in existing]
        reusable = find_reusable_stage_artifacts(root, contract)
        disk_status = f"""
【当前磁盘交付状态（以后端实际文件为准）】
- 已存在的必需文件：{", ".join(existing) if existing else "无"}
- 本次必须一次性补齐的文件：{", ".join(missing) if missing else "无"}
- 可复用的真实中间产物：{", ".join(reusable) if reusable else "无"}
先读取并核对已有 CSV/MAT/JSON 中的真实结果。已有证据足够时禁止无意义地重跑耗时模型；
但不得臆造、手填或篡改指标。最后一次 execute_code 必须生成全部缺失文件，逐个读取验证并打印检查结果。
""".strip()
    return f"""
上一次输出未通过强制质量门禁：{error}
请继续执行代码修正产物，不要只用文字解释。仅当已有产物不足或不一致时，才重新运行必要的候选模型、基线、独立验证和稳健性检查；禁止直接篡改 JSON 数值绕过门禁。
{disk_status}
{contract.prompt_block()}
""".strip()
