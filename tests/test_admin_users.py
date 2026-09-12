import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_database_session
from app.main import app
from app.models import Role, User
from app.seed import seed_default_roles
from app.user_service import create_initial_admin, create_user


@pytest.fixture
def admin_test_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seed_default_roles(session)
        admin = create_initial_admin(session, "admin", "Admin-password-123")
        employee = create_user(
            session,
            "alice",
            "Employee-password-123",
            "Employee",
        )

        def override_database_session():
            yield session

        app.dependency_overrides[get_database_session] = override_database_session
        with TestClient(app) as client:
            yield client, session, admin, employee

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


def test_admin_can_create_and_list_user_without_exposing_password(
    admin_test_app,
) -> None:
    client, _session, _admin, _employee = admin_test_app
    admin_token = get_token(client, "admin", "Admin-password-123")

    create_response = client.post(
        "/admin/users",
        headers=bearer(admin_token),
        json={
            "username": "bob",
            "password": "HR-password-123",
            "role": "HR",
        },
    )
    list_response = client.get("/admin/users", headers=bearer(admin_token))

    assert create_response.status_code == 201
    assert create_response.json()["role"] == "HR"
    assert "password" not in create_response.json()
    assert list_response.status_code == 200
    assert {user["username"] for user in list_response.json()} == {
        "admin",
        "alice",
        "bob",
    }


def test_employee_cannot_access_admin_endpoints(admin_test_app) -> None:
    client, _session, _admin, _employee = admin_test_app
    employee_token = get_token(client, "alice", "Employee-password-123")

    response = client.get("/admin/users", headers=bearer(employee_token))

    assert response.status_code == 403


def test_role_change_is_visible_to_existing_token(admin_test_app) -> None:
    client, session, _admin, employee = admin_test_app
    admin_token = get_token(client, "admin", "Admin-password-123")
    employee_token = get_token(client, "alice", "Employee-password-123")

    response = client.patch(
        f"/admin/users/{employee.id}/role",
        headers=bearer(admin_token),
        json={"role": "HR"},
    )
    current_user_response = client.get(
        "/auth/me",
        headers=bearer(employee_token),
    )

    assert response.status_code == 200
    assert response.json()["permission_version"] == 2
    assert current_user_response.status_code == 200
    assert current_user_response.json()["role"] == "HR"
    assert session.scalar(select(User).where(User.id == employee.id)).role_id == (
        session.scalar(select(Role).where(Role.name == "HR")).id
    )


def test_deactivation_blocks_existing_token_and_new_login(admin_test_app) -> None:
    client, _session, _admin, employee = admin_test_app
    admin_token = get_token(client, "admin", "Admin-password-123")
    employee_token = get_token(client, "alice", "Employee-password-123")

    response = client.patch(
        f"/admin/users/{employee.id}/status",
        headers=bearer(admin_token),
        json={"is_active": False},
    )

    assert response.status_code == 200
    assert response.json()["permission_version"] == 2
    assert client.get(
        "/auth/me",
        headers=bearer(employee_token),
    ).status_code == 401
    assert client.post(
        "/auth/login",
        json={"username": "alice", "password": "Employee-password-123"},
    ).status_code == 401


def test_admin_cannot_remove_own_access(admin_test_app) -> None:
    client, _session, admin, _employee = admin_test_app
    admin_token = get_token(client, "admin", "Admin-password-123")

    role_response = client.patch(
        f"/admin/users/{admin.id}/role",
        headers=bearer(admin_token),
        json={"role": "Employee"},
    )
    status_response = client.patch(
        f"/admin/users/{admin.id}/status",
        headers=bearer(admin_token),
        json={"is_active": False},
    )

    assert role_response.status_code == 409
    assert status_response.status_code == 409


def test_duplicate_username_is_rejected(admin_test_app) -> None:
    client, _session, _admin, _employee = admin_test_app
    admin_token = get_token(client, "admin", "Admin-password-123")

    response = client.post(
        "/admin/users",
        headers=bearer(admin_token),
        json={
            "username": "alice",
            "password": "Another-password-123",
            "role": "HR",
        },
    )

    assert response.status_code == 409

