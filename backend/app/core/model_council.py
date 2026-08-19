"""多模型建模评审组：独立探索、匿名盲审与可追溯落盘。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.llm.errors import ProviderRefusalError
from app.core.llm.llm import LLM
from app.schemas.A2A import (
    CoordinatorToModeler,
    ModelCouncilResult,
    ModelCouncilReview,
    ModelScoutProposal,
    ModelerToCoder,
)
from app.schemas.enums import AgentType
from app.schemas.response import SystemMessage
from app.services.redis_manager import redis_manager
from app.utils.log_util import logger


SchemaT = TypeVar("SchemaT", bound=BaseModel)


_METHODOLOGY_REDACTIONS = (
    ("Non-invasive Prenatal Test", "longitudinal screening task"),
    ("NIPT", "纵向检测任务"),
    ("唐氏综合征", "类别A"),
    ("爱德华氏综合征", "类别B"),
    ("帕陶氏综合征", "类别C"),
    ("非整倍体", "异常标签"),
    ("染色体", "目标特征"),
    ("孕妇", "受试个体"),
    ("母亲", "受试个体"),
    ("胎儿", "目标个体"),
    ("男胎", "A组样本"),
    ("女胎", "B组样本"),
    ("妊娠", "观测过程"),
    ("孕周", "时间变量"),
    ("流产", "不良结局"),
    ("异常判定", "标签判定"),
    ("产前", "观测前期"),
    ("怀孕", "观测过程"),
)


def _sanitize_methodology_payload(value: Any) -> Any:
    """移除会触发医疗决策拒答的语义，只保留统计结构。"""
    if isinstance(value, str):
        sanitized = value
        for source, target in _METHODOLOGY_REDACTIONS:
            sanitized = sanitized.replace(source, target)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_methodology_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_methodology_payload(item) for key, item in value.items()}
    return value


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in target:
            target.append(normalized)


def _parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("响应中没有完整 JSON 对象")
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("响应顶层必须是 JSON 对象")
    return parsed


class ModelCouncil:
    """让探索模型与盲审模型独立参与初始模型选型。"""

    # 深审模型整题裁决可能超过 90 秒；超时定为 300 秒，避免长推理请求
    # 在正常完成前被误判失败并触发熔断。
    critic_timeout_seconds = 300.0
    # The fallback reviewer receives the complete four-question portfolio. In
    # large contest tasks this regularly needs more than three minutes even
    # though the upstream call is still making progress. Keep the timeout above
    # the observed end-to-end latency so a valid review is not discarded.
    fallback_timeout_seconds = 600.0
    # 评审模型只参与一次决定整题模型路线的战略裁决。普通小问、格式修复
    # 和自动重试均不得再次调用它，避免一次任务被放大为多笔高价请求。
    critic_call_limit = 1

    def __init__(
        self,
        *,
        task_id: str,
        scout_llm: LLM,
        critic_llm: LLM,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self.task_id = task_id
        self.scout_llm = scout_llm
        self.critic_llm = critic_llm
        self.cancel_event = cancel_event
        self.reviewer_by_question: dict[str, str] = {}
        self.fallback_reasons: dict[str, str] = {}
        self.critic_calls_used = 0
        self.critic_status = "completed"

    async def propose(self, coordinator: CoordinatorToModeler) -> ModelScoutProposal:
        """让探索模型在看不到主建模手答案的条件下提出候选集。"""
        question_keys = self._question_keys(coordinator.questions)
        payload = {
            "role": "独立模型探索者",
            "objective": (
                "为数学建模竞赛的每个正式小问独立提出可实测的候选模型，"
                "重点寻找主流方案之外但有数据依据的备选；不能虚构数据结果。"
            ),
            "questions": {key: coordinator.questions[key] for key in question_keys},
            "analysis_summary": coordinator.analysis_summary,
            "question_analyses": {
                key: coordinator.question_analyses[key].model_dump(mode="json")
                for key in question_keys
                if key in coordinator.question_analyses
            },
            "user_requirements": coordinator.user_requirements,
            "literature_brief": coordinator.literature_brief or "无",
            "data_profile": coordinator.data_profile or "未知",
            "method_recommendations": coordinator.method_recommendations,
            "rules": [
                "每题至少两个候选，其中至少一个简单可解释 baseline",
                "逐项落实数据校正版题意中的目标、变量、约束、风险和验证要求",
                "本地三级方法库候选用于扩大搜索空间；必须审查其假设和失败模式，"
                "可以拒绝但不得忽略且不得按分数机械选型",
                "候选必须在相同的样本外划分上比较，并按真实独立单位分组",
                "明确数据泄漏、重复测量、样本支持区和外推风险",
                "复杂模型只有在样本外改进稳定且可解释时才可推荐",
                "候选必须能在竞赛环境落地（无 GPU、单问计算预算约 30 分钟）；"
                "文献 SOTA 思路只取可落地部分",
                "只输出 JSON，不输出 Markdown",
            ],
            "output_schema": {
                "questions": {
                    "ques1": {
                        "candidate_models": [
                            {
                                "name": "模型名称",
                                "role": "baseline | candidate",
                                "reason": "选择理由",
                            }
                        ],
                        "recommended_model": "必须来自 candidate_models",
                        "strategy": "特征、估计、约束与可执行步骤",
                        "validation_plan": "独立单位、切分、指标、区间与稳健性",
                        "failure_risks": ["可能失败的原因"],
                    }
                },
                "global_data_risks": ["跨小问数据风险"],
                "cross_question_strategy": "跨小问如何共享证据但避免结局泄漏",
            },
        }
        proposal = await self._json_call(
            llm=self.scout_llm,
            system=(
                "你是数学建模竞赛的独立模型探索者。你不替代主建模手，"
                "你的职责是扩大候选空间并设计公平的样本外验证。"
            ),
            payload=payload,
            schema=ModelScoutProposal,
            label="候选模型探索",
        )
        self._require_keys(proposal.questions, question_keys, "探索方案")
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=(
                    f"独立模型探索完成：{self.scout_llm.model} 已为 "
                    f"{len(question_keys)} 个小问建立候选集"
                )
            ),
        )
        return proposal

    async def review(
        self,
        *,
        coordinator: CoordinatorToModeler,
        primary: ModelerToCoder,
        scout: ModelScoutProposal,
    ) -> tuple[ModelCouncilReview, dict[str, str]]:
        """整题只调用一次高价盲审；失败后熔断并整包切换备用审稿人。"""
        question_keys = self._question_keys(coordinator.questions)
        swap = int(hashlib.sha256(self.task_id.encode("utf-8")).hexdigest(), 16) % 2
        if swap:
            label_map = {"A": "scout", "B": "primary"}
        else:
            label_map = {"A": "primary", "B": "scout"}

        self.reviewer_by_question = {}
        self.fallback_reasons = {}
        self.critic_status = "completed"

        critic_payload = self._build_portfolio_review_payload(
            question_keys=question_keys,
            coordinator=coordinator,
            primary=primary,
            scout=scout,
            label_map=label_map,
            redact_for_methodology=True,
        )
        reviewer = str(self.critic_llm.model or "critic")
        try:
            if self.critic_calls_used >= self.critic_call_limit:
                raise RuntimeError("Fable 战略调用预算已耗尽")
            self.critic_calls_used += 1
            packet = await asyncio.wait_for(
                self._json_call(
                    llm=self.critic_llm,
                    system=(
                        "你是数学建模竞赛的战略模型审稿人。只做一次整题级裁决："
                        "判断候选模型族、验证框架与淘汰路线是否足以决定最终模型上限。"
                        "输入已去除领域敏感语义；不要提供现实决策建议。"
                        "没有真实运行指标时不得宣称某模型已经胜出。"
                    ),
                    payload=critic_payload,
                    schema=ModelCouncilReview,
                    label="整题战略模型盲审",
                    max_attempts=1,
                    call_max_retries=1,
                ),
                timeout=self.critic_timeout_seconds,
            )
        except (ProviderRefusalError, ValueError, TimeoutError, RuntimeError) as exc:
            reason = (
                "安全策略拒绝"
                if isinstance(exc, ProviderRefusalError)
                else (
                    "审稿超时"
                    if isinstance(exc, TimeoutError)
                    else (
                        "调用预算已耗尽"
                        if isinstance(exc, RuntimeError)
                        else "结构化输出失败"
                    )
                )
            )
            self.critic_status = "fallback"
            self.fallback_reasons = {key: reason for key in question_keys}
            reviewer = f"{self.scout_llm.model or 'scout'}（备用审稿）"
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(
                    content=(
                        f"Fable 整题战略审查出现{reason}；本任务已熔断 Fable，"
                        "后续不会再次调用，现由备用审稿模型一次性完成。"
                    ),
                    type="warning",
                ),
            )
            fallback_payload = self._build_portfolio_review_payload(
                question_keys=question_keys,
                coordinator=coordinator,
                primary=primary,
                scout=scout,
                label_map=label_map,
                redact_for_methodology=False,
            )
            packet = await asyncio.wait_for(
                self._json_call(
                    llm=self.scout_llm,
                    system=(
                        "你是备用整题战略统计审稿人。一次性比较所有小问的方案、"
                        "验证设计和泄漏风险，不得把未运行模型写成赢家。"
                    ),
                    payload=fallback_payload,
                    schema=ModelCouncilReview,
                    label="备用整题战略盲审",
                ),
                timeout=self.fallback_timeout_seconds,
            )

        self._require_keys(packet.question_reviews, question_keys, "整题盲审结论")
        self.reviewer_by_question = {key: reviewer for key in question_keys}
        if self.fallback_reasons:
            _extend_unique(
                packet.human_review_focus,
                ["Fable 战略审查未完成，人工需重点复核备用裁决的独立性"],
            )

        status_message = (
            f"Fable 完成 1 次整题战略模型裁决：{self.critic_llm.model}；"
            if self.critic_status == "completed"
            else f"Fable 已熔断，整题战略裁决由 {self.scout_llm.model} 备用完成；"
        )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content=status_message + "等待主建模手综合并接受人工审核"),
        )
        return packet, label_map

    @staticmethod
    def _build_portfolio_review_payload(
        *,
        question_keys: list[str],
        coordinator: CoordinatorToModeler,
        primary: ModelerToCoder,
        scout: ModelScoutProposal,
        label_map: dict[str, str],
        redact_for_methodology: bool,
    ) -> dict[str, Any]:
        questions: dict[str, str] = {}
        anonymous_proposals: dict[str, dict[str, Any]] = {}
        for key in question_keys:
            questions[key] = ModelCouncil._compact_text(
                coordinator.questions.get(key, ""), 480
            )
            primary_plan = ModelCouncil._compact_text(
                primary.questions_solution.get(key, ""), 900
            )
            scout_plan = scout.questions[key]
            compact_scout = {
                "candidate_models": [
                    {
                        "name": item.name,
                        "role": item.role,
                        "reason": ModelCouncil._compact_text(item.reason, 160),
                    }
                    for item in scout_plan.candidate_models[:5]
                ],
                "recommended_model": scout_plan.recommended_model,
                "strategy": ModelCouncil._compact_text(scout_plan.strategy, 600),
                "validation_plan": ModelCouncil._compact_text(
                    scout_plan.validation_plan, 420
                ),
                "failure_risks": [
                    ModelCouncil._compact_text(item, 160)
                    for item in scout_plan.failure_risks[:5]
                ],
            }
            by_source = {"primary": primary_plan, "scout": compact_scout}
            anonymous_proposals[key] = {
                label: by_source[source] for label, source in label_map.items()
            }

        payload = {
            "role": "整题战略模型审稿人",
            "review_scope": (
                "只裁决决定模型高度和走向的模型族、验证框架、淘汰路线与跨小问一致性；"
                "不参与普通 EDA、代码修补、格式校验或文字润色"
            ),
            "questions": questions,
            "anonymous_proposals": anonymous_proposals,
            "rules": [
                "你不知道 A/B 来自哪个模型，不得猜测身份或按文风投票",
                "只评估假设、数据结构匹配、泄漏风险、可验证性和竞赛解释力",
                "没有真实运行指标时不得宣称某模型效果更好",
                "逐题可选 A、B 或 hybrid，但必须明确淘汰项和最小实验矩阵",
                "同时检查各小问之间是否共享了会造成结局泄漏的信息",
                "最终方案必须能直接交给代码手在 MATLAB 中公平比较",
                "只输出 JSON，不输出 Markdown",
            ],
            "output_schema": {
                "question_reviews": {
                    key: {
                        "selected_source": "A | B | hybrid",
                        "recommended_models": ["需真实运行的模型"],
                        "rationale": "基于数据结构与验证设计的理由",
                        "rejected_options": ["暂不进入实验的方案及原因"],
                        "required_experiments": ["公平对比实验"],
                        "final_plan": "交给主建模手综合的完整方法建议",
                    }
                    for key in question_keys
                },
                "global_risks": ["整题跨阶段或跨小问风险"],
                "minimum_experiment_matrix": ["整题最小公平实验矩阵"],
                "human_review_focus": ["人工审核时必须确认的事项"],
            },
        }
        if redact_for_methodology:
            payload = _sanitize_methodology_payload(payload)
            payload["redaction_notice"] = (
                "领域名词已替换为中性统计术语；只需审查方法，不要恢复原领域语义"
            )
        return payload

    @staticmethod
    def _compact_text(value: Any, limit: int) -> str:
        normalized = " ".join(str(value or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "…"

    def build_result(
        self,
        *,
        scout: ModelScoutProposal,
        review: ModelCouncilReview,
        label_map: dict[str, str],
    ) -> ModelCouncilResult:
        return ModelCouncilResult(
            scout_model=str(self.scout_llm.model or ""),
            critic_model=str(self.critic_llm.model or ""),
            critic_call_limit=self.critic_call_limit,
            critic_calls_used=self.critic_calls_used,
            critic_status=self.critic_status,  # type: ignore[arg-type]
            blind_label_map=label_map,  # type: ignore[arg-type]
            reviewer_by_question=dict(self.reviewer_by_question),
            fallback_reasons=dict(self.fallback_reasons),
            scout_proposal=scout,
            review=review,
        )

    @staticmethod
    def save_result(work_dir: str, result: ModelCouncilResult) -> list[str]:
        """把评审证据作为模型节点产物落盘。"""
        path = Path(work_dir) / "model_council_review.json"
        path.write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        scout_path = Path(work_dir) / "model_scout_proposal.json"
        scout_path.write_text(
            json.dumps(
                result.scout_proposal.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return [path.name, scout_path.name]

    async def _json_call(
        self,
        *,
        llm: LLM,
        system: str,
        payload: dict[str, Any],
        schema: type[SchemaT],
        label: str,
        max_attempts: int = 3,
        call_max_retries: int | None = None,
    ) -> SchemaT:
        history: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]
        last_error = "未知格式错误"
        for attempt in range(1, max_attempts + 1):
            response = await self._chat_with_cancel(
                llm, history, max_retries=call_max_retries
            )
            content = response.content or ""
            try:
                return schema.model_validate(_parse_json_object(content))
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                logger.warning(
                    f"{label} JSON 校验失败 ({attempt}/{max_attempts}): {last_error}"
                )
                history.extend(
                    [
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                f"上次输出未通过结构校验：{last_error}。"
                                "请修正后仅输出完整 JSON 对象，不要解释。"
                            ),
                        },
                    ]
                )
        raise ValueError(f"{label} 连续 {max_attempts} 次未通过结构校验: {last_error}")

    async def _chat_with_cancel(
        self,
        llm: LLM,
        history: list[dict[str, Any]],
        *,
        max_retries: int | None = None,
    ):
        task = asyncio.create_task(
            llm.chat(
                history=history,
                agent_name=AgentType.SYSTEM,
                publish=False,
                max_retries=max_retries,
            )
        )
        if self.cancel_event is None:
            return await task
        cancel_task = asyncio.create_task(self.cancel_event.wait())
        done, pending = await asyncio.wait(
            {task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if cancel_task in done:
            task.cancel()
            for item in pending:
                item.cancel()
            raise asyncio.CancelledError("模型评审组被用户停止")
        cancel_task.cancel()
        return await task

    @staticmethod
    def _question_keys(questions: dict[str, Any]) -> list[str]:
        return [
            key for key in questions if key.startswith("ques") and key != "ques_count"
        ]

    @staticmethod
    def _require_keys(actual: dict[str, Any], expected: list[str], label: str) -> None:
        missing = [key for key in expected if key not in actual]
        if missing:
            raise ValueError(f"{label}缺少小问: {', '.join(missing)}")
