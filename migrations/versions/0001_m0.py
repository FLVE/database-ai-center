"""M0 system-of-record schema; no diagnostic implementation or seed credentials."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001_m0"
down_revision = None
branch_labels = None
depends_on = None


def uid(name="id", primary=False, reference=None):
    args = [sa.ForeignKey(reference)] if reference else []
    return sa.Column(name, UUID(as_uuid=True), *args, primary_key=primary, nullable=False)


def timestamp(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def task_ref():
    return uid("task_id", reference="diagnostic_tasks.id")


def upgrade():
    op.create_table(
        "instances",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("environment", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("connection_ref", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint("environment = 'lab'", name="ck_instances_lab"),
        sa.CheckConstraint("connection_ref ~ '^env:[A-Z][A-Z0-9_]*$'", name="ck_connection_ref"),
        sa.CheckConstraint(
            "type IN ('mysql','redis','mongodb','postgresql','doris')", name="ck_instance_type"
        ),
    )
    op.create_table(
        "diagnostic_tasks",
        uid(primary=True),
        sa.Column("instance_id", sa.Text, sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("requested_by", sa.Text, nullable=False),
        sa.Column("database_type", sa.Text, nullable=False),
        sa.Column("problem_type", sa.Text, nullable=False),
        timestamp("window_start", True),
        timestamp("window_end", True),
        sa.Column("playbook_id", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        timestamp("created_at"),
        timestamp("started_at", True),
        timestamp("finished_at", True),
        sa.Column("error_code", sa.Text),
        sa.Column("parent_task_id", UUID(as_uuid=True), sa.ForeignKey("diagnostic_tasks.id")),
        sa.CheckConstraint("parent_task_id IS NULL OR parent_task_id <> id", name="ck_task_parent"),
        sa.CheckConstraint(
            "(window_start IS NULL AND window_end IS NULL) OR "
            "(window_start IS NOT NULL AND window_end IS NOT NULL AND window_end > window_start)",
            name="ck_task_window",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND finished_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('completed','partial','failed','cancelled') "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL)",
            name="ck_task_lifecycle",
        ),
        sa.CheckConstraint(
            "started_at >= created_at AND finished_at >= started_at", name="ck_task_time"
        ),
    )
    op.create_index("ix_tasks_instance_created", "diagnostic_tasks", ["instance_id", "created_at"])
    op.create_table(
        "diagnostic_steps",
        uid(primary=True),
        task_ref(),
        sa.Column("step_key", sa.Text, nullable=False),
        sa.Column("tool_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("elapsed_ms", sa.Float),
        sa.Column("error_code", sa.Text),
        timestamp("started_at", True),
        timestamp("finished_at", True),
        sa.UniqueConstraint("task_id", "step_key"),
        sa.CheckConstraint("elapsed_ms >= 0", name="ck_step_elapsed"),
    )
    op.create_table(
        "evidence",
        uid(primary=True),
        task_ref(),
        sa.Column("instance_id", sa.Text, sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        timestamp("observed_at"),
        timestamp("collected_at"),
        timestamp("window_start", True),
        timestamp("window_end", True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("quality", sa.Text, nullable=False),
        sa.Column("payload_json", JSONB, nullable=False),
        sa.Column("limitations_json", JSONB, nullable=False),
        sa.Column("redacted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("tool_version", sa.Text),
        sa.Column("parameters_summary", sa.Text),
        sa.Column("payload_hash", sa.Text),
        sa.CheckConstraint(
            "status IN ('ok','empty','unavailable','timeout','forbidden','error')",
            name="ck_evidence_status",
        ),
        sa.CheckConstraint(
            "quality IN ('observed','derived','inferred')", name="ck_evidence_quality"
        ),
        sa.CheckConstraint(
            "kind IN ('metric','log','database_state','historical_incident','derived')",
            name="ck_evidence_kind",
        ),
    )
    op.create_table(
        "findings",
        uid(primary=True),
        task_ref(),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("certainty", sa.Text, nullable=False),
        sa.Column("rule_id", sa.Text),
        sa.Column("confirmed_by", sa.Text),
        sa.Column("evidence_ids_json", JSONB, nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_ids_json) = 'array' AND "
            "jsonb_array_length(evidence_ids_json) > 0",
            name="ck_finding_evidence",
        ),
        sa.CheckConstraint(
            "certainty IN ('observed','supported_hypothesis','unverified_hypothesis',"
            "'confirmed_root_cause')",
            name="ck_finding_certainty",
        ),
        sa.CheckConstraint(
            "certainty <> 'confirmed_root_cause' OR "
            "(confirmed_by IS NOT NULL AND confirmed_by IN ('rule','human'))",
            name="ck_finding_confirmation",
        ),
    )
    op.create_table(
        "reports",
        uid("task_id", primary=True, reference="diagnostic_tasks.id"),
        sa.Column("summary_md", sa.Text, nullable=False),
        sa.Column("report_json", JSONB, nullable=False),
        timestamp("generated_at"),
        sa.Column("generator_version", sa.Text, nullable=False),
    )
    op.create_table(
        "feedback",
        uid(primary=True),
        task_ref(),
        sa.Column("verdict", sa.Text, nullable=False),
        sa.Column("confirmed_root_cause", sa.Text),
        sa.Column("comment", sa.Text),
        timestamp("created_at"),
        sa.CheckConstraint(
            "verdict IN ('correct','partial','incorrect')", name="ck_feedback_verdict"
        ),
    )
    op.create_table(
        "audit_events",
        uid(primary=True),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("diagnostic_tasks.id")),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("elapsed_ms", sa.Float),
        sa.Column("status", sa.Text, nullable=False),
        timestamp("created_at"),
        sa.CheckConstraint("elapsed_ms >= 0", name="ck_audit_elapsed"),
    )
    for table in ("diagnostic_steps", "evidence", "findings", "feedback", "audit_events"):
        op.create_index(f"ix_{table}_task", table, ["task_id"])


def downgrade():
    for table in (
        "audit_events",
        "feedback",
        "reports",
        "findings",
        "evidence",
        "diagnostic_steps",
        "diagnostic_tasks",
        "instances",
    ):
        op.drop_table(table)
