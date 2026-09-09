"""编码 Agent：让模型写代码、驱动执行后端、按错误反思自愈。

主循环语义：模型每轮要么调用 ``execute_code`` 工具推进任务，
要么返回纯文本宣告完成；连续失败计入反思预算，
模型服务故障与代码执行故障分别记账、分别兜底。
"""

import asyncio
import json
from typing import Any

from app.config.setting import settings
from app.core.activity import publish_activity
from app.core.agents.agent import Agent
from app.core.functions import get_coder_tools
from app.core.llm.llm import LLM
from app.core.prompts.shared import get_reflection_prompt
from app.core.prompts.coder import get_coder_prompt
from app.schemas.A2A import CoderToWriter
from app.schemas.response import InterpreterMessage, SystemMessage
from app.services.redis_manager import redis_manager
from app.tools.base_interpreter import BaseCodeInterpreter
from app.utils.common_utils import get_current_files
from app.utils.log_util import logger


class CoderAgentRunError(RuntimeError):
    """编码阶段未能完成；质量门不得把它当成有效产出验收。"""


class CoderAgentUnavailableError(CoderAgentRunError):
    """模型服务在多次瞬断重试后仍不可用。"""


class CoderCodeExecutionError(CoderAgentRunError):
    """生成的代码持续执行失败，期间没有任何一次成功运行。"""


class CoderAgentBudgetError(CoderAgentRunError):
    """代码手达到硬轮次或执行次数上限，防止成功调用掩盖失控循环。"""


class CoderAgent(Agent):
    """以工具调用循环推进编码任务的 Agent。"""

    def __init__(
        self,
        task_id: str,
        model: LLM,
        work_dir: str,
        max_chat_turns: int | None = settings.MAX_CHAT_TURNS,
        max_retries: int | None = settings.MAX_RETRIES,
        max_code_executions: int | None = settings.MAX_CODE_EXECUTIONS_PER_RUN,
        code_interpreter: BaseCodeInterpreter | None = None,
        context_window: int = 128000,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        language = code_interpreter.language if code_interpreter else "python"
        super().__init__(
            task_id,
            model,
            context_window,
            cancel_event=cancel_event,
            system_prompt=get_coder_prompt(language),
        )
        self.work_dir = work_dir
        self.max_chat_turns = max_chat_turns or settings.MAX_CHAT_TURNS or 20
        self.max_retries = max_retries or settings.MAX_RETRIES or 3
        self.max_code_executions = (
            max_code_executions or settings.MAX_CODE_EXECUTIONS_PER_RUN
        )
        self.current_chat_turns = 0
        self.current_code_executions = 0
        self.is_first_run = True
        self.code_interpreter = code_interpreter

    # ---- 主循环 ----

    async def run(self, prompt: str, subtask_title: str) -> CoderToWriter:  # type: ignore[reportIncompatibleMethodOverride]
        """推进一个编码子任务直到模型宣告完成。

        Args:
            prompt: 子任务说明。
            subtask_title: 小节标题，用于 notebook 分段与输出归集。

        Returns:
            编码结论与产出图片清单。

        Raises:
            CoderAgentUnavailableError: 模型服务连续不可达。
            CoderCodeExecutionError: 代码连续执行失败。
        """
        logger.info(f"{self.__class__.__name__}:开始:执行子任务: {subtask_title}")
        interpreter = self._require_interpreter()
        # 每个小问使用独立模型上下文；运行状态由 notebook/checkpoint 承接，
        # 避免前一问的大段代码把后续提示膨胀到上下文极限。
        self.chat_history = []
        self.current_token_count = 0
        self.is_first_run = True
        # 预算按单次调用计，跨小问 / 修复尝试不共享。
        self.current_chat_turns = 0
        self.current_code_executions = 0
        interpreter.add_section(subtask_title)
        interpreter.notebook_serializer.add_markdown_segmentation_to_notebook(
            "以下代码与输出属于该工作流节点，可按此标题在 notebook 中定位。",
            subtask_title,
        )

        # 统一 OpenAI 工具格式：Provider 各自转换，
        # 备用模型跨协议切换时工具形状才不会失配
        tools = get_coder_tools(interpreter.language, anthropic=False)

        await self._prime_history(prompt)

        retry_count = 0
        last_error = ""
        last_source = ""

        while True:
            self._enforce_budget(retry_count, last_error, last_source)
            self.current_chat_turns += 1
            await self._inject_user_notes()
            await publish_activity(
                self.task_id,
                f"代码手正在思考下一步（{subtask_title} 第 {self.current_chat_turns} 轮）",
                category="llm",
            )

            try:
                response = await self._call_model(tools)
            except Exception as exc:
                # LLM.chat 已拥有网络重试和备用模型切换权；这里再次重试会把
                # 4 次网关请求乘成 12 次，因此只负责转换为可续跑的阶段错误。
                message = (
                    f"代码手模型服务在内部重试后仍不可用：{exc}。"
                    "已生成文件均已保留，可从当前节点续跑。"
                )
                logger.error(message)
                raise CoderAgentUnavailableError(message) from exc

            if not response.tool_calls:
                # 没有工具调用 = 模型宣告任务完成
                logger.info("没有工具调用，任务完成")
                await publish_activity(
                    self.task_id,
                    f"{subtask_title} 的代码工作完成，进入质量检查",
                    category="gate",
                )
                return CoderToWriter(
                    code_response=response.content,
                    created_images=await interpreter.get_created_images(subtask_title),
                )

            outcome = await self._handle_tool_call(response, interpreter)
            if outcome == "ok":
                retry_count, last_error, last_source = 0, "", ""
                continue
            if outcome is not None:
                # outcome 是 (错误详情)，走反思路径
                retry_count += 1
                last_source, last_error = "execution", outcome
                await self._notify("代码手反思纠正错误", "error")
                await publish_activity(
                    self.task_id,
                    f"代码报错，正在自动修复（{retry_count}/{self.max_retries or '∞'}）",
                    category="repair",
                    detail=outcome[:160],
                )
                await self.append_chat_history(
                    {"role": "user", "content": get_reflection_prompt(outcome, self._last_code)}
                )

    # ---- 步骤拆分 ----

    def _require_interpreter(self) -> BaseCodeInterpreter:
        if self.code_interpreter is None:
            raise RuntimeError("code_interpreter is not initialized")
        return self.code_interpreter

    async def _prime_history(self, prompt: str) -> None:
        """首次运行时铺垫系统提示与数据清单，再注入本轮任务。"""
        if self.is_first_run:
            logger.info("首次运行，添加系统提示和数据集文件信息")
            self.is_first_run = False
            await self._ensure_system_prompt()
        dataset_files = get_current_files(self.work_dir, "data")
        await self.append_chat_history(
            {"role": "user", "content": f"可用数据文件清单：{dataset_files}"}
        )
        await self.append_chat_history({"role": "user", "content": prompt})

    def _enforce_budget(self, retry_count: int, last_error: str, last_source: str) -> None:
        """两类预算耗尽时抛出对应异常；轮次预算共用通用异常。"""
        if retry_count >= self.max_retries:
            if last_source == "model":
                message = (
                    f"代码手模型服务连接连续失败 {self.max_retries} 次：{last_error}。"
                    "已生成文件均已保留，可从当前节点续跑。"
                )
                error_type: type[CoderAgentRunError] = CoderAgentUnavailableError
            else:
                message = (
                    f"代码手连续执行失败 {self.max_retries} 次：{last_error}。"
                    "已生成文件均已保留，可从当前节点续跑。"
                )
                error_type = CoderCodeExecutionError
            logger.error(message)
            raise error_type(message)

        if self.current_chat_turns >= self.max_chat_turns:
            logger.error(f"超过最大聊天次数: {self.max_chat_turns}")
            raise CoderAgentBudgetError(
                f"代码手达到最大对话轮次 {self.max_chat_turns}，任务尚未完成；"
                "已保留当前产物和 checkpoint。"
            )

    async def _notify(self, content: str, level: str) -> None:
        await redis_manager.publish_message(
            self.task_id, SystemMessage(content=content, type=level)  # type: ignore[arg-type]
        )

    async def _call_model(self, tools: list[dict]) -> Any:
        return await self._chat(
            history=self.chat_history,
            tools=tools,
            tool_choice="auto",
            agent_name=self.__class__.__name__,
        )

    _last_code: str = ""

    async def _handle_tool_call(
        self, response: Any, interpreter: BaseCodeInterpreter
    ) -> str | None:
        """执行模型请求的工具调用。

        Returns:
            ``"ok"`` 表示执行成功；错误详情字符串表示需要反思；
            ``None`` 表示非 execute_code 调用（忽略）。
        """
        tool_call = response.tool_calls[0]
        if tool_call.name != "execute_code":
            logger.info(f"忽略非代码工具调用: {tool_call.name}")
            return None

        if self.current_code_executions >= self.max_code_executions:
            raise CoderAgentBudgetError(
                f"代码手达到单节点 {self.max_code_executions} 次代码执行上限；"
                "禁止通过成功调用重置总预算，请拆分计算或改用低复杂度方案。"
            )
        self.current_code_executions += 1

        logger.info(f"调用工具: {tool_call.name}")
        await self._notify(f"代码手调用{tool_call.name}工具", "info")

        code = json.loads(tool_call.arguments)["code"]
        self._last_code = code
        await redis_manager.publish_message(
            self.task_id,
            InterpreterMessage(
                input={
                    "code": code,
                    "language": interpreter.language,
                    "backend": interpreter.backend_name,
                },
            ),
        )
        await self.append_chat_history(self._assistant_history_entry(response))

        await publish_activity(
            self.task_id,
            f"正在执行 {interpreter.backend_name} 代码…",
            category="code",
        )
        output_text, failed, error_detail = await interpreter.execute_code(code)

        tool_reply = {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "name": "execute_code",
            "content": error_detail if failed else output_text,
        }
        await self.append_chat_history(tool_reply)

        if failed:
            logger.warning(f"代码执行错误: {error_detail}")
            return error_detail

        await publish_activity(self.task_id, "代码执行成功，继续下一步", category="code")
        return "ok"
