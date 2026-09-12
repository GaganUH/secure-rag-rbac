from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.document_service as document_service
from app.database import Base, get_database_session
from app.document_service import chunk_text
from app.main import app
from app.models import Document, DocumentChunk, DocumentPermission
from app.seed import seed_default_roles
from app.user_service import create_initial_admin, create_user


@pytest.fixture
def ingestion_test_app(tmp_path: Path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(document_service, "UPLOAD_DIRECTORY", tmp_path / "uploads")
    monkeypatch.setattr(document_service, "index_document", lambda _document: 1)
    monkeypatch.setattr(
        document_service,
        "update_vector_permissions",
        lambda _document_id, _roles: None,
    )

    with Session(engine) as session:
        seed_default_roles(session)
        create_initial_admin(session, "admin", "Admin-password-123")
        create_user(session, "alice", "Employee-password-123", "Employee")

        def override_database_session():
            yield session

        app.dependency_overrides[get_database_session] = override_database_session
        with TestClient(app) as client:
            yield client, session, tmp_path / "uploads"

        app.dependency_overrides.clear()

    Base.metadata.drop_all(engine)
    engine.dispose()


def get_token(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_chunking_retains_overlap() -> None:
    text = "one two three four five six seven eight nine ten"
    chunks = chunk_text(text, chunk_size=5, overlap=2)

    assert chunks == [
        "one two three four five",
        "four five six seven eight",
        "seven eight nine ten",
    ]


def test_admin_uploads_txt_with_permissions_and_chunks(ingestion_test_app) -> None:
    client, session, upload_directory = ingestion_test_app
    admin_token = get_token(client, "admin", "Admin-password-123")
    text = " ".join(f"word{number}" for number in range(1, 221))

    response = client.post(
        "/admin/documents",
        headers=bearer(admin_token),
        data={"title": "Leave Policy", "allowed_roles": "Employee,HR"},
        files={"file": ("leave-policy.txt", text.encode("utf-8"), "text/plain")},
    )

    assert response.status_code == 201
    assert response.json()["allowed_roles"] == ["Employee", "HR"]
    assert response.json()["chunk_count"] == 3

    document = session.scalar(select(Document))
    assert document is not None
    assert len(session.scalars(select(DocumentChunk)).all()) == 3
    assert len(session.scalars(select(DocumentPermission)).all()) == 2
    assert (upload_directory / str(document.id) / "source.txt").read_text() == text


def test_employee_cannot_upload_document(ingestion_test_app) -> None:
    client, _session, _upload_directory = ingestion_test_app
    employee_token = get_token(client, "alice", "Employee-password-123")

    response = client.post(
        "/admin/documents",
        headers=bearer(employee_token),
        data={"title": "Secret", "allowed_roles": "Employee"},
        files={"file": ("secret.txt", b"Some text", "text/plain")},
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("filename", "content", "expected_detail"),
    [
        ("program.exe", b"not allowed", "Only PDF and TXT"),
        ("disguised.pdf", b"This is not really a PDF", "content is not a PDF"),
        ("broken.pdf", b"%PDF-this-is-corrupted", "corrupted or cannot be read"),
    ],
)
def test_invalid_uploads_are_rejected(
    ingestion_test_app,
    filename: str,
    content: bytes,
    expected_detail: str,
) -> None:
    client, session, _upload_directory = ingestion_test_app
    admin_token = get_token(client, "admin", "Admin-password-123")

    response = client.post(
        "/admin/documents",
        headers=bearer(admin_token),
        data={"title": "Invalid", "allowed_roles": "Employee"},
        files={"file": (filename, content, "application/octet-stream")},
    )

    assert response.status_code == 422
    assert expected_detail in response.json()["detail"]
    assert session.scalar(select(Document)) is None


def test_upload_requires_at_least_one_valid_role(ingestion_test_app) -> None:
    client, session, _upload_directory = ingestion_test_app
    admin_token = get_token(client, "admin", "Admin-password-123")

    response = client.post(
        "/admin/documents",
        headers=bearer(admin_token),
        data={"title": "Policy", "allowed_roles": ""},
        files={"file": ("policy.txt", b"Readable text", "text/plain")},
    )

    assert response.status_code == 422
    assert session.scalar(select(Document)) is None


def test_admin_changes_document_permissions_immediately(ingestion_test_app) -> None:
    client, _session, _upload_directory = ingestion_test_app
    admin_token = get_token(client, "admin", "Admin-password-123")
    upload = client.post(
        "/admin/documents",
        headers=bearer(admin_token),
        data={"title": "Salary Records", "allowed_roles": "HR,Admin"},
        files={"file": ("salary.txt", b"Fictional salary data", "text/plain")},
    )
    document_id = upload.json()["id"]

    response = client.patch(
        f"/admin/documents/{document_id}/permissions",
        headers=bearer(admin_token),
        json={"allowed_roles": ["Admin"]},
    )

    assert response.status_code == 200
    assert response.json()["allowed_roles"] == ["Admin"]
