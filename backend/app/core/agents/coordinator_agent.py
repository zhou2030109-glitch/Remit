"""协调者 Agent 模块，负责识别用户意图并拆解数学建模问题。"""

from app.core.agents.agent import Agent
from app.core.llm.llm import LLM
from app.core.llm.types import StandardResponse
from app.core.json_recovery import decode_json_object
from app.core.prompts.coordinator import COORDINATOR_PROMPT, REFINE_ANALYSIS_PROMPT
import json
import re
from app.utils.log_util import logger
from app.schemas.A2A import CoordinatorToModeler, QuestionAnalysis


_DEFAULT_STRUCTURED_OUTPUT_TOKENS = 8192
_MAX_STRUCTURED_OUTPUT_TOKENS = 65536
_TRUNCATION_REASONS = {
    "length",
    "max_tokens",
    "max_output_tokens",
    "incomplete",
}


class _StructuredOutputTruncated(ValueError):
    """供应商因输出预算耗尽而返回了不完整的结构化内容。"""


def _configured_output_budget(model: LLM) -> int:
    configured = getattr(model, "max_tokens", None)
    if isinstance(configured, int) and configured > 0:
        return configured
    return _DEFAULT_STRUCTURED_OUTPUT_TOKENS


def _response_was_truncated(
    response: StandardResponse,
    requested_tokens: int,
) -> bool:
    reason = str(response.finish_reason or "").strip().lower()
    if any(marker in reason for marker in _TRUNCATION_REASONS):
        return True
    return requested_tokens > 0 and response.usage.completion_tokens >= requested_tokens


def _expanded_output_budget(current: int) -> int:
    return min(max(current * 2, 16384), _MAX_STRUCTURED_OUTPUT_TOKENS)


def _question_keys(questions: dict) -> set[str]:
    return {str(key) for key in questions if re.fullmatch(r"ques\d+", str(key))}


def _validate_analysis_coverage(
    questions: dict,
    analyses: dict[str, QuestionAnalysis],
) -> None:
    expected = _question_keys(questions)
    actual = set(analyses)
    if expected != actual:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("缺少 " + "、".join(missing))
        if unexpected:
            details.append("多出 " + "、".join(unexpected))
        raise ValueError("逐题结构化分析未完整覆盖正式小问：" + "；".join(details))


class CoordinatorAgent(Agent):
    """协调者 Agent，判断用户输入是否为数学建模问题并拆解为结构化问题列表。"""

    default_system_prompt = COORDINATOR_PROMPT

    async def run(
        self,
        ques_all: str,
        user_requirements: str = "",
        *,
        previous_analysis: CoordinatorToModeler | None = None,
        cumulative_feedback: list[str] | None = None,
    ) -> CoordinatorToModeler:  # type: ignore[reportIncompatibleMethodOverride]
        """Turn the problem statement into validated questions and evidence needs."""
        await self._ensure_system_prompt()
        previous_payload = (
            {
                "analysis_summary": previous_analysis.analysis_summary,
                "question_analyses": {
                    key: value.model_dump(mode="json")
                    for key, value in previous_analysis.question_analyses.items()
                },
            }
            if previous_analysis is not None
            else {}
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    f"【完整题目】\n{ques_all}\n\n"
                    f"【额外交付要求（程序将独立保存，不得忽略）】\n"
                    f"{user_requirements or '无'}\n\n"
                    "【上一版结构化分析（返修时必须在此基础上改进）】\n"
                    f"{json.dumps(previous_payload, ensure_ascii=False)}\n\n"
                    "【累计人工返修意见（必须逐条落实，不得只看最后一条）】\n"
                    f"{json.dumps(cumulative_feedback or [], ensure_ascii=False)}"
                ),
            }
        )
        attempt = 0
        output_budget = _configured_output_budget(self.model)
        while True:
            try:
                response = await self._chat(
                    history=self.chat_history,
                    agent_name=self.__class__.__name__,
                    max_tokens=output_budget,
                )
                if _response_was_truncated(response, output_budget):
                    raise _StructuredOutputTruncated(
                        "供应商在输出上限处截断响应："
                        f"finish_reason={response.finish_reason or 'unknown'}, "
                        f"completion_tokens={response.usage.completion_tokens}, "
                        f"budget={output_budget}, "
                        f"content_chars={len(response.content or '')}"
                    )
                payload = decode_json_object(response.content or "")
                if payload is None:
                    raise ValueError("协调者没有返回可解析的 JSON 对象")
                parsed_questions = {
                    key: value
                    for key, value in payload.items()
                    if key in {"title", "background", "ques_count"}
                    or re.fullmatch(r"ques\d+", str(key))
                }
                questions = (
                    previous_analysis.questions
                    if previous_analysis is not None
                    else parsed_questions
                )
                ques_count = (
                    previous_analysis.ques_count
                    if previous_analysis is not None
                    else payload["ques_count"]
                )
                analyses = {
                    str(key): QuestionAnalysis.model_validate(value)
                    for key, value in dict(payload.get("question_analyses", {})).items()
                }
                _validate_analysis_coverage(questions, analyses)
                logger.info(f"questions:{questions}")
                return CoordinatorToModeler(
                    questions=questions,
                    ques_count=ques_count,
                    original_problem=(
                        previous_analysis.original_problem
                        if previous_analysis is not None
                        else ques_all
                    ),
                    analysis_summary=str(payload.get("analysis_summary", "")),
                    question_analyses=analyses,
                    user_requirements=user_requirements.strip(),
                )

            except _StructuredOutputTruncated as e:
                attempt += 1
                next_budget = _expanded_output_budget(output_budget)
                logger.warning(
                    f"协调器结构化输出被截断 (第{attempt}/3次): {e}；"
                    f"下次预算={next_budget}"
                )
                if attempt >= 3:
                    raise ValueError(
                        "题目提取与结构化分析连续 3 次达到输出上限；"
                        "请缩短额外交付要求或改用支持更大输出的模型"
                    ) from e
                output_budget = next_budget
                await self.append_chat_history(
                    {
                        "role": "system",
                        "content": (
                            "上次响应因输出上限被截断。请减少解释性文字，"
                            "但必须保留完整原题字段和每个小问的全部结构化字段，"
                            "一次性重发闭合的完整 JSON。"
                        ),
                    }
                )
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                attempt += 1
                logger.warning(f"解析失败 (尝试 {attempt}): {str(e)}")

                # 添加错误反馈提示
                error_prompt = f"⚠️ 上次响应格式错误: {str(e)}。请严格输出JSON格式"
                await self.append_chat_history(
                    {
                        "role": "system",
                        "content": self.system_prompt + "\n" + error_prompt,
                    }
                )
                if attempt >= 3:
                    raise ValueError(
                        f"题目提取与结构化分析连续 3 次校验失败: {e}"
                    ) from e

    async def refine_analysis(
        self,
        previous: CoordinatorToModeler,
        *,
        data_profile: dict,
        literature_brief: str,
        cumulative_feedback: list[str] | None = None,
    ) -> CoordinatorToModeler:
        """根据真实附件画像和文献证据校正上一版题目理解。"""
        await self.append_chat_history(
            {"role": "system", "content": REFINE_ANALYSIS_PROMPT}
        )
        await self.append_chat_history(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "fixed_questions": previous.questions,
                        "previous_analysis": {
                            "analysis_summary": previous.analysis_summary,
                            "question_analyses": {
                                key: value.model_dump(mode="json")
                                for key, value in previous.question_analyses.items()
                            },
                        },
                        "data_profile": data_profile,
                        "literature_brief": literature_brief,
                        "cumulative_revision_feedback": cumulative_feedback or [],
                    },
                    ensure_ascii=False,
                ),
            }
        )
        last_error: Exception | None = None
        output_budget = _configured_output_budget(self.model)
        for attempt in range(1, 4):
            try:
                response = await self._chat(
                    history=self.chat_history,
                    agent_name=self.__class__.__name__,
                    max_tokens=output_budget,
                )
                if _response_was_truncated(response, output_budget):
                    raise _StructuredOutputTruncated(
                        "数据校正版题意在输出上限处被截断："
                        f"finish_reason={response.finish_reason or 'unknown'}, "
                        f"completion_tokens={response.usage.completion_tokens}, "
                        f"budget={output_budget}"
                    )
                payload = decode_json_object(response.content or "")
                if payload is None:
                    raise ValueError("数据校正版题意不是有效 JSON 对象")
                analyses = {
                    str(key): QuestionAnalysis.model_validate(value)
                    for key, value in dict(payload["question_analyses"]).items()
                }
                _validate_analysis_coverage(previous.questions, analyses)
                return previous.model_copy(
                    update={
                        "analysis_summary": str(payload["analysis_summary"]),
                        "question_analyses": analyses,
                        "data_profile": data_profile,
                        "literature_brief": literature_brief,
                    }
                )
            except (json.JSONDecodeError, ValueError, KeyError) as exc:
                last_error = exc
                if isinstance(exc, _StructuredOutputTruncated):
                    output_budget = _expanded_output_budget(output_budget)
                logger.warning(
                    f"数据校正版题意结构校验失败 (第{attempt}/3次): {exc}；"
                    f"下次预算={output_budget}"
                )
                if attempt < 3:
                    await self.append_chat_history(
                        {
                            "role": "system",
                            "content": (
                                "上次数据校正版题意格式不完整："
                                f"{exc}。请保留固定题面并严格重发完整 JSON。"
                            ),
                        }
                    )
        raise ValueError(
            f"数据校正版题意连续 3 次校验失败: {last_error}"
        ) from last_error
