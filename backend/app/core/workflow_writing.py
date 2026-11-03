"""论文写作节点：并行章节写作、审稿润色与终稿生成。"""

import asyncio

import re
from pathlib import Path
from typing import Any

from app.config.setting import settings
from app.core.agents.writer_agent import WriterAgent
from app.core.deliverable_contract import (
    DeliverableValidationError,
    build_question_contract,
    collect_grounding_values,
    collect_model_quality_evidence,
    validate_final_paper,
    validate_writer_section,
)
from app.core.llm.llm import LLM
from app.core.activity import publish_activity
from app.core.paper_judge import (
    build_review_explain_numbers,
    judge_paper,
    save_review,
)
from app.core.paper_quality import audit_paper_style
from app.models.user_output import UserOutput
from app.schemas.A2A import (
    WriterResponse,
)
from app.schemas.request import Problem
from app.schemas.enums import CompTemplate
from app.services.redis_manager import redis_manager
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.log_util import logger
from app.utils.paper_polish import (
    PaperRenderError,
    polish_markdown,
    render_paper_deliverables,
)


class WritingNodeMixin:
    """由 RemitWorkFlow 继承；依赖其检查点与节点生命周期方法。"""

    def _clone_writer_agent(
        self,
        writer_llm: LLM,
        problem: Problem,
        scholar: OpenAlexScholar,
    ) -> WriterAgent:
        """为并行章节创建独立写作手实例，避免共享对话历史串线。"""
        return self._new_writer_agent(writer_llm, problem, scholar)

    def _new_writer_agent(
        self,
        writer_llm: LLM,
        problem: Problem,
        scholar: OpenAlexScholar,
    ) -> WriterAgent:
        """Build an isolated writing session with the workflow's shared limits."""
        return WriterAgent(
            problem.task_id,
            writer_llm,
            scholar=scholar,
            cancel_event=self.cancel_event,
            format_output=problem.format_output,
            context_window=settings.WRITER_CONTEXT_WINDOW,
            comp_template=problem.comp_template,
        )

    async def _write_chapters_parallel(
        self,
        pending_write: list[tuple[str, str]],
        state: dict[str, Any],
        writer_llm: LLM,
        problem: Problem,
        scholar: OpenAlexScholar,
        user_output: UserOutput,
    ) -> None:
        """并行撰写结构章节，逐章校验返修，最后合并为一次人工验收。

        各章节输入互不引用（flows 六章 prompt 相互独立），并行不损失一致性；
        合并审批仍保留退回单章的能力（revision_targets 含全部章节）。
        """
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        await self._start_node(state, f"write:{pending_write[0][0]}")
        await self._check_cancelled()
        await redis_manager.notify(
            self.task_id, f"论文手开始并行撰写 {len(pending_write)} 个结构章节"
        )

        # 插话统一广播到全部章节：并行克隆各自 drain 会被随机一章独占
        user_notes = await redis_manager.drain_user_notes(self.task_id)
        note_block = (
            "\n\n【用户实时插话，全部章节都必须遵循】\n"
            + "\n".join(f"- {note}" for note in user_notes)
            if user_notes
            else ""
        )
        prompts: dict[str, str] = {}
        for key, prompt in pending_write:
            feedback = self.checkpoint.consume_revision_feedback(state, f"write:{key}")
            prompts[key] = (
                f"{prompt}\n\n【人工审核退回意见，必须重写本节并逐条落实】\n{feedback}"
                if feedback
                else prompt
            ) + note_block

        # 限制同时在飞的章节数，避免六路并发触发中转限流连锁
        write_semaphore = asyncio.Semaphore(3)

        async def _generate(key: str) -> WriterResponse:
            async with write_semaphore:
                agent = self._clone_writer_agent(writer_llm, problem, scholar)
                return await agent.run(prompt=prompts[key], sub_title=key)

        outcomes = await asyncio.gather(
            *(_generate(key) for key, _ in pending_write),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, asyncio.CancelledError):
                raise asyncio.CancelledError("任务被用户停止")
        await self._check_cancelled()

        failed_chapters: list[tuple[str, str]] = []
        for (key, _), outcome in zip(pending_write, outcomes):
            node_id = f"write:{key}"
            writer_response: WriterResponse | None = (
                outcome if isinstance(outcome, WriterResponse) else None
            )
            retry_prompt = prompts[key]
            if writer_response is None:
                await redis_manager.notify(
                    self.task_id,
                    f"{key} 章节并行生成失败，转入串行重写: {outcome}",
                    "warning",
                )
            last_error = ""
            for writer_attempt in range(1, 3):
                if writer_response is None:
                    agent = self._clone_writer_agent(writer_llm, problem, scholar)
                    writer_response = await agent.run(
                        prompt=retry_prompt, sub_title=key
                    )
                try:
                    validate_writer_section(
                        key,
                        writer_response.response_content,
                        expected_question_count=(
                            self.ques_count if key == "firstPage" else 0
                        ),
                    )
                    break
                except DeliverableValidationError as error:
                    last_error = str(error)
                    writer_response = None
                    if writer_attempt >= 2:
                        break
                    await redis_manager.notify(
                        self.task_id,
                        f"{key} 章节未通过门禁，写作手返修: {error}",
                        "warning",
                    )
                    retry_prompt = (
                        f"上一稿未通过章节门禁：{error}\n"
                        "请重写本节并逐条修复上述全部问题。\n" + prompts[key]
                    )
            if writer_response is None:
                # 先完成其余章节再统一挂起，避免恢复后浪费重写成功章节
                failed_chapters.append((key, last_error))
                continue
            user_output.set_res(key, writer_response)
            state.setdefault("write_results", {})[key] = writer_response.model_dump(
                mode="json"
            )
            await self._complete_node(state, node_id)

        if failed_chapters:
            failed_keys = "、".join(key for key, _ in failed_chapters)
            first_key, first_error = failed_chapters[0]
            await redis_manager.notify(
                self.task_id,
                f"章节 {failed_keys} 连续 2 次未通过门禁，"
                f"任务已挂起等待人工处理；其余章节已完成并保留。",
                "error",
            )
            await self._require_human_approval(
                state,
                f"write:{first_key}",
                summary=(
                    f"章节 {failed_keys} 连续 2 次未通过章节门禁，已暂停任务；"
                    f"其余章节已完成并保留。最后一次失败原因：{first_error}\n"
                    "批准后将只重写失败章节；退回并附意见可指导重写方向。"
                ),
                allow_incomplete=True,
            )

        chapter_labels = "、".join(
            self.checkpoint.node_label(f"write:{key}", state)
            for key, _ in pending_write
        )
        last_node = f"write:{pending_write[-1][0]}"
        await self._require_human_approval(
            state,
            last_node,
            summary=(
                f"论文 {len(pending_write)} 个结构章节已并行完成并通过章节门禁："
                f"{chapter_labels}。"
            ),
            explain={
                "what_happened": (
                    f"{len(pending_write)} 个结构章节（{chapter_labels}）已并行撰写完成，"
                    "各章均通过自动检查；本次合并为一次验收。"
                ),
                "next_step": self._next_step_plain(state, last_node),
                "revise_hint": (
                    "对某一章不满意，退回时在目标节点里选择该章节，"
                    "只会重写该章及其下游。"
                ),
            },
        )

    async def _review_and_polish_paper(
        self,
        state: dict[str, Any],
        user_output: UserOutput,
        writer_agent: WriterAgent,
        judge_llm: LLM,
    ) -> dict[str, Any] | None:
        """终稿评委评审 + 最弱章节定向重写；失败降级，绝不阻塞交付。"""
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        existing = state.get("paper_review")
        if isinstance(existing, dict) and existing:
            # 哨兵值表示上次评审已降级跳过：一次 finalize 生命周期内不重试
            return None if existing.get("skipped") else existing
        paper_text = user_output.get_result_to_save()
        if not paper_text.strip():
            return None
        await publish_activity(
            self.task_id, "评委视角正在通读整篇论文并打分…", category="gate"
        )
        try:
            deterministic_audit = audit_paper_style(paper_text)
            review = await judge_paper(
                judge_llm,
                paper_text,
                self.ques_count,
                deterministic_findings=list(deterministic_audit.issues),
            )
        except Exception as exc:
            logger.warning(f"终稿评审降级跳过: {exc}")
            return None
        if not review:
            await redis_manager.notify(
                self.task_id,
                "终稿评审未能完成，跳过评委复审直接进入最终检查",
                "warning",
            )
            state["paper_review"] = {"skipped": True}
            self.checkpoint.save(state)
            return None

        score_line = "、".join(
            f"{label}{review['scores'].get(key, 0):.0f}分"
            for key, label in (
                ("abstract", "摘要"),
                ("modeling", "建模"),
                ("solution_validation", "求解验证"),
                ("evidence", "证据"),
                ("style", "表述"),
                ("writing", "规范"),
                ("innovation", "创新"),
            )
        )
        await redis_manager.notify(
            self.task_id,
            f"评委复审完成：总分 {review['overall']:.0f}/10（{score_line}）。"
            + (
                f"将定向重写 {len(review['weakest_sections'])} 个最弱章节"
                if review["weakest_sections"]
                else "未发现需要重写的章节"
            ),
        )
        rewritten_sections: list[str] = []
        for item in review["weakest_sections"][:2]:
            key = str(item.get("section_key", ""))
            if key not in user_output.seq:
                continue
            current = user_output.get_res().get(key, {}).get("response_content", "")
            if not isinstance(current, str) or not current.strip():
                continue
            await publish_activity(
                self.task_id,
                f"评委指出《{key}》需改进，论文手定向重写中…",
                category="llm",
            )
            rewrite_prompt = (
                f"【终稿评委复审重写指令】章节({key})被评委指出问题：\n"
                f"{item.get('problems', '')}\n"
                f"修改要求：{item.get('revision_directive', '')}\n"
                "硬性约束：保留原稿中的所有 Markdown 标题（如 ## 5.2）一字不改；"
                "保留全部真实数值、图片引用与结论不变，只改表达、结构与深度；"
                "只输出重写后的本章节完整正文。\n\n【当前稿件】\n"
                f"{current}"
            )
            # 复原 solve 阶段的门禁强度：重写稿不得引入假数值或丢图
            grounding: set[float] | None = None
            required_images: list[str] | None = None
            rewrite_quality_report: dict[str, Any] | None = None
            question_text = ""
            if key.startswith("ques"):
                question_text = str(self.questions.get(key, ""))
                try:
                    contract = build_question_contract(
                        question_key=key,
                        question_text=question_text,
                        user_requirements=str(
                            (state.get("problem") or {}).get("user_requirements", "")
                        ),
                        task_purpose=(state.get("problem") or {}).get(
                            "task_purpose", "modeling"
                        ),
                    )
                    evidence = collect_model_quality_evidence(self.work_dir, contract)
                    grounding = collect_grounding_values(evidence)
                    quality = evidence.get("quality_report")
                    rewrite_quality_report = (
                        quality if isinstance(quality, dict) else None
                    )
                except Exception as exc:
                    logger.warning(f"重写校验证据重建失败 {key}: {exc}")
                required_images = [
                    str(image)
                    for image in (
                        state.get("solution_results", {}).get(key, {}) or {}
                    ).get("paper_ready_images", [])
                ]
            try:
                rewritten = await writer_agent.run(prompt=rewrite_prompt, sub_title=key)
                validate_writer_section(
                    key,
                    rewritten.response_content,
                    required_images=required_images,
                    omitted_images=rewritten.omitted_images,
                    quality_report=rewrite_quality_report,
                    question_text=question_text,
                    grounding_values=grounding,
                    expected_question_count=(
                        self.ques_count if key == "firstPage" else 0
                    ),
                )
                # 标题保持：重写不得弄丢原稿的编号主标题，否则终稿硬门禁必失败
                for heading in re.findall(r"(?m)^##\s+\d+\.\d+", current):
                    if heading not in (rewritten.response_content or ""):
                        raise DeliverableValidationError(
                            f"{key} 重写稿丢失原稿标题 {heading}"
                        )
            except Exception as exc:
                await redis_manager.notify(
                    self.task_id,
                    f"章节 {key} 定向重写未通过校验，保留原稿：{exc}",
                    "warning",
                )
                continue
            user_output.set_res(key, rewritten)
            if key in state.get("write_results", {}):
                state["write_results"][key] = rewritten.model_dump(mode="json")
            elif key in state.get("solution_results", {}):
                state["solution_results"][key]["writer_response"] = (
                    rewritten.model_dump(mode="json")
                )
            item["rewritten"] = True
            rewritten_sections.append(key)
        if rewritten_sections:
            review["rewritten_sections"] = rewritten_sections
        user_output.save_result()
        state["paper_review"] = review
        self.checkpoint.save(state)
        try:
            save_review(self.work_dir, review)
        except Exception as exc:
            logger.warning(f"评审结果落盘失败: {exc}")
        return review

    async def _finalize_node(
        self,
        state: dict[str, Any],
        user_output: UserOutput,
        writer_agent: WriterAgent,
        judge_llm: LLM,
    ) -> None:
        """合并全文并执行最终硬门禁。"""
        if self.checkpoint is None:
            raise RuntimeError("workflow checkpoint is not initialized")
        await self._start_node(state, "finalize")
        await self._check_cancelled()
        revision_feedback = self.checkpoint.consume_revision_feedback(state, "finalize")
        if revision_feedback:
            # finalize 是确定性合并检查，无法按意见自动修改内容；
            # 明确告知而不是静默吞掉意见。
            await redis_manager.notify(
                self.task_id,
                "最终合并节点无法按退回意见自动修改论文内容，"
                f"意见仅作记录：{revision_feedback}\n"
                "如需修改正文，请退回具体章节节点。",
                "warning",
            )
        user_output.save_result()
        review = await self._review_and_polish_paper(
            state, user_output, writer_agent, judge_llm
        )
        paper_markdown = polish_markdown(
            user_output.get_result_to_save(), Path(self.work_dir)
        )
        try:
            validate_final_paper(
                self.work_dir,
                user_output.get_res(),
                expected_sections=user_output.seq,
                paper_text=paper_markdown,
            )
            comp_template = CompTemplate(
                str((state.get("problem") or {}).get("comp_template", "CHINA"))
            )
            from app.services.async_io import run_cancellable
            from app.services.call_ledger import scope as call_scope

            with call_scope(
                self.task_id, run_id=state.get("execution_id"), stage_id="finalize"
            ):
                delivery = await run_cancellable(
                    render_paper_deliverables,
                    paper_markdown,
                    self.work_dir,
                    comp_template,
                )
        except (DeliverableValidationError, PaperRenderError, ValueError) as error:
            # 此时全部计算与写作已完成，损失最大，绝不作废任务。
            # 以前的终稿批准只覆盖当时的有效产物；本次检查失败后不可复用。
            for field in ("approved_nodes", "completed_nodes"):
                state[field] = [
                    node for node in state.get(field, []) if node != "finalize"
                ]
            self.checkpoint.save(state)
            await redis_manager.notify(
                self.task_id,
                f"最终论文未通过硬门禁，任务已挂起等待人工处理：{error}",
                "error",
            )
            await self._require_human_approval(
                state,
                "finalize",
                summary=(
                    f"最终论文未通过硬门禁，已暂停任务。失败原因：{error}\n"
                    "请退回失败原因对应的写作/求解章节重写后自动重新合并"
                    "（退回 finalize 本身只会原样重检，无法修复正文问题）。"
                ),
                allow_incomplete=True,
            )
        await self._complete_node(state, "finalize")
        await redis_manager.notify(
            self.task_id,
            f"整篇论文通过最终硬门禁并完成两次 LaTeX 编译："
            f"{delivery.tex_path.name}、{delivery.pdf_path.name} "
            f"（{delivery.page_count} 页）",
            "success",
        )
        review_summary = ""
        review_numbers: list[dict[str, Any]] = []
        if isinstance(review, dict) and review:
            review_summary = (
                f"评委复审总分 {review.get('overall', 0):.0f}/10。"
                f"{str(review.get('summary', ''))[:200]}"
            )
            rewritten_sections = review.get("rewritten_sections") or []
            if rewritten_sections:
                review_summary += (
                    f"（已按评委意见定向重写：{'、'.join(rewritten_sections)}；"
                    "评分为重写前基准）"
                )
            review_numbers = build_review_explain_numbers(review)
        await self._require_human_approval(
            state,
            "finalize",
            summary=(
                "整篇论文已通过内容、表述、LaTeX 编译与 PDF 渲染门禁，"
                "等待你的最终验收。"
            ),
            artifacts=[delivery.pdf_path.name, delivery.tex_path.name],
            explain={
                "what_happened": (
                    "全文已合并为可编译 LaTeX，并由同一源码生成 PDF；"
                    "章节、字数、证据表达、参考文献、图片、纸型、空白页与"
                    "抽样渲染均已检查。" + review_summary
                ),
                "key_numbers": review_numbers,
                "next_step": "批准后直接交付 res.pdf 与 res.tex，任务完成",
                "revise_hint": "对某章不满意，退回对应章节修改后会自动重新合并。",
            },
        )
