from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func, select

from app.admin_routes import router as admin_router
from app.auth_routes import router as auth_router
from app.database import SessionLocal, initialize_database
from app.document_routes import router as document_router
from app.models import Role
from app.retrieval_routes import router as retrieval_router
from app.rag_routes import router as rag_router
from app.seed import seed_default_roles


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Prepare the database before the API accepts requests."""
    initialize_database()
    with SessionLocal() as session:
        seed_default_roles(session)
    yield


app = FastAPI(
    title="Secure RAG with RBAC",
    description="A RAG system that enforces permissions before retrieval.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(document_router)
app.include_router(retrieval_router)
app.include_router(rag_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "project": "Secure RAG with RBAC",
        "message": "Backend is running. Open /docs to use the API.",
    }


@app.get("/health")
def health() -> dict[str, str | int]:
    """Prove that both the web API and the database are working."""
    with SessionLocal() as session:
        role_count = session.scalar(select(func.count()).select_from(Role)) or 0

    return {
        "status": "healthy",
        "database": "connected",
        "roles": role_count,
    }
