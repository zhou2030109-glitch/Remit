import httpx
import pytest

from app.core.agents.coder_agent import CoderAgentUnavailableError
from app.core.llm.errors import ModelStageBudgetExceeded, TransientLLMError
from app.services import conversation_errors, task_failure


@pytest.mark.parametrize(
    "status,code,hint",
    [
        (401, "AUTHENTICATION", "密钥"),
        (429, "RATE_LIMIT", "限流"),
        (503, "TRANSPORT", "暂时不可用"),
    ],
)
def test_wrapped_provider_failure_preserves_diagnosis(tmp_path, status, code, hint):
    request = httpx.Request("POST", "https://fixture.invalid/test")
    cause = httpx.HTTPStatusError(
        "private-provider-detail",
        request=request,
        response=httpx.Response(status, request=request),
    )
    error = CoderAgentUnavailableError("private-wrapper-detail")
    error.__cause__ = cause
    report = task_failure.describe(error, tmp_path, retrying=False, attempts=1)
    assert report["code"] == code
    assert hint in conversation_errors.explain(error)
    assert "private" not in str(report)
    assert "private" not in conversation_errors.explain(error)


def test_wrapped_budget_failure_preserves_phase_and_used_count(tmp_path):
    cause = ModelStageBudgetExceeded(
        used_calls=20,
        call_limit=24,
        elapsed_seconds=600,
        seconds_limit=900,
        phase="work",
    )
    error = CoderAgentUnavailableError("failed to summarize")
    error.__cause__ = cause
    report = task_failure.describe(error, tmp_path, retrying=False, attempts=1)
    assert report["code"] == "MODEL_STAGE_BUDGET"
    assert report["budget_phase"] == "work"
    assert report["attempts_used"] == 20
    assert report["actions"] == ["model_budget", "results", "diagnostics"]
    assert "额度" in conversation_errors.explain(error)


def test_cyclic_unknown_causes_are_bounded(tmp_path):
    outer = RuntimeError("private")
    inner = RuntimeError("private")
    outer.__cause__ = inner
    inner.__cause__ = outer
    assert (
        task_failure.describe(outer, tmp_path, retrying=False, attempts=0)["code"]
        == "UNCLASSIFIED"
    )
    assert "private" not in conversation_errors.explain(outer)


def test_suppressed_context_is_not_reported_as_current_failure(tmp_path):
    error = ValueError("private")
    error.__context__ = ConnectionError("earlier discarded failure")
    error.__suppress_context__ = True
    assert (
        task_failure.describe(error, tmp_path, retrying=False, attempts=0)["code"]
        == "UNCLASSIFIED"
    )


def test_public_local_validation_remains_authoritative():
    from fastapi import HTTPException

    error = HTTPException(409, "请先恢复归档项目。")
    error.__cause__ = ConnectionError("earlier failure")
    assert conversation_errors.explain(error) == "请先恢复归档项目。"


def test_transient_stream_failure_has_consistent_retry_classification(tmp_path):
    error = CoderAgentUnavailableError("private wrapper")
    error.__cause__ = TransientLLMError("private provider details")
    report = task_failure.describe(error, tmp_path, retrying=True, attempts=1)
    assert report["code"] == "TRANSPORT"
    assert report["retryable"] is True
    assert report["retrying"] is True
    assert "暂时不可用" in conversation_errors.explain(error)
    assert "private" not in str(report)


def test_nested_http_error_does_not_expose_provider_details():
    from fastapi import HTTPException

    error = RuntimeError("private wrapper")
    error.__cause__ = HTTPException(502, "private provider details")
    message = conversation_errors.explain(error)
    assert "HTTP 502" in message
    assert "private" not in message
