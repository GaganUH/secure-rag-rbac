from functools import lru_cache

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import (
    EMBEDDING_MODEL,
    SEMANTIC_SCORE_THRESHOLD,
    VECTOR_COLLECTION_NAME,
    VECTOR_STORE_DIRECTORY,
)
from app.models import Document, DocumentPermission


class VectorIndexingError(RuntimeError):
    """Raised when chunks cannot be added to the vector index."""


@lru_cache(maxsize=1)
def get_vector_client() -> QdrantClient:
    VECTOR_STORE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(VECTOR_STORE_DIRECTORY))


def ensure_collection(client: QdrantClient) -> None:
    if client.collection_exists(VECTOR_COLLECTION_NAME):
        return
    client.create_collection(
        collection_name=VECTOR_COLLECTION_NAME,
        vectors_config=models.VectorParams(
            size=client.get_embedding_size(EMBEDDING_MODEL),
            distance=models.Distance.COSINE,
        ),
    )


def index_document(
    document: Document,
    client: QdrantClient | None = None,
) -> int:
    """Embed a document's chunks together with their access metadata."""
    vector_client = client or get_vector_client()
    ensure_collection(vector_client)
    roles = sorted(permission.role.name for permission in document.permissions)
    chunks = list(document.chunks)
    if not chunks:
        return 0

    try:
        vector_client.upload_collection(
            collection_name=VECTOR_COLLECTION_NAME,
            vectors=[
                models.Document(text=chunk.text, model=EMBEDDING_MODEL)
                for chunk in chunks
            ],
            payload=[
                {
                    "chunk_id": chunk.id,
                    "document_id": document.id,
                    "document_title": document.title,
                    "chunk_index": chunk.chunk_index,
                    "page_number": chunk.page_number,
                    "text": chunk.text,
                    "allowed_roles": roles,
                    "content_version": document.content_version,
                }
                for chunk in chunks
            ],
            ids=[chunk.id for chunk in chunks],
        )
    except Exception as error:
        raise VectorIndexingError("The document could not be vector-indexed.") from error
    return len(chunks)


def rebuild_vector_index(
    session: Session,
    client: QdrantClient | None = None,
) -> tuple[int, int]:
    """Replace the index using the current database permissions."""
    vector_client = client or get_vector_client()
    if vector_client.collection_exists(VECTOR_COLLECTION_NAME):
        vector_client.delete_collection(VECTOR_COLLECTION_NAME)
    ensure_collection(vector_client)

    documents = list(
        session.scalars(
            select(Document)
            .options(
                joinedload(Document.permissions).joinedload(DocumentPermission.role),
                joinedload(Document.chunks),
            )
            .where(Document.status == "ready")
            .order_by(Document.id)
        )
        .unique()
        .all()
    )
    chunk_count = sum(index_document(document, vector_client) for document in documents)
    return len(documents), chunk_count


def query_authorized_vectors(
    client: QdrantClient,
    query: models.Document | list[float],
    role_name: str,
    authorized_document_ids: list[int],
    top_k: int,
    score_threshold: float | None = SEMANTIC_SCORE_THRESHOLD,
) -> list[dict[str, object]]:
    """Run similarity search with the role filter inside Qdrant."""
    if not authorized_document_ids or not client.collection_exists(
        VECTOR_COLLECTION_NAME
    ):
        return []
    points = client.query_points(
        collection_name=VECTOR_COLLECTION_NAME,
        query=query,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="allowed_roles",
                    match=models.MatchValue(value=role_name),
                ),
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchAny(any=authorized_document_ids),
                ),
            ]
        ),
        with_payload=True,
        with_vectors=False,
        score_threshold=score_threshold,
        limit=top_k,
    ).points

    results: list[dict[str, object]] = []
    for point in points:
        payload = point.payload or {}
        results.append(
            {
                "document_id": int(payload["document_id"]),
                "document_title": str(payload["document_title"]),
                "chunk_id": int(payload["chunk_id"]),
                "chunk_index": int(payload["chunk_index"]),
                "page_number": payload.get("page_number"),
                "text": str(payload["text"]),
                "score": round(float(point.score), 4),
            }
        )
    return results


def get_authorized_document_fingerprint(
    session: Session, role_name: str
) -> tuple[tuple[int, int], ...]:
    """Read permitted document IDs and content versions from SQLite."""
    return tuple(
        (document_id, content_version)
        for document_id, content_version in session.execute(
            select(Document.id, Document.content_version)
            .join(DocumentPermission, DocumentPermission.document_id == Document.id)
            .join(DocumentPermission.role)
            .where(Document.status == "ready", DocumentPermission.role.has(name=role_name))
            .order_by(Document.id)
        ).all()
    )


def update_vector_permissions(
    document_id: int,
    allowed_roles: list[str],
    client: QdrantClient | None = None,
) -> None:
    """Synchronize role metadata without recomputing embeddings."""
    vector_client = client or get_vector_client()
    if not vector_client.collection_exists(VECTOR_COLLECTION_NAME):
        return
    vector_client.set_payload(
        collection_name=VECTOR_COLLECTION_NAME,
        payload={"allowed_roles": sorted(allowed_roles)},
        points=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=document_id),
                )
            ]
        ),
    )


def semantic_search(
    session: Session,
    query: str,
    role_name: str,
    top_k: int,
    authorized_document_ids: list[int] | None = None,
) -> list[dict[str, object]]:
    if authorized_document_ids is None:
        authorized_document_ids = [
            document_id
            for document_id, _version in get_authorized_document_fingerprint(
                session, role_name
            )
        ]
    if not authorized_document_ids:
        return []
    client = get_vector_client()
    return query_authorized_vectors(
        client=client,
        query=models.Document(text=query, model=EMBEDDING_MODEL),
        role_name=role_name,
        authorized_document_ids=authorized_document_ids,
        top_k=top_k,
    )
