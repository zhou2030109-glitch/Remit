"""Runtime state guards must not disappear under optimized Python."""

from __future__ import annotations

import unittest

from app.core.workflow import RemitWorkFlow


class WorkflowRuntimeGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_node_requires_checkpoint(self) -> None:
        workflow = RemitWorkFlow()

        with self.assertRaisesRegex(RuntimeError, "checkpoint is not initialized"):
            await workflow._start_node({}, "coordinator")


if __name__ == "__main__":
    unittest.main()
