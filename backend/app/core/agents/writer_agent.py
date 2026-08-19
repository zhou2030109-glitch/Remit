"""写作 Agent：把建模与执行证据组织成竞赛论文章节。"""

import asyncio
from typing import Any

from app.core.activity import publish_activity
from app.core.agents.agent import Agent
from app.core.functions import writer_tools
from app.core.llm.llm import LLM
from app.core.prompts import get_writer_prompt
from app.schemas.A2A import WriterResponse
from app.schemas.enums import CompTemplate, FormatOutPut
from app.schemas.response import SystemMessage, WriterMessage
from app.services.redis_manager import redis_manager
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.log_util import logger


class WriterAgent(Agent):
    """按章节推进论文写作，可借助文献检索工具补充引用。"""

    def __init__(
        self,
        task_id: str,
        model: LLM,
        comp_template: CompTemplate = CompTemplate.CHINA,
        format_output: FormatOutPut = FormatOutPut.Markdown,
        scholar: OpenAlexScholar | None = None,
        context_window: int = 128000,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        super().__init__(task_id, model, context_window, cancel_event=cancel_event)
        self.comp_template = comp_template
        self.format_out_put = format_output
        self.scholar = scholar
        self.is_first_run = True
        self.system_prompt = get_writer_prompt(format_output)
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
            available_images: 需要插入正文的图片相对路径。
            sub_title: 章节名，用于前端展示。

        Returns:
            章节正文与脚注。
        """
        logger.info(f"subtitle是:{sub_title}")

        if self.is_first_run:
            self.is_first_run = False
            await self.append_chat_history(
                {"role": "system", "content": self.system_prompt}
            )

        if available_images:
            self.available_images = available_images
            prompt += self._image_directive(available_images)

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
        response = await self._chat(
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
        return WriterResponse(response_content=body, footnotes=[])

    # ---- 内部步骤 ----

    @staticmethod
    def _image_directive(available_images: list[str]) -> str:
        """把图片清单转成必须执行的插图指令。"""
        lines = "\n".join(f"- ![{img}]({img})" for img in available_images)
        directive = (
            "\n\n【必须插入的图片列表】\n"
            "以下图片是代码手生成的，你必须在论文相关段落后用 Markdown 格式逐一插入：\n"
            f"{lines}\n"
            "插入格式为独占一行的 ![描述](文件名)，每张图片后需配3行以上的分析解读。\n"
        )
        logger.info(f"image_prompt是:{directive}")
        return directive

    async def _roundtrip_with_tools(self, response: Any, sub_title: str | None) -> str:
        """先应答全部工具调用，再做一轮无工具的收尾写作。

        第二轮刻意不提供工具：文献服务不可用时，
        写作手必须产出正文而不是无限重试检索。
        """
        logger.info("检测到工具调用")
        await self.append_chat_history(self._assistant_entry(response))

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

        follow_up = await self._chat(
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
        await redis_manager.publish_message(
            self.task_id, SystemMessage(content=f"写作手调用{tool_call.name}工具")
        )
        import json

        query = json.loads(tool_call.arguments)["query"]
        await redis_manager.publish_message(self.task_id, WriterMessage(content=query))

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

    @staticmethod
    def _assistant_entry(response: Any) -> dict:
        entry: dict[str, Any] = {"role": "assistant", "content": response.content}
        if response.reasoning_content:
            entry["reasoning_content"] = response.reasoning_content
        entry["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in response.tool_calls
        ]
        return entry

    def _record_final_turn(self, body: str, response: Any) -> None:
        entry: dict[str, Any] = {"role": "assistant", "content": body}
        if response.reasoning_content:
            entry["reasoning_content"] = response.reasoning_content
        self.chat_history.append(entry)

    # ---- 任务摘要 ----

    async def summarize(self) -> str:
        """请模型回顾本任务产出了什么，供收尾展示。"""
        try:
            await self.append_chat_history(
                {"role": "user", "content": "请简单总结以上完成什么任务取得什么结果:"}
            )
            response = await self._chat(
                history=self.chat_history, agent_name=self.__class__.__name__
            )
            body = response.content or ""
            self._record_final_turn(body, response)
            return body
        except Exception as exc:
            logger.error(f"总结生成失败: {exc}")
            return "由于网络原因无法生成详细总结，但已完成主要任务处理。"
