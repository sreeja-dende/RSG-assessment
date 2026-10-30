"""Request/response shapes (Pydantic).

Pydantic validates input automatically: an empty title or a missing field
returns a clear 422 error instead of crashing the app.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    created_at: datetime
    chunk_count: int


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)


class Source(BaseModel):
    chunk_id: int
    document_id: int
    score: float
    preview: str


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    mode: str


class AgentStep(BaseModel):
    tool: str
    input: str
    output: str


class AgentResponse(BaseModel):
    answer: str
    steps: list[AgentStep]
