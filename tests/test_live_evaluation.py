import httpx
import pytest

import app.evaluate_live as live


def _fake_client(
    leak_salary: bool = False, blocked_gemini_time: float = 0.0
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("Authorization", "")
        if request.url.path == "/auth/me":
            role = "Admin" if token == "Bearer admin-token" else "Employee"
            return httpx.Response(200, json={"role": role})
        if request.url.path == "/admin/documents":
            return httpx.Response(
                200,
                json=[
                    {
                        "title": "Employee Handbook",
                        "status": "ready",
                        "allowed_roles": ["Admin", "Employee", "HR"],
                    },
                    {
                        "title": "Salary Records",
                        "status": "ready",
                        "allowed_roles": ["Admin", "HR"],
                    },
                ],
            )
        if request.url.path == "/rag/ask":
            query = request.read().decode()
            if token == "Bearer alice-token" and "compensation" in query:
                return httpx.Response(
                    200,
                    json={
                        "user_role": "Employee",
                        "status": "no_accessible_source",
                        "answer": (
                            "720000 rupees" if leak_salary else "No accessible answer."
                        ),
                        "citations": [],
                        "timings_ms": {
                            "context_preparation": 12.0,
                            "gemini_generation": blocked_gemini_time,
                        },
                    },
                )
            if token == "Bearer alice-token":
                return httpx.Response(
                    200,
                    json={
                        "user_role": "Employee",
                        "status": "answered",
                        "answer": "Up to two days per week with manager approval [1].",
                        "citations": [{"document_title": "Employee Handbook"}],
                        "timings_ms": {
                            "context_preparation": 10.0,
                            "gemini_generation": 100.0,
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "user_role": "Admin",
                    "status": "answered",
                    "answer": "Fictional salary: 720,000 rupees [1].",
                    "citations": [{"document_title": "Salary Records"}],
                    "timings_ms": {
                        "context_preparation": 11.0,
                        "gemini_generation": 100.0,
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.url.path}")

    return httpx.Client(
        base_url=live.LOCAL_API,
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )


TOKENS = {"admin": "admin-token", "alice": "alice-token"}


def test_live_evaluation_checks_three_cases_without_storing_secrets() -> None:
    with _fake_client() as client:
        live.validate_setup(client, TOKENS)
        report = live.evaluate_live(client, TOKENS)

    assert report["passed"] is True
    assert report["passed_cases"] == 3
    assert report["gemini_eligible_cases"] == 2
    assert all(trial["latency_ms"] >= 0 for trial in report["trials"])
    assert all(trial["server_timings_valid"] for trial in report["trials"])
    assert report["trials"][0]["server_timings_ms"]["gemini_generation"] == 0.0
    assert "admin-token" not in str(report)
    assert "alice-token" not in str(report)
    assert "720000" not in str(report)
    assert "720,000" not in str(report)


def test_live_evaluation_fails_on_salary_leak_in_employee_answer() -> None:
    with _fake_client(leak_salary=True) as client:
        report = live.evaluate_live(client, TOKENS)

    assert report["passed"] is False
    assert report["trials"][0]["employee_salary_exposure"] is True
    assert "720000" not in str(report)


def test_live_evaluation_rejects_nonzero_gemini_time_for_blocked_case() -> None:
    with _fake_client(blocked_gemini_time=10.0) as client:
        report = live.evaluate_live(client, TOKENS)

    assert report["passed"] is False
    assert report["trials"][0]["server_timings_valid"] is False


def test_live_script_does_not_prompt_or_call_api_without_live_flag(
    monkeypatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["evaluate_live"])
    monkeypatch.setattr(
        live.getpass,
        "getpass",
        lambda *_args: pytest.fail("Password prompt should not appear"),
    )
    with pytest.raises(SystemExit) as error:
        live.main()
    assert error.value.code == 2
