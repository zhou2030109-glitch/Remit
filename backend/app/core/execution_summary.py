"""Build compact, evidence-backed execution records for the task workbench."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable

from app.core.metric_explainer import explain_metrics
from app.schemas.A2A import ModelExecutionReview
from app.schemas.response import (
    CodeLocation,
    ExecutionMetric,
    ExecutionSummaryMessage,
    TablePreview,
)

_PREVIEW_MAX_FILES = 4
_PREVIEW_MAX_ROWS = 10
_PREVIEW_MAX_COLUMNS = 12


_CODE_SUFFIXES = {".ipynb", ".m", ".mlx", ".py"}
_LANGUAGE_BY_SUFFIX = {
    ".ipynb": "Notebook",
    ".m": "MATLAB",
    ".mlx": "MATLAB",
    ".py": "Python",
}


def snapshot_code_files(work_dir: str | Path) -> set[str]:
    """Return the code files that already existed before a solve node started."""
    root = Path(work_dir)
    return {
        str(path.resolve())
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in _CODE_SUFFIXES
    }


def collect_code_locations(
    work_dir: str | Path,
    *,
    section: str,
    files_before: set[str] | None = None,
    limit: int = 8,
) -> list[CodeLocation]:
    """Locate the notebook section and code files created by the current node."""
    root = Path(work_dir)
    notebook = (root / "notebook.ipynb").resolve()
    candidates = [
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in _CODE_SUFFIXES
    ]
    changed = [
        path
        for path in candidates
        if files_before is None or str(path) not in files_before
    ]
    changed.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    ordered: list[Path] = []
    if notebook.is_file():
        ordered.append(notebook)
    for path in changed:
        if path != notebook and path not in ordered:
            ordered.append(path)
    if len(ordered) == 1 and ordered[0] == notebook:
        existing_scripts = sorted(
            (path for path in candidates if path != notebook),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        ordered.extend(existing_scripts[: max(0, limit - 1)])

    return [
        CodeLocation(
            path=str(path),
            section=section if path == notebook else "",
            language=_LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "Code"),
        )
        for path in ordered[:limit]
    ]


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _relative_improvement(
    model_value: float,
    baseline_value: float,
    higher_is_better: bool,
) -> float | None:
    denominator = abs(baseline_value)
    if denominator <= 1e-12:
        return None
    difference = (
        model_value - baseline_value
        if higher_is_better
        else baseline_value - model_value
    )
    return difference / denominator


def _collect_metrics(evidence: dict[str, Any]) -> list[ExecutionMetric]:
    payload = evidence.get("prediction_metrics")
    if not isinstance(payload, dict):
        return []

    metrics: list[ExecutionMetric] = []
    primary = payload.get("primary_metric")
    if isinstance(primary, dict):
        name = str(primary.get("name", "")).strip()
        model_value = _finite_float(primary.get("model_value"))
        baseline_value = _finite_float(primary.get("baseline_value"))
        higher = primary.get("higher_is_better")
        if name and model_value is not None:
            relative = None
            if baseline_value is not None and isinstance(higher, bool):
                relative = _relative_improvement(
                    model_value,
                    baseline_value,
                    higher,
                )
            metrics.append(
                ExecutionMetric(
                    name=name,
                    model_value=model_value,
                    baseline_value=baseline_value,
                    higher_is_better=higher if isinstance(higher, bool) else None,
                    relative_improvement=relative,
                )
            )

    secondary = payload.get("secondary_metrics")
    if isinstance(secondary, dict):
        existing = {item.name.casefold() for item in metrics}
        for name, value in secondary.items():
            model_value = _finite_float(value)
            normalized = str(name).strip()
            if (
                not normalized
                or model_value is None
                or normalized.casefold() in existing
            ):
                continue
            metrics.append(
                ExecutionMetric(name=normalized, model_value=model_value)
            )
            if len(metrics) >= 4:
                break
    return metrics


def _collect_table_previews(evidence: dict[str, Any]) -> list[TablePreview]:
    """把证据里已读好的 CSV 预览裁剪成前端可直接渲染的表格。"""
    payload = evidence.get("supporting_artifact_previews")
    if not isinstance(payload, dict):
        return []
    previews: list[TablePreview] = []
    for filename, table in payload.items():
        if not isinstance(table, dict):
            continue
        columns = [
            str(column)
            for column in table.get("columns", [])
            if str(column).strip()
        ][:_PREVIEW_MAX_COLUMNS]
        if not columns:
            continue
        rows = [
            {column: str(row.get(column, "")) for column in columns}
            for row in table.get("rows", [])[:_PREVIEW_MAX_ROWS]
            if isinstance(row, dict)
        ]
        previews.append(
            TablePreview(
                filename=str(filename),
                columns=columns,
                rows=rows,
                preview_limited_to_rows=_PREVIEW_MAX_ROWS,
            )
        )
        if len(previews) >= _PREVIEW_MAX_FILES:
            break
    return previews


def _candidate_names(quality_report: dict[str, Any]) -> list[str]:
    candidates = quality_report.get("candidate_models")
    if not isinstance(candidates, list):
        return []
    names: list[str] = []
    for item in candidates:
        name = item.get("name") if isinstance(item, dict) else item
        normalized = str(name or "").strip()
        if normalized and normalized not in names:
            names.append(normalized)
    return names


def _build_run_summary(
    *,
    selected_model: str,
    metrics: list[ExecutionMetric],
    artifact_count: int,
    revision_count: int,
) -> str:
    subject = selected_model or "当前建模方案"
    prefix = f"{subject} 已完成真实运行与独立质量校验"
    if revision_count:
        prefix += f"，在建模手反馈后重跑 {revision_count} 轮"
    if metrics:
        primary = metrics[0]
        metric_text = f"{primary.name.upper()}={primary.model_value:.4g}"
        if primary.baseline_value is not None:
            metric_text += f"，基线={primary.baseline_value:.4g}"
        if primary.relative_improvement is not None:
            metric_text += f"，相对改善={primary.relative_improvement:.1%}"
        prefix += f"；主指标 {metric_text}"
    prefix += f"；登记 {artifact_count} 项可追溯产物。"
    return prefix


def build_execution_summary_message(
    *,
    task_id: str,
    node_id: str,
    node_label: str,
    section: str,
    work_dir: str | Path,
    evidence: dict[str, Any],
    review: ModelExecutionReview,
    revision_count: int,
    artifacts: Iterable[str],
    paper_ready_images: Iterable[str],
    files_before: set[str] | None = None,
) -> ExecutionSummaryMessage:
    """Create one user-facing result card from verified files and modeler review."""
    quality_report = evidence.get("quality_report")
    if not isinstance(quality_report, dict):
        quality_report = {}
    artifact_list = sorted({str(item) for item in artifacts if str(item).strip()})
    image_list = sorted(
        {str(item) for item in paper_ready_images if str(item).strip()}
    )
    selected_model = str(quality_report.get("selected_model", "")).strip()
    metrics = _collect_metrics(evidence)
    run_summary = _build_run_summary(
        selected_model=selected_model,
        metrics=metrics,
        artifact_count=len(artifact_list),
        revision_count=revision_count,
    )
    status = "passed"
    if review.verdict == "manual_review":
        status = "needs_review"
    elif revision_count:
        status = "refined"

    return ExecutionSummaryMessage(
        id=f"execution-summary:{task_id}:{section}",
        content=run_summary,
        node_id=node_id,
        node_label=node_label,
        status=status,
        run_summary=run_summary,
        selected_model=selected_model,
        candidate_models=_candidate_names(quality_report),
        metrics=metrics,
        code_locations=collect_code_locations(
            work_dir,
            section=section,
            files_before=files_before,
        ),
        artifacts=artifact_list,
        paper_ready_images=image_list,
        modeler_verdict=review.verdict,
        modeler_summary=review.summary,
        modeler_evidence=review.evidence,
        modeler_weaknesses=review.weaknesses,
        writer_guidance=review.writer_guidance,
        revision_count=revision_count,
        metric_explanations=explain_metrics(metrics),
        table_previews=_collect_table_previews(evidence),
    )
