from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: bool
    permission_version: int


RoleName = Literal["Employee", "HR", "Admin"]


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=128)
    role: RoleName


class UserRoleUpdateRequest(BaseModel):
    role: RoleName


class UserStatusUpdateRequest(BaseModel):
    is_active: bool


class ManagedUserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    permission_version: int


class DocumentResponse(BaseModel):
    id: int
    title: str
    file_name: str
    status: str
    content_version: int
    allowed_roles: list[str]
    chunk_count: int


class DocumentPermissionUpdateRequest(BaseModel):
    allowed_roles: list[RoleName] = Field(min_length=1)


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=3, ge=1, le=10)


class RetrievalResult(BaseModel):
    document_id: int
    document_title: str
    chunk_id: int
    chunk_index: int
    page_number: int | None
    text: str
    score: float


class RetrievalResponse(BaseModel):
    query: str
    user_role: str
    results: list[RetrievalResult]
    cache_hit: bool = False


class VectorIndexResponse(BaseModel):
    indexed_documents: int
    indexed_chunks: int


class SourceCitation(BaseModel):
    source_number: int
    document_id: int
    document_title: str
    chunk_id: int
    page_number: int | None


class ContextPreviewResponse(BaseModel):
    query: str
    user_role: str
    status: Literal["ready", "no_accessible_source"]
    context: str
    citations: list[SourceCitation]


class RAGTimings(BaseModel):
    context_preparation: float
    gemini_generation: float


class RAGAnswerResponse(BaseModel):
    query: str
    user_role: str
    status: Literal["answered", "no_accessible_source"]
    answer: str
    citations: list[SourceCitation]
    timings_ms: RAGTimings
