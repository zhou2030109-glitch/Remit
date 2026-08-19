import json
import re

import asyncio
from icecream import ic  # type: ignore[import-unresolved]
from pydantic import ValidationError

from app.core.agents.agent import Agent
from app.core.llm.llm import LLM
from app.core.prompts import MODELER_PROMPT
from app.schemas.A2A import (
    CoordinatorToModeler,
    MethodCard,
    ModelCouncilResult,
    ModelExecutionReview,
    ModelerToCoder,
    ModelRevisionPlan,
    PilotDecision,
    PilotPlan,
)
from app.utils.log_util import logger


def repair_json(json_str: str) -> dict | None:
    """尝试修复 LLM 输出的格式错误的 JSON。

    Args:
        json_str: 可能包含格式错误的 JSON 字符串。

    Returns:
        修复后的字典，无法修复时返回 None。
    """
    json_str = json_str.replace("```json", "").replace("```", "").strip()

    # Try direct parse first
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 模型偶尔会在 JSON 前补一句解释；从每个左花括号尝试解码完整对象，
    # 避免最后的正则兜底只捞到第一个简单字段而静默丢失其余小问。
    decoder = json.JSONDecoder()
    for index, character in enumerate(json_str):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(json_str[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    # Fix unescaped newlines and quotes inside string values
    try:
        fixed = re.sub(
            r'(?<=: ")(.*?)(?=",\s*\n\s*"|"\s*\n\s*})',
            lambda m: m.group(0).replace('"', '\\"'),
            json_str,
            flags=re.DOTALL,
        )
        return json.loads(fixed)
    except (json.JSONDecodeError, re.error):
        pass

    # Extract key-value pairs with regex as last resort
    try:
        pattern = r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.|"(?!,\s*\n)|"(?!\s*\n\s*}))*)"'
        matches = re.findall(pattern, json_str, re.DOTALL)
        if matches:
            return {k: v.replace('\\"', '"') for k, v in matches}
    except re.error:
        pass

    return None


def _expected_model_plan_keys(questions: dict) -> list[str]:
    question_keys = [
        str(key)
        for key in questions
        if str(key).startswith("ques") and str(key) != "ques_count"
    ]
    return ["eda", *question_keys, "sensitivity_analysis"]


def _validate_candidate_provenance(
    plan: PilotPlan,
    cards_by_question: dict[str, list[dict]],
    known_card_ids: set[str],
) -> None:
    """确保候选方案的文献溯源真实可查。

    没有溯源就无法在代码验证后判断"哪篇文献真的影响了建模"，最终引用也就退化成
    凭印象堆参考文献。因此有方法卡的小问必须至少有一个候选引用它。

    Raises:
        ValueError: 引用了不存在的 card_id，或有卡的小问一个都没引用。
    """
    errors: list[str] = []
    for question_key, question_plan in plan.questions.items():
        cited = {
            candidate.source_card_id.strip()
            for candidate in question_plan.candidates
            if candidate.source_card_id.strip()
        }
        unknown = sorted(cited - known_card_ids)
        if unknown:
            errors.append(f"{question_key} 引用了不存在的方法卡：{'、'.join(unknown)}")
        if cards_by_question.get(question_key) and not cited:
            errors.append(
                f"{question_key} 有方法卡却没有任何候选引用，"
                "至少一个非 baseline 候选必须填 source_card_id"
            )
        for candidate in question_plan.candidates:
            if candidate.source_card_id.strip() and not candidate.adaptation.strip():
                errors.append(
                    f"{question_key} 候选 {candidate.name} 引用了方法卡但没写 adaptation"
                )
    if errors:
        raise ValueError("；".join(errors))


def _validate_citation_decisions(
    decision: PilotDecision,
    candidate_sources: dict[str, list[dict]],
) -> None:
    """确保每篇被候选引用过的文献都有明确去留裁决。

    漏判的文献既不能进参考文献（无证据），也不能悄悄消失（无法解释为何读它），
    因此必须逐张裁决。

    Raises:
        ValueError: 有引用未裁决，或裁决了本轮根本没引用过的方法卡。
    """
    errors: list[str] = []
    for question_key, items in candidate_sources.items():
        expected = {
            str(item.get("source_card_id", "")).strip()
            for item in items
            if str(item.get("source_card_id", "")).strip()
        }
        if not expected:
            continue
        item = decision.questions.get(question_key)
        judged = (
            {entry.card_id.strip() for entry in item.citation_decisions}
            if item
            else set()
        )
        missing = sorted(expected - judged)
        if missing:
            errors.append(f"{question_key} 缺少文献裁决：{'、'.join(missing)}")
        unknown = sorted(judged - expected)
        if unknown:
            errors.append(
                f"{question_key} 裁决了本轮未引用的方法卡：{'、'.join(unknown)}"
            )
    if errors:
        raise ValueError("；".join(errors))


def _normalize_model_plan(
    parsed: dict,
    expected_keys: list[str],
) -> dict[str, str]:
    """强制总体方案覆盖 EDA、全部正式小问与敏感性分析。"""
    if isinstance(parsed.get("questions_solution"), dict):
        parsed = parsed["questions_solution"]
    missing = [key for key in expected_keys if key not in parsed]
    if missing:
        raise ValueError("缺少方案键：" + "、".join(missing))
    normalized = {key: str(parsed[key]).strip() for key in expected_keys}
    too_short = [key for key, value in normalized.items() if len(value) < 40]
    if too_short:
        raise ValueError("方案内容过短：" + "、".join(too_short))
    return normalized


class ModelerAgent(Agent):
    """建模手 Agent，分析问题类型并制定建模方案、求解方法和可视化策略。"""

    def __init__(
        self,
        task_id: str,
        model: LLM,
        context_window: int = 128000,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        super().__init__(task_id, model, context_window, cancel_event=cancel_event)
        self.system_prompt = MODELER_PROMPT

    async def run(self, coordinator_to_modeler: CoordinatorToModeler) -> ModelerToCoder:  # type: ignore[reportIncompatibleMethodOverride]
        """根据协调者拆解的问题生成建模方案。

        Args:
            coordinator_to_modeler: 协调者传递的结构化问题信息。

        Returns:
            ModelerToCoder 对象，包含各问题的建模解决方案。
        """
        await self.append_chat_history(
            {"role": "system", "content": self.system_prompt}
        )
        modeler_input: dict = {
            "questions": coordinator_to_modeler.questions,
            "user_requirements": coordinator_to_modeler.user_requirements,
        }
        if coordinator_to_modeler.analysis_summary:
            modeler_input["analysis_summary"] = coordinator_to_modeler.analysis_summary
        if coordinator_to_modeler.question_analyses:
            modeler_input["question_analyses"] = {
                key: value.model_dump(mode="json")
                for key, value in coordinator_to_modeler.question_analyses.items()
            }
            modeler_input["analysis_instruction"] = (
                "以下逐题分析已经过附件画像与文献证据校正，是建模方案的约束性输入。"
                "方案必须逐项落实目标、输入、决策变量、约束、依赖、风险和验证要求；"
                "不得退回仅按原题关键词猜测方法。"
            )
        if coordinator_to_modeler.literature_brief:
            modeler_input["literature_brief"] = (
                coordinator_to_modeler.literature_brief
                + "\n方案必须说明与文献主流方法的关系；创新点可采纳但必须可落地。"
            )
        if coordinator_to_modeler.method_cards:
            modeler_input["method_cards"] = {
                key: [card.model_dump(mode="json") for card in cards]
                for key, cards in coordinator_to_modeler.method_cards.items()
            }
            modeler_input["method_card_instruction"] = (
                "这些方法卡来自真实精读的文献（evidence_level=full_text 的已读全文）。"
                "方案中采纳某张卡的方法时，必须写明引用的 card_id 以及相对原文做了"
                "什么修改；卡里的适用条件不满足就必须拒绝并说明理由，"
                "不得把未读全文的方法写成已验证结论。"
            )
        if coordinator_to_modeler.data_profile:
            modeler_input["data_profile"] = coordinator_to_modeler.data_profile
        if coordinator_to_modeler.method_recommendations:
            modeler_input["method_recommendations"] = (
                coordinator_to_modeler.method_recommendations
            )
            modeler_input["method_retrieval_instruction"] = (
                "这些是本地三级方法库按题意检索的候选证据。逐项检查其假设、"
                "失败模式和验证要求；可以拒绝不适用方法，但必须解释理由，"
                "不得仅按相关性分数机械选型。"
            )
        await self.append_chat_history(
            {
                "role": "user",
                "content": json.dumps(modeler_input, ensure_ascii=False),
            }
        )

        expected_keys = _expected_model_plan_keys(coordinator_to_modeler.questions)
        last_error = "未知格式错误"
        for attempt in range(1, 4):
            response = await self._chat(
                history=self.chat_history,
                agent_name=self.__class__.__name__,
            )

            json_str = response.content
            if not json_str:
                raise ValueError("返回的 JSON 字符串为空，请检查输入内容。")

            questions_solution = repair_json(json_str)
            try:
                if not questions_solution:
                    raise ValueError("返回内容不是有效 JSON 对象")
                normalized = _normalize_model_plan(
                    questions_solution,
                    expected_keys,
                )
                ic(normalized)
                return ModelerToCoder(questions_solution=normalized)
            except ValueError as exc:
                last_error = str(exc)
            logger.warning(f"总体建模方案结构校验失败 (第{attempt}/3次): {last_error}")
            retry_msg: dict = {"role": "assistant", "content": json_str}
            if response.reasoning_content:
                retry_msg["reasoning_content"] = response.reasoning_content
            await self.append_chat_history(retry_msg)
            await self.append_chat_history(
                {
                    "role": "user",
                    "content": (
                        f"总体方案未通过结构校验：{last_error}。"
                        f"必须完整保留这些键：{expected_keys}。"
                        '请严格输出单层JSON，字符串值内的双引号必须转义为\\"。'
                    ),
                }
            )
        raise ValueError(f"总体建模方案连续 3 次结构校验失败: {last_error}")

    async def design_pilot_plan(
        self,
        *,
        questions: dict,
        questions_solution: dict[str, str],
        literature_brief: str,
        data_profile_summary: str,
        backend_language: str,
        method_cards: dict[str, list[MethodCard]] | None = None,
    ) -> PilotPlan:
        """为每个正式小问设计小样本探索实验协议。

        Args:
            questions: 各小问原文。
            questions_solution: 当前总体建模方案。
            literature_brief: 文献调研摘要（可为空）。
            data_profile_summary: 数据画像摘要（可为空）。
            backend_language: 代码执行后端语言（matlab/python）。
            method_cards: 逐题文献方法卡；候选方案必须标明引用了哪一张。

        Returns:
            结构校验通过的探索实验协议。

        Raises:
            ValueError: 连续三次无法生成有效协议。
        """
        if not self.chat_history:
            await self.append_chat_history(
                {"role": "system", "content": self.system_prompt}
            )
        question_keys = [
            str(key)
            for key in questions
            if str(key).startswith("ques") and str(key) != "ques_count"
        ]
        cards_by_question = {
            key: [
                card.model_dump(mode="json")
                for card in (method_cards or {}).get(key, [])
            ]
            for key in question_keys
        }
        available_card_ids = sorted(
            card["card_id"] for cards in cards_by_question.values() for card in cards
        )
        payload = {
            "task": "根据文献方法卡设计候选方案与小样本探索实验协议",
            "questions": {key: questions.get(key, "") for key in question_keys},
            "current_plans": {
                key: questions_solution.get(key, "") for key in question_keys
            },
            "method_cards": cards_by_question,
            "available_card_ids": available_card_ids,
            "literature_brief": literature_brief or "无",
            "data_profile": data_profile_summary or "未知",
            "execution_environment": (
                f"{backend_language} 实现、无 GPU、清洗后数据已在工作目录"
            ),
            "rules": [
                "每问 2-3 个候选，必须含一个简单可解释的 baseline",
                "该问有方法卡时，至少一个非 baseline 候选必须基于方法卡设计",
                "引用方法卡的候选必须填 source_card_id（只能用 available_card_ids 中的 ID）"
                "，并在 adaptation 里写清相对原文做了什么修改"
                "（换数据、简化步骤、改参数、只取其中一环等）",
                "不引用文献的候选把 source_card_id 留空，adaptation 写明方法来源",
                "候选必须能在小样本上快速跑通；方法卡适用条件不满足就不要选它",
                "sampling_rule 写明抽样方式（如按时间取前 20% 或分层抽样 2000 行）",
                "primary_metric 用该题型惯例指标；time_budget_minutes 每候选 1-8 分钟",
                "探索实验只为比较候选优劣，不追求最终精度",
            ],
            "output_schema": {
                "questions": {
                    "ques1": {
                        "candidates": [
                            {
                                "name": "候选名",
                                "role": "baseline | candidate",
                                "approach": "怎么实现（一两句）",
                                "source_card_id": "ques1-C1 或空字符串",
                                "adaptation": "相对原文做了什么修改",
                            }
                        ],
                        "sampling_rule": "抽样规则",
                        "primary_metric": "rmse",
                        "higher_is_better": False,
                        "time_budget_minutes": 5,
                    }
                }
            },
        }
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    "请为每个正式小问生成 2-3 个候选方案并设计小样本探索实验协议，"
                    "只输出 JSON：\n" + json.dumps(payload, ensure_ascii=False)
                ),
            }
        )
        known_card_ids = set(available_card_ids)
        last_error = "未知格式错误"
        for attempt in range(1, 4):
            response = await self._chat(
                history=self.chat_history,
                agent_name=self.__class__.__name__,
            )
            parsed = repair_json(response.content or "")
            try:
                if not parsed:
                    raise ValueError("返回内容不是有效 JSON 对象")
                plan = PilotPlan.model_validate(parsed)
                missing = [key for key in question_keys if key not in plan.questions]
                if missing:
                    raise ValueError("协议缺少小问：" + "、".join(missing))
                _validate_candidate_provenance(plan, cards_by_question, known_card_ids)
                return plan
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)
            logger.warning(f"探索实验协议校验失败 (第{attempt}/3次): {last_error}")
            retry_msg: dict = {"role": "assistant", "content": response.content}
            if response.reasoning_content:
                retry_msg["reasoning_content"] = response.reasoning_content
            await self.append_chat_history(retry_msg)
            await self.append_chat_history(
                {
                    "role": "user",
                    "content": (
                        f"协议未通过结构校验：{last_error}。"
                        f"必须覆盖全部小问：{question_keys}，每问 candidates 含 baseline，"
                        f"source_card_id 只能取自 {sorted(known_card_ids)} 或空字符串。"
                        "只输出 JSON。"
                    ),
                }
            )
        raise ValueError(f"探索实验协议连续 3 次校验失败: {last_error}")

    async def finalize_with_pilot(
        self,
        *,
        questions: dict,
        questions_solution: dict[str, str],
        pilot_results: dict,
        literature_brief: str,
        pilot_plan: PilotPlan | None = None,
    ) -> PilotDecision:
        """基于探索实验的真实小样本数据做最终选型定案与文献去留裁决。

        Args:
            questions: 各小问原文。
            questions_solution: 当前总体建模方案。
            pilot_results: 探索实验结果（真实指标）。
            literature_brief: 文献调研摘要。
            pilot_plan: 本轮候选协议，提供候选与方法卡的对应关系。

        Returns:
            结构校验通过的选型定案。

        Raises:
            ValueError: 连续三次无法生成有效定案。
        """
        if not self.chat_history:
            await self.append_chat_history(
                {"role": "system", "content": self.system_prompt}
            )
        question_keys = [
            str(key)
            for key in questions
            if str(key).startswith("ques") and str(key) != "ques_count"
        ]
        candidate_sources = {
            key: [
                {
                    "name": candidate.name,
                    "source_card_id": candidate.source_card_id,
                    "adaptation": candidate.adaptation,
                }
                for candidate in question_plan.candidates
            ]
            for key, question_plan in (
                pilot_plan.questions if pilot_plan else {}
            ).items()
        }
        cited_card_ids = sorted(
            {
                item["source_card_id"]
                for items in candidate_sources.values()
                for item in items
                if item["source_card_id"]
            }
        )
        payload = {
            "task": "基于探索实验真实数据做最终选型，并裁决每篇被引文献的去留",
            "questions": {key: questions.get(key, "") for key in question_keys},
            "current_plans": {
                key: questions_solution.get(key, "") for key in question_keys
            },
            "pilot_results": pilot_results,
            "candidate_sources": candidate_sources,
            "cited_card_ids": cited_card_ids,
            "literature_brief": literature_brief or "无",
            "rules": [
                "selected_model 必须是该问 pilot 中真实跑通(ran_ok=true)的候选",
                "revised_strategy 是给代码手的完整正式求解方案（含选中方法、"
                "验证口径、与次优候选的对比要求）",
                "小样本表现接近时优先选更简单可解释的方案",
                "禁止选择 pilot 中失败或没跑过的方案",
                "citation_decisions 必须覆盖该问 candidate_sources 里出现过的每个"
                "非空 source_card_id：",
                "  adopted = 该文献方法被直接采用进最终方案",
                "  modified = 采用了它的思路但按 adaptation 做了实质改动",
                "  rejected = 实验表现不佳或不适用，最终没有采用",
                "evidence 必须引用 pilot_results 里的真实指标数字，禁止编造",
                "influence 写明该文献对最终模型的具体影响；rejected 可写放弃原因",
            ],
            "output_schema": {
                "questions": {
                    "ques1": {
                        "selected_model": "入选候选名",
                        "revised_strategy": "正式求解方案（≥40字）",
                        "justification": "基于 pilot 数字的选择理由",
                        "citation_decisions": [
                            {
                                "card_id": "ques1-C1",
                                "decision": "adopted | modified | rejected",
                                "evidence": "引用 pilot 真实指标的依据",
                                "influence": "对最终模型的具体影响",
                            }
                        ],
                    }
                }
            },
        }
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    "请基于探索实验数据完成最终选型与文献去留裁决，只输出 JSON：\n"
                    + json.dumps(payload, ensure_ascii=False)
                ),
            }
        )
        # 每问真实跑通的候选名（casefold 归一），selected_model 必须在其中
        ok_names_by_question: dict[str, set[str]] = {}
        for key, entry in (pilot_results.get("questions") or {}).items():
            ok_names_by_question[str(key)] = {
                str(candidate.get("name", "")).strip().casefold()
                for candidate in entry.get("candidates", [])
                if isinstance(candidate, dict) and candidate.get("ran_ok") is True
            }
        last_error = "未知格式错误"
        for attempt in range(1, 4):
            response = await self._chat(
                history=self.chat_history,
                agent_name=self.__class__.__name__,
            )
            parsed = repair_json(response.content or "")
            try:
                if not parsed:
                    raise ValueError("返回内容不是有效 JSON 对象")
                decision = PilotDecision.model_validate(parsed)
                missing = [
                    key for key in question_keys if key not in decision.questions
                ]
                if missing:
                    raise ValueError("定案缺少小问：" + "、".join(missing))
                invalid_selection = [
                    f"{key}={item.selected_model}"
                    for key, item in decision.questions.items()
                    if key in ok_names_by_question
                    and item.selected_model.strip().casefold()
                    not in ok_names_by_question[key]
                ]
                if invalid_selection:
                    raise ValueError(
                        "selected_model 必须是该问真实跑通(ran_ok=true)的候选："
                        + "、".join(invalid_selection)
                    )
                _validate_citation_decisions(decision, candidate_sources)
                return decision
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)
            logger.warning(f"选型定案校验失败 (第{attempt}/3次): {last_error}")
            retry_msg: dict = {"role": "assistant", "content": response.content}
            if response.reasoning_content:
                retry_msg["reasoning_content"] = response.reasoning_content
            await self.append_chat_history(retry_msg)
            await self.append_chat_history(
                {
                    "role": "user",
                    "content": (
                        f"定案未通过结构校验：{last_error}。"
                        f"必须覆盖全部小问：{question_keys}。只输出 JSON。"
                    ),
                }
            )
        raise ValueError(f"选型定案连续 3 次校验失败: {last_error}")

    async def revise_after_execution(
        self,
        *,
        question_key: str,
        question_text: str,
        original_plan: str,
        gate_error: str,
        evidence: dict,
        rejected_models: list[str],
    ) -> ModelRevisionPlan:
        """根据代码手的真实运行证据更换模型并制定新验证方案。

        Args:
            question_key: 当前问题编号。
            question_text: 当前问题原文。
            original_plan: 上一轮建模方案。
            gate_error: 自动质量门禁失败原因。
            evidence: 质量报告与独立验证指标。
            rejected_models: 本节点已经失败、禁止再次入选的模型。

        Returns:
            经过结构校验、且未重复失败模型的返修方案。

        Raises:
            ValueError: 连续三次无法生成有效换模方案。
        """
        if not self.chat_history:
            await self.append_chat_history(
                {"role": "system", "content": self.system_prompt}
            )
        normalized_rejected = {
            item.strip().casefold() for item in rejected_models if item.strip()
        }
        payload = {
            "task": "根据真实运行结果诊断失败并更换模型",
            "question_key": question_key,
            "question_text": question_text,
            "original_plan": original_plan,
            "gate_error": gate_error,
            "execution_evidence": evidence,
            "rejected_models": rejected_models,
            "requirements": [
                "selected_model 不能是 rejected_models 中已经失败的模型",
                "至少给出一个简单基线和一个新候选，并在相同独立验证折上比较",
                "必须说明失败原因、新模型为何可能改善以及仍失败时的回退路线",
                "不得修改报告数值或降低原质量门槛",
            ],
            "output_schema": {
                "diagnosis": "基于指标的失败诊断",
                "rejected_models": ["已失败模型"],
                "candidate_models": [
                    {"name": "简单基线", "role": "baseline", "reason": "理由"},
                    {"name": "新候选", "role": "candidate", "reason": "理由"},
                ],
                "selected_model": "本轮主模型",
                "revised_strategy": "可直接交给代码手执行的完整新方案",
                "validation_plan": "独立单位、数据划分、指标和稳健性检查",
                "acceptance_criteria": "不得降低的量化验收门槛",
            },
        }
        await self.append_chat_history(
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            }
        )

        last_error = "未知格式错误"
        for attempt in range(1, 4):
            response = await self._chat(
                history=self.chat_history,
                agent_name=self.__class__.__name__,
                sub_title=f"{question_key}-模型返修",
            )
            content = response.content or ""
            parsed = repair_json(content)
            try:
                if not parsed:
                    raise ValueError("返回内容不是有效 JSON 对象")
                plan = ModelRevisionPlan.model_validate(parsed)
                selected = plan.selected_model.strip().casefold()
                if selected in normalized_rejected:
                    raise ValueError(
                        f"selected_model {plan.selected_model} 已失败，必须更换模型"
                    )
                if plan.revised_strategy.strip() == original_plan.strip():
                    raise ValueError("revised_strategy 与失败方案完全相同")
                merged_rejected = sorted(
                    {
                        *(item.strip() for item in rejected_models if item.strip()),
                        *(
                            item.strip()
                            for item in plan.rejected_models
                            if item.strip()
                        ),
                    }
                )
                plan = plan.model_copy(update={"rejected_models": merged_rejected})
                await self.append_chat_history(
                    {"role": "assistant", "content": content}
                )
                return plan
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)
                logger.warning(
                    f"{question_key} 换模方案无效 (第{attempt}次): {last_error}"
                )
                await self.append_chat_history(
                    {"role": "assistant", "content": content}
                )
                if attempt < 3:
                    await self.append_chat_history(
                        {
                            "role": "user",
                            "content": (
                                f"换模方案未通过结构校验：{last_error}。"
                                "请输出新的单层 JSON；不得再次选择已失败模型，也不得降低验收门槛。"
                            ),
                        }
                    )
        raise ValueError(f"{question_key} 连续3次无法生成有效换模方案: {last_error}")

    async def reconcile_with_council(
        self,
        *,
        coordinator_to_modeler: CoordinatorToModeler,
        primary_plan: ModelerToCoder,
        council_result: ModelCouncilResult,
    ) -> ModelerToCoder:
        """由主建模手综合匿名盲审结论，形成唯一可执行总体方案。"""
        expected_keys = _expected_model_plan_keys(coordinator_to_modeler.questions)
        _normalize_model_plan(primary_plan.questions_solution, expected_keys)
        payload = {
            "task": "综合主方案、独立候选与匿名盲审，形成最终总体建模方案",
            "questions": coordinator_to_modeler.questions,
            "user_requirements": coordinator_to_modeler.user_requirements,
            "primary_plan": primary_plan.questions_solution,
            "independent_scout": council_result.scout_proposal.model_dump(mode="json"),
            "blind_review": council_result.review.model_dump(mode="json"),
            "blind_label_map": council_result.blind_label_map,
            "requirements": [
                "最终决定权属于主建模手，但必须逐条回应盲审的 required_experiments",
                "每个正式小问至少保留简单基线与有竞争力候选，不能只写单一模型",
                "候选必须在完全相同的样本外划分和指标上比较",
                "明确真实独立单位、重复测量、泄漏隔离、支持区、区间估计和回退路线",
                "不得把任何尚未运行的模型描述成效果更好",
                "EDA 与灵敏度分析也必须保留，且与最终候选矩阵一致",
                "只输出以原键名为键的单层 JSON 对象，不输出 Markdown",
            ],
            "expected_keys": expected_keys,
        }
        await self.append_chat_history(
            {
                "role": "assistant",
                "content": json.dumps(
                    primary_plan.questions_solution, ensure_ascii=False
                ),
            }
        )
        await self.append_chat_history(
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        )

        last_error = "未知格式错误"
        for attempt in range(1, 4):
            response = await self._chat(
                history=self.chat_history,
                agent_name=self.__class__.__name__,
                sub_title="模型评审组综合",
            )
            content = response.content or ""
            parsed = repair_json(content)
            try:
                if not parsed:
                    raise ValueError("返回内容不是有效 JSON 对象")
                normalized = _normalize_model_plan(parsed, expected_keys)
                await self.append_chat_history(
                    {"role": "assistant", "content": content}
                )
                return ModelerToCoder(questions_solution=normalized)
            except ValueError as exc:
                last_error = str(exc)
                logger.warning(f"模型评审组综合结果无效 ({attempt}/3): {last_error}")
                await self.append_chat_history(
                    {"role": "assistant", "content": content}
                )
                if attempt < 3:
                    await self.append_chat_history(
                        {
                            "role": "user",
                            "content": (
                                f"综合方案未通过结构校验：{last_error}。"
                                "请保留全部 expected_keys，仅输出修正后的单层 JSON。"
                            ),
                        }
                    )
        raise ValueError(f"模型评审组综合连续 3 次失败: {last_error}")

    async def review_execution_result(
        self,
        *,
        question_key: str,
        question_text: str,
        current_plan: str,
        evidence: dict,
        rejected_models: list[str],
        remaining_runs: int,
    ) -> ModelExecutionReview:
        """复核已通过机器门禁的真实结果，必要时提出可执行的再次建模方案。"""
        if not self.chat_history:
            await self.append_chat_history(
                {"role": "system", "content": self.system_prompt}
            )
        payload = {
            "task": "复核代码手真实运行结果，并决定接受、继续改模或交人工重点审核",
            "question_key": question_key,
            "question_text": question_text,
            "current_modeling_plan": current_plan,
            "execution_evidence": evidence,
            "rejected_models": rejected_models,
            "remaining_execution_runs": remaining_runs,
            "decision_rules": [
                "只能引用 execution_evidence 中真实存在的指标，不得编造数值",
                "不得把 quality_report.key_inference 或 supporting_artifact_previews 中已经存在的系数、置信区间、显著性检验误判为缺失",
                "若复杂预测模型的增益不稳定，但现有简单关系模型已完整报告效应量、聚类稳健或Bootstrap区间、校正后检验和局限，应优先 accept 并如实写明局限，不得仅为追求复杂度而 refine",
                "机器门禁通过不等于模型已经充分；若有明确可执行的改进且仍有运行次数，选择 refine",
                "refine 必须提供完整 revision_plan，且不得重新选择当前模型或 rejected_models",
                "若证据不足、结论脆弱或已无运行次数，选择 manual_review 并明确风险",
                "remaining_execution_runs 小于等于0时禁止选择 refine",
                "accept 或 manual_review 时 revision_plan 必须为 null，禁止输出空对象 {}",
                "revision_plan.candidate_models[*].role 只允许 baseline 或 candidate",
                "只有模型、验证、稳定性和结论均足以支撑论文时才选择 accept",
            ],
            "output_schema": {
                "verdict": "accept | refine | manual_review",
                "summary": "基于实测指标的复核结论",
                "evidence": ["支撑结论的实测证据"],
                "strengths": ["已证实的优点"],
                "weaknesses": ["仍存在的问题或局限"],
                "writer_guidance": "论文手应如何如实解释结果和局限",
                "revision_plan": (
                    "accept/manual_review 时必须为 null；refine 时按 "
                    "revision_plan_schema_when_refine 填写"
                ),
            },
            "revision_plan_schema_when_refine": {
                "diagnosis": "失败诊断",
                "rejected_models": ["已淘汰模型"],
                "candidate_models": [
                    {
                        "name": "简单基线",
                        "role": "baseline",
                        "reason": "比较理由",
                    },
                    {
                        "name": "新候选",
                        "role": "candidate",
                        "reason": "改进理由",
                    },
                ],
                "selected_model": "下一轮主模型，必须来自 candidate_models",
                "revised_strategy": "可直接执行的新方案",
                "validation_plan": "保持一致的独立验证方案",
                "acceptance_criteria": "不得降低的量化标准",
            },
        }
        await self.append_chat_history(
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        )

        normalized_rejected = {
            item.strip().casefold() for item in rejected_models if item.strip()
        }
        quality_report = evidence.get("quality_report", {})
        current_model = (
            str(quality_report.get("selected_model", "")).strip().casefold()
            if isinstance(quality_report, dict)
            else ""
        )
        forbidden_refinement_models = set(normalized_rejected)
        if current_model:
            forbidden_refinement_models.add(current_model)
        last_error = "未知格式错误"
        for attempt in range(1, 4):
            response = await self._chat(
                history=self.chat_history,
                agent_name=self.__class__.__name__,
                sub_title=f"{question_key}-结果复核",
            )
            content = response.content or ""
            parsed = repair_json(content)
            try:
                if not parsed:
                    raise ValueError("返回内容不是有效 JSON 对象")
                review = ModelExecutionReview.model_validate(parsed)
                plan = review.revision_plan
                if review.verdict == "refine" and plan is not None:
                    if (
                        plan.selected_model.strip().casefold()
                        in forbidden_refinement_models
                    ):
                        raise ValueError(
                            f"selected_model {plan.selected_model} 是当前或已淘汰模型，必须换模型"
                        )
                    if plan.revised_strategy.strip() == current_plan.strip():
                        raise ValueError(
                            "refine 的 revised_strategy 不能与当前方案相同"
                        )
                    merged_rejected = sorted(
                        {
                            *(item.strip() for item in rejected_models if item.strip()),
                            *(
                                item.strip()
                                for item in plan.rejected_models
                                if item.strip()
                            ),
                        }
                    )
                    review = review.model_copy(
                        update={
                            "revision_plan": plan.model_copy(
                                update={"rejected_models": merged_rejected}
                            )
                        }
                    )
                if review.verdict == "refine" and remaining_runs <= 0:
                    review = review.model_copy(
                        update={
                            "verdict": "manual_review",
                            "revision_plan": None,
                        }
                    )
                await self.append_chat_history(
                    {"role": "assistant", "content": content}
                )
                return review
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)
                logger.warning(
                    f"{question_key} 结果复核无效 (第{attempt}次): {last_error}"
                )
                await self.append_chat_history(
                    {"role": "assistant", "content": content}
                )
                if attempt < 3:
                    await self.append_chat_history(
                        {
                            "role": "user",
                            "content": (
                                f"复核结果未通过结构校验：{last_error}。"
                                "请仅输出符合 schema 的单层 JSON；refine 必须附完整 "
                                "revision_plan，candidate_models.role 只能是 baseline 或 "
                                "candidate；accept/manual_review 的 revision_plan 必须为 null。"
                            ),
                        }
                    )
        quality_status = (
            str(quality_report.get("status", "unknown"))
            if isinstance(quality_report, dict)
            else "unknown"
        )
        selected_model = (
            str(quality_report.get("selected_model", "unknown"))
            if isinstance(quality_report, dict)
            else "unknown"
        )
        logger.error(
            f"{question_key} 连续3次无法生成有效结果复核，"
            "降级为保守的人工复核结论，避免重复执行已通过门禁的计算。"
        )
        return ModelExecutionReview(
            verdict="manual_review",
            summary=(
                "建模手连续三次返回不符合 schema 的复核结果。"
                "确定性交付门禁已完成，因此保留现有计算产物并转为保守复核，"
                "不因格式错误重复耗费计算资源。"
            ),
            evidence=[
                f"quality_report.status={quality_status}",
                f"quality_report.selected_model={selected_model}",
                f"last_schema_error={last_error}",
            ],
            strengths=["现有产物已通过确定性结构、文件和质量门禁。"],
            weaknesses=["建模手的自由文本复核未能通过结构化 schema 校验。"],
            writer_guidance=(
                "仅使用质量报告和落盘产物中可核对的数值；"
                "保守表述结论，完整报告局限，不得把未通过 schema 的建议写成已验证结论。"
            ),
            revision_plan=None,
        )
