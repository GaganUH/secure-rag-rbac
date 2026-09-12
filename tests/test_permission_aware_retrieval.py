import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_database_session
from app.main import app
from app.models import Document, DocumentChunk, DocumentPermission, Role, User
from app.seed import seed_default_roles
from app.user_service import change_user_role, create_initial_admin, create_user
import app.retrieval_routes as retrieval_routes


@pytest.fixture
def retrieval_test_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seed_default_roles(session)
        create_initial_admin(session, "admin", "Admin-password-123")
        create_user(session, "alice", "Employee-password-123", "Employee")

        roles = {
            role.name: role for role in session.scalars(select(Role)).all()
        }
        handbook = Document(
            title="Employee Handbook",
            file_name="employee-handbook.txt",
            status="ready",
            permissions=[
                DocumentPermission(role_id=roles[name].id)
                for name in ("Employee", "HR", "Admin")
            ],
            chunks=[
                DocumentChunk(
                    chunk_index=0,
                    page_number=1,
                    text="Employees may work remotely two days each week.",
                )
            ],
        )
        salaries = Document(
            title="Salary Records",
            file_name="salary-records.txt",
            status="ready",
            permissions=[
                DocumentPermission(role_id=roles[name].id)
                for name in ("HR", "Admin")
            ],
            chunks=[
                DocumentChunk(
                    chunk_index=0,
                    page_number=1,
                    text="Alice has a fictional salary of 720000 rupees.",
                )
            ],
        )
        session.add_all([handbook, salaries])
        session.commit()

        def override_database_session():
            yield session

        app.dependency_overrides[get_database_session] = override_database_session
        with TestClient(app) as client:
            yield client, session

        app.dependency_overrides.clear()

    Base.metadata.drop_all(engine)
    engine.dispose()


def login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def search(client: TestClient, token: str, query: str):
    return client.post(
        "/retrieval/search",
        headers=bearer(token),
        json={"query": query, "top_k": 3},
    )


def test_employee_never_receives_hr_only_chunk(retrieval_test_app) -> None:
    client, _session = retrieval_test_app
    token = login(client, "alice", "Employee-password-123")

    response = search(client, token, "What is Alice salary?")

    assert response.status_code == 200
    assert response.json()["results"] == []
    serialized_response = response.text.lower()
    assert "salary records" not in serialized_response
    assert "720000" not in serialized_response


def test_employee_can_retrieve_general_document(retrieval_test_app) -> None:
    client, _session = retrieval_test_app
    token = login(client, "alice", "Employee-password-123")

    response = search(client, token, "How many remote work days?")

    assert response.status_code == 200
    assert response.json()["results"][0]["document_title"] == "Employee Handbook"


def test_existing_token_uses_changed_role_immediately(retrieval_test_app) -> None:
    client, session = retrieval_test_app
    token = login(client, "alice", "Employee-password-123")
    alice = session.scalar(select(User).where(User.username == "alice"))
    assert alice is not None

    change_user_role(session, alice.id, "HR")
    response = search(client, token, "What is Alice salary?")

    assert response.status_code == 200
    assert response.json()["user_role"] == "HR"
    assert response.json()["results"][0]["document_title"] == "Salary Records"
    assert "720000" in response.json()["results"][0]["text"]


def test_retrieval_requires_authentication(retrieval_test_app) -> None:
    client, _session = retrieval_test_app

    response = client.post(
        "/retrieval/search",
        json={"query": "remote work", "top_k": 3},
    )

    assert response.status_code == 401


def test_keyword_result_is_suppressed_if_role_changes_mid_search(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")

    def revoke_during_search(*_args, **_kwargs):
        change_user_role(session, 2, "Employee")
        return [{"chunk_id": 2, "document_id": 2, "text": "720000"}]

    monkeypatch.setattr(
        retrieval_routes, "retrieve_authorized_chunks", revoke_during_search
    )
    response = search(client, token, "What is Alice salary?")

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert "720000" not in response.text


def test_semantic_result_is_not_returned_or_cached_after_mid_search_revocation(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")
    retrieval_routes.semantic_retrieval_cache.clear()

    def revoke_during_search(*_args, **_kwargs):
        change_user_role(session, 2, "Employee")
        return [{"chunk_id": 2, "document_id": 2, "text": "720000"}]

    monkeypatch.setattr(retrieval_routes, "semantic_search", revoke_during_search)
    response = client.post(
        "/retrieval/semantic-search",
        headers=bearer(token),
        json={"query": "What is Alice salary?", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert "720000" not in response.text
    assert len(retrieval_routes.semantic_retrieval_cache._entries) == 0


def test_cached_semantic_result_is_suppressed_if_role_changes_during_cache_read(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    change_user_role(session, 2, "HR")
    token = login(client, "alice", "Employee-password-123")

    def revoke_during_cache_read(_key):
        change_user_role(session, 2, "Employee")
        return [{"chunk_id": 2, "document_id": 2, "text": "720000"}]

    monkeypatch.setattr(
        retrieval_routes.semantic_retrieval_cache,
        "get",
        revoke_during_cache_read,
    )
    response = client.post(
        "/retrieval/semantic-search",
        headers=bearer(token),
        json={"query": "What is Alice salary?", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["cache_hit"] is False
    assert "720000" not in response.text
