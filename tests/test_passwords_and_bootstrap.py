import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import User
from app.security import hash_password, verify_password
from app.seed import seed_default_roles
from app.user_service import (
    InitialAdminAlreadyExistsError,
    create_initial_admin,
)


@pytest.fixture
def database_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seed_default_roles(session)
        yield session

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_password_is_hashed_and_can_be_verified() -> None:
    original_password = "A-strong-test-password"
    stored_hash = hash_password(original_password)

    assert stored_hash != original_password
    assert stored_hash.startswith("$argon2")
    assert verify_password(original_password, stored_hash) is True
    assert verify_password("wrong-password", stored_hash) is False


def test_initial_admin_stores_only_a_password_hash(
    database_session: Session,
) -> None:
    original_password = "Admin-test-password"
    admin = create_initial_admin(database_session, " FirstAdmin ", original_password)

    stored_admin = database_session.scalar(
        select(User).where(User.username == "firstadmin")
    )

    assert stored_admin is not None
    assert admin.id == stored_admin.id
    assert stored_admin.password_hash != original_password
    assert verify_password(original_password, stored_admin.password_hash) is True
    assert stored_admin.role.name == "Admin"


def test_only_one_initial_admin_can_be_created(
    database_session: Session,
) -> None:
    create_initial_admin(database_session, "firstadmin", "First-password-123")

    with pytest.raises(InitialAdminAlreadyExistsError):
        create_initial_admin(database_session, "secondadmin", "Second-password-123")


def test_initial_admin_password_must_be_long_enough(
    database_session: Session,
) -> None:
    with pytest.raises(ValueError, match="at least 10 characters"):
        create_initial_admin(database_session, "firstadmin", "short")

