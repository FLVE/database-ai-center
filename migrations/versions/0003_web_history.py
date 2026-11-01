"""Index bounded task history queries without changing stored diagnostics."""

from alembic import op

revision = "0003_web_history"
down_revision = "0002_m1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_tasks_created_id", "diagnostic_tasks", ["created_at", "id"])
    op.create_index(
        "ix_tasks_status_created_id", "diagnostic_tasks", ["status", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_status_created_id", table_name="diagnostic_tasks")
    op.drop_index("ix_tasks_created_id", table_name="diagnostic_tasks")
