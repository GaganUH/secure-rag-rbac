import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_database_session
from app.main import app
from app.models import AuthSession, Role
from app.security import hash_access_token
from app.seed import seed_default_roles
from app.user_service import create_initial_admin


@pytest.fixture
def authenticated_test_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seed_default_roles(session)
        create_initial_admin(session, "admin", "Admin-password-123")

        def override_database_session():
            yield session

        app.dependency_overrides[get_database_session] = override_database_session
        with TestClient(app) as client:
            yield client, session

        app.dependency_overrides.clear()

    Base.metadata.drop_all(engine)
    engine.dispose()


def login_as_admin(client: TestClient) -> str:
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "Admin-password-123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_valid_login_returns_token_and_me_returns_current_user(
    authenticated_test_app,
) -> None:
    client, _session = authenticated_test_app
    access_token = login_as_admin(client)

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "username": "admin",
        "role": "Admin",
        "is_active": True,
        "permission_version": 1,
    }


def test_raw_access_token_is_not_stored(authenticated_test_app) -> None:
    client, session = authenticated_test_app
    access_token = login_as_admin(client)
    stored_session = session.scalar(select(AuthSession))

    assert stored_session is not None
    assert stored_session.token_hash != access_token
    assert stored_session.token_hash == hash_access_token(access_token)


def test_wrong_password_is_rejected(authenticated_test_app) -> None:
    client, _session = authenticated_test_app
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert "access_token" not in response.json()


def test_missing_or_invalid_token_is_rejected(authenticated_test_app) -> None:
    client, _session = authenticated_test_app

    assert client.get("/auth/me").status_code == 401
    assert client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    ).status_code == 401


def test_deactivated_user_is_rejected_immediately(authenticated_test_app) -> None:
    client, session = authenticated_test_app
    access_token = login_as_admin(client)

    admin = session.scalar(select(Role).where(Role.name == "Admin")).users[0]
    admin.is_active = False
    session.commit()

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 401

