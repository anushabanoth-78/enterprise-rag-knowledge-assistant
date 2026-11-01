import datetime as dt
import uuid
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, EmailStr, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------- auth
class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    is_admin: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------- documents
class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    status: str
    error: str | None = None
    n_pages: int
    n_chunks: int
    size_bytes: int = 0
    processing_ms: float | None = None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    document_id: uuid.UUID
    status: str
    celery_state: str | None = None
    error: str | None = None


# ---------------------------------------------------------------- chat
class ChatIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    conversation_id: uuid.UUID | None = None
    provider: Literal["hosted", "ollama"] = "hosted"
    top_k: int = Field(5, ge=1, le=20)
    rerank: bool = True
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


class CompareIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    providers: list[Literal["hosted", "ollama"]] = Field(default=["hosted", "ollama"], min_length=2, max_length=2)
    top_k: int = Field(5, ge=1, le=20)
    rerank: bool = True
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


class CitationOut(BaseModel):
    ref: int
    document_id: uuid.UUID
    filename: str
    page: int
    text: str
    score: float


class ChatOut(BaseModel):
    conversation_id: uuid.UUID
    answer: str
    answered: bool
    provider: str
    model: str
    citations: list[CitationOut]
    retrieval_ms: float
    generation_ms: float
    prompt_tokens: int
    completion_tokens: int


class CompareAnswer(BaseModel):
    provider: str
    model: str
    answer: str | None = None
    error: str | None = None
    generation_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    valid_citations: list[int] = []


class CompareOut(BaseModel):
    question: str
    retrieval_ms: float
    citations: list[CitationOut]
    results: list[CompareAnswer]


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    citations: list[CitationOut] | None = None
    model: str | None = None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
