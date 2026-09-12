"""Opt-in live API evaluation using only the two fictional sample documents."""

import argparse
import getpass
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import httpx

from app.config import PROJECT_ROOT


LOCAL_API = "http://127.0.0.1:8000"
EXPECTED_DOCUMENTS = {
    "Employee Handbook": ["Admin", "Employee", "HR"],
    "Salary Records": ["Admin", "HR"],
}
SALARY_CANARIES = ("720000", "840000", "Salary Records")


class EvaluationSetupError(RuntimeError):
    """The local API or sample setup is not ready for the fixed live cases."""


@dataclass(frozen=True)
class LiveCase:
    name: str
    account: str
    query: str
    expected_status: str
    expected_source: str | None


CASES = (
    LiveCase(
        "employee_salary_blocked",
        "alice",
        "What is Alice's compensation?",
        "no_accessible_source",
        None,
    ),
    LiveCase(
        "employee_remote_answer",
        "alice",
        "How many telecommuting days are permitted?",
        "answered",
        "Employee Handbook",
    ),
    LiveCase(
        "admin_salary_answer",
        "admin",
        "What is Alice's compensation?",
        "answered",
        "Salary Records",
    ),
)


def login(client: httpx.Client, username: str, password: str) -> str:
    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    if response.status_code != 200:
        raise EvaluationSetupError(
            f"{username} login failed (HTTP {response.status_code})."
        )
    token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise EvaluationSetupError(f"{username} login returned no access token.")
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def validate_setup(client: httpx.Client, tokens: dict[str, str]) -> None:
    for username, expected_role in (("alice", "Employee"), ("admin", "Admin")):
        response = client.get("/auth/me", headers=bearer(tokens[username]))
        if response.status_code != 200 or response.json().get("role") != expected_role:
            raise EvaluationSetupError(
                f"{username} must currently have the {expected_role} role."
            )

    response = client.get("/admin/documents", headers=bearer(tokens["admin"]))
    if response.status_code != 200:
        raise EvaluationSetupError("Could not verify the fictional sample documents.")
    documents = response.json()
    actual = {
        document["title"]: sorted(document["allowed_roles"])
        for document in documents
        if document["status"] == "ready"
    }
    if len(documents) != 2 or actual != EXPECTED_DOCUMENTS:
        raise EvaluationSetupError(
            "This run needs only the two ready fictional documents with original roles."
        )


def _answer_matches(case: LiveCase, answer: str) -> bool:
    if case.name == "employee_remote_answer":
        return bool(re.search(r"\b(?:two|2)\s+days?\b", answer, re.I)) and (
            "manager approval" in answer.lower()
        )
    if case.name == "admin_salary_answer":
        return "720000" in answer.replace(",", "")
    return bool(answer.strip()) and not any(
        canary.lower() in answer.lower() for canary in SALARY_CANARIES
    )


def _valid_server_timings(timings: object, blocked: bool) -> bool:
    if not isinstance(timings, dict):
        return False
    values = (timings.get("context_preparation"), timings.get("gemini_generation"))
    if any(type(value) not in (int, float) or value < 0 for value in values):
        return False
    return not blocked or timings["gemini_generation"] == 0


def evaluate_live(client: httpx.Client, tokens: dict[str, str]) -> dict:
    """Measure three HTTP round trips; never store raw answers or tokens."""
    trials: list[dict] = []
    for case in CASES:
        started = perf_counter()
        try:
            response = client.post(
                "/rag/ask",
                headers=bearer(tokens[case.account]),
                json={"query": case.query, "top_k": 3},
            )
            latency_ms = round((perf_counter() - started) * 1000, 2)
        except httpx.RequestError:
            trials.append(
                {
                    "case": case.name,
                    "role": "Employee" if case.account == "alice" else "Admin",
                    "http_status": None,
                    "passed": False,
                    "failure_category": "local_connection_error",
                }
            )
            continue

        if response.status_code != 200:
            trials.append(
                {
                    "case": case.name,
                    "role": "Employee" if case.account == "alice" else "Admin",
                    "http_status": response.status_code,
                    "latency_ms": latency_ms,
                    "passed": False,
                    "failure_category": "api_error",
                }
            )
            continue

        body = response.json()
        citations = body.get("citations", [])
        titles = sorted({citation.get("document_title") for citation in citations})
        answer = body.get("answer", "")
        source_match = (
            not citations
            if case.expected_source is None
            else case.expected_source in titles
        )
        answer_match = isinstance(answer, str) and _answer_matches(case, answer)
        server_timings = body.get("timings_ms")
        timings_valid = _valid_server_timings(
            server_timings, blocked=case.expected_source is None
        )
        no_employee_salary_exposure = (
            case.account != "alice"
            or not any(canary.lower() in response.text.lower() for canary in SALARY_CANARIES)
        )
        passed = (
            body.get("user_role") == ("Employee" if case.account == "alice" else "Admin")
            and body.get("status") == case.expected_status
            and source_match
            and answer_match
            and timings_valid
            and no_employee_salary_exposure
        )
        trials.append(
            {
                "case": case.name,
                "role": body.get("user_role"),
                "http_status": response.status_code,
                "observed_status": body.get("status"),
                "observed_sources": titles,
                "expected_source_match": source_match,
                "answer_content_match": answer_match,
                "server_timings_ms": server_timings if timings_valid else None,
                "server_timings_valid": timings_valid,
                "employee_salary_exposure": not no_employee_salary_exposure,
                "latency_ms": latency_ms,
                "passed": passed,
            }
        )

    return {
        "evaluation": "live local API and Gemini answer check",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": "two fictional sample documents; roles verified before requests",
        "case_count": len(CASES),
        "gemini_eligible_cases": 2,
        "passed_cases": sum(trial["passed"] for trial in trials),
        "passed": all(trial["passed"] for trial in trials),
        "limitations": [
            "Only three fixed questions and one run; answers and timings may vary.",
            "Checks selected answer facts and citations, not every factual claim.",
            "String-based answer checks may reject correct paraphrases.",
            "Server timings separate context preparation from Gemini generation; client timing also includes local HTTP overhead.",
            "The script cannot directly inspect the external provider's logs or prove no call on the blocked case.",
            "Does not test concurrent revocation or production-scale performance.",
        ],
        "trials": trials,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly allow up to two real Gemini calls with fictional data.",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("Add --live only when you intend to use Gemini API quota.")

    print("Uses localhost only; passwords and tokens will not be printed or saved.")
    admin_password = getpass.getpass("Admin password: ")
    alice_password = getpass.getpass("Alice password: ")
    try:
        with httpx.Client(base_url=LOCAL_API, timeout=120.0, trust_env=False) as client:
            tokens = {
                "admin": login(client, "admin", admin_password),
                "alice": login(client, "alice", alice_password),
            }
            validate_setup(client, tokens)
            report = evaluate_live(client, tokens)
    except httpx.RequestError:
        print("Cannot reach the local API. Start the backend first.")
        return 2
    except EvaluationSetupError as error:
        print(f"Cannot run: {error}")
        return 2

    report_dir = PROJECT_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    report_path = report_dir / f"live_evaluation_{timestamp}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Result: {'PASS' if report['passed'] else 'FAIL'}")
    for trial in report["trials"]:
        timing = f", {trial['latency_ms']} ms" if "latency_ms" in trial else ""
        print(f"{trial['case']}: {'PASS' if trial['passed'] else 'FAIL'}{timing}")
        server_timings = trial.get("server_timings_ms")
        if server_timings is not None:
            print(
                f"  Context preparation: {server_timings['context_preparation']} ms; "
                f"Gemini generation: {server_timings['gemini_generation']} ms"
            )
    print(f"Report: {report_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
