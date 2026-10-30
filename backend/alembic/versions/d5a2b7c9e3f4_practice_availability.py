"""practice test availability window and attempt limit

Revision ID: d5a2b7c9e3f4
Revises: c41d5e8f0a21
Create Date: 2026-10-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5a2b7c9e3f4"
down_revision: Union[str, None] = "c41d5e8f0a21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("practice_tests", schema=None) as batch_op:
        batch_op.add_column(sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("max_attempts", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("practice_tests", schema=None) as batch_op:
        batch_op.drop_column("max_attempts")
        batch_op.drop_column("closes_at")
        batch_op.drop_column("opens_at")
