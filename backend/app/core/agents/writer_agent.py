"""写作 Agent：把建模与执行证据组织成竞赛论文章节。"""

import asyncio
import json
import re
from typing import Any

from app.core.activity import publish_activity
from app.core.agents.agent import Agent
from app.core.functions import writer_tools
from app.core.llm.llm import LLM
from app.core.prompts.writer import get_writer_prompt
from app.core.paper_mode import PaperMode, SHORT_REPORT_PROMPT, paper_mode
from app.core.structured_output import (
    configured_output_budget,
    expanded_output_budget,
    response_was_truncated,
)
from app.schemas.A2A import WriterResponse
from app.schemas.enums import CompTemplate, FormatOutPut
from app.schemas.response import WriterMessage
from app.services.task_bus import task_bus
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.log_util import logger


class WriterAgent(Agent):
    """按章节推进论文写作，可借助文献检索工具补充引用。"""

    def __init__(
        self,
        task_id: str,
        model: LLM,
        comp_template: CompTemplate = CompTemplate.CHINA,
        format_output: FormatOutPut = FormatOutPut.LaTeX,
        scholar: OpenAlexScholar | None = None,
        context_window: int = 128000,
        cancel_event: asyncio.Event | None = None,
        mode: PaperMode = "full_paper",
    ) -> None:
        super().__init__(
            task_id,
            model,
            context_window,
            cancel_event=cancel_event,
            system_prompt=(
                SHORT_REPORT_PROMPT
                if paper_mode(mode) == "short_report"
                else get_writer_prompt(format_output)
            ),
        )
        self.comp_template = comp_template
        self.format_out_put = format_output
        self.scholar = scholar
        self.is_first_run = True
        self.available_images: list[str] = []

    async def run(  # type: ignore[reportIncompatibleMethodOverride]
        self,
        prompt: str,
        available_images: list[str] | None = None,
        sub_title: str | None = None,
    ) -> WriterResponse:
        """撰写一个章节。

        Args:
            prompt: 章节写作要求。
            available_images: 可按证据贡献选用的图片相对路径。
            sub_title: 章节名，用于前端展示。

        Returns:
            章节正文与脚注。
        """
        logger.info(f"subtitle是:{sub_title}")

        if self.is_first_run:
            self.is_first_run = False
            await self._ensure_system_prompt()

        if available_images:
            self.available_images = available_images
            prompt += self._image_directive(available_images, self.comp_template)

        logger.info(f"{self.__class__.__name__}:开始:执行对话")
        await self._inject_user_notes()
        await publish_activity(
            self.task_id,
            f"论文手正在撰写{sub_title or '论文章节'}…",
            category="llm",
        )
        await self.append_chat_history({"role": "user", "content": prompt})

        # 统一 OpenAI 工具格式：Provider 各自转换，
        # 备用模型跨协议切换时工具形状才不会失配
        response = await self._complete_response(
            history=self.chat_history,
            tools=writer_tools,
            tool_choice="auto",
            agent_name=self.__class__.__name__,
            sub_title=sub_title,
        )

        if response.tool_calls:
            body = await self._roundtrip_with_tools(response, sub_title)
        else:
            body = response.content or ""

        self._record_final_turn(body, response)
        logger.info(f"{self.__class__.__name__}:完成:执行对话")
        return self._section_response(body)

    # ---- 内部步骤 ----

    @staticmethod
    def _section_response(body: str) -> WriterResponse:
        pattern = r"<!--\s*remit-omitted-images:\s*(.*?)\s*-->"
        records = re.findall(pattern, body, re.S)
        omitted = {}
        for record in records:
            data = json.loads(record)
            if not isinstance(data, dict) or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in data.items()
            ):
                raise ValueError("图片取舍记录必须为文件名与理由的映射")
            if omitted.keys() & data.keys():
                raise ValueError("图片取舍记录重复")
            omitted.update(data)
        plain_pattern = r"<!--\s*remit-omit:\s*([^|\n]+)\|([^\n]+?)\s*-->"
        for name, reason in re.findall(plain_pattern, body):
            name, reason = name.strip(), reason.strip()
            if name in omitted:
                raise ValueError("图片取舍记录重复")
            omitted[name] = reason
        clean = re.sub(plain_pattern, "", re.sub(pattern, "", body, flags=re.S))
        return WriterResponse(response_content=clean.strip(), omitted_images=omitted)

    async def _complete_response(self, **kwargs: Any) -> Any:
        """Allow three bounded retries; never publish incomplete prose as done."""
        budget = configured_output_budget(self.model)
        for attempt in range(4):
            response = await self._chat(
                **{**kwargs, **({"purpose": "structure_repair"} if attempt else {})},
                max_tokens=budget,
            )
            truncated = response_was_truncated(response, budget)
            has_text = bool((response.content or "").strip())
            has_allowed_tools = bool(response.tool_calls and kwargs.get("tools"))
            if (
                not truncated
                and (has_text or has_allowed_tools)
                and not (response.tool_calls and not kwargs.get("tools"))
            ):
                return response
            if attempt == 3:
                raise RuntimeError(
                    "论文手连续四次返回空内容、截断正文或无效工具调用，未保存为完成稿"
                )
            if truncated and not has_text:
                budget = max(expanded_output_budget(budget), 32768)
            elif truncated:
                budget = expanded_output_budget(budget)
            await publish_activity(
                self.task_id, "论文章节响应不完整，正在重新生成", category="repair"
            )
        raise RuntimeError("论文手没有返回完整正文")

    @staticmethod
    def _image_directive(
        available_images: list[str], template: CompTemplate = CompTemplate.CHINA
    ) -> str:
        """Offer evidence figures for selection, not a compulsory slide gallery."""
        lines = "\n".join(f"- ![{img}]({img})" for img in available_images)
        references = (
            "用 @fig:文件名.png@ 引用图片，排版器会填入真实图号；alt写简洁图题，不手填图号。"
            if template == CompTemplate.CHINA
            else "正文图号须与图片顺序一致，按 Figure 1, Figure 2 顺序引用，alt只写简洁图题。"
        )
        directive = (
            "\n\n【可供正文选用的证据图片】\n"
            "按独特证据贡献选取下列图片，不要求全部展示；优先保留关键对比、约束与负结果：\n"
            f"{lines}\n"
            "以上是本节完整候选列表。其他章节或共享证据中的图片不属于本节取舍范围，不要为其添加 omit 注释。"
            "图片以 ![简洁图题](文件名) 独占一行，前后留空行，插入相邻论证段落之间。"
            "图题不超过20字，不写文件名。正文先提出结论，"
            f"{references}"
            "自然解释证据，不逐图机械写三行说明，不重复罗列所有点值。"
            "正文末尾为每张未选图片写一条单行隐藏注释：<!-- remit-omit: 文件.png | 具体理由及替代证据位置 -->。"
            "这条记录会单独保存，不进入论文正文；"
            "理由须说明其证据在哪段正文、表格或另一图保留，不能以篇幅为由隐藏不利结果。\n"
        )
        logger.info(f"image_prompt是:{directive}")
        return directive

    async def _roundtrip_with_tools(self, response: Any, sub_title: str | None) -> str:
        """先应答全部工具调用，再做一轮无工具的收尾写作。

        第二轮刻意不提供工具：文献服务不可用时，
        写作手必须产出正文而不是无限重试检索。
        """
        logger.info("检测到工具调用")
        await self.append_chat_history(self._assistant_history_entry(response))

        for tool_call in response.tool_calls:
            reply = await self._serve_tool_call(tool_call)
            await self.append_chat_history(
                {
                    "role": "tool",
                    "content": reply,
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                }
            )

        follow_up = await self._complete_response(
            history=self.chat_history,
            agent_name=self.__class__.__name__,
            sub_title=sub_title,
        )
        return follow_up.content or ""

    async def _serve_tool_call(self, tool_call: Any) -> str:
        """执行一次文献检索；未知工具与检索故障都有兜底文案。"""
        if tool_call.name != "search_papers":
            return f"工具 {tool_call.name} 不受支持。请使用已有证据继续完成正文。"

        logger.info("调用工具: search_papers")
        await task_bus.notify(self.task_id, f"写作手调用{tool_call.name}工具")
        import json

        query = json.loads(tool_call.arguments)["query"]
        await task_bus.publish_message(self.task_id, WriterMessage(content=query))

        try:
            if self.scholar is None:
                raise RuntimeError("scholar 未初始化")
            papers = await self.scholar.search_papers(query)
            result = self.scholar.papers_to_str(papers)
            logger.info(f"搜索文献结果\n{result}")
            return result
        except Exception as exc:
            logger.warning(f"搜索文献失败: {exc}")
            return (
                f"搜索文献失败: {exc}。文献检索当前不可用。请勿编造引文；"
                "仅使用提示中已有的可核验来源和通过质量门禁的证据，"
                "现在直接完成所要求的完整论文正文。"
            )

    def _record_final_turn(self, body: str, response: Any) -> None:
        entry: dict[str, Any] = {"role": "assistant", "content": body}
        if response.reasoning_content:
            entry["reasoning_content"] = response.reasoning_content
        self.chat_history.append(entry)

    # ---- 任务摘要 ----

    async def summarize(self) -> str:
        """请模型回顾本任务产出了什么，供收尾展示。"""
        try:
            request = {
                "role": "user",
                "content": (
                    "用三点收尾：已完成的建模工作、最重要的可核验结果、"
                    "仍需读者留意的限制。不要引入此前未出现的数字。"
                ),
            }
            response = await self._chat(
                history=[*self.chat_history, request],
                agent_name=type(self).__name__,
            )
            await self.append_chat_history(request)
            body = response.content or ""
            self._record_final_turn(body, response)
            return body
        except Exception as exc:
            logger.error(f"总结生成失败: {exc}")
            return "由于网络原因无法生成详细总结，但已完成主要任务处理。"
