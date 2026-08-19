"""把阶段产物转换为可审计、不会把降级冒充成功的状态。"""

from __future__ import annotations

import re
from typing import Any


def _question_keys(questions: object) -> set[str]:
    if not isinstance(questions, dict):
        return set()
    return {str(key) for key in questions if re.fullmatch(r"ques\d+", str(key))}


def evaluate_research(state: dict[str, Any]) -> dict[str, Any]:
    """分别核对附件画像和文献产物，任一缺失都不得显示绿色完成。"""
    profile = state.get("data_profile")
    profile = profile if isinstance(profile, dict) else {}
    profiled_files = profile.get("files")
    profiled_files = profiled_files if isinstance(profiled_files, list) else []
    data_status = str(profile.get("status") or "")
    if not data_status:
        data_status = "completed" if profiled_files else "failed"

    review = state.get("literature_review")
    review = review if isinstance(review, dict) else {}
    paper_count = int(review.get("paper_count", 0) or 0)
    cards = review.get("method_cards")
    cards = cards if isinstance(cards, list) else []
    fulltext_stats = review.get("fulltext_stats")
    fulltext_stats = fulltext_stats if isinstance(fulltext_stats, dict) else {}
    fulltext_read = int(fulltext_stats.get("succeeded", 0) or 0)
    literature_status = str(review.get("status") or "")
    if not literature_status:
        literature_status = "completed" if paper_count > 0 else "failed"

    issues: list[str] = []
    if data_status != "completed":
        notes = [str(item) for item in (profile.get("notes") or []) if str(item)]
        issues.extend(notes[:4] or ["附件数据画像未生成或未识别到数据文件"])
    if literature_status not in {"completed"}:
        errors = [str(item) for item in (review.get("errors") or []) if str(item)]
        issues.extend(errors[:5] or ["文献调研没有取得可用文献和方法卡"])
    if paper_count and not cards:
        issues.append("检索到文献但没有提取出任何方法卡，建模无法引用具体方法")
    elif cards and not fulltext_read:
        # 全部只读到摘要仍可用，但必须让人知道证据强度不足
        issues.append("入选文献均未取到开放获取全文，方法卡只基于摘要")

    status = (
        "completed"
        if data_status == "completed" and literature_status == "completed" and cards
        else "warning"
    )
    return {
        "status": status,
        "data_status": data_status,
        "literature_status": literature_status,
        "profiled_file_count": len(profiled_files),
        "paper_count": paper_count,
        "method_card_count": len(cards),
        "fulltext_read_count": fulltext_read,
        "issues": issues,
        "summary": (
            f"已画像 {len(profiled_files)} 个附件，检索 {paper_count} 篇文献，"
            f"精读 {fulltext_read} 篇全文并产出 {len(cards)} 张方法卡"
            if status == "completed"
            else (
                f"阶段仅部分完成：附件画像 {len(profiled_files)} 个，"
                f"文献 {paper_count} 篇，方法卡 {len(cards)} 张"
            )
        ),
    }


def evaluate_analysis(state: dict[str, Any]) -> dict[str, Any]:
    """核对逐题分析覆盖率及其证据基础。"""
    response = state.get("analysis_response")
    response = response if isinstance(response, dict) else {}
    expected = _question_keys(state.get("questions"))
    analyses = response.get("question_analyses")
    analyses = analyses if isinstance(analyses, dict) else {}
    actual = _question_keys(analyses)
    missing = sorted(expected - actual)
    issues: list[str] = []
    if not expected:
        issues.append("尚未提取正式小问")
    if missing:
        issues.append("缺少逐题分析：" + "、".join(missing))
    if not str(response.get("analysis_summary", "")).strip():
        issues.append("缺少总体题意分析")

    research = evaluate_research(state)
    if research["status"] != "completed":
        issues.extend(str(item) for item in research["issues"])
    if not expected or missing or not analyses:
        status = "failed"
    elif research["status"] != "completed":
        status = "warning"
    else:
        status = "completed"
    return {
        "status": status,
        "question_count": len(actual),
        "expected_question_count": len(expected),
        "issues": list(dict.fromkeys(issues)),
        "summary": (
            f"已生成 {len(actual)} 问结构化分析，且附件与文献证据完整"
            if status == "completed"
            else f"已生成 {len(actual)} 问结构化分析，但证据核验尚未完整"
        ),
    }


def evaluated_node_status(state: dict[str, Any], node_id: str) -> str:
    """返回供进度条使用的真实状态。"""
    if node_id == "research":
        return str(evaluate_research(state)["status"])
    if node_id == "analysis":
        return str(evaluate_analysis(state)["status"])
    return "completed"
