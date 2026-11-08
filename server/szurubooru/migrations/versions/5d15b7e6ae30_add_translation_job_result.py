"""
Store the translator's raw blocks on translation_job

Revision ID: 5d15b7e6ae30
Created at: 2026-11-08 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "5d15b7e6ae30"
down_revision = "d97f7e581cf8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "translation_job", sa.Column("result", sa.JSON(), nullable=True)
    )


def downgrade():
    op.drop_column("translation_job", "result")
