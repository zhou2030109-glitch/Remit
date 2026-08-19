"""MATLAB 优先执行后端与 Python 回退策略测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.config.setting import settings
from app.core.functions import get_coder_tools
from app.core.prompts.coder import get_coder_prompt
from app.tools.interpreter_factory import create_interpreter
from app.tools.local_interpreter import LocalCodeInterpreter
from app.tools.matlab_interpreter import MatlabCodeInterpreter, MatlabUnavailableError
from app.tools.notebook_serializer import NotebookSerializer


class MatlabInterpreterTests(unittest.IsolatedAsyncioTestCase):
    """验证本机 MATLAB 主路径和严格的不可用回退路径。"""

    def test_matlab_prompt_and_tool_schema_forbid_python(self) -> None:
        prompt = get_coder_prompt("matlab")
        tools = get_coder_tools("matlab")

        self.assertIn("MATLAB code", prompt)
        self.assertIn("never Python", prompt)
        description = tools[0]["function"]["description"]
        self.assertIn("MATLAB syntax only", description)
        self.assertIn("Do not send Python code", description)

    async def test_factory_uses_python_only_after_matlab_probe_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            notebook = NotebookSerializer(work_dir=tmp)
            with (
                patch.object(settings, "CODE_EXECUTION_BACKEND", "matlab"),
                patch.object(settings, "MATLAB_FALLBACK_TO_PYTHON", True),
                patch.object(
                    MatlabCodeInterpreter,
                    "initialize",
                    new=AsyncMock(
                        side_effect=MatlabUnavailableError("license unavailable")
                    ),
                ),
                patch.object(
                    LocalCodeInterpreter,
                    "initialize",
                    new=AsyncMock(),
                ) as python_initialize,
                patch(
                    "app.tools.interpreter_factory.redis_manager.publish_message",
                    new=AsyncMock(),
                ),
            ):
                interpreter = await create_interpreter(
                    task_id="fallback-test",
                    work_dir=tmp,
                    notebook_serializer=notebook,
                )

            self.assertIsInstance(interpreter, LocalCodeInterpreter)
            python_initialize.assert_awaited_once()
            metadata = json.loads(
                (Path(tmp) / "execution_backend.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["selected_backend"], "python")
            self.assertTrue(metadata["python_fallback"])
            self.assertIn("license unavailable", metadata["fallback_reason"])

    async def test_matlab_failure_is_fatal_when_fallback_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(settings, "CODE_EXECUTION_BACKEND", "matlab"),
                patch.object(settings, "MATLAB_FALLBACK_TO_PYTHON", False),
                patch.object(
                    MatlabCodeInterpreter,
                    "initialize",
                    new=AsyncMock(side_effect=MatlabUnavailableError("not installed")),
                ),
            ):
                with self.assertRaises(MatlabUnavailableError):
                    await create_interpreter(
                        task_id="no-fallback-test",
                        work_dir=tmp,
                        notebook_serializer=NotebookSerializer(work_dir=tmp),
                    )

    async def test_engine_timeout_cancels_active_future(self) -> None:
        class MatlabEngineTimeoutError(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            interpreter = MatlabCodeInterpreter(
                task_id="matlab-timeout-test",
                work_dir=tmp,
                notebook_serializer=NotebookSerializer(work_dir=tmp),
                executable=r"C:\MATLAB\bin\matlab.exe",
                timeout=1,
            )
            interpreter.calls_dir.mkdir(parents=True, exist_ok=True)
            future = Mock()
            future.result.side_effect = MatlabEngineTimeoutError(
                "Execution of MATLAB function timed out"
            )
            engine = Mock()
            engine.eval.return_value = future
            interpreter.engine = engine
            interpreter.engine_module = SimpleNamespace(
                TimeoutError=MatlabEngineTimeoutError
            )

            with (
                patch(
                    "app.tools.matlab_interpreter.redis_manager.publish_message",
                    new=AsyncMock(),
                ),
                patch.object(
                    interpreter,
                    "_push_to_websocket",
                    new=AsyncMock(),
                ),
            ):
                output, error_occurred, error_message = await interpreter.execute_code(
                    "pause(10);"
                )

            self.assertTrue(error_occurred)
            self.assertIn("MATLAB 代码执行超过 1 秒", output)
            self.assertIn("已中断", output)
            self.assertEqual(error_message, output)
            future.cancel.assert_called_once_with()
            self.assertIsNone(interpreter._active_future)

    async def test_installed_matlab_reuses_session_and_workspace(self) -> None:
        executable = MatlabCodeInterpreter.discover_executable()
        if not executable:
            self.skipTest("本机未安装 MATLAB")
        with tempfile.TemporaryDirectory() as tmp:
            interpreter = MatlabCodeInterpreter(
                task_id="matlab-live-test",
                work_dir=tmp,
                notebook_serializer=NotebookSerializer(work_dir=tmp),
                executable=executable,
                timeout=60,
            )
            try:
                with patch(
                    "app.tools.matlab_interpreter.redis_manager.publish_message",
                    new=AsyncMock(),
                ):
                    await interpreter.initialize()
                    interpreter.add_section("ques1")
                    first_output, first_error, _ = await interpreter.execute_code(
                        "x = 41; fprintf('FIRST_VALUE=%d\\n', x);"
                    )
                    second_output, second_error, _ = await interpreter.execute_code(
                        "x = x + 1; writematrix(x, 'matlab_result.csv'); "
                        "fprintf('SECOND_VALUE=%d\\n', x);"
                    )

                self.assertFalse(first_error, first_output)
                self.assertFalse(second_error, second_output)
                self.assertIn("FIRST_VALUE=41", first_output)
                self.assertIn("SECOND_VALUE=42", second_output)
                self.assertEqual(
                    (Path(tmp) / "matlab_result.csv")
                    .read_text(encoding="utf-8")
                    .strip(),
                    "42",
                )
                metadata = json.loads(
                    (Path(tmp) / "execution_backend.json").read_text(encoding="utf-8")
                )
                self.assertEqual(metadata["selected_backend"], "matlab")
                self.assertEqual(metadata["backend_mode"], "persistent_engine")
                self.assertFalse(metadata["python_fallback"])
            finally:
                await interpreter.cleanup()
