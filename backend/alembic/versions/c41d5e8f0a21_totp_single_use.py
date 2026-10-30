"""totp codes are single use

Revision ID: c41d5e8f0a21
Revises: 7b1f3c9a2d10
Create Date: 2026-10-30 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c41d5e8f0a21"
down_revision: Union[str, None] = "7b1f3c9a2d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mfa_last_step", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("mfa_last_step")
