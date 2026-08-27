"""add limit_bytes to user_quota_usage

Existing FAMILY/ADMIN rows are backfilled to 100 MiB (the product default).
Users already over that cap stay over-quota until an admin raises limit_bytes.
Do not inflate the default from current usage.

Revision ID: 004_add_user_limit_bytes
Revises: 003_add_private_limit_bytes
Create Date: 2026-08-23 14:22:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_add_user_limit_bytes"
down_revision: Union[str, None] = "003_add_private_limit_bytes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_LIMIT_BYTES = 100 * 1024 * 1024


def upgrade() -> None:
    op.add_column(
        "user_quota_usage",
        sa.Column(
            "limit_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=str(_DEFAULT_LIMIT_BYTES),
        ),
    )
    op.alter_column("user_quota_usage", "limit_bytes", server_default=None)


def downgrade() -> None:
    op.drop_column("user_quota_usage", "limit_bytes")
