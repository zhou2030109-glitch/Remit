"""主页面阶段审计、附件侦察和状态真实性回归测试。"""

from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.data_scout import build_data_profile
from app.core.literature import run_literature_review
from app.core.llm.types import StandardResponse
from app.core.progress import build_progress_message
from app.core.workflow_checkpoint import WorkflowCheckpoint
from app.services.redis_manager import redis_manager
from app.schemas.request import Problem
from app.routers import common_router
from app.tools.openalex_scholar import OpenAlexScholar


class ProjectAuditTests(unittest.TestCase):
    def test_degraded_research_and_analysis_are_not_green_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WorkflowCheckpoint(tmp)
            state = checkpoint.initialize(
                Problem(task_id="audit-task", ques_all="问题1：建立模型")
            )
            state["questions"] = {"ques_count": 1, "ques1": "建立模型"}
            state["ques_count"] = 1
            state["coordinator_response"] = {
                "questions": state["questions"],
                "ques_count": 1,
            }
            state["data_profile"] = {"files": [], "notes": []}
            state["literature_review"] = {}
            state["analysis_response"] = {
                "questions": state["questions"],
                "ques_count": 1,
                "analysis_summary": "结构化分析已生成",
                "question_analyses": {"ques1": {"objective": "建立模型"}},
            }
            checkpoint.complete_node(state, "coordinator")
            checkpoint.complete_node(state, "research")
            checkpoint.complete_node(state, "analysis")

            progress = build_progress_message("audit-task", checkpoint, state)

        statuses = {stage.node_id: stage.status for stage in progress.stages}
        self.assertEqual(statuses["research"], "warning")
        self.assertEqual(statuses["analysis"], "warning")

    def test_parenthesized_txt_attachments_are_profiled(self) -> None:
        attachment = """组名称,(接口名称),(接口坐标),HPWL,RSMT
Group1,(Cell1:A1,Cell2:B),((1,2),(3,4)),4,5
Group2,(Cell3:Z,Cell4:I),((5,6),(7,8)),6,7

布局区域宽度,布局区域高度,网格数
38080,37800,64
"""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "附件1.txt").write_text(attachment, encoding="utf-8")

            profile = build_data_profile(tmp)

        self.assertEqual(profile["status"], "completed")
        self.assertEqual(profile["discovered_files"], ["附件1.txt"])
        self.assertEqual(profile["files"][0]["rows"], 3)
        self.assertEqual(profile["files"][0]["sections"][0]["rows"], 2)
        self.assertEqual(profile["files"][0]["sections"][0]["columns_count"], 5)


class ProjectAuditAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_scholar_falls_back_to_crossref_without_openalex_key(self) -> None:
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "message": {
                "items": [
                    {
                        "title": ["A rectilinear Steiner tree method"],
                        "abstract": "<jats:p>Evidence-grounded abstract.</jats:p>",
                        "author": [
                            {"given": "Ada", "family": "Lovelace"},
                            {"given": "Alan", "family": "Turing"},
                        ],
                        "is-referenced-by-count": 42,
                        "DOI": "10.1000/example",
                        "published": {"date-parts": [[2024, 6, 1]]},
                        "container-title": ["Test Journal"],
                        "URL": "https://doi.org/10.1000/example",
                    },
                    {
                        "title": ["Target course estimation using local coordinates"],
                        "author": [{"given": "Irrelevant", "family": "Author"}],
                        "is-referenced-by-count": 3,
                        "DOI": "10.1000/irrelevant",
                        "published": {"date-parts": [[2023]]},
                    },
                ]
            }
        }
        scholar = OpenAlexScholar(task_id="audit-task")

        with (
            patch(
                "app.tools.openalex_scholar.requests.get",
                return_value=response,
            ) as request,
            patch.object(redis_manager, "publish_message", new=AsyncMock()),
        ):
            papers = await scholar.search_papers("rectilinear Steiner tree", limit=3)

        self.assertEqual(request.call_args.args[0], "https://api.crossref.org/works")
        self.assertEqual(papers[0]["title"], "A rectilinear Steiner tree method")
        self.assertEqual(papers[0]["publication_year"], 2024)
        self.assertEqual(papers[0]["citations_count"], 42)
        self.assertEqual(papers[0]["source"], "Crossref")
        self.assertEqual(papers[0]["doi"], "10.1000/example")
        self.assertEqual(papers[0]["abstract"], "Evidence-grounded abstract.")
        self.assertEqual(len(papers), 1)

    async def test_workspace_snapshot_exposes_all_problem_analysis_stages(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = root / "audit-task"
            task_dir.mkdir()
            state = {
                "version": 1,
                "status": "awaiting_approval",
                "current_node": None,
                "workflow_features": ["research", "analysis", "pilot"],
                "completed_nodes": ["coordinator", "research", "analysis"],
                "approved_nodes": [],
                "questions": {"ques_count": 1, "ques1": "原题第一问"},
                "ques_count": 1,
                "problem": {"task_id": "audit-task", "ques_all": "完整原题"},
                "coordinator_response_pre_analysis": {
                    "original_problem": "完整原题",
                    "questions": {
                        "title": "测试题",
                        "ques_count": 1,
                        "ques1": "原题第一问",
                    },
                    "ques_count": 1,
                    "analysis_summary": "初步理解",
                    "question_analyses": {"ques1": {"objective": "初步目标"}},
                },
                "coordinator_response": {
                    "original_problem": "完整原题",
                    "questions": {
                        "title": "测试题",
                        "ques_count": 1,
                        "ques1": "原题第一问",
                    },
                    "ques_count": 1,
                    "analysis_summary": "数据校正版理解",
                    "question_analyses": {"ques1": {"objective": "校正目标"}},
                },
                "analysis_response": {
                    "original_problem": "完整原题",
                    "questions": {
                        "title": "测试题",
                        "ques_count": 1,
                        "ques1": "原题第一问",
                    },
                    "ques_count": 1,
                    "analysis_summary": "数据校正版理解",
                    "question_analyses": {"ques1": {"objective": "校正目标"}},
                },
                "data_profile": {
                    "status": "completed",
                    "files": [{"file": "附件1.txt"}],
                },
                "literature_review": {
                    "status": "failed",
                    "paper_count": 0,
                    "questions": {},
                    "errors": ["OpenAlex 未配置"],
                },
                "modeler_response": None,
            }
            (task_dir / "workflow_state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )

            with patch.object(common_router, "TASK_WORK_DIR_ROOT", root):
                snapshot = await common_router.get_task_workspace("audit-task")

        self.assertEqual(snapshot["source"]["original_problem"], "完整原题")
        self.assertEqual(
            snapshot["preliminary_analysis"]["analysis_summary"], "初步理解"
        )
        self.assertEqual(
            snapshot["refined_analysis"]["analysis_summary"], "数据校正版理解"
        )
        self.assertEqual(snapshot["research"]["outcome"]["status"], "warning")
        self.assertEqual(
            snapshot["research"]["literature_review"]["errors"], ["OpenAlex 未配置"]
        )

    async def test_failed_literature_search_returns_auditable_failure(self) -> None:
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=StandardResponse(
                content='{"queries":[{"question_key":"ques1","query":"RSMT placement"}]}'
            )
        )
        scholar = MagicMock()
        scholar.search_papers = AsyncMock(
            side_effect=ValueError("配置OpenAlex邮箱获取访问文献权利")
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "app.core.literature.publish_activity",
                new=AsyncMock(),
            ),
        ):
            review = await run_literature_review(
                task_id="audit-task",
                llm=llm,
                scholar=scholar,
                questions={"ques1": "建立线长模型", "ques_count": 1},
                work_dir=tmp,
            )

        self.assertEqual(review["status"], "failed")
        self.assertEqual(review["paper_count"], 0)
        self.assertEqual(review["searched_queries"], ["RSMT placement"])
        self.assertIn("OpenAlex", review["errors"][0])


if __name__ == "__main__":
    unittest.main()
