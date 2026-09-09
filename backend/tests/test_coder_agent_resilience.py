"""Regression tests for coder retries and resumable delivery finalization."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.setting import ApiType
from app.core.agents.agent import _message_tokens
from app.core.agents.coder_agent import (
    CoderAgent,
    CoderAgentBudgetError,
    CoderAgentUnavailableError,
)
from app.core.llm.types import StandardResponse, ToolCall


def _tool_response(call_id: str, code: str) -> StandardResponse:
    return StandardResponse(
        content="running",
        tool_calls=[
            ToolCall(
                id=call_id,
                name="execute_code",
                arguments=json.dumps({"code": code}),
            )
        ],
    )


def _make_agent(
    *,
    max_retries: int = 2,
    max_chat_turns: int = 10,
    max_code_executions: int = 8,
) -> CoderAgent:
    model = MagicMock()
    model.api_type = ApiType.OPENAI_CHAT
    interpreter = MagicMock()
    interpreter.language = "matlab"
    interpreter.backend_name = "MATLAB test double"
    interpreter.notebook_serializer = MagicMock()
    interpreter.execute_code = AsyncMock()
    interpreter.get_created_images = AsyncMock(return_value=[])
    return CoderAgent(
        task_id="task-test",
        model=model,
        work_dir=".",
        max_retries=max_retries,
        max_chat_turns=max_chat_turns,
        max_code_executions=max_code_executions,
        code_interpreter=interpreter,
    )


class CoderAgentResilienceTests(unittest.IsolatedAsyncioTestCase):
    def test_tool_arguments_are_counted_toward_context_budget(self) -> None:
        small = _message_tokens({"role": "assistant", "content": "ok"})
        with_code = _message_tokens(
            {
                "role": "assistant",
                "content": "ok",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "execute_code",
                            "arguments": json.dumps({"code": "x" * 3000}),
                        },
                    }
                ],
            }
        )
        self.assertGreater(with_code, small + 500)

    async def test_transport_failure_is_not_returned_as_completed_code(self) -> None:
        agent = _make_agent(max_retries=2)
        agent._chat = AsyncMock(side_effect=ConnectionError("provider offline"))

        with (
            patch(
                "app.core.agents.coder_agent.redis_manager.publish_message",
                new=AsyncMock(),
            ),
            patch("app.core.agents.coder_agent.asyncio.sleep", new=AsyncMock()),
        ):
            with self.assertRaisesRegex(CoderAgentUnavailableError, "provider offline"):
                await agent.run("finish ques1", "ques1")
        self.assertEqual(agent._chat.await_count, 1)

    async def test_successful_execution_resets_consecutive_error_budget(self) -> None:
        agent = _make_agent(max_retries=2)
        agent._chat = AsyncMock(
            side_effect=[
                _tool_response("call-1", "bad1"),
                _tool_response("call-2", "good1"),
                _tool_response("call-3", "bad2"),
                _tool_response("call-4", "good2"),
                StandardResponse(content="done"),
            ]
        )
        agent.code_interpreter.execute_code.side_effect = [
            ("", True, "first MATLAB error"),
            ("first result", False, ""),
            ("", True, "second MATLAB error"),
            ("second result", False, ""),
        ]

        with patch(
            "app.core.agents.coder_agent.redis_manager.publish_message",
            new=AsyncMock(),
        ):
            result = await agent.run("finish ques1", "ques1")

        self.assertEqual(result.code_response, "done")

    async def test_chat_turn_budget_is_per_run_not_whole_workflow(self) -> None:
        agent = _make_agent(max_chat_turns=1)
        agent._chat = AsyncMock(
            side_effect=[
                StandardResponse(content="first"),
                StandardResponse(content="second"),
            ]
        )

        with patch(
            "app.core.agents.coder_agent.redis_manager.publish_message",
            new=AsyncMock(),
        ):
            first = await agent.run("eda", "eda")
            second = await agent.run("ques1 repair", "ques1")

        self.assertEqual(first.code_response, "first")
        self.assertEqual(second.code_response, "second")

    async def test_successful_tools_do_not_reset_total_execution_budget(self) -> None:
        agent = _make_agent(max_code_executions=2, max_chat_turns=10)
        agent._chat = AsyncMock(
            side_effect=[
                _tool_response("call-1", "good1"),
                _tool_response("call-2", "good2"),
                _tool_response("call-3", "good3"),
            ]
        )
        agent.code_interpreter.execute_code.side_effect = [
            ("one", False, ""),
            ("two", False, ""),
        ]

        with patch(
            "app.core.agents.coder_agent.redis_manager.publish_message",
            new=AsyncMock(),
        ):
            with self.assertRaisesRegex(CoderAgentBudgetError, "2"):
                await agent.run("finish ques1", "ques1")

        self.assertEqual(agent.code_interpreter.execute_code.await_count, 2)

    async def test_each_subtask_starts_with_fresh_model_history(self) -> None:
        agent = _make_agent()
        captured: list[list[dict]] = []

        async def finish(**kwargs):
            captured.append([dict(item) for item in kwargs["history"]])
            return StandardResponse(content="done")

        agent._chat = AsyncMock(side_effect=finish)
        with patch(
            "app.core.agents.coder_agent.redis_manager.publish_message",
            new=AsyncMock(),
        ):
            await agent.run("first prompt", "ques1")
            await agent.run("second prompt", "ques2")

        self.assertEqual(len(captured), 2)
        self.assertNotIn("first prompt", json.dumps(captured[1], ensure_ascii=False))
        self.assertIn("second prompt", json.dumps(captured[1], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
