from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_database_session
from app.dependencies import require_admin
from app.document_service import (
    DuplicateDocumentError,
    DocumentNotFoundError,
    FileTooLargeError,
    IngestionError,
    ingest_document,
    list_documents,
    change_document_permissions,
)
from app.models import Document, User
from app.schemas import (
    DocumentPermissionUpdateRequest,
    DocumentResponse,
    VectorIndexResponse,
)
from app.vector_service import VectorIndexingError, rebuild_vector_index
from app.retrieval_cache import semantic_retrieval_cache


router = APIRouter(prefix="/admin/documents", tags=["Documents"])


def to_document_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        title=document.title,
        file_name=document.file_name,
        status=document.status,
        content_version=document.content_version,
        allowed_roles=sorted(
            permission.role.name for permission in document.permissions
        ),
        chunk_count=len(document.chunks),
    )


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    title: Annotated[str, Form(min_length=1, max_length=200)],
    allowed_roles: Annotated[
        str,
        Form(description="Comma-separated roles, for example: Employee,HR"),
    ],
    file: Annotated[UploadFile, File(description="PDF or UTF-8 TXT, maximum 10 MB")],
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> DocumentResponse:
    content = await file.read(10 * 1024 * 1024 + 1)
    await file.close()
    role_names = [role.strip() for role in allowed_roles.split(",") if role.strip()]

    try:
        document = ingest_document(
            session=session,
            title=title,
            filename=file.filename or "",
            content=content,
            allowed_role_names=role_names,
        )
    except FileTooLargeError as error:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(error),
        ) from error
    except DuplicateDocumentError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except IngestionError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except VectorIndexingError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error

    return to_document_response(document)


@router.get("", response_model=list[DocumentResponse])
def read_documents(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> list[DocumentResponse]:
    return [to_document_response(document) for document in list_documents(session)]


@router.patch("/{document_id}/permissions", response_model=DocumentResponse)
def update_document_access(
    document_id: int,
    update: DocumentPermissionUpdateRequest,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> DocumentResponse:
    try:
        document = change_document_permissions(
            session,
            document_id,
            list(update.allowed_roles),
        )
    except DocumentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except IngestionError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except VectorIndexingError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return to_document_response(document)


@router.post("/reindex", response_model=VectorIndexResponse)
def reindex_documents(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
) -> VectorIndexResponse:
    semantic_retrieval_cache.clear()
    try:
        document_count, chunk_count = rebuild_vector_index(session)
    except VectorIndexingError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The local vector index is temporarily unavailable.",
        ) from error
    semantic_retrieval_cache.clear()
    return VectorIndexResponse(
        indexed_documents=document_count,
        indexed_chunks=chunk_count,
    )
