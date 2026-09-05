"""代码执行复杂度、超时和事件循环存活性回归测试。"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.tools.execution_guard import assess_code_execution
from app.tools.local_interpreter import LocalCodeInterpreter
from app.tools.notebook_serializer import NotebookSerializer


class ExecutionComplexityGuardTests(unittest.TestCase):
    def test_rejects_metaheuristic_with_quadratic_check_in_inner_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            solver = Path(tmp) / "solver.m"
            solver.write_text(
                """
seeds=1:10; n=300; levels=35; moves_per_temp=15;
for seed=seeds
  for level=1:levels
    for move=1:round(moves_per_temp*n)
      [layout, score] = btSA_step(layout);
      score = indVerify(layout, n);
    end
  end
end
function score=indVerify(layout,n)
score=0;
for i=1:n-1
  for j=i+1:n
    score=score+overlaps(layout,i,j);
  end
end
end
""".strip(),
                encoding="utf-8",
            )

            assessment = assess_code_execution(
                "run('solver.m');", language="matlab", work_dir=tmp
            )

        self.assertFalse(assessment.allowed)
        self.assertIn("复杂度", assessment.reason)
        self.assertIn("solver.m", assessment.reason)

    def test_allows_small_bounded_vectorized_code(self) -> None:
        assessment = assess_code_execution(
            "x = 1:100; y = x.^2; disp(mean(y));",
            language="matlab",
            work_dir=".",
        )

        self.assertTrue(assessment.allowed, assessment.reason)


class LocalExecutionWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_does_not_block_event_loop_and_recovers_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            interpreter = LocalCodeInterpreter(
                task_id="python-timeout-test",
                work_dir=tmp,
                notebook_serializer=NotebookSerializer(work_dir=tmp),
                timeout=0.05,
            )
            interpreter.kc = MagicMock()
            interpreter.km = MagicMock()
            loop = asyncio.get_running_loop()
            loop_thread = threading.get_ident()
            worker_started = asyncio.Event()
            loop_progressed = asyncio.Event()
            release_worker = threading.Event()
            worker_finished = threading.Event()
            worker_threads: list[int] = []
            execution_order: list[str] = []

            def blocked_execution(_code: str) -> list:
                worker_threads.append(threading.get_ident())
                execution_order.append("worker_started")
                loop.call_soon_threadsafe(worker_started.set)
                try:
                    self.assertNotEqual(threading.get_ident(), loop_thread)
                    if not release_worker.wait(10):
                        raise TimeoutError("测试未释放执行线程")
                    return []
                finally:
                    execution_order.append("worker_finished")
                    worker_finished.set()

            async def recover(worker: asyncio.Task) -> None:
                # 超时可以先于线程调度发生；等待握手，避免 CI 负载影响顺序。
                await asyncio.wait_for(loop_progressed.wait(), timeout=10)
                self.assertFalse(worker_finished.is_set())
                execution_order.append("recovery")
                release_worker.set()
                await asyncio.wait_for(asyncio.shield(worker), timeout=10)

            interpreter._run_raw = MagicMock(side_effect=blocked_execution)
            interpreter._recover_kernel_after_timeout = AsyncMock(side_effect=recover)
            interpreter._push_to_websocket = AsyncMock()

            with (
                patch(
                    "app.tools.local_interpreter.redis_manager.publish_message",
                    new=AsyncMock(),
                ),
                patch(
                    "app.tools.local_interpreter.settings."
                    "CODE_EXECUTION_HEARTBEAT_SECONDS",
                    0.01,
                    create=True,
                ),
            ):
                execution = asyncio.create_task(interpreter.execute_code("value = 1"))
                try:
                    await asyncio.wait_for(worker_started.wait(), timeout=10)
                    # 线程仍在阻塞时，本协程已被事件循环调度，证明循环未被占住。
                    self.assertEqual(len(worker_threads), 1)
                    self.assertNotEqual(worker_threads[0], loop_thread)
                    self.assertFalse(worker_finished.is_set())
                    execution_order.append("loop_progressed")
                    loop_progressed.set()
                    output, failed, error = await asyncio.wait_for(execution, timeout=10)
                finally:
                    release_worker.set()
                    loop_progressed.set()
                    if not execution.done():
                        execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)

            self.assertTrue(failed)
            self.assertIn("执行超过", output)
            self.assertEqual(error, output)
            self.assertEqual(
                execution_order,
                ["worker_started", "loop_progressed", "recovery", "worker_finished"],
            )
            interpreter._recover_kernel_after_timeout.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
