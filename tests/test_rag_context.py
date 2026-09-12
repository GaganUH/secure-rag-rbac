import app.rag_service as rag_service
import app.rag_routes as rag_routes
import app.document_service as document_service
from app.gemini_provider import GeminiGenerationError
from app.rag_service import NO_ACCESSIBLE_SOURCE_ANSWER, generate_grounded_answer
from app.user_service import change_user_role, change_user_status
from tests.test_permission_aware_retrieval import bearer, login, retrieval_test_app


class FakeProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "Employees may work remotely two days per week. [1]"


def test_preview_uses_only_currently_authorized_sqlite_text(
    retrieval_test_app, monkeypatch
) -> None:
    client, _session = retrieval_test_app
    token = login(client, "alice", "Employee-password-123")

    # Simulate stale or malicious vector payload, including a secret document.
    monkeypatch.setattr(
        rag_service,
        "semantic_search",
        lambda *_args, **_kwargs: [
            {
                "chunk_id": 2,
                "document_id": 2,
                "text": "Alice has a fictional salary of 720000 rupees.",
            },
            {
                "chunk_id": 1,
                "document_id": 1,
                "text": "Altered vector payload text should not be trusted.",
            },
        ],
    )

    response = client.post(
        "/rag/context-preview",
        headers=bearer(token),
        json={"query": "remote work", "top_k": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["user_role"] == "Employee"
    assert [citation["document_title"] for citation in body["citations"]] == [
        "Employee Handbook"
    ]
    assert "Employees may work remotely" in body["context"]
    assert "720000" not in response.text
    assert "Altered vector payload" not in response.text


def test_no_accessible_source_never_calls_provider(
    retrieval_test_app, monkeypatch
) -> None:
    _client, session = retrieval_test_app
    monkeypatch.setattr(rag_service, "semantic_search", lambda *_args, **_kwargs: [])
    provider = FakeProvider()

    result = generate_grounded_answer(
        session, "What is Alice's salary?", "Employee", 3, provider, 2, 1
    )

    assert result.answer == NO_ACCESSIBLE_SOURCE_ANSWER
    assert result.citations == []
    assert result.gemini_generation_ms == 0.0
    assert provider.prompts == []


def test_provider_receives_only_authorized_context(
    retrieval_test_app, monkeypatch
) -> None:
    _client, session = retrieval_test_app
    monkeypatch.setattr(
        rag_service,
        "semantic_search",
        lambda *_args, **_kwargs: [
            {"chunk_id": 2, "document_id": 2},
            {"chunk_id": 1, "document_id": 1},
        ],
    )
    provider = FakeProvider()

    result = generate_grounded_answer(
        session, "How many remote work days?", "Employee", 3, provider, 2, 1
    )

    assert result.answer.endswith("[1]")
    assert [citation.document_title for citation in result.citations] == [
        "Employee Handbook"
    ]
    assert len(provider.prompts) == 1
    assert "Employee Handbook" in provider.prompts[0]
    assert "720000" not in provider.prompts[0]


def test_preview_requires_login(retrieval_test_app) -> None:
    client, _session = retrieval_test_app
    response = client.post(
        "/rag/context-preview",
        json={"query": "remote work", "top_k": 3},
    )
    assert response.status_code == 401


def test_ask_never_calls_gemini_without_accessible_source(
    retrieval_test_app, monkeypatch
) -> None:
    client, _session = retrieval_test_app
    token = login(client, "alice", "Employee-password-123")
    monkeypatch.setattr(rag_service, "semantic_search", lambda *_args, **_kwargs: [])
    provider = FakeProvider()
    monkeypatch.setattr(rag_routes, "GeminiAnswerProvider", lambda: provider)

    response = client.post(
        "/rag/ask",
        headers=bearer(token),
        json={"query": "What is Alice's salary?", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "no_accessible_source"
    assert response.json()["answer"] == NO_ACCESSIBLE_SOURCE_ANSWER
    assert response.json()["citations"] == []
    assert response.json()["timings_ms"]["gemini_generation"] == 0.0
    assert provider.prompts == []


def test_ask_sends_only_authorized_text_to_provider(
    retrieval_test_app, monkeypatch
) -> None:
    client, _session = retrieval_test_app
    token = login(client, "alice", "Employee-password-123")
    monkeypatch.setattr(
        rag_service,
        "semantic_search",
        lambda *_args, **_kwargs: [
            {"chunk_id": 2, "document_id": 2},
            {"chunk_id": 1, "document_id": 1},
        ],
    )
    provider = FakeProvider()
    monkeypatch.setattr(rag_routes, "GeminiAnswerProvider", lambda: provider)

    response = client.post(
        "/rag/ask",
        headers=bearer(token),
        json={"query": "How many remote work days?", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert response.json()["answer"].endswith("[1]")
    assert [source["document_title"] for source in response.json()["citations"]] == [
        "Employee Handbook"
    ]
    assert len(provider.prompts) == 1
    assert "Employee Handbook" in provider.prompts[0]
    assert "720000" not in provider.prompts[0]


def test_ask_with_accessible_source_needs_api_key(
    retrieval_test_app, monkeypatch
) -> None:
    client, _session = retrieval_test_app
    token = login(client, "alice", "Employee-password-123")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        rag_service,
        "semantic_search",
        lambda *_args, **_kwargs: [{"chunk_id": 1, "document_id": 1}],
    )

    response = client.post(
        "/rag/ask",
        headers=bearer(token),
        json={"query": "How many remote work days?", "top_k": 3},
    )

    assert response.status_code == 503
    assert "Gemini is not configured" in response.json()["detail"]
    assert "Employees may work" not in response.text


def test_ask_requires_login(retrieval_test_app) -> None:
    client, _session = retrieval_test_app
    response = client.post(
        "/rag/ask",
        json={"query": "remote work", "top_k": 3},
    )
    assert response.status_code == 401


def test_ask_reports_only_safe_provider_status(retrieval_test_app, monkeypatch) -> None:
    client, _session = retrieval_test_app
    token = login(client, "alice", "Employee-password-123")
    monkeypatch.setattr(
        rag_service,
        "semantic_search",
        lambda *_args, **_kwargs: [{"chunk_id": 1, "document_id": 1}],
    )

    class FailingProvider:
        def generate(self, _prompt: str) -> str:
            raise GeminiGenerationError("provider_http_error", 403)

    monkeypatch.setattr(rag_routes, "GeminiAnswerProvider", FailingProvider)
    response = client.post(
        "/rag/ask",
        headers=bearer(token),
        json={"query": "How many remote work days?", "top_k": 3},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Gemini rejected the request (provider HTTP 403)."
    assert "Employees may work" not in response.text


def test_provider_failure_does_not_echo_exception_text_or_context_to_logs(
    retrieval_test_app, monkeypatch, caplog
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")
    monkeypatch.setattr(
        rag_service,
        "semantic_search",
        lambda *_args, **_kwargs: [{"chunk_id": 2, "document_id": 2}],
    )

    class FailingProvider:
        def generate(self, _prompt: str) -> str:
            raise GeminiGenerationError("provider included salary 720000")

    monkeypatch.setattr(rag_routes, "GeminiAnswerProvider", FailingProvider)
    response = client.post(
        "/rag/ask",
        headers=bearer(token),
        json={"query": "What is Alice's compensation?", "top_k": 3},
    )

    assert response.status_code == 502
    assert "720000" not in response.text
    assert "720000" not in caplog.text


def test_existing_token_stops_sending_salary_after_role_revocation(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")
    monkeypatch.setattr(
        rag_service,
        "semantic_search",
        lambda *_args, **_kwargs: [{"chunk_id": 2, "document_id": 2}],
    )
    provider = FakeProvider()
    monkeypatch.setattr(rag_routes, "GeminiAnswerProvider", lambda: provider)
    request = {"query": "What is Alice's compensation?", "top_k": 3}

    before = client.post("/rag/ask", headers=bearer(token), json=request)
    assert before.status_code == 200
    assert before.json()["status"] == "answered"
    assert len(provider.prompts) == 1
    assert "720000" in provider.prompts[0]

    change_user_role(session, 2, "Employee")
    after = client.post("/rag/ask", headers=bearer(token), json=request)
    assert after.status_code == 200
    assert after.json()["user_role"] == "Employee"
    assert after.json()["status"] == "no_accessible_source"
    assert after.json()["citations"] == []
    assert "720000" not in after.text
    assert len(provider.prompts) == 1


def test_existing_token_stops_sending_salary_after_document_revocation(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")
    monkeypatch.setattr(
        rag_service,
        "semantic_search",
        lambda *_args, **_kwargs: [{"chunk_id": 2, "document_id": 2}],
    )
    monkeypatch.setattr(
        document_service, "update_vector_permissions", lambda *_args: None
    )
    provider = FakeProvider()
    monkeypatch.setattr(rag_routes, "GeminiAnswerProvider", lambda: provider)
    request = {"query": "What is Alice's compensation?", "top_k": 3}

    before = client.post("/rag/ask", headers=bearer(token), json=request)
    assert before.status_code == 200
    assert before.json()["status"] == "answered"
    assert len(provider.prompts) == 1

    document_service.change_document_permissions(session, 2, ["Admin"])
    after = client.post("/rag/ask", headers=bearer(token), json=request)
    assert after.status_code == 200
    assert after.json()["user_role"] == "HR"
    assert after.json()["status"] == "no_accessible_source"
    assert after.json()["citations"] == []
    assert "720000" not in after.text
    assert len(provider.prompts) == 1


def test_role_revoked_during_retrieval_skips_provider(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")

    def revoke_during_search(*_args, **_kwargs):
        change_user_role(session, 2, "Employee")
        return [{"chunk_id": 2, "document_id": 2}]

    monkeypatch.setattr(rag_service, "semantic_search", revoke_during_search)
    provider = FakeProvider()
    monkeypatch.setattr(rag_routes, "GeminiAnswerProvider", lambda: provider)
    response = client.post(
        "/rag/ask",
        headers=bearer(token),
        json={"query": "What is Alice's compensation?", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "no_accessible_source"
    assert response.json()["citations"] == []
    assert response.json()["timings_ms"]["gemini_generation"] == 0.0
    assert "720000" not in response.text
    assert provider.prompts == []


def test_document_revoked_during_retrieval_skips_provider(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")

    def revoke_during_search(*_args, **_kwargs):
        document_service.change_document_permissions(session, 2, ["Admin"])
        return [{"chunk_id": 2, "document_id": 2}]

    monkeypatch.setattr(rag_service, "semantic_search", revoke_during_search)
    monkeypatch.setattr(
        document_service, "update_vector_permissions", lambda *_args: None
    )
    provider = FakeProvider()
    monkeypatch.setattr(rag_routes, "GeminiAnswerProvider", lambda: provider)
    response = client.post(
        "/rag/ask",
        headers=bearer(token),
        json={"query": "What is Alice's compensation?", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "no_accessible_source"
    assert response.json()["citations"] == []
    assert response.json()["timings_ms"]["gemini_generation"] == 0.0
    assert "720000" not in response.text
    assert provider.prompts == []


def test_account_deactivated_during_retrieval_skips_provider(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")

    def deactivate_during_search(*_args, **_kwargs):
        change_user_status(session, 2, False)
        return [{"chunk_id": 2, "document_id": 2}]

    monkeypatch.setattr(rag_service, "semantic_search", deactivate_during_search)
    provider = FakeProvider()
    monkeypatch.setattr(rag_routes, "GeminiAnswerProvider", lambda: provider)
    response = client.post(
        "/rag/ask",
        headers=bearer(token),
        json={"query": "What is Alice's compensation?", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "no_accessible_source"
    assert response.json()["citations"] == []
    assert "720000" not in response.text
    assert provider.prompts == []


def test_revocation_after_provider_dispatch_suppresses_stale_answer(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")
    monkeypatch.setattr(
        rag_service,
        "semantic_search",
        lambda *_args, **_kwargs: [{"chunk_id": 2, "document_id": 2}],
    )

    class RevokingProvider:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def generate(self, prompt: str) -> str:
            self.prompts.append(prompt)
            change_user_role(session, 2, "Employee")
            return "Alice earns 720000 rupees [1]."

    provider = RevokingProvider()
    monkeypatch.setattr(rag_routes, "GeminiAnswerProvider", lambda: provider)
    response = client.post(
        "/rag/ask",
        headers=bearer(token),
        json={"query": "What is Alice's compensation?", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "no_accessible_source"
    assert response.json()["citations"] == []
    assert "720000" not in response.text
    # Once dispatched, the prompt cannot be recalled from an external provider.
    assert len(provider.prompts) == 1
    assert "720000" in provider.prompts[0]


def test_preview_revocation_during_retrieval_returns_no_context(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")

    def revoke_during_search(*_args, **_kwargs):
        change_user_role(session, 2, "Employee")
        return [{"chunk_id": 2, "document_id": 2}]

    monkeypatch.setattr(rag_service, "semantic_search", revoke_during_search)
    response = client.post(
        "/rag/context-preview",
        headers=bearer(token),
        json={"query": "What is Alice's compensation?", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "no_accessible_source"
    assert response.json()["context"] == ""
    assert response.json()["citations"] == []
    assert "720000" not in response.text
