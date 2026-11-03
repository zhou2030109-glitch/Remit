"""真实编译停止、取消传播和上一版 PDF 保留。"""

import asyncio
import shutil
import time
import json
import subprocess
import sys

import httpx
import pymupdf
import pytest
import psutil
from fastapi import FastAPI

from app.config.setting import settings
from app.schemas.enums import CompTemplate
from app.routers import writing_router
from app.services import writing_workspace as workspace
from app.utils.paper_polish import PaperRenderError, render_paper_deliverables


@pytest.mark.skipif(not shutil.which("xelatex"), reason="requires XeLaTeX")
@pytest.mark.parametrize("stop_method", ["endpoint", "shutdown"])
def test_manual_compile_can_stop_and_retains_previous_pdf(
    tmp_path, monkeypatch, stop_method
):
    monkeypatch.setattr(writing_router, "_resolve_task_directory", lambda _: tmp_path)
    monkeypatch.setattr("app.utils.common_utils.get_work_dir", lambda _: str(tmp_path))
    monkeypatch.setattr(settings, "LATEX_COMPILE_TIMEOUT_SECONDS", 3)
    writing_router._locks.clear()
    writing_router._compiling.clear()
    root = workspace.ensure_workspace(tmp_path)
    # 有限循环用于留出停止窗口，编译器另有 3 秒硬超时。
    (root / "main.tex").write_text(
        r"\documentclass{article}\begin{document}\newwrite\signal"
        r"\immediate\openout\signal=started.txt\immediate\write\signal{ready}"
        r"\immediate\closeout\signal\newcount\counter\counter=0"
        r"\loop\advance\counter by 1\ifnum\counter<100000000\repeat\end{document}"
    )
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Previous valid PDF")
        pdf.save(root / "preview.pdf")
    previous = (root / "preview.pdf").read_bytes()
    workspace.write_json(
        root / "compile.json", {"pdf_revision": "previous", "page_count": 1}
    )
    app = FastAPI()
    app.include_router(writing_router.router)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            job = asyncio.create_task(client.post("/api/writing/fixture/compile"))
            try:
                for _ in range(200):
                    if list((root / ".build").glob("*/started.txt")):
                        break
                    await asyncio.sleep(0.02)
                else:
                    raise AssertionError("真实编译器未进入测试文档")
                start = time.monotonic()
                if stop_method == "endpoint":
                    response = await client.post("/api/writing/fixture/cancel")
                    assert response.json()["status"] == "stopping"
                    await client.post("/api/writing/fixture/cancel")
                    result = (await asyncio.wait_for(job, 5)).json()
                else:
                    await writing_router.shutdown_writers()
                    with pytest.raises(asyncio.CancelledError):
                        await job
                    result = workspace.read_json(root / "compile.json")
                assert result["status"] == "cancelled"
                from app.services.work_timing import summary

                assert summary(tmp_path)["categories"]["latex"]["cancelled"] == 1
                assert time.monotonic() - start < 2
                assert result["pdf_revision"] == "previous"
                assert not writing_router._compiling
                assert (root / "preview.pdf").read_bytes() == previous
                (root / "main.tex").write_text(
                    r"\documentclass{article}\begin{document}Recovered compilation\end{document}"
                )
                recovered = (await client.post("/api/writing/fixture/compile")).json()
                assert recovered["status"] == "completed"
                with pymupdf.open(root / "preview.pdf") as pdf:
                    assert "Recovered compilation" in pdf[0].get_text()
            finally:
                if not job.done():
                    job.cancel()
                await asyncio.gather(job, return_exceptions=True)

    asyncio.run(exercise())


def test_failed_replacement_preserves_final_source_pdf_and_report(
    tmp_path, monkeypatch
):
    from app.utils import paper_polish

    saved = {
        "res.tex": b"previous source",
        "res.pdf": b"previous verified pdf",
        "paper_delivery_report.json": b'{"status":"pass"}',
    }
    for name, content in saved.items():
        (tmp_path / name).write_bytes(content)

    def failed(*args, **kwargs):
        raise PaperRenderError("compiler failed fixture")

    monkeypatch.setattr(paper_polish, "_compile_latex", failed)
    with pytest.raises(PaperRenderError, match="compiler failed"):
        render_paper_deliverables(
            "# 新论文\n\n保留上次结果。", tmp_path, CompTemplate.CHINA
        )
    for name, content in saved.items():
        assert (tmp_path / name).read_bytes() == content


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_owned_compiler_children_exit_and_unrelated_process_survives(tmp_path, mode):
    from app.services.async_io import run_cancellable
    from app.utils.tex_process import _run_owned

    script = tmp_path / "compiler_fixture.py"
    script.write_text(
        "import subprocess,sys,time,json,os\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
        "Path('owned.tmp').write_text(json.dumps([os.getpid(),child.pid]))\n"
        "Path('owned.tmp').replace('owned.json')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    control = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(30)"], creationflags=flags
    )
    owned = []

    async def exercise():
        def run():
            return _run_owned(
                [sys.executable, str(script)],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=1.5 if mode == "timeout" else 10,
            )

        job = asyncio.create_task(run_cancellable(run))
        try:
            for _ in range(150):
                if (tmp_path / "owned.json").is_file():
                    owned.extend(
                        psutil.Process(pid)
                        for pid in json.loads((tmp_path / "owned.json").read_text())
                    )
                    break
                await asyncio.sleep(0.02)
            assert len(owned) == 2
            if mode == "cancel":
                # Give the owner one polling cycle to observe its child.
                await asyncio.sleep(0.15)
                job.cancel()
                job.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await job
            else:
                with pytest.raises(subprocess.TimeoutExpired):
                    await job
            assert all(not process.is_running() for process in owned)
            assert control.poll() is None
        finally:
            if not job.done():
                job.cancel()
            await asyncio.gather(job, return_exceptions=True)

    try:
        asyncio.run(exercise())
    finally:
        for process in owned:
            if process.is_running():
                process.kill()
        control.kill()
        control.wait(timeout=5)


def test_cancelled_scope_does_not_launch_bundled_pdf_driver(tmp_path, monkeypatch):
    from threading import Event
    from app.services.async_io import blocking_cancel, WorkCancelled
    from app.utils import tex_process

    signal = Event()
    calls = []

    def completed(command, **kwargs):
        calls.append(command)
        signal.set()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(tex_process, "_run_owned", completed)
    token = blocking_cancel.set(signal)
    try:
        with pytest.raises(WorkCancelled):
            tex_process.run_xelatex(
                ["xelatex", "main.tex"],
                pdf_path=tmp_path / "main.pdf",
                env={"REMIT_BUNDLED_TEX": "1"},
            )
        assert len(calls) == 1
    finally:
        blocking_cancel.reset(token)


def test_cancellation_does_not_hide_cleanup_failure():
    from threading import Event
    from app.services.async_io import run_cancellable

    ready = Event()
    failed = Event()

    def worker():
        ready.set()
        failed.wait(3)
        raise RuntimeError("fixture process cleanup failed")

    async def exercise():
        job = asyncio.create_task(run_cancellable(worker))
        while not ready.is_set():
            await asyncio.sleep(0.01)
        job.cancel()
        await asyncio.sleep(0.01)
        failed.set()
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await job

    asyncio.run(exercise())


def test_bundled_compiler_and_pdf_driver_share_one_timeout(tmp_path, monkeypatch):
    from app.utils import tex_process

    calls = []

    def completed(command, **kwargs):
        calls.append(kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(tex_process, "_run_owned", completed)
    monkeypatch.setattr(
        tex_process.shutil, "which", lambda *args, **kwargs: "xdvipdfmx"
    )
    clock = iter([0, 7])
    monkeypatch.setattr(tex_process.time, "monotonic", lambda: next(clock))
    tex_process.run_xelatex(
        ["xelatex", "main.tex"],
        pdf_path=tmp_path / "main.pdf",
        timeout=10,
        env={"REMIT_BUNDLED_TEX": "1"},
    )
    assert calls == [10, 3]


def test_previous_final_approval_cannot_validate_failed_recompile(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock, MagicMock
    from app.core import workflow as module
    from app.core import workflow_writing
    from app.core.workflow_checkpoint import WorkflowCheckpoint
    from app.schemas.request import Problem

    flow = module.RemitWorkFlow()
    flow.task_id = "fixture"
    flow.work_dir = str(tmp_path)
    flow.checkpoint = WorkflowCheckpoint(tmp_path)
    state = flow.checkpoint.initialize(Problem(task_id="fixture"))
    state["workflow_features"].remove("separate_writing")
    state["approved_nodes"] = ["finalize"]
    state["completed_nodes"] = ["finalize"]
    monkeypatch.setattr(flow, "_start_node", AsyncMock())
    monkeypatch.setattr(flow, "_complete_node", AsyncMock())
    monkeypatch.setattr(flow, "_review_and_polish_paper", AsyncMock(return_value={}))
    monkeypatch.setattr(workflow_writing, "polish_markdown", lambda *args: "fixture")
    monkeypatch.setattr(module.redis_manager, "publish_message", AsyncMock())

    def invalid(*args, **kwargs):
        raise workflow_writing.PaperRenderError("new compilation failed")

    monkeypatch.setattr(workflow_writing, "validate_final_paper", invalid)

    async def exercise():
        with pytest.raises(
            (module.WorkflowApprovalRequired, module.WorkflowPausedForReview)
        ):
            await flow._finalize_node(state, MagicMock(), MagicMock(), MagicMock())
        flow._complete_node.assert_not_awaited()
        restored = flow.checkpoint.load()
        assert restored["status"] == "awaiting_approval"
        assert "finalize" not in restored["completed_nodes"]
        assert "finalize" not in restored["approved_nodes"]

    asyncio.run(exercise())
