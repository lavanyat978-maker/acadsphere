"""rbac, student enrolment, uploaded papers, attempt autosave

Revision ID: 7b1f3c9a2d10
Revises: e802da85ace5
Create Date: 2026-10-30 09:30:00.000000

"""
from typing import Sequence, Union
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "7b1f3c9a2d10"
down_revision: Union[str, None] = "e802da85ace5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "student_subjects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "student_id", name="uq_subject_student"),
    )
    with op.batch_alter_table("student_subjects", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_student_subjects_student_id"), ["student_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_student_subjects_subject_id"), ["subject_id"], unique=False)

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("internal_exams", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(length=20), nullable=False, server_default="GENERATED")
        )
    with op.batch_alter_table("encrypted_papers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("content_type", sa.String(length=80), nullable=False, server_default="application/json")
        )
        batch_op.add_column(
            sa.Column("original_name", sa.String(length=255), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("practice_attempts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("draft_answers", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("last_saved_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("security_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("resolved_by", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))

    # Before this revision every student could see every published test. Enrol
    # existing students in every existing subject so nobody loses access on
    # upgrade; the HOD can then narrow enrolments from the admin screen.
    bind = op.get_bind()
    students = bind.execute(sa.text("SELECT id FROM users WHERE role = 'STUDENT'")).scalars().all()
    subjects = bind.execute(sa.text("SELECT id FROM subjects")).scalars().all()
    if students and subjects:
        now = datetime.now(timezone.utc)
        table = sa.table(
            "student_subjects",
            sa.column("id", sa.String),
            sa.column("subject_id", sa.String),
            sa.column("student_id", sa.String),
            sa.column("enrolled_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(
            table,
            [
                {"id": str(uuid.uuid4()), "subject_id": subject_id, "student_id": student_id, "enrolled_at": now}
                for subject_id in subjects
                for student_id in students
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("security_events", schema=None) as batch_op:
        batch_op.drop_column("resolved_at")
        batch_op.drop_column("resolved_by")
    with op.batch_alter_table("practice_attempts", schema=None) as batch_op:
        batch_op.drop_column("last_saved_at")
        batch_op.drop_column("draft_answers")
    with op.batch_alter_table("encrypted_papers", schema=None) as batch_op:
        batch_op.drop_column("page_count")
        batch_op.drop_column("original_name")
        batch_op.drop_column("content_type")
    with op.batch_alter_table("internal_exams", schema=None) as batch_op:
        batch_op.drop_column("source")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("must_change_password")
    with op.batch_alter_table("student_subjects", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_student_subjects_subject_id"))
        batch_op.drop_index(batch_op.f("ix_student_subjects_student_id"))
    op.drop_table("student_subjects")
