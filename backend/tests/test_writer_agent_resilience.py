"""Regression tests for graceful WriterAgent literature-search fallback."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.setting import ApiType
from app.core.agents.writer_agent import WriterAgent
from app.core.llm.types import StandardResponse, ToolCall


class WriterAgentResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_tool_arguments_are_not_executed(self) -> None:
        agent = WriterAgent(task_id="task-test", model=MagicMock())
        agent._serve_tool_call = AsyncMock()
        agent._chat = AsyncMock(
            side_effect=[
                StandardResponse(
                    content="partial",
                    finish_reason="length",
                    tool_calls=[
                        ToolCall(id="bad", name="search_papers", arguments='{"query":')
                    ],
                ),
                StandardResponse(
                    content="经过验证的完整论文章节。", finish_reason="stop"
                ),
            ]
        )
        with patch(
            "app.core.agents.writer_agent.task_bus.publish_message",
            new=AsyncMock(),
        ):
            result = await agent.run("Write from evidence", sub_title="ques1")
        self.assertEqual(result.response_content, "经过验证的完整论文章节。")
        agent._serve_tool_call.assert_not_awaited()
        self.assertEqual(agent._chat.await_args_list[0].kwargs["max_tokens"], 32768)
        self.assertEqual(agent._chat.await_args_list[1].kwargs["max_tokens"], 65536)
        self.assertFalse(any(m.get("content") == "partial" for m in agent.chat_history))

    async def test_empty_writer_responses_fail_with_a_bounded_retry(self) -> None:
        agent = WriterAgent(task_id="task-test", model=MagicMock())
        agent._chat = AsyncMock(
            return_value=StandardResponse(content="", finish_reason="stop")
        )
        with patch(
            "app.core.agents.writer_agent.task_bus.publish_message",
            new=AsyncMock(),
        ):
            with self.assertRaisesRegex(RuntimeError, "未保存为完成稿"):
                await agent.run("Write from evidence", sub_title="ques1")
        self.assertEqual(agent._chat.await_count, 4)

    async def test_search_failure_falls_back_to_prose(self) -> None:
        model = MagicMock()
        model.api_type = ApiType.OPENAI_CHAT
        scholar = MagicMock()
        scholar.search_papers = AsyncMock(
            side_effect=ValueError("OpenAlex email is not configured")
        )
        agent = WriterAgent(task_id="task-test", model=model, scholar=scholar)
        agent._chat = AsyncMock(
            side_effect=[
                StandardResponse(
                    content="I will search first.",
                    tool_calls=[
                        ToolCall(
                            id="search-1",
                            name="search_papers",
                            arguments=json.dumps({"query": "Turnbull 1976"}),
                        ),
                        ToolCall(
                            id="search-2",
                            name="search_papers",
                            arguments=json.dumps({"query": "discrete survival"}),
                        ),
                    ],
                ),
                StandardResponse(
                    content="Complete paper section from verified evidence."
                ),
            ]
        )

        with patch(
            "app.core.agents.writer_agent.task_bus.publish_message",
            new=AsyncMock(),
        ):
            result = await agent.run("Write the section", sub_title="ques2")

        self.assertEqual(
            result.response_content,
            "Complete paper section from verified evidence.",
        )
        self.assertEqual(scholar.search_papers.await_count, 2)
        self.assertEqual(agent._chat.await_count, 2)
        follow_up = agent._chat.await_args_list[1].kwargs
        self.assertNotIn("tools", follow_up)
        self.assertNotIn("tool_choice", follow_up)
        tool_messages = [
            message for message in agent.chat_history if message.get("role") == "tool"
        ]
        self.assertEqual(
            {message["tool_call_id"] for message in tool_messages},
            {"search-1", "search-2"},
        )
        self.assertTrue(
            all("请勿编造引文" in message["content"] for message in tool_messages)
        )

    async def test_reasoning_only_truncation_receives_room_for_prose(self) -> None:
        agent = WriterAgent(task_id="task-test", model=MagicMock())
        agent._chat = AsyncMock(
            side_effect=[
                StandardResponse(content="", finish_reason="length"),
                StandardResponse(content="完整的证据论述。", finish_reason="stop"),
            ]
        )
        with patch(
            "app.core.agents.writer_agent.task_bus.publish_message",
            new=AsyncMock(),
        ):
            result = await agent.run("Write from evidence", sub_title="ques1")
        self.assertEqual(result.response_content, "完整的证据论述。")
        self.assertEqual(agent._chat.await_args_list[1].kwargs["max_tokens"], 65536)


if __name__ == "__main__":
    unittest.main()
