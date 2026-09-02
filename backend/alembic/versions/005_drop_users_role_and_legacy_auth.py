"""drop users.role and unused local auth columns

Revision ID: 005_drop_users_role
Revises: 004_add_user_limit_bytes
Create Date: 2026-08-29 18:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005_drop_users_role"
down_revision: Union[str, None] = "004_add_user_limit_bytes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_users_google_id"), table_name="users")
    op.drop_column("users", "google_id")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "role")
    postgresql.ENUM(name="user_role").drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    user_role_enum = postgresql.ENUM(
        "STRANGER",
        "FAMILY",
        "ADMIN",
        name="user_role",
        create_type=False,
    )
    user_role_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "users",
        sa.Column("role", user_role_enum, nullable=False, server_default="STRANGER"),
    )
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("google_id", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_users_google_id"), "users", ["google_id"], unique=True)
    op.alter_column("users", "role", server_default=None)
