"""探索实验（pilot）：小样本候选 PK 的提示词、结果校验与审批展示。"""

import json
import math
from pathlib import Path
from typing import Any

from app.schemas.A2A import PilotDecision, PilotPlan

PILOT_RESULTS_FILENAME = "pilot_results.json"


class PilotValidationError(ValueError):
    """探索实验产物缺失或不满足最低要求。"""


def build_pilot_coder_prompt(plan: PilotPlan) -> str:
    """把探索实验协议转成代码手可执行的提示词。"""
    question_blocks: list[str] = []
    for key, question_plan in plan.questions.items():
        candidate_lines = "\n".join(
            f"  - {item.name}（{item.role}）：{item.approach}"
            for item in question_plan.candidates
        )
        question_blocks.append(
            f"""【{key}】
抽样规则：{question_plan.sampling_rule}
主指标：{question_plan.primary_metric}（higher_is_better={question_plan.higher_is_better}）
每个候选时间预算：{question_plan.time_budget_minutes} 分钟
候选：
{candidate_lines}"""
        )
    blocks = "\n\n".join(question_blocks)
    return f"""【探索实验：小样本候选方案 PK，不是正式求解】
目的：用小样本快速比较各候选方案的真实表现，为最终选型提供数据。

{blocks}

硬性要求：
1. 严格按抽样规则取小样本；同一小问的所有候选必须用完全相同的数据划分。
2. 每个候选真实训练/求解并计算主指标；超出时间预算的候选记为失败，不要死磕。
3. 结果写入 {PILOT_RESULTS_FILENAME}（UTF-8 JSON），结构：
{{"questions": {{"ques1": {{"sample_description": "实际用了多少数据",
  "candidates": [{{"name": "候选名", "metric_name": "rmse",
    "metric_value": 3.21, "runtime_seconds": 12.5,
    "ran_ok": true, "notes": "一句话备注"}}]}}}}}}
4. 跑失败的候选也要如实记录 ran_ok=false 和失败原因，禁止编造数字。
5. 不要生成正式交付产物（质量报告/预测 CSV/论文图），只写 {PILOT_RESULTS_FILENAME}。
完成后读取该文件并打印内容自查。"""


def validate_pilot_results(
    work_dir: str | Path, plan: PilotPlan
) -> dict[str, Any]:
    """校验探索实验产物；每问至少一个真实跑通且指标有限的候选。

    Returns:
        规范化后的结果字典。

    Raises:
        PilotValidationError: 文件缺失、结构错误或结果不满足最低要求。
    """
    path = Path(work_dir) / PILOT_RESULTS_FILENAME
    if not path.is_file():
        raise PilotValidationError(f"缺少 {PILOT_RESULTS_FILENAME}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotValidationError(
            f"{PILOT_RESULTS_FILENAME} 不是有效 JSON: {exc}"
        ) from exc
    questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(questions, dict):
        raise PilotValidationError("questions 必须是 JSON 对象")

    errors: list[str] = []
    normalized: dict[str, Any] = {}
    for key in plan.questions:
        entry = questions.get(key)
        if not isinstance(entry, dict):
            errors.append(f"{key} 缺少结果")
            continue
        raw_candidates = entry.get("candidates")
        if not isinstance(raw_candidates, list) or not raw_candidates:
            errors.append(f"{key}.candidates 必须是非空数组")
            continue
        # 候选名必须属于本轮协议：防止旧一轮的 pilot_results 冒充新实验
        allowed_names = {
            candidate.name.strip().casefold()
            for candidate in plan.questions[key].candidates
        }
        candidates: list[dict[str, Any]] = []
        ok_count = 0
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            item_name = str(item.get("name", "")).strip().casefold()
            if item_name and item_name not in allowed_names:
                errors.append(
                    f"{key} 出现不属于本轮协议的候选 "
                    f"{item.get('name')}，疑似旧结果或未按协议执行"
                )
                continue
            ran_ok = item.get("ran_ok") is True
            metric_value = item.get("metric_value")
            if isinstance(metric_value, (int, float, str)):
                try:
                    metric_number = float(metric_value)
                except (TypeError, ValueError):
                    metric_number = math.nan
            else:
                metric_number = math.nan
            if ran_ok and not math.isfinite(metric_number):
                errors.append(
                    f"{key} 候选 {item.get('name', '?')} ran_ok=true "
                    "但 metric_value 不是有限数值"
                )
                continue
            if ran_ok:
                ok_count += 1
            candidates.append(
                {
                    "name": str(item.get("name", "")).strip() or "unnamed",
                    "metric_name": str(item.get("metric_name", "")),
                    "metric_value": metric_number
                    if math.isfinite(metric_number)
                    else None,
                    "runtime_seconds": item.get("runtime_seconds"),
                    "ran_ok": ran_ok,
                    "notes": str(item.get("notes", ""))[:160],
                }
            )
        if ok_count < 1:
            errors.append(f"{key} 没有任何真实跑通的候选")
            continue
        normalized[key] = {
            "sample_description": str(entry.get("sample_description", "")),
            "candidates": candidates,
        }
    if errors:
        raise PilotValidationError(
            "；".join(f"[{index}] {item}" for index, item in enumerate(errors, 1))
        )
    return {"questions": normalized}


def build_pilot_table(
    results: dict[str, Any], decision: PilotDecision | None
) -> dict[str, Any]:
    """把 pilot 结果转成审批卡可直接渲染的对比表。"""
    selected_by_question = (
        {key: item.selected_model for key, item in decision.questions.items()}
        if decision
        else {}
    )
    columns = ["小问", "候选方案", "指标", "数值", "耗时(秒)", "状态", "入选"]
    rows: list[dict[str, str]] = []
    for key, entry in (results.get("questions") or {}).items():
        for candidate in entry.get("candidates", []):
            metric_value = candidate.get("metric_value")
            selected = (
                str(selected_by_question.get(key, "")).strip().casefold()
                == str(candidate.get("name", "")).strip().casefold()
            )
            rows.append(
                {
                    "小问": key,
                    "候选方案": str(candidate.get("name", "")),
                    "指标": str(candidate.get("metric_name", "")),
                    "数值": f"{metric_value:.4g}"
                    if isinstance(metric_value, (int, float))
                    else "—",
                    "耗时(秒)": str(candidate.get("runtime_seconds") or "—"),
                    "状态": "跑通" if candidate.get("ran_ok") else "失败",
                    "入选": "✅" if selected else "",
                }
            )
    return {
        "filename": PILOT_RESULTS_FILENAME,
        "columns": columns,
        "rows": rows,
        "preview_limited_to_rows": len(rows),
    }
