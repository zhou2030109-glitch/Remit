"""Production-bundle UI benchmark server. Local synthetic fixtures, no provider calls."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18005)
    parser.add_argument(
        "--dist-dir",
        type=Path,
        help="Saved production distribution for paired measurements",
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    data = args.data_dir.resolve()
    if not data.is_relative_to(repo / "logs/optimization"):
        raise SystemExit("Benchmark data must remain under logs/optimization")
    data.mkdir(parents=True, exist_ok=True)
    os.chdir(data)
    sys.path.insert(0, str(repo / "backend"))
    os.environ["REMIT_USER_CONFIG_PATH"] = str(data / ".env.test-user")
    os.environ["REMIT_MESSAGES_DIR"] = str(data / "logs/messages")
    from app.services import writing_workspace as ws, team_state as team
    from app.core.workflow_checkpoint import WorkflowCheckpoint
    from app.schemas.request import Problem
    from app.core.llm import llm
    from app.core.llm.providers.base import BaseProvider
    from app.utils.log_util import logger

    class NoCalls(BaseProvider):
        async def send(self, request):
            raise RuntimeError("No model calls permitted in frontend benchmark")

    llm._resolve_provider = lambda *_: NoCalls()
    logger.remove()
    records = []
    paragraph = (
        "这是用于压力测量的固定长段落，保留原始观测、验证结果和局限。数据不代表真实用户分布。"
        * 15
    )
    header = r"\documentclass[UTF8]{ctexart}\begin{document}" + "\n"
    block = (
        "\\section{压力夹具}\n"
        + paragraph
        + "\n"
        + r"\[y_i=\beta_0+\beta_1 x_i,\quad L=\sum_i(y_i-\hat y_i)^2\]"
        + "\n"
    )
    source = (header + block * 100)[:49985] + "\n\\end{document}"
    for suffix in ["a", "b"]:
        task = data / "project/work_dir" / f"perf-{suffix}"
        task.mkdir(parents=True, exist_ok=True)
        if not (task / "workflow_state.json").exists():
            checkpoint = WorkflowCheckpoint(task)
            state = checkpoint.initialize(
                Problem(task_id=task.name, ques_all="性能夹具，不是科学成果")
            )
            state.update(
                status="completed",
                ques_count=0,
                questions={"title": f"压力测试 {suffix.upper()}"},
                solution_results={},
            )
            checkpoint.save(state)
            ws.write_json(
                task / ".project.json",
                {"title": f"压力测试 {suffix.upper()}", "status": "completed"},
            )
            with team.database(task) as db:
                db.executemany(
                    "INSERT INTO events(event_key,at,role,kind,content,data) VALUES(?,?,?,?,?,?)",
                    [
                        (
                            f"perf:{i}",
                            team.now(),
                            "coder" if i % 3 else "coordinator",
                            ["reply", "activity", "tool", "reply"][i % 4],
                            f"夹具事件 {i:04d}\n"
                            + (
                                paragraph + "\n$$y=2x+1,\\quad E=\\sum_i e_i^2$$"
                                if i % 4 in [0, 3]
                                else (
                                    "执行输出\n```text\n"
                                    + ("row=6 residual=0\n" * 30)
                                    + "```"
                                    if i % 4 == 2
                                    else "正在核对固定文件"
                                )
                            ),
                            "{}",
                        )
                        for i in range(2000)
                    ],
                )
            paper = ws.ensure_workspace(task)
            ws.sync_results(task, state)
            (paper / "main.tex").write_text(source, encoding="utf-8")
            pdf = repo / "docs/optimization/chapter-reviewed-report.pdf"
            revision = hashlib.sha256(pdf.read_bytes()).hexdigest()
            (paper / ".pdf").mkdir()
            shutil.copy2(pdf, paper / ".pdf" / f"{revision}.pdf")
            shutil.copy2(pdf, paper / "preview.pdf")
            ws.write_json(
                paper / "compile.json",
                {
                    "status": "completed",
                    "pdf_revision": revision,
                    "revision": revision,
                    "page_count": 5,
                    "mode": "short_report",
                    "pdf_mode": "short_report",
                    "fixture_note": "Existing real five-page PDF used only for preview stress; not compiled from 50k fixture.",
                },
            )
        records.append(
            {
                "task_id": task.name,
                "events": len(team.events(task, limit=3000)),
                "source_chars": len(
                    (task / "paper/main.tex").read_text(encoding="utf-8")
                ),
            }
        )
    dist = args.dist_dir.resolve() if args.dist_dir else repo / "frontend/dist"
    manifest = {
        str(p.relative_to(dist)).replace("\\", "/"): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(dist.rglob("*"))
        if p.is_file()
    }
    ws.write_json(
        data / "fixture.json",
        {
            "projects": records,
            "production_manifest": manifest,
            "metrics_method": "Input/click/change/scroll event timestamp to second requestAnimationFrame; upper estimate of next paint, not browser EventTiming or network load completion. No artificial CPU/network throttle. Instrumentation overhead included.",
        },
    )
    from app.main import app
    from fastapi.responses import Response, JSONResponse, FileResponse

    script = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")

    @app.middleware("http")
    async def measurement(request, call_next):
        if request.url.path == "/__perf.js":
            return Response(script, media_type="application/javascript")
        if request.url.path == "/__perf/report" and request.method == "POST":
            value = await request.json()
            identifier = str(value.get("id", ""))
            if not identifier.isalnum() or len(identifier) > 40:
                return JSONResponse({"error": "invalid id"}, status_code=400)
            ws.write_json(data / f"measurement-{identifier}.json", value)
            return JSONResponse({"saved": True})
        if request.method == "GET" and (
            request.url.path == "/"
            or request.url.path.startswith(("/project/", "/home", "/writing/"))
        ):
            return Response(
                (dist / "index.html")
                .read_text(encoding="utf-8")
                .replace("<head>", '<head><script src="/__perf.js"></script>'),
                media_type="text/html",
            )
        asset = (dist / request.url.path.lstrip("/")).resolve()
        if request.method == "GET" and asset.is_relative_to(dist) and asset.is_file():
            return FileResponse(asset)
        return await call_next(request)

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
