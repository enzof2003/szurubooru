"""
Add translation_job table (background machine translation of post notes)

Revision ID: d97f7e581cf8
Created at: 2026-11-08 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "d97f7e581cf8"
down_revision = "d60550a53dc0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "translation_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.Unicode(length=16), nullable=False),
        sa.Column("error", sa.UnicodeText(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes_added", sa.Integer(), nullable=True),
        sa.Column("notes_replaced", sa.Integer(), nullable=True),
        sa.Column("notes_skipped", sa.Integer(), nullable=True),
        sa.Column("creation_time", sa.DateTime(), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=True),
        sa.Column("end_time", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["post_id"], ["post.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_translation_job_post_id"),
        "translation_job",
        ["post_id"],
        unique=False,
    )
    # At most one pending/running job per post, enforced by the database so
    # two simultaneous clicks cannot both get through.
    op.create_index(
        "ix_translation_job_one_active_per_post",
        "translation_job",
        ["post_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade():
    op.drop_index(
        "ix_translation_job_one_active_per_post", table_name="translation_job"
    )
    op.drop_index(
        op.f("ix_translation_job_post_id"), table_name="translation_job"
    )
    op.drop_table("translation_job")
