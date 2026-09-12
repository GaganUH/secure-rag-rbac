"""Repeatable, Gemini-free retrieval evaluation on the two fictional sample files."""

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from statistics import median
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database import SessionLocal
from app.models import Document, DocumentPermission, Role
from app.rag_service import prepare_authorized_context
from app.vector_service import get_vector_client


EXPECTED_PERMISSIONS = {
    "Employee Handbook": {"Employee", "HR", "Admin"},
    "Salary Records": {"HR", "Admin"},
}
SALARY_CANARIES = ("Salary Records", "720000", "840000")


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    role: str
    query: str
    expected_source: str | None


CASES = (
    EvaluationCase(
        "employee_remote_work",
        "Employee",
        "How many telecommuting days are permitted?",
        "Employee Handbook",
    ),
    EvaluationCase(
        "employee_alice_salary",
        "Employee",
        "What is Alice's compensation?",
        None,
    ),
    EvaluationCase(
        "employee_bob_salary",
        "Employee",
        "What is Bob's annual salary?",
        None,
    ),
    EvaluationCase(
        "hr_alice_salary",
        "HR",
        "What is Alice's compensation?",
        "Salary Records",
    ),
    EvaluationCase(
        "admin_alice_salary",
        "Admin",
        "What is Alice's compensation?",
        "Salary Records",
    ),
)


def _validate_sample_dataset(session: Session) -> dict[str, set[int]]:
    """Avoid presenting results from an altered or private corpus as this benchmark."""
    documents = list(session.scalars(select(Document).where(Document.status == "ready")))
    if len(documents) != 2 or {doc.title for doc in documents} != set(
        EXPECTED_PERMISSIONS
    ):
        raise ValueError(
            "This evaluation needs exactly the two ready fictional sample documents."
        )

    rows = session.execute(
        select(Document.id, Document.title, Role.name)
        .join(DocumentPermission, DocumentPermission.document_id == Document.id)
        .join(Role, Role.id == DocumentPermission.role_id)
        .where(Document.status == "ready")
    ).all()
    actual_permissions: dict[str, set[str]] = {
        title: set() for title in EXPECTED_PERMISSIONS
    }
    permitted_ids: dict[str, set[int]] = {
        role: set() for role in ("Employee", "HR", "Admin")
    }
    for document_id, title, role_name in rows:
        actual_permissions[title].add(role_name)
        permitted_ids[role_name].add(document_id)
    if actual_permissions != EXPECTED_PERMISSIONS:
        raise ValueError(
            "Sample document roles differ from the benchmark setup; restore them first."
        )
    return permitted_ids


def evaluate(session: Session, repeats: int = 3, top_k: int = 3) -> dict:
    """Check retrieval visibility and timing; never invoke the Gemini provider."""
    if repeats < 1 or not 1 <= top_k <= 10:
        raise ValueError("Use at least one repeat and top_k between 1 and 10.")
    permitted_ids = _validate_sample_dataset(session)

    # Load the local embedding model before timing. This warm-up is not scored.
    prepare_authorized_context(
        session, CASES[0].query, CASES[0].role, top_k
    )

    trials: list[dict] = []
    for case in CASES:
        for repeat in range(1, repeats + 1):
            started = perf_counter()
            context = prepare_authorized_context(session, case.query, case.role, top_k)
            latency_ms = (perf_counter() - started) * 1000
            observed_titles = sorted({cite.document_title for cite in context.citations})
            unauthorized_citation = any(
                cite.document_id not in permitted_ids[case.role]
                for cite in context.citations
            )
            forbidden_context = case.role == "Employee" and any(
                canary in context.text for canary in SALARY_CANARIES
            )
            exposure = unauthorized_citation or forbidden_context
            source_match = (
                not context.citations
                if case.expected_source is None
                else case.expected_source in observed_titles
            )
            trials.append(
                {
                    "case": case.name,
                    "role": case.role,
                    "repeat": repeat,
                    "expected_source": case.expected_source,
                    "observed_sources": observed_titles,
                    "expected_source_match": source_match,
                    "unauthorized_exposure": exposure,
                    "latency_ms": round(latency_ms, 2),
                }
            )

    timings = sorted(trial["latency_ms"] for trial in trials)
    leaks = sum(trial["unauthorized_exposure"] for trial in trials)
    matches = sum(trial["expected_source_match"] for trial in trials)
    restricted_trials = [trial for trial in trials if trial["expected_source"] is None]
    restricted_leaks = sum(trial["unauthorized_exposure"] for trial in restricted_trials)
    return {
        "evaluation": "offline permission-aware context preparation",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": "two fictional sample documents; exact role baseline checked",
        "gemini_calls": 0,
        "case_count": len(CASES),
        "repeats_per_case": repeats,
        "trial_count": len(trials),
        "unauthorized_exposures": leaks,
        "restricted_trial_count": len(restricted_trials),
        "restricted_exposures": restricted_leaks,
        "expected_source_match_rate_percent": round(100 * matches / len(trials), 2),
        "context_preparation_latency_ms": {
            "median": round(median(timings), 2),
            "p95_nearest_rank": timings[ceil(0.95 * len(timings)) - 1],
        },
        "passed": leaks == 0 and matches == len(trials),
        "limitations": [
            "Single local run; timings depend on this computer and corpus size.",
            "Only five fixed cases; canary checks cover known fictional salary strings.",
            "Measures context and citations, not LLM answer correctness or log exposure.",
            "Does not measure API, authentication, concurrent revocation, or Gemini latency.",
        ],
        "trials": trials,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    try:
        with SessionLocal() as session:
            report = evaluate(session, repeats=args.repeats)
    finally:
        # The local Qdrant client must close before Python module shutdown.
        if get_vector_client.cache_info().currsize:
            get_vector_client().close()
            get_vector_client.cache_clear()

    report_dir = PROJECT_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    report_path = report_dir / f"security_evaluation_{timestamp}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Result: {'PASS' if report['passed'] else 'FAIL'}")
    print(f"Cases: {report['case_count']} x {report['repeats_per_case']} repeats")
    print(f"Unauthorized exposures: {report['unauthorized_exposures']}")
    print(f"Expected source match: {report['expected_source_match_rate_percent']}%")
    print(
        "Context preparation latency (ms): "
        f"median {report['context_preparation_latency_ms']['median']}, "
        f"p95 {report['context_preparation_latency_ms']['p95_nearest_rank']}"
    )
    print(f"Report: {report_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
