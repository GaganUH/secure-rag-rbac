from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import UPLOAD_DIRECTORY
from app.models import Document, DocumentChunk, DocumentPermission, Role
from app.vector_service import (
    VectorIndexingError,
    index_document,
    update_vector_permissions,
)
from app.retrieval_cache import semantic_retrieval_cache


MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
CHUNK_SIZE_WORDS = 120
CHUNK_OVERLAP_WORDS = 25
ALLOWED_EXTENSIONS = {".pdf", ".txt"}


class IngestionError(ValueError):
    """Base class for safe document-ingestion errors."""


class FileTooLargeError(IngestionError):
    """Raised when a file exceeds the configured upload limit."""


class DuplicateDocumentError(IngestionError):
    """Raised when the original filename is already registered."""


class DocumentNotFoundError(IngestionError):
    """Raised when a requested document does not exist."""


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str


def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE_WORDS,
    overlap: int = CHUNK_OVERLAP_WORDS,
) -> list[str]:
    """Split text into word-based chunks while retaining boundary context."""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk size and overlap are invalid.")

    words = text.split()
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - overlap
    return chunks


def validate_file(filename: str, content: bytes) -> tuple[str, str]:
    """Validate size, extension, basic signature, and safe original name."""
    original_name = Path(filename).name.strip()
    if not original_name:
        raise IngestionError("The uploaded file must have a filename.")
    if not content:
        raise IngestionError("The uploaded file is empty.")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise FileTooLargeError("The uploaded file exceeds the 10 MB limit.")

    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise IngestionError("Only PDF and TXT files are supported.")
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise IngestionError("The file extension is PDF but its content is not a PDF.")

    return original_name, extension


def extract_pages(extension: str, content: bytes) -> list[ExtractedPage]:
    """Extract readable text while preserving PDF page numbers."""
    if extension == ".txt":
        try:
            text = content.decode("utf-8-sig").strip()
        except UnicodeDecodeError as error:
            raise IngestionError("The TXT file is not valid UTF-8 text.") from error
        if not text:
            raise IngestionError("The TXT file does not contain readable text.")
        return [ExtractedPage(page_number=1, text=text)]

    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise IngestionError("Encrypted PDFs are not supported.")
        pages = [
            ExtractedPage(page_number=index, text=(page.extract_text() or "").strip())
            for index, page in enumerate(reader.pages, start=1)
        ]
    except (PdfReadError, OSError, ValueError) as error:
        if isinstance(error, IngestionError):
            raise
        raise IngestionError("The PDF is corrupted or cannot be read.") from error

    readable_pages = [page for page in pages if page.text]
    if not readable_pages:
        raise IngestionError(
            "The PDF has no extractable text. Scanned PDFs require OCR."
        )
    return readable_pages


def ingest_document(
    session: Session,
    title: str,
    filename: str,
    content: bytes,
    allowed_role_names: list[str],
) -> Document:
    """Validate, extract, chunk, authorize, and persist one document."""
    clean_title = title.strip()
    if not clean_title:
        raise IngestionError("Document title cannot be empty.")

    original_name, extension = validate_file(filename, content)
    if session.scalar(select(Document).where(Document.file_name == original_name)):
        raise DuplicateDocumentError("A document with that filename already exists.")

    requested_roles = set(allowed_role_names)
    if not requested_roles:
        raise IngestionError("At least one allowed role must be selected.")
    roles = list(session.scalars(select(Role).where(Role.name.in_(requested_roles))))
    if {role.name for role in roles} != requested_roles:
        raise IngestionError("One or more selected roles do not exist.")

    pages = extract_pages(extension, content)
    chunks_with_pages: list[tuple[int, str]] = []
    for page in pages:
        chunks_with_pages.extend(
            (page.page_number, chunk) for chunk in chunk_text(page.text)
        )
    if not chunks_with_pages:
        raise IngestionError("The document did not produce any searchable chunks.")

    document = Document(
        title=clean_title,
        file_name=original_name,
        status="processing",
    )
    document.permissions = [DocumentPermission(role_id=role.id) for role in roles]
    document.chunks = [
        DocumentChunk(
            chunk_index=index,
            page_number=page_number,
            text=chunk,
        )
        for index, (page_number, chunk) in enumerate(chunks_with_pages)
    ]

    session.add(document)
    session.flush()

    document_directory = UPLOAD_DIRECTORY / str(document.id)
    stored_file = document_directory / f"source{extension}"
    try:
        document_directory.mkdir(parents=True, exist_ok=False)
        stored_file.write_bytes(content)
        session.commit()
    except Exception:
        session.rollback()
        if stored_file.exists():
            stored_file.unlink()
        if document_directory.exists():
            document_directory.rmdir()
        raise

    indexed_document = get_document_by_id(session, document.id)
    try:
        index_document(indexed_document)
    except VectorIndexingError:
        indexed_document.status = "indexing_failed"
        session.commit()
        raise

    indexed_document.status = "ready"
    session.commit()
    semantic_retrieval_cache.clear()
    return get_document_by_id(session, document.id)


def get_document_by_id(session: Session, document_id: int) -> Document:
    document = session.scalar(
        select(Document)
        .options(
            joinedload(Document.permissions).joinedload(DocumentPermission.role),
            joinedload(Document.chunks),
        )
        .where(Document.id == document_id)
    )
    if document is None:
        raise IngestionError("Document not found.")
    return document


def list_documents(session: Session) -> list[Document]:
    return list(
        session.scalars(
            select(Document)
            .options(
                joinedload(Document.permissions).joinedload(DocumentPermission.role),
                joinedload(Document.chunks),
            )
            .order_by(Document.id)
        )
        .unique()
        .all()
    )


def change_document_permissions(
    session: Session,
    document_id: int,
    allowed_role_names: list[str],
) -> Document:
    """Commit current permissions, then synchronize vector metadata."""
    document = session.scalar(select(Document).where(Document.id == document_id))
    if document is None:
        raise DocumentNotFoundError("Document not found.")

    requested_roles = set(allowed_role_names)
    if not requested_roles:
        raise IngestionError("At least one allowed role must be selected.")
    roles = list(session.scalars(select(Role).where(Role.name.in_(requested_roles))))
    if {role.name for role in roles} != requested_roles:
        raise IngestionError("One or more selected roles do not exist.")

    existing_permissions = list(
        session.scalars(
            select(DocumentPermission).where(
                DocumentPermission.document_id == document.id
            )
        )
    )
    for permission in existing_permissions:
        session.delete(permission)
    session.flush()

    session.add_all(
        [
            DocumentPermission(document_id=document.id, role_id=role.id)
            for role in roles
        ]
    )
    session.commit()
    semantic_retrieval_cache.clear()

    # SQLite is authoritative during every search, so a failed metadata sync
    # cannot restore access that was revoked above.
    try:
        update_vector_permissions(document.id, list(requested_roles))
    except Exception as error:
        raise VectorIndexingError(
            "Database permissions changed, but vector metadata synchronization failed."
        ) from error
    return get_document_by_id(session, document.id)
