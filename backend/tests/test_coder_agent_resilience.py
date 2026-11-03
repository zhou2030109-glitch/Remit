"""Regression tests for coder retries and resumable delivery finalization."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.setting import ApiType
from app.core.agents.agent import _message_tokens
from app.core.agents.coder_agent import (
    CoderAgent,
    CoderAgentRunError,
    CoderAgentUnavailableError,
)
from app.core.llm.errors import NonRetryableLLMError
from app.core.llm.types import StandardResponse, ToolCall
from app.core.deliverable_contract import ArtifactConsistencyValidationError


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
    max_tokens: int | None = None,
) -> CoderAgent:
    model = MagicMock()
    model.max_tokens = max_tokens
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
    async def test_validation_feedback_repairs_in_same_run_before_exhausting_budget(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            agent = _make_agent(max_code_executions=3)
            agent.work_dir = tmp
            agent._inject_user_notes = AsyncMock()
            path = Path(tmp, "eda_quality_report.json")
            agent._chat = AsyncMock(
                side_effect=[
                    _tool_response("wrong", "wrong"),
                    _tool_response("fixed", "fixed"),
                ]
            )

            async def execute(code):
                path.write_text(json.dumps({"valid": code == "fixed"}))
                return ("executed", False, "")

            def validate():
                if not json.loads(path.read_text())["valid"]:
                    raise ArtifactConsistencyValidationError("实体编号错位：007")
                return True

            agent.code_interpreter.execute_code.side_effect = execute
            with (
                patch(
                    "app.core.agents.coder_agent.task_bus.publish_message",
                    new_callable=AsyncMock,
                ),
                patch(
                    "app.core.agents.coder_agent.publish_activity",
                    new_callable=AsyncMock,
                ),
                patch.object(
                    agent, "append_chat_history", wraps=agent.append_chat_history
                ) as history,
            ):
                await agent.run(
                    "verify",
                    "eda",
                    required_files=(path.name,),
                    completion_check=validate,
                )
            feedback = [
                call.args[0].get("content", "") for call in history.call_args_list
            ]
            self.assertTrue(any("实体编号错位：007" in text for text in feedback))
            self.assertEqual(agent._chat.await_count, 2)
            self.assertEqual(agent.current_code_executions, 2)

    async def test_delivery_check_stops_calls_only_after_new_valid_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = _make_agent()
            agent.work_dir = tmp
            agent._inject_user_notes = AsyncMock()
            path = Path(tmp, "pilot_ques1_results.json")
            path.write_text('{"valid": true}')
            agent._chat = AsyncMock(
                side_effect=[
                    _tool_response("inspect", "inspect"),
                    _tool_response("partial", "partial"),
                    _tool_response("finish", "finish"),
                ]
            )

            async def execute(code):
                if code == "partial":
                    path.write_text('{"valid": false}')
                if code == "finish":
                    path.write_text('{"valid": true, "new": true}')
                return ("executed", False, "")

            agent.code_interpreter.execute_code.side_effect = execute
            with (
                patch(
                    "app.core.agents.coder_agent.task_bus.publish_message",
                    new_callable=AsyncMock,
                ),
                patch(
                    "app.core.agents.coder_agent.publish_activity",
                    new_callable=AsyncMock,
                ),
            ):
                result = await agent.run(
                    "pilot",
                    "pilot:ques1",
                    required_files=(path.name,),
                    completion_check=lambda: json.loads(path.read_text())["valid"],
                )
            self.assertEqual(agent._chat.await_count, 3)
            self.assertEqual(agent.current_code_executions, 3)
            self.assertIn("实际指标仍由后续审查和用户验收", result.code_response)

    async def test_multi_call_reply_keeps_only_first_call(self):
        agent = _make_agent()
        multiple = StandardResponse(
            tool_calls=[
                *_tool_response("first", "disp(1)").tool_calls,
                *_tool_response("second", "disp(2)").tool_calls,
            ]
        )
        agent._chat = AsyncMock(return_value=multiple)
        result = await agent._call_model([{"type": "function"}])
        self.assertIs(result, multiple)
        self.assertEqual([call.id for call in result.tool_calls], ["first"])
        agent._chat.assert_awaited_once()
        self.assertIs(agent._chat.call_args.kwargs["parallel_tool_calls"], False)

    def test_tool_argument_variants_are_normalized(self):
        cases = {
            '{"code": "disp(1)"}': "disp(1)",
            '```json\n{"code": "disp(2)",}\n```': "disp(2)",
            '{"source": "disp(3)"}': "disp(3)",
            '{"code": "", "script": "disp(4)"}': "disp(4)",
            "disp(5)": "disp(5)",
        }
        for arguments, expected in cases.items():
            with self.subTest(arguments=arguments):
                call = ToolCall(id="c", name="execute_code", arguments=arguments)
                self.assertEqual(CoderAgent._validated_code(call), expected)
                self.assertEqual(json.loads(call.arguments), {"code": expected})
        call = ToolCall(id="c", name="execute_code", arguments={"code": "disp(6)"})  # type: ignore[arg-type]
        self.assertEqual(CoderAgent._validated_code(call), "disp(6)")

    async def test_same_node_quality_repair_keeps_observed_schema_and_fixed_code(self):
        agent = _make_agent(max_code_executions=2)
        captured = []
        responses = iter(
            [
                _tool_response("bad", "bad_missing_check()"),
                _tool_response("fixed", "disp(raw)"),
                StandardResponse(content="缺少最终质量报告"),
                StandardResponse(content="针对报告继续处理"),
                StandardResponse(content="新问题"),
            ]
        )

        async def chat(**kwargs):
            captured.append(json.dumps(kwargs["history"], ensure_ascii=False))
            return next(responses)

        agent._chat = chat
        agent._inject_user_notes = AsyncMock()
        agent.code_interpreter.execute_code.side_effect = [
            ("", True, "操作数必须可转换为标量逻辑值"),
            ("真实列名：经度、纬度；18行", False, ""),
        ]
        with patch(
            "app.core.agents.coder_agent.task_bus.publish_message", new=AsyncMock()
        ):
            await agent.run("读取数据", "eda")
            await agent.run("补全质量报告", "eda")
            await agent.run("求解第一问", "ques1")
        repair = captured[3]
        self.assertIn("标量逻辑值", repair)
        self.assertIn("真实列名：经度、纬度；18行", repair)
        self.assertIn("disp(raw)", repair)
        self.assertIn("同一节点的质量返修", repair)
        self.assertIn("执行预算为 2 次", repair)
        self.assertNotIn("你已用完本轮", repair)
        self.assertNotIn("真实列名：经度、纬度；18行", captured[4])
        self.assertEqual(agent.code_interpreter.execute_code.await_count, 2)

    def test_execution_summary_uses_error_evidence(self):
        summarize = CoderAgent._execution_failure_summary
        self.assertIn("列名数量", summarize("VariableNames 属性必须包含一个名称"))
        self.assertIn(
            "空单元格", summarize("错误使用 fprintf 不支持 <missing> 字符串元素")
        )
        self.assertIn("数组", summarize("操作数必须可转换为标量逻辑值"))
        self.assertEqual(summarize("unknown failure"), "代码执行出错")
        source_context = (
            "【实际报错源码】\n87: table(x,'VariableNames',{'名称'});\n"
            "89: T.中文=1;\n错误: 文件: call_0011.m 行: 89\n文本字符无效。"
        )
        self.assertIn("语法检查失败", summarize(source_context))

    async def test_output_budget_learned_from_truncation_is_reused(self):
        agent = _make_agent(max_tokens=8192)
        agent._chat = AsyncMock(
            side_effect=[
                StandardResponse(content=None, finish_reason="max_output_tokens"),
                StandardResponse(content="first complete response"),
                StandardResponse(content="second complete response"),
            ]
        )
        with patch(
            "app.core.agents.coder_agent.task_bus.publish_message", new=AsyncMock()
        ):
            await agent._call_model([])
            await agent._call_model([])
        assert [c.kwargs["max_tokens"] for c in agent._chat.await_args_list] == [
            8192,
            16384,
            16384,
        ]
        assert agent.model.max_tokens == 8192

    async def test_narration_without_deliverables_keeps_execution_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = _make_agent()
            agent.work_dir = tmp
            agent._chat = AsyncMock(
                side_effect=[
                    _tool_response("inspect", "inspect()"),
                    StandardResponse(content="接下来保存数据"),
                    _tool_response("save", "save()"),
                    StandardResponse(content="已保存"),
                ]
            )

            async def execute(code):
                if code == "save()":
                    Path(tmp, "quality.json").write_text('{"status":"manual_review"}')
                return ("真实表列名和DEM属性", False, "")

            agent.code_interpreter.execute_code.side_effect = execute
            with patch(
                "app.core.agents.coder_agent.task_bus.publish_message",
                new=AsyncMock(),
            ):
                result = await agent.run(
                    "clean", "eda", required_files=("quality.json",)
                )
            self.assertEqual(result.code_response, "已保存")
            self.assertEqual(agent.current_code_executions, 2)
            self.assertIn(
                "真实表列名和DEM属性",
                json.dumps(agent.chat_history, ensure_ascii=False),
            )

    async def test_missing_artifact_narration_retries_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = _make_agent()
            agent.work_dir = tmp
            agent._chat = AsyncMock(return_value=StandardResponse(content="尚未完成"))
            with patch(
                "app.core.agents.coder_agent.task_bus.publish_message",
                new=AsyncMock(),
            ):
                result = await agent.run(
                    "clean", "eda", required_files=("missing.json",)
                )
            self.assertEqual(agent._chat.await_count, 3)
            self.assertEqual(result.code_response, "尚未完成")
            agent.code_interpreter.execute_code.assert_not_awaited()

    async def test_invalid_tool_arguments_have_bounded_retries(self) -> None:
        for arguments in (
            "{}",
            "[]",
            "{",
            '{"code":null}',
            '{"code":42}',
            '{"code":" "}',
        ):
            with self.subTest(arguments=arguments):
                agent = _make_agent()
                agent._inject_user_notes = AsyncMock()
                agent._chat = AsyncMock(
                    return_value=StandardResponse(
                        tool_calls=[
                            ToolCall(id="bad", name="execute_code", arguments=arguments)
                        ]
                    )
                )
                with patch(
                    "app.core.agents.coder_agent.task_bus.publish_message",
                    new=AsyncMock(),
                ):
                    with self.assertRaisesRegex(CoderAgentRunError, "连续 3 次"):
                        await agent.run("eda", "eda")
                self.assertEqual(agent._chat.await_count, 3)
                agent.code_interpreter.execute_code.assert_not_awaited()
                self.assertEqual(agent.current_code_executions, 0)

    async def test_truncated_empty_tool_is_retried_before_execution(self) -> None:
        agent = _make_agent(max_tokens=4096)
        agent._inject_user_notes = AsyncMock()
        agent._chat = AsyncMock(
            side_effect=[
                StandardResponse(
                    finish_reason="max_tokens",
                    tool_calls=[
                        ToolCall(id="bad", name="execute_code", arguments="{}")
                    ],
                ),
                _tool_response("good", "print(1)"),
                StandardResponse(content="done"),
            ]
        )
        agent.code_interpreter.execute_code.return_value = ("1", False, "")
        with patch(
            "app.core.agents.coder_agent.task_bus.publish_message", new=AsyncMock()
        ):
            result = await agent.run("eda", "eda")
        self.assertEqual(result.code_response, "done")
        agent.code_interpreter.execute_code.assert_awaited_once_with("print(1)")
        self.assertEqual(agent._chat.await_args_list[1].kwargs["max_tokens"], 16384)

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

    def test_missing_interpreter_raises_explicit_runtime_error(self) -> None:
        agent = _make_agent()
        agent.code_interpreter = None

        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            agent._require_interpreter()

    async def test_configuration_error_preserves_nonretryable_class(self):
        agent = _make_agent()
        agent._chat = AsyncMock(side_effect=NonRetryableLLMError("missing workspaceid"))
        agent._prime_history = AsyncMock()
        agent._inject_user_notes = AsyncMock()
        with patch("app.core.agents.coder_agent.publish_activity", new=AsyncMock()):
            with self.assertRaisesRegex(NonRetryableLLMError, "workspaceid"):
                await agent.run("finish ques1", "ques1")
        agent._chat.assert_awaited_once()
        agent.code_interpreter.execute_code.assert_not_awaited()

    async def test_transport_failure_is_not_returned_as_completed_code(self) -> None:
        agent = _make_agent(max_retries=2)
        agent._chat = AsyncMock(side_effect=ConnectionError("provider offline"))

        with (
            patch(
                "app.core.agents.coder_agent.task_bus.publish_message",
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
            "app.core.agents.coder_agent.task_bus.publish_message",
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
            "app.core.agents.coder_agent.task_bus.publish_message",
            new=AsyncMock(),
        ):
            first = await agent.run("eda", "eda")
            second = await agent.run("ques1 repair", "ques1")

        self.assertEqual(first.code_response, "first")
        self.assertEqual(second.code_response, "second")

    async def test_execution_budget_forces_summary_without_an_extra_tool_call(
        self,
    ) -> None:
        agent = _make_agent(max_code_executions=2, max_chat_turns=10)
        calls = 0

        async def respond(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return _tool_response("call-1", "good1")
            if calls == 2:
                return _tool_response("call-2", "good2")
            if kwargs["tools"]:
                return _tool_response("call-3", "good3")
            return StandardResponse(content="summary from existing results")

        agent._chat = AsyncMock(side_effect=respond)
        agent.code_interpreter.execute_code.side_effect = [
            ("one", False, ""),
            ("two", False, ""),
        ]

        with patch(
            "app.core.agents.coder_agent.task_bus.publish_message",
            new=AsyncMock(),
        ):
            result = await agent.run("finish ques1", "ques1")

        self.assertEqual(result.code_response, "summary from existing results")
        self.assertEqual(agent.code_interpreter.execute_code.await_count, 2)
        self.assertEqual(agent._chat.await_args_list[-1].kwargs["tools"], [])
        self.assertEqual(agent._chat.await_args_list[-1].kwargs["tool_choice"], "none")

    async def test_run_can_apply_a_smaller_repair_execution_budget(self) -> None:
        agent = _make_agent(max_code_executions=8, max_chat_turns=10)

        async def respond(**kwargs):
            if kwargs["tools"]:
                return _tool_response("repair-call", "inspect_or_repair()")
            return StandardResponse(content="repair budget exhausted")

        agent._chat = AsyncMock(side_effect=respond)
        agent.code_interpreter.execute_code.return_value = ("ok", False, "")

        with patch(
            "app.core.agents.coder_agent.task_bus.publish_message",
            new=AsyncMock(),
        ):
            result = await agent.run(
                "repair only",
                "ques1",
                max_code_executions=2,
            )

        self.assertEqual(result.code_response, "repair budget exhausted")
        self.assertEqual(agent.code_interpreter.execute_code.await_count, 2)
        self.assertEqual(agent.max_code_executions, 8)

    async def test_warns_model_to_persist_contract_before_last_execution(self) -> None:
        agent = _make_agent(max_code_executions=3, max_chat_turns=10)
        histories: list[list[dict]] = []

        async def respond(**kwargs):
            histories.append([dict(item) for item in kwargs["history"]])
            if len(histories) <= 2:
                return _tool_response(f"call-{len(histories)}", "work()")
            return StandardResponse(content="files persisted")

        agent._chat = AsyncMock(side_effect=respond)
        agent.code_interpreter.execute_code.return_value = ("ok", False, "")

        with patch(
            "app.core.agents.coder_agent.task_bus.publish_message",
            new=AsyncMock(),
        ):
            result = await agent.run("solve and persist", "ques1")

        self.assertEqual(result.code_response, "files persisted")
        self.assertTrue(
            any(
                "下一次执行必须优先" in str(item.get("content", ""))
                for item in histories[1]
            )
        )

    async def test_each_subtask_starts_with_fresh_model_history(self) -> None:
        agent = _make_agent()
        captured: list[list[dict]] = []

        async def finish(**kwargs):
            captured.append([dict(item) for item in kwargs["history"]])
            return StandardResponse(content="done")

        agent._chat = AsyncMock(side_effect=finish)
        with patch(
            "app.core.agents.coder_agent.task_bus.publish_message",
            new=AsyncMock(),
        ):
            await agent.run("first prompt", "ques1")
            await agent.run("second prompt", "ques2")

        self.assertEqual(len(captured), 2)
        self.assertNotIn("first prompt", json.dumps(captured[1], ensure_ascii=False))
        self.assertIn("second prompt", json.dumps(captured[1], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
