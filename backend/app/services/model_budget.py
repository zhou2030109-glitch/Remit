"""Durable model allowance, with a protected share for result review."""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.config.setting import settings
from app.core.llm.errors import ModelStageBudgetExceeded
from app.services.call_ledger import ExecutionBudgetConflict, model_stage_limits


@dataclass(frozen=True)
class ModelBudget:
    root: Path
    stage_key: str
    calls: int
    seconds: float
    protect_review: bool

    @staticmethod
    def schema(db):
        db.execute(
            "CREATE TABLE IF NOT EXISTS reservations (attempt_id TEXT PRIMARY KEY, call_id TEXT, stage_key TEXT)"
        )
        columns = {r[1] for r in db.execute("PRAGMA table_info(reservations)")}
        if "budget_phase" not in columns:
            db.execute(
                "ALTER TABLE reservations ADD COLUMN budget_phase TEXT NOT NULL DEFAULT 'work'"
            )
        db.execute(
            "CREATE TABLE IF NOT EXISTS model_budget_grants (request_id TEXT PRIMARY KEY, stage_key TEXT, additional_calls INTEGER, additional_seconds REAL, expected_used INTEGER, expected_limit INTEGER, expected_seconds REAL, resume_scheduled INTEGER NOT NULL DEFAULT 0)"
        )

    def counts(self, db, phase):
        calls, seconds = db.execute(
            "SELECT COALESCE(SUM(additional_calls),0),COALESCE(SUM(additional_seconds),0) FROM model_budget_grants WHERE stage_key=?",
            (self.stage_key,),
        ).fetchone()
        limit, seconds_limit = self.calls + calls, self.seconds + seconds
        used = db.execute(
            "SELECT COUNT(*) FROM reservations WHERE stage_key=?", (self.stage_key,)
        ).fetchone()[0]
        work_used = db.execute(
            "SELECT COUNT(*) FROM reservations WHERE stage_key=? AND budget_phase!='review'",
            (self.stage_key,),
        ).fetchone()[0]
        elapsed = work_elapsed = 0.0
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='calls'").fetchone():
            elapsed, work_elapsed = db.execute(
                "SELECT COALESCE(SUM(c.elapsed_seconds),0),COALESCE(SUM(CASE WHEN r.budget_phase!='review' THEN c.elapsed_seconds ELSE 0 END),0) FROM reservations r JOIN calls c ON c.attempt_id=r.attempt_id WHERE r.stage_key=?",
                (self.stage_key,),
            ).fetchone()
        reserved_calls = (
            min(settings.LLM_REVIEW_RESERVED_CALLS, max(0, self.calls - 1))
            if self.protect_review
            else 0
        )
        reserved_seconds = (
            min(settings.LLM_REVIEW_RESERVED_SECONDS, self.seconds / 3)
            if self.protect_review
            else 0
        )
        remaining_calls, remaining_seconds = (
            max(0, limit - used),
            max(0.0, seconds_limit - elapsed),
        )
        if phase != "review":
            remaining_calls = min(
                remaining_calls, max(0, limit - reserved_calls - work_used)
            )
            remaining_seconds = min(
                remaining_seconds,
                max(0.0, seconds_limit - reserved_seconds - work_elapsed),
            )
        minimum = min(settings.LLM_MIN_REQUEST_SECONDS, self.seconds)
        return dict(
            stage_key=self.stage_key,
            phase=phase,
            used=used,
            limit=limit,
            elapsed_seconds=elapsed,
            seconds_limit=seconds_limit,
            remaining=remaining_calls,
            remaining_seconds=remaining_seconds,
            minimum_request_seconds=minimum,
            reserved_calls=reserved_calls,
            reserved_seconds=reserved_seconds,
            can_resume=remaining_calls > 0 and remaining_seconds >= minimum,
        )

    def snapshot(self, phase="work"):
        with closing(sqlite3.connect(self.root / ".calls.sqlite3", timeout=3)) as db:
            db.execute("BEGIN IMMEDIATE")
            self.schema(db)
            result = self.counts(db, phase)
            db.commit()
            return result

    @staticmethod
    def require(snapshot):
        if not snapshot["can_resume"]:
            raise ModelStageBudgetExceeded(
                used_calls=snapshot["used"],
                call_limit=snapshot["limit"],
                elapsed_seconds=snapshot["elapsed_seconds"],
                seconds_limit=snapshot["seconds_limit"],
                phase=snapshot["phase"],
                remaining_seconds=snapshot["remaining_seconds"],
                minimum_seconds=snapshot["minimum_request_seconds"],
            )

    def reserve(self, call_id, attempt_id, phase):
        with closing(sqlite3.connect(self.root / ".calls.sqlite3", timeout=3)) as db:
            db.execute("BEGIN IMMEDIATE")
            self.schema(db)
            snapshot = self.counts(db, phase)
            self.require(snapshot)
            db.execute(
                "INSERT OR IGNORE INTO reservations (attempt_id,call_id,stage_key,budget_phase) VALUES(?,?,?,?)",
                (attempt_id, call_id, self.stage_key, phase),
            )
            db.commit()
            return snapshot["remaining_seconds"]

    def extend(
        self,
        request_id,
        stage_key,
        expected_used,
        expected_limit,
        expected_seconds,
        additional_calls,
        additional_seconds,
    ):
        if (
            stage_key != self.stage_key
            or not 1 <= additional_calls <= 24
            or not 60 <= additional_seconds <= 1800
        ):
            raise ExecutionBudgetConflict("阶段或追加模型额度无效，请重新查看")
        expected = (
            stage_key,
            additional_calls,
            additional_seconds,
            expected_used,
            expected_limit,
            expected_seconds,
        )
        with closing(sqlite3.connect(self.root / ".calls.sqlite3", timeout=3)) as db:
            db.execute("BEGIN IMMEDIATE")
            self.schema(db)
            prior = db.execute(
                "SELECT stage_key,additional_calls,additional_seconds,expected_used,expected_limit,expected_seconds FROM model_budget_grants WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if prior:
                if prior != expected:
                    raise ExecutionBudgetConflict("确认编号已用于其他模型额度")
                return
            current = self.counts(db, "review")
            if (current["used"], current["limit"], current["seconds_limit"]) != (
                expected_used,
                expected_limit,
                expected_seconds,
            ):
                raise ExecutionBudgetConflict("模型额度已变化，请重新查看后确认")
            db.execute(
                "INSERT INTO model_budget_grants (request_id,stage_key,additional_calls,additional_seconds,expected_used,expected_limit,expected_seconds) VALUES(?,?,?,?,?,?,?)",
                (request_id, *expected),
            )
            db.commit()

    def mark_resume_scheduled(self, request_id):
        with closing(sqlite3.connect(self.root / ".calls.sqlite3", timeout=3)) as db:
            db.execute("BEGIN IMMEDIATE")
            self.schema(db)
            changed = db.execute(
                "UPDATE model_budget_grants SET resume_scheduled=1 WHERE request_id=? AND stage_key=? AND resume_scheduled=0",
                (request_id, self.stage_key),
            ).rowcount
            db.commit()
            return bool(changed)


def for_task(task_id: str, purpose: str = "default") -> ModelBudget | None:
    from app.utils.common_utils import get_work_dir
    from app.services.writing_workspace import read_json

    try:
        root = Path(get_work_dir(task_id))
    except FileNotFoundError:
        return None
    state = read_json(root / "workflow_state.json")
    paper = (
        read_json(root / "paper/input.json")
        if purpose.startswith("WriterAgent")
        or (
            purpose == "default"
            and str(state.get("current_node", "")).startswith(("write:", "paper:"))
        )
        else {}
    )
    identity = {
        "node": state.get("current_node") or purpose,
        "revisions": state.get("revision_counts") or {},
        "paper": paper.get("revision"),
    }
    # Preserve the old key exactly; upgrades and retries never erase past usage.
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    calls, seconds = model_stage_limits(state)
    return ModelBudget(
        root,
        key,
        calls,
        seconds,
        str(state.get("current_node", "")).startswith("solve:"),
    )


def resume_phase(root: Path, state: dict) -> str:
    """Route recovery to saved review; the workflow revalidates files before use."""
    from app.services.writing_workspace import read_json

    node = str(state.get("current_node", ""))
    saved_phase = state.get("model_stage_phase") or {}
    if saved_phase.get("node") == node and saved_phase.get("phase") in {
        "work",
        "review",
    }:
        return saved_phase["phase"]
    if state.get("pending_model_review") == node:
        return "review"
    # Older checkpoints did not save the sub-step. A budget failure plus a
    # report permits revalidation, never implicit acceptance or skipping gates.
    import re

    if re.fullmatch(r"solve:(eda|ques\d+|sensitivity_analysis)", node):
        failure = read_json(root / ".runtime-failure.json")
        key = node.split(":", 1)[1]
        if (
            failure.get("stage") == node
            and failure.get("run_id") == state.get("execution_id")
            and failure.get("budget_phase") in {"work", "review"}
        ):
            return failure["budget_phase"]
        reviews = (state.get("model_execution_reviews") or {}).get(key) or []
        if reviews and (reviews[-1].get("review") or {}).get("verdict") == "refine":
            # A report left by the rejected run is not a review checkpoint.
            return "work"
        if (
            failure.get("code") == "MODEL_STAGE_BUDGET"
            and (root / f"{key}_quality_report.json").is_file()
        ):
            return "review"
    return "work"
