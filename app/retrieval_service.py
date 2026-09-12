import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk, DocumentPermission, Role


WORD_PATTERN = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "how",
    "i",
    "in",
    "is",
    "of",
    "the",
    "to",
    "what",
}


def tokenize(text: str) -> list[str]:
    """Convert text into useful lowercase search terms."""
    return [
        word
        for word in WORD_PATTERN.findall(text.lower())
        if word not in STOP_WORDS
    ]


def retrieve_authorized_chunks(
    session: Session,
    query: str,
    role_name: str,
    top_k: int,
) -> list[dict[str, object]]:
    """Filter by role in SQL first, then rank only authorized chunks."""
    authorized_rows = session.execute(
        select(DocumentChunk, Document)
        .join(Document, DocumentChunk.document_id == Document.id)
        .join(
            DocumentPermission,
            DocumentPermission.document_id == Document.id,
        )
        .join(Role, DocumentPermission.role_id == Role.id)
        .where(Role.name == role_name, Document.status == "ready")
    ).all()

    query_terms = Counter(tokenize(query))
    if not query_terms:
        return []

    ranked: list[dict[str, object]] = []
    for chunk, document in authorized_rows:
        chunk_terms = Counter(tokenize(chunk.text))
        title_terms = Counter(tokenize(document.title))
        text_matches = sum(
            min(count, chunk_terms.get(term, 0))
            for term, count in query_terms.items()
        )
        title_matches = sum(
            min(count, title_terms.get(term, 0))
            for term, count in query_terms.items()
        )
        weighted_matches = text_matches + (2 * title_matches)
        if weighted_matches == 0:
            continue

        ranked.append(
            {
                "document_id": document.id,
                "document_title": document.title,
                "chunk_id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "page_number": chunk.page_number,
                "text": chunk.text,
                "score": round(weighted_matches / sum(query_terms.values()), 4),
            }
        )

    ranked.sort(
        key=lambda result: (-float(result["score"]), int(result["chunk_id"]))
    )
    return ranked[:top_k]
