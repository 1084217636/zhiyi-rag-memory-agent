"""Add user-owned research artifacts with citation snapshots.

Revision ID: 20261030_artifacts
Revises: 9e2f3fe76932
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261030_artifacts"
down_revision: Union[str, None] = "9e2f3fe76932"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("source_snapshot", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_research_artifacts_user_id", "research_artifacts", ["user_id"])
    op.create_index("ix_research_artifacts_kb_id", "research_artifacts", ["kb_id"])
    op.create_index("ix_research_artifacts_conversation_id", "research_artifacts", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_research_artifacts_conversation_id", table_name="research_artifacts")
    op.drop_index("ix_research_artifacts_kb_id", table_name="research_artifacts")
    op.drop_index("ix_research_artifacts_user_id", table_name="research_artifacts")
    op.drop_table("research_artifacts")
