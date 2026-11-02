"""保存到研究空间中的可编辑成果。"""
import uuid

from pydantic import BaseModel, Field, field_validator


class ArtifactCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    kb_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    citations: list[dict] = Field(default_factory=list, max_length=50)

    @field_validator("title", "content")
    @classmethod
    def trim_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value


class ArtifactUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    expected_version: int = Field(ge=1)

    @field_validator("title", "content")
    @classmethod
    def trim_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value
