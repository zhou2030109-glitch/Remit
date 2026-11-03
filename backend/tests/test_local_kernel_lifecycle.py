"""Real kernel failures must not leave processes or misattribute saved results."""

import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import psutil
import pytest

from app.tools.local_interpreter import LocalCodeInterpreter
from app.tools.notebook_serializer import NotebookSerializer


def make_interpreter(path, monkeypatch, timeout=15):
    monkeypatch.setattr(
        "app.tools.local_interpreter.redis_manager.publish_message", AsyncMock()
    )
    return LocalCodeInterpreter(
        "kernel-lifecycle", str(path), NotebookSerializer(str(path)), timeout=timeout
    )


def test_failed_initialization_releases_kernel(tmp_path, monkeypatch):
    (tmp_path / "broken.ttf").write_bytes(b"invalid font fixture")
    interpreter = make_interpreter(tmp_path, monkeypatch)

    async def run():
        try:
            with pytest.raises(RuntimeError, match="初始化失败"):
                await interpreter.initialize()
            assert interpreter.km is None or not interpreter.km.is_alive()
        finally:
            await interpreter.cleanup()

    asyncio.run(run())


def test_old_request_idle_cannot_finish_new_request(tmp_path, monkeypatch):
    interpreter = make_interpreter(tmp_path, monkeypatch)

    async def run():
        try:
            await interpreter.initialize()
            interpreter.kc.execute("import time; time.sleep(0.2); print('OLD_REQUEST')")
            marks = await asyncio.to_thread(
                interpreter._run_raw,
                "from pathlib import Path\nPath('new-result.txt').write_text('42')\nprint('NEW_REQUEST')",
            )
            assert (tmp_path / "new-result.txt").read_text() == "42"
            assert any("NEW_REQUEST" in value for _, value in marks)
            assert all("OLD_REQUEST" not in value for _, value in marks)
        finally:
            await interpreter.cleanup()

    asyncio.run(run())


@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_execution_stop_reaps_owned_children(tmp_path, monkeypatch, stop):
    interpreter = make_interpreter(
        tmp_path, monkeypatch, timeout=2 if stop == "timeout" else 30
    )
    children = []

    async def run():
        control = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(45)"]
        )
        try:
            await interpreter.initialize()
            interpreter.add_section("lifecycle")
            code = "import subprocess, sys, time\nfrom pathlib import Path\nchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(45)'])\nPath('child.pid').write_text(str(child.pid))\ntime.sleep(40)"
            pending = asyncio.create_task(interpreter.execute_code(code))
            for _ in range(100):
                if (tmp_path / "child.pid").exists():
                    break
                await asyncio.sleep(0.05)
            child = psutil.Process(int((tmp_path / "child.pid").read_text()))
            children.append(child)
            if stop == "cancel":
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                _, failed, error = await asyncio.wait_for(pending, 30)
                assert failed and "执行超过" in error
            assert not child.is_running(), (
                "Stopped execution left its child process running"
            )
            assert control.poll() is None, (
                "Stopping one kernel affected another process"
            )
            if stop == "timeout":
                output, failed, error = await interpreter.execute_code(
                    "from pathlib import Path\nPath('recovered.txt').write_text('42')\nprint(42)"
                )
                assert not failed, error
                assert (
                    "42" in output and (tmp_path / "recovered.txt").read_text() == "42"
                )
        finally:
            await interpreter.cleanup()
            control.kill()
            control.wait(timeout=5)
            for child in children:
                if child.is_running():
                    child.kill()
                    child.wait(timeout=5)

    asyncio.run(run())


def test_cancel_during_initialization_waits_for_cleanup(tmp_path, monkeypatch):
    interpreter = make_interpreter(tmp_path, monkeypatch)
    started, release = threading.Event(), threading.Event()
    original = interpreter._start_kernel
    processes = []

    def delayed_start():
        original()
        processes.append(psutil.Process(interpreter.km.provisioner.pid))
        started.set()
        assert release.wait(10)

    monkeypatch.setattr(interpreter, "_start_kernel", delayed_start)

    async def run():
        pending = asyncio.create_task(interpreter.initialize())
        try:
            assert await asyncio.to_thread(started.wait, 10)
            pending.cancel()
            await asyncio.sleep(0.05)
            pending.cancel()  # 重复点击停止也须等实际资源回收。
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 10)
            assert not processes[0].is_running()
            assert interpreter.km is None
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await interpreter.cleanup()

    asyncio.run(run())


def test_uses_own_python_despite_foreign_kernel_spec(tmp_path, monkeypatch):
    spec = tmp_path / "jupyter" / "kernels" / "python3"
    spec.mkdir(parents=True)
    (spec / "kernel.json").write_text(
        json.dumps(
            {
                "argv": ["missing-python-runtime", "-f", "{connection_file}"],
                "display_name": "Foreign Python",
                "language": "python",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JUPYTER_PATH", str(spec.parents[1]))
    interpreter = make_interpreter(tmp_path, monkeypatch)

    async def run():
        try:
            await interpreter.initialize()
            marks = await asyncio.to_thread(
                interpreter._run_raw,
                "import sys\nfrom pathlib import Path\nPath('runtime.txt').write_text(sys.executable, encoding='utf-8')\nprint('RUNTIME_VERIFIED')",
            )
            assert (
                Path((tmp_path / "runtime.txt").read_text(encoding="utf-8")).resolve()
                == Path(sys.executable).resolve()
            )
            assert any("RUNTIME_VERIFIED" in value for _, value in marks)
        finally:
            await interpreter.cleanup()

    asyncio.run(run())
