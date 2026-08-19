"""Project Copilot 必须真正调用建模模型并返回持久化回复。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core.llm.types import StandardResponse
from app.routers import common_router


class _FakeModelerLLM:
    model = "modeler-test"

    def __init__(self) -> None:
        self.history = []

    async def chat(self, *, history, **kwargs):
        self.history = history
        return StandardResponse(content="当前只有 EDA 证据，尚无可比较的 OOF 指标。")


class TaskCopilotTests(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_action_gets_modeler_response(self) -> None:
        self.assertTrue(hasattr(common_router, "post_task_copilot"))
        llm = _FakeModelerLLM()
        factory = unittest.mock.MagicMock()
        factory.get_all_llms.return_value = (None, llm, None, None)

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(common_router, "TASK_WORK_DIR_ROOT", Path(tmp)),
                patch.object(
                    common_router.redis_manager,
                    "task_exists",
                    new=AsyncMock(return_value=True),
                ),
                patch.object(
                    common_router.redis_manager,
                    "load_task_messages",
                    new=AsyncMock(return_value=[]),
                ),
                patch.object(
                    common_router.redis_manager,
                    "publish_message",
                    new=AsyncMock(),
                ) as publish,
                patch.object(common_router, "LLMFactory", return_value=factory),
            ):
                response = await common_router.post_task_copilot(
                    "copilot-task",
                    common_router.TaskCopilotRequest(action="分析当前结果"),
                )

        self.assertEqual(response.response.content, "当前只有 EDA 证据，尚无可比较的 OOF 指标。")
        self.assertIn("只依据真实落盘证据", llm.history[0]["content"])
        self.assertEqual(publish.await_count, 2)


if __name__ == "__main__":
    unittest.main()
