from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Role


DEFAULT_ROLES = {
    "Employee": "Can access general company documents.",
    "HR": "Can access general and human-resources documents.",
    "Admin": "Can manage users, documents, roles, and permissions.",
}


def seed_default_roles(session: Session) -> None:
    """Insert the three demo roles once; do nothing if they already exist."""
    existing_names = set(session.scalars(select(Role.name)).all())

    for name, description in DEFAULT_ROLES.items():
        if name not in existing_names:
            session.add(Role(name=name, description=description))

    session.commit()

