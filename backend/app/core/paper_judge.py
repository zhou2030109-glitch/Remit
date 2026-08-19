"""终稿评委评审：按国赛评分维度给整篇论文打分并产出定向修改指令。

评审失败一律降级跳过，绝不阻塞交付。
"""

import json
from typing import Any

from app.core.agents.modeler_agent import repair_json
from app.core.llm.llm import LLM
from app.utils.log_util import logger

RUBRIC_KEYS = (
    "abstract",
    "modeling",
    "solution_validation",
    "writing",
    "innovation",
)
RUBRIC_LABELS = {
    "abstract": "摘要质量",
    "modeling": "建模合理性",
    "solution_validation": "求解与验证",
    "writing": "写作规范",
    "innovation": "创新性",
}
# 低于该分的章节才触发定向重写
REWRITE_SCORE_THRESHOLD = 7
MAX_REWRITE_SECTIONS = 2

_JUDGE_SYSTEM = """你是全国大学生数学建模竞赛（CUMCM）的资深评委，正在评审一篇参赛论文能否达到国家一等奖水准。
评分维度（各 1-10 分，国一水准约 8 分以上）：
- abstract 摘要质量：是否"针对问题N"逐问给出方法与核心数字、能否 3 分钟看懂全文贡献
- modeling 建模合理性：方法选择依据、假设合理性、公式规范
- solution_validation 求解与验证：结果可信度、基线对比、敏感性/稳健性分析
- writing 写作规范：结构完整、图表引用、表述严谨、无过程话术
- innovation 创新性：是否在经典方法上有可信的改进点
硬性要求：
- weakest_sections 最多列 3 个真正拖分的章节，revision_directive 必须具体可执行
- 修改指令绝不允许改动任何数值结论（数值来自真实计算，只能改表达与结构）
- section_key 只能取：firstPage/RepeatQues/analysisQues/modelAssumption/symbol/judge/eda/ques1..N/sensitivity_analysis
只输出 JSON：
{"scores": {"abstract": 8, "modeling": 8, "solution_validation": 8, "writing": 8, "innovation": 7},
 "overall": 8,
 "weakest_sections": [{"section_key": "firstPage", "problems": "问题", "revision_directive": "怎么改"}],
 "summary": "一段总评"}"""


async def judge_paper(
    llm: LLM, paper_text: str, ques_count: int
) -> dict[str, Any] | None:
    """让评委模型给整篇论文打分；解析失败返回 None（降级跳过）。"""
    text = paper_text
    if len(text) > 60000:
        # 掐中间保两头：摘要与结尾章节（敏感性/评价/参考文献）都必须被评到
        text = (
            text[:40000]
            + "\n\n[中间部分因过长省略，不得对省略内容下重写指令]\n\n"
            + text[-20000:]
        )
    user_content = f"这是一篇 {ques_count} 问的参赛论文全文，请评审：\n\n{text}"
    for attempt in range(1, 3):
        try:
            response = await llm.chat(
                history=[
                    {"role": "system", "content": _JUDGE_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                agent_name="ModelerAgent",
                publish=False,
            )
        except Exception as exc:
            logger.warning(f"终稿评审调用失败 (第{attempt}/2次): {exc}")
            continue
        parsed = repair_json(response.content or "")
        if not isinstance(parsed, dict):
            continue
        scores = parsed.get("scores")
        if not isinstance(scores, dict):
            continue
        normalized_scores: dict[str, float] = {}
        for key in RUBRIC_KEYS:
            raw_score = scores.get(key)
            if isinstance(raw_score, (int, float, str)):
                try:
                    normalized_scores[key] = max(
                        1.0, min(10.0, float(raw_score))
                    )
                except (TypeError, ValueError):
                    normalized_scores[key] = 0.0
            else:
                normalized_scores[key] = 0.0
        weakest = [
            {
                "section_key": str(item.get("section_key", "")).strip(),
                "problems": str(item.get("problems", ""))[:500],
                "revision_directive": str(item.get("revision_directive", ""))[:800],
            }
            for item in parsed.get("weakest_sections", [])
            if isinstance(item, dict) and str(item.get("section_key", "")).strip()
        ][:3]
        raw_overall = parsed.get("overall")
        if isinstance(raw_overall, (int, float, str)):
            try:
                overall = max(1.0, min(10.0, float(raw_overall)))
            except (TypeError, ValueError):
                overall = sum(normalized_scores.values()) / len(RUBRIC_KEYS)
        else:
            overall = sum(normalized_scores.values()) / len(RUBRIC_KEYS)
        return {
            "scores": normalized_scores,
            "overall": overall,
            "weakest_sections": weakest,
            "summary": str(parsed.get("summary", ""))[:1000],
        }
    return None


def build_review_explain_numbers(review: dict[str, Any]) -> list[dict[str, Any]]:
    """把评审分数转成审批卡的 key_numbers 结构。"""
    numbers: list[dict[str, Any]] = []
    scores = review.get("scores") or {}
    for key in RUBRIC_KEYS:
        score = float(scores.get(key, 0) or 0)
        verdict = "good" if score >= 8 else ("ok" if score >= 6 else "poor")
        numbers.append(
            {
                "name": key,
                "friendly_name": RUBRIC_LABELS[key],
                "value_text": f"{score:.0f}/10",
                "meaning": "评委视角评分，国一水准约 8 分以上。",
                "verdict": verdict,
            }
        )
    return numbers


def save_review(work_dir: str, review: dict[str, Any]) -> None:
    from pathlib import Path

    path = Path(work_dir) / "paper_review.json"
    path.write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
    )
