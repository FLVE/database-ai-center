"""Persist request correlation for M1 task creation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_m1"
down_revision = "0001_m0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("diagnostic_tasks", sa.Column("request_id", UUID(as_uuid=True), nullable=True))


def downgrade():
    op.drop_column("diagnostic_tasks", "request_id")
