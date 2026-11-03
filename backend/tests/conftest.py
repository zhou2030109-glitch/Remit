"""单元测试隔离本机任务档案与任务总线，禁止污染正在使用的工作区。"""

import os

os.environ["REMIT_FILE_LOG"] = "0"

import pytest  # noqa: E402

from app.services.message_archive import MessageArchive
from app.services.task_bus import task_bus
from app.routers import modeling_router


@pytest.fixture(autouse=True)
def isolated_message_archive(tmp_path, monkeypatch):
    """每个测试获得独立持久化位置，需要独立总线的测试自行创建 TaskBus。"""
    directory = tmp_path / "messages"
    monkeypatch.setattr(task_bus, "messages_dir", directory)
    monkeypatch.setattr(task_bus, "archive", MessageArchive(directory))
    monkeypatch.setattr(task_bus, "_subscriptions", {})
    monkeypatch.setattr(task_bus, "_user_notes", {})
    monkeypatch.setattr(task_bus, "_cancellation_requests", set())
    monkeypatch.setattr(task_bus, "_message_locks", {})
    monkeypatch.setattr(task_bus, "_deleting_tasks", set())
    modeling_router._pending_cancellations.clear()
    modeling_router._scheduled_tasks.clear()
    modeling_router._active_tasks.clear()
    yield
    modeling_router._pending_cancellations.clear()
    modeling_router._scheduled_tasks.clear()
    modeling_router._active_tasks.clear()


@pytest.fixture
def blocked_pandoc(tmp_path, monkeypatch):
    """Let real Pandoc enter a bounded Lua filter before cancelling it."""
    import json
    import subprocess
    import psutil
    from app.utils import owned_process, pandoc_process

    filter_root = tmp_path / "中文 工作目录"
    filter_root.mkdir()
    marker = filter_root / "conversion-started.txt"
    filter_path = filter_root / "waiting filter.lua"
    filter_path.write_text(
        "function Pandoc(doc)\nlocal f=assert(io.open("
        + json.dumps(marker.name)
        + ",'w')); f:write('ready'); f:close()\n"
        "local started=os.clock(); while os.clock()-started < 15 do end\n"
        "return doc\nend\n",
        encoding="utf-8",
    )
    original = pandoc_process.run_owned
    original_popen = subprocess.Popen
    processes = []

    def launch(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(psutil.Process(process.pid))
        return process

    def run(command, **kwargs):
        kwargs["cwd"] = filter_root
        return original([*command, f"--lua-filter={filter_path}"], **kwargs)

    # Resolve the executable before tracking only conversion subprocesses.
    pandoc_process.pypandoc.get_pandoc_path()
    monkeypatch.setattr(owned_process.subprocess, "Popen", launch)
    monkeypatch.setattr(pandoc_process, "run_owned", run)
    yield marker, processes
    for process in processes:
        if process.is_running():
            process.kill()
    psutil.wait_procs(processes, timeout=3)
