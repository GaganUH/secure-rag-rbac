from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Role, User
from app.security import hash_password
from app.retrieval_cache import semantic_retrieval_cache


class InitialAdminAlreadyExistsError(ValueError):
    """Raised when the one-time Admin setup has already been completed."""


class UsernameAlreadyExistsError(ValueError):
    """Raised when a username is already registered."""


class UserNotFoundError(ValueError):
    """Raised when a requested user ID does not exist."""


class RoleNotFoundError(ValueError):
    """Raised when a requested role does not exist."""


def create_initial_admin(session: Session, username: str, password: str) -> User:
    """Create the first Admin account without storing its plaintext password."""
    clean_username = username.strip().lower()
    if not clean_username:
        raise ValueError("Username cannot be empty.")
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters.")

    admin_role = session.scalar(select(Role).where(Role.name == "Admin"))
    if admin_role is None:
        raise ValueError("Admin role is missing. Initialize the database first.")

    existing_admin = session.scalar(
        select(User).where(User.role_id == admin_role.id).limit(1)
    )
    if existing_admin is not None:
        raise InitialAdminAlreadyExistsError(
            "The initial Admin account has already been created."
        )

    existing_username = session.scalar(
        select(User).where(User.username == clean_username)
    )
    if existing_username is not None:
        raise ValueError("That username already exists.")

    admin = User(
        username=clean_username,
        password_hash=hash_password(password),
        role_id=admin_role.id,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


def create_user(
    session: Session,
    username: str,
    password: str,
    role_name: str,
) -> User:
    """Create a user with a hashed password and an existing role."""
    clean_username = username.strip().lower()
    if not clean_username:
        raise ValueError("Username cannot be empty.")
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters.")

    existing_user = session.scalar(select(User).where(User.username == clean_username))
    if existing_user is not None:
        raise UsernameAlreadyExistsError("That username already exists.")

    role = session.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        raise RoleNotFoundError("The requested role does not exist.")

    user = User(
        username=clean_username,
        password_hash=hash_password(password),
        role_id=role.id,
    )
    session.add(user)
    session.commit()
    return get_user_by_id(session, user.id)


def get_user_by_id(session: Session, user_id: int) -> User:
    user = session.scalar(
        select(User).options(joinedload(User.role)).where(User.id == user_id)
    )
    if user is None:
        raise UserNotFoundError("User not found.")
    return user


def list_users(session: Session) -> list[User]:
    return list(
        session.scalars(
            select(User).options(joinedload(User.role)).order_by(User.id)
        ).all()
    )


def change_user_role(session: Session, user_id: int, role_name: str) -> User:
    user = get_user_by_id(session, user_id)
    role = session.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        raise RoleNotFoundError("The requested role does not exist.")

    if user.role_id != role.id:
        user.role_id = role.id
        user.permission_version += 1
        session.commit()
        semantic_retrieval_cache.clear()

    return get_user_by_id(session, user_id)


def change_user_status(session: Session, user_id: int, is_active: bool) -> User:
    user = get_user_by_id(session, user_id)
    if user.is_active != is_active:
        user.is_active = is_active
        user.permission_version += 1
        session.commit()
        semantic_retrieval_cache.clear()

    return get_user_by_id(session, user_id)
