"""探索实验节点：按小问运行候选方法比较并定案。"""

import asyncio

from app.core.llm.errors import NonRetryableLLMError
import json
import hashlib
import re
from pathlib import Path
from typing import Any

from app.core.agents.coder_agent import CoderAgent
from app.core.agents.modeler_agent import ModelerAgent
from app.core.activity import publish_activity
from app.core.citations import (
    FINAL_CITATIONS_FILENAME,
    build_citation_ledger,
    build_citation_table,
    persist_citation_ledger,
)
from app.core.data_scout import summarize_data_profile
from app.core.literature import (
    METHOD_CARDS_FILENAME,
    build_method_cards,
)
from app.core.pilot import (
    PILOT_RESULTS_FILENAME,
    PilotValidationError,
    build_pilot_coder_prompt,
    build_pilot_table,
    pilot_input_context,
    pilot_input_fingerprint,
    pilot_question_filename,
    validate_pilot_results,
)
from app.schemas.A2A import (
    ModelerToCoder,
    PilotPlan,
)
from app.services.task_bus import task_bus
from app.utils.log_util import logger


class PilotNodeMixin:
    """由 RemitWorkFlow 继承；依赖其检查点与节点生命周期方法。"""

    async def _run_pilot_questions(
        self,
        state: dict[str, Any],
        coder_agent: CoderAgent,
        modeler_response: ModelerToCoder,
        plan: PilotPlan,
        feedback: str = "",
    ) -> dict[str, Any]:
        """逐问交付与校验；重试、续跑仅重做尚未通过且输入未变化的部分。"""
        root = Path(self.work_dir)
        table_context = await asyncio.to_thread(pilot_input_context, root)
        progress = state.setdefault("pilot_progress", {})
        combined: dict[str, Any] = {"questions": {}}
        for index, (key, question) in enumerate(plan.questions.items(), 1):
            await self._check_cancelled()
            filename = pilot_question_filename(key)
            path = root / filename
            scoped = PilotPlan(questions={key: question})
            context = {
                "question": self.questions[key],
                "approved_strategy": modeler_response.questions_solution.get(key, ""),
                "protocol": scoped.model_dump(mode="json"),
                "feedback": feedback,
            }
            signature = await asyncio.to_thread(pilot_input_fingerprint, root, context)
            cached = progress.get(key, {})
            reusable = (
                cached.get("input_signature") == signature
                and path.is_file()
                and cached.get("result_hash")
                == hashlib.sha256(path.read_bytes()).hexdigest()
            )
            result = None
            if reusable:
                try:
                    result = validate_pilot_results(root, scoped, filename=filename)
                except PilotValidationError:
                    pass
            if result is None:
                task_context = (
                    "【当前小问与已批准方案】\n"
                    + json.dumps(context, ensure_ascii=False)
                    + "\n"
                    + table_context
                )
                prompt = build_pilot_coder_prompt(
                    scoped, filename=filename, task_context=task_context
                )

                def completion_check() -> bool:
                    try:
                        validate_pilot_results(root, scoped, filename=filename)
                        return True
                    except PilotValidationError:
                        return False

                for attempt in range(2):
                    await publish_activity(
                        self.task_id,
                        f"候选实验 {index}/{len(plan.questions)}：{key}，"
                        + (
                            "修复未通过的产物"
                            if attempt
                            else "先验证基线，再逐个比较候选"
                        ),
                        category="code",
                    )
                    before = (
                        (path.stat().st_mtime_ns, path.stat().st_size)
                        if path.is_file()
                        else None
                    )
                    await coder_agent.run(
                        prompt=prompt,
                        subtask_title=f"pilot:{key}",
                        max_code_executions=3 if attempt else 6,
                        required_files=(filename,),
                        completion_check=completion_check,
                    )
                    try:
                        # 未执行本轮交付的旧文件不能冒充新输入的实验。
                        if (
                            not path.is_file()
                            or (path.stat().st_mtime_ns, path.stat().st_size) == before
                        ):
                            raise PilotValidationError(f"{filename} 尚未生成或更新")
                        result = validate_pilot_results(root, scoped, filename=filename)
                        break
                    except PilotValidationError as error:
                        if attempt:
                            raise
                        prompt = (
                            f"当前小问产物校验失败：{error}。仅修复此小问，复用已验证结果。\n"
                            + build_pilot_coder_prompt(
                                scoped, filename=filename, task_context=task_context
                            )
                        )
                if (
                    await asyncio.to_thread(pilot_input_fingerprint, root, context)
                    != signature
                ):
                    raise PilotValidationError(
                        "实验期间输入发生变化，当前结果不能作为新输入的证据"
                    )
                progress[key] = {
                    "input_signature": signature,
                    "result_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                self.checkpoint.save(state)
            combined["questions"].update(result["questions"])
            await publish_activity(
                self.task_id,
                f"{key} 候选实验记录已校验并保存（{index}/{len(plan.questions)}）",
                category="gate",
            )
        # 仅汇总 Agent 已实际写入并通过结构校验的指标。
        target = root / PILOT_RESULTS_FILENAME
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(combined, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(target)
        return validate_pilot_results(root, plan)

    async def _pilot_node(
        self,
        state: dict[str, Any],
        modeler_agent: ModelerAgent,
        coder_agent: CoderAgent,
        modeler_response: ModelerToCoder,
    ) -> None:
        """探索实验：候选方案小样本 PK → 数据定案 → 知情审批。

        成功后把定案方案写回 state["modeler_response"]；开启审核时暂停，
        自动模式由 execute 立即加载新方案。降级跳过时沿用原方案。
        """
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        if self.code_interpreter is None:
            raise RuntimeError("code interpreter is not initialized")
        node_id = "pilot"
        state.pop("pilot_skipped", None)
        state.setdefault("node_outcomes", {}).pop("pilot", None)
        await self._start_node(state, node_id)
        await self._check_cancelled()
        await task_bus.notify(
            self.task_id, "开始核对探索实验范围；适用时再执行小样本候选比较"
        )
        revision_feedback = self.checkpoint.consume_revision_feedback(state, node_id)
        literature_brief = str(state.get("literature_brief", ""))
        if revision_feedback:
            literature_brief = (
                f"{literature_brief}\n【人工审核退回意见，必须逐条落实】\n"
                f"{revision_feedback}"
            ).strip()
        method_cards = build_method_cards(state.get("literature_review") or {})
        try:
            profile_summary = summarize_data_profile(state.get("data_profile") or {})
            await publish_activity(
                self.task_id,
                "建模手正在核对任务约束与探索实验适用性…",
                category="llm",
            )
            plan_signature = await asyncio.to_thread(
                pilot_input_fingerprint,
                self.work_dir,
                {
                    "questions": self.questions,
                    "solution": modeler_response.questions_solution,
                    "literature": literature_brief,
                    "cards": {
                        key: [card.model_dump(mode="json") for card in cards]
                        for key, cards in method_cards.items()
                    },
                    "task_constraints": state.get("problem") or {},
                },
            )
            if (state.get("problem") or {}).get(
                "task_purpose"
            ) == "numerical_verification":
                plan = PilotPlan(
                    not_applicable_reason="用户明确选择指定计算与数值核验；不进行候选选型，按已批准方法执行独立复算。"
                )
            elif (
                state.get("pilot_plan")
                and not revision_feedback
                and state.get("pilot_plan_signature") in {None, plan_signature}
            ):
                # 旧检查点可保留实验协议；没有输入签名的旧结果仍必须重新核验/执行。
                plan = PilotPlan.model_validate(state["pilot_plan"])
            else:
                plan = await modeler_agent.design_pilot_plan(
                    questions=self.questions,
                    questions_solution=modeler_response.questions_solution,
                    literature_brief=literature_brief,
                    data_profile_summary=profile_summary,
                    backend_language=self.code_interpreter.language,
                    method_cards=method_cards,
                    task_constraints=json.dumps(
                        {
                            "problem": state.get("problem") or {},
                            "modeler_revision_feedback": (
                                state.get("revision_feedback") or {}
                            ).get("modeler", ""),
                            "pilot_revision_feedback": revision_feedback or "",
                        },
                        ensure_ascii=False,
                    ),
                )
            if plan.not_applicable_reason:
                self.checkpoint.retire_pilot_outputs(state)
                state["pilot_plan"] = plan.model_dump(mode="json")
                state["pilot_plan_signature"] = plan_signature
                state["pilot_skipped"] = "不适用：" + plan.not_applicable_reason
                state.setdefault("node_outcomes", {})["pilot"] = {
                    "status": "skipped",
                    "issues": [state["pilot_skipped"]],
                    "summary": "未运行候选比较；按已批准方案继续正式计算与验证",
                }
                await self._complete_node(state, node_id)
                await task_bus.notify(self.task_id, state["pilot_skipped"], "warning")
                return
            expected_keys = {
                key for key in self.questions if re.fullmatch(r"ques\d+", key)
            }
            if not plan.questions or set(plan.questions) != expected_keys:
                raise PilotValidationError("探索实验协议的小问范围与当前任务不一致")
            state["pilot_plan"] = plan.model_dump(mode="json")
            state["pilot_plan_signature"] = plan_signature
            self.checkpoint.save(state)

            feedback_suffix = (
                f"\n\n【人工审核退回意见，必须逐条落实】\n{revision_feedback}"
                if revision_feedback
                else ""
            )
            results = await self._run_pilot_questions(
                state, coder_agent, modeler_response, plan, feedback_suffix
            )

            await publish_activity(
                self.task_id, "建模手正在基于小实验数据定案…", category="llm"
            )
            decision = await modeler_agent.finalize_with_pilot(
                questions=self.questions,
                questions_solution=modeler_response.questions_solution,
                pilot_results=results,
                literature_brief=literature_brief,
                pilot_plan=plan,
            )
        except asyncio.CancelledError:
            raise
        except NonRetryableLLMError:
            # A configuration failure affects formal solving too; do not skip
            # the pilot and issue the same invalid request in the next node.
            raise
        except Exception as exc:
            if not isinstance(exc, PilotValidationError):
                state.setdefault("node_outcomes", {})["pilot"] = {
                    "status": "failed",
                    "issues": [f"探索实验技术故障：{exc}"],
                    "summary": "已保留探索进度，需修复技术故障后继续",
                }
                self.checkpoint.save(state)
                raise
            # 探索实验是增强环节：失败降级按原方案继续，绝不阻塞主流程
            logger.error(f"探索实验降级跳过: {exc}")
            await task_bus.notify(
                self.task_id,
                f"探索实验未能完成，已降级跳过，按原方案继续求解：{exc}",
                "warning",
            )
            state["pilot_skipped"] = str(exc)
            from app.core.project_audit import evaluate_pilot

            state.setdefault("node_outcomes", {})["pilot"] = evaluate_pilot(state)
            # 同一代码手实例将继续正式求解：撤销探索阶段的产物限制指令
            try:
                await coder_agent.append_chat_history(
                    {
                        "role": "user",
                        "content": (
                            "探索实验阶段已结束。此前“不要生成正式交付产物”的"
                            "限制全部作废，后续按正式求解契约执行。"
                        ),
                    }
                )
            except Exception as reset_error:
                logger.warning(f"重置代码手探索上下文失败: {reset_error}")
            await self._complete_node(state, node_id)
            return

        updated_solution = dict(modeler_response.questions_solution)
        for key, item in decision.questions.items():
            if key not in updated_solution or not key.startswith("ques"):
                # 定案只允许覆盖正式小问，防止误改 eda/敏感性方案
                continue
            updated_solution[key] = item.revised_strategy
        updated_response = ModelerToCoder(questions_solution=updated_solution)
        ledger = build_citation_ledger(
            method_cards=method_cards, plan=plan, decision=decision
        )
        persist_citation_ledger(self.work_dir, ledger)
        state["pilot_results"] = results
        state["pilot_decision"] = decision.model_dump(mode="json")
        state["citation_ledger"] = ledger
        # 备份定案前方案：退回 pilot 重跑时恢复真正的"原方案"
        state["modeler_response_pre_pilot"] = modeler_response.model_dump(mode="json")
        state["modeler_response"] = updated_response.model_dump(mode="json")
        await self._complete_node(state, node_id)

        selected_lines = "；".join(
            f"{key}→{item.selected_model}" for key, item in decision.questions.items()
        )
        used_count = int(ledger.get("used_count", 0) or 0)
        judged_count = int(ledger.get("judged_count", 0) or 0)
        citation_note = (
            f" 参考的 {judged_count} 篇文献经代码验证后保留 {used_count} 篇，"
            "只有这些会进论文参考文献。"
            if judged_count
            else ""
        )
        await task_bus.notify(
            self.task_id,
            f"探索实验完成，已按真实数据定案：{selected_lines}。{citation_note}",
            "success",
        )
        artifacts = [PILOT_RESULTS_FILENAME]
        if judged_count:
            artifacts.extend([METHOD_CARDS_FILENAME, FINAL_CITATIONS_FILENAME])
        await self._require_human_approval(
            state,
            node_id,
            summary=(
                "探索实验完成：各候选方案已在小样本、相同数据划分下真实比较，"
                f"并按数据选出每问的正式方案：{selected_lines}。{citation_note}"
            ),
            artifacts=artifacts,
            explain={
                "what_happened": (
                    "每个小问的候选方案先在小样本上真实跑了一遍（相同数据划分公平比较），"
                    "下表是真实结果；已按数据表现选出每问的正式方案"
                    + (
                        "，并逐篇裁决了参考文献是采用、修改还是放弃。"
                        if judged_count
                        else "。"
                    )
                ),
                "next_step": "批准后按入选方案进行正式全量求解",
                "revise_hint": (
                    "对某问的选择不满意，退回本节点并说明方向"
                    "（例如换候选、改抽样规则），会重新做小实验。"
                ),
                "pilot_table": build_pilot_table(results, decision),
                "citation_table": build_citation_table(ledger) if judged_count else {},
                "candidates": [
                    {
                        "question": key,
                        "name": item.selected_model,
                        "role": "selected",
                        "reason": item.justification[:120],
                    }
                    for key, item in decision.questions.items()
                ],
            },
        )
