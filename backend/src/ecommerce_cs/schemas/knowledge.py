"""知识库管理模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    category: str = Field(default="通用", max_length=50)
    doc_type: str = Field(default="custom", max_length=40)
    content: str = Field(..., min_length=1)


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=50)
    doc_type: str | None = Field(default=None, max_length=40)
    content: str | None = None


class DocumentOut(BaseModel):
    id: str
    title: str
    category: str
    doc_type: str
    content: str
    source: str
    meta: dict = Field(default_factory=dict)
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[DocumentOut]


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class KnowledgeStats(BaseModel):
    total: int
    doc_types: dict[str, int]
    categories: dict[str, int]
    retrieval: dict[str, float | int]
