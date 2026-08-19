"""Agent 公共骨架：对话历史、取消协作与上下文压缩。"""

import asyncio
from typing import Any

from app.core.llm.llm import LLM, simple_chat
from app.schemas.response import SystemMessage
from app.services.redis_manager import redis_manager
from app.utils.log_util import logger

# 中英混合文本的保守 token 估算（字符 / token）
_CHARS_PER_TOKEN = 3
# 单条消息的角色 / 分隔符等结构开销
_MESSAGE_OVERHEAD_TOKENS = 4
# 估算用量占上下文窗口的比例，越过即压缩
_DEFAULT_TOKEN_THRESHOLD_RATIO = 0.75
# 总结时单条消息的最大引用长度
_SUMMARY_SNIPPET_LIMIT = 500


def _rough_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _message_tokens(msg: dict) -> int:
    return _rough_tokens(msg.get("content") or "") + _MESSAGE_OVERHEAD_TOKENS


class Agent:
    """所有角色 Agent 的基类。

    子类通过 ``self._chat`` 发起模型调用（可被取消打断），
    通过 ``self.append_chat_history`` 记录对话并自动维护上下文长度。
    """

    def __init__(
        self,
        task_id: str,
        model: LLM,
        context_window: int = 128000,
        token_threshold_ratio: float = _DEFAULT_TOKEN_THRESHOLD_RATIO,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self.task_id = task_id
        self.model = model
        self.context_window = context_window
        self.token_threshold_ratio = token_threshold_ratio
        self.cancel_event = cancel_event
        self.chat_history: list[dict] = []
        self.current_token_count = 0

    # ---- 模型调用 ----

    async def _chat(self, **kwargs: Any) -> Any:
        """透传调用底层 LLM；挂接了取消事件时可被即时打断。"""
        if not self.cancel_event:
            return await self.model.chat(**kwargs)

        chat_task = asyncio.create_task(self.model.chat(**kwargs))
        watch_task = asyncio.create_task(self.cancel_event.wait())
        done, pending = await asyncio.wait(
            {chat_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if watch_task in done:
            for task in pending:
                task.cancel()
            raise asyncio.CancelledError("任务被用户停止")
        watch_task.cancel()
        return await chat_task

    async def run(self, prompt: str, system_prompt: str, sub_title: str) -> Any:
        """标准的单轮问答入口：注入 system + user，返回回复文本。"""
        name = self.__class__.__name__
        try:
            logger.info(f"{name}:开始:执行对话")
            await self.append_chat_history({"role": "system", "content": system_prompt})
            await self.append_chat_history({"role": "user", "content": prompt})

            response = await self._chat(
                history=self.chat_history, agent_name=name, sub_title=sub_title
            )
            self._record_assistant_turn(response)
            logger.info(f"{name}:完成:执行对话")
            return response.content
        except asyncio.CancelledError:
            logger.info(f"{name}:任务被用户停止")
            raise
        except Exception as exc:
            logger.error(f"Agent执行失败: {exc}")
            return f"执行过程中遇到错误: {exc}"

    def _record_assistant_turn(self, response: Any) -> None:
        """把助手回复登记进历史，并按真实用量校准 token 计数。"""
        msg: dict[str, Any] = {"role": "assistant", "content": response.content}
        if response.reasoning_content:
            msg["reasoning_content"] = response.reasoning_content
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in response.tool_calls
            ]
        self.chat_history.append(msg)

        if response.usage.prompt_tokens > 0:
            self.current_token_count = response.usage.prompt_tokens
        else:
            self.current_token_count += _message_tokens(msg)

    # ---- 用户实时插话 ----

    async def _inject_user_notes(self) -> None:
        """把任务运行中用户的插话注入下一轮对话，支持中途纠偏。"""
        notes = await redis_manager.drain_user_notes(self.task_id)
        if not notes:
            return
        joined = "\n".join(f"- {note}" for note in notes)
        await self.append_chat_history(
            {
                "role": "user",
                "content": (
                    "【用户实时插话，优先级最高，必须立即调整后续做法】\n"
                    f"{joined}"
                ),
            }
        )
        try:
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(
                    content=f"已把你的 {len(notes)} 条补充意见注入当前步骤",
                    type="success",
                ),
            )
        except Exception as exc:
            logger.warning(f"插话注入确认消息发送失败: {exc}")

    # ---- 历史维护与压缩 ----

    async def append_chat_history(self, msg: dict) -> None:
        """追加一条消息；非工具消息追加后检查是否需要压缩。

        工具消息之间不能插入压缩动作，否则会拆散 tool_call 链。
        """
        self.chat_history.append(msg)
        self.current_token_count += _message_tokens(msg)
        if msg.get("role") != "tool":
            await self.compress_if_needed()

    async def compress_if_needed(self) -> None:
        """上下文逼近窗口上限时，把旧对话总结成一段摘要。"""
        budget = int(self.context_window * self.token_threshold_ratio)
        if self.current_token_count <= budget:
            return

        name = self.__class__.__name__
        logger.info(
            f"{name}:触发记忆压缩，当前 token ~{self.current_token_count}，阈值 {budget}"
        )
        try:
            await self._summarize_and_rebuild()
            logger.info(
                f"{name}:记忆压缩完成，压缩至 {len(self.chat_history)} 条记录，"
                f"约 {self.current_token_count} tokens"
            )
        except Exception as exc:
            logger.error(f"记忆压缩失败，退化为安全截断: {exc}")
            self.chat_history = self._fallback_tail()
            self._recount_tokens()

    async def _summarize_and_rebuild(self) -> None:
        """保留 system 与近期完整对话，中段请模型总结。"""
        system_msg = (
            self.chat_history[0]
            if self.chat_history and self.chat_history[0]["role"] == "system"
            else None
        )
        keep_from = self._safe_tail_start()
        first_stale = 1 if system_msg else 0
        if keep_from <= first_stale:
            logger.info(f"{self.__class__.__name__}:无需压缩，记录数量合理")
            return

        stale_text = "\n".join(
            f"{m['role']}: {(m.get('content') or '')[:_SUMMARY_SNIPPET_LIMIT]}"
            for m in self.chat_history[first_stale:keep_from]
        )
        prompt_messages = ([system_msg] if system_msg else []) + [
            {
                "role": "user",
                "content": (
                    "请简洁总结以下对话的关键内容和重要结论，"
                    f"保留重要的上下文信息：\n\n{stale_text}"
                ),
            }
        ]
        summary = await simple_chat(self.model, prompt_messages)

        self.chat_history = (
            ([system_msg] if system_msg else [])
            + [{"role": "assistant", "content": f"[历史对话总结] {summary}"}]
            + self.chat_history[keep_from:]
        )
        self._recount_tokens()

    def _recount_tokens(self) -> None:
        self.current_token_count = sum(_message_tokens(m) for m in self.chat_history)

    # ---- 切割点安全性：保证不拆散 tool_call / tool 配对 ----

    def _tool_call_ids_in(self, span: range) -> set[str]:
        ids: set[str] = set()
        for i in span:
            msg = self.chat_history[i]
            if isinstance(msg, dict):
                ids.update(
                    tc.get("id") for tc in msg.get("tool_calls") or [] if tc.get("id")
                )
        return ids

    def _orphan_tool_exists(self, start_idx: int) -> bool:
        """从 start_idx 截断是否会产生找不到调用方的 tool 消息。"""
        declared = self._tool_call_ids_in(range(start_idx, len(self.chat_history)))
        for i in range(start_idx, len(self.chat_history)):
            msg = self.chat_history[i]
            if isinstance(msg, dict) and msg.get("role") == "tool":
                call_id = msg.get("tool_call_id")
                if call_id and call_id not in declared:
                    return True
        return False

    def _safe_tail_start(self) -> int:
        """选保留尾部的起点：至少留 3 条，且不拆散工具调用对。"""
        earliest = max(0, len(self.chat_history) - 3)
        for idx in range(earliest, -1, -1):
            if not self._orphan_tool_exists(idx):
                return idx
        return len(self.chat_history) - 1

    def _fallback_tail(self) -> list[dict]:
        """总结失败时的降级方案：system + 安全尾部。"""
        if not self.chat_history:
            return []
        head = (
            [self.chat_history[0]]
            if self.chat_history[0].get("role") == "system"
            else []
        )
        for size in range(1, min(4, len(self.chat_history)) + 1):
            start = len(self.chat_history) - size
            if not self._orphan_tool_exists(start):
                return head + self.chat_history[start:]
        # 尾部都拆不开时，只留最后一条非工具消息
        for msg in reversed(self.chat_history):
            if isinstance(msg, dict) and msg.get("role") != "tool":
                return head + [msg]
        return head
