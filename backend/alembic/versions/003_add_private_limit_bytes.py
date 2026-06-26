"""add private_limit_bytes to user_quota_usage

Revision ID: 003_add_private_limit_bytes
Revises: 002_add_google_id
Create Date: 2026-06-26 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_add_private_limit_bytes"
down_revision: Union[str, None] = "002_add_google_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_quota_usage",
        sa.Column("private_limit_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.alter_column("user_quota_usage", "private_limit_bytes", server_default=None)


def downgrade() -> None:
    op.drop_column("user_quota_usage", "private_limit_bytes")
