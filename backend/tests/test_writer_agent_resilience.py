"""Regression tests for graceful WriterAgent literature-search fallback."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.setting import ApiType
from app.core.agents.writer_agent import WriterAgent
from app.core.llm.types import StandardResponse, ToolCall


class WriterAgentResilienceTests(unittest.IsolatedAsyncioTestCase):
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
                StandardResponse(content="Complete paper section from verified evidence."),
            ]
        )

        with patch(
            "app.core.agents.writer_agent.redis_manager.publish_message",
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
        self.assertEqual({message["tool_call_id"] for message in tool_messages}, {"search-1", "search-2"})
        self.assertTrue(
            all("请勿编造引文" in message["content"] for message in tool_messages)
        )


if __name__ == "__main__":
    unittest.main()
