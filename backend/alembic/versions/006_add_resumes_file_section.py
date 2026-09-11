"""add RESUMES value to the file_section enum

Revision ID: 006_add_resumes_file_section
Revises: 005_drop_users_role
Create Date: 2026-09-02 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "006_add_resumes_file_section"
down_revision: Union[str, None] = "005_drop_users_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block, so the
    # migration transaction is committed before the enum is extended.
    op.execute("COMMIT")
    op.execute("ALTER TYPE file_section ADD VALUE IF NOT EXISTS 'RESUMES'")


def downgrade() -> None:
    # PostgreSQL cannot drop a value from an enum type, so this is a no-op.
    pass
