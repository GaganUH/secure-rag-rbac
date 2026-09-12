from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATA_DIRECTORY, DATABASE_URL


class Base(DeclarativeBase):
    """Parent class for every database model in this project."""


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Make SQLite enforce relationships between tables."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def initialize_database() -> None:
    """Create the database file and all tables that do not exist yet."""
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)

    # Importing the models registers their table definitions with Base.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_database_session() -> Generator[Session, None, None]:
    """Give one database session to an API request, then close it safely."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

