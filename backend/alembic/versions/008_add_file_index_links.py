"""add file_index_links async indexing queue

Revision ID: 008_add_file_index_links
Revises: 007_add_generated_file_section
Create Date: 2026-10-26 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008_add_file_index_links"
down_revision: Union[str, None] = "007_add_generated_file_section"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

file_section_enum = postgresql.ENUM(
    "PHOTOS",
    "FILES",
    "PRIVATE",
    "SHARED",
    "RESUMES",
    "GENERATED",
    name="file_section",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "file_index_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section", file_section_enum, nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("rag_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["file_record_id"],
            ["file_records.id"],
            name="file_index_links_file_record_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="file_index_links_pkey"),
        sa.UniqueConstraint("file_record_id", name="file_index_links_file_record_id_key"),
        sa.UniqueConstraint("rag_file_id", name="file_index_links_rag_file_id_key"),
    )
    op.create_index(
        "ix_file_index_links_state_next_attempt_at",
        "file_index_links",
        ["state", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_file_index_links_user_id_state",
        "file_index_links",
        ["user_id", "state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_file_index_links_user_id_state", table_name="file_index_links")
    op.drop_index(
        "ix_file_index_links_state_next_attempt_at",
        table_name="file_index_links",
    )
    op.drop_table("file_index_links")
