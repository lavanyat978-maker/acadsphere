from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class TopicOut(BaseModel):
    id: str
    name: str
    unit: str
    description: str = ""


class SubjectOut(BaseModel):
    id: str
    code: str
    name: str
    description: str
    department: str
    topics: list[TopicOut] = []
    assigned_teachers: list[str] = []
    teacher_ids: list[str] = []
    student_count: int = 0
    assigned_to_me: bool = False


class SubjectCreate(BaseModel):
    code: str = Field(min_length=2, max_length=20, pattern=r"^[A-Za-z0-9\-]+$")
    name: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=2000)
    department: str = Field(default="Computer Science", max_length=120)
    teacher_ids: list[str] = []


class SubjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    department: str | None = Field(default=None, max_length=120)


class EnrollRequest(BaseModel):
    student_ids: list[str] = Field(min_length=1, max_length=500)


class TopicUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    unit: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def trim(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class TopicCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    unit: str = Field(default="Unit 1", max_length=80)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def trim(cls, value: str) -> str:
        return value.strip()


class SyllabusDocumentOut(BaseModel):
    id: str
    subject_id: str
    original_name: str
    content_type: str
    size_bytes: int
    chunk_count: int
    char_count: int
    status: str
    created_at: datetime
    uploaded_by_name: str = ""


class RagSearchResult(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    content: str
    score: float
