"""文档自动标签的数据访问层。"""
import random
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag_model import Tag, document_tags

_TAG_COLORS = ["#155EEF", "#369F21", "#FF5D34", "#7839EE", "#0E9384"]


class TagRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, user_id: uuid.UUID, name: str) -> Tag:
        tag = (await self.session.execute(select(Tag).where(Tag.user_id == user_id, Tag.name == name))).scalar_one_or_none()
        if tag:
            return tag
        tag = Tag(user_id=user_id, name=name, color=random.choice(_TAG_COLORS))
        self.session.add(tag)
        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    async def list_by_user(self, user_id: uuid.UUID) -> list[Tag]:
        return list((await self.session.execute(select(Tag).where(Tag.user_id == user_id).order_by(Tag.name))).scalars().all())

    async def set_document_tags(self, document_id: uuid.UUID, tag_ids: list[uuid.UUID]) -> None:
        await self.session.execute(delete(document_tags).where(document_tags.c.document_id == document_id))
        for tag_id in tag_ids:
            await self.session.execute(document_tags.insert().values(document_id=document_id, tag_id=tag_id))
        await self.session.commit()

    async def get_document_tag_names(self, document_id: uuid.UUID) -> list[str]:
        statement = select(Tag.name).join(document_tags, Tag.id == document_tags.c.tag_id).where(document_tags.c.document_id == document_id)
        return list((await self.session.execute(statement)).scalars().all())

    async def get_document_tags(self, document_id: uuid.UUID) -> list[dict]:
        statement = select(Tag.name, Tag.color).join(document_tags, Tag.id == document_tags.c.tag_id).where(document_tags.c.document_id == document_id)
        rows = (await self.session.execute(statement)).all()
        return [{"name": row.name, "color": row.color} for row in rows]
