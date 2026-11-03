import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.project_audit import evaluated_node_status, pilot_evidence_notice
from app.core.pilot import PilotValidationError
from app.core.workflow import RemitWorkFlow
from app.schemas.A2A import ModelerToCoder, PilotPlan
from app.services.writing_workspace import sync_results


class PilotOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_not_applicable_does_not_execute_or_replace_approved_solution(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf = RemitWorkFlow()
            wf.work_dir, wf.task_id = tmp, "fixed-method"
            wf.questions = {"ques1": "固定方法数值核验"}
            wf.code_interpreter = SimpleNamespace(language="python")
            wf.checkpoint = MagicMock()
            wf.checkpoint.consume_revision_feedback.return_value = "不允许新候选"
            wf._start_node = AsyncMock()
            wf._complete_node = AsyncMock()
            wf._run_pilot_questions = AsyncMock()
            modeler = SimpleNamespace(
                design_pilot_plan=AsyncMock(
                    return_value=PilotPlan(
                        not_applicable_reason="本任务已固定唯一方法，仅要求数值复算，不适用候选选型。"
                    )
                ),
                finalize_with_pilot=AsyncMock(),
            )
            response = ModelerToCoder(questions_solution={"ques1": "独立公式复算"})
            state = {
                "problem": {"user_requirements": "只使用实际输入数据"},
                "modeler_response": response.model_dump(),
            }
            with (
                patch(
                    "app.core.workflow.task_bus.publish_message",
                    new_callable=AsyncMock,
                ),
                patch("app.core.workflow.publish_activity", new_callable=AsyncMock),
            ):
                await wf._pilot_node(state, modeler, SimpleNamespace(), response)
            wf._run_pilot_questions.assert_not_awaited()
            modeler.finalize_with_pilot.assert_not_awaited()
            wf._complete_node.assert_awaited_once()
            self.assertEqual(state["modeler_response"], response.model_dump())
            self.assertEqual(evaluated_node_status(state, "pilot"), "skipped")
            constraints = json.loads(
                modeler.design_pilot_plan.await_args.kwargs["task_constraints"]
            )
            self.assertEqual(constraints["pilot_revision_feedback"], "不允许新候选")

    async def test_technical_error_does_not_complete_or_skip_pilot(self):
        await self.check_failure(ConnectionError("gateway unavailable"), technical=True)

    async def test_explicit_numeric_scope_skips_planning_and_coding_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf = RemitWorkFlow()
            wf.work_dir, wf.task_id = tmp, "fixed-method"
            wf.questions = {"ques1": "核对 OLS"}
            wf.code_interpreter = SimpleNamespace(language="python")
            wf.checkpoint = MagicMock()
            wf.checkpoint.consume_revision_feedback.return_value = ""
            wf._start_node = AsyncMock()
            wf._complete_node = AsyncMock()
            wf._run_pilot_questions = AsyncMock()
            modeler = SimpleNamespace(
                design_pilot_plan=AsyncMock(), finalize_with_pilot=AsyncMock()
            )
            state = {"problem": {"task_purpose": "numerical_verification"}}
            with (
                patch(
                    "app.core.workflow.task_bus.publish_message",
                    new_callable=AsyncMock,
                ),
                patch("app.core.workflow.publish_activity", new_callable=AsyncMock),
            ):
                await wf._pilot_node(
                    state,
                    modeler,
                    SimpleNamespace(),
                    ModelerToCoder(questions_solution={"ques1": "OLS"}),
                )
            modeler.design_pilot_plan.assert_not_awaited()
            modeler.finalize_with_pilot.assert_not_awaited()
            wf._run_pilot_questions.assert_not_awaited()
            wf.checkpoint.retire_pilot_outputs.assert_called_once_with(state)
            self.assertEqual(evaluated_node_status(state, "pilot"), "skipped")

    async def test_invalid_comparison_is_explicitly_skipped(self):
        await self.check_failure(
            PilotValidationError("no valid candidates"), technical=False
        )

    async def check_failure(self, error, *, technical):
        with tempfile.TemporaryDirectory() as tmp:
            wf = RemitWorkFlow()
            wf.work_dir = tmp
            wf.task_id = "test"
            wf.questions = {"ques1": "question"}
            wf.code_interpreter = SimpleNamespace(language="matlab")
            wf.checkpoint = MagicMock()
            wf.checkpoint.consume_revision_feedback.return_value = None
            wf._start_node = AsyncMock()
            wf._complete_node = AsyncMock()
            wf._check_cancelled = AsyncMock()
            modeler = SimpleNamespace(design_pilot_plan=AsyncMock(side_effect=error))
            coder = SimpleNamespace(append_chat_history=AsyncMock())
            state = {}
            with (
                patch(
                    "app.core.workflow.task_bus.publish_message",
                    new_callable=AsyncMock,
                ),
                patch("app.core.workflow.publish_activity", new_callable=AsyncMock),
            ):
                if technical:
                    with self.assertRaises(ConnectionError):
                        await wf._pilot_node(
                            state,
                            modeler,
                            coder,
                            ModelerToCoder(questions_solution={"ques1": "strategy"}),
                        )
                    wf._complete_node.assert_not_awaited()
                    self.assertNotIn("pilot_skipped", state)
                    self.assertEqual(
                        state["node_outcomes"]["pilot"]["status"], "failed"
                    )
                else:
                    await wf._pilot_node(
                        state,
                        modeler,
                        coder,
                        ModelerToCoder(questions_solution={"ques1": "strategy"}),
                    )
                    wf._complete_node.assert_awaited_once()
                    self.assertEqual(evaluated_node_status(state, "pilot"), "skipped")

    def test_old_skipped_checkpoint_and_writing_snapshot_keep_limitations(self):
        state = {"pilot_skipped": "no usable comparison", "completed_nodes": ["pilot"]}
        self.assertEqual(evaluated_node_status(state, "pilot"), "skipped")
        self.assertIn("不得声称", pilot_evidence_notice(state))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            info = sync_results(root, state)
            snapshot = root / "paper" / ".inputs" / info["revision"] / "evidence.json"
            evidence = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(evidence["pilot_skipped"], state["pilot_skipped"])
            self.assertIn("不得声称", evidence["evidence_notice"])

    def test_records_without_decision_are_not_passed(self):
        self.assertEqual(
            evaluated_node_status({"pilot_results": {"questions": {}}}, "pilot"),
            "warning",
        )
