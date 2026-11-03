"""Keep ordinary inspection output intact and cap aggregate model context."""

import asyncio
from unittest.mock import AsyncMock, Mock

from app.tools.local_interpreter import LocalCodeInterpreter
from app.tools.notebook_serializer import NotebookSerializer


def interpreter_with_marks(tmp_path, monkeypatch, marks):
    monkeypatch.setattr(
        "app.tools.local_interpreter.task_bus.publish_message", AsyncMock()
    )
    interpreter = LocalCodeInterpreter(
        "tool-output", str(tmp_path), NotebookSerializer(str(tmp_path))
    )
    interpreter._run_raw = Mock(return_value=marks)
    interpreter._push_to_websocket = AsyncMock()
    return interpreter


def test_short_script_middle_is_visible_to_model(tmp_path, monkeypatch):
    script = "\n".join(
        f"line {i:03}: value = {i}  # inspected source" for i in range(90)
    )
    interpreter = interpreter_with_marks(tmp_path, monkeypatch, [("stdout", script)])
    text, failed, error = asyncio.run(interpreter.execute_code("print('inspection')"))
    assert text == "[stdout]\n" + script
    assert not failed and not error
    assert "line 045" in text


def test_multiple_outputs_share_one_context_cap_and_full_ui_evidence(
    tmp_path, monkeypatch
):
    outputs = ["first\n" + "a" * 6000, "b" * 6000 + "\nlast"]
    interpreter = interpreter_with_marks(
        tmp_path, monkeypatch, [("stdout", value) for value in outputs]
    )
    text, failed, _ = asyncio.run(interpreter.execute_code("print('inspection')"))
    assert not failed
    assert len(text) < 8050 and "内容已截断" in text
    assert text.startswith("[stdout]\nfirst") and text.endswith("last")
    displayed = interpreter._push_to_websocket.await_args.args[0]
    assert [item.msg for item in displayed] == outputs
