import pytest

import app.evaluate_security as evaluation
from app.rag_service import PreparedContext
from app.schemas import SourceCitation
from tests.test_permission_aware_retrieval import retrieval_test_app


HANDBOOK_SOURCE = SourceCitation(
    source_number=1,
    document_id=1,
    document_title="Employee Handbook",
    chunk_id=1,
    page_number=1,
)
SALARY_SOURCE = SourceCitation(
    source_number=1,
    document_id=2,
    document_title="Salary Records",
    chunk_id=2,
    page_number=1,
)


def _expected_context(_session, query: str, role: str, _top_k: int):
    if "telecommuting" in query:
        return PreparedContext("Employee Handbook remote days", [HANDBOOK_SOURCE])
    if role == "Employee":
        return PreparedContext("", [])
    return PreparedContext("Fictional annual salary", [SALARY_SOURCE])


def test_evaluation_scores_expected_sources_without_gemini(
    retrieval_test_app, monkeypatch
) -> None:
    _client, session = retrieval_test_app
    monkeypatch.setattr(evaluation, "prepare_authorized_context", _expected_context)

    report = evaluation.evaluate(session, repeats=2)

    assert report["passed"] is True
    assert report["trial_count"] == 10
    assert report["restricted_trial_count"] == 4
    assert report["unauthorized_exposures"] == 0
    assert report["expected_source_match_rate_percent"] == 100.0
    assert report["gemini_calls"] == 0
    assert "720000" not in str(report)


def test_evaluation_detects_leaked_context_without_a_citation(
    retrieval_test_app, monkeypatch
) -> None:
    _client, session = retrieval_test_app

    def leaking_context(session, query, role, top_k):
        if role == "Employee" and "salary" in query:
            return PreparedContext("720000 rupees", [])
        if role == "Employee" and "compensation" in query:
            return PreparedContext("720000 rupees", [])
        return _expected_context(session, query, role, top_k)

    monkeypatch.setattr(evaluation, "prepare_authorized_context", leaking_context)
    report = evaluation.evaluate(session, repeats=1)

    assert report["passed"] is False
    assert report["unauthorized_exposures"] == 2
    assert report["restricted_exposures"] == 2
    assert "720000" not in str(report)


def test_evaluation_rejects_changed_sample_permissions(
    retrieval_test_app
) -> None:
    _client, session = retrieval_test_app
    # The benchmark must not silently treat a different policy as its baseline.
    from app.models import DocumentPermission
    from sqlalchemy import delete

    session.execute(delete(DocumentPermission).where(DocumentPermission.document_id == 2))
    session.commit()
    with pytest.raises(ValueError, match="Sample document roles differ"):
        evaluation.evaluate(session, repeats=1)
