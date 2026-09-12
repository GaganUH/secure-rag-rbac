from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Role


def test_health_endpoint_and_default_roles() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "database": "connected",
        "roles": 3,
    }

    with SessionLocal() as session:
        role_names = set(session.scalars(select(Role.name)).all())

    assert role_names == {"Employee", "HR", "Admin"}


def test_seeding_roles_is_repeatable() -> None:
    with TestClient(app):
        pass
    with TestClient(app):
        pass

    with SessionLocal() as session:
        role_names = session.scalars(select(Role.name)).all()

    assert sorted(role_names) == ["Admin", "Employee", "HR"]

