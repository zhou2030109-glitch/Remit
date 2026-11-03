"""Opt-in real model / CoderAgent / kernel smoke test on synthetic local files.

Never runs as part of pytest. Requires --allow-live and an empty output directory.
Credentials are read by the application; reports never include keys or endpoints.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import io
import os
import shutil
from pathlib import Path
import sys
import time
import zipfile


def verify_outputs(work: Path) -> dict:
    """Recompute results from disk independently of the generated program."""
    with (work / "result.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 6, "Expected six result rows"
    for index, row in enumerate(rows):
        assert float(row["x"]) == index and float(row["y"]) == 2 * index + 1
    mse = (
        sum(
            (float(row["predicted"]) - (2 * index + 1)) ** 2
            for index, row in enumerate(rows)
        )
        / 6
    )
    assert mse < 1e-20, "Prediction values do not match input data"
    metrics = json.loads((work / "metrics.json").read_text(encoding="utf-8-sig"))
    assert metrics["n"] == 6
    assert abs(float(metrics["slope"]) - 2) < 1e-10
    assert abs(float(metrics["intercept"]) - 1) < 1e-10
    assert abs(float(metrics["mse"]) - mse) < 1e-20
    assert (work / "fit.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (work / "fit.png").stat().st_size > 1000
    assert (work / "solver.py").stat().st_size > 20
    return {
        "n": 6,
        "slope": metrics["slope"],
        "intercept": metrics["intercept"],
        "independent_mse": mse,
    }


async def run_case(data: Path, *, generate_report: bool) -> dict:
    from app.config.setting import settings

    settings.LLM_STAGE_CALL_LIMIT = 10
    settings.LLM_STAGE_API_SECONDS = 600
    settings.API_HARD_TIMEOUT_SECONDS = min(settings.API_HARD_TIMEOUT_SECONDS, 120)
    # Test-local message archive, using the actual TaskBus and SQLite implementation.
    import app.services.task_bus as bus_module

    bus_module.task_bus = bus_module.TaskBus(data / "logs/messages")
    from app.utils.log_util import logger

    logger.remove()
    from app.core.agents.coder_agent import CoderAgent
    from app.core.llm.llm_factory import LLMFactory
    from app.services import api_probe, call_ledger
    from app.services.model_capabilities import role_config
    from app.tools.local_interpreter import LocalCodeInterpreter
    from app.tools.notebook_serializer import NotebookSerializer
    from app.utils.common_utils import create_work_dir

    task_id = "live-linear-case"
    work = Path(create_work_dir(task_id)).resolve()
    source = "x,y\n0,1\n1,3\n2,5\n3,7\n4,9\n5,11\n"
    (work / "observed.csv").write_text(source, encoding="utf-8")
    report = {
        "status": "running",
        "case": "six-row synthetic linear regression",
        "full_workflow_tested": False,
        "model_configuration": {},
        "probe_calls": 0,
        "probe_usage": "unknown",
        "cost": None,
        "currency": None,
    }
    config = role_config("coder")
    report["model_configuration"] = {
        key: config[key]
        for key in (
            "model_id",
            "api_type",
            "context_window",
            "max_tokens",
            "reasoning_effort",
        )
    }
    report_path = data / "live-result.json"
    started = time.monotonic()
    interpreter = None

    def save(stage):
        report["stage"] = stage
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["usage"] = call_ledger.summary(work)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {"stage": stage, "elapsed_seconds": report["elapsed_seconds"]},
                ensure_ascii=False,
            ),
            flush=True,
        )

    try:
        save("capability_probe")
        profile = await api_probe.check_capabilities(config, needs_tools=True)
        report["capabilities"] = profile
        report["probe_calls"] = profile["calls"]
        if any(profile[key] != "supported" for key in ("text", "tools", "tool_result")):
            raise RuntimeError("required_capabilities_not_verified")
        save("real_coder_and_kernel")
        coder_model = LLMFactory(task_id).get_modeling_llms()[2]
        coder_model.capabilities = profile
        interpreter = LocalCodeInterpreter(
            task_id, str(work), NotebookSerializer(str(work)), timeout=45
        )
        await interpreter.initialize()
        coder = CoderAgent(
            task_id,
            coder_model,
            str(work),
            max_chat_turns=6,
            max_retries=2,
            max_code_executions=4,
            code_interpreter=interpreter,
            context_window=coder_model.context_window,
        )
        prompt = """这是已授权的真实模型软件验收案例，输入为工作目录里的 observed.csv（六行合成数据），不是现实科研证据。
仅在当前目录读写文件，不访问网络或目录外文件，不安装依赖。请实际调用 execute_code 读取 CSV、用带截距最小二乘拟合 y 对 x，并保存：
1. solver.py：可独立重新运行的完整 Python 程序；请在本次内核实际执行该程序。
2. result.csv：六行，列必须为 x,y,predicted，按输入顺序。
3. metrics.json：n,slope,intercept,mse；全部来自实际计算，mse 是训练集均方误差。
4. fit.png：中文标题、横纵轴名称、观测点和拟合直线图例，能正常显示中文。
数据就在 observed.csv，必须读取，不得硬编码拟合系数或结果。不要把训练误差解释为泛化能力。一次执行尽量生成全部文件并回读检查，不需要寻找其他数据或扩展模型。"""

        def completed():
            try:
                verify_outputs(work)
                return True
            except (OSError, ValueError, KeyError, AssertionError):
                return False

        result = await coder.run(
            prompt,
            "固定合成数据真实试跑",
            required_files=("solver.py", "result.csv", "metrics.json", "fit.png"),
            completion_check=completed,
        )
        report["coder_turns"] = coder.current_chat_turns
        report["code_executions"] = coder.current_code_executions
        report["independent_validation"] = verify_outputs(work)
        (work / "coder-result.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        save("independent_validation_passed")
        if generate_report:
            writer = LLMFactory(task_id).get_writer_llm()
            response = await writer.chat(
                history=[
                    {
                        "role": "user",
                        "content": "请写一份约400字中文 Markdown 验收短报告。仅用下面的真实复算结果和文件名，包含方法、结果、证据文件、局限。数据仅有六行且为合成数据，无独立测试集，不能推断泛化能力，也不能编造性能或真实世界结论。\n"
                        + json.dumps(
                            report["independent_validation"], ensure_ascii=False
                        )
                        + "\n证据：observed.csv、solver.py、result.csv、metrics.json、fit.png、notebook.ipynb。",
                    }
                ],
                max_tokens=2048,
                max_retries=2,
                agent_name="WriterAgent",
                publish=False,
            )
            if not response.content or response.finish_reason in {
                "length",
                "max_tokens",
            }:
                raise RuntimeError("short_report_missing_or_truncated")
            (work / "report.md").write_text(response.content, encoding="utf-8")
            report["short_report"] = "report.md"
        report["status"] = "passed"
        save("completed")
    except Exception as error:
        report["status"] = "failed"
        report["error"] = {
            "type": type(error).__name__,
            "status_code": getattr(error, "status_code", None),
        }
        save("failed")
    finally:
        if interpreter is not None:
            await interpreter.cleanup()
        await bus_module.task_bus.close()
    return report


async def run_writing_case(data: Path) -> dict:
    """Exercise the real writing API on independently rechecked synthetic files.

    The completed modeling checkpoint is a fixture, not a Coordinator/Modeler
    acceptance test. No model client, section validator or compiler is mocked.
    """
    os.environ["REMIT_MESSAGES_DIR"] = str(data / "logs/messages")
    from app.config.setting import settings

    settings.API_HARD_TIMEOUT_SECONDS = min(settings.API_HARD_TIMEOUT_SECONDS, 120)
    from app.utils.log_util import logger

    logger.remove()
    from fastapi import FastAPI
    import httpx
    import pymupdf
    from app.core.workflow_checkpoint import WorkflowCheckpoint
    from app.routers import writing_router
    from app.schemas.request import Problem
    from app.services import call_ledger, writing_workspace as ws
    from app.services.task_bus import task_bus
    from app.utils.common_utils import create_work_dir

    task_id = "live-short-report"
    work = Path(create_work_dir(task_id)).resolve()
    fixture = Path(__file__).resolve().parents[1] / "docs/optimization/live-case"
    for name in ("observed.csv", "result.csv", "metrics.json", "fit.png", "solver.py"):
        shutil.copy2(fixture / name, work / name)
    verified = verify_outputs(work)
    checkpoint = WorkflowCheckpoint(work)
    question = "用带截距最小二乘拟合六行合成数据；说明训练误差，不能推断泛化能力。"
    state = checkpoint.initialize(Problem(task_id=task_id, ques_all=question))
    state.update(
        status="completed",
        ques_count=1,
        questions={"background": "固定软件验收合成数据", "ques1": question},
        solution_results={
            "ques1": {
                "question_text": question,
                "execution_summary": json.dumps(verified, ensure_ascii=False),
                "grounding_values": list(verified.values()),
                "artifacts": [
                    "observed.csv",
                    "result.csv",
                    "metrics.json",
                    "fit.png",
                    "solver.py",
                ],
                "paper_ready_images": ["fit.png"],
                "model_review": {
                    "weaknesses": "六行合成数据、无独立测试集，不能推断真实世界泛化能力。"
                },
            }
        },
    )
    checkpoint.save(state)
    state_before = (work / "workflow_state.json").read_bytes()
    app = FastAPI()
    app.include_router(writing_router.router)
    started = time.monotonic()
    report = {
        "status": "running",
        "mode": "short_report",
        "model": settings.WRITER_MODEL,
        "modeling_checkpoint": "seeded_fixture",
        "full_workflow_tested": False,
        "independent_validation": verified,
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            url = f"/api/writing/{task_id}"
            current = (await client.get(url)).json()
            mode = await client.put(
                url + "/mode",
                json={"mode": "short_report", "version": current["mode_version"]},
            )
            mode.raise_for_status()
            response = await client.post(url + "/generate")
            response.raise_for_status()
            job = writing_router._generations[task_id]
            await asyncio.wait_for(asyncio.shield(job), timeout=600)
            result = (await client.get(url)).json()
            report["generation"] = result["generation"]
            report["compile"] = {
                key: result["compile"].get(key)
                for key in ("status", "page_count", "mode", "pdf_mode", "layout_review")
            }
            assert result["generation"]["status"] == "completed", "generation_failed"
            assert result["compile"]["status"] == "completed", "compile_failed"
            assert result["compile"]["pdf_mode"] == "short_report"
            assert (work / "workflow_state.json").read_bytes() == state_before
            with pymupdf.open(ws.paper_root(work) / "preview.pdf") as pdf:
                text = "".join(page.get_text() for page in pdf)
                assert "短报告" in text and "合成" in text
                assert "附录" not in text
                assert "泛化" in text or "测试集" in text
                assert sum(len(page.get_images()) for page in pdf) >= 1
                for index, page in enumerate(pdf):
                    page.get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2)).save(
                        data / f"page-{index + 1}.png"
                    )
            archive = await client.get(url + "/export")
            archive.raise_for_status()
            (data / "short-report.zip").write_bytes(archive.content)
            with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
                for name in ("observed.csv", "result.csv", "metrics.json", "solver.py"):
                    copies = [
                        path
                        for path in bundle.namelist()
                        if path.startswith("assets/") and path.endswith("/" + name)
                    ]
                    assert (
                        len(copies) == 1
                        and bundle.read(copies[0]) == (work / name).read_bytes()
                    )
            report["status"] = "passed"
    except Exception as error:
        report.update(
            status="failed",
            error={
                "type": type(error).__name__,
                "status_code": getattr(error, "status_code", None),
            },
        )
    finally:
        await writing_router.shutdown_writers()
        await task_bus.close()
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["usage"] = call_ledger.summary(work)
        (data / "live-writing-result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "elapsed_seconds": report["elapsed_seconds"],
                    "usage": report["usage"],
                }
            ),
            flush=True,
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--writing-only",
        action="store_true",
        help="Run real writing API on the saved synthetic case",
    )
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Real model calls require explicit --allow-live authorization")
    repo = Path(__file__).resolve().parents[1]
    data = args.output_dir.resolve()
    data.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(repo / "backend"))
    os.chdir(data)
    result = asyncio.run(
        run_writing_case(data)
        if args.writing_only
        else run_case(data, generate_report=True)
    )
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
