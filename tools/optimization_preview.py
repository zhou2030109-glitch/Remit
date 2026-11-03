"""隔离验收服务器；默认模拟供应商，--allow-live 显式启用现有真实模型配置。"""

import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18003)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Use real configured providers; requires user authorization",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    data = args.data_dir.resolve()
    if data == root or data == root / "backend" or data == Path.home():
        raise SystemExit("必须使用独立测试数据目录")
    data.mkdir(parents=True, exist_ok=True)
    os.chdir(data)
    sys.path.insert(0, str(root / "backend"))
    if args.allow_live:
        # Use the application's existing configuration; only local data and
        # message storage are isolated. No pre-completed workflow is seeded.
        os.environ["REMIT_MESSAGES_DIR"] = str(data / "logs/messages")
        os.environ["SERVER_HOST"] = f"http://127.0.0.1:{args.port}"
        import uvicorn
        from app.main import app
        from app.utils.log_util import logger

        # Inspect synthetic artifacts/checkpoints deliberately; do not dump
        # provider errors or full prompt bodies to the validation console.
        logger.remove()
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        return
    os.environ["REMIT_USER_CONFIG_PATH"] = str(data / ".env.test-user")
    os.environ["REMIT_MESSAGES_DIR"] = str(data / "logs" / "messages")
    os.environ["SERVER_HOST"] = f"http://127.0.0.1:{args.port}"
    os.environ["CODE_EXECUTION_BACKEND"] = "python"
    os.environ["PDF_VISION_ENABLED"] = "false"
    os.environ["MODEL_COUNCIL_ENABLED"] = "false"
    os.environ["MODEL_SHARED_CORE"] = "true"
    for role in (
        "COORDINATOR",
        "MODELER",
        "CODER",
        "WRITER",
        "VISION",
        "MODEL_SCOUT",
        "MODEL_CRITIC",
        "FALLBACK",
    ):
        os.environ[f"{role}_API_KEY"] = "local-protocol-fixture"
        os.environ[f"{role}_MODEL"] = "protocol-fixture"
        os.environ[f"{role}_API_TYPE"] = "openai-chat"
        os.environ[f"{role}_BASE_URL"] = "http://127.0.0.1:9"
    from app.core.llm.providers.base import BaseProvider
    from app.core.llm.types import StandardResponse
    from app.core.llm import llm
    from app.services import api_probe

    class FixtureProvider(BaseProvider):
        async def send(self, request):
            # 协议仿真不运行模型，错误注入用于验证真实产品的恢复界面。
            text = json.dumps(request.messages, ensure_ascii=False)
            if '"kind"' in text or "understanding" in text:
                return StandardResponse(
                    content=json.dumps(
                        {
                            "kind": "plan",
                            "title": "协议测试 · 固定线性数据",
                            "understanding": "协议仿真：拟合 y=2x+1；不是模型实际推理结果。",
                            "steps": ["核对六行数据", "计算并独立复算", "组织短报告"],
                            "questions": [],
                        },
                        ensure_ascii=False,
                    )
                )
            raise RuntimeError("协议仿真主动注入失败，已有成果保留。")

    llm._resolve_provider = lambda *_: FixtureProvider()
    api_probe.provider_for = lambda *_: FixtureProvider()
    from app.services.writing_workspace import (
        ensure_workspace,
        sync_results,
        write_json,
    )
    from app.services.team_state import record
    from app.core.workflow_checkpoint import WorkflowCheckpoint
    from app.schemas.request import Problem

    for identifier, status in (
        ("fixture-results", "completed"),
        ("fixture-failed", "failed"),
        ("fixture-running", "running"),
    ):
        task = data / "project/work_dir" / identifier
        task.mkdir(parents=True, exist_ok=True)
        if (task / "workflow_state.json").exists():
            continue
        (task / "result.csv").write_text(
            "x,actual,predicted\n0,1,1\n1,3,3\n2,5,5\n3,7,7\n4,9,9\n5,11,11\n",
            encoding="utf-8",
        )
        checkpoint = WorkflowCheckpoint(task)
        state = checkpoint.initialize(
            Problem(
                task_id=identifier,
                ques_all="测试夹具：对固定数据 y=2x+1 计算误差，不代表真实模型输出。",
                execution_backend="python",
            )
        )
        state.update(
            status=status,
            questions={
                "title": f"测试夹具 · {status}",
                "background": "固定测试数据",
                "ques1": "计算六行数据的均方误差",
            },
            ques_count=1,
            completed_nodes=["coordinator", "modeler", "solve:ques1"],
            current_node="solve:ques1" if status != "completed" else None,
            solution_results={
                "ques1": {
                    "question_text": "计算六行数据的均方误差",
                    "artifacts": ["result.csv"],
                    "paper_ready_images": [],
                    "execution_summary": {
                        "status": "completed",
                        "metrics": [{"name": "均方误差", "value": 0}],
                        "conclusion": "固定夹具六行数据的均方误差为 0；只验证读取展示，不代表预测泛化能力。",
                    },
                    "quality_report": {
                        "status": "manual_review",
                        "manual_review_required": True,
                    },
                }
            },
        )
        checkpoint.save(state)
        record(
            task,
            "coordinator",
            "reply",
            "【测试夹具】无外部模型调用。计算、证据审核与论文状态分别展示。",
        )
        if status == "failed":
            write_json(
                task / ".runtime-failure.json",
                {
                    "code": "INPUT_DATA",
                    "layer": "execution",
                    "reason": "测试夹具：输入数据列缺失，当前步骤未完成。",
                    "technical_detail": "KeyError",
                    "retrying": False,
                    "attempts_used": 0,
                    "saved_result_count": 1,
                },
            )
        if status == "running":
            for index in range(2000):
                record(
                    task,
                    "coder" if index % 3 else "coordinator",
                    "activity" if index % 2 else "reply",
                    f"测试进度 {index}：固定夹具观察，公式 $y=2x+1$，不代表真实求解。",
                    key=f"fixture:{index}",
                )
        paper = ensure_workspace(task)
        sync_results(task, state)
        source = r"\documentclass[UTF8,fontset=fandol]{ctexart}\usepackage{booktabs}\begin{document}\section{固定证据短报告}这是环境验收夹具，不是 AI 解题成果。六个样本满足 $y=2x+1$，均方误差为零。该结果不能证明泛化能力。\section{证据与局限}证据为 result.csv，需人工核对科学适用范围。\end{document}"
        (paper / "main.tex").write_text(source, encoding="utf-8")
        (paper / "long-fixture.tex").write_text(
            source.replace(
                r"\end{document}",
                ("\n% 长文编辑性能夹具：固定数据与公式 y=2x+1。" * 1900)
                + r"\end{document}",
            ),
            encoding="utf-8",
        )
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
